from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
import yaml
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.config import settings
from app.main import app
from app.services import (
    auth as auth_service,
)
from app.services import (
    market_recap_reports,
    preferences,
    stock_reports,
    watchlist,
)
from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet

STOCK_BODY_CANARY = "UNIQUE_STOCK_MARKDOWN_BODY_CANARY"
MARKET_BODY_CANARY = "UNIQUE_MARKET_MARKDOWN_BODY_CANARY"
SECRET_CANARIES = {
    "UNIQUE_TICKFLOW_API_KEY_CANARY",
    "UNIQUE_TUSHARE_TOKEN_CANARY",
    "UNIQUE_DEEPSEEK_API_KEY_CANARY",
    "UNIQUE_FEISHU_WEBHOOK_CANARY",
    "UNIQUE_FEISHU_SECRET_CANARY",
    "UNIQUE_WECOM_WEBHOOK_CANARY",
    "UNIQUE_WECOM_BOT_ID_CANARY",
    "UNIQUE_WECOM_BOT_SECRET_CANARY",
    "UNIQUE_COOKIE_CANARY",
    "UNIQUE_PASSWORD_CANARY",
}
REPORT_CANARIES = {
    STOCK_BODY_CANARY,
    MARKET_BODY_CANARY,
    "UNIQUE_STOCK_REPORT_UNKNOWN_FIELD_CANARY",
    "UNIQUE_MARKET_REPORT_UNKNOWN_FIELD_CANARY",
}
ALL_CANARIES = SECRET_CANARIES | REPORT_CANARIES


