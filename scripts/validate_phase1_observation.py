#!/usr/bin/env python3
"""Validate and materialize TickFlow Phase 1.2 observation evidence."""

# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

SYMBOLS = ("000403.SZ", "600489.SH", "300059.SZ")
SYMBOL_NAMES = {
    "000403.SZ": "派林生物",
    "600489.SH": "中金黄金",
    "300059.SZ": "东方财富",
}
COMPACT_SYMBOLS = {
    "000403.SZ": "000403SZ",
    "600489.SH": "600489SH",
    "300059.SZ": "300059SZ",
}
OHLC_ABS_EPSILON = 1e-10
AMOUNT_ZERO_EPSILON = 1e-6
DAY1_DATE = "2026-07-27"
SOURCE_STATUS = "PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING"
CONTRACT_VERSION = "tickflow_phase1_2_observation_v1"
SYMBOL_SET_HASH = hashlib.sha256(",".join(SYMBOLS).encode("ascii")).hexdigest()
VENDOR_PENDING = (
    "intraday_batch_entitlement",
    "first_30m_bucket_includes_09_30",
    "volume_unit",
    "amount_unit",
)
DAY_STATUSES = {"DAY_PASSED", "DAY_BLOCKED", "NON_TRADING_DAY"}
PHASE_STATUSES = {
    "PHASE1_OBSERVATION_IN_PROGRESS",
    "PHASE1_OBSERVATION_PASSED",
    "PHASE1_OBSERVATION_BLOCKED",
}
SAFE_WINDOW_START = time(16, 10)
NORMAL_WINDOW_END = time(18, 0)
EXECUTION_TIMINGS = {"ON_TIME", "LATE_SAME_DAY"}
SUPPORTED_CALENDAR_YEAR = 2026
# SSE announcement dated 2025-12-22, notice 45 of 2025.
# https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml
SSE_2026_CLOSURES = frozenset(
    date.fromisoformat(value)
    for value in (
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
        "2026-02-14",
        "2026-02-15",
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",
        "2026-02-19",
        "2026-02-20",
        "2026-02-21",
        "2026-02-22",
        "2026-02-23",
        "2026-02-28",
        "2026-04-04",
        "2026-04-05",
        "2026-04-06",
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
        "2026-05-04",
        "2026-05-05",
        "2026-05-09",
        "2026-06-19",
        "2026-06-20",
        "2026-06-21",
        "2026-09-20",
        "2026-09-25",
        "2026-09-26",
        "2026-09-27",
        "2026-10-01",
        "2026-10-02",
        "2026-10-03",
        "2026-10-04",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
        "2026-10-10",
    )
)


class EvidenceError(RuntimeError):
    """The supplied observation evidence does not satisfy the contract."""


class EvidenceConflictError(EvidenceError):
    """A date already has a different immutable evidence manifest."""


def _fail(code: str, detail: str | None = None) -> None:
    message = code if detail is None else f"{code}: {detail}"
    raise EvidenceError(message)


def _require(condition: bool, code: str, detail: str | None = None) -> None:
    if not condition:
        _fail(code, detail)


