from __future__ import annotations

import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import polars as pl
import pytest

from app.api import settings as settings_api
from app.config import settings
from app.services import (
    ai_reports,
    backtest_summaries,
    market_recap_reports,
    preferences,
    stock_reports,
    watchlist,
)
from app.workspace.commands import execute_command
from app.workspace.locks import resource_lock
from app.workspace.models import (
    ResourceName,
    WorkspaceCommandConflict,
    WorkspacePreconditionRequired,
    WorkspaceRevisionConflict,
)
from app.workspace.registry import snapshot_resource


def _temp_files(path) -> list:
    return [item for item in path.iterdir() if ".tmp" in item.name]


def _summary(**updates) -> dict:
    value = {
        "id": "bt_001",
        "task": "strategy_backtest",
        "strategy_id": "macd_cross",
        "parameters_digest": "a" * 64,
        "stats": {"return_pct": 3.2, "max_drawdown_pct": -1.1},
        "started_at": "2026-07-20T16:00:00+08:00",
        "finished_at": "2026-07-20T16:00:05+08:00",
        "data_as_of": "2026-07-20",
        "engine": "tickflow",
        "execution_target": "local",
    }
    value.update(updates)
    return value


_REQUIRED_VERIFICATION = [
    "latest_close",
    "key_levels",
    "technical_indicators",
    "financial_data_availability",
    "material_news",
    "corporate_actions",
]


def _stock_content(**updates) -> str:
    frontmatter = {
        "type": "stock-analysis",
        "source_system": "tickflow-stock-panel",
        "data_scope": "watchlist-sample",
        "can_publish": False,
        "trading_advice": False,
        "verification_status": "pending",
        "needs_verification": _REQUIRED_VERIFICATION,
        "timeframe": "1d",
    }
    frontmatter.update(updates)
    verification = "\n".join(f"  - {item}" for item in frontmatter.pop("needs_verification"))
    scalar_lines = "\n".join(
        f"{key}: {str(value).lower() if isinstance(value, bool) else value}"
        for key, value in frontmatter.items()
    )
    return f"---\n{scalar_lines}\nneeds_verification:\n{verification}\n---\n# 个股日线复盘\n"


def _stock_report_payload(**updates) -> dict:
    value = {
        "symbol": "000403.SZ",
        "name": "派林生物",
        "focus": "",
        "title": "派林生物日线复盘",
        "content": _stock_content(),
        "summary": "仅供人工复核",
        "close": 23.45,
        "levels": {"support": 22.8, "resistance": 24.1},
        "data_as_of": "2026-07-20",
        "type": "stock-analysis",
        "source_system": "tickflow-stock-panel",
        "data_scope": "watchlist-sample",
        "can_publish": False,
        "trading_advice": False,
        "verification_status": "pending",
        "needs_verification": list(_REQUIRED_VERIFICATION),
        "timeframe": "1d",
    }
    value.update(updates)
    return value


def _market_recap_payload(**updates) -> dict:
    value = {
        "as_of": "2026-07-20",
        "focus": "",
        "content": "# 自选样本复盘\n",
        "summary": "仅限样本, 不代表全市场",
        "emotion_score": 50,
        "emotion_label": "中性",
        "title": "自选样本收盘复盘",
        "data_as_of": "2026-07-20",
        "verification_status": "pending",
        "can_publish": False,
        "trading_advice": False,
    }
    value.update(updates)
    return value