class FakeRepo:
    def __init__(self, data_dir: Path, data_as_of: object = date(2026, 7, 20)) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)
        self._data_as_of = data_as_of

    def get_enriched_latest(self):
        return pl.DataFrame(), self._data_as_of

    def get_name_map(self, _symbols):
        return {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", False)
    monkeypatch.setattr(app.state, "repo", FakeRepo(tmp_path), raising=False)
    monkeypatch.setattr(
        app.state,
        "capabilities",
        CapabilitySet({Cap.KLINE_DAILY_BY_SYMBOL: CapabilityLimits(rpm=48, batch=10)}),
        raising=False,
    )
    monkeypatch.setattr(auth_service, "is_configured", lambda: True)
    monkeypatch.setattr(auth_service, "is_valid_session", lambda token: token == "valid-session")
    test_client = TestClient(app)
    test_client.cookies.set(auth_api.COOKIE_NAME, "valid-session")
    yield test_client
    test_client.close()


@pytest.fixture
def unauthenticated_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", False)
    monkeypatch.setattr(app.state, "repo", FakeRepo(tmp_path), raising=False)
    monkeypatch.setattr(app.state, "capabilities", CapabilitySet(), raising=False)
    monkeypatch.setattr(auth_service, "is_configured", lambda: True)
    monkeypatch.setattr(auth_service, "is_valid_session", lambda _token: False)
    test_client = TestClient(app)
    yield test_client
    test_client.close()


def _seed_sensitive_data(tmp_path: Path) -> None:
    preferences.save(
        {
            "nav_order": ["watchlist", "review"],
            "indices_nav_pinned": True,
            "feishu_webhook_url": "UNIQUE_FEISHU_WEBHOOK_CANARY",
            "feishu_webhook_secret": "UNIQUE_FEISHU_SECRET_CANARY",
            "wecom_webhook_url": "UNIQUE_WECOM_WEBHOOK_CANARY",
            "wecom_bot_id": "UNIQUE_WECOM_BOT_ID_CANARY",
            "wecom_bot_secret": "UNIQUE_WECOM_BOT_SECRET_CANARY",
            "api_key": "UNIQUE_TICKFLOW_API_KEY_CANARY",
            "session_token": "UNIQUE_COOKIE_CANARY",
            "db_password": "UNIQUE_PASSWORD_CANARY",
        }
    )
    secrets_path = tmp_path / "user_data" / "secrets.json"
    secrets_path.write_text(
        json.dumps(
            {
                "tickflow_api_key": "UNIQUE_TICKFLOW_API_KEY_CANARY",
                "tushare_token": "UNIQUE_TUSHARE_TOKEN_CANARY",
                "ai_api_key": "UNIQUE_DEEPSEEK_API_KEY_CANARY",
            }
        ),
        encoding="utf-8",
    )
    stock_reports.save_report(
        {
            "id": "sar_exact",
            "symbol": "000403.SZ",
            "name": "派林生物",
            "title": "派林生物日线复盘",
            "content": STOCK_BODY_CANARY,
            "summary": "not part of synced metadata",
            "created_at": "2026-07-20T16:00:00+08:00",
            "data_as_of": "2026-07-20",
            "verification_status": "pending",
            "can_publish": False,
            "trading_advice": False,
            "private_blob": "UNIQUE_STOCK_REPORT_UNKNOWN_FIELD_CANARY",
        }
    )
    market_recap_reports.save_report(
        {
            "id": "mkr_exact",
            "title": "自选样本收盘复盘",
            "content": MARKET_BODY_CANARY,
            "summary": "not part of synced metadata",
            "created_at": "2026-07-20T16:10:00+08:00",
            "data_as_of": "2026-07-20",
            "verification_status": "pending",
            "can_publish": False,
            "trading_advice": False,
            "private_blob": "UNIQUE_MARKET_REPORT_UNKNOWN_FIELD_CANARY",
        }
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_workspace_reads_require_authentication(unauthenticated_client):
    response = unauthenticated_client.get("/api/workspace/bootstrap")

    assert response.status_code == 401


def test_workspace_openapi_exposes_explicit_response_dtos():
    paths = app.openapi()["paths"]

    bootstrap_schema = paths["/api/workspace/bootstrap"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    resource_schema = paths["/api/workspace/resources/{name}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    revisions_schema = paths["/api/workspace/revisions"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    command_schema = paths["/api/workspace/resources/{name}/commands"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    stock_body_schema = paths["/api/stock-analysis/reports/{report_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    market_body_schema = paths["/api/market-recap/reports/{report_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]

    assert bootstrap_schema["$ref"].endswith("/WorkspaceBootstrapDTO")
    assert resource_schema["$ref"].endswith("/ResourceResponseDTO")
    assert revisions_schema["$ref"].endswith("/WorkspaceRevisionsDTO")
    assert command_schema["$ref"].endswith("/ResourceResponseDTO")
    assert stock_body_schema["$ref"].endswith("/StockReportBodyResponseDTO")
    assert market_body_schema["$ref"].endswith("/MarketRecapBodyResponseDTO")


def test_bootstrap_uses_explicit_contract_and_metadata_only_reports(client, tmp_path):
    _seed_sensitive_data(tmp_path)

    response = client.get("/api/workspace/bootstrap")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["schema_version"] == 1
    assert body["mode"] == "cloud"
    assert body["data_as_of"] == "2026-07-20"
    assert set(body["capabilities"]) == {"label", "capabilities"}
    assert set(body["resources"]) == {
        "watchlist",
        "preferences",
        "stock_reports",
        "market_recaps",
        "backtest_summaries",
    }
    assert body["resources"]["preferences"]["data"]["preferences"] == {
        "indices_nav_pinned": True,
        "nav_order": ["watchlist", "review"],
        "has_feishu_webhook": True,
        "has_wecom_webhook": True,
        "has_wecom_bot": True,
    }
    assert body["resources"]["stock_reports"]["data"]["reports"] == [
        {
            "id": "sar_exact",
            "symbol": "000403.SZ",
            "title": "派林生物日线复盘",
            "created_at": "2026-07-20T16:00:00+08:00",
            "data_as_of": "2026-07-20",
            "verification_status": "pending",
            "can_publish": False,
            "trading_advice": False,
        }
    ]


@pytest.mark.parametrize(
    "data_as_of, expected",
    [
        (date(2026, 7, 20), "2026-07-20"),
        (datetime(2026, 7, 20, 15, 0), "2026-07-20"),
        ("2026-07-20", "2026-07-20"),
        ("not-a-date", None),
        (None, None),
        ({"unexpected": True}, None),
    ],
)
def test_bootstrap_data_as_of_is_robust_or_null(
    client, monkeypatch, tmp_path, data_as_of, expected
):
    monkeypatch.setattr(app.state, "repo", FakeRepo(tmp_path, data_as_of), raising=False)

    response = client.get("/api/workspace/bootstrap")

    assert response.status_code == 200
    assert response.json()["data_as_of"] == expected


def test_bootstrap_data_as_of_failure_is_null(client, monkeypatch):
    class BrokenRepo:
        def get_enriched_latest(self):
            raise RuntimeError("UNIQUE_REPOSITORY_ERROR_CANARY")

    monkeypatch.setattr(app.state, "repo", BrokenRepo(), raising=False)

    response = client.get("/api/workspace/bootstrap")

    assert response.status_code == 200
    assert response.json()["data_as_of"] is None
    assert "UNIQUE_REPOSITORY_ERROR_CANARY" not in response.text


@pytest.mark.parametrize(
    "resource",
    ["watchlist", "preferences", "stock_reports", "market_recaps", "backtest_summaries"],
)
def test_resource_get_returns_strong_etag_and_private_no_store(client, resource):
    response = client.get(f"/api/workspace/resources/{resource}")

    assert response.status_code == 200
    assert response.headers["etag"] == f'"{response.json()["revision"]}"'
    assert response.headers["cache-control"] == "private, no-store"
    assert len(response.json()["revision"]) == 64


def test_revisions_returns_all_resources_without_data(client):
    response = client.get("/api/workspace/revisions")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert set(body) == {"server_time", "resources"}
    assert datetime.fromisoformat(body["server_time"]).tzinfo is not None
    assert set(body["resources"]) == {
        "watchlist",
        "preferences",
        "stock_reports",
        "market_recaps",
        "backtest_summaries",
    }
    assert all(
        isinstance(revision, str) and len(revision) == 64 for revision in body["resources"].values()
    )
    assert "data" not in body


def test_full_bootstrap_resource_and_list_json_contains_no_secret_or_report_canary(
    client, tmp_path
):
    _seed_sensitive_data(tmp_path)
    responses = [
        client.get("/api/workspace/bootstrap"),
        client.get("/api/workspace/revisions"),
        client.get("/api/settings/preferences"),
        client.get("/api/stock-analysis/reports"),
        client.get("/api/market-recap/reports"),
    ]
    responses.extend(
        client.get(f"/api/workspace/resources/{resource}")
        for resource in (
            "watchlist",
            "preferences",
            "stock_reports",
            "market_recaps",
            "backtest_summaries",
        )
    )

    full_text = "\n".join(response.text for response in responses)
    assert all(response.status_code == 200 for response in responses)
    assert all(canary not in full_text for canary in ALL_CANARIES)
    for forbidden_key in (
        "tickflow_api_key",
        "tushare_token",
        "ai_api_key",
        "feishu_webhook_url",
        "feishu_webhook_secret",
        "wecom_webhook_url",
        "wecom_bot_id",
        "wecom_bot_secret",
        "private_blob",
        "content",
        "summary",
    ):
        assert forbidden_key not in full_text


def test_report_lists_are_metadata_only_and_bodies_require_exact_authenticated_get(
    client, tmp_path
):
    _seed_sensitive_data(tmp_path)

    stock_list = client.get("/api/stock-analysis/reports")
    market_list = client.get("/api/market-recap/reports")
    stock_body = client.get("/api/stock-analysis/reports/sar_exact")
    market_body = client.get("/api/market-recap/reports/mkr_exact")
    missing = client.get("/api/stock-analysis/reports/sar")

    assert STOCK_BODY_CANARY not in stock_list.text
    assert MARKET_BODY_CANARY not in market_list.text
    assert stock_body.status_code == 200
    assert stock_body.json()["report"]["content"] == STOCK_BODY_CANARY
    assert stock_body.json()["report"]["summary"] == "not part of synced metadata"
    assert stock_body.headers["cache-control"] == "private, no-store"
    assert "private_blob" not in stock_body.text
    assert market_body.status_code == 200
    assert market_body.json()["report"]["content"] == MARKET_BODY_CANARY
    assert market_body.json()["report"]["summary"] == "not part of synced metadata"
    assert market_body.headers["cache-control"] == "private, no-store"
    assert "private_blob" not in market_body.text
    assert missing.status_code == 404
    assert "sar_exact" not in missing.text


def test_workspace_command_is_disabled_by_default(client):
    revision = client.get("/api/workspace/resources/watchlist").json()["revision"]

    response = client.post(
        "/api/workspace/resources/watchlist/commands",
        headers={"If-Match": f'"{revision}"'},
        json={"operation": "add", "payload": {"symbol": "000403.SZ"}},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "WORKSPACE_SYNC_DISABLED"
    assert watchlist.list_symbols() == []


def test_workspace_command_success_returns_new_snapshot_and_etag(client, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", True)
    revision = client.get("/api/workspace/resources/watchlist").json()["revision"]

    response = client.post(
        "/api/workspace/resources/watchlist/commands",
        headers={"If-Match": f'"{revision}"'},
        json={"operation": "add", "payload": {"symbol": "000403.SZ"}},
    )

    assert response.status_code == 200
    assert response.headers["etag"] == f'"{response.json()["revision"]}"'
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["data"]["symbols"][0]["symbol"] == "000403.SZ"


def test_command_rejects_missing_malformed_and_stale_if_match(client, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", True)
    path = "/api/workspace/resources/watchlist/commands"
    command = {"operation": "add", "payload": {"symbol": "000403.SZ"}}

    missing = client.post(path, json=command)
    malformed = client.post(path, headers={"If-Match": "stale"}, json=command)
    stale = client.post(path, headers={"If-Match": f'"{"b" * 64}"'}, json=command)

    assert missing.status_code == 428
    assert missing.json()["code"] == "WORKSPACE_PRECONDITION_REQUIRED"
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "INVALID_IF_MATCH"
    assert stale.status_code == 412
    assert stale.json()["code"] == "WORKSPACE_REVISION_CONFLICT"
    assert (
        stale.headers["etag"] == (client.get("/api/workspace/resources/watchlist").headers["etag"])
    )
    assert watchlist.list_symbols() == []


def test_command_maps_business_conflict_and_hides_invalid_payload_details(client, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", True)
    stock_revision = client.get("/api/workspace/resources/stock_reports").json()["revision"]
    watch_revision = client.get("/api/workspace/resources/watchlist").json()["revision"]

    conflict = client.post(
        "/api/workspace/resources/stock_reports/commands",
        headers={"If-Match": f'"{stock_revision}"'},
        json={"operation": "delete", "payload": {"id": "missing"}},
    )
    invalid = client.post(
        "/api/workspace/resources/watchlist/commands",
        headers={"If-Match": f'"{watch_revision}"'},
        json={
            "operation": "add",
            "payload": {
                "symbol": "000403.SZ",
                "api_key": "UNIQUE_INVALID_COMMAND_SECRET_CANARY",
            },
        },
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "WORKSPACE_COMMAND_CONFLICT"
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_WORKSPACE_COMMAND"
    assert "UNIQUE_INVALID_COMMAND_SECRET_CANARY" not in invalid.text
    assert "traceback" not in (conflict.text + invalid.text).lower()


LEGACY_SHARED_MUTATIONS = [
    ("POST", "/api/watchlist", {"json": {"symbol": "000403.SZ"}}),
    ("POST", "/api/watchlist/batch", {"json": {"symbols": ["000403.SZ"]}}),
    ("POST", "/api/watchlist/000403.SZ/top", {}),
    ("DELETE", "/api/watchlist/000403.SZ", {}),
    ("DELETE", "/api/watchlist", {}),
    ("PUT", "/api/settings/preferences/data-providers", {"json": {}}),
    ("PUT", "/api/settings/preferences/nav-order", {"json": {"nav_order": []}}),
    ("PUT", "/api/settings/preferences/nav-hidden", {"json": {"nav_hidden": []}}),
    ("PUT", "/api/settings/preferences/watchlist-columns", {"json": {"columns": []}}),
    (
        "PUT",
        "/api/settings/preferences/screener-result-columns",
        {"json": {"columns": []}},
    ),
    (
        "PUT",
        "/api/settings/preferences/indices-nav-pinned",
        {"json": {"indices_nav_pinned": False}},
    ),
    (
        "PUT",
        "/api/settings/preferences/realtime-monitor",
        {"json": {"screener_auto_run": False, "sidebar_index_symbols": []}},
    ),
    (
        "POST",
        "/api/stock-analysis/reports",
        {"json": {"symbol": "000403.SZ", "content": "legacy"}},
    ),
    ("DELETE", "/api/stock-analysis/reports/sar_exact", {}),
    (
        "POST",
        "/api/market-recap/reports",
        {"json": {"as_of": "2026-07-20", "content": "legacy"}},
    ),
    ("DELETE", "/api/market-recap/reports/mkr_exact", {}),
]


@pytest.mark.parametrize(("method", "path", "kwargs"), LEGACY_SHARED_MUTATIONS)
def test_every_legacy_shared_mutation_is_blocked_before_side_effect_when_gate_enabled(
    client, tmp_path, monkeypatch, method, path, kwargs
):
    _seed_sensitive_data(tmp_path)
    watchlist.add("600489.SH")
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", True)
    before = _tree_bytes(tmp_path)

    response = client.request(method, path, **kwargs)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Legacy workspace write is disabled while versioned sync is enabled",
        "code": "LEGACY_WORKSPACE_WRITE_DISABLED",
    }
    assert _tree_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "path",
    [
        "/api/watchlist/import-image",
        "/api/stock-analysis/analyze",
        "/api/market-recap/analyze",
    ],
)
def test_gate_does_not_block_non_mutating_post_operations(monkeypatch, path):
    from app.api.workspace import legacy_workspace_write_response

    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", True)
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path=path))

    assert legacy_workspace_write_response(request) is None


def test_gate_false_preserves_representative_legacy_writes(client, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", False)
    monkeypatch.setattr("app.jobs.daily_pipeline._maybe_push_review", lambda *_args: None)

    watch = client.post("/api/watchlist", json={"symbol": "000403.SZ"})
    pref = client.put(
        "/api/settings/preferences/nav-order",
        json={"nav_order": ["watchlist"]},
    )
    stock = client.post(
        "/api/stock-analysis/reports",
        json={"symbol": "000403.SZ", "content": "legacy stock body"},
    )
    market = client.post(
        "/api/market-recap/reports",
        json={"as_of": "2026-07-20", "content": "legacy market body"},
    )

    assert watch.status_code == 200
    assert pref.status_code == 200
    assert stock.status_code == 200
    assert market.status_code == 200
    assert watchlist.list_symbols()[0]["symbol"] == "000403.SZ"
    assert preferences.load()["nav_order"] == ["watchlist"]
    assert stock_reports.list_reports()[0]["content"] == "legacy stock body"
    assert market_recap_reports.list_reports()[0]["content"] == "legacy market body"


def test_compose_explicitly_passes_workspace_sync_gate_default_false():
    compose_path = Path(__file__).resolve().parents[3] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    environment = compose["services"]["app"]["environment"]

    assert "WORKSPACE_SYNC_ENABLED=${WORKSPACE_SYNC_ENABLED:-false}" in environment