def _read_json(path: Path) -> dict[str, Any]:
    _require(path.is_file() and not path.is_symlink(), "SOURCE_FILE_MISSING", str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("SOURCE_JSON_INVALID", f"{path}: {type(exc).__name__}")
    _require(isinstance(value, dict), "SOURCE_JSON_NOT_OBJECT", str(path))
    return value


def sha256_file(path: Path) -> str:
    _require(path.is_file() and not path.is_symlink(), "SOURCE_FILE_MISSING", str(path))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_evidence_paths(repo_root: Path) -> list[Path]:
    reports = repo_root.resolve() / "reports"
    fixed = [
        reports / "tickflow_phase1_numeric_contract_closeout.md",
        reports / "phase1_tickflow_request_audit.json",
    ]
    directories = [
        reports / "phase1_numeric_contracts",
        reports / "phase1_data_contracts",
    ]
    paths = list(fixed)
    for directory in directories:
        _require(
            directory.is_dir() and not directory.is_symlink(),
            "SOURCE_DIRECTORY_MISSING",
            str(directory),
        )
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                _fail("SOURCE_SYMLINK_FORBIDDEN", str(path))
            if path.is_file():
                paths.append(path)
    for path in paths:
        _require(path.is_file() and not path.is_symlink(), "SOURCE_FILE_MISSING", str(path))
    return sorted(set(paths), key=lambda item: item.relative_to(repo_root).as_posix())


def hash_evidence(
    paths: Sequence[Path],
    repo_root: Path,
) -> list[dict[str, Any]]:
    root = repo_root.resolve()
    manifest: list[dict[str, Any]] = []
    for path in sorted(
        (item.resolve() for item in paths),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            _fail("SOURCE_PATH_OUTSIDE_REPOSITORY", str(path))
        manifest.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return manifest


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_observation_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        _fail("OBSERVATION_DATE_INVALID", str(value))
    _require(parsed.isoformat() == value, "OBSERVATION_DATE_INVALID", str(value))
    return parsed


def _parse_shanghai_now(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        _fail("OBSERVATION_NOW_INVALID", str(value))
    _require(
        parsed.utcoffset() == timedelta(hours=8),
        "OBSERVATION_NOW_INVALID",
        "Asia/Shanghai +08:00 required",
    )
    return parsed


def _classify_execution_timing(
    observation_date: str,
    run_started_at: str,
) -> str:
    target_date = _parse_observation_date(observation_date)
    started = _parse_shanghai_now(run_started_at)
    _require(
        started.date() == target_date,
        "RUN_STARTED_OUTSIDE_OBSERVATION_DATE",
        run_started_at,
    )
    started_time = started.timetz().replace(tzinfo=None)
    if started_time < SAFE_WINDOW_START:
        _fail("BEFORE_SAFE_WINDOW", run_started_at)
    return (
        "ON_TIME"
        if started_time <= NORMAL_WINDOW_END
        else "LATE_SAME_DAY"
    )


def apply_live_execution_metadata(
    day: Mapping[str, Any],
    *,
    observation_date: str,
    run_started_at: str,
    run_completed_at: str,
) -> dict[str, Any]:
    """Attach immutable timing metadata without changing contract results."""

    _require(
        day.get("observation_date") == observation_date,
        "OBSERVATION_DATE_MISMATCH",
        observation_date,
    )
    execution_timing = _classify_execution_timing(
        observation_date,
        run_started_at,
    )
    started = _parse_shanghai_now(run_started_at)
    completed = _parse_shanghai_now(run_completed_at)
    _require(
        completed >= started,
        "RUN_COMPLETED_BEFORE_START",
        run_completed_at,
    )
    enriched = dict(day)
    enriched.update(
        {
            "evidence_origin": "phase1_2_daily_live",
            "execution_timing": execution_timing,
            "run_started_at": run_started_at,
            "run_completed_at": run_completed_at,
            "counts_as_valid_day": day.get("status") == "DAY_PASSED",
        }
    )
    return enriched


def _is_sse_trading_day(value: date) -> bool:
    _require(
        value.year == SUPPORTED_CALENDAR_YEAR,
        "CALENDAR_YEAR_UNSUPPORTED",
        str(value.year),
    )
    return value.weekday() < 5 and value not in SSE_2026_CLOSURES


def _next_sse_trading_day(value: date) -> date:
    candidate = value + timedelta(days=1)
    while not _is_sse_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def _idempotency_key(observation_date: str) -> str:
    return (
        f"phase1_observation:{observation_date}:"
        f"{SYMBOL_SET_HASH}:{CONTRACT_VERSION}"
    )


def _successful_request_audit_exists(day_directory: Path) -> bool:
    audit_path = day_directory / "request_audit.json"
    if not audit_path.exists():
        return False
    audit = _read_json(audit_path)
    records = audit.get("records")
    return (
        isinstance(records, list)
        and bool(records)
        and all(
            isinstance(record, Mapping) and record.get("status") == "PASSED"
            for record in records
        )
    )


def evaluate_live_run_gate(
    reports_root: Path,
    *,
    observation_date: str,
    now_iso: str,
) -> dict[str, Any]:
    """Evaluate every local date/idempotency gate before any live process."""

    reports = reports_root.resolve()
    target_date = _parse_observation_date(observation_date)
    now = _parse_shanghai_now(now_iso)
    if target_date > now.date():
        _fail("FUTURE_DATE_FORBIDDEN", observation_date)
    if target_date < now.date():
        _fail("HISTORICAL_LIVE_FORBIDDEN", observation_date)
    execution_timing = _classify_execution_timing(
        observation_date,
        now_iso,
    )

    is_trading_day = _is_sse_trading_day(target_date)
    observation_root = reports / "phase1_observation"
    day_directory = observation_root / observation_date
    if day_directory.is_symlink():
        _fail("OBSERVATION_PATH_INVALID", str(day_directory))
    if day_directory.exists():
        _require(day_directory.is_dir(), "OBSERVATION_PATH_INVALID")
        summary_path = day_directory / "daily_summary.json"
        if summary_path.exists():
            summary = _read_json(summary_path)
            if summary.get("status") == "DAY_PASSED":
                _fail("OBSERVATION_DATE_ALREADY_PASSED", observation_date)
            _fail("OBSERVATION_DATE_ALREADY_RECORDED", observation_date)
        if _successful_request_audit_exists(day_directory):
            _fail("OBSERVATION_REQUEST_ALREADY_RECORDED", observation_date)
        _fail("OBSERVATION_PARTIAL_OUTPUT_PRESENT", observation_date)

    index_path = observation_root / "observation_index.json"
    if index_path.exists():
        index = _read_json(index_path)
        indexed_days = index.get("days")
        _require(isinstance(indexed_days, list), "OBSERVATION_INDEX_INVALID")
        for indexed_day in indexed_days:
            _require(
                isinstance(indexed_day, Mapping),
                "OBSERVATION_INDEX_INVALID",
            )
            if indexed_day.get("observation_date") != observation_date:
                continue
            if indexed_day.get("status") == "DAY_PASSED":
                _fail("OBSERVATION_DATE_ALREADY_PASSED", observation_date)
            _fail("OBSERVATION_DATE_ALREADY_RECORDED", observation_date)

    if not is_trading_day:
        return {
            "decision": "NON_TRADING_DAY",
            "observation_date": observation_date,
            "calendar_source": "SSE_2026_OFFICIAL_CLOSURES",
            "new_api_request_count": 0,
            "idempotency_key": _idempotency_key(observation_date),
        }

    passed_dates: list[date] = []
    for directory in _day_directories(observation_root):
        summary = _read_json(directory / "daily_summary.json")
        if summary.get("status") == "DAY_PASSED":
            passed_dates.append(_parse_observation_date(directory.name))
    _require(passed_dates, "PHASE1_DAY1_REQUIRED")
    expected_date = _next_sse_trading_day(max(passed_dates))
    _require(
        target_date == expected_date,
        "OBSERVATION_DATE_NOT_NEXT_TRADING_DAY",
        f"expected={expected_date.isoformat()}",
    )
    return {
        "decision": "LIVE_ALLOWED",
        "date_gate": "PASSED",
        "observation_date": observation_date,
        "execution_timing": execution_timing,
        "run_started_at": now_iso,
        "calendar_source": "SSE_2026_OFFICIAL_CLOSURES",
        "symbol_set_hash": SYMBOL_SET_HASH,
        "contract_version": CONTRACT_VERSION,
        "idempotency_key": _idempotency_key(observation_date),
    }


def _nested(mapping: Mapping[str, Any], *keys: str, code: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            _fail(code, ".".join(keys))
        value = value[key]
    return value


def _int_value(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(code, repr(value))
    return value


def _float_value(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(code, repr(value))
    return float(value)


def _timestamp_at_close(value: Any, observation_date: str, code: str) -> str:
    _require(isinstance(value, str), code, "timestamp missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(code, str(value))
    _require(parsed.utcoffset() is not None, code, "timezone missing")
    _require(parsed.date().isoformat() == observation_date, code, str(value))
    _require(parsed.timetz().replace(tzinfo=None) >= time(15, 0), code, str(value))
    return value


def _validate_daily(
    reports: Path,
    symbol: str,
    observation_date: str,
) -> dict[str, Any]:
    compact = COMPACT_SYMBOLS[symbol]
    contract = _read_json(
        reports / "phase1_data_contracts" / f"{compact}_daily_contract.json"
    )
    return _validate_daily_contract(contract, symbol, observation_date)


def _validate_daily_contract(
    contract: Mapping[str, Any],
    symbol: str,
    observation_date: str,
) -> dict[str, Any]:
    checks = _nested(contract, "checks", code="DAILY_CONTRACT_MISSING")
    _require(contract.get("passed") is True, "DAILY_CONTRACT_FAILED", symbol)
    _require(
        checks.get("symbol_matches_request") is True,
        "DAILY_SYMBOL_MISMATCH",
        symbol,
    )
    for field in ("expected_recent_trade_date", "latest_raw_trade_date", "latest_qfq_trade_date"):
        _require(checks.get(field) == observation_date, "DAILY_STALE", f"{symbol}:{field}")
    for field in (
        "latest_date_not_behind_recent_trade_date",
        "qfq_repeat_stable",
        "trade_dates_align_between_raw_and_qfq",
    ):
        _require(checks.get(field) is True, "DAILY_CONTRACT_FAILED", f"{symbol}:{field}")
    raw_latest = _nested(contract, "raw", "latest_timestamp", code="DAILY_CONTRACT_MISSING")
    qfq_latest = _nested(contract, "qfq", "latest_timestamp", code="DAILY_CONTRACT_MISSING")
    _require(
        isinstance(raw_latest, str) and raw_latest.startswith(observation_date),
        "DAILY_STALE",
        f"{symbol}:raw",
    )
    _require(
        isinstance(qfq_latest, str) and qfq_latest.startswith(observation_date),
        "DAILY_STALE",
        f"{symbol}:qfq",
    )
    return {
        "status": "PASSED",
        "latest_raw_trade_date": checks["latest_raw_trade_date"],
        "latest_qfq_trade_date": checks["latest_qfq_trade_date"],
        "qfq_repeat_stable": True,
    }


def _validate_factors(reports: Path, symbol: str) -> dict[str, Any]:
    compact = COMPACT_SYMBOLS[symbol]
    contract = _read_json(
        reports / "phase1_data_contracts" / f"{compact}_ex_factors_contract.json"
    )
    return _validate_factor_contract(contract, symbol)


def _validate_factor_contract(
    contract: Mapping[str, Any],
    symbol: str,
) -> dict[str, Any]:
    checks = _nested(contract, "checks", code="EX_FACTOR_CONTRACT_MISSING")
    _require(contract.get("passed") is True, "EX_FACTOR_CONTRACT_FAILED", symbol)
    _require(
        _int_value(contract.get("factor_count"), "EX_FACTOR_CONTRACT_FAILED") > 0,
        "EX_FACTOR_CONTRACT_FAILED",
        f"{symbol}:empty",
    )
    for field in (
        "factor_timestamps_valid",
        "factor_dates_unique",
        "factor_dates_sorted",
        "factor_values_positive",
        "repeat_result_stable",
        "qfq_repeat_result_stable",
        "qfq_relation_passed",
    ):
        _require(
            checks.get(field) is True,
            "EX_FACTOR_CONTRACT_FAILED",
            f"{symbol}:{field}",
        )
    match_rate = _float_value(
        _nested(
            checks,
            "qfq_relation",
            "all_price_fields_match_rate",
            code="EX_FACTOR_CONTRACT_MISSING",
        ),
        "EX_FACTOR_CONTRACT_FAILED",
    )
    _require(match_rate == 1.0, "EX_FACTOR_CONTRACT_FAILED", f"{symbol}:qfq")
    _require(
        bool(checks.get("no_second_adjustment_evidence")),
        "EX_FACTOR_CONTRACT_FAILED",
        f"{symbol}:second_adjustment",
    )
    return {
        "status": "PASSED",
        "factor_count": contract["factor_count"],
        "repeat_stable": True,
        "qfq_relation_match_rate": match_rate,
        "second_adjustment_detected": False,
    }


def _validate_bar_contract(
    value: Mapping[str, Any],
    *,
    symbol: str,
    period: str,
    observation_date: str,
) -> dict[str, Any]:
    prefix = "ONE_MINUTE" if period == "1m" else "THIRTY_MINUTE"
    _require(value.get("passed") is True, f"{prefix}_CONTRACT_FAILED", symbol)
    freshness = _nested(value, "freshness", code=f"{prefix}_CONTRACT_MISSING")
    _require(
        freshness.get("passed") is True
        and freshness.get("same_trade_date") is True
        and freshness.get("time_fresh") is True
        and freshness.get("expected_trade_date") == observation_date,
        f"{prefix}_STALE",
        symbol,
    )
    latest_timestamp = _timestamp_at_close(
        freshness.get("latest_timestamp"),
        observation_date,
        f"{prefix}_STALE",
    )
    validation = _nested(value, "validation", code=f"{prefix}_CONTRACT_MISSING")
    _require(validation.get("passed") is True, f"{prefix}_CONTRACT_FAILED", symbol)
    checks = _nested(validation, "checks", code=f"{prefix}_CONTRACT_MISSING")
    _require(
        _float_value(checks.get("ohlc_abs_epsilon"), "OHLC_EPSILON_MISMATCH")
        == OHLC_ABS_EPSILON,
        "OHLC_EPSILON_MISMATCH",
        symbol,
    )
    _require(
        _float_value(checks.get("amount_zero_epsilon"), "AMOUNT_EPSILON_MISMATCH")
        == AMOUNT_ZERO_EPSILON,
        "AMOUNT_EPSILON_MISMATCH",
        symbol,
    )
    _require(checks.get("timestamps_unique") is True, "DUPLICATE_TIMESTAMP", f"{symbol}:{period}")
    _require(
        checks.get("timestamps_strictly_increasing") is True,
        "TIMESTAMP_NOT_INCREASING",
        f"{symbol}:{period}",
    )
    _require(
        _int_value(checks.get("lunch_break_rows"), "LUNCH_BREAK_BAR") == 0,
        "LUNCH_BREAK_BAR",
        f"{symbol}:{period}",
    )
    for field, code in (
        ("future_timestamp_rows", "FUTURE_TIMESTAMP"),
        ("off_continuous_session_rows", "OFF_SESSION_BAR"),
        ("invalid_numeric_rows", "INVALID_NUMERIC_BAR"),
        ("invalid_ohlc_rows", "MATERIAL_OHLC_ANOMALY"),
        ("negative_volume_rows", "NEGATIVE_VOLUME"),
        ("negative_amount_rows", "MATERIAL_NEGATIVE_AMOUNT"),
    ):
        _require(
            _int_value(checks.get(field), code) == 0,
            code,
            f"{symbol}:{period}",
        )
    distribution = _nested(
        validation,
        "ohlc_violation_distribution",
        code=f"{prefix}_CONTRACT_MISSING",
    )
    threshold_count = _int_value(
        _nested(
            distribution,
            "threshold_counts",
            "gt_1e_10",
            code=f"{prefix}_CONTRACT_MISSING",
        ),
        "MATERIAL_OHLC_ANOMALY",
    )
    _require(
        distribution.get("contract_passed") is True and threshold_count == 0,
        "MATERIAL_OHLC_ANOMALY",
        f"{symbol}:{period}",
    )
    amount = _nested(
        validation,
        "amount_tolerance",
        code=f"{prefix}_CONTRACT_MISSING",
    )
    failed_amount = _int_value(
        amount.get("failed_count"),
        "MATERIAL_NEGATIVE_AMOUNT",
    )
    minimum_amount = amount.get("minimum_amount")
    if minimum_amount is not None:
        _require(
            _float_value(minimum_amount, "MATERIAL_NEGATIVE_AMOUNT")
            >= -AMOUNT_ZERO_EPSILON,
            "MATERIAL_NEGATIVE_AMOUNT",
            f"{symbol}:{period}",
        )
    _require(
        amount.get("contract_passed") is True and failed_amount == 0,
        "MATERIAL_NEGATIVE_AMOUNT",
        f"{symbol}:{period}",
    )
    bar_count = _int_value(validation.get("bar_count"), f"{prefix}_CONTRACT_FAILED")
    if period == "30m":
        _require(bar_count == 8, "THIRTY_MINUTE_BUCKET_COUNT", symbol)
    return {
        "status": "PASSED",
        "period": period,
        "latest_timestamp": latest_timestamp,
        "bar_count": bar_count,
        "material_ohlc_anomalies": threshold_count,
        "material_negative_amount": failed_amount,
        "duplicate_timestamps": 0,
        "lunch_break_bars": 0,
        "epsilon_ohlc_warning_count": _int_value(
            checks.get("ohlc_epsilon_warning_rows", 0),
            "OHLC_WARNING_COUNT_INVALID",
        ),
        "epsilon_amount_warning_count": _int_value(
            checks.get("amount_epsilon_warning_rows", 0),
            "AMOUNT_WARNING_COUNT_INVALID",
        ),
    }


def _effective_field_mismatches(
    selected: Mapping[str, Any],
    candidate: Mapping[str, Any],
    field: str,
    symbol: str,
) -> int:
    detail = _nested(selected, "fields", field, code="MINUTE_30M_COMPARISON_MISSING")
    compared = _int_value(detail.get("compared"), "MINUTE_30M_COMPARISON_INVALID")
    matched = _int_value(detail.get("matched"), "MINUTE_30M_COMPARISON_INVALID")
    _require(compared == 8 and 0 <= matched <= compared, "MINUTE_30M_COMPARISON_INVALID", f"{symbol}:{field}")
    mismatches = compared - matched
    candidate_match = candidate.get(f"{field}_match")
    if mismatches:
        _require(mismatches == 1 and candidate_match is True, "FIRST_BUCKET_UNEXPLAINED", f"{symbol}:{field}")
        return 0
    return 0


def _validate_minute_symbol(
    minute: Mapping[str, Any],
    symbol: str,
    observation_date: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    symbols = _nested(minute, "symbols", code="MINUTE_CONTRACT_MISSING")
    _require(symbol in symbols and isinstance(symbols[symbol], Mapping), "MINUTE_SYMBOL_MISSING", symbol)
    value = symbols[symbol]
    one = _validate_bar_contract(
        _nested(value, "one_minute", code="ONE_MINUTE_CONTRACT_MISSING"),
        symbol=symbol,
        period="1m",
        observation_date=observation_date,
    )
    thirty = _validate_bar_contract(
        _nested(value, "direct_30m", code="THIRTY_MINUTE_CONTRACT_MISSING"),
        symbol=symbol,
        period="30m",
        observation_date=observation_date,
    )
    local = _nested(value, "local_1m_to_30m", code="MINUTE_30M_COMPARISON_MISSING")
    _require(
        local.get("passed") is True and local.get("ohlc_contract") == "PASSED",
        "THIRTY_MINUTE_OHLC_MISMATCH",
        symbol,
    )
    selected = _nested(
        local,
        "selected_comparison",
        code="MINUTE_30M_COMPARISON_MISSING",
    )
    for field in ("pair_count", "direct_bar_count", "aggregated_bar_count", "complete_bucket_count"):
        _require(
            _int_value(selected.get(field), "THIRTY_MINUTE_BUCKET_COUNT") == 8,
            "THIRTY_MINUTE_BUCKET_COUNT",
            f"{symbol}:{field}",
        )
    dual = _nested(local, "dual_first_bucket", code="FIRST_BUCKET_EVIDENCE_MISSING")
    _require(dual.get("trade_date") == observation_date, "FIRST_BUCKET_UNEXPLAINED", symbol)
    _require(
        dual.get("verdict") == "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED"
        and dual.get("vendor_contract_confirmed") is False
        and dual.get("raw_values_modified") is False
        and dual.get("timestamps_shifted") is False,
        "FIRST_BUCKET_UNEXPLAINED",
        symbol,
    )
    candidate = _nested(
        dual,
        "symbols",
        symbol,
        "vendor_candidate_09_30_to_10_00",
        code="FIRST_BUCKET_EVIDENCE_MISSING",
    )
    _require(
        candidate.get("all_fields_match") is True
        and _int_value(candidate.get("minute_count"), "FIRST_BUCKET_UNEXPLAINED")
        == 31,
        "FIRST_BUCKET_UNEXPLAINED",
        symbol,
    )
    ohlc_mismatches = sum(
        _effective_field_mismatches(selected, candidate, field, symbol)
        for field in ("open", "high", "low", "close")
    )
    volume_amount_mismatches = sum(
        _effective_field_mismatches(selected, candidate, field, symbol)
        for field in ("volume", "amount")
    )
    _require(ohlc_mismatches == 0, "THIRTY_MINUTE_OHLC_MISMATCH", symbol)
    _require(
        volume_amount_mismatches == 0,
        "NON_FIRST_BUCKET_VOLUME_AMOUNT_MISMATCH",
        symbol,
    )
    contract = {
        "symbol": symbol,
        "name": SYMBOL_NAMES[symbol],
        "status": "PASSED",
        "one_minute": one,
        "direct_30m": thirty,
        "minute_to_30m": {
            "status": "PASSED",
            "ohlc_mismatches": ohlc_mismatches,
            "non_first_bucket_volume_amount_mismatches": volume_amount_mismatches,
            "first_bucket_rule": "09:30-10:00",
            "first_bucket_mechanically_explained": True,
            "vendor_contract_confirmed": False,
        },
    }
    metrics = {
        "material_ohlc_anomalies": (
            one["material_ohlc_anomalies"] + thirty["material_ohlc_anomalies"]
        ),
        "material_negative_amount": (
            one["material_negative_amount"] + thirty["material_negative_amount"]
        ),
        "duplicate_timestamps": (
            one["duplicate_timestamps"] + thirty["duplicate_timestamps"]
        ),
        "lunch_break_bars": one["lunch_break_bars"] + thirty["lunch_break_bars"],
        "thirty_minute_ohlc_mismatches": ohlc_mismatches,
        "non_first_bucket_volume_amount_mismatches": volume_amount_mismatches,
        "first_bucket_matches": 1,
        "first_bucket_checks": 1,
    }
    return contract, metrics


def _validate_audit(reports: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    audit = _read_json(reports / "phase1_tickflow_request_audit.json")
    _require(audit.get("final_status") == SOURCE_STATUS, "SOURCE_STATUS_MISMATCH")
    _require(
        audit.get("http_429_detected") is False,
        "HTTP_429_DETECTED",
    )
    _require(
        _int_value(audit.get("retry_count"), "REQUEST_AUDIT_INVALID") == 0,
        "RETRY_DETECTED",
    )
    _require(
        _int_value(audit.get("phase1_1_request_count"), "REQUEST_AUDIT_INVALID")
        == 14,
        "REQUEST_COUNT_MISMATCH",
    )
    for field, code in (
        ("cloud_redeployed", "CLOUD_REDEPLOY_DETECTED"),
        ("integrated_gold_enabled", "INTEGRATED_GOLD_ENABLED"),
        ("real_key_exposed", "REAL_KEY_EXPOSED"),
        ("credential_values_recorded", "CREDENTIAL_VALUE_RECORDED"),
    ):
        _require(audit.get(field) is False, code)
    _require(
        _int_value(
            audit.get("integrated_gold_external_send_count"),
            "GOLD_SEND_COUNT_INVALID",
        )
        == 0,
        "GOLD_EXTERNAL_SEND_DETECTED",
    )
    gates = _nested(audit, "gates", code="SECURITY_GATE_MISSING")
    _require(
        gates.get("passed") is True
        and gates.get("recent_log_credential_shape_scan_passed") is True
        and gates.get("credential_values_recorded") is False,
        "SENSITIVE_SHAPE_DETECTED",
    )
    phase = _nested(audit, "phase1_1", code="PHASE1_1_AUDIT_MISSING")
    _require(
        phase.get("pipeline") == "phase1_1_minute_contract_closeout"
        and phase.get("http_429_detected") is False
        and phase.get("credential_values_recorded") is False,
        "PHASE1_1_AUDIT_INVALID",
    )
    records = phase.get("records")
    _require(isinstance(records, list), "PHASE1_1_AUDIT_INVALID", "records")
    _require(
        _int_value(phase.get("request_count"), "REQUEST_AUDIT_INVALID") == 14
        and len(records) == 14,
        "REQUEST_COUNT_MISMATCH",
    )
    for record in records:
        _require(isinstance(record, Mapping), "REQUEST_AUDIT_INVALID", "record")
        capability = record.get("capability")
        _require(isinstance(capability, str), "REQUEST_AUDIT_INVALID", "capability")
        _require("intraday_batch" not in capability, "INTRADAY_BATCH_DETECTED")
        _require(record.get("status") == "PASSED", "REQUEST_FAILED", capability)
        _require(
            _int_value(record.get("retry_count"), "REQUEST_AUDIT_INVALID") == 0,
            "RETRY_DETECTED",
            capability,
        )
        _require(
            isinstance(record.get("pipeline"), str)
            and record.get("pipeline") == phase["pipeline"],
            "REQUEST_AUDIT_INVALID",
            f"{capability}:pipeline",
        )
        _require(
            _int_value(record.get("symbol_count"), "REQUEST_AUDIT_INVALID") >= 1,
            "REQUEST_AUDIT_INVALID",
            f"{capability}:symbol_count",
        )
        _require(
            _int_value(record.get("duration_ms"), "REQUEST_AUDIT_INVALID") >= 0,
            "REQUEST_AUDIT_INVALID",
            f"{capability}:duration_ms",
        )
    compact = {
        "pipeline": phase["pipeline"],
        "source_request_count": 14,
        "new_api_request_count": 0,
        "live_request_reexecuted": False,
        "retry_count": 0,
        "http_429": 0,
        "intraday_batch_called": False,
        "credential_values_recorded": False,
        "records": [
            {
                "pipeline": record["pipeline"],
                "capability": record["capability"],
                "symbol_count": record["symbol_count"],
                "duration_ms": record["duration_ms"],
                "status": record["status"],
                "retry_count": 0,
                "rate_limit_wait_ms": record.get("rate_limit_wait_ms", 0),
            }
            for record in records
        ],
    }
    boundaries = {
        "ai": "NOT_CONFIGURED",
        "paper_trading": "NOT_STARTED",
        "cloud_redeployed": False,
        "integrated_gold_enabled": False,
        "integrated_gold_external_send_count": 0,
        "real_key": "NOT_EXPOSED",
    }
    return compact, boundaries


def _validate_verification(reports: Path) -> None:
    summary = _read_json(
        reports / "phase1_numeric_contracts" / "verification_summary.json"
    )
    _require(summary.get("final_status") == SOURCE_STATUS, "SOURCE_STATUS_MISMATCH")
    scan = _nested(
        summary,
        "verification",
        "report_sensitive_shape_scan",
        code="SENSITIVE_SCAN_MISSING",
    )
    _require(
        scan.get("status") == "PASSED"
        and _int_value(scan.get("credential_shape_hits"), "SENSITIVE_SHAPE_DETECTED")
        == 0,
        "SENSITIVE_SHAPE_DETECTED",
    )
    cloud = _nested(summary, "cloud_read_only_recheck", code="CLOUD_EVIDENCE_MISSING")
    _require(
        cloud.get("redeployed") is False
        and cloud.get("integrated_gold_enabled") is False
        and _int_value(
            cloud.get("integrated_gold_external_send_count"),
            "GOLD_SEND_COUNT_INVALID",
        )
        == 0,
        "CLOUD_BOUNDARY_FAILED",
    )
    boundaries = _nested(summary, "boundaries", code="BOUNDARY_EVIDENCE_MISSING")
    for field in (
        "raw_market_values_modified",
        "timestamps_shifted",
        "missing_minutes_filled",
        "ai_configured",
        "paper_trading_started",
        "real_key_exposed",
    ):
        _require(boundaries.get(field) is False, "BOUNDARY_EVIDENCE_FAILED", field)


def _validate_numeric_cross_checks(reports: Path, observation_date: str) -> None:
    numeric = reports / "phase1_numeric_contracts"
    dual = _read_json(numeric / "minute_30m_dual_convention_validation.json")
    _require(
        dual.get("trade_date") == observation_date
        and dual.get("verdict") == "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED"
        and _int_value(
            dual.get("candidate_match_symbol_count"),
            "FIRST_BUCKET_UNEXPLAINED",
        )
        == 3
        and _int_value(dual.get("symbol_count"), "FIRST_BUCKET_UNEXPLAINED")
        == 3
        and dual.get("vendor_contract_confirmed") is False,
        "FIRST_BUCKET_UNEXPLAINED",
    )
    ohlc = _read_json(numeric / "000403SZ_ohlc_violation_distribution.json")
    for period in ("one_minute", "direct_30m"):
        period_ohlc = _nested(
            ohlc,
            period,
            code="NUMERIC_CROSS_CHECK_MISSING",
        )
        _require(
            period_ohlc.get("contract_passed") is True
            and _int_value(
                _nested(
                    period_ohlc,
                    "threshold_counts",
                    "gt_1e_10",
                    code="NUMERIC_CROSS_CHECK_MISSING",
                ),
                "MATERIAL_OHLC_ANOMALY",
            )
            == 0,
            "MATERIAL_OHLC_ANOMALY",
            period,
        )
    amount = _read_json(numeric / "300059SZ_amount_tolerance.json")
    one_minute_amount = _nested(
        amount,
        "one_minute",
        code="NUMERIC_CROSS_CHECK_MISSING",
    )
    _require(
        one_minute_amount.get("contract_passed") is True
        and _int_value(
            one_minute_amount.get("failed_count"),
            "MATERIAL_NEGATIVE_AMOUNT",
        )
        == 0,
        "MATERIAL_NEGATIVE_AMOUNT",
    )
    factors = _read_json(numeric / "000403SZ_ex_factor_final.json")
    factor_checks = _nested(
        factors,
        "checks",
        code="NUMERIC_CROSS_CHECK_MISSING",
    )
    _require(
        factors.get("EX_FACTOR_CONTRACT") == "PASSED"
        and factor_checks.get("qfq_relation_passed") is True
        and _float_value(
            _nested(
                factor_checks,
                "qfq_relation",
                "all_price_fields_match_rate",
                code="NUMERIC_CROSS_CHECK_MISSING",
            ),
            "EX_FACTOR_CONTRACT_FAILED",
        )
        == 1.0,
        "EX_FACTOR_CONTRACT_FAILED",
    )


def build_reused_day(
    repo_root: Path,
    observation_date: str,
) -> dict[str, Any]:
    root = repo_root.resolve()
    _require(observation_date == DAY1_DATE, "REUSE_DATE_NOT_ALLOWED", observation_date)
    reports = root / "reports"
    _require(
        (reports / "tickflow_phase1_numeric_contract_closeout.md").is_file(),
        "SOURCE_REPORT_MISSING",
    )
    request_audit, boundaries = _validate_audit(reports)
    _validate_verification(reports)
    _validate_numeric_cross_checks(reports, observation_date)

    minute = _read_json(
        reports / "phase1_data_contracts" / "minute_to_30m_validation.json"
    )
    _require(
        minute.get("passed") is True
        and minute.get("timezone") == "Asia/Shanghai"
        and minute.get("intraday_batch_dependency") is False,
        "MINUTE_CONTRACT_FAILED",
    )
    symbol_contracts: dict[str, dict[str, Any]] = {}
    metrics = {
        "stale_data": 0,
        "material_ohlc_anomalies": 0,
        "material_negative_amount": 0,
        "duplicate_timestamps": 0,
        "lunch_break_bars": 0,
        "thirty_minute_ohlc_mismatches": 0,
        "non_first_bucket_volume_amount_mismatches": 0,
        "first_bucket_matches": 0,
        "first_bucket_checks": 0,
        "ex_factor_passes": 0,
        "ex_factor_checks": 0,
        "http_429": 0,
        "sensitive_shape_hits": 0,
    }
    minute_comparison: dict[str, Any] = {
        "observation_date": observation_date,
        "timezone": "Asia/Shanghai",
        "symbols": {},
        "vendor_pending": list(VENDOR_PENDING),
    }
    for symbol in SYMBOLS:
        daily = _validate_daily(reports, symbol, observation_date)
        factors = _validate_factors(reports, symbol)
        minute_contract, minute_metrics = _validate_minute_symbol(
            minute,
            symbol,
            observation_date,
        )
        minute_contract["daily"] = daily
        minute_contract["ex_factors"] = factors
        symbol_contracts[symbol] = minute_contract
        for key, value in minute_metrics.items():
            metrics[key] += value
        metrics["ex_factor_passes"] += 1
        metrics["ex_factor_checks"] += 1
        minute_comparison["symbols"][symbol] = minute_contract["minute_to_30m"]
    _require(
        metrics["material_ohlc_anomalies"] == 0,
        "MATERIAL_OHLC_ANOMALY",
    )
    _require(
        metrics["material_negative_amount"] == 0,
        "MATERIAL_NEGATIVE_AMOUNT",
    )
    _require(
        metrics["thirty_minute_ohlc_mismatches"] == 0,
        "THIRTY_MINUTE_OHLC_MISMATCH",
    )
    _require(
        metrics["non_first_bucket_volume_amount_mismatches"] == 0,
        "NON_FIRST_BUCKET_VOLUME_AMOUNT_MISMATCH",
    )
    manifest = hash_evidence(source_evidence_paths(root), root)
    return {
        "contract": "tickflow_phase1_2_observation_day_v1",
        "observation_date": observation_date,
        "observation_day": 1,
        "status": "DAY_PASSED",
        "idempotency_key": _idempotency_key(observation_date),
        "evidence_origin": "phase1_1_reuse",
        "live_request_reexecuted": False,
        "source_status": SOURCE_STATUS,
        "source_report": "reports/tickflow_phase1_numeric_contract_closeout.md",
        "source_request_audit": "reports/phase1_tickflow_request_audit.json",
        "source_evidence_dir": "reports/phase1_numeric_contracts/",
        "source_request_count": 14,
        "new_api_request_count": 0,
        "source_data_hashes_verified": True,
        "source_evidence": manifest,
        "source_evidence_digest": _canonical_sha256(manifest),
        "vendor_pending": list(VENDOR_PENDING),
        "symbols": {symbol: "PASSED" for symbol in SYMBOLS},
        "symbol_contracts": symbol_contracts,
        "minute_30m_comparison": minute_comparison,
        "request_audit": request_audit,
        "metrics": metrics,
        "boundaries": boundaries,
        "failure_reasons": [],
    }


def _empty_metrics() -> dict[str, int]:
    return {
        "stale_data": 0,
        "material_ohlc_anomalies": 0,
        "material_negative_amount": 0,
        "duplicate_timestamps": 0,
        "lunch_break_bars": 0,
        "thirty_minute_ohlc_mismatches": 0,
        "non_first_bucket_volume_amount_mismatches": 0,
        "first_bucket_matches": 0,
        "first_bucket_checks": 0,
        "ex_factor_passes": 0,
        "ex_factor_checks": 0,
        "http_429": 0,
        "sensitive_shape_hits": 0,
    }


def build_non_trading_day(observation_date: str) -> dict[str, Any]:
    market_date = _parse_observation_date(observation_date)
    _require(
        not _is_sse_trading_day(market_date),
        "EXPECTED_NON_TRADING_DAY",
        observation_date,
    )
    calendar_evidence = [
        {
            "calendar_source": "SSE_2026_OFFICIAL_CLOSURES",
            "observation_date": observation_date,
            "trading_day": False,
        }
    ]
    return {
        "contract": "tickflow_phase1_2_observation_day_v1",
        "observation_date": observation_date,
        "observation_day": None,
        "status": "NON_TRADING_DAY",
        "idempotency_key": _idempotency_key(observation_date),
        "evidence_origin": "phase1_2_calendar",
        "live_request_reexecuted": False,
        "source_status": "NON_TRADING_DAY",
        "source_report": None,
        "source_request_audit": None,
        "source_evidence_dir": None,
        "source_request_count": 0,
        "new_api_request_count": 0,
        "source_data_hashes_verified": True,
        "source_evidence": calendar_evidence,
        "source_evidence_digest": _canonical_sha256(calendar_evidence),
        "vendor_pending": list(VENDOR_PENDING),
        "symbols": {symbol: "PENDING" for symbol in SYMBOLS},
        "symbol_contracts": {
            symbol: {
                "symbol": symbol,
                "name": SYMBOL_NAMES[symbol],
                "status": "NON_TRADING_DAY",
            }
            for symbol in SYMBOLS
        },
        "minute_30m_comparison": {
            "observation_date": observation_date,
            "status": "NON_TRADING_DAY",
            "symbols": {},
        },
        "request_audit": {
            "pipeline": "phase1_2_observation_calendar_gate",
            "source_request_count": 0,
            "new_api_request_count": 0,
            "live_request_reexecuted": False,
            "retry_count": 0,
            "http_429": 0,
            "intraday_batch_called": False,
            "credential_values_recorded": False,
            "records": [],
        },
        "metrics": _empty_metrics(),
        "boundaries": {
            "ai": "NOT_CONFIGURED",
            "paper_trading": "NOT_STARTED",
            "cloud_redeployed": False,
            "integrated_gold_enabled": False,
            "integrated_gold_external_send_count": 0,
            "real_key": "NOT_EXPOSED",
        },
        "failure_reasons": [],
    }


def _contains_sensitive_shape(value: Any, path: tuple[str, ...] = ()) -> bool:
    forbidden_keys = {
        "api_key",
        "authorization",
        "cookie",
        "password",
        "secret",
        "session_cookie",
        "token",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if (
                normalized in forbidden_keys
                and item is not None
                and item is not False
                and item != ""
            ):
                return True
            if _contains_sensitive_shape(item, (*path, normalized)):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive_shape(item, path) for item in value)
    if isinstance(value, str):
        return (
            re.search(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", value)
            is not None
            or re.search(r"\bsk-[A-Za-z0-9_-]{12,}", value) is not None
        )
    return False


def _live_source_manifest(
    result_path: Path,
    repo_root: Path,
) -> list[dict[str, Any]]:
    resolved = result_path.resolve()
    try:
        label = resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        label = f"ephemeral/{resolved.name}"
    return [
        {
            "path": label,
            "size": resolved.stat().st_size,
            "sha256": sha256_file(resolved),
            "retained_after_materialization": False,
        }
    ]


def _explicit_bool(
    mapping: Mapping[str, Any],
    field: str,
    *,
    missing_code: str,
    invalid_code: str,
    detail: str | None = None,
) -> bool:
    field_detail = detail or field
    _require(field in mapping, missing_code, field_detail)
    value = mapping[field]
    _require(isinstance(value, bool), invalid_code, field_detail)
    return value


def normalize_boundary_evidence_v1(
    result: Mapping[str, Any],
    *,
    source_evidence_hash: str,
) -> dict[str, Any]:
    _require(
        re.fullmatch(r"[0-9a-fA-F]{64}", source_evidence_hash) is not None,
        "SOURCE_EVIDENCE_HASH_INVALID",
    )
    boundaries = _nested(result, "boundaries", code="BOUNDARY_EVIDENCE_MISSING")
    boolean_fields = (
        "cloud_redeployed",
        "real_key_exposed",
        "raw_market_values_modified",
        "timestamps_shifted",
        "missing_minutes_filled",
        "ai_configured",
        "paper_trading_started",
        "integrated_gold_enabled",
    )
    if "boundary_schema_version" in boundaries:
        version = boundaries["boundary_schema_version"]
        _require(
            isinstance(version, int)
            and not isinstance(version, bool)
            and version == 1,
            "BOUNDARY_SCHEMA_VERSION_UNSUPPORTED",
            repr(version),
        )
        canonical = {
            "boundary_schema_version": version,
            **{
                field: _explicit_bool(
                    boundaries,
                    field,
                    missing_code="BOUNDARY_FIELD_MISSING",
                    invalid_code="BOUNDARY_FIELD_INVALID",
                )
                for field in boolean_fields
            },
            "integrated_gold_external_send_count": _int_value(
                boundaries.get("integrated_gold_external_send_count"),
                "GOLD_SEND_COUNT_INVALID",
            ),
        }
        return {
            "boundaries": canonical,
            "normalization_applied": False,
            "normalization_version": "boundary-v1",
            "source_schema": "boundary-v1",
            "source_evidence_hash": source_evidence_hash.lower(),
            "derived_fields": [],
        }

    legacy_boolean_fields = (
        "cloud_redeployed",
        "real_key_exposed",
        "ai_configured_by_phase",
        "paper_trading_started",
        "integrated_gold_enabled",
    )
    legacy_values = {
        field: _explicit_bool(
            boundaries,
            field,
            missing_code="LEGACY_BOUNDARY_FIELD_MISSING",
            invalid_code="LEGACY_BOUNDARY_FIELD_INVALID",
        )
        for field in legacy_boolean_fields
    }
    mutation_field_map = {
        "raw_market_values_modified": "raw_values_modified",
        "timestamps_shifted": "timestamps_shifted",
        "missing_minutes_filled": "missing_minutes_filled",
    }
    mutation_values: dict[str, bool] = {}
    for canonical_field, legacy_field in mutation_field_map.items():
        per_symbol: list[bool] = []
        for symbol in SYMBOLS:
            dual_first_bucket = _nested(
                result,
                "contracts",
                "minute_to_30m",
                "symbols",
                symbol,
                "local_1m_to_30m",
                "dual_first_bucket",
                code="LEGACY_BOUNDARY_PATH_MISSING",
            )
            value = _explicit_bool(
                dual_first_bucket,
                legacy_field,
                missing_code="LEGACY_BOUNDARY_FIELD_MISSING",
                invalid_code="LEGACY_BOUNDARY_FIELD_INVALID",
                detail=f"{symbol}.{legacy_field}",
            )
            _require(
                value is False,
                "BOUNDARY_EVIDENCE_FAILED",
                f"{symbol}.{legacy_field}",
            )
            per_symbol.append(value)
        mutation_values[canonical_field] = any(per_symbol)

    canonical = {
        "boundary_schema_version": 1,
        "cloud_redeployed": legacy_values["cloud_redeployed"],
        "real_key_exposed": legacy_values["real_key_exposed"],
        **mutation_values,
        "ai_configured": legacy_values["ai_configured_by_phase"],
        "paper_trading_started": legacy_values["paper_trading_started"],
        "integrated_gold_enabled": legacy_values["integrated_gold_enabled"],
        "integrated_gold_external_send_count": _int_value(
            boundaries.get("integrated_gold_external_send_count"),
            "GOLD_SEND_COUNT_INVALID",
        ),
    }
    return {
        "boundaries": canonical,
        "normalization_applied": True,
        "normalization_version": "boundary-v1",
        "source_schema": "legacy-phase1.1",
        "source_evidence_hash": source_evidence_hash.lower(),
        "derived_fields": [
            "raw_market_values_modified",
            "timestamps_shifted",
            "missing_minutes_filled",
            "ai_configured",
        ],
    }


def _validate_live_security(
    result: Mapping[str, Any],
    *,
    source_evidence_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(
        not _contains_sensitive_shape(result),
        "SENSITIVE_SHAPE_DETECTED",
        "live result",
    )
    gate = _nested(result, "security_gate", code="SECURITY_GATE_MISSING")
    for field in (
        "passed",
        "authentication_configured",
        "current_https_session_valid",
        "has_tickflow_key",
        "masked_tickflow_key_present",
        "recent_log_credential_shape_scan_passed",
    ):
        _require(gate.get(field) is True, "SECURITY_GATE_FAILED", field)
    for field in (
        "has_ai_key",
        "credential_values_recorded",
        "integrated_gold_enabled",
    ):
        _require(gate.get(field) is False, "SECURITY_GATE_FAILED", field)
    _require(
        _int_value(
            gate.get("integrated_gold_external_send_count"),
            "GOLD_SEND_COUNT_INVALID",
        )
        == 0,
        "GOLD_EXTERNAL_SEND_DETECTED",
    )
    normalized = normalize_boundary_evidence_v1(
        result,
        source_evidence_hash=source_evidence_hash,
    )
    boundaries = normalized["boundaries"]
    for field in (
        "cloud_redeployed",
        "real_key_exposed",
        "raw_market_values_modified",
        "timestamps_shifted",
        "missing_minutes_filled",
        "ai_configured",
        "paper_trading_started",
        "integrated_gold_enabled",
    ):
        _require(boundaries.get(field) is False, "BOUNDARY_EVIDENCE_FAILED", field)
    _require(
        _int_value(
            boundaries.get("integrated_gold_external_send_count"),
            "GOLD_SEND_COUNT_INVALID",
        )
        == 0,
        "GOLD_EXTERNAL_SEND_DETECTED",
    )
    return (
        {
            "ai": "NOT_CONFIGURED",
            "paper_trading": "NOT_STARTED",
            "cloud_redeployed": False,
            "integrated_gold_enabled": False,
            "integrated_gold_external_send_count": 0,
            "real_key": "NOT_EXPOSED",
        },
        {
            key: value
            for key, value in normalized.items()
            if key != "boundaries"
        },
    )


def _compact_live_audit(
    result: Mapping[str, Any],
    *,
    allow_429: bool,
) -> dict[str, Any]:
    records = result.get("request_audit")
    _require(isinstance(records, list) and records, "REQUEST_AUDIT_INVALID")
    count = _int_value(
        result.get("tickflow_request_count"),
        "REQUEST_AUDIT_INVALID",
    )
    _require(count == len(records), "REQUEST_COUNT_MISMATCH")
    detected_429 = result.get("http_429_detected") is True
    _require(detected_429 is allow_429, "HTTP_429_EVIDENCE_MISMATCH")
    compact_records: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        _require(isinstance(record, Mapping), "REQUEST_AUDIT_INVALID")
        capability = record.get("capability")
        _require(isinstance(capability, str), "REQUEST_AUDIT_INVALID")
        _require("intraday_batch" not in capability, "INTRADAY_BATCH_DETECTED")
        retry_count = _int_value(
            record.get("retry_count"),
            "REQUEST_AUDIT_INVALID",
        )
        _require(retry_count == 0, "RETRY_DETECTED", capability)
        status = record.get("status")
        if allow_429 and index == len(records) - 1:
            _require(
                status == "FAILED"
                and _int_value(record.get("http_status"), "HTTP_429_DETECTED")
                == 429,
                "HTTP_429_EVIDENCE_MISMATCH",
            )
        else:
            _require(status == "PASSED", "REQUEST_FAILED", capability)
        compact_records.append(
            {
                "pipeline": record.get(
                    "pipeline",
                    "phase1_1_minute_contract_closeout",
                ),
                "capability": capability,
                "symbol_count": _int_value(
                    record.get("symbol_count"),
                    "REQUEST_AUDIT_INVALID",
                ),
                "duration_ms": _int_value(
                    record.get("duration_ms"),
                    "REQUEST_AUDIT_INVALID",
                ),
                "status": status,
                "retry_count": 0,
                "rate_limit_wait_ms": record.get("rate_limit_wait_ms", 0),
                **(
                    {"http_status": 429}
                    if allow_429 and index == len(records) - 1
                    else {}
                ),
            }
        )
    if not allow_429:
        _require(count == 14, "REQUEST_COUNT_MISMATCH")
    return {
        "pipeline": "phase1_1_minute_contract_closeout",
        "source_request_count": count,
        "new_api_request_count": count,
        "live_request_reexecuted": True,
        "retry_count": 0,
        "http_429": 1 if allow_429 else 0,
        "intraday_batch_called": False,
        "credential_values_recorded": False,
        "records": compact_records,
    }


def _next_observation_day(reports: Path, observation_date: str) -> int:
    observation_root = reports / "phase1_observation"
    prior_dates: list[str] = []
    for directory in _day_directories(observation_root):
        if directory.name >= observation_date:
            continue
        summary = _read_json(directory / "daily_summary.json")
        if summary.get("status") == "DAY_PASSED":
            prior_dates.append(directory.name)
    return len(prior_dates) + 1


def _blocked_live_day(
    *,
    result_path: Path,
    repo_root: Path,
    observation_date: str,
    request_audit: Mapping[str, Any],
    reason: str,
    source_status: str,
) -> dict[str, Any]:
    manifest = _live_source_manifest(result_path, repo_root)
    metrics = _empty_metrics()
    metric_by_reason = {
        "HTTP_429_DETECTED": "http_429",
        "DAILY_STALE": "stale_data",
        "ONE_MINUTE_STALE": "stale_data",
        "THIRTY_MINUTE_STALE": "stale_data",
        "MATERIAL_OHLC_ANOMALY": "material_ohlc_anomalies",
        "MATERIAL_NEGATIVE_AMOUNT": "material_negative_amount",
        "DUPLICATE_TIMESTAMP": "duplicate_timestamps",
        "LUNCH_BREAK_BAR": "lunch_break_bars",
        "THIRTY_MINUTE_OHLC_MISMATCH": "thirty_minute_ohlc_mismatches",
        "NON_FIRST_BUCKET_VOLUME_AMOUNT_MISMATCH": (
            "non_first_bucket_volume_amount_mismatches"
        ),
        "SENSITIVE_SHAPE_DETECTED": "sensitive_shape_hits",
    }
    metric = metric_by_reason.get(reason)
    if metric is not None:
        metrics[metric] = 1
    if reason == "FIRST_BUCKET_UNEXPLAINED":
        metrics["first_bucket_checks"] = 1
    if reason == "EX_FACTOR_CONTRACT_FAILED":
        metrics["ex_factor_checks"] = 1
    return {
        "contract": "tickflow_phase1_2_observation_day_v1",
        "observation_date": observation_date,
        "observation_day": None,
        "status": "DAY_BLOCKED",
        "idempotency_key": _idempotency_key(observation_date),
        "evidence_origin": "phase1_2_live",
        "live_request_reexecuted": True,
        "source_status": source_status,
        "source_report": None,
        "source_request_audit": "embedded_sanitized_live_result",
        "source_evidence_dir": None,
        "source_request_count": request_audit["source_request_count"],
        "new_api_request_count": request_audit["new_api_request_count"],
        "source_data_hashes_verified": True,
        "source_evidence": manifest,
        "source_evidence_digest": _canonical_sha256(manifest),
        "vendor_pending": list(VENDOR_PENDING),
        "symbols": {symbol: "BLOCKED" for symbol in SYMBOLS},
        "symbol_contracts": {
            symbol: {
                "symbol": symbol,
                "name": SYMBOL_NAMES[symbol],
                "status": "BLOCKED",
                "failure_reason": reason,
            }
            for symbol in SYMBOLS
        },
        "minute_30m_comparison": {
            "observation_date": observation_date,
            "status": "BLOCKED",
            "failure_reason": reason,
            "symbols": {},
        },
        "request_audit": dict(request_audit),
        "metrics": metrics,
        "boundaries": {
            "ai": "NOT_CONFIGURED",
            "paper_trading": "NOT_STARTED",
            "cloud_redeployed": False,
            "integrated_gold_enabled": False,
            "integrated_gold_external_send_count": 0,
            "real_key": "NOT_EXPOSED",
        },
        "failure_reasons": [reason],
    }


def _build_live_day_or_raise(
    result_path: Path,
    repo_root: Path,
    observation_date: str,
) -> dict[str, Any]:
    _require(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", observation_date) is not None,
        "OBSERVATION_DATE_INVALID",
    )
    result = _read_json(result_path)
    source_evidence_hash = sha256_file(result_path)
    status = result.get("final_status")
    if status == "PHASE1_RATE_LIMIT_BLOCKED":
        request_audit = _compact_live_audit(result, allow_429=True)
        return _blocked_live_day(
            result_path=result_path,
            repo_root=repo_root,
            observation_date=observation_date,
            request_audit=request_audit,
            reason="HTTP_429_DETECTED",
            source_status="PHASE1_RATE_LIMIT_BLOCKED",
        )
    _require(status == SOURCE_STATUS, "SOURCE_STATUS_MISMATCH", str(status))
    boundaries, boundary_normalization = _validate_live_security(
        result,
        source_evidence_hash=source_evidence_hash,
    )
    request_audit = _compact_live_audit(result, allow_429=False)
    contracts = _nested(result, "contracts", code="LIVE_CONTRACTS_MISSING")
    daily_contracts = _nested(contracts, "daily", code="DAILY_CONTRACT_MISSING")
    factor_contracts = _nested(
        contracts,
        "ex_factors",
        code="EX_FACTOR_CONTRACT_MISSING",
    )
    minute = _nested(
        contracts,
        "minute_to_30m",
        code="MINUTE_CONTRACT_MISSING",
    )
    _require(
        minute.get("passed") is True
        and minute.get("timezone") == "Asia/Shanghai"
        and minute.get("intraday_batch_dependency") is False,
        "MINUTE_CONTRACT_FAILED",
    )
    metrics = _empty_metrics()
    symbol_contracts: dict[str, dict[str, Any]] = {}
    minute_comparison: dict[str, Any] = {
        "observation_date": observation_date,
        "timezone": "Asia/Shanghai",
        "symbols": {},
        "vendor_pending": list(VENDOR_PENDING),
    }
    for symbol in SYMBOLS:
        _require(
            symbol in daily_contracts
            and isinstance(daily_contracts[symbol], Mapping),
            "DAILY_CONTRACT_MISSING",
            symbol,
        )
        _require(
            symbol in factor_contracts
            and isinstance(factor_contracts[symbol], Mapping),
            "EX_FACTOR_CONTRACT_MISSING",
            symbol,
        )
        daily = _validate_daily_contract(
            daily_contracts[symbol],
            symbol,
            observation_date,
        )
        factors = _validate_factor_contract(factor_contracts[symbol], symbol)
        minute_contract, minute_metrics = _validate_minute_symbol(
            minute,
            symbol,
            observation_date,
        )
        minute_contract["daily"] = daily
        minute_contract["ex_factors"] = factors
        symbol_contracts[symbol] = minute_contract
        for key, value in minute_metrics.items():
            metrics[key] += value
        metrics["ex_factor_passes"] += 1
        metrics["ex_factor_checks"] += 1
        minute_comparison["symbols"][symbol] = minute_contract["minute_to_30m"]
    manifest = _live_source_manifest(result_path, repo_root)
    return {
        "contract": "tickflow_phase1_2_observation_day_v1",
        "observation_date": observation_date,
        "observation_day": _next_observation_day(
            repo_root.resolve() / "reports",
            observation_date,
        ),
        "status": "DAY_PASSED",
        "idempotency_key": _idempotency_key(observation_date),
        "evidence_origin": "phase1_2_daily_live",
        "live_request_reexecuted": True,
        "source_status": SOURCE_STATUS,
        "source_report": None,
        "source_request_audit": "embedded_sanitized_live_result",
        "source_evidence_dir": None,
        "source_request_count": request_audit["source_request_count"],
        "new_api_request_count": request_audit["new_api_request_count"],
        "source_data_hashes_verified": True,
        "source_evidence": manifest,
        "source_evidence_digest": _canonical_sha256(manifest),
        "vendor_pending": list(VENDOR_PENDING),
        "symbols": {symbol: "PASSED" for symbol in SYMBOLS},
        "symbol_contracts": symbol_contracts,
        "minute_30m_comparison": minute_comparison,
        "request_audit": request_audit,
        "metrics": metrics,
        "boundaries": boundaries,
        "boundary_normalization": boundary_normalization,
        "failure_reasons": [],
    }


def _minimal_blocked_audit(result: Mapping[str, Any]) -> dict[str, Any]:
    records = result.get("request_audit")
    record_count = len(records) if isinstance(records, list) else 0
    raw_count = result.get("tickflow_request_count")
    count = (
        raw_count
        if isinstance(raw_count, int) and not isinstance(raw_count, bool)
        else record_count
    )
    return {
        "pipeline": "phase1_1_minute_contract_closeout",
        "source_request_count": count,
        "new_api_request_count": count,
        "live_request_reexecuted": True,
        "retry_count": 0,
        "http_429": 1 if result.get("http_429_detected") is True else 0,
        "intraday_batch_called": False,
        "credential_values_recorded": False,
        "records": [],
        "records_omitted_for_safety": True,
    }


def build_live_day(
    result_path: Path,
    repo_root: Path,
    observation_date: str,
) -> dict[str, Any]:
    try:
        return _build_live_day_or_raise(
            result_path,
            repo_root,
            observation_date,
        )
    except EvidenceError as exc:
        reason = str(exc).split(":", 1)[0]
        if reason in {
            "SOURCE_FILE_MISSING",
            "SOURCE_JSON_INVALID",
            "SOURCE_JSON_NOT_OBJECT",
            "OBSERVATION_DATE_INVALID",
        }:
            raise
        result = _read_json(result_path)
        source_status = result.get("final_status")
        if not isinstance(source_status, str):
            source_status = "PHASE1_OBSERVATION_VALIDATION_FAILED"
        request_audit: Mapping[str, Any]
        if reason == "SENSITIVE_SHAPE_DETECTED":
            request_audit = _minimal_blocked_audit(result)
        else:
            try:
                request_audit = _compact_live_audit(
                    result,
                    allow_429=result.get("http_429_detected") is True,
                )
            except EvidenceError:
                request_audit = _minimal_blocked_audit(result)
        return _blocked_live_day(
            result_path=result_path,
            repo_root=repo_root,
            observation_date=observation_date,
            request_audit=request_audit,
            reason=reason,
            source_status=source_status,
        )


def _write_json_file(path: Path, value: Any) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    with path.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        _fsync_directory(path.parent)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


def _summary_from_day(day: Mapping[str, Any]) -> dict[str, Any]:
    omitted = {"symbol_contracts", "minute_30m_comparison", "request_audit"}
    return {key: value for key, value in day.items() if key not in omitted}


def materialize_day(
    day: Mapping[str, Any],
    reports_root: Path,
) -> dict[str, Any]:
    observation_date = day.get("observation_date")
    _require(
        isinstance(observation_date, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", observation_date) is not None,
        "OBSERVATION_DATE_INVALID",
    )
    observation_root = reports_root.resolve() / "phase1_observation"
    observation_root.mkdir(parents=True, exist_ok=True)
    target = observation_root / observation_date
    summary = _summary_from_day(day)
    status = summary.get("status")
    _require(status in DAY_STATUSES, "OBSERVATION_STATUS_INVALID", str(status))
    _require(
        summary.get("idempotency_key") == _idempotency_key(observation_date),
        "OBSERVATION_IDEMPOTENCY_KEY_INVALID",
    )
    manifest = summary.get("source_evidence")
    _require(isinstance(manifest, list), "SOURCE_EVIDENCE_MANIFEST_MISSING")
    if target.exists():
        _require(target.is_dir() and not target.is_symlink(), "OBSERVATION_PATH_INVALID")
        existing = _read_json(target / "daily_summary.json")
        if existing != summary:
            raise EvidenceConflictError(
                f"OBSERVATION_EVIDENCE_CONFLICT: {observation_date}"
            )
        return existing
    _require(
        summary.get("source_evidence_digest") == _canonical_sha256(manifest),
        "SOURCE_EVIDENCE_DIGEST_MISMATCH",
    )

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{observation_date}.tmp-",
            dir=observation_root,
        )
    )
    try:
        _write_json_file(temporary / "daily_summary.json", summary)
        contracts = day.get("symbol_contracts")
        _require(isinstance(contracts, Mapping), "SYMBOL_CONTRACTS_MISSING")
        for symbol in SYMBOLS:
            _require(
                symbol in contracts and isinstance(contracts[symbol], Mapping),
                "SYMBOL_CONTRACTS_MISSING",
                symbol,
            )
            _write_json_file(
                temporary / f"{COMPACT_SYMBOLS[symbol]}_contract.json",
                contracts[symbol],
            )
        _write_json_file(
            temporary / "minute_30m_comparison.json",
            day.get("minute_30m_comparison"),
        )
        _write_json_file(
            temporary / "request_audit.json",
            day.get("request_audit"),
        )
        _fsync_directory(temporary)
        os.replace(temporary, target)
        _fsync_directory(observation_root)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def _repo_relative_path(path: Path, repo_root: Path, code: str) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        _fail(code, str(path))


def _frozen_file_by_role(
    manifest: Mapping[str, Any],
    *,
    role: str,
    repo_root: Path,
) -> tuple[Path, str]:
    entries = manifest.get("frozen_files")
    _require(isinstance(entries, list), "FROZEN_FILE_MANIFEST_INVALID")
    matches = [
        entry
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("role") == role
    ]
    _require(len(matches) == 1, "FROZEN_FILE_ROLE_INVALID", role)
    entry = matches[0]
    raw_path = entry.get("path")
    expected_hash = entry.get("sha256")
    _require(
        isinstance(raw_path, str)
        and isinstance(expected_hash, str)
        and re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is not None,
        "FROZEN_FILE_MANIFEST_INVALID",
        role,
    )
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root.resolve() / path
    _repo_relative_path(path, repo_root, "FROZEN_FILE_OUTSIDE_REPOSITORY")
    actual_hash = sha256_file(path)
    _require(
        actual_hash == expected_hash.lower(),
        "FROZEN_FILE_HASH_MISMATCH",
        role,
    )
    return path, actual_hash


def _validate_blocked_evidence_manifest(
    *,
    result_path: Path,
    repo_root: Path,
    observation_date: str,
    evidence_manifest_path: Path,
) -> dict[str, Any]:
    manifest = _read_json(evidence_manifest_path)
    _require(
        manifest.get("contract")
        == "tickflow_phase1_2_original_blocked_evidence_v1",
        "FROZEN_MANIFEST_CONTRACT_INVALID",
    )
    _require(
        manifest.get("observation_date") == observation_date,
        "FROZEN_MANIFEST_DATE_MISMATCH",
    )
    _require(
        manifest.get("original_day_status") == "DAY_BLOCKED",
        "ORIGINAL_BLOCKED_STATUS_MISSING",
    )
    _require(
        manifest.get("original_blocker") == "BOUNDARY_SCHEMA_MISMATCH",
        "ORIGINAL_BLOCKER_MISMATCH",
    )
    _require(
        isinstance(manifest.get("original_blocked_at"), str),
        "ORIGINAL_BLOCKED_TIMESTAMP_MISSING",
    )
    _require(
        manifest.get("source_evidence_modified") is False
        and manifest.get("api_requests_added") == 0
        and manifest.get("second_live_run") is False,
        "FROZEN_MANIFEST_BOUNDARY_INVALID",
    )
    source = _nested(
        manifest,
        "source_live_result",
        code="FROZEN_SOURCE_MANIFEST_INVALID",
    )
    expected_source_path = source.get("path")
    expected_source_hash = source.get("sha256")
    expected_source_size = source.get("size")
    _require(
        isinstance(expected_source_path, str)
        and isinstance(expected_source_hash, str)
        and re.fullmatch(r"[0-9a-fA-F]{64}", expected_source_hash) is not None
        and isinstance(expected_source_size, int)
        and not isinstance(expected_source_size, bool),
        "FROZEN_SOURCE_MANIFEST_INVALID",
    )
    _require(
        Path(expected_source_path).resolve() == result_path.resolve(),
        "FROZEN_SOURCE_PATH_MISMATCH",
    )
    source_hash = sha256_file(result_path)
    _require(
        source_hash == expected_source_hash.lower(),
        "FROZEN_SOURCE_HASH_MISMATCH",
    )
    _require(
        result_path.stat().st_size == expected_source_size,
        "FROZEN_SOURCE_SIZE_MISMATCH",
    )

    preserved_summary_path, preserved_summary_hash = _frozen_file_by_role(
        manifest,
        role="preserved_original_blocked_day_summary",
        repo_root=repo_root,
    )
    preserved_audit_path, preserved_audit_hash = _frozen_file_by_role(
        manifest,
        role="preserved_original_day2_compact_request_audit",
        repo_root=repo_root,
    )
    preserved_index_path, preserved_index_hash = _frozen_file_by_role(
        manifest,
        role="preserved_original_blocked_observation_index",
        repo_root=repo_root,
    )
    diagnosis_path, diagnosis_hash = _frozen_file_by_role(
        manifest,
        role="original_blocker_diagnosis",
        repo_root=repo_root,
    )
    cumulative_audit_path, cumulative_audit_hash = _frozen_file_by_role(
        manifest,
        role="phase1_cumulative_request_audit",
        repo_root=repo_root,
    )
    preserved_summary = _read_json(preserved_summary_path)
    _require(
        preserved_summary.get("observation_date") == observation_date
        and preserved_summary.get("status") == "DAY_BLOCKED",
        "ORIGINAL_BLOCKED_SUMMARY_INVALID",
    )
    preserved_audit = _read_json(preserved_audit_path)
    _require(
        preserved_audit.get("source_request_count") == 14
        and preserved_audit.get("new_api_request_count") == 14
        and preserved_audit.get("live_request_reexecuted") is True
        and preserved_audit.get("http_429") == 0,
        "ORIGINAL_BLOCKED_AUDIT_INVALID",
    )
    preserved_index = _read_json(preserved_index_path)
    _require(
        preserved_index.get("final_status") == "PHASE1_OBSERVATION_BLOCKED"
        and preserved_index.get("valid_observation_days") == 1,
        "ORIGINAL_BLOCKED_INDEX_INVALID",
    )
    cumulative_audit = _read_json(cumulative_audit_path)
    _require(
        cumulative_audit.get("phase1_1_request_count") == 14
        and cumulative_audit.get("http_429_detected") is False
        and cumulative_audit.get("retry_count") == 0
        and cumulative_audit.get("cloud_redeployed") is False
        and cumulative_audit.get("integrated_gold_enabled") is False
        and cumulative_audit.get("integrated_gold_external_send_count") == 0
        and cumulative_audit.get("real_key_exposed") is False,
        "CUMULATIVE_REQUEST_AUDIT_INVALID",
    )
    _require(
        evidence_manifest_path.resolve().parent
        == (
            repo_root.resolve()
            / "reports"
            / "phase1_observation"
            / observation_date
        ),
        "FROZEN_MANIFEST_PATH_INVALID",
    )
    return {
        "manifest": manifest,
        "source_hash": source_hash,
        "manifest_hash": sha256_file(evidence_manifest_path),
        "manifest_path": _repo_relative_path(
            evidence_manifest_path,
            repo_root,
            "FROZEN_MANIFEST_OUTSIDE_REPOSITORY",
        ),
        "preserved_summary_path": _repo_relative_path(
            preserved_summary_path,
            repo_root,
            "FROZEN_FILE_OUTSIDE_REPOSITORY",
        ),
        "preserved_summary_hash": preserved_summary_hash,
        "preserved_audit_path": _repo_relative_path(
            preserved_audit_path,
            repo_root,
            "FROZEN_FILE_OUTSIDE_REPOSITORY",
        ),
        "preserved_audit_hash": preserved_audit_hash,
        "preserved_index_path": _repo_relative_path(
            preserved_index_path,
            repo_root,
            "FROZEN_FILE_OUTSIDE_REPOSITORY",
        ),
        "preserved_index_hash": preserved_index_hash,
        "diagnosis_path": _repo_relative_path(
            diagnosis_path,
            repo_root,
            "FROZEN_FILE_OUTSIDE_REPOSITORY",
        ),
        "diagnosis_hash": diagnosis_hash,
        "cumulative_audit_path": _repo_relative_path(
            cumulative_audit_path,
            repo_root,
            "FROZEN_FILE_OUTSIDE_REPOSITORY",
        ),
        "cumulative_audit_hash": cumulative_audit_hash,
    }


def _rematerialized_day_files(day: Mapping[str, Any]) -> dict[str, Any]:
    contracts = day.get("symbol_contracts")
    _require(isinstance(contracts, Mapping), "SYMBOL_CONTRACTS_MISSING")
    files: dict[str, Any] = {
        "daily_summary.json": _summary_from_day(day),
        "minute_30m_comparison.json": day.get("minute_30m_comparison"),
        "request_audit.json": day.get("request_audit"),
    }
    for symbol in SYMBOLS:
        _require(
            symbol in contracts and isinstance(contracts[symbol], Mapping),
            "SYMBOL_CONTRACTS_MISSING",
            symbol,
        )
        files[f"{COMPACT_SYMBOLS[symbol]}_contract.json"] = contracts[symbol]
    return files


def _publish_rematerialized_day(
    day: Mapping[str, Any],
    target: Path,
) -> None:
    _require(target.is_dir() and not target.is_symlink(), "OBSERVATION_PATH_INVALID")
    files = _rematerialized_day_files(day)
    current_summary = _read_json(target / "daily_summary.json")
    if current_summary.get("status") == "DAY_PASSED":
        for filename, expected in files.items():
            _require(
                _read_json(target / filename) == expected,
                "REMATERIALIZATION_IDEMPOTENCY_CONFLICT",
                filename,
            )
        return
    _require(
        current_summary.get("status") == "DAY_BLOCKED",
        "ORIGINAL_BLOCKED_STATUS_MISSING",
    )

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{target.name}.rematerialize-",
            dir=target.parent,
        )
    )
    try:
        for filename, value in files.items():
            staged = temporary / filename
            _write_json_file(staged, value)
            staged.chmod(0o600)
        for filename in sorted(files):
            if filename == "daily_summary.json":
                continue
            os.replace(temporary / filename, target / filename)
        _fsync_directory(target)
        os.replace(
            temporary / "daily_summary.json",
            target / "daily_summary.json",
        )
        _fsync_directory(target)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def rematerialize_blocked_live_day(
    *,
    result_path: Path,
    repo_root: Path,
    observation_date: str,
    evidence_manifest_path: Path,
    schema_fix_commit: str,
) -> dict[str, Any]:
    _require(
        re.fullmatch(r"[0-9a-f]{40}", schema_fix_commit) is not None,
        "SCHEMA_FIX_COMMIT_INVALID",
    )
    frozen = _validate_blocked_evidence_manifest(
        result_path=result_path,
        repo_root=repo_root,
        observation_date=observation_date,
        evidence_manifest_path=evidence_manifest_path,
    )
    passed = _build_live_day_or_raise(
        result_path,
        repo_root,
        observation_date,
    )
    source_request_count = _int_value(
        passed.get("source_request_count"),
        "REQUEST_AUDIT_INVALID",
    )
    _require(source_request_count == 14, "REQUEST_COUNT_MISMATCH")
    source_audit = passed.get("request_audit")
    _require(isinstance(source_audit, Mapping), "REQUEST_AUDIT_INVALID")
    request_audit = {
        **source_audit,
        "source_live_request_count": source_request_count,
        "source_request_count": source_request_count,
        "new_api_request_count": 0,
        "live_request_reexecuted": False,
        "rematerialization_mode": "OFFLINE_EVIDENCE_REUSE",
    }
    manifest = frozen["manifest"]
    rematerialized = {
        **passed,
        "evidence_origin": "phase1_2_live_reuse",
        "live_request_reexecuted": False,
        "source_live_request_count": source_request_count,
        "source_request_count": source_request_count,
        "new_api_request_count": 0,
        "source_request_audit": frozen["preserved_audit_path"],
        "source_evidence_manifest": frozen["manifest_path"],
        "source_evidence_manifest_sha256": frozen["manifest_hash"],
        "cumulative_request_audit_sha256": frozen[
            "cumulative_audit_hash"
        ],
        "original_status": "DAY_BLOCKED",
        "original_day_status": "DAY_BLOCKED",
        "original_blocker": manifest["original_blocker"],
        "original_blocker_detail": manifest.get("original_blocker_detail"),
        "original_blocked_at": manifest["original_blocked_at"],
        "resolution": "OFFLINE_SCHEMA_REMATERIALIZATION",
        "rematerialization": {
            "status": "PASSED",
            "mode": "OFFLINE_EVIDENCE_REUSE",
            "api_requests_added": 0,
            "schema_fix_commit": schema_fix_commit,
            "source_hash_verified": True,
        },
        "request_audit": request_audit,
    }
    target = (
        repo_root.resolve()
        / "reports"
        / "phase1_observation"
        / observation_date
    )
    _publish_rematerialized_day(rematerialized, target)
    return rematerialized


def _day_directories(observation_root: Path) -> list[Path]:
    if not observation_root.exists():
        return []
    return sorted(
        (
            path
            for path in observation_root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name)
        ),
        key=lambda path: path.name,
    )


def _verify_reused_source_manifest(
    day: Mapping[str, Any],
    reports_root: Path,
) -> None:
    manifest = day.get("source_evidence")
    _require(
        isinstance(manifest, list) and bool(manifest),
        "SOURCE_EVIDENCE_MANIFEST_MISSING",
        DAY1_DATE,
    )
    repo_root = reports_root.parent.resolve()
    for entry in manifest:
        _require(isinstance(entry, Mapping), "SOURCE_EVIDENCE_MANIFEST_INVALID")
        relative = entry.get("path")
        expected_size = entry.get("size")
        expected_hash = entry.get("sha256")
        _require(
            isinstance(relative, str)
            and isinstance(expected_size, int)
            and not isinstance(expected_size, bool)
            and isinstance(expected_hash, str),
            "SOURCE_EVIDENCE_MANIFEST_INVALID",
        )
        candidate = repo_root / relative
        try:
            candidate.resolve().relative_to(repo_root)
        except ValueError:
            _fail("SOURCE_PATH_OUTSIDE_REPOSITORY", relative)
        _require(
            candidate.is_file() and not candidate.is_symlink(),
            "SOURCE_FILE_MISSING",
            relative,
        )
        _require(
            candidate.stat().st_size == expected_size
            and sha256_file(candidate) == expected_hash,
            "SOURCE_EVIDENCE_HASH_MISMATCH",
            relative,
        )


def _validate_day_sequence(
    days: Sequence[Mapping[str, Any]],
    reports_root: Path,
) -> None:
    expected_day = 0
    for day in days:
        observation_date = day.get("observation_date")
        _require(
            isinstance(observation_date, str),
            "OBSERVATION_DATE_INVALID",
        )
        _parse_observation_date(observation_date)
        manifest = day.get("source_evidence")
        _require(
            isinstance(manifest, list),
            "SOURCE_EVIDENCE_MANIFEST_MISSING",
            observation_date,
        )
        _require(
            day.get("source_evidence_digest") == _canonical_sha256(manifest),
            "SOURCE_EVIDENCE_DIGEST_MISMATCH",
            observation_date,
        )
        _require(
            day.get("source_data_hashes_verified") is True,
            "SOURCE_HASH_VERIFICATION_MISSING",
            observation_date,
        )
        _require(
            day.get("idempotency_key") == _idempotency_key(observation_date),
            "OBSERVATION_IDEMPOTENCY_KEY_INVALID",
            observation_date,
        )
        if day.get("status") == "DAY_PASSED":
            expected_day += 1
            _require(
                day.get("observation_day") == expected_day,
                "OBSERVATION_DAY_SEQUENCE_INVALID",
                observation_date,
            )
        else:
            _require(
                day.get("observation_day") is None,
                "OBSERVATION_DAY_SEQUENCE_INVALID",
                observation_date,
            )
        if day.get("evidence_origin") == "phase1_2_live_reuse":
            rematerialization = day.get("rematerialization")
            _require(
                day.get("status") == "DAY_PASSED"
                and day.get("live_request_reexecuted") is False
                and day.get("source_live_request_count") == 14
                and day.get("source_request_count") == 14
                and day.get("new_api_request_count") == 0
                and day.get("original_status") == "DAY_BLOCKED"
                and day.get("resolution")
                == "OFFLINE_SCHEMA_REMATERIALIZATION"
                and isinstance(rematerialization, Mapping)
                and rematerialization.get("status") == "PASSED"
                and rematerialization.get("mode") == "OFFLINE_EVIDENCE_REUSE"
                and rematerialization.get("api_requests_added") == 0
                and rematerialization.get("source_hash_verified") is True,
                "REMATERIALIZED_DAY_INVALID",
                observation_date,
            )
        if day.get("evidence_origin") == "phase1_2_daily_live":
            timing = day.get("execution_timing")
            started_at = day.get("run_started_at")
            completed_at = day.get("run_completed_at")
            _require(
                timing in EXECUTION_TIMINGS
                and isinstance(started_at, str)
                and isinstance(completed_at, str)
                and day.get("counts_as_valid_day")
                is (day.get("status") == "DAY_PASSED"),
                "LIVE_EXECUTION_METADATA_INVALID",
                observation_date,
            )
            _require(
                _classify_execution_timing(observation_date, started_at)
                == timing,
                "LIVE_EXECUTION_TIMING_MISMATCH",
                observation_date,
            )
            _require(
                _parse_shanghai_now(completed_at)
                >= _parse_shanghai_now(started_at),
                "RUN_COMPLETED_BEFORE_START",
                completed_at,
            )
    if expected_day:
        day1 = next(
            (
                day
                for day in days
                if day.get("status") == "DAY_PASSED"
                and day.get("observation_day") == 1
            ),
            None,
        )
        _require(isinstance(day1, Mapping), "PHASE1_DAY1_REQUIRED")
        _require(
            day1.get("observation_date") == DAY1_DATE
            and day1.get("evidence_origin") == "phase1_1_reuse"
            and day1.get("live_request_reexecuted") is False
            and day1.get("source_request_count") == 14
            and day1.get("new_api_request_count") == 0,
            "DAY1_SOURCE_IMMUTABLE",
        )
        _verify_reused_source_manifest(day1, reports_root)


def _semantic_index(index: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in index.items()
        if key not in {"index_hash_before", "index_hash_after"}
    }


def rebuild_index(reports_root: Path) -> dict[str, Any]:
    reports = reports_root.resolve()
    observation_root = reports / "phase1_observation"
    observation_root.mkdir(parents=True, exist_ok=True)
    days: list[dict[str, Any]] = []
    for directory in _day_directories(observation_root):
        summary = _read_json(directory / "daily_summary.json")
        _require(
            summary.get("observation_date") == directory.name,
            "OBSERVATION_DATE_MISMATCH",
            directory.name,
        )
        status = summary.get("status")
        _require(status in DAY_STATUSES, "OBSERVATION_STATUS_INVALID", str(status))
        days.append(summary)
    _validate_day_sequence(days, reports)

    passed_days = [day for day in days if day["status"] == "DAY_PASSED"]
    blocked_days = [day for day in days if day["status"] == "DAY_BLOCKED"]
    metric_keys = (
        "stale_data",
        "material_ohlc_anomalies",
        "material_negative_amount",
        "duplicate_timestamps",
        "lunch_break_bars",
        "thirty_minute_ohlc_mismatches",
        "non_first_bucket_volume_amount_mismatches",
        "http_429",
        "sensitive_shape_hits",
    )
    metrics: dict[str, int | float] = {key: 0 for key in metric_keys}
    first_matches = 0
    first_checks = 0
    factor_passes = 0
    factor_checks = 0
    for day in days:
        day_metrics = day.get("metrics")
        _require(isinstance(day_metrics, Mapping), "OBSERVATION_METRICS_MISSING")
        for key in metric_keys:
            metrics[key] += _int_value(
                day_metrics.get(key),
                "OBSERVATION_METRICS_INVALID",
            )
        first_matches += _int_value(
            day_metrics.get("first_bucket_matches"),
            "OBSERVATION_METRICS_INVALID",
        )
        first_checks += _int_value(
            day_metrics.get("first_bucket_checks"),
            "OBSERVATION_METRICS_INVALID",
        )
        factor_passes += _int_value(
            day_metrics.get("ex_factor_passes"),
            "OBSERVATION_METRICS_INVALID",
        )
        factor_checks += _int_value(
            day_metrics.get("ex_factor_checks"),
            "OBSERVATION_METRICS_INVALID",
        )
    metrics["first_bucket_stable_rate"] = (
        first_matches / first_checks if first_checks else 0.0
    )
    metrics["ex_factor_pass_rate"] = (
        factor_passes / factor_checks if factor_checks else 0.0
    )
    blocking_metrics = sum(int(metrics[key]) for key in metric_keys)
    if blocked_days or blocking_metrics:
        final_status = "PHASE1_OBSERVATION_BLOCKED"
    elif (
        len(passed_days) >= 5
        and metrics["first_bucket_stable_rate"] == 1.0
        and metrics["ex_factor_pass_rate"] == 1.0
    ):
        final_status = "PHASE1_OBSERVATION_PASSED"
    else:
        final_status = "PHASE1_OBSERVATION_IN_PROGRESS"

    symbol_statuses: dict[str, str] = {}
    for symbol in SYMBOLS:
        statuses = [
            day.get("symbols", {}).get(symbol)
            for day in days
            if day["status"] != "NON_TRADING_DAY"
        ]
        symbol_statuses[symbol] = (
            "PASSED"
            if statuses and all(status == "PASSED" for status in statuses)
            else "BLOCKED"
            if any(status in {"BLOCKED", "DAY_BLOCKED"} for status in statuses)
            else "PENDING"
        )
    reused_observation_days = sum(
        day.get("evidence_origin") == "phase1_1_reuse" for day in passed_days
    )
    live_observation_days = sum(
        day.get("evidence_origin")
        in {
            "phase1_2_live",
            "phase1_2_live_reuse",
            "phase1_2_daily_live",
        }
        for day in passed_days
    )
    semantic_index = {
        "contract": "tickflow_phase1_2_observation_index_v1",
        "final_status": final_status,
        "valid_trading_days": len(passed_days),
        "required_trading_days": 5,
        "remaining_actual_trading_days": max(5 - len(passed_days), 0),
        "reused_observation_days": reused_observation_days,
        "phase1_2_live_observation_days": live_observation_days,
        "valid_observation_days": len(passed_days),
        "remaining_observation_days": max(5 - len(passed_days), 0),
        "symbols": symbol_statuses,
        "metrics": metrics,
        "days": [
            {
                "observation_date": day["observation_date"],
                "observation_day": day.get("observation_day"),
                "status": day["status"],
                "evidence_origin": day.get("evidence_origin"),
                "execution_timing": day.get("execution_timing"),
                "run_started_at": day.get("run_started_at"),
                "run_completed_at": day.get("run_completed_at"),
                "counts_as_valid_day": day.get("counts_as_valid_day"),
                "live_request_reexecuted": day.get("live_request_reexecuted"),
                "source_request_count": day.get("source_request_count"),
                "source_live_request_count": day.get(
                    "source_live_request_count"
                ),
                "new_api_request_count": day.get("new_api_request_count"),
                "source_report": day.get("source_report"),
                "source_request_audit": day.get("source_request_audit"),
                "source_evidence_dir": day.get("source_evidence_dir"),
                "source_data_hashes_verified": day.get(
                    "source_data_hashes_verified"
                ),
                "source_evidence_digest": day.get("source_evidence_digest"),
                "vendor_pending": day.get("vendor_pending"),
                "idempotency_key": day.get("idempotency_key"),
                "original_status": day.get("original_status"),
                "original_day_status": day.get("original_day_status"),
                "original_blocker": day.get("original_blocker"),
                "original_blocked_at": day.get("original_blocked_at"),
                "resolution": day.get("resolution"),
                "rematerialization": day.get("rematerialization"),
            }
            for day in days
        ],
        "vendor_pending": list(VENDOR_PENDING),
        "boundaries": {
            "ai": "NOT_CONFIGURED",
            "paper_trading": "NOT_STARTED",
            "cloud_redeployed": False,
            "integrated_gold_enabled": False,
            "integrated_gold_external_send_count": 0,
            "real_key": "NOT_EXPOSED",
        },
    }
    _require(final_status in PHASE_STATUSES, "OBSERVATION_PHASE_STATUS_INVALID")
    index_path = observation_root / "observation_index.json"
    previous_hash = "0" * 64
    existing_index: dict[str, Any] | None = None
    if index_path.exists():
        previous_hash = sha256_file(index_path)
        existing_index = _read_json(index_path)
        existing_hash = existing_index.get("index_hash_after")
        if existing_hash is not None:
            _require(
                existing_hash == _canonical_sha256(_semantic_index(existing_index)),
                "OBSERVATION_INDEX_HASH_INVALID",
            )
    if (
        existing_index is not None
        and _semantic_index(existing_index) == semantic_index
    ):
        index = existing_index
    else:
        index = {
            **semantic_index,
            "index_hash_before": previous_hash,
            "index_hash_after": _canonical_sha256(semantic_index),
        }
        _atomic_write(
            index_path,
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    status_path = reports / "tickflow_phase1_observation_status.md"
    final_path = reports / "tickflow_phase1_observation_final.md"
    if final_status == "PHASE1_OBSERVATION_IN_PROGRESS":
        report_path = status_path
        stale_report_path = final_path
    else:
        report_path = final_path
        stale_report_path = status_path
    _atomic_write(report_path, render_observation_report(index))
    if stale_report_path.exists():
        _require(
            stale_report_path.is_file() and not stale_report_path.is_symlink(),
            "OBSERVATION_REPORT_PATH_INVALID",
            str(stale_report_path),
        )
        stale_report_path.unlink()
        _fsync_directory(reports)
    return index


def _percentage(value: Any) -> str:
    return f"{_float_value(value, 'RATE_INVALID') * 100:.2f}%"


def render_observation_report(index: Mapping[str, Any]) -> str:
    metrics = _nested(index, "metrics", code="OBSERVATION_METRICS_MISSING")
    symbols = _nested(index, "symbols", code="OBSERVATION_SYMBOLS_MISSING")
    lines = [
        "# TickFlow Phase 1.2 三票分钟数据连续观察",
        "",
        "## 当前状态",
        "",
        "```text",
        str(index["final_status"]),
        "```",
        "",
        (
            f"有效交易日：{index['valid_observation_days']}/"
            f"{index['required_trading_days']}，剩余 "
            f"{index['remaining_observation_days']} 日"
        ),
        (
            f"复用观察日：{index['reused_observation_days']}；"
            f"Phase 1.2 新增 live 观察日："
            f"{index['phase1_2_live_observation_days']}"
        ),
        "",
        "当前只完成真实证据已经落盘的观察日；未预生成未来交易日结果。",
        "",
        "## 观察日证据",
        "",
        "| 日期 | Day | 状态 | 执行时段 | 证据来源 | live 重跑 | 源请求 | 新增 API 请求 | 哈希复核 |",
        "|---|---:|---|---|---|---|---:|---:|---|",
    ]
    for day in index.get("days", []):
        lines.append(
            "| "
            f"{day.get('observation_date')} | "
            f"{day.get('observation_day') or '-'} | "
            f"{day.get('status')} | "
            f"{day.get('execution_timing') or '-'} | "
            f"{day.get('evidence_origin') or '-'} | "
            f"{'YES' if day.get('live_request_reexecuted') else 'NO'} | "
            f"{day.get('source_request_count', '-')} | "
            f"{day.get('new_api_request_count', '-')} | "
            f"{'PASSED' if day.get('source_data_hashes_verified') else 'N/A'} |"
        )
    for day in index.get("days", []):
        if day.get("evidence_origin") == "phase1_1_reuse":
            lines.extend(
                [
                    "",
                    (
                        f"Day {day.get('observation_day')} 新增 API 请求："
                        f"{day.get('new_api_request_count')}"
                        "；该日复用 Phase 1.1 正式 live 证据。"
                    ),
                ]
            )
        if day.get("evidence_origin") == "phase1_2_live_reuse":
            lines.extend(
                [
                    "",
                    (
                        f"Day {day.get('observation_day')} 保留原 "
                        f"{day.get('original_status')} 审计，并通过 "
                        "OFFLINE_SCHEMA_REMATERIALIZATION 复用唯一一次 "
                        "live 证据；新增 API 请求为 0。"
                    ),
                ]
            )
        if day.get("evidence_origin") == "phase1_2_daily_live":
            lines.extend(
                [
                    "",
                    (
                        f"Day {day.get('observation_day')} 于 "
                        f"{day.get('run_started_at')} 启动，"
                        f"{day.get('run_completed_at')} 完成；执行时段为 "
                        f"{day.get('execution_timing')}，"
                        "行情与安全合同仍按原阈值验收。"
                    ),
                ]
            )
    lines.extend(
        [
            "",
        "## 三票状态",
        "",
        "| 标的 | 状态 |",
        "|---|---|",
        ]
    )
    for symbol in SYMBOLS:
        lines.append(f"| {SYMBOL_NAMES[symbol]} `{symbol}` | {symbols[symbol]} |")
    lines.extend(
        [
            "",
            "## 聚合合同",
            "",
            f"- 数据过期：{metrics['stale_data']}",
            f"- material OHLC 异常：{metrics['material_ohlc_anomalies']}",
            f"- material negative amount：{metrics['material_negative_amount']}",
            f"- 重复时间戳：{metrics['duplicate_timestamps']}",
            f"- 午休伪 K 线：{metrics['lunch_break_bars']}",
            f"- 30m OHLC 不一致：{metrics['thirty_minute_ohlc_mismatches']}",
            (
                "- 非首桶量额不一致："
                f"{metrics['non_first_bucket_volume_amount_mismatches']}"
            ),
            f"- 09:30 首桶稳定率：{_percentage(metrics['first_bucket_stable_rate'])}",
            f"- 除权因子通过率：{_percentage(metrics['ex_factor_pass_rate'])}",
            f"- HTTP 429：{metrics['http_429']}",
            f"- 敏感形态命中：{metrics['sensitive_shape_hits']}",
            "",
            "## 边界",
            "",
            "AI：NOT_CONFIGURED",
            "",
            "Paper Trading：NOT_STARTED",
            "",
            "云端重新部署：NO",
            "",
            "Integrated Gold：DISABLED / external_send_count=0",
            "",
            "真实 Key：NOT_EXPOSED",
            "",
            "供应商仍待确认：`intraday_batch` 权限、30m 首桶是否正式包含 "
            "09:30、volume 单位、amount 单位。这些待确认项不改变已验证的 "
            "有效观察日计数。",
            "",
        ]
    )
    return "\n".join(lines)


def render_final_report(index: Mapping[str, Any]) -> str:
    """Backward-compatible renderer name for existing offline callers."""

    return render_observation_report(index)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--reuse-phase11-day1",
        action="store_true",
        help="Materialize 2026-07-27 from existing Phase 1.1 evidence.",
    )
    mode.add_argument(
        "--materialize-live",
        type=Path,
        help="Materialize one sanitized Phase 1.1-compatible live result.",
    )
    mode.add_argument(
        "--preflight-live-date",
        action="store_true",
        help="Evaluate offline date and idempotency gates.",
    )
    mode.add_argument(
        "--record-non-trading-day",
        action="store_true",
        help="Record one calendar-confirmed non-trading day without live requests.",
    )
    mode.add_argument(
        "--summarize",
        action="store_true",
        help="Rebuild the observation index and current status/final report.",
    )
    mode.add_argument(
        "--observation-root",
        type=Path,
        help="Rebuild one observation root offline (compatibility mode).",
    )
    mode.add_argument(
        "--rematerialize-blocked-live",
        type=Path,
        help="Reuse one frozen sanitized live result without any API call.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
    )
    parser.add_argument(
        "--observation-date",
        help="Observation date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--now",
        help="Current Asia/Shanghai ISO-8601 time for a live date gate.",
    )
    parser.add_argument(
        "--run-started-at",
        help="Formal live start time in Asia/Shanghai.",
    )
    parser.add_argument(
        "--run-completed-at",
        help="Formal live completion time in Asia/Shanghai.",
    )
    parser.add_argument(
        "--symbol-set-hash",
        help="Fixed three-symbol contract hash supplied by the runner.",
    )
    parser.add_argument(
        "--evidence-manifest",
        type=Path,
        help="Frozen original DAY_BLOCKED evidence manifest.",
    )
    parser.add_argument(
        "--schema-fix-commit",
        help="Full commit SHA implementing boundary schema version 1.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.observation_root is not None:
            observation_root = args.observation_root.resolve()
            _require(
                observation_root.name == "phase1_observation"
                and observation_root.is_dir()
                and not observation_root.is_symlink(),
                "OBSERVATION_ROOT_INVALID",
                str(observation_root),
            )
            index = rebuild_index(observation_root.parent)
            print(json.dumps(index, ensure_ascii=False, sort_keys=True))
            return 0
        if args.summarize:
            index = rebuild_index(args.repo_root / "reports")
            print(json.dumps(index, ensure_ascii=False, sort_keys=True))
            return 0
        if args.rematerialize_blocked_live is not None:
            _require(
                isinstance(args.observation_date, str),
                "OBSERVATION_DATE_REQUIRED",
            )
            _require(
                isinstance(args.evidence_manifest, Path),
                "FROZEN_MANIFEST_REQUIRED",
            )
            _require(
                isinstance(args.schema_fix_commit, str),
                "SCHEMA_FIX_COMMIT_REQUIRED",
            )
            day = rematerialize_blocked_live_day(
                result_path=args.rematerialize_blocked_live,
                repo_root=args.repo_root,
                observation_date=args.observation_date,
                evidence_manifest_path=args.evidence_manifest,
                schema_fix_commit=args.schema_fix_commit,
            )
            index = rebuild_index(args.repo_root / "reports")
            print(
                json.dumps(
                    {
                        "day_status": day["status"],
                        "resolution": day["resolution"],
                        "final_status": index["final_status"],
                        "valid_trading_days": index["valid_trading_days"],
                        "remaining_actual_trading_days": index[
                            "remaining_actual_trading_days"
                        ],
                        "new_api_request_count": day[
                            "new_api_request_count"
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.preflight_live_date:
            _require(
                isinstance(args.observation_date, str),
                "OBSERVATION_DATE_REQUIRED",
            )
            _require(isinstance(args.now, str), "OBSERVATION_NOW_REQUIRED")
            _require(
                args.symbol_set_hash == SYMBOL_SET_HASH,
                "SYMBOL_SET_HASH_MISMATCH",
            )
            decision = evaluate_live_run_gate(
                args.repo_root / "reports",
                observation_date=args.observation_date,
                now_iso=args.now,
            )
            print(json.dumps(decision, ensure_ascii=False, sort_keys=True))
            return 0
        if args.reuse_phase11_day1:
            day = build_reused_day(args.repo_root, DAY1_DATE)
        elif args.record_non_trading_day:
            _require(
                isinstance(args.observation_date, str),
                "OBSERVATION_DATE_REQUIRED",
            )
            _require(isinstance(args.now, str), "OBSERVATION_NOW_REQUIRED")
            _require(
                args.symbol_set_hash == SYMBOL_SET_HASH,
                "SYMBOL_SET_HASH_MISMATCH",
            )
            decision = evaluate_live_run_gate(
                args.repo_root / "reports",
                observation_date=args.observation_date,
                now_iso=args.now,
            )
            _require(
                decision.get("decision") == "NON_TRADING_DAY",
                "EXPECTED_NON_TRADING_DAY",
            )
            day = build_non_trading_day(args.observation_date)
        else:
            _require(
                isinstance(args.observation_date, str),
                "OBSERVATION_DATE_REQUIRED",
            )
            _require(isinstance(args.now, str), "OBSERVATION_NOW_REQUIRED")
            _require(
                args.symbol_set_hash == SYMBOL_SET_HASH,
                "SYMBOL_SET_HASH_MISMATCH",
            )
            run_started_at = args.run_started_at or args.now
            run_completed_at = args.run_completed_at or run_started_at
            decision = evaluate_live_run_gate(
                args.repo_root / "reports",
                observation_date=args.observation_date,
                now_iso=run_started_at,
            )
            _require(
                decision.get("decision") == "LIVE_ALLOWED",
                "LIVE_DATE_NOT_ALLOWED",
            )
            day = build_live_day(
                args.materialize_live,
                args.repo_root,
                args.observation_date,
            )
            day = apply_live_execution_metadata(
                day,
                observation_date=args.observation_date,
                run_started_at=run_started_at,
                run_completed_at=run_completed_at,
            )
        materialize_day(day, args.repo_root / "reports")
        index = rebuild_index(args.repo_root / "reports")
        print(
            json.dumps(
                {
                    "day_status": day["status"],
                    "final_status": index["final_status"],
                    "valid_trading_days": index["valid_trading_days"],
                    "remaining_actual_trading_days": index[
                        "remaining_actual_trading_days"
                    ],
                    "new_api_request_count": day["new_api_request_count"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if day["status"] in {"DAY_PASSED", "NON_TRADING_DAY"} else 2
    except EvidenceError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
