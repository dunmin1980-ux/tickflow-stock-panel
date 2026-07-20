"""In-process revision notifications for authenticated workspace clients."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.workspace.models import ResourceName, ResourceSnapshot


class WorkspaceEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    type: Literal["resource_changed", "resync_required"]
    resource: ResourceName | None = None
    revision: str | None = None
    updated_at: datetime


class WorkspaceEventHub:
    """Fan out revision-only events from sync workers to the server event loop."""

    queue_maxsize = 32

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[WorkspaceEvent]] = set()
        self._last_event_id = 0

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        if loop.is_closed():
            raise RuntimeError("workspace event loop is closed")
        with self._lock:
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError("workspace event hub is already bound to another loop")
            self._loop = loop

    def unbind(self) -> None:
        with self._lock:
            self._loop = None
            self._subscribers.clear()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[WorkspaceEvent]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[WorkspaceEvent] = asyncio.Queue(maxsize=self.queue_maxsize)
        with self._lock:
            if self._loop is not loop:
                raise RuntimeError("workspace event hub is not bound to the running loop")
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            with self._lock:
                self._subscribers.discard(queue)

    def publish(
        self,
        snapshot_or_resource: ResourceSnapshot | ResourceName,
        revision: str | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        if isinstance(snapshot_or_resource, ResourceSnapshot):
            resource = snapshot_or_resource.resource
            revision = snapshot_or_resource.revision
            updated_at = snapshot_or_resource.updated_at
        else:
            resource = ResourceName(snapshot_or_resource)
            if revision is None:
                raise ValueError("revision is required when publishing a resource name")
            updated_at = updated_at or datetime.now(UTC)

        with self._lock:
            loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._deliver, resource, revision, updated_at)
        except RuntimeError:
            # A concurrent lifespan shutdown may close the loop after the check.
            return

    def _next_event_id(self) -> str:
        with self._lock:
            event_id = max(time.time_ns(), self._last_event_id + 1)
            self._last_event_id = event_id
            return str(event_id)

    def _deliver(self, resource: ResourceName, revision: str, updated_at: datetime) -> None:
        event = WorkspaceEvent(
            id=self._next_event_id(),
            type="resource_changed",
            resource=resource,
            revision=revision,
            updated_at=updated_at,
        )
        with self._lock:
            if self._loop is not asyncio.get_running_loop():
                return
            subscribers = tuple(self._subscribers)

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                while True:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                queue.put_nowait(
                    WorkspaceEvent(
                        id=self._next_event_id(),
                        type="resync_required",
                        updated_at=datetime.now(UTC),
                    )
                )
