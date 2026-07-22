"""Fail-closed validation for persisted workspace and credential file shapes."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from app.services.backtest_summaries import _validate_summary
from app.services.preferences import CLIENT_PREFERENCE_KEYS, _valid_client_value

_OBJECT_FILES = ("preferences.json", "secrets.json", "auth.json")
_REPORT_FILES = ("ai_stock_reports.json", "ai_market_recaps.json")
_SUMMARY_FILE = "backtest_summaries.json"
_HEX = re.compile(r"[0-9a-f]+")
_SECRET_TEXT_FIELDS = frozenset({
    "tickflow_api_key",
    "tickflow_base_url",
    "tushare_token",
    "ai_provider",
    "ai_base_url",
    "ai_api_key",
    "ai_model",
    "ai_codex_command",
    "ai_codex_reasoning_effort",
    "ai_user_agent",
})
_CREDENTIAL_TEXT_FIELDS = (
    "feishu_webhook_url",
    "feishu_webhook_secret",
    "wecom_webhook_url",
    "wecom_bot_id",
    "wecom_bot_secret",
)
_PREFERENCE_BOOL_FIELDS = frozenset({
    "realtime_quotes_enabled",
    "indices_nav_pinned",
    "minute_sync_enabled",
    "minute_intraday_refresh",
    "pipeline_pull_a_share",
    "pipeline_pull_etf",
    "pipeline_pull_index",
    "limit_ladder_monitor_enabled",
    "review_push_enabled",
    "realtime_pull_stock",
    "realtime_pull_etf",
    "realtime_pull_index",
    "strategy_monitor_enabled",
    "system_notify_enabled",
    "wecom_bot_enabled",
    "webhook_enabled_default",
    "screener_auto_run",
    "onboarding_completed",
})
_PREFERENCE_INT_RANGES = {
    "minute_intraday_refresh_interval": (3, 60),
    "minute_sync_days": (1, 30),
    "minute_sync_segment_days": (5, 30),
    "enriched_batch_size": (1, 10_000),
    "index_daily_batch_size": (1, 10_000),
}
_PREFERENCE_NUMBER_RANGES = {
    "realtime_quote_interval": (0.0, None),
    "depth_polling_interval": (0.0, None),
    "last_fetch_ms": (0.0, None),
}
_PREFERENCE_PROVIDER_FIELDS = frozenset({
    "daily_data_provider",
    "adj_factor_provider",
    "minute_data_provider",
    "realtime_data_provider",
    "financial_data_provider",
})
_PREFERENCE_TEXT_FIELDS = frozenset({
    "pipeline_index_symbols",
    "review_push_channel",
    "realtime_index_mode",
})
_PREFERENCE_STRING_LIST_FIELDS = frozenset({
    "realtime_watchlist_symbols",
    "sidebar_index_symbols",
    "strategy_monitor_ids",
    "nav_order",
    "nav_hidden",
})
_PREFERENCE_CHANNEL_LIST_FIELDS = frozenset({
    "review_push_channels",
    "webhook_default_channels",
})
_PREFERENCE_SCHEDULE_FIELDS = frozenset({
    "pipeline_schedule",
    "instruments_schedule",
    "depth_finalize_time",
})
_PREFERENCE_SPECIAL_FIELDS = frozenset({
    "review_schedule",
    "realtime_index_symbols",
    "sse_refresh_pages",
    "monitor_ext_fields",
    "financial_sync_times",
    "watchlist_columns",
    "screener_result_columns",
})
_KNOWN_PREFERENCE_FIELDS = (
    _PREFERENCE_BOOL_FIELDS
    | frozenset(_PREFERENCE_INT_RANGES)
    | frozenset(_PREFERENCE_NUMBER_RANGES)
    | _PREFERENCE_PROVIDER_FIELDS
    | _PREFERENCE_TEXT_FIELDS
    | _PREFERENCE_STRING_LIST_FIELDS
    | _PREFERENCE_CHANNEL_LIST_FIELDS
    | _PREFERENCE_SCHEDULE_FIELDS
    | _PREFERENCE_SPECIAL_FIELDS
    | frozenset(_CREDENTIAL_TEXT_FIELDS)
)
_STOCK_REPORT_FIELDS = frozenset({
    "id",
    "symbol",
    "name",
    "focus",
    "title",
    "content",
    "summary",
    "close",
    "levels",
    "created_at",
    "data_as_of",
    "verification_status",
    "can_publish",
    "trading_advice",
    "type",
    "source_system",
    "data_scope",
    "needs_verification",
    "timeframe",
})
_MARKET_RECAP_FIELDS = frozenset({
    "id",
    "as_of",
    "focus",
    "title",
    "content",
    "summary",
    "emotion_score",
    "emotion_label",
    "created_at",
    "data_as_of",
    "verification_status",
    "can_publish",
    "trading_advice",
})


class WorkspaceStorageError(RuntimeError):
    """Raised when a persisted file would otherwise be silently discarded."""


def _invalid(filename: str, reason: str) -> WorkspaceStorageError:
    return WorkspaceStorageError(f"{filename}: {reason}")


def _parse_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _invalid(path.name, "invalid JSON") from exc


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _validate_string_list(filename: str, key: str, value: object) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _invalid(filename, f"{key} must be an array of strings")


def _validate_schedule(filename: str, key: str, value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"hour", "minute"}:
        raise _invalid(filename, f"{key} must contain hour and minute")
    hour = value["hour"]
    minute = value["minute"]
    if (
        not _is_integer(hour)
        or not _is_integer(minute)
        or not 0 <= hour <= 23
        or not 0 <= minute <= 59
    ):
        raise _invalid(filename, f"{key} time is invalid")


def _validate_monitor_ext_field(filename: str, value: object) -> None:
    if value is None or isinstance(value, str):
        return
    if not isinstance(value, dict):
        raise _invalid(filename, "monitor ext field is invalid")
    allowed = {"field", "maxTags", "hiddenIndices"}
    if not set(value) <= allowed or not isinstance(value.get("field"), str):
        raise _invalid(filename, "monitor ext field schema is invalid")
    if "maxTags" in value and (
        not _is_integer(value["maxTags"]) or value["maxTags"] < 0
    ):
        raise _invalid(filename, "monitor ext maxTags is invalid")
    if "hiddenIndices" in value:
        hidden = value["hiddenIndices"]
        if (
            not isinstance(hidden, list)
            or not all(_is_integer(item) and item >= 0 for item in hidden)
        ):
            raise _invalid(filename, "monitor ext hiddenIndices is invalid")


def _validate_monitor_ext_fields(filename: str, value: object) -> None:
    if not isinstance(value, dict) or not set(value) <= {"concept", "industry"}:
        raise _invalid(filename, "monitor_ext_fields schema is invalid")
    for item in value.values():
        _validate_monitor_ext_field(filename, item)


def _validate_secrets(filename: str, payload: dict) -> None:
    unknown = set(payload) - _SECRET_TEXT_FIELDS
    if unknown:
        raise _invalid(filename, "contains unsupported fields")
    for key, value in payload.items():
        if not isinstance(value, str):
            raise _invalid(filename, f"{key} must be a string")


def _validate_auth(filename: str, payload: dict) -> None:
    if set(payload) - {"password_salt", "password_hash", "updated_at", "sessions"}:
        raise _invalid(filename, "contains unsupported fields")
    if "updated_at" in payload and (
        not _is_integer(payload["updated_at"]) or payload["updated_at"] < 0
    ):
        raise _invalid(filename, "updated_at must be a non-negative integer")
    sessions = payload.get("sessions", {})
    if not isinstance(sessions, dict):
        raise _invalid(filename, "sessions must be an object")
    for token, expires_at in sessions.items():
        if (
            not isinstance(token, str)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(expires_at)
        ):
            raise _invalid(filename, "session entry is invalid")

    salt = payload.get("password_salt")
    password_hash = payload.get("password_hash")
    if salt is None and password_hash is None:
        return
    if (
        not isinstance(salt, str)
        or not isinstance(password_hash, str)
        or len(salt) != 32
        or len(password_hash) != 64
        or _HEX.fullmatch(salt) is None
        or _HEX.fullmatch(password_hash) is None
    ):
        raise _invalid(filename, "password verifier is invalid")


def _validate_preferences(filename: str, payload: dict) -> None:
    unknown = set(payload) - _KNOWN_PREFERENCE_FIELDS
    if unknown:
        raise _invalid(filename, "contains unsupported fields")

    for key in CLIENT_PREFERENCE_KEYS:
        if key in payload and not _valid_client_value(key, payload[key]):
            raise _invalid(filename, f"{key} is invalid")

    for key in _PREFERENCE_BOOL_FIELDS:
        if key in payload and not isinstance(payload[key], bool):
            raise _invalid(filename, f"{key} must be a boolean")
    for key, (minimum, maximum) in _PREFERENCE_INT_RANGES.items():
        if key not in payload:
            continue
        value = payload[key]
        if (
            not _is_integer(value)
            or value < minimum
            or (maximum is not None and value > maximum)
        ):
            raise _invalid(filename, f"{key} is outside its integer range")
    for key, (minimum, maximum) in _PREFERENCE_NUMBER_RANGES.items():
        if key not in payload:
            continue
        value = payload[key]
        if (
            not _is_number(value)
            or value <= minimum
            or (maximum is not None and value > maximum)
        ):
            if key == "last_fetch_ms" and _is_number(value) and value == 0:
                continue
            raise _invalid(filename, f"{key} is outside its numeric range")
    for key in _PREFERENCE_PROVIDER_FIELDS:
        if key in payload and not _valid_client_value(key, payload[key]):
            raise _invalid(filename, f"{key} is invalid")
    for key in _PREFERENCE_TEXT_FIELDS:
        if key in payload and not isinstance(payload[key], str):
            raise _invalid(filename, f"{key} must be a string")
    if payload.get("realtime_index_mode", "core") not in {"core", "all"}:
        raise _invalid(filename, "realtime_index_mode is invalid")
    if "review_push_channel" in payload and payload["review_push_channel"] != "feishu":
        raise _invalid(filename, "review_push_channel is invalid")
    for key in _PREFERENCE_STRING_LIST_FIELDS:
        if key in payload:
            _validate_string_list(filename, key, payload[key])
    for key in _PREFERENCE_CHANNEL_LIST_FIELDS:
        if key not in payload:
            continue
        _validate_string_list(filename, key, payload[key])
        if not set(payload[key]) <= {"feishu", "wecom"}:
            raise _invalid(filename, f"{key} contains an unsupported channel")
    for key in _PREFERENCE_SCHEDULE_FIELDS:
        if key in payload:
            _validate_schedule(filename, key, payload[key])

    if "review_schedule" in payload:
        schedule = payload["review_schedule"]
        if not isinstance(schedule, dict) or set(schedule) != {"enabled", "hour", "minute"}:
            raise _invalid(filename, "review_schedule schema is invalid")
        if not isinstance(schedule["enabled"], bool):
            raise _invalid(filename, "review_schedule enabled must be a boolean")
        _validate_schedule(
            filename,
            "review_schedule",
            {"hour": schedule["hour"], "minute": schedule["minute"]},
        )
    if "realtime_index_symbols" in payload:
        symbols = payload["realtime_index_symbols"]
        if not isinstance(symbols, str):
            _validate_string_list(filename, "realtime_index_symbols", symbols)
    if "sse_refresh_pages" in payload:
        pages = payload["sse_refresh_pages"]
        if (
            not isinstance(pages, dict)
            or not all(isinstance(key, str) and isinstance(value, bool) for key, value in pages.items())
        ):
            raise _invalid(filename, "sse_refresh_pages must map strings to booleans")
    if "monitor_ext_fields" in payload:
        _validate_monitor_ext_fields(filename, payload["monitor_ext_fields"])
    if "financial_sync_times" in payload:
        times = payload["financial_sync_times"]
        if (
            not isinstance(times, dict)
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in times.items())
        ):
            raise _invalid(filename, "financial_sync_times must map strings to strings")
    for key in _CREDENTIAL_TEXT_FIELDS:
        if key in payload and not isinstance(payload[key], str):
            raise _invalid(filename, f"{key} must be a string")


def _validate_report_safety(filename: str, item: dict) -> None:
    for key in ("can_publish", "trading_advice"):
        if key in item and not isinstance(item[key], bool):
            raise _invalid(filename, f"report {key} must be a boolean")
    if item.get("can_publish") is True:
        raise _invalid(filename, "reports cannot be marked publishable")
    if item.get("trading_advice") is True:
        raise _invalid(filename, "reports cannot contain trading advice")
    if item.get("data_scope") == "watchlist-sample" and item.get("can_publish") is not False:
        raise _invalid(filename, "watchlist-sample reports must set can_publish=false")


def _validate_stock_report(filename: str, item: dict) -> None:
    if set(item) - _STOCK_REPORT_FIELDS:
        raise _invalid(filename, "stock report contains unsupported fields")
    for key in ("id", "symbol", "content", "created_at"):
        if not isinstance(item.get(key), str) or not item[key]:
            raise _invalid(filename, f"stock report {key} is required")
    for key in (
        "name",
        "focus",
        "title",
        "summary",
        "data_as_of",
        "verification_status",
        "type",
        "source_system",
        "data_scope",
        "timeframe",
    ):
        if key in item and item[key] is not None and not isinstance(item[key], str):
            raise _invalid(filename, f"stock report {key} must be a string")
    if "close" in item and item["close"] is not None and not _is_number(item["close"]):
        raise _invalid(filename, "stock report close must be a finite number")
    if "levels" in item and item["levels"] is not None and not isinstance(item["levels"], dict):
        raise _invalid(filename, "stock report levels must be an object")
    if "needs_verification" in item and item["needs_verification"] is not None:
        _validate_string_list(filename, "needs_verification", item["needs_verification"])
    _validate_report_safety(filename, item)


def _validate_market_recap(filename: str, item: dict) -> None:
    if set(item) - _MARKET_RECAP_FIELDS:
        raise _invalid(filename, "market recap contains unsupported fields")
    for key in ("id", "as_of", "content", "created_at"):
        if not isinstance(item.get(key), str) or not item[key]:
            raise _invalid(filename, f"market recap {key} is required")
    for key in (
        "focus",
        "title",
        "summary",
        "emotion_label",
        "data_as_of",
        "verification_status",
    ):
        if key in item and item[key] is not None and not isinstance(item[key], str):
            raise _invalid(filename, f"market recap {key} must be a string")
    if "emotion_score" in item and item["emotion_score"] is not None:
        score = item["emotion_score"]
        if not _is_integer(score) or not 0 <= score <= 100:
            raise _invalid(filename, "market recap emotion_score is invalid")
    _validate_report_safety(filename, item)


def validate_storage_files(data_dir: Path) -> tuple[str, ...]:
    """Validate files that tolerant runtime loaders can otherwise erase logically."""
    user_data = Path(data_dir) / "user_data"
    if not user_data.exists():
        return ()
    if not user_data.is_dir():
        raise WorkspaceStorageError("user_data: expected a directory")

    validated: list[str] = []
    for filename in _OBJECT_FILES:
        path = user_data / filename
        if not path.exists():
            continue
        payload = _parse_json(path)
        if not isinstance(payload, dict):
            raise _invalid(filename, "top level must be an object")
        if filename == "preferences.json":
            _validate_preferences(filename, payload)
        elif filename == "secrets.json":
            _validate_secrets(filename, payload)
        elif filename == "auth.json":
            _validate_auth(filename, payload)
        validated.append(filename)

    for filename in _REPORT_FILES:
        path = user_data / filename
        if not path.exists():
            continue
        payload = _parse_json(path)
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise _invalid(filename, "top level must be an array of objects")
        for item in payload:
            if filename == "ai_stock_reports.json":
                _validate_stock_report(filename, item)
            else:
                _validate_market_recap(filename, item)
        validated.append(filename)

    summary_path = user_data / _SUMMARY_FILE
    if summary_path.exists():
        payload = _parse_json(summary_path)
        if not isinstance(payload, list):
            raise _invalid(_SUMMARY_FILE, "top level must be an array")
        try:
            for item in payload:
                _validate_summary(item)
        except (TypeError, ValueError) as exc:
            raise _invalid(_SUMMARY_FILE, "summary entry is invalid") from exc
        validated.append(_SUMMARY_FILE)

    return tuple(validated)
