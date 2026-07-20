from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack, suppress
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sse_starlette.event import ServerSentEvent

from app.api import auth as auth_api
from app.api.workspace import events as workspace_events
from app.config import settings
from app.main import app
from app.services import auth as auth_service
from app.services import watchlist
from app.workspace.events import WorkspaceEventHub
from app.workspace.models import ResourceName, ResourceSnapshot


def _snapshot(resource: ResourceName, revision: str) -> ResourceSnapshot:
    return ResourceSnapshot(
        resource=resource,
        revision=revision,
        updated_at=datetime(2026, 7, 21, 8, 0, tzinfo=UTC),
        data={"private_body": "must not be published"},
    )


async def _wait_for_subscribers(hub: WorkspaceEventHub, count: int) -> None:
    async with asyncio.timeout(1):
        while hub.subscriber_count != count:
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_publish_from_worker_thread_delivers_typed_revision_without_snapshot_data():
    hub = WorkspaceEventHub()
    hub.bind(asyncio.get_running_loop())

    async with hub.subscribe() as queue:
        await asyncio.to_thread(
            hub.publish,
            _snapshot(ResourceName.WATCHLIST, "a" * 64),
        )
        event = await asyncio.wait_for(queue.get(), 1)

    hub.unbind()
    assert event.type == "resource_changed"
    assert event.resource == ResourceName.WATCHLIST
    assert event.revision == "a" * 64
    assert "private_body" not in event.model_dump_json()


@pytest.mark.asyncio
async def test_event_ids_are_unique_and_monotonic_for_diagnostics():
    hub = WorkspaceEventHub()
    hub.bind(asyncio.get_running_loop())

    async with hub.subscribe() as queue:
        hub.publish(ResourceName.WATCHLIST, "a" * 64)
        hub.publish(ResourceName.PREFERENCES, "b" * 64)
        first = await asyncio.wait_for(queue.get(), 1)
        second = await asyncio.wait_for(queue.get(), 1)

    hub.unbind()
    assert int(second.id) > int(first.id)


@pytest.mark.asyncio
async def test_all_concurrent_subscribers_receive_one_publication_and_unregister():
    hub = WorkspaceEventHub()
    hub.bind(asyncio.get_running_loop())

    async with AsyncExitStack() as stack:
        queues = [await stack.enter_async_context(hub.subscribe()) for _ in range(12)]
        assert hub.subscriber_count == 12
        await asyncio.to_thread(hub.publish, ResourceName.STOCK_REPORTS, "c" * 64)
        events = await asyncio.gather(*(asyncio.wait_for(queue.get(), 1) for queue in queues))

    hub.unbind()
    assert {event.id for event in events} == {events[0].id}
    assert hub.subscriber_count == 0


@pytest.mark.asyncio
async def test_overflow_discards_stale_entries_and_emits_one_resync_sentinel():
    hub = WorkspaceEventHub()
    hub.bind(asyncio.get_running_loop())

    async with hub.subscribe() as queue:
        await asyncio.to_thread(
            lambda: [hub.publish(ResourceName.WATCHLIST, f"{index:064x}") for index in range(34)]
        )
        await asyncio.sleep(0)

        assert queue.qsize() == 2
        sentinel = queue.get_nowait()
        assert sentinel.type == "resync_required"
        assert sentinel.resource is None
        assert sentinel.revision is None

        post_overflow = queue.get_nowait()
        assert post_overflow.type == "resource_changed"
        assert post_overflow.revision == f"{33:064x}"
        assert int(post_overflow.id) > int(sentinel.id)

        hub.publish(ResourceName.WATCHLIST, "f" * 64)
        current = await asyncio.wait_for(queue.get(), 1)
        assert current.type == "resource_changed"
        assert current.revision == "f" * 64

    hub.unbind()


@pytest.mark.asyncio
async def test_cancelled_subscriber_always_unregisters():
    hub = WorkspaceEventHub()
    hub.bind(asyncio.get_running_loop())

    async def wait_forever() -> None:
        async with hub.subscribe() as queue:
            await queue.get()

    task = asyncio.create_task(wait_forever())
    await _wait_for_subscribers(hub, 1)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    hub.unbind()
    assert hub.subscriber_count == 0


