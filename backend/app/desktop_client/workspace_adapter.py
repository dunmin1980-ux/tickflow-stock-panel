"""Cloud-backed workspace adapter for the desktop loopback server."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from app.desktop_client.cache import WorkspaceCache
from app.desktop_client.remote import RemoteClient
from app.workspace.models import (
    ResourceName,
    ResourceSnapshot,
    WorkspaceCommandConflict,
    WorkspacePreconditionRequired,
    WorkspaceRevisionConflict,
)
from app.workspace.revision import etag_for, parse_if_match


class WorkspaceOfflineReadOnly(RuntimeError):  # noqa: N818 - binding public contract
    pass


class WorkspaceAdapter(Protocol):
    def bootstrap(self) -> dict: ...

    def get(self, resource: ResourceName) -> ResourceSnapshot: ...

    def command(
        self,
        resource: ResourceName,
        operation: str,
        payload: dict,
        revision: str,
    ) -> ResourceSnapshot: ...

    def revisions(self) -> dict[ResourceName, str]: ...

    def stream_events(self) -> Iterator[dict]: ...


RemoteProvider = RemoteClient | Callable[[], RemoteClient]


class CloudWorkspaceAdapter:
    def __init__(self, remote: RemoteProvider, cache: WorkspaceCache) -> None:
        self._remote_provider = remote
        self.cache = cache

    def _remote(self) -> RemoteClient:
        if callable(self._remote_provider):
            return self._remote_provider()
        return self._remote_provider

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("cloud workspace response must be an object")
        return payload

    @staticmethod
    def _snapshot(resource: ResourceName, payload: dict[str, Any]) -> ResourceSnapshot:
        candidate = dict(payload)
        candidate["resource"] = resource.value
        snapshot = ResourceSnapshot.model_validate(candidate)
        if snapshot.resource is not resource:
            raise ValueError("cloud workspace resource mismatch")
        return snapshot

    def _cached(self, resource: ResourceName) -> ResourceSnapshot:
        snapshot = self.cache.get(resource)
        if snapshot is None:
            raise WorkspaceOfflineReadOnly("cloud workspace is offline and no cache is available")
        return snapshot.model_copy(update={"offline_readonly": True})

    def bootstrap(self) -> dict:
        try:
            payload = self._payload(self._remote().request("GET", "/api/workspace/bootstrap"))
        except httpx.RequestError:
            resources: dict[str, dict[str, Any]] = {}
            for resource in ResourceName:
                snapshot = self._cached(resource)
                item = snapshot.model_dump(mode="json")
                item.pop("resource")
                resources[resource.value] = item
            return {
                "schema_version": 1,
                "server_time": datetime.now(UTC).isoformat(),
                "data_as_of": None,
                "mode": "cloud",
                "capabilities": {"label": "Offline", "capabilities": {}},
                "resources": resources,
                "offline_readonly": True,
            }

        resources = payload.get("resources")
        if not isinstance(resources, dict):
            raise ValueError("cloud workspace bootstrap resources are invalid")
        snapshots = [
            self._snapshot(resource, resources.get(resource.value, {})) for resource in ResourceName
        ]
        snapshots = [self.cache.validate(snapshot) for snapshot in snapshots]
        for snapshot in snapshots:
            self.cache.put(snapshot)
        return payload

    def get(self, resource: ResourceName) -> ResourceSnapshot:
        resource = ResourceName(resource)
        path = f"/api/workspace/resources/{resource.value}"
        try:
            payload = self._payload(self._remote().request("GET", path))
        except httpx.RequestError:
            return self._cached(resource)
        snapshot = self._snapshot(resource, payload)
        self.cache.put(snapshot)
        return snapshot

    def command(
        self,
        resource: ResourceName,
        operation: str,
        payload: dict,
        revision: str,
    ) -> ResourceSnapshot:
        resource = ResourceName(resource)
        path = f"/api/workspace/resources/{resource.value}/commands"
        try:
            response = self._remote().request(
                "POST",
                path,
                headers={"If-Match": etag_for(revision)},
                json={"operation": operation, "payload": payload},
            )
        except httpx.RequestError as exc:
            raise WorkspaceOfflineReadOnly("cloud workspace is offline and read-only") from exc
        if response.status_code == 428:
            raise WorkspacePreconditionRequired(resource)
        if response.status_code == 412:
            try:
                current_revision = parse_if_match(response.headers.get("etag"))
            except ValueError:
                current_revision = None
            if current_revision is not None:
                raise WorkspaceRevisionConflict(resource, current_revision)
        if response.status_code == 409:
            raise WorkspaceCommandConflict(resource, operation, "cloud command conflict")
        return self._snapshot(resource, self._payload(response))

    def revisions(self) -> dict[ResourceName, str]:
        try:
            payload = self._payload(self._remote().request("GET", "/api/workspace/revisions"))
        except httpx.RequestError as exc:
            cached = {
                resource: snapshot.revision
                for resource in ResourceName
                if (snapshot := self.cache.get(resource)) is not None
            }
            if not cached:
                raise WorkspaceOfflineReadOnly(
                    "cloud workspace is offline and no cache is available"
                ) from exc
            return cached
        resources = payload.get("resources")
        if not isinstance(resources, dict):
            raise ValueError("cloud workspace revisions are invalid")
        return {ResourceName(name): str(revision) for name, revision in resources.items()}

    def stream_events(self) -> Iterator[dict]:
        try:
            remote = self._remote()
            stream_method = getattr(remote, "stream", None)
            if callable(stream_method):
                with stream_method(
                    "GET",
                    "/api/workspace/events",
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    yield from self._event_payloads(response)
                return
            response = remote.request(
                "GET", "/api/workspace/events", headers={"Accept": "text/event-stream"}
            )
            yield from self._event_payloads(response)
        except httpx.RequestError as exc:
            raise WorkspaceOfflineReadOnly("cloud workspace event stream is offline") from exc

    @staticmethod
    def _event_payloads(response: httpx.Response) -> Iterator[dict]:
        response.raise_for_status()
        data_lines: list[str] = []
        for line in response.iter_lines():
            if line == "":
                if data_lines:
                    payload = json.loads("\n".join(data_lines))
                    if isinstance(payload, dict):
                        yield payload
                data_lines = []
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            payload = json.loads("\n".join(data_lines))
            if isinstance(payload, dict):
                yield payload
