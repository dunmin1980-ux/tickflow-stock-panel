from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main as main_module
from app.api import workspace as workspace_api
from app.config import settings
from app.desktop_client.cache import WorkspaceCache
from app.desktop_client.workspace_adapter import (
    CloudWorkspaceAdapter,
    WorkspaceOfflineReadOnly,
)
from app.workspace.models import (
    ResourceName,
    ResourceSnapshot,
    WorkspaceCommandConflict,
    WorkspacePreconditionRequired,
    WorkspaceRevisionConflict,
)
from app.workspace.revision import revision_for


def _data(resource: ResourceName) -> dict:
    return {
        ResourceName.WATCHLIST: {"symbols": [{"symbol": "000403.SZ", "note": "focus"}]},
        ResourceName.PREFERENCES: {
            "preferences": {
                "nav_order": ["watchlist", "review"],
                "has_feishu_webhook": True,
                "has_feishu_credential_data": True,
                "has_wecom_webhook": False,
                "has_wecom_bot": False,
                "has_wecom_bot_credential_data": False,
            }
        },
        ResourceName.STOCK_REPORTS: {
            "reports": [
                {
                    "id": "stock-1",
                    "symbol": "000403.SZ",
                    "title": "Daily review",
                    "created_at": "2026-07-21T16:00:00+08:00",
                    "data_as_of": "2026-07-21",
                    "verification_status": "pending",
                    "can_publish": False,
                    "trading_advice": False,
                }
            ]
        },
        ResourceName.MARKET_RECAPS: {
            "reports": [
                {
                    "id": "market-1",
                    "title": "Market close",
                    "created_at": "2026-07-21T16:10:00+08:00",
                    "data_as_of": "2026-07-21",
                    "verification_status": "pending",
                    "can_publish": False,
                    "trading_advice": False,
                }
            ]
        },
        ResourceName.BACKTEST_SUMMARIES: {
            "summaries": [
                {
                    "id": "bt-1",
                    "task": "strategy",
                    "strategy_id": "momentum",
                    "parameters_digest": "a" * 64,
                    "stats": {"return": 1.25},
                    "started_at": "2026-07-21T09:00:00+08:00",
                    "finished_at": "2026-07-21T09:01:00+08:00",
                    "data_as_of": "2026-07-18",
                    "engine": "matrix",
                    "execution_target": "local",
                }
            ]
        },
    }[resource]


def _snapshot(resource: ResourceName) -> ResourceSnapshot:
    data = _data(resource)
    return ResourceSnapshot(
        resource=resource,
        revision=revision_for(data),
        updated_at=datetime(2026, 7, 21, 8, tzinfo=UTC),
        data=data,
    )


def _resource_payload(resource: ResourceName, *, include_resource: bool = True) -> dict:
    snapshot = _snapshot(resource)
    payload = snapshot.model_dump(mode="json")
    if not include_resource:
        payload.pop("resource")
    return payload


def _bootstrap_payload() -> dict:
    return {
        "schema_version": 1,
        "server_time": "2026-07-21T16:00:00+08:00",
        "data_as_of": "2026-07-21",
        "mode": "cloud",
        "capabilities": {"label": "Pro", "capabilities": {}},
        "resources": {
            resource.value: _resource_payload(resource, include_resource=False)
            for resource in ResourceName
        },
    }


def _response(method: str, path: str, payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request(method, f"https://cloud.example{path}"),
    )


class RemoteStub:
    def __init__(self, responses: list[httpx.Response] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str, dict]] = []
        self.error: Exception | None = None

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.calls.append((method, path, kwargs))
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def test_corrupt_cache_is_quarantined(tmp_path: Path) -> None:
    cache = WorkspaceCache(tmp_path)
    cache.path(ResourceName.WATCHLIST).write_text(
        '{"schema_version":1,"resource":"watchlist","revision":"bad",'
        '"fetched_at":"2026-07-21T08:00:00Z","checksum":"bad","data":{}}',
        encoding="utf-8",
    )

    assert cache.get(ResourceName.WATCHLIST) is None
    quarantined = list((tmp_path / "quarantine").iterdir())
    assert len(quarantined) == 1
    assert quarantined[0].name.endswith("-watchlist.json")
    assert not cache.path(ResourceName.WATCHLIST).exists()


