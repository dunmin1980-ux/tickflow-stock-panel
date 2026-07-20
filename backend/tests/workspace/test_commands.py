from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime

import polars as pl
import pytest

from app.api import settings as settings_api
from app.config import settings
from app.services import ai_reports, market_recap_reports, preferences, stock_reports, watchlist
from app.workspace.locks import resource_lock
from app.workspace.models import ResourceName


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