@pytest.mark.asyncio
async def test_sse_response_is_private_typed_json_with_deterministic_heartbeat():
    hub = WorkspaceEventHub()
    hub.bind(asyncio.get_running_loop())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workspace_event_hub=hub)))

    response = await workspace_events(request)
    stream = response.body_iterator
    next_event = asyncio.create_task(anext(stream))
    await _wait_for_subscribers(hub, 1)
    hub.publish(ResourceName.MARKET_RECAPS, "d" * 64)
    item = await asyncio.wait_for(next_event, 1)
    await stream.aclose()

    assert isinstance(item, ServerSentEvent)
    payload = json.loads(item.data)
    assert payload == {
        "id": item.id,
        "type": "resource_changed",
        "resource": "market_recaps",
        "revision": "d" * 64,
        "updated_at": payload["updated_at"],
    }
    assert item.event == "resource_changed"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response._ping_interval == 20
    assert response.ping_message_factory().encode() == b": heartbeat\r\n\r\n"
    assert hub.subscriber_count == 0
    hub.unbind()


@pytest.mark.asyncio
async def test_sse_stream_cancellation_cleans_subscription_queue():
    hub = WorkspaceEventHub()
    hub.bind(asyncio.get_running_loop())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workspace_event_hub=hub)))

    response = await workspace_events(request)
    stream = response.body_iterator
    pending = asyncio.create_task(anext(stream))
    await _wait_for_subscribers(hub, 1)
    pending.cancel()
    with suppress(asyncio.CancelledError):
        await pending
    await stream.aclose()

    assert hub.subscriber_count == 0
    hub.unbind()


class RecordingEventHub:
    def __init__(self) -> None:
        self.snapshots: list[ResourceSnapshot] = []

    def publish(self, snapshot: ResourceSnapshot) -> None:
        self.snapshots.append(snapshot)


@pytest.fixture
def command_client(tmp_path, monkeypatch):
    recorder = RecordingEventHub()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", True)
    monkeypatch.setattr(app.state, "workspace_event_hub", recorder, raising=False)
    monkeypatch.setattr(auth_service, "is_configured", lambda: True)
    monkeypatch.setattr(auth_service, "is_valid_session", lambda token: token == "valid-session")
    client = TestClient(app)
    client.cookies.set(auth_api.COOKIE_NAME, "valid-session")
    yield client, recorder
    client.close()


def test_successful_post_commit_command_publishes_new_snapshot(command_client):
    client, recorder = command_client
    before = client.get("/api/workspace/resources/watchlist").json()["revision"]

    response = client.post(
        "/api/workspace/resources/watchlist/commands",
        headers={"If-Match": f'"{before}"'},
        json={"operation": "add", "payload": {"symbol": "000403.SZ"}},
    )

    assert response.status_code == 200
    assert watchlist.list_symbols()[0]["symbol"] == "000403.SZ"
    assert len(recorder.snapshots) == 1
    assert recorder.snapshots[0].resource == ResourceName.WATCHLIST
    assert recorder.snapshots[0].revision == response.json()["revision"]


def test_failed_commands_never_publish_for_422_409_412_or_503(command_client, monkeypatch):
    client, recorder = command_client
    watch_revision = client.get("/api/workspace/resources/watchlist").json()["revision"]
    stock_revision = client.get("/api/workspace/resources/stock_reports").json()["revision"]
    path = "/api/workspace/resources/watchlist/commands"

    invalid = client.post(
        path,
        headers={"If-Match": f'"{watch_revision}"'},
        json={
            "operation": "add",
            "payload": {"symbol": "000403.SZ", "api_key": "must-not-be-accepted"},
        },
    )
    conflict = client.post(
        "/api/workspace/resources/stock_reports/commands",
        headers={"If-Match": f'"{stock_revision}"'},
        json={"operation": "delete", "payload": {"id": "missing"}},
    )
    stale = client.post(
        path,
        headers={"If-Match": f'"{"f" * 64}"'},
        json={"operation": "add", "payload": {"symbol": "000403.SZ"}},
    )
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", False)
    disabled = client.post(
        path,
        headers={"If-Match": f'"{watch_revision}"'},
        json={"operation": "add", "payload": {"symbol": "000403.SZ"}},
    )

    assert [invalid.status_code, conflict.status_code, stale.status_code, disabled.status_code] == [
        422,
        409,
        412,
        503,
    ]
    assert recorder.snapshots == []


def test_workspace_events_requires_authentication(monkeypatch):
    monkeypatch.setattr(auth_service, "is_configured", lambda: True)
    monkeypatch.setattr(auth_service, "is_valid_session", lambda _token: False)
    client = TestClient(app)

    response = client.get("/api/workspace/events")

    client.close()
    assert response.status_code == 401