def test_unsafe_corrupt_cache_is_quarantined_without_rejected_payload(tmp_path: Path) -> None:
    cache = WorkspaceCache(tmp_path)
    resource = ResourceName.STOCK_REPORTS
    cache.path(resource).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resource": resource.value,
                "revision": "a" * 64,
                "fetched_at": "2026-07-21T08:00:00Z",
                "checksum": "a" * 64,
                "data": {
                    "reports": [
                        {
                            "id": "report-1",
                            "content": "QUARANTINED_MARKDOWN_BODY_CANARY",
                            "api_key": "QUARANTINED_CREDENTIAL_CANARY",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    assert cache.get(resource) is None

    quarantine_files = list((tmp_path / "quarantine").glob("*.json"))
    assert len(quarantine_files) == 1
    persisted = "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    assert "QUARANTINED_MARKDOWN_BODY_CANARY" not in persisted
    assert "QUARANTINED_CREDENTIAL_CANARY" not in persisted


def test_cache_round_trip_uses_integrity_envelope_and_owner_only_file(tmp_path: Path) -> None:
    cache = WorkspaceCache(tmp_path)
    snapshot = _snapshot(ResourceName.WATCHLIST)

    cache.put(snapshot)

    payload = json.loads(cache.path(ResourceName.WATCHLIST).read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "resource": "watchlist",
        "revision": snapshot.revision,
        "fetched_at": "2026-07-21T08:00:00Z",
        "checksum": revision_for(snapshot.data),
        "data": snapshot.data,
    }
    restored = cache.get(ResourceName.WATCHLIST)
    assert restored is not None
    assert restored.resource is ResourceName.WATCHLIST
    assert restored.revision == snapshot.revision
    assert restored.data == snapshot.data
    if hasattr(cache.path(ResourceName.WATCHLIST).stat(), "st_mode"):
        assert cache.path(ResourceName.WATCHLIST).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("resource", "unsafe_update", "canary"),
    [
        (ResourceName.STOCK_REPORTS, {"content": "MARKDOWN_BODY_CANARY"}, "MARKDOWN_BODY_CANARY"),
        (ResourceName.MARKET_RECAPS, {"content": "MARKDOWN_BODY_CANARY"}, "MARKDOWN_BODY_CANARY"),
        (ResourceName.PREFERENCES, {"api_key": "CREDENTIAL_VALUE_CANARY"}, "CREDENTIAL_VALUE_CANARY"),
        (
            ResourceName.PREFERENCES,
            {"feishu_webhook_url": "WEBHOOK_VALUE_CANARY"},
            "WEBHOOK_VALUE_CANARY",
        ),
    ],
)
def test_cache_rejects_report_bodies_and_credential_fields(
    tmp_path: Path,
    resource: ResourceName,
    unsafe_update: dict,
    canary: str,
) -> None:
    snapshot = _snapshot(resource)
    if resource is ResourceName.PREFERENCES:
        snapshot.data["preferences"].update(unsafe_update)
    else:
        snapshot.data["reports"][0].update(unsafe_update)
    cache = WorkspaceCache(tmp_path)

    with pytest.raises(ValueError, match="safe workspace DTO"):
        cache.put(snapshot)

    assert not cache.path(resource).exists()
    assert canary not in "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )


def test_online_get_caches_only_validated_resource_snapshot(tmp_path: Path) -> None:
    resource = ResourceName.STOCK_REPORTS
    remote = RemoteStub(
        [_response("GET", f"/api/workspace/resources/{resource.value}", _resource_payload(resource))]
    )
    adapter = CloudWorkspaceAdapter(remote, WorkspaceCache(tmp_path))

    result = adapter.get(resource)

    assert result == _snapshot(resource)
    assert remote.calls == [("GET", "/api/workspace/resources/stock_reports", {})]
    persisted = tmp_path.joinpath("stock_reports.json").read_text(encoding="utf-8")
    assert "content" not in persisted


def test_offline_get_returns_cache_marked_read_only(tmp_path: Path) -> None:
    cache = WorkspaceCache(tmp_path)
    cache.put(_snapshot(ResourceName.WATCHLIST))
    remote = RemoteStub()
    remote.error = httpx.ConnectError("offline")
    adapter = CloudWorkspaceAdapter(remote, cache)

    result = adapter.get(ResourceName.WATCHLIST)

    assert result.data == _data(ResourceName.WATCHLIST)
    assert result.offline_readonly is True


def test_bootstrap_caches_safe_resources_and_builds_offline_read_only_result(
    tmp_path: Path,
) -> None:
    remote = RemoteStub([_response("GET", "/api/workspace/bootstrap", _bootstrap_payload())])
    adapter = CloudWorkspaceAdapter(remote, WorkspaceCache(tmp_path))

    online = adapter.bootstrap()
    assert online["mode"] == "cloud"
    assert sorted(path.name for path in tmp_path.glob("*.json")) == sorted(
        f"{resource.value}.json" for resource in ResourceName
    )

    remote.error = httpx.ConnectError("offline")
    offline = adapter.bootstrap()

    assert offline["offline_readonly"] is True
    assert all(
        snapshot["offline_readonly"] is True for snapshot in offline["resources"].values()
    )
    serialized = json.dumps(offline)
    assert "MARKDOWN_BODY_CANARY" not in serialized
    assert "CREDENTIAL_VALUE_CANARY" not in serialized


def test_command_delegates_with_strong_etag_and_never_writes_on_offline(
    tmp_path: Path,
) -> None:
    revision = _snapshot(ResourceName.WATCHLIST).revision
    remote = RemoteStub()
    remote.error = httpx.ConnectError("offline")
    adapter = CloudWorkspaceAdapter(remote, WorkspaceCache(tmp_path))

    with pytest.raises(WorkspaceOfflineReadOnly):
        adapter.command(
            ResourceName.WATCHLIST,
            "add",
            {"symbol": "000403.SZ"},
            revision,
        )

    assert remote.calls == [
        (
            "POST",
            "/api/workspace/resources/watchlist/commands",
            {
                "headers": {"If-Match": f'"{revision}"'},
                "json": {"operation": "add", "payload": {"symbol": "000403.SZ"}},
            },
        )
    ]
    forbidden = ("pending", "queue", "outbox")
    assert not [
        path
        for path in adapter.cache.root.rglob("*")
        if any(word in path.name.lower() for word in forbidden)
    ]
    assert not list(adapter.cache.root.glob("*.json"))


@pytest.mark.parametrize(
    ("status_code", "expected_exception"),
    [
        (428, WorkspacePreconditionRequired),
        (412, WorkspaceRevisionConflict),
        (409, WorkspaceCommandConflict),
    ],
)
def test_command_maps_cloud_precondition_and_conflict_statuses(
    tmp_path: Path,
    status_code: int,
    expected_exception: type[RuntimeError],
) -> None:
    resource = ResourceName.WATCHLIST
    revision = _snapshot(resource).revision
    current_revision = "b" * 64
    response = httpx.Response(
        status_code,
        json={"detail": "REMOTE_BODY_CANARY"},
        headers={"etag": f'"{current_revision}"'},
        request=httpx.Request(
            "POST",
            "https://cloud.example/api/workspace/resources/watchlist/commands",
        ),
    )
    adapter = CloudWorkspaceAdapter(RemoteStub([response]), WorkspaceCache(tmp_path))

    with pytest.raises(expected_exception) as raised:
        adapter.command(resource, "add", {"symbol": "000403.SZ"}, revision)

    assert "REMOTE_BODY_CANARY" not in str(raised.value)
    if isinstance(raised.value, WorkspaceRevisionConflict):
        assert raised.value.current_revision == current_revision


def test_revisions_and_event_stream_use_exact_cloud_paths(tmp_path: Path) -> None:
    revisions = {resource.value: _snapshot(resource).revision for resource in ResourceName}
    event = {
        "id": "evt-1",
        "type": "workspace.resource.changed",
        "resource": "watchlist",
        "revision": revisions["watchlist"],
    }
    sse = httpx.Response(
        200,
        content=f"id: evt-1\nevent: workspace.resource.changed\ndata: {json.dumps(event)}\n\n",
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("GET", "https://cloud.example/api/workspace/events"),
    )
    remote = RemoteStub(
        [
            _response("GET", "/api/workspace/revisions", {"resources": revisions}),
            sse,
        ]
    )
    adapter = CloudWorkspaceAdapter(remote, WorkspaceCache(tmp_path))

    assert adapter.revisions() == {ResourceName(key): value for key, value in revisions.items()}
    assert list(adapter.stream_events()) == [event]
    assert [(method, path) for method, path, _kwargs in remote.calls] == [
        ("GET", "/api/workspace/revisions"),
        ("GET", "/api/workspace/events"),
    ]


class DelegatingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def bootstrap(self) -> dict:
        self.calls.append(("bootstrap",))
        return _bootstrap_payload()

    def get(self, resource: ResourceName) -> ResourceSnapshot:
        self.calls.append(("get", resource))
        return _snapshot(resource)

    def command(
        self,
        resource: ResourceName,
        operation: str,
        payload: dict,
        revision: str,
    ) -> ResourceSnapshot:
        self.calls.append(("command", resource, operation, payload, revision))
        return _snapshot(resource)

    def revisions(self) -> dict[ResourceName, str]:
        self.calls.append(("revisions",))
        return {resource: _snapshot(resource).revision for resource in ResourceName}

    def stream_events(self) -> Iterator[dict]:
        self.calls.append(("stream_events",))
        yield {
            "id": "evt-1",
            "type": "workspace.resource.changed",
            "resource": "watchlist",
            "revision": _snapshot(ResourceName.WATCHLIST).revision,
        }


def _delegating_app(adapter: DelegatingAdapter) -> FastAPI:
    app = FastAPI()
    app.state.workspace_adapter = adapter
    app.include_router(workspace_api.router)
    workspace_api.register_exception_handlers(app)
    return app


def test_desktop_workspace_http_routes_delegate_without_local_registry_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DelegatingAdapter()
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", False)
    monkeypatch.setattr(
        workspace_api,
        "snapshot_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local registry read")),
    )
    monkeypatch.setattr(
        workspace_api,
        "execute_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local registry write")),
    )
    revision = _snapshot(ResourceName.WATCHLIST).revision

    with TestClient(_delegating_app(adapter)) as client:
        assert client.get("/api/workspace/bootstrap").status_code == 200
        assert client.get("/api/workspace/resources/watchlist").status_code == 200
        assert client.get("/api/workspace/revisions").status_code == 200
        command = client.post(
            "/api/workspace/resources/watchlist/commands",
            headers={"If-Match": f'"{revision}"'},
            json={"operation": "add", "payload": {"symbol": "000403.SZ"}},
        )
        assert command.status_code == 200
        events = client.get("/api/workspace/events")
        assert events.status_code == 200
        assert "workspace.resource.changed" in events.text

    assert adapter.calls == [
        ("bootstrap",),
        ("get", ResourceName.WATCHLIST),
        ("revisions",),
        (
            "command",
            ResourceName.WATCHLIST,
            "add",
            {"symbol": "000403.SZ"},
            revision,
        ),
        ("stream_events",),
    ]


def test_workspace_adapter_installs_only_in_desktop_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    cloud_app = FastAPI()
    monkeypatch.delenv("TICKFLOW_DESKTOP_CLIENT", raising=False)

    main_module._initialize_desktop_workspace(cloud_app)

    assert not hasattr(cloud_app.state, "workspace_adapter")

    desktop_app = FastAPI()
    monkeypatch.setenv("TICKFLOW_DESKTOP_CLIENT", "1")
    main_module._initialize_desktop_workspace(desktop_app)

    adapter = desktop_app.state.workspace_adapter
    assert isinstance(adapter, CloudWorkspaceAdapter)
    assert adapter.cache.root == tmp_path / "cache" / "workspace"