def test_watchlist_replace_normalizes_unique_symbols_and_preserves_order(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    rows = watchlist.replace_all(
        [
            {"symbol": " 000403.sz ", "added_at": "2026-07-20T16:00:00", "note": "A"},
            {"symbol": "600489.sh", "added_at": "2026-07-20T16:01:00", "note": "B"},
        ]
    )

    assert [row["symbol"] for row in rows] == ["000403.SZ", "600489.SH"]
    assert watchlist.list_symbols() == rows
    assert not _temp_files(tmp_path / "user_data")


@pytest.mark.parametrize(
    "rows",
    [
        [{"symbol": ""}],
        [{"symbol": "000403.SZ"}, {"symbol": " 000403.sz "}],
        [{"symbol": None}],
    ],
)
def test_watchlist_replace_rejects_invalid_rows_without_partial_write(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    user_data = tmp_path / "user_data"
    user_data.mkdir(parents=True)
    original = pl.DataFrame(
        {
            "symbol": ["600489.SH"],
            "added_at": ["2026-07-19T16:00:00"],
            "note": ["keep"],
        }
    )
    original.write_parquet(user_data / "watchlist.parquet")

    with pytest.raises(ValueError):
        watchlist.replace_all(rows)

    assert watchlist.list_symbols() == original.to_dicts()
    assert not _temp_files(user_data)


def test_watchlist_atomic_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    user_data = tmp_path / "user_data"
    user_data.mkdir(parents=True)
    original = pl.DataFrame(
        {
            "symbol": ["300059.SZ"],
            "added_at": ["2026-07-19T16:00:00"],
            "note": ["keep"],
        }
    )
    original.write_parquet(user_data / "watchlist.parquet")

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(watchlist.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        watchlist.replace_all([{"symbol": "000403.SZ"}])

    assert pl.read_parquet(user_data / "watchlist.parquet").to_dicts() == original.to_dicts()
    assert not _temp_files(user_data)


def test_watchlist_and_preferences_use_workspace_resource_locks():
    assert watchlist._lock() is resource_lock(ResourceName.WATCHLIST)
    assert preferences._lock() is resource_lock(ResourceName.PREFERENCES)


def test_preferences_atomic_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    preferences.save({"nav_order": ["watchlist"]})
    before = (tmp_path / "user_data" / "preferences.json").read_bytes()

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(preferences.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        preferences.save({"nav_hidden": ["review"]})

    assert (tmp_path / "user_data" / "preferences.json").read_bytes() == before
    assert not _temp_files(tmp_path / "user_data")


def test_client_preferences_are_allowlisted_and_canaries_never_serialize(monkeypatch):
    canaries = {
        "feishu_webhook_url": "https://open.feishu.cn/UNIQUE_FEISHU_CANARY",
        "feishu_webhook_secret": "UNIQUE_FEISHU_SECRET_CANARY",
        "wecom_webhook_url": "https://qyapi.weixin.qq.com/UNIQUE_WECOM_CANARY",
        "wecom_bot_id": "UNIQUE_WECOM_BOT_ID_CANARY",
        "wecom_bot_secret": "UNIQUE_WECOM_BOT_SECRET_CANARY",
        "system_notification_password": "UNIQUE_SYSTEM_PASSWORD_CANARY",
        "masked_api_key": "****-CANARY-FRAGMENT",
    }
    monkeypatch.setattr(
        preferences,
        "load",
        lambda: {
            "nav_order": ["watchlist"],
            "watchlist_columns": ["symbol", "price"],
            "realtime_quotes_enabled": True,
            **canaries,
        },
    )

    client = preferences.load_client_preferences()
    payload = json.dumps(client, ensure_ascii=False, sort_keys=True)

    assert client == {
        "nav_order": ["watchlist"],
        "watchlist_columns": ["symbol", "price"],
        "has_feishu_webhook": True,
        "has_wecom_webhook": True,
        "has_wecom_bot": True,
    }
    assert all(value not in payload for value in canaries.values())


@pytest.mark.parametrize(
    "key",
    [
        "unknown_setting",
        "api_key",
        "session_token",
        "db_password",
        "auth_cookie",
        "feishu_webhook_url",
        "wecom_bot_id",
        "notification_secret",
    ],
)
def test_merge_client_preferences_rejects_unknown_or_sensitive_keys_atomically(
    tmp_path, monkeypatch, key
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    preferences.save({"nav_order": ["watchlist"]})

    with pytest.raises(ValueError):
        preferences.merge_client_preferences({"nav_hidden": ["review"], key: "CANARY"})

    assert preferences.load() == {"nav_order": ["watchlist"]}


def test_settings_preferences_response_reuses_safe_client_projection(monkeypatch):
    canaries = [
        "https://open.feishu.cn/SETTINGS_RESPONSE_CANARY",
        "SETTINGS_RESPONSE_SECRET_CANARY",
        "SETTINGS_RESPONSE_BOT_ID_CANARY",
    ]
    monkeypatch.setattr(
        preferences,
        "load",
        lambda: {
            "indices_nav_pinned": False,
            "nav_order": ["watchlist", "review"],
            "feishu_webhook_url": canaries[0],
            "feishu_webhook_secret": canaries[1],
            "wecom_bot_id": canaries[2],
            "wecom_bot_secret": "configured",
        },
    )

    response = settings_api.get_preferences()
    payload = json.dumps(response, ensure_ascii=False, sort_keys=True)

    assert response == preferences.load_client_preferences()
    assert set(response) <= preferences.CLIENT_RESPONSE_KEYS
    assert all(canary not in payload for canary in canaries)
    assert not any("****" in str(value) for value in response.values())


def test_shared_report_stores_use_resource_locks_and_financial_store_remains_compatible(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    assert stock_reports._store._lock is resource_lock(ResourceName.STOCK_REPORTS)
    assert market_recap_reports._store._lock is resource_lock(ResourceName.MARKET_RECAPS)
    assert ai_reports._store._lock is not resource_lock(ResourceName.STOCK_REPORTS)

    saved = ai_reports.save_report({"symbol": "000403.SZ", "content": "financial"})
    assert ai_reports.list_reports()[0]["id"] == saved["id"]


def test_report_metadata_explicitly_excludes_markdown_and_unapproved_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    content_canary = "UNIQUE_REPORT_MARKDOWN_CANARY"
    stock_reports.save_report(
        {
            "id": "sar_1",
            "symbol": "000403.SZ",
            "name": "派林生物",
            "title": "派林生物日线复盘",
            "content": content_canary,
            "summary": "must not sync",
            "created_at": "2026-07-20T16:00:00+08:00",
            "data_as_of": "2026-07-20",
            "verification_status": "pending",
            "can_publish": False,
            "trading_advice": False,
        }
    )
    market_recap_reports.save_report(
        {
            "id": "mkr_1",
            "title": "收盘复盘",
            "content": content_canary,
            "emotion_score": 60,
            "created_at": "2026-07-20T16:00:00+08:00",
            "data_as_of": "2026-07-20",
            "verification_status": "pending",
            "can_publish": False,
            "trading_advice": False,
        }
    )

    stock_metadata = stock_reports.list_report_metadata()
    market_metadata = market_recap_reports.list_report_metadata()
    payload = json.dumps({"stock": stock_metadata, "market": market_metadata}, ensure_ascii=False)

    assert set(stock_metadata[0]) == {
        "id",
        "symbol",
        "title",
        "created_at",
        "data_as_of",
        "verification_status",
        "can_publish",
        "trading_advice",
    }
    assert set(market_metadata[0]) == {
        "id",
        "title",
        "created_at",
        "data_as_of",
        "verification_status",
        "can_publish",
        "trading_advice",
    }
    assert content_canary not in payload
    assert "summary" not in payload
    assert "emotion_score" not in payload


def test_backtest_summaries_persist_only_exact_allowed_bounded_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    store_type = importlib.import_module("app.services.backtest_summaries").BacktestSummaryStore
    store = store_type()

    saved = store.append(_summary())

    assert saved == _summary()
    assert store.list_summaries() == [_summary()]
    assert not _temp_files(tmp_path / "user_data")


@pytest.mark.parametrize(
    "invalid",
    [
        _summary(trades=[{"symbol": "000403.SZ"}]),
        _summary(api_key="BACKTEST_KEY_CANARY"),
        _summary(content="# markdown"),
        _summary(stats={"trades": 1}),
        _summary(stats={"nested": {"secret_key": "CANARY"}}),
        _summary(stats={"blob": "x" * (16 * 1024)}),
    ],
)
def test_backtest_summaries_reject_unsafe_or_oversized_payload_without_partial_write(
    tmp_path, monkeypatch, invalid
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    store_type = importlib.import_module("app.services.backtest_summaries").BacktestSummaryStore
    store = store_type()
    store.append(_summary())

    with pytest.raises(ValueError):
        store.append(invalid)

    assert store.list_summaries() == [_summary()]


def test_registry_missing_resource_uses_epoch_and_stable_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    registry = importlib.import_module("app.workspace.registry")

    first = registry.snapshot_resource(ResourceName.WATCHLIST)
    second = registry.snapshot_resource(ResourceName.WATCHLIST)

    assert first.updated_at == datetime.fromtimestamp(0, UTC)
    assert second.updated_at == first.updated_at
    assert second.revision == first.revision
    assert first.data == {"symbols": []}


def test_registry_projects_report_metadata_without_content(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    registry = importlib.import_module("app.workspace.registry")
    stock_reports.save_report(
        {
            "id": "sar_registry",
            "symbol": "000403.SZ",
            "title": "日线复盘",
            "content": "REGISTRY_CONTENT_CANARY",
            "created_at": "2026-07-20T16:00:00+08:00",
        }
    )

    data = registry.load_resource(ResourceName.STOCK_REPORTS)

    assert data == {
        "reports": [
            {
                "id": "sar_registry",
                "symbol": "000403.SZ",
                "title": "日线复盘",
                "created_at": "2026-07-20T16:00:00+08:00",
            }
        ],
    }
    assert "REGISTRY_CONTENT_CANARY" not in json.dumps(data, ensure_ascii=False)


def test_command_requires_revision_before_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    with pytest.raises(WorkspacePreconditionRequired):
        execute_command(
            ResourceName.WATCHLIST,
            "add",
            {"symbol": "000403.SZ", "note": ""},
            None,
        )

    assert watchlist.list_symbols() == []


@pytest.mark.parametrize(
    "revision",
    ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64, 123],
)
def test_command_rejects_malformed_revision_without_snapshot(monkeypatch, revision):
    commands = importlib.import_module("app.workspace.commands")

    def unexpected_snapshot(_resource):
        raise AssertionError("snapshot must not be read for malformed revision")

    monkeypatch.setattr(commands, "snapshot_resource", unexpected_snapshot)

    expected = WorkspacePreconditionRequired if revision == "" else ValueError
    with pytest.raises(expected):
        execute_command(ResourceName.WATCHLIST, "clear", {}, revision)


def test_command_rejects_unknown_resource_as_input_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    with pytest.raises(ValueError, match="unknown workspace resource"):
        execute_command("not-a-resource", "clear", {}, "a" * 64)


def test_stale_revision_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = snapshot_resource(ResourceName.WATCHLIST)

    execute_command(
        ResourceName.WATCHLIST,
        "add",
        {"symbol": "000403.SZ", "note": ""},
        first.revision,
    )

    with pytest.raises(WorkspaceRevisionConflict) as exc_info:
        execute_command(
            ResourceName.WATCHLIST,
            "add",
            {"symbol": "600489.SH", "note": ""},
            first.revision,
        )

    assert exc_info.value.resource is ResourceName.WATCHLIST
    assert exc_info.value.current_revision == snapshot_resource(ResourceName.WATCHLIST).revision
    assert [item["symbol"] for item in watchlist.list_symbols()] == ["000403.SZ"]


def test_unknown_operation_is_business_conflict_and_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = snapshot_resource(ResourceName.WATCHLIST)

    with pytest.raises(WorkspaceCommandConflict, match="unsupported operation"):
        execute_command(ResourceName.WATCHLIST, "replace", {}, first.revision)

    assert snapshot_resource(ResourceName.WATCHLIST).revision == first.revision
    assert not (tmp_path / "user_data" / "watchlist.parquet").exists()


def test_watchlist_batch_add_normalizes_deduplicates_and_preserves_requested_order(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    watchlist.replace_all(
        [
            {"symbol": "300059.SZ", "note": "existing"},
            {"symbol": "600489.SH", "note": "old"},
        ]
    )
    first = snapshot_resource(ResourceName.WATCHLIST)
    payload = {
        "symbols": [" 000403.sz ", "600489.sh", "000403.SZ", " 300750.sz"],
        "note": "sample",
    }
    original = json.loads(json.dumps(payload))

    result = execute_command(ResourceName.WATCHLIST, "batch_add", payload, first.revision)

    rows = result.data["symbols"]
    assert [row["symbol"] for row in rows] == [
        "000403.SZ",
        "600489.SH",
        "300750.SZ",
        "300059.SZ",
    ]
    assert [row["note"] for row in rows[:3]] == ["sample"] * 3
    assert rows[3]["note"] == "existing"
    assert payload == original
    assert watchlist.list_symbols() == rows


def test_watchlist_supported_operations_use_fixed_payload_schemas(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    current = snapshot_resource(ResourceName.WATCHLIST)
    current = execute_command(
        ResourceName.WATCHLIST,
        "add",
        {"symbol": "000403.sz", "note": "first"},
        current.revision,
    )
    current = execute_command(
        ResourceName.WATCHLIST,
        "add",
        {"symbol": "600489.sh", "note": "second"},
        current.revision,
    )
    current = execute_command(
        ResourceName.WATCHLIST,
        "move_to_top",
        {"symbol": "000403.sz"},
        current.revision,
    )
    assert [row["symbol"] for row in current.data["symbols"]] == [
        "000403.SZ",
        "600489.SH",
    ]

    current = execute_command(
        ResourceName.WATCHLIST,
        "remove",
        {"symbol": "600489.sh"},
        current.revision,
    )
    assert [row["symbol"] for row in current.data["symbols"]] == ["000403.SZ"]

    with pytest.raises(ValueError):
        execute_command(
            ResourceName.WATCHLIST,
            "clear",
            {"unexpected": True},
            current.revision,
        )
    assert watchlist.list_symbols()

    current = execute_command(ResourceName.WATCHLIST, "clear", {}, current.revision)
    assert current.data == {"symbols": []}


def test_preference_command_allows_only_safe_merge_and_does_not_mutate_payload(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = snapshot_resource(ResourceName.PREFERENCES)
    payload = {"nav_order": ["watchlist", "review"], "indices_nav_pinned": False}
    original = json.loads(json.dumps(payload))

    result = execute_command(
        ResourceName.PREFERENCES,
        "merge_safe",
        payload,
        first.revision,
    )

    assert result.data["preferences"]["nav_order"] == ["watchlist", "review"]
    assert payload == original
    current_revision = result.revision
    with pytest.raises(ValueError):
        execute_command(
            ResourceName.PREFERENCES,
            "merge_safe",
            {"feishu_webhook_url": "https://example.invalid/CANARY"},
            current_revision,
        )
    assert snapshot_resource(ResourceName.PREFERENCES).revision == current_revision


def test_stock_report_append_validates_frontmatter_and_keeps_body_out_of_snapshot(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = snapshot_resource(ResourceName.STOCK_REPORTS)
    payload = _stock_report_payload()
    original = json.loads(json.dumps(payload, ensure_ascii=False))

    result = execute_command(ResourceName.STOCK_REPORTS, "append", payload, first.revision)

    saved = stock_reports.list_reports()[0]
    assert saved["content"] == payload["content"]
    assert saved["data_scope"] == "watchlist-sample"
    assert saved["can_publish"] is False
    assert saved["trading_advice"] is False
    assert saved["verification_status"] == "pending"
    assert payload == original
    assert payload["content"] not in json.dumps(result.data, ensure_ascii=False)
    assert "content" not in result.data["reports"][0]


@pytest.mark.parametrize(
    ("payload_update", "frontmatter_update"),
    [
        ({"can_publish": True}, {}),
        ({"trading_advice": True}, {}),
        ({"verification_status": "verified"}, {}),
        ({"data_scope": "full-market"}, {}),
        ({"source_system": "other"}, {}),
        ({"timeframe": "30m"}, {}),
        ({"needs_verification": _REQUIRED_VERIFICATION[:-1]}, {}),
        ({}, {"can_publish": True}),
        ({}, {"trading_advice": True}),
        ({}, {"verification_status": "verified"}),
        ({}, {"data_scope": "full-market"}),
        ({}, {"source_system": "other"}),
        ({}, {"timeframe": "30m"}),
        ({}, {"needs_verification": _REQUIRED_VERIFICATION[:-1]}),
    ],
)
def test_stock_report_rejects_unsafe_or_inconsistent_safety_fields_without_write(
    tmp_path, monkeypatch, payload_update, frontmatter_update
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = snapshot_resource(ResourceName.STOCK_REPORTS)
    payload = _stock_report_payload(**payload_update)
    if frontmatter_update:
        payload["content"] = _stock_content(**frontmatter_update)

    with pytest.raises(ValueError):
        execute_command(ResourceName.STOCK_REPORTS, "append", payload, first.revision)

    assert stock_reports.list_reports() == []
    assert snapshot_resource(ResourceName.STOCK_REPORTS).revision == first.revision


def test_report_append_payloads_forbid_unknown_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    stock_revision = snapshot_resource(ResourceName.STOCK_REPORTS).revision
    with pytest.raises(ValueError):
        execute_command(
            ResourceName.STOCK_REPORTS,
            "append",
            _stock_report_payload(webhook="CANARY"),
            stock_revision,
        )

    market_revision = snapshot_resource(ResourceName.MARKET_RECAPS).revision
    with pytest.raises(ValueError):
        execute_command(
            ResourceName.MARKET_RECAPS,
            "append",
            _market_recap_payload(body="CANARY"),
            market_revision,
        )

    assert stock_reports.list_reports() == []
    assert market_recap_reports.list_reports() == []


def test_market_recap_append_keeps_content_out_of_workspace_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = snapshot_resource(ResourceName.MARKET_RECAPS)
    payload = _market_recap_payload(content="MARKET_BODY_CANARY")

    result = execute_command(ResourceName.MARKET_RECAPS, "append", payload, first.revision)

    assert market_recap_reports.list_reports()[0]["content"] == "MARKET_BODY_CANARY"
    serialized = json.dumps(result.data, ensure_ascii=False)
    assert "MARKET_BODY_CANARY" not in serialized
    assert "content" not in result.data["reports"][0]


def test_backtest_commands_use_exact_summary_schema_and_atomic_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = snapshot_resource(ResourceName.BACKTEST_SUMMARIES)
    appended = execute_command(
        ResourceName.BACKTEST_SUMMARIES,
        "append",
        _summary(),
        first.revision,
    )
    assert appended.data == {"summaries": [_summary()]}

    with pytest.raises(ValueError):
        execute_command(
            ResourceName.BACKTEST_SUMMARIES,
            "append",
            _summary(content="# markdown"),
            appended.revision,
        )
    assert backtest_summaries.list_summaries() == [_summary()]

    deleted = execute_command(
        ResourceName.BACKTEST_SUMMARIES,
        "delete",
        {"id": "bt_001"},
        appended.revision,
    )
    assert deleted.data == {"summaries": []}


@pytest.mark.parametrize(
    ("resource", "payload"),
    [
        (ResourceName.STOCK_REPORTS, _stock_report_payload()),
        (ResourceName.MARKET_RECAPS, _market_recap_payload()),
        (ResourceName.BACKTEST_SUMMARIES, _summary()),
    ],
)
def test_report_delete_requires_nonempty_existing_id(tmp_path, monkeypatch, resource, payload):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = snapshot_resource(resource)

    with pytest.raises(ValueError):
        execute_command(resource, "delete", {"id": "  "}, first.revision)
    with pytest.raises(WorkspaceCommandConflict, match="not found"):
        execute_command(resource, "delete", {"id": "missing"}, first.revision)

    appended = execute_command(resource, "append", payload, first.revision)
    saved_id = (
        appended.data["summaries"][0]["id"]
        if resource is ResourceName.BACKTEST_SUMMARIES
        else appended.data["reports"][0]["id"]
    )
    deleted = execute_command(resource, "delete", {"id": saved_id}, appended.revision)
    collection = "summaries" if resource is ResourceName.BACKTEST_SUMMARIES else "reports"
    assert deleted.data[collection] == []


def test_two_writers_on_same_revision_yield_one_success_and_one_revision_conflict(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    initial = snapshot_resource(ResourceName.WATCHLIST)
    barrier = Barrier(2)

    def write(symbol: str):
        barrier.wait(timeout=5)
        try:
            return execute_command(
                ResourceName.WATCHLIST,
                "add",
                {"symbol": symbol, "note": ""},
                initial.revision,
            )
        except WorkspaceRevisionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["000403.SZ", "600489.SH"]))

    successes = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, WorkspaceRevisionConflict)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert len(watchlist.list_symbols()) == 1
    assert conflicts[0].current_revision == successes[0].revision
