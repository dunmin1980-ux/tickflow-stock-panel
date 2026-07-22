from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.workspace.storage_validation import (
    _KNOWN_PREFERENCE_FIELDS,
    _SECRET_TEXT_FIELDS,
    WorkspaceStorageError,
    validate_storage_files,
)


def test_missing_optional_workspace_files_are_valid(tmp_path: Path) -> None:
    validate_storage_files(tmp_path)


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("preferences.json", "{"),
        ("preferences.json", '{"indices_nav_pinned": "yes"}'),
        ("preferences.json", '{"minute_sync_days": "broken"}'),
        ("preferences.json", '{"unknown_preference": true}'),
        ("secrets.json", "[]"),
        ("secrets.json", '{"tickflow_api_key": ["not-a-string"]}'),
        ("secrets.json", '{"unknown_secret": "value"}'),
        ("auth.json", '{"sessions": []}'),
        ("auth.json", '{"sessions": {}, "updated_at": "yesterday"}'),
        ("ai_stock_reports.json", "{}"),
        (
            "ai_stock_reports.json",
            '[{"id":"sar-broken","symbol":"000403.SZ",'
            '"content":42,"created_at":"2026-07-22T15:00:00"}]',
        ),
        (
            "ai_stock_reports.json",
            '[{"id":"sar-risk","symbol":"000403.SZ","content":"report",'
            '"created_at":"2026-07-22T15:00:00","data_scope":"watchlist-sample",'
            '"can_publish":true}]',
        ),
        ("ai_market_recaps.json", '["not-an-object"]'),
        (
            "ai_market_recaps.json",
            '[{"id":"mkr-broken","as_of":"2026-07-22",'
            '"content":42,"created_at":"2026-07-22T15:00:00"}]',
        ),
        ("backtest_summaries.json", "[{}]"),
    ],
)
def test_corrupt_or_lossy_workspace_files_fail_closed(
    tmp_path: Path,
    filename: str,
    payload: str,
) -> None:
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    (user_data / filename).write_text(payload, encoding="utf-8")

    with pytest.raises(WorkspaceStorageError, match=filename):
        validate_storage_files(tmp_path)


def test_valid_storage_files_pass_without_exposing_values(tmp_path: Path) -> None:
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    preferences = {
        "realtime_quotes_enabled": False,
        "indices_nav_pinned": True,
        "minute_sync_enabled": False,
        "minute_intraday_refresh": False,
        "pipeline_pull_a_share": True,
        "pipeline_pull_etf": False,
        "pipeline_pull_index": True,
        "limit_ladder_monitor_enabled": False,
        "review_push_enabled": False,
        "realtime_pull_stock": True,
        "realtime_pull_etf": False,
        "realtime_pull_index": True,
        "strategy_monitor_enabled": False,
        "system_notify_enabled": False,
        "wecom_bot_enabled": False,
        "webhook_enabled_default": False,
        "screener_auto_run": True,
        "onboarding_completed": True,
        "minute_intraday_refresh_interval": 6,
        "minute_sync_days": 5,
        "minute_sync_segment_days": 20,
        "enriched_batch_size": 1000,
        "index_daily_batch_size": 100,
        "realtime_quote_interval": 6.0,
        "depth_polling_interval": 10.0,
        "last_fetch_ms": 0.0,
        "daily_data_provider": "tickflow",
        "adj_factor_provider": "same_as_daily",
        "minute_data_provider": "tickflow",
        "realtime_data_provider": "tickflow",
        "financial_data_provider": "tickflow",
        "pipeline_index_symbols": "",
        "review_push_channel": "feishu",
        "realtime_index_mode": "core",
        "realtime_watchlist_symbols": ["000403.SZ"],
        "sidebar_index_symbols": ["000001.SH"],
        "strategy_monitor_ids": [],
        "nav_order": ["watchlist"],
        "nav_hidden": [],
        "review_push_channels": [],
        "webhook_default_channels": [],
        "pipeline_schedule": {"hour": 15, "minute": 30},
        "instruments_schedule": {"hour": 9, "minute": 10},
        "depth_finalize_time": {"hour": 15, "minute": 2},
        "review_schedule": {"enabled": False, "hour": 15, "minute": 10},
        "realtime_index_symbols": ["000001.SH"],
        "sse_refresh_pages": {"watchlist": True},
        "monitor_ext_fields": {
            "concept": {"field": "ext_gn_ths.concept", "maxTags": 3},
            "industry": None,
        },
        "financial_sync_times": {"metrics": "2026-07-22T15:00:00+08:00"},
        "watchlist_columns": [],
        "screener_result_columns": [],
        "feishu_webhook_url": "configured",
        "feishu_webhook_secret": "configured",
        "wecom_webhook_url": "configured",
        "wecom_bot_id": "configured",
        "wecom_bot_secret": "configured",
    }
    secrets = {
        "tickflow_api_key": "local-only",
        "tickflow_base_url": "https://api.tickflow.org",
        "tushare_token": "local-only",
        "ai_provider": "openai_compat",
        "ai_base_url": "https://example.invalid/v1",
        "ai_api_key": "local-only",
        "ai_model": "model",
        "ai_codex_command": "codex",
        "ai_codex_reasoning_effort": "medium",
        "ai_user_agent": "TickFlow test",
    }
    assert set(preferences) == _KNOWN_PREFERENCE_FIELDS
    assert set(secrets) == _SECRET_TEXT_FIELDS
    files = {
        "preferences.json": preferences,
        "secrets.json": secrets,
        "auth.json": {
            "password_salt": "a" * 32,
            "password_hash": "b" * 64,
            "updated_at": 1_753_171_200,
            "sessions": {},
        },
        "ai_stock_reports.json": [
            {
                "id": "sar-valid",
                "symbol": "000403.SZ",
                "content": "---\ntrading_advice: false\n---\n# report",
                "created_at": "2026-07-22T15:00:00",
                "data_scope": "watchlist-sample",
                "can_publish": False,
                "trading_advice": False,
                "needs_verification": ["latest_close"],
                "close": 18.25,
                "levels": {"support": [17.8]},
            }
        ],
        "ai_market_recaps.json": [
            {
                "id": "mkr-valid",
                "as_of": "2026-07-22",
                "content": "# recap",
                "created_at": "2026-07-22T15:00:00",
                "emotion_score": 50,
                "can_publish": False,
                "trading_advice": False,
            }
        ],
        "backtest_summaries.json": [],
    }
    for filename, payload in files.items():
        (user_data / filename).write_text(json.dumps(payload), encoding="utf-8")

    assert validate_storage_files(tmp_path) == tuple(files)
