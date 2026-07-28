#!/usr/bin/env python3
# ruff: noqa: RUF001
"""Run the bounded TickFlow Pro Phase 1 data-contract validation.

The live mode is intended to be streamed into the target container:

    docker exec -i -w /app TickFlow_Stock_Panel /app/.venv/bin/python - --live

It loads the already-configured TickFlow key through the application secret
store, keeps it in process memory, and emits only sanitized contract evidence.
The SDK is configured with zero retries so the first HTTP 429 aborts the
pipeline.

The materialize mode runs locally against that sanitized JSON and writes the
requested reports. This script is an operator artifact; it is not imported by
the application runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as daytime
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
SYMBOLS = ("000403.SZ", "600489.SH", "300059.SZ")
EXPECTED_NAMES = {
    "000403.SZ": "派林生物",
    "600489.SH": "中金黄金",
    "300059.SZ": "东方财富",
}
PIPELINE = "phase1_1_minute_contract_closeout"
SDK_QFQ_ADJUST = "forward"
REPORT_SOURCE = "tickflow-pro-python-sdk/0.1.24"
PRICE_ABS_TOLERANCE = 0.011
PRICE_REL_TOLERANCE = 5e-4
SUM_REL_TOLERANCE = 1e-8
OHLC_ABS_EPSILON = 1e-10
AMOUNT_ZERO_EPSILON = 1e-6
PACED_WAIT_SECONDS = 0.25
PRIOR_LOCAL_FAILURE_REQUEST_COUNT = 12
MIN_CONTRACT_DATE = date(1990, 1, 1)
MAX_CONTRACT_FUTURE_DAYS = 366
OHLC_VIOLATION_FIELDS = (
    "high_vs_open_violation",
    "high_vs_close_violation",
    "high_vs_low_violation",
    "low_vs_open_violation",
    "low_vs_close_violation",
    "low_vs_high_violation",
    "open_outside_range_violation",
    "close_outside_range_violation",
)


class RateLimitAbortError(RuntimeError):
    """Raised after the first 429 so no later capability can run."""


@dataclass
class CallResult:
    value: Any
    succeeded: bool


def _now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def _iso_now() -> str:
    return _now_shanghai().isoformat(timespec="seconds")


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _as_float(value: Any) -> float | None:
    if not _is_number(value):
        return None
    return float(value)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _numeric_distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
        }
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
    }


def _ohlc_violation_components(row: dict[str, Any]) -> dict[str, float] | None:
    values = {
        field: _as_float(row.get(field))
        for field in ("open", "high", "low", "close")
    }
    if any(value is None for value in values.values()):
        return None
    open_price = float(values["open"])
    high = float(values["high"])
    low = float(values["low"])
    close = float(values["close"])
    return {
        "high_vs_open_violation": max(0.0, open_price - high),
        "high_vs_close_violation": max(0.0, close - high),
        "high_vs_low_violation": max(0.0, low - high),
        "low_vs_open_violation": max(0.0, low - open_price),
        "low_vs_close_violation": max(0.0, low - close),
        "low_vs_high_violation": max(0.0, low - high),
        "open_outside_range_violation": max(
            0.0,
            low - open_price,
            open_price - high,
        ),
        "close_outside_range_violation": max(
            0.0,
            low - close,
            close - high,
        ),
    }


def _at_least(value: float, threshold: float) -> bool:
    return value > threshold or math.isclose(
        value,
        threshold,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def ohlc_violation_distribution(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    source_hash = _canonical_hash(rows)
    by_constraint_values: dict[str, list[float]] = {
        field: [] for field in OHLC_VIOLATION_FIELDS
    }
    row_violations: list[float] = []
    invalid_numeric_rows = 0
    for row in rows:
        components = _ohlc_violation_components(row)
        if components is None:
            invalid_numeric_rows += 1
            continue
        for field, value in components.items():
            if value > 0:
                by_constraint_values[field].append(value)
        row_max = max(components.values())
        if row_max > 0:
            row_violations.append(row_max)

    maximum = max(row_violations, default=0.0)
    if _at_least(maximum, 0.01):
        classification = "MATERIAL_OHLC_ANOMALY"
    elif _at_least(maximum, 0.001):
        classification = "POTENTIAL_DATA_ANOMALY"
    elif maximum > OHLC_ABS_EPSILON:
        classification = "FLOAT_PRECISION_WARNING"
    elif maximum > 0:
        classification = "NUMERIC_EPSILON_ONLY"
    else:
        classification = "NO_OHLC_VIOLATION"

    return {
        "ohlc_abs_epsilon": OHLC_ABS_EPSILON,
        "source_row_count": len(rows),
        "valid_numeric_row_count": len(rows) - invalid_numeric_rows,
        "invalid_numeric_row_count": invalid_numeric_rows,
        "anomaly_row_count": len(row_violations),
        "row_violation_stats": _numeric_distribution(row_violations),
        "by_constraint": {
            field: _numeric_distribution(by_constraint_values[field])
            for field in OHLC_VIOLATION_FIELDS
        },
        "threshold_counts": {
            "gt_1e_12": sum(value > 1e-12 for value in row_violations),
            "gt_1e_10": sum(value > 1e-10 for value in row_violations),
            "gt_1e_8": sum(value > 1e-8 for value in row_violations),
            "gt_1e_6": sum(value > 1e-6 for value in row_violations),
            "gt_0_001": sum(value > 0.001 for value in row_violations),
            "ge_0_001": sum(_at_least(value, 0.001) for value in row_violations),
            "ge_0_01": sum(_at_least(value, 0.01) for value in row_violations),
        },
        "classification": classification,
        "contract_passed": (
            invalid_numeric_rows == 0
            and all(value <= OHLC_ABS_EPSILON for value in row_violations)
        ),
        "raw_values_modified": False,
        "source_hash_before": source_hash,
        "source_hash_after": _canonical_hash(rows),
    }


def classify_amount_tolerance(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    source_hash = _canonical_hash(rows)
    negative_values: list[float] = []
    epsilon_warnings: list[float] = []
    failed: list[float] = []
    invalid_numeric_count = 0
    for row in rows:
        amount = _as_float(row.get("amount"))
        if amount is None:
            invalid_numeric_count += 1
            continue
        if amount >= 0:
            continue
        negative_values.append(amount)
        if amount >= -AMOUNT_ZERO_EPSILON:
            epsilon_warnings.append(amount)
        else:
            failed.append(amount)
    if failed:
        classification = "NEGATIVE_AMOUNT_FAILED"
    elif epsilon_warnings:
        classification = "NUMERIC_EPSILON_WARNING"
    else:
        classification = "VALID"
    return {
        "amount_zero_epsilon": AMOUNT_ZERO_EPSILON,
        "source_row_count": len(rows),
        "invalid_numeric_count": invalid_numeric_count,
        "strict_negative_count": len(negative_values),
        "epsilon_warning_count": len(epsilon_warnings),
        "failed_count": len(failed),
        "negative_values": negative_values,
        "minimum_amount": min(negative_values) if negative_values else None,
        "maximum_absolute_negative_amount": (
            max(abs(value) for value in negative_values)
            if negative_values
            else 0.0
        ),
        "classification": classification,
        "contract_passed": invalid_numeric_count == 0 and not failed,
        "raw_values_modified": False,
        "source_hash_before": source_hash,
        "source_hash_after": _canonical_hash(rows),
    }


def _aggregate_first_bucket(
    rows: list[dict[str, Any]],
    *,
    trade_date: str,
    include_09_30: bool,
) -> dict[str, Any]:
    start_clock = daytime(9, 30) if include_09_30 else daytime(9, 31)
    end_clock = daytime(10, 0)
    selected = []
    for row in rows:
        parsed = _dt_from_ms(row.get("timestamp"))
        if (
            parsed
            and parsed.date().isoformat() == trade_date
            and start_clock <= parsed.time().replace(tzinfo=None) <= end_clock
        ):
            selected.append(row)
    selected.sort(key=lambda row: row["timestamp"])
    if not selected:
        return {
            "minute_count": 0,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": None,
            "amount": None,
        }
    numeric = all(
        _is_number(row.get(field))
        for row in selected
        for field in ("open", "high", "low", "close", "volume", "amount")
    )
    if not numeric:
        return {
            "minute_count": len(selected),
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": None,
            "amount": None,
        }
    return {
        "minute_count": len(selected),
        "open": selected[0]["open"],
        "high": max(float(row["high"]) for row in selected),
        "low": min(float(row["low"]) for row in selected),
        "close": selected[-1]["close"],
        "volume": sum(float(row["volume"]) for row in selected),
        "amount": sum(float(row["amount"]) for row in selected),
    }


def _compare_first_bucket(
    local: dict[str, Any],
    vendor: dict[str, Any] | None,
) -> dict[str, Any]:
    vendor = vendor or {}
    result = {
        "minute_count": local["minute_count"],
        "local_open": local["open"],
        "local_high": local["high"],
        "local_low": local["low"],
        "local_close": local["close"],
        "local_volume": local["volume"],
        "local_amount": local["amount"],
        "vendor_open": vendor.get("open"),
        "vendor_high": vendor.get("high"),
        "vendor_low": vendor.get("low"),
        "vendor_close": vendor.get("close"),
        "vendor_volume": vendor.get("volume"),
        "vendor_amount": vendor.get("amount"),
    }
    for field in ("open", "high", "low", "close"):
        result[f"{field}_match"] = _value_close(
            local.get(field),
            vendor.get(field),
            abs_tolerance=PRICE_ABS_TOLERANCE,
            rel_tolerance=PRICE_REL_TOLERANCE,
        )
    local_volume = _as_float(local.get("volume"))
    vendor_volume = _as_float(vendor.get("volume"))
    local_amount = _as_float(local.get("amount"))
    vendor_amount = _as_float(vendor.get("amount"))
    result["volume_diff"] = (
        vendor_volume - local_volume
        if vendor_volume is not None and local_volume is not None
        else None
    )
    result["amount_diff"] = (
        vendor_amount - local_amount
        if vendor_amount is not None and local_amount is not None
        else None
    )
    result["volume_diff_ratio"] = (
        result["volume_diff"] / local_volume
        if result["volume_diff"] is not None and local_volume
        else None
    )
    result["amount_diff_ratio"] = (
        result["amount_diff"] / local_amount
        if result["amount_diff"] is not None and local_amount
        else None
    )
    result["ohlc_match"] = all(
        result[f"{field}_match"] for field in ("open", "high", "low", "close")
    )
    result["volume_match"] = (
        result["volume_diff"] is not None
        and math.isclose(
            result["volume_diff"],
            0.0,
            rel_tol=SUM_REL_TOLERANCE,
            abs_tol=1e-8,
        )
    )
    result["amount_match"] = (
        result["amount_diff"] is not None
        and math.isclose(
            result["amount_diff"],
            0.0,
            rel_tol=SUM_REL_TOLERANCE,
            abs_tol=AMOUNT_ZERO_EPSILON,
        )
    )
    result["all_fields_match"] = (
        result["ohlc_match"]
        and result["volume_match"]
        and result["amount_match"]
    )
    return result


def build_dual_convention_validation(
    minute_rows_by_symbol: dict[str, list[dict[str, Any]]],
    vendor_rows_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    trade_date: str,
) -> dict[str, Any]:
    symbols: dict[str, Any] = {}
    candidate_matches = 0
    target_symbols = [
        symbol
        for symbol in SYMBOLS
        if symbol in minute_rows_by_symbol or symbol in vendor_rows_by_symbol
    ]
    for symbol in target_symbols:
        minute_rows = minute_rows_by_symbol.get(symbol, [])
        vendor_rows = sorted(
            (
                row
                for row in vendor_rows_by_symbol.get(symbol, [])
                if _date_from_ms(row.get("timestamp")) == trade_date
            ),
            key=lambda row: row["timestamp"],
        )
        vendor_first = vendor_rows[0] if vendor_rows else None
        continuous = _compare_first_bucket(
            _aggregate_first_bucket(
                minute_rows,
                trade_date=trade_date,
                include_09_30=False,
            ),
            vendor_first,
        )
        candidate = _compare_first_bucket(
            _aggregate_first_bucket(
                minute_rows,
                trade_date=trade_date,
                include_09_30=True,
            ),
            vendor_first,
        )
        if candidate["all_fields_match"]:
            candidate_matches += 1
        symbols[symbol] = {
            "continuous_09_31_to_10_00": continuous,
            "vendor_candidate_09_30_to_10_00": candidate,
            "raw_minute_hash": _canonical_hash(minute_rows),
            "raw_vendor_hash": _canonical_hash(vendor_rows),
            "raw_values_modified": False,
        }
    if target_symbols and candidate_matches == len(target_symbols):
        verdict = "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED"
    elif candidate_matches:
        verdict = "FIRST_BUCKET_CONVENTION_PARTIAL"
    else:
        verdict = "FIRST_BUCKET_CONVENTION_UNRESOLVED"
    return {
        "trade_date": trade_date,
        "continuous_definition": "09:31-10:00",
        "vendor_candidate_definition": "09:30-10:00",
        "symbols": symbols,
        "symbol_count": len(target_symbols),
        "candidate_match_symbol_count": candidate_matches,
        "verdict": verdict,
        "interpretation": (
            "LIKELY_VENDOR_FIRST_BUCKET_INCLUDES_09_30"
            if verdict == "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED"
            else "VENDOR_FIRST_BUCKET_CONVENTION_UNRESOLVED"
        ),
        "vendor_contract_confirmed": False,
        "timestamps_shifted": False,
        "missing_minutes_filled": False,
        "raw_values_modified": False,
    }


def _dt_from_ms(value: Any) -> datetime | None:
    if not _is_number(value):
        return None
    timestamp = float(value)
    if timestamp < 10_000_000_000:
        return None
    try:
        parsed = datetime.fromtimestamp(timestamp / 1000, UTC).astimezone(SHANGHAI)
    except (OverflowError, OSError, ValueError):
        return None
    maximum = _now_shanghai().date() + timedelta(days=MAX_CONTRACT_FUTURE_DAYS)
    return parsed if MIN_CONTRACT_DATE <= parsed.date() <= maximum else None


def _date_from_ms(value: Any) -> str | None:
    parsed = _dt_from_ms(value)
    return parsed.date().isoformat() if parsed else None


def _normalize_trade_date(value: Any) -> str | None:
    normalized: date | None = None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=SHANGHAI)
        normalized = parsed.astimezone(SHANGHAI).date()
    elif isinstance(value, date):
        normalized = value
    elif _is_number(value):
        numeric = int(float(value))
        text = str(numeric)
        if len(text) == 8:
            try:
                normalized = datetime.strptime(text, "%Y%m%d").date()
            except ValueError:
                return None
        elif numeric >= 10_000_000_000:
            parsed = _dt_from_ms(numeric)
            normalized = parsed.date() if parsed else None
        elif numeric >= 1_000_000_000:
            try:
                normalized = (
                    datetime.fromtimestamp(numeric, UTC)
                    .astimezone(SHANGHAI)
                    .date()
                )
            except (OverflowError, OSError, ValueError):
                return None
        else:
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if len(text) == 8 and text.isdigit():
            try:
                normalized = datetime.strptime(text, "%Y%m%d").date()
            except ValueError:
                return None
        else:
            try:
                normalized = date.fromisoformat(text[:10])
            except ValueError:
                try:
                    normalized = (
                        datetime.fromisoformat(text.replace("Z", "+00:00"))
                        .astimezone(SHANGHAI)
                        .date()
                    )
                except ValueError:
                    return None
    if normalized is None:
        return None
    maximum = _now_shanghai().date() + timedelta(days=MAX_CONTRACT_FUTURE_DAYS)
    return (
        normalized.isoformat()
        if MIN_CONTRACT_DATE <= normalized <= maximum
        else None
    )


def _safe_exception(exc: BaseException) -> dict[str, Any]:
    status_code = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    return {
        "type": type(exc).__name__,
        "status_code": status_code if isinstance(status_code, int) else None,
        "code": str(code)[:64] if code is not None else None,
        "message_recorded": False,
    }


def _regular_file_metadata(path: Path) -> dict[str, Any]:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return {
            "present": False,
            "regular_file": False,
            "symlink": False,
            "owner_uid": None,
            "owner_gid": None,
            "mode": None,
        }
    return {
        "present": True,
        "regular_file": path.is_file() and not path.is_symlink(),
        "symlink": path.is_symlink(),
        "owner_uid": stat_result.st_uid,
        "owner_gid": stat_result.st_gid,
        "mode": f"{stat_result.st_mode & 0o777:03o}",
    }


class RequestAuditor:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.request_count = 0
        self._first = True
        self.rate_limit_detected = False

    def call(
        self,
        capability: str,
        symbol_count: int,
        operation: Callable[[], Any],
    ) -> CallResult:
        if self.rate_limit_detected:
            raise RateLimitAbortError("pipeline already stopped after HTTP 429")

        pacing_wait_ms = 0
        if not self._first:
            time.sleep(PACED_WAIT_SECONDS)
            pacing_wait_ms = round(PACED_WAIT_SECONDS * 1000)
        self._first = False

        started = time.perf_counter()
        fetched_at = _iso_now()
        self.request_count += 1
        try:
            value = operation()
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            error = _safe_exception(exc)
            status_code = error["status_code"]
            is_429 = status_code == 429 or type(exc).__name__ == "RateLimitError"
            self.records.append(
                {
                    "pipeline": PIPELINE,
                    "capability": capability,
                    "symbol_count": symbol_count,
                    "duration_ms": duration_ms,
                    "status": "HTTP_429_STOPPED" if is_429 else "FAILED",
                    "retry_count": 0,
                    "rate_limit_wait_ms": 0,
                    "pacing_wait_ms": pacing_wait_ms,
                    "fetched_at": fetched_at,
                    "error": error,
                }
            )
            if is_429:
                self.rate_limit_detected = True
                raise RateLimitAbortError("first HTTP 429 stopped the pipeline") from exc
            return CallResult(None, False)

        duration_ms = round((time.perf_counter() - started) * 1000)
        self.records.append(
            {
                "pipeline": PIPELINE,
                "capability": capability,
                "symbol_count": symbol_count,
                "duration_ms": duration_ms,
                "status": "PASSED",
                "retry_count": 0,
                "rate_limit_wait_ms": 0,
                "pacing_wait_ms": pacing_wait_ms,
                "fetched_at": fetched_at,
            }
        )
        return CallResult(value, True)

    def fetched_at(self, capability: str) -> str | None:
        for record in self.records:
            if record["capability"] == capability:
                return str(record["fetched_at"])
        return None


def _compact_rows(data: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = ("timestamp", "open", "high", "low", "close", "volume", "amount")
    if not isinstance(data, dict):
        return [], {
            "input_is_mapping": False,
            "missing_columns": list(required),
            "column_lengths": {},
            "equal_column_lengths": False,
        }

    missing = [column for column in required if column not in data]
    lengths: dict[str, int | None] = {}
    for column in required:
        values = data.get(column)
        lengths[column] = len(values) if isinstance(values, list) else None
    numeric_lengths = [length for length in lengths.values() if length is not None]
    equal_lengths = len(numeric_lengths) == len(required) and len(set(numeric_lengths)) == 1
    if missing or not equal_lengths:
        return [], {
            "input_is_mapping": True,
            "missing_columns": missing,
            "column_lengths": lengths,
            "equal_column_lengths": equal_lengths,
        }

    row_count = numeric_lengths[0] if numeric_lengths else 0
    rows = [{column: data[column][index] for column in required} for index in range(row_count)]
    return rows, {
        "input_is_mapping": True,
        "missing_columns": [],
        "column_lengths": lengths,
        "equal_column_lengths": True,
    }


def _row_signature(rows: list[dict[str, Any]]) -> str:
    return _canonical_hash(rows)


def _session_membership(parsed: datetime) -> bool:
    clock = parsed.time().replace(tzinfo=None)
    morning = daytime(9, 30) <= clock <= daytime(11, 30)
    afternoon = daytime(13, 0) <= clock <= daytime(15, 0)
    return morning or afternoon


def _in_lunch_break(parsed: datetime) -> bool:
    clock = parsed.time().replace(tzinfo=None)
    return daytime(11, 30) < clock < daytime(13, 0)


def _validate_kline_rows(
    rows: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    intraday: bool,
    now: datetime,
) -> dict[str, Any]:
    timestamps = [row.get("timestamp") for row in rows]
    numeric_timestamps = [int(value) for value in timestamps if _is_number(value)]
    parsed_timestamps = [_dt_from_ms(value) for value in timestamps]
    valid_parsed = [value for value in parsed_timestamps if value is not None]

    invalid_numeric_rows = 0
    strict_invalid_ohlc_rows = 0
    invalid_ohlc_rows = 0
    ohlc_epsilon_warning_rows = 0
    negative_volume_rows = 0
    strict_negative_amount_rows = 0
    negative_amount_rows = 0
    amount_epsilon_warning_rows = 0
    future_rows = 0
    lunch_rows = 0
    off_session_rows = 0
    for row, parsed in zip(rows, parsed_timestamps, strict=True):
        values = {
            key: _as_float(row.get(key))
            for key in ("open", "high", "low", "close", "volume", "amount")
        }
        if any(value is None for value in values.values()) or parsed is None:
            invalid_numeric_rows += 1
            continue
        assert all(value is not None for value in values.values())
        violations = _ohlc_violation_components(row)
        assert violations is not None
        maximum_violation = max(violations.values())
        if maximum_violation > 0:
            strict_invalid_ohlc_rows += 1
            if maximum_violation <= OHLC_ABS_EPSILON:
                ohlc_epsilon_warning_rows += 1
            else:
                invalid_ohlc_rows += 1
        if float(values["volume"]) < 0:
            negative_volume_rows += 1
        amount = float(values["amount"])
        if amount < 0:
            strict_negative_amount_rows += 1
            if amount >= -AMOUNT_ZERO_EPSILON:
                amount_epsilon_warning_rows += 1
            else:
                negative_amount_rows += 1
        if parsed > now + timedelta(minutes=5):
            future_rows += 1
        if intraday:
            if _in_lunch_break(parsed):
                lunch_rows += 1
            if not _session_membership(parsed):
                off_session_rows += 1

    unique_timestamps = len(set(numeric_timestamps)) == len(numeric_timestamps)
    strictly_increasing = all(left < right for left, right in pairwise(numeric_timestamps))
    date_values = sorted({value.date().isoformat() for value in valid_parsed})
    latest = valid_parsed[-1] if valid_parsed else None
    earliest = valid_parsed[0] if valid_parsed else None

    passed = bool(rows) and all(
        (
            schema.get("equal_column_lengths") is True,
            not schema.get("missing_columns"),
            invalid_numeric_rows == 0,
            invalid_ohlc_rows == 0,
            negative_volume_rows == 0,
            negative_amount_rows == 0,
            future_rows == 0,
            unique_timestamps,
            strictly_increasing,
            lunch_rows == 0,
        )
    )
    return {
        "passed": passed,
        "bar_count": len(rows),
        "date_count": len(date_values),
        "dates": date_values,
        "earliest_timestamp": earliest.isoformat() if earliest else None,
        "latest_timestamp": latest.isoformat() if latest else None,
        "schema": schema,
        "checks": {
            "non_empty": bool(rows),
            "timestamps_unique": unique_timestamps,
            "timestamps_strictly_increasing": strictly_increasing,
            "invalid_numeric_rows": invalid_numeric_rows,
            "strict_invalid_ohlc_rows": strict_invalid_ohlc_rows,
            "invalid_ohlc_rows": invalid_ohlc_rows,
            "ohlc_epsilon_warning_rows": ohlc_epsilon_warning_rows,
            "ohlc_abs_epsilon": OHLC_ABS_EPSILON,
            "negative_volume_rows": negative_volume_rows,
            "strict_negative_amount_rows": strict_negative_amount_rows,
            "negative_amount_rows": negative_amount_rows,
            "amount_epsilon_warning_rows": amount_epsilon_warning_rows,
            "amount_zero_epsilon": AMOUNT_ZERO_EPSILON,
            "future_timestamp_rows": future_rows,
            "lunch_break_rows": lunch_rows if intraday else None,
            "off_continuous_session_rows": off_session_rows if intraday else None,
        },
        "ohlc_violation_distribution": ohlc_violation_distribution(rows),
        "amount_tolerance": classify_amount_tolerance(rows),
        "response_hash": _row_signature(rows),
    }


def _value_close(
    left: Any,
    right: Any,
    *,
    abs_tolerance: float,
    rel_tolerance: float,
) -> bool:
    left_value = _as_float(left)
    right_value = _as_float(right)
    if left_value is None or right_value is None:
        return False
    return math.isclose(
        left_value,
        right_value,
        rel_tol=rel_tolerance,
        abs_tol=abs_tolerance,
    )


def _quote_contract(
    quotes: Any,
    *,
    now: datetime,
    fetched_at: str | None,
) -> dict[str, Any]:
    items = quotes if isinstance(quotes, list) else []
    by_symbol = {
        str(item.get("symbol")): item
        for item in items
        if isinstance(item, dict) and item.get("symbol")
    }
    requested_set = set(SYMBOLS)
    returned_set = set(by_symbol)
    expected_trade_dates: list[str] = []
    snapshots: dict[str, Any] = {}
    valid_rows = True
    for symbol in SYMBOLS:
        item = by_symbol.get(symbol)
        if not item:
            valid_rows = False
            snapshots[symbol] = {"present": False}
            continue
        parsed = _dt_from_ms(item.get("timestamp"))
        fields_valid = (
            all(
                _is_number(item.get(field))
                for field in (
                    "last_price",
                    "prev_close",
                    "open",
                    "high",
                    "low",
                    "volume",
                    "amount",
                )
            )
            and parsed is not None
        )
        if fields_valid:
            values = [
                float(item[field]) for field in ("last_price", "prev_close", "open", "high", "low")
            ]
            fields_valid = all(value >= 0 for value in values)
            fields_valid = fields_valid and float(item["volume"]) >= 0
            fields_valid = fields_valid and float(item["amount"]) >= 0
        valid_rows = valid_rows and fields_valid
        trade_date = parsed.date().isoformat() if parsed else None
        if trade_date:
            expected_trade_dates.append(trade_date)
        snapshots[symbol] = {
            "present": True,
            "symbol_matches": item.get("symbol") == symbol,
            "timestamp": parsed.isoformat() if parsed else None,
            "trade_date": trade_date,
            "last_price": item.get("last_price"),
            "prev_close": item.get("prev_close"),
            "open": item.get("open"),
            "high": item.get("high"),
            "low": item.get("low"),
            "volume": item.get("volume"),
            "amount": item.get("amount"),
            "fields_valid": fields_valid,
        }
    expected_trade_date = max(expected_trade_dates) if expected_trade_dates else None
    passed = requested_set == returned_set and valid_rows
    return {
        "passed": passed,
        "source": REPORT_SOURCE,
        "fetched_at": fetched_at,
        "requested_symbols": list(SYMBOLS),
        "returned_symbols": sorted(returned_set),
        "exact_symbol_set": requested_set == returned_set,
        "expected_recent_trade_date": expected_trade_date,
        "expected_recent_trade_date_basis": "latest TickFlow quote timestamp",
        "snapshots": snapshots,
        "response_hash": _canonical_hash(items),
        "evaluated_at": now.isoformat(timespec="seconds"),
    }


def _instrument_contract(
    instruments: Any,
    *,
    fetched_at: str | None,
) -> dict[str, Any]:
    items = instruments if isinstance(instruments, list) else []
    by_symbol = {
        str(item.get("symbol")): item
        for item in items
        if isinstance(item, dict) and item.get("symbol")
    }
    checks: dict[str, Any] = {}
    for symbol in SYMBOLS:
        item = by_symbol.get(symbol)
        checks[symbol] = {
            "present": item is not None,
            "symbol_matches": bool(item and item.get("symbol") == symbol),
            "name": item.get("name") if item else None,
            "name_matches": bool(item and item.get("name") == EXPECTED_NAMES[symbol]),
            "code": item.get("code") if item else None,
            "exchange": item.get("exchange") if item else None,
            "region": item.get("region") if item else None,
            "type": item.get("type") if item else None,
        }
    passed = all(
        value["present"] and value["symbol_matches"] and value["name_matches"]
        for value in checks.values()
    )
    return {
        "passed": passed,
        "source": REPORT_SOURCE,
        "fetched_at": fetched_at,
        "checks": checks,
        "response_hash": _canonical_hash(items),
    }


def _daily_contract(
    symbol: str,
    raw_data: Any,
    qfq_data: Any,
    qfq_repeat_data: Any,
    *,
    expected_trade_date: str | None,
    fetched_at_raw: str | None,
    fetched_at_qfq: str | None,
    fetched_at_qfq_repeat: str | None,
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    raw_rows, raw_schema = _compact_rows(raw_data)
    qfq_rows, qfq_schema = _compact_rows(qfq_data)
    qfq_repeat_rows, repeat_schema = _compact_rows(qfq_repeat_data)
    raw_validation = _validate_kline_rows(raw_rows, raw_schema, intraday=False, now=now)
    qfq_validation = _validate_kline_rows(qfq_rows, qfq_schema, intraday=False, now=now)
    repeat_validation = _validate_kline_rows(
        qfq_repeat_rows, repeat_schema, intraday=False, now=now
    )
    raw_dates = [_date_from_ms(row["timestamp"]) for row in raw_rows]
    qfq_dates = [_date_from_ms(row["timestamp"]) for row in qfq_rows]
    latest_raw_date = raw_dates[-1] if raw_dates else None
    latest_qfq_date = qfq_dates[-1] if qfq_dates else None
    latest_not_behind = bool(
        expected_trade_date
        and latest_raw_date
        and latest_qfq_date
        and latest_raw_date >= expected_trade_date
        and latest_qfq_date >= expected_trade_date
    )
    date_alignment = raw_dates == qfq_dates and bool(raw_dates)
    repeat_stable = _row_signature(qfq_rows) == _row_signature(qfq_repeat_rows) and bool(qfq_rows)
    latest_prices_match = False
    if raw_rows and qfq_rows and raw_rows[-1]["timestamp"] == qfq_rows[-1]["timestamp"]:
        latest_prices_match = all(
            _value_close(
                raw_rows[-1][field],
                qfq_rows[-1][field],
                abs_tolerance=PRICE_ABS_TOLERANCE,
                rel_tolerance=PRICE_REL_TOLERANCE,
            )
            for field in ("open", "high", "low", "close")
        )
    passed = all(
        (
            raw_validation["passed"],
            qfq_validation["passed"],
            repeat_validation["passed"],
            date_alignment,
            latest_not_behind,
            repeat_stable,
            latest_prices_match,
        )
    )
    contract = {
        "contract": "tickflow_daily_v1",
        "symbol": symbol,
        "name": EXPECTED_NAMES[symbol],
        "passed": passed,
        "source": REPORT_SOURCE,
        "fetched_at": {
            "adjust_none": fetched_at_raw,
            "adjust_qfq_sdk_forward": fetched_at_qfq,
            "adjust_qfq_repeat": fetched_at_qfq_repeat,
        },
        "requested": {
            "period": "1d",
            "count": 1000,
            "raw_adjustment": "none",
            "qfq_semantic": "qfq",
            "qfq_sdk_parameter": SDK_QFQ_ADJUST,
        },
        "raw": raw_validation,
        "qfq": qfq_validation,
        "checks": {
            "symbol_matches_request": symbol in SYMBOLS,
            "trade_dates_align_between_raw_and_qfq": date_alignment,
            "latest_raw_trade_date": latest_raw_date,
            "latest_qfq_trade_date": latest_qfq_date,
            "expected_recent_trade_date": expected_trade_date,
            "expected_recent_trade_date_basis": "latest TickFlow quote timestamp",
            "latest_date_not_behind_recent_trade_date": latest_not_behind,
            "qfq_repeat_stable": repeat_stable,
            "latest_qfq_prices_equal_raw_prices": latest_prices_match,
        },
        "units": {
            "volume": "SDK raw numeric unit; semantic unit inferred in minute contract",
            "amount": "SDK raw numeric unit; semantic unit inferred in minute contract",
        },
        "evaluated_at": now.isoformat(timespec="seconds"),
    }
    return contract, raw_rows, qfq_rows


def _factor_entries(data: Any, symbol: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    values = data.get(symbol)
    if not isinstance(values, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        raw_date = item.get("trade_date")
        if raw_date is None:
            raw_date = item.get("date")
        if raw_date is None:
            raw_date = item.get("timestamp")
        factor = item.get("ex_factor")
        if factor is None:
            factor = item.get("adj_factor")
        entries.append(
            {
                "timestamp": item.get("timestamp"),
                "trade_date": _normalize_trade_date(raw_date),
                "ex_factor": factor,
            }
        )
    return entries


def _expected_qfq_rows(
    raw_rows: list[dict[str, Any]],
    factors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    usable_factors = [
        (entry["trade_date"], float(entry["ex_factor"]))
        for entry in factors
        if entry.get("trade_date")
        and _is_number(entry.get("ex_factor"))
        and float(entry["ex_factor"]) > 0
    ]
    if not raw_rows or not usable_factors:
        return []
    latest_date = _date_from_ms(raw_rows[-1].get("timestamp"))
    relevant = [
        (factor_date, factor)
        for factor_date, factor in usable_factors
        if latest_date and factor_date <= latest_date
    ]
    total_factor = math.prod(factor for _, factor in relevant)
    if total_factor <= 0:
        return []
    expected: list[dict[str, Any]] = []
    for row in raw_rows:
        trade_date = _date_from_ms(row.get("timestamp"))
        cumulative = math.prod(
            factor for factor_date, factor in relevant if trade_date and factor_date <= trade_date
        )
        ratio = cumulative / total_factor
        expected.append(
            {
                **row,
                **{
                    field: (float(row[field]) * ratio if _is_number(row.get(field)) else None)
                    for field in ("open", "high", "low", "close")
                },
                "_ratio": ratio,
            }
        )
    return expected


def _comparison_stats(
    expected_rows: list[dict[str, Any]],
    actual_rows: list[dict[str, Any]],
    fields: Iterable[str],
) -> dict[str, Any]:
    actual_by_timestamp = {
        row.get("timestamp"): row for row in actual_rows if row.get("timestamp") is not None
    }
    per_field: dict[str, Any] = {}
    all_matches = 0
    compared_rows = 0
    for field in fields:
        matched = 0
        compared = 0
        max_abs = 0.0
        max_rel = 0.0
        for expected in expected_rows:
            actual = actual_by_timestamp.get(expected.get("timestamp"))
            if not actual:
                continue
            left = _as_float(expected.get(field))
            right = _as_float(actual.get(field))
            if left is None or right is None:
                continue
            compared += 1
            abs_diff = abs(left - right)
            rel_diff = abs_diff / max(abs(left), abs(right), 1e-12)
            max_abs = max(max_abs, abs_diff)
            max_rel = max(max_rel, rel_diff)
            if math.isclose(
                left,
                right,
                rel_tol=PRICE_REL_TOLERANCE,
                abs_tol=PRICE_ABS_TOLERANCE,
            ):
                matched += 1
        per_field[field] = {
            "matched": matched,
            "compared": compared,
            "match_rate": matched / compared if compared else 0.0,
            "max_abs_diff": max_abs if compared else None,
            "max_rel_diff": max_rel if compared else None,
        }
    for expected in expected_rows:
        actual = actual_by_timestamp.get(expected.get("timestamp"))
        if not actual:
            continue
        compared_rows += 1
        if all(
            _value_close(
                expected.get(field),
                actual.get(field),
                abs_tolerance=PRICE_ABS_TOLERANCE,
                rel_tolerance=PRICE_REL_TOLERANCE,
            )
            for field in fields
        ):
            all_matches += 1
    return {
        "rows_compared": compared_rows,
        "all_price_fields_match_rows": all_matches,
        "all_price_fields_match_rate": (all_matches / compared_rows if compared_rows else 0.0),
        "per_field": per_field,
    }


def _event_continuity(
    raw_rows: list[dict[str, Any]],
    qfq_rows: list[dict[str, Any]],
    factors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_by_date = {_date_from_ms(row["timestamp"]): row for row in raw_rows}
    qfq_by_date = {_date_from_ms(row["timestamp"]): row for row in qfq_rows}
    ordered_dates = sorted(value for value in raw_by_date if value)
    previous_date = {
        ordered_dates[index]: ordered_dates[index - 1] for index in range(1, len(ordered_dates))
    }
    evidence: list[dict[str, Any]] = []
    for entry in factors:
        event_date = entry.get("trade_date")
        prior_date = previous_date.get(event_date)
        if not event_date or not prior_date:
            continue
        raw_today = raw_by_date[event_date]
        raw_prior = raw_by_date[prior_date]
        qfq_today = qfq_by_date.get(event_date)
        qfq_prior = qfq_by_date.get(prior_date)
        if not qfq_today or not qfq_prior:
            continue
        raw_open = _as_float(raw_today.get("open"))
        raw_close = _as_float(raw_prior.get("close"))
        qfq_open = _as_float(qfq_today.get("open"))
        qfq_close = _as_float(qfq_prior.get("close"))
        if raw_open is None or raw_close in (None, 0) or qfq_open is None or qfq_close in (None, 0):
            continue
        raw_gap = raw_open / raw_close - 1
        qfq_gap = qfq_open / qfq_close - 1
        evidence.append(
            {
                "factor_date": event_date,
                "effective_date": event_date,
                "previous_trade_date": prior_date,
                "ex_factor": entry.get("ex_factor"),
                "raw_overnight_gap_pct": raw_gap * 100,
                "qfq_overnight_gap_pct": qfq_gap * 100,
                "absolute_gap_reduced": abs(qfq_gap) <= abs(raw_gap),
            }
        )
    return evidence


def _ex_factor_contract(
    symbol: str,
    factor_data: Any,
    factor_repeat_data: Any,
    raw_rows: list[dict[str, Any]],
    qfq_rows: list[dict[str, Any]],
    *,
    qfq_repeat_stable: bool,
    fetched_at: str | None,
    fetched_at_repeat: str | None,
    now: datetime,
) -> dict[str, Any]:
    factors = _factor_entries(factor_data, symbol)
    repeated = _factor_entries(factor_repeat_data, symbol)
    dates = [entry["trade_date"] for entry in factors]
    positive = all(
        _is_number(entry["ex_factor"]) and float(entry["ex_factor"]) > 0 for entry in factors
    )
    timestamps_valid = all(entry["trade_date"] is not None for entry in factors)
    dates_unique = len(set(dates)) == len(dates)
    dates_sorted = bool(dates) and all(
        left <= right for left, right in pairwise(dates) if left is not None and right is not None
    )
    repeat_stable = _canonical_hash(factors) == _canonical_hash(repeated)
    expected_rows = _expected_qfq_rows(raw_rows, factors)
    relation = _comparison_stats(expected_rows, qfq_rows, ("open", "high", "low", "close"))
    relation_passed = relation["all_price_fields_match_rate"] >= 0.95
    continuity = _event_continuity(raw_rows, qfq_rows, factors)
    observable = bool(continuity)
    passed = all(
        (
            bool(factors),
            positive,
            timestamps_valid,
            dates_unique,
            dates_sorted,
            repeat_stable,
            qfq_repeat_stable,
            bool(expected_rows),
            relation_passed,
            observable,
        )
    )
    return {
        "contract": "tickflow_ex_factors_v1",
        "symbol": symbol,
        "name": EXPECTED_NAMES[symbol],
        "passed": passed,
        "source": REPORT_SOURCE,
        "fetched_at": {
            "initial": fetched_at,
            "repeat": fetched_at_repeat,
        },
        "factor_count": len(factors),
        "factor_dates": factors,
        "checks": {
            "non_empty": bool(factors),
            "factor_timestamps_valid": timestamps_valid,
            "factor_dates_unique": dates_unique,
            "factor_dates_sorted": dates_sorted,
            "factor_values_positive": positive,
            "repeat_result_stable": repeat_stable,
            "qfq_repeat_result_stable": qfq_repeat_stable,
            "qfq_relation_formula": (
                "adjusted = raw * cumulative_product(factors <= trade_date) "
                "/ total_product(factors <= latest_raw_date)"
            ),
            "qfq_relation": relation,
            "qfq_relation_passed": relation_passed,
            "no_second_adjustment_evidence": (
                "SDK qfq matched one local application and repeated qfq/factor "
                "responses were stable"
            ),
            "continuity_observable": observable,
            "continuity_events": continuity,
        },
        "limitations": [
            "TickFlow exposes one timestamp per factor entry; this validation "
            "interprets its Asia/Shanghai date as both factor date and effective date.",
            "Continuity evidence separates mechanical adjustment consistency from "
            "real overnight market movement; it does not require every adjusted "
            "overnight gap to be smaller.",
        ],
        "evaluated_at": now.isoformat(timespec="seconds"),
    }


def _merge_rows(
    historical_data: Any,
    current_data: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    historical_rows, historical_schema = _compact_rows(historical_data)
    current_rows, current_schema = _compact_rows(current_data)
    merged_by_timestamp = {
        row["timestamp"]: row for row in historical_rows if row.get("timestamp") is not None
    }
    for row in current_rows:
        if row.get("timestamp") is not None:
            merged_by_timestamp[row["timestamp"]] = row
    merged = [merged_by_timestamp[timestamp] for timestamp in sorted(merged_by_timestamp)]
    return merged, {
        "historical": historical_schema,
        "current": current_schema,
        "historical_row_count": len(historical_rows),
        "current_row_count": len(current_rows),
        "merged_row_count": len(merged),
        "overlap_row_count": (len(historical_rows) + len(current_rows) - len(merged)),
    }


def _current_intraday_freshness(
    rows: list[dict[str, Any]],
    *,
    expected_trade_date: str | None,
    now: datetime,
) -> dict[str, Any]:
    parsed = [
        value for value in (_dt_from_ms(row.get("timestamp")) for row in rows) if value is not None
    ]
    latest = parsed[-1] if parsed else None
    if latest is None or expected_trade_date is None:
        return {
            "passed": False,
            "latest_timestamp": latest.isoformat() if latest else None,
            "expected_trade_date": expected_trade_date,
            "rule": "missing timestamp or expected trade date",
        }
    same_trade_date = latest.date().isoformat() == expected_trade_date
    current_clock = now.time().replace(tzinfo=None)
    if now.date().isoformat() != expected_trade_date:
        time_fresh = True
        rule = "quote-derived latest completed trade date"
    elif current_clock < daytime(9, 30):
        time_fresh = latest.date() < now.date()
        rule = "pre-open expects previous completed session"
    elif current_clock <= daytime(11, 30) or (daytime(13, 0) <= current_clock <= daytime(15, 0)):
        lag = now - latest
        time_fresh = timedelta(0) <= lag <= timedelta(minutes=10)
        rule = "open-session lag <= 10 minutes"
    elif current_clock < daytime(13, 0):
        time_fresh = latest.time().replace(tzinfo=None) >= daytime(11, 25)
        rule = "lunch break accepts morning close >= 11:25"
    else:
        time_fresh = latest.time().replace(tzinfo=None) >= daytime(14, 55)
        rule = "post-close accepts current-session final bar >= 14:55"
    return {
        "passed": same_trade_date and time_fresh,
        "latest_timestamp": latest.isoformat(),
        "expected_trade_date": expected_trade_date,
        "same_trade_date": same_trade_date,
        "time_fresh": time_fresh,
        "rule": rule,
    }


def _minute_index(parsed: datetime, convention: str) -> tuple[int, int] | None:
    minute_of_day = parsed.hour * 60 + parsed.minute
    if convention == "end_labeled":
        if 9 * 60 + 31 <= minute_of_day <= 11 * 60 + 30:
            ordinal = minute_of_day - (9 * 60 + 30) - 1
            return ordinal // 30, ordinal % 30
        if 13 * 60 + 1 <= minute_of_day <= 15 * 60:
            ordinal = minute_of_day - 13 * 60 - 1
            return 4 + ordinal // 30, ordinal % 30
        return None
    if convention == "start_labeled":
        if 9 * 60 + 30 <= minute_of_day <= 11 * 60 + 29:
            ordinal = minute_of_day - (9 * 60 + 30)
            return ordinal // 30, ordinal % 30
        if 13 * 60 <= minute_of_day <= 14 * 60 + 59:
            ordinal = minute_of_day - 13 * 60
            return 4 + ordinal // 30, ordinal % 30
        return None
    raise ValueError(f"unsupported convention: {convention}")


def _bucket_label(day: date, bucket: int, convention: str) -> datetime:
    if convention == "end_labeled":
        labels = (
            daytime(10, 0),
            daytime(10, 30),
            daytime(11, 0),
            daytime(11, 30),
            daytime(13, 30),
            daytime(14, 0),
            daytime(14, 30),
            daytime(15, 0),
        )
    else:
        labels = (
            daytime(9, 30),
            daytime(10, 0),
            daytime(10, 30),
            daytime(11, 0),
            daytime(13, 0),
            daytime(13, 30),
            daytime(14, 0),
            daytime(14, 30),
        )
    return datetime.combine(day, labels[bucket], SHANGHAI)


def _aggregate_30m(
    rows: list[dict[str, Any]],
    *,
    trade_date: str,
    convention: str,
) -> dict[str, Any]:
    buckets: dict[int, list[dict[str, Any]]] = {index: [] for index in range(8)}
    excluded = 0
    for row in rows:
        parsed = _dt_from_ms(row.get("timestamp"))
        if not parsed or parsed.date().isoformat() != trade_date:
            continue
        index = _minute_index(parsed, convention)
        if index is None:
            excluded += 1
            continue
        buckets[index[0]].append(row)
    aggregated: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    day = date.fromisoformat(trade_date)
    for index in range(8):
        group = sorted(buckets[index], key=lambda row: row["timestamp"])
        counts[str(index)] = len(group)
        if not group:
            continue
        prices = {
            field: [_as_float(row.get(field)) for row in group]
            for field in ("open", "high", "low", "close")
        }
        volume = [_as_float(row.get("volume")) for row in group]
        amount = [_as_float(row.get("amount")) for row in group]
        if any(
            any(item is None for item in values) for values in (*prices.values(), volume, amount)
        ):
            continue
        label = _bucket_label(day, index, convention)
        aggregated.append(
            {
                "bucket": index,
                "timestamp": int(label.timestamp() * 1000),
                "open": prices["open"][0],
                "high": max(prices["high"]),
                "low": min(prices["low"]),
                "close": prices["close"][-1],
                "volume": sum(volume),
                "amount": sum(amount),
                "source_minute_count": len(group),
            }
        )
    return {
        "convention": convention,
        "trade_date": trade_date,
        "bars": aggregated,
        "bucket_minute_counts": counts,
        "complete_bucket_count": sum(value == 30 for value in counts.values()),
        "excluded_continuous_session_rows": excluded,
    }


def _direct_30m_for_date(
    rows: list[dict[str, Any]],
    trade_date: str,
) -> list[dict[str, Any]]:
    selected = [row for row in rows if _date_from_ms(row.get("timestamp")) == trade_date]
    return sorted(selected, key=lambda row: row["timestamp"])


def _relative_difference(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1e-12)


def _compare_aggregated_to_direct(
    aggregate: dict[str, Any],
    direct: list[dict[str, Any]],
) -> dict[str, Any]:
    aggregate_rows = aggregate["bars"]
    pair_count = min(len(aggregate_rows), len(direct), 8)
    fields = ("open", "high", "low", "close", "volume", "amount")
    stats: dict[str, dict[str, Any]] = {
        field: {
            "matched": 0,
            "compared": 0,
            "max_abs_diff": 0.0,
            "max_rel_diff": 0.0,
        }
        for field in fields
    }
    timestamp_deltas: list[int] = []
    fully_matching = 0
    for index in range(pair_count):
        aggregated = aggregate_rows[index]
        direct_bar = direct[index]
        timestamp_deltas.append(int(direct_bar["timestamp"]) - int(aggregated["timestamp"]))
        bar_matches = True
        for field in fields:
            left = _as_float(aggregated.get(field))
            right = _as_float(direct_bar.get(field))
            if left is None or right is None:
                bar_matches = False
                continue
            abs_diff = abs(left - right)
            rel_diff = _relative_difference(left, right)
            stats[field]["compared"] += 1
            stats[field]["max_abs_diff"] = max(stats[field]["max_abs_diff"], abs_diff)
            stats[field]["max_rel_diff"] = max(stats[field]["max_rel_diff"], rel_diff)
            if field in ("open", "high", "low", "close"):
                matches = math.isclose(
                    left,
                    right,
                    rel_tol=PRICE_REL_TOLERANCE,
                    abs_tol=PRICE_ABS_TOLERANCE,
                )
            else:
                matches = math.isclose(
                    left,
                    right,
                    rel_tol=SUM_REL_TOLERANCE,
                    abs_tol=1e-8,
                )
            if matches:
                stats[field]["matched"] += 1
            else:
                bar_matches = False
        if bar_matches:
            fully_matching += 1
    score = 0
    for _field, values in stats.items():
        compared = values["compared"]
        values["match_rate"] = values["matched"] / compared if compared else 0.0
        score += values["matched"]
        if compared == 0:
            values["max_abs_diff"] = None
            values["max_rel_diff"] = None
    price_pass = all(stats[field]["match_rate"] >= 0.95 for field in fields[:4])
    sums_pass = all(stats[field]["match_rate"] >= 0.95 for field in fields[4:])
    complete = aggregate["complete_bucket_count"] == 8
    passed = pair_count == 8 and len(direct) == 8 and complete and price_pass and sums_pass
    return {
        "passed": passed,
        "score": score,
        "pair_count": pair_count,
        "direct_bar_count": len(direct),
        "aggregated_bar_count": len(aggregate_rows),
        "complete_bucket_count": aggregate["complete_bucket_count"],
        "fully_matching_bar_count": fully_matching,
        "fields": stats,
        "direct_minus_aggregate_timestamp_ms": timestamp_deltas,
        "direct_timestamps": [
            _dt_from_ms(row["timestamp"]).isoformat() if _dt_from_ms(row["timestamp"]) else None
            for row in direct
        ],
        "aggregate_timestamps": [
            _dt_from_ms(row["timestamp"]).isoformat() if _dt_from_ms(row["timestamp"]) else None
            for row in aggregate_rows
        ],
        "bucket_minute_counts": aggregate["bucket_minute_counts"],
        "excluded_continuous_session_rows": aggregate["excluded_continuous_session_rows"],
    }


def _infer_units(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ratios: list[float] = []
    for row in rows:
        volume = _as_float(row.get("volume"))
        amount = _as_float(row.get("amount"))
        close = _as_float(row.get("close"))
        if volume and amount is not None and close:
            ratios.append(amount / (volume * close))
    median_ratio = statistics.median(ratios) if ratios else None
    if median_ratio is not None and 0.5 <= median_ratio <= 2:
        volume_inference = "shares"
        amount_inference = "CNY-compatible currency amount"
    elif median_ratio is not None and 50 <= median_ratio <= 200:
        volume_inference = "100-share lots"
        amount_inference = "CNY-compatible currency amount"
    else:
        volume_inference = "unknown"
        amount_inference = "unknown"
    return {
        "method": "median(amount / (volume * close)) over nonzero 1m bars",
        "sample_count": len(ratios),
        "median_ratio": median_ratio,
        "volume_unit_inference": volume_inference,
        "amount_unit_inference": amount_inference,
        "authoritative_sdk_unit_documentation_found": False,
        "conclusion": (
            "Arithmetic consistency is contract-tested; semantic units remain "
            "an inference until TickFlow publishes an authoritative unit contract."
        ),
    }


def _select_comparison_date(
    minute_rows: list[dict[str, Any]],
    direct_rows: list[dict[str, Any]],
) -> str | None:
    minute_counts: dict[str, int] = {}
    direct_counts: dict[str, int] = {}
    for row in minute_rows:
        day = _date_from_ms(row.get("timestamp"))
        if day:
            minute_counts[day] = minute_counts.get(day, 0) + 1
    for row in direct_rows:
        day = _date_from_ms(row.get("timestamp"))
        if day:
            direct_counts[day] = direct_counts.get(day, 0) + 1
    candidates = [
        day
        for day in minute_counts.keys() & direct_counts.keys()
        if minute_counts[day] >= 220 and direct_counts[day] >= 7
    ]
    return max(candidates) if candidates else None


def _field_reconciled_by_09_30_candidate(
    selected_comparison: dict[str, Any] | None,
    dual_first_bucket: dict[str, Any],
    symbol: str,
    field: str,
) -> bool:
    selected = _mapping(selected_comparison)
    field_stats = _mapping(_mapping(selected.get("fields")).get(field))
    compared = field_stats.get("compared")
    matched = field_stats.get("matched")
    detail = _mapping(_mapping(dual_first_bucket.get("symbols")).get(symbol))
    continuous = _mapping(detail.get("continuous_09_31_to_10_00"))
    candidate = _mapping(detail.get("vendor_candidate_09_30_to_10_00"))
    if not isinstance(compared, int) or compared != 8 or not isinstance(matched, int):
        return False
    if matched == compared:
        return True
    return bool(
        matched == compared - 1
        and continuous.get(f"{field}_match") is False
        and candidate.get(f"{field}_match") is True
    )


def _ohlc_contract_with_09_30_candidate(
    selected_comparison: dict[str, Any] | None,
    dual_first_bucket: dict[str, Any],
    symbol: str,
) -> bool:
    selected = _mapping(selected_comparison)
    structural_checks = (
        selected.get("pair_count") == 8,
        selected.get("direct_bar_count") == 8,
        selected.get("aggregated_bar_count") == 8,
        selected.get("complete_bucket_count") == 8,
    )
    return all(structural_checks) and all(
        _field_reconciled_by_09_30_candidate(
            selected_comparison,
            dual_first_bucket,
            symbol,
            field,
        )
        for field in ("open", "high", "low", "close")
    )


def _minute_symbol_contract(
    symbol: str,
    historical_1m_data: Any,
    current_1m_data: Any,
    historical_30m_data: Any,
    current_30m_data: Any,
    *,
    expected_trade_date: str | None,
    now: datetime,
    require_historical_permission: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    minute_rows, minute_merge = _merge_rows(historical_1m_data, current_1m_data)
    direct_rows, direct_merge = _merge_rows(historical_30m_data, current_30m_data)
    minute_validation = _validate_kline_rows(
        minute_rows,
        {
            "input_is_mapping": True,
            "missing_columns": [],
            "column_lengths": {"merged": len(minute_rows)},
            "equal_column_lengths": True,
        },
        intraday=True,
        now=now,
    )
    direct_validation = _validate_kline_rows(
        direct_rows,
        {
            "input_is_mapping": True,
            "missing_columns": [],
            "column_lengths": {"merged": len(direct_rows)},
            "equal_column_lengths": True,
        },
        intraday=True,
        now=now,
    )
    minute_freshness = _current_intraday_freshness(
        minute_rows, expected_trade_date=expected_trade_date, now=now
    )
    direct_freshness = _current_intraday_freshness(
        direct_rows, expected_trade_date=expected_trade_date, now=now
    )
    historical_permission_observed = (
        minute_validation["date_count"] >= 2
        and bool(minute_validation["dates"])
        and expected_trade_date is not None
        and minute_validation["dates"][0] < expected_trade_date
    )
    historical_permission_satisfied = (
        historical_permission_observed
        if require_historical_permission
        else True
    )
    comparison_date = _select_comparison_date(minute_rows, direct_rows)
    candidates: dict[str, Any] = {}
    if comparison_date:
        direct_day = _direct_30m_for_date(direct_rows, comparison_date)
        for convention in ("end_labeled", "start_labeled"):
            aggregate = _aggregate_30m(
                minute_rows,
                trade_date=comparison_date,
                convention=convention,
            )
            candidates[convention] = _compare_aggregated_to_direct(aggregate, direct_day)
    selected_convention = None
    selected_comparison = None
    if candidates:
        selected_convention = max(
            candidates,
            key=lambda name: (
                bool(candidates[name]["passed"]),
                candidates[name]["score"],
                candidates[name]["complete_bucket_count"],
            ),
        )
        selected_comparison = candidates[selected_convention]
    continuous_aggregation_ohlc_passed = bool(
        selected_comparison
        and selected_comparison["pair_count"] == 8
        and all(
            selected_comparison["fields"][field]["match_rate"] == 1.0
            for field in ("open", "high", "low", "close")
        )
    )
    minute_day_rows = (
        _direct_30m_for_date(minute_rows, comparison_date)
        if comparison_date
        else []
    )
    direct_day_rows = (
        _direct_30m_for_date(direct_rows, comparison_date)
        if comparison_date
        else []
    )
    dual_first_bucket = build_dual_convention_validation(
        {symbol: minute_day_rows},
        {symbol: direct_day_rows},
        trade_date=comparison_date or "",
    )
    aggregation_ohlc_passed = _ohlc_contract_with_09_30_candidate(
        selected_comparison,
        dual_first_bucket,
        symbol,
    )
    first_bucket_candidate_passed = (
        dual_first_bucket["verdict"]
        == "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED"
    )
    vendor_convention_pending = bool(
        aggregation_ohlc_passed
        and first_bucket_candidate_passed
        and selected_comparison
        and selected_comparison["fields"]["volume"]["matched"] == 7
        and selected_comparison["fields"]["amount"]["matched"] == 7
    )
    one_minute_passed = all(
        (
            minute_validation["passed"],
            minute_freshness["passed"],
            historical_permission_satisfied,
        )
    )
    direct_30m_passed = all(
        (
            direct_validation["passed"],
            direct_freshness["passed"],
            direct_validation["date_count"]
            >= (2 if require_historical_permission else 1),
        )
    )
    contract = {
        "symbol": symbol,
        "name": EXPECTED_NAMES[symbol],
        "one_minute": {
            "passed": one_minute_passed,
            "validation": minute_validation,
            "freshness": minute_freshness,
            "merge": minute_merge,
            "historical_permission_observed": historical_permission_observed,
            "historical_permission_required": require_historical_permission,
            "historical_permission_satisfied": historical_permission_satisfied,
        },
        "direct_30m": {
            "passed": direct_30m_passed,
            "validation": direct_validation,
            "freshness": direct_freshness,
            "merge": direct_merge,
        },
        "local_1m_to_30m": {
            "passed": aggregation_ohlc_passed and first_bucket_candidate_passed,
            "ohlc_contract": (
                "PASSED" if aggregation_ohlc_passed else "FAILED"
            ),
            "continuous_09_31_ohlc_contract": (
                "PASSED"
                if continuous_aggregation_ohlc_passed
                else "FIRST_BUCKET_MISMATCH"
            ),
            "ohlc_contract_rule": (
                "All continuous buckets must match, except a first-bucket field "
                "may be reconciled only when its observed 09:31-10:00 value "
                "mismatches and the unmodified 09:30-10:00 candidate matches."
            ),
            "volume_amount_first_bucket": (
                "VENDOR_CONVENTION_PENDING"
                if vendor_convention_pending
                else "UNRESOLVED"
            ),
            "comparison_trade_date": comparison_date,
            "selected_convention": selected_convention,
            "selected_comparison": selected_comparison,
            "all_candidate_conventions": candidates,
            "dual_first_bucket": dual_first_bucket,
            "time_bucket_boundaries": {
                "end_labeled": [
                    "09:31-10:00",
                    "10:01-10:30",
                    "10:31-11:00",
                    "11:01-11:30",
                    "13:01-13:30",
                    "13:31-14:00",
                    "14:01-14:30",
                    "14:31-15:00",
                ],
                "start_labeled": [
                    "09:30-09:59",
                    "10:00-10:29",
                    "10:30-10:59",
                    "11:00-11:29",
                    "13:00-13:29",
                    "13:30-13:59",
                    "14:00-14:29",
                    "14:30-14:59",
                ],
            },
            "auction_policy": (
                "Rows outside continuous-auction windows are counted and excluded; "
                "they are not folded into the first bucket."
            ),
            "adjustment": "none for both 1m input and direct 30m",
            "missing_minute_policy": "do not fill; incomplete buckets remain visible",
            "volume_aggregation": "sum raw SDK volume values",
            "amount_aggregation": "sum raw SDK amount values",
        },
        "unit_inference": _infer_units(minute_rows),
    }
    return contract, {
        "one_minute_passed": one_minute_passed,
        "direct_30m_passed": direct_30m_passed,
        "aggregation_ohlc_passed": aggregation_ohlc_passed,
        "first_bucket_candidate_passed": first_bucket_candidate_passed,
        "vendor_convention_pending": vendor_convention_pending,
    }


def _depth_contract(
    depth: Any,
    *,
    symbol: str,
    expected_trade_date: str | None,
    fetched_at: str | None,
    now: datetime,
) -> dict[str, Any]:
    item = depth if isinstance(depth, dict) else {}
    parsed = _dt_from_ms(item.get("timestamp"))
    fields: dict[str, Any] = {}
    all_levels_valid = True
    for side in ("bid", "ask"):
        prices = item.get(f"{side}_prices")
        volumes = item.get(f"{side}_volumes")
        prices_list = prices if isinstance(prices, list) else []
        volumes_list = volumes if isinstance(volumes, list) else []
        valid = (
            len(prices_list) >= 5
            and len(volumes_list) >= 5
            and all(_is_number(value) and float(value) >= 0 for value in prices_list[:5])
            and all(_is_number(value) and float(value) >= 0 for value in volumes_list[:5])
        )
        all_levels_valid = all_levels_valid and valid
        fields[side] = {
            "level_count": min(len(prices_list), len(volumes_list)),
            "first_five_prices": prices_list[:5],
            "first_five_volumes": volumes_list[:5],
            "valid": valid,
        }
    same_symbol = item.get("symbol") == symbol
    same_trade_date = bool(
        parsed and expected_trade_date and parsed.date().isoformat() == expected_trade_date
    )
    not_future = bool(parsed and parsed <= now + timedelta(minutes=5))
    if parsed and now.date() == parsed.date() and now.time() >= daytime(15, 0):
        time_fresh = parsed.time().replace(tzinfo=None) >= daytime(14, 55)
        freshness_rule = "post-close snapshot timestamp >= 14:55"
    elif parsed:
        time_fresh = timedelta(0) <= now - parsed <= timedelta(minutes=10)
        freshness_rule = "open-session lag <= 10 minutes"
    else:
        time_fresh = False
        freshness_rule = "missing timestamp"
    passed = all((same_symbol, all_levels_valid, same_trade_date, not_future, time_fresh))
    return {
        "contract": "tickflow_depth5_snapshot_v1",
        "passed": passed,
        "source": REPORT_SOURCE,
        "fetched_at": fetched_at,
        "sample_scope": "one contract check for 000403.SZ only",
        "symbol": item.get("symbol"),
        "region": item.get("region"),
        "timestamp": parsed.isoformat() if parsed else None,
        "expected_trade_date": expected_trade_date,
        "checks": {
            "symbol_matches": same_symbol,
            "five_bid_levels_valid": fields["bid"]["valid"],
            "five_ask_levels_valid": fields["ask"]["valid"],
            "same_recent_trade_date": same_trade_date,
            "not_future": not_future,
            "fresh": time_fresh,
            "freshness_rule": freshness_rule,
        },
        "levels": fields,
        "limitations": [
            "This is one five-level snapshot contract check, not matching.",
            "It cannot reconstruct exchange queue priority or tick-by-tick trades.",
        ],
        "response_hash": _canonical_hash(item),
    }


def _security_gate(
    *,
    session_valid_confirmed: bool,
    log_scan_passed: bool,
) -> tuple[dict[str, Any], str]:
    from app.config import settings
    from app.secrets_store import get_ai_key, get_tickflow_key, mask
    from app.services import auth as auth_service

    user_data = settings.data_dir / "user_data"
    auth_path = user_data / "auth.json"
    secrets_path = user_data / "secrets.json"
    tickflow_key = get_tickflow_key()
    ai_key = get_ai_key()
    top_level_symlinks: list[str] = []
    sensitive_symlinks: list[str] = []
    if user_data.exists():
        for child in user_data.iterdir():
            if child.is_symlink():
                top_level_symlinks.append(child.name)
                if child.name in {"auth.json", "secrets.json", ".env"}:
                    sensitive_symlinks.append(child.name)
    auth_meta = _regular_file_metadata(auth_path)
    secrets_meta = _regular_file_metadata(secrets_path)
    configured = bool(auth_service.is_configured())
    has_tickflow_key = bool(tickflow_key)
    masked_present = bool(mask(tickflow_key)) if tickflow_key else False
    has_ai_key = bool(ai_key)
    integrated_gold_enabled = bool(settings.gold_workspace_enabled)
    integrated_gold_external_send_count = 0 if not integrated_gold_enabled else None
    del ai_key
    passed = all(
        (
            configured,
            session_valid_confirmed,
            has_tickflow_key,
            masked_present,
            not has_ai_key,
            auth_meta["regular_file"],
            auth_meta["mode"] == "600",
            secrets_meta["regular_file"],
            secrets_meta["mode"] == "600",
            not top_level_symlinks,
            not sensitive_symlinks,
            log_scan_passed,
            not integrated_gold_enabled,
            integrated_gold_external_send_count == 0,
        )
    )
    result = {
        "passed": passed,
        "checked_at": _iso_now(),
        "authentication_configured": configured,
        "current_https_session_valid": session_valid_confirmed,
        "current_https_session_evidence": (
            "previous authenticated HTTPS gate retained; no session value read"
        ),
        "has_tickflow_key": has_tickflow_key,
        "masked_tickflow_key_present": masked_present,
        "has_ai_key": has_ai_key,
        "auth_json": auth_meta,
        "secrets_json": secrets_meta,
        "user_data_top_level_symlink_count": len(top_level_symlinks),
        "sensitive_top_level_symlink_count": len(sensitive_symlinks),
        "recent_log_credential_shape_scan_passed": log_scan_passed,
        "integrated_gold_enabled": integrated_gold_enabled,
        "integrated_gold_external_send_count": integrated_gold_external_send_count,
        "credential_values_recorded": False,
    }
    if not log_scan_passed:
        return result, "PHASE1_SECRET_LOGGING_BLOCKED"
    return result, "PHASE1_AUTH_BLOCKED" if not passed else "PASSED"


def _dict_symbol(data: Any, symbol: str) -> Any:
    return data.get(symbol) if isinstance(data, dict) else None


def _collect_phase11_calls(
    client: Any,
    auditor: RequestAuditor,
    *,
    now: datetime,
) -> dict[str, CallResult]:
    del now
    calls: dict[str, CallResult] = {}

    def invoke(
        capability: str,
        symbol_count: int,
        operation: Callable[[], Any],
    ) -> None:
        calls[capability] = auditor.call(
            capability,
            symbol_count,
            operation,
        )

    invoke(
        "instruments.batch",
        3,
        lambda: client.instruments.batch(list(SYMBOLS)),
    )
    invoke(
        "klines.batch.1d.adjust_none",
        3,
        lambda: client.klines.batch(
            list(SYMBOLS),
            period="1d",
            count=1000,
            adjust="none",
            as_dataframe=False,
            max_workers=1,
            batch_size=100,
        ),
    )
    invoke(
        "klines.batch.1d.adjust_forward",
        3,
        lambda: client.klines.batch(
            list(SYMBOLS),
            period="1d",
            count=1000,
            adjust=SDK_QFQ_ADJUST,
            as_dataframe=False,
            max_workers=1,
            batch_size=100,
        ),
    )
    invoke(
        "klines.ex_factors",
        3,
        lambda: client.klines.ex_factors(
            list(SYMBOLS),
            as_dataframe=False,
            max_workers=1,
            batch_size=100,
        ),
    )
    invoke(
        "quotes.get",
        3,
        lambda: client.quotes.get(
            symbols=list(SYMBOLS),
            as_dataframe=False,
        ),
    )
    for period in ("1m", "30m"):
        for symbol in SYMBOLS:
            capability = f"klines.intraday.{symbol}.{period}"
            invoke(
                capability,
                1,
                lambda symbol=symbol, period=period: client.klines.intraday(
                    symbol,
                    period=period,
                    as_dataframe=False,
                ),
            )
    invoke(
        "depth.get.once",
        1,
        lambda: client.depth.get(SYMBOLS[0]),
    )
    invoke(
        "klines.batch.1d.adjust_forward.repeat",
        3,
        lambda: client.klines.batch(
            list(SYMBOLS),
            period="1d",
            count=1000,
            adjust=SDK_QFQ_ADJUST,
            as_dataframe=False,
            max_workers=1,
            batch_size=100,
        ),
    )
    invoke(
        "klines.ex_factors.repeat",
        3,
        lambda: client.klines.ex_factors(
            list(SYMBOLS),
            as_dataframe=False,
            max_workers=1,
            batch_size=100,
        ),
    )
    return calls


def _boundary_evidence_v1(security_gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "boundary_schema_version": 1,
        "cloud_redeployed": False,
        "real_key_exposed": False,
        "raw_market_values_modified": False,
        "timestamps_shifted": False,
        "missing_minutes_filled": False,
        "ai_configured": False,
        "paper_trading_started": False,
        "integrated_gold_enabled": security_gate.get("integrated_gold_enabled"),
        "integrated_gold_external_send_count": security_gate.get(
            "integrated_gold_external_send_count"
        ),
    }


def _live_result_skeleton(
    *,
    security_gate: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    return {
        "final_status": status,
        "pipeline": PIPELINE,
        "generated_at": _iso_now(),
        "target": {
            "pwa": "https://vm-0-9-ubuntu.tail21c236.ts.net:8443",
            "host_binding": "127.0.0.1:3019",
            "container": "TickFlow_Stock_Panel",
        },
        "security_gate": security_gate,
        "symbols": list(SYMBOLS),
        "sdk": {
            "version": "0.1.24",
            "base_url": "https://api.tickflow.org",
            "max_retries": 0,
            "single_process": True,
            "single_pipeline": True,
        },
        "contracts": {},
        "request_audit": [],
        "attempts": [],
        "http_429_detected": False,
        "boundaries": _boundary_evidence_v1(security_gate),
    }


def run_live(
    *,
    session_valid_confirmed: bool,
    log_scan_passed: bool,
    prior_attempt_local_failure: bool = False,
) -> dict[str, Any]:
    security_gate, gate_status = _security_gate(
        session_valid_confirmed=session_valid_confirmed,
        log_scan_passed=log_scan_passed,
    )
    if gate_status != "PASSED":
        return _live_result_skeleton(
            security_gate=security_gate,
            status=gate_status,
        )

    from tickflow import TickFlow

    from app.secrets_store import get_tickflow_key

    key = get_tickflow_key()
    client = TickFlow(
        api_key=key,
        base_url="https://api.tickflow.org",
        timeout=30.0,
        max_retries=0,
    )
    del key
    auditor = RequestAuditor()
    now = _now_shanghai()

    try:
        calls = _collect_phase11_calls(
            client,
            auditor,
            now=now,
        )
    except RateLimitAbortError:
        result = _live_result_skeleton(
            security_gate=security_gate,
            status="PHASE1_RATE_LIMIT_BLOCKED",
        )
        result["request_audit"] = auditor.records
        result["http_429_detected"] = True
        result["stopped_after_capability"] = (
            auditor.records[-1]["capability"] if auditor.records else None
        )
        if prior_attempt_local_failure:
            result["attempts"].append(
                {
                    "attempt": 1,
                    "status": "LOCAL_VALIDATOR_ERROR_AFTER_REQUESTS",
                    "estimated_high_level_request_count": PRIOR_LOCAL_FAILURE_REQUEST_COUNT,
                    "estimate_basis": (
                        "control flow reached local ex-factor contract evaluation "
                        "after all 12 sequential SDK calls"
                    ),
                    "error_type": "TypeError",
                    "credential_data_recorded": False,
                }
            )
        result["attempts"].append(
            {
                "attempt": 2 if prior_attempt_local_failure else 1,
                "status": "HTTP_429_STOPPED",
                "high_level_request_count": auditor.request_count,
            }
        )
        result["tickflow_request_count"] = auditor.request_count + (
            PRIOR_LOCAL_FAILURE_REQUEST_COUNT if prior_attempt_local_failure else 0
        )
        client.close()
        return result

    client.close()
    value = lambda capability: (  # noqa: E731
        calls[capability].value if capability in calls else None
    )
    instrument_contract = _instrument_contract(
        value("instruments.batch"),
        fetched_at=auditor.fetched_at("instruments.batch"),
    )
    quote_contract = _quote_contract(
        value("quotes.get"),
        now=now,
        fetched_at=auditor.fetched_at("quotes.get"),
    )
    expected_trade_date = quote_contract.get("expected_recent_trade_date")
    daily_contracts: dict[str, Any] = {}
    factor_contracts: dict[str, Any] = {}
    daily_rows: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for symbol in SYMBOLS:
        daily_contract, raw_rows, qfq_rows = _daily_contract(
            symbol,
            _dict_symbol(value("klines.batch.1d.adjust_none"), symbol),
            _dict_symbol(value("klines.batch.1d.adjust_forward"), symbol),
            _dict_symbol(value("klines.batch.1d.adjust_forward.repeat"), symbol),
            expected_trade_date=expected_trade_date,
            fetched_at_raw=auditor.fetched_at("klines.batch.1d.adjust_none"),
            fetched_at_qfq=auditor.fetched_at("klines.batch.1d.adjust_forward"),
            fetched_at_qfq_repeat=auditor.fetched_at("klines.batch.1d.adjust_forward.repeat"),
            now=now,
        )
        daily_contracts[symbol] = daily_contract
        daily_rows[symbol] = (raw_rows, qfq_rows)
        factor_contracts[symbol] = _ex_factor_contract(
            symbol,
            value("klines.ex_factors"),
            value("klines.ex_factors.repeat"),
            raw_rows,
            qfq_rows,
            qfq_repeat_stable=daily_contract["checks"]["qfq_repeat_stable"],
            fetched_at=auditor.fetched_at("klines.ex_factors"),
            fetched_at_repeat=auditor.fetched_at("klines.ex_factors.repeat"),
            now=now,
        )

    minute_symbols: dict[str, Any] = {}
    minute_summary_by_symbol: dict[str, Any] = {}
    for symbol in SYMBOLS:
        minute_contract, minute_summary = _minute_symbol_contract(
            symbol,
            None,
            value(f"klines.intraday.{symbol}.1m"),
            None,
            value(f"klines.intraday.{symbol}.30m"),
            expected_trade_date=expected_trade_date,
            now=now,
            require_historical_permission=False,
        )
        minute_symbols[symbol] = minute_contract
        minute_summary_by_symbol[symbol] = minute_summary

    minute_contract = {
        "contract": "tickflow_phase1_1_minute_observation_v1",
        "passed": all(
            summary["one_minute_passed"]
            and summary["direct_30m_passed"]
            and summary["aggregation_ohlc_passed"]
            and summary["first_bucket_candidate_passed"]
            and summary["vendor_convention_pending"]
            for summary in minute_summary_by_symbol.values()
        ),
        "source": REPORT_SOURCE,
        "requested_range": {
            "mode": "single-symbol current intraday",
            "trade_date": expected_trade_date,
            "calendar_days": 1,
            "twelve_month_backfill": False,
        },
        "timezone": "Asia/Shanghai",
        "intraday_batch_dependency": False,
        "intraday_batch_status": "ENTITLEMENT_PENDING",
        "symbols": minute_symbols,
        "summary_by_symbol": minute_summary_by_symbol,
        "fetched_at": {
            capability: auditor.fetched_at(capability)
            for capability in (
                *(
                    f"klines.intraday.{symbol}.1m"
                    for symbol in SYMBOLS
                ),
                *(
                    f"klines.intraday.{symbol}.30m"
                    for symbol in SYMBOLS
                ),
            )
        },
        "limitations": [
            "No missing minute was synthesized.",
            "The standard continuous bucket and the 09:30-included vendor candidate are both retained.",
            "The 09:30 interpretation remains pending TickFlow confirmation.",
            "SDK unit semantics are inferred from arithmetic because authoritative unit documentation was not present in SDK 0.1.24.",
        ],
        "evaluated_at": now.isoformat(timespec="seconds"),
    }
    depth_contract = _depth_contract(
        value("depth.get.once"),
        symbol=SYMBOLS[0],
        expected_trade_date=expected_trade_date,
        fetched_at=auditor.fetched_at("depth.get.once"),
        now=now,
    )
    daily_passed = all(contract["passed"] for contract in daily_contracts.values())
    factors_passed = all(contract["passed"] for contract in factor_contracts.values())
    one_minute_passed = all(
        summary["one_minute_passed"] for summary in minute_summary_by_symbol.values()
    )
    direct_30m_passed = all(
        summary["direct_30m_passed"] for summary in minute_summary_by_symbol.values()
    )
    aggregate_ohlc_passed = all(
        summary["aggregation_ohlc_passed"]
        for summary in minute_summary_by_symbol.values()
    )
    first_bucket_candidate_passed = all(
        summary["first_bucket_candidate_passed"]
        for summary in minute_summary_by_symbol.values()
    )
    vendor_convention_pending = all(
        summary["vendor_convention_pending"]
        for summary in minute_summary_by_symbol.values()
    )
    components = {
        "stock_basic_info": instrument_contract["passed"],
        "quotes": quote_contract["passed"],
        "daily": daily_passed,
        "ex_factors": factors_passed,
        "one_minute": one_minute_passed,
        "direct_30m": direct_30m_passed,
        "minute_to_30m_ohlc": aggregate_ohlc_passed,
        "first_bucket_09_30_candidate": first_bucket_candidate_passed,
        "depth5": depth_contract["passed"],
    }
    observation_ready = all(components.values()) and vendor_convention_pending
    final_status = (
        "PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING"
        if observation_ready
        else "PHASE1_UPSTREAM_DATA_BLOCKED"
    )
    result = _live_result_skeleton(
        security_gate=security_gate,
        status=final_status,
    )
    result.update(
        {
            "generated_at": now.isoformat(timespec="seconds"),
            "expected_recent_trade_date": expected_trade_date,
            "contracts": {
                "components": components,
                "instruments": instrument_contract,
                "quotes": quote_contract,
                "daily": daily_contracts,
                "ex_factors": factor_contracts,
                "minute_to_30m": minute_contract,
                "depth5": depth_contract,
            },
            "observation_readiness": {
                "ready": observation_ready,
                "vendor_convention_pending": vendor_convention_pending,
                "intraday_batch_dependency": False,
                "intraday_batch_status": "ENTITLEMENT_PENDING",
                "paper_trading_ready": False,
            },
            "request_audit": auditor.records,
            "http_429_detected": auditor.rate_limit_detected,
            "tickflow_request_count": auditor.request_count,
        }
    )
    if prior_attempt_local_failure:
        result["attempts"].append(
            {
                "attempt": 1,
                "status": "LOCAL_VALIDATOR_ERROR_AFTER_REQUESTS",
                "estimated_high_level_request_count": PRIOR_LOCAL_FAILURE_REQUEST_COUNT,
                "estimate_basis": (
                    "control flow reached local ex-factor contract evaluation "
                    "after all 12 sequential SDK calls"
                ),
                "error_type": "TypeError",
                "credential_data_recorded": False,
            }
        )
    result["attempts"].append(
        {
            "attempt": 2 if prior_attempt_local_failure else 1,
            "status": "COMPLETED",
            "high_level_request_count": auditor.request_count,
        }
    )
    result["tickflow_request_count"] = auditor.request_count + (
        PRIOR_LOCAL_FAILURE_REQUEST_COUNT if prior_attempt_local_failure else 0
    )
    return result


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _status_word(value: bool | None) -> str:
    if value is True:
        return "PASSED"
    if value is False:
        return "FAILED"
    return "BLOCKED_NOT_RUN"


def _markdown_table(rows: list[tuple[str, str, str]]) -> str:
    lines = [
        "| 检查项 | 状态 | 证据 |",
        "|---|---|---|",
    ]
    lines.extend(f"| {name} | `{status}` | {evidence} |" for name, status, evidence in rows)
    return "\n".join(lines)


def _minute_component(
    minute_contract: dict[str, Any],
    key: str,
) -> bool | None:
    summaries = minute_contract.get("summary_by_symbol")
    if not isinstance(summaries, dict) or not summaries:
        return None
    return all(bool(value.get(key)) for value in summaries.values())


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _minute_symbol_contracts(result: dict[str, Any]) -> dict[str, Any]:
    contracts = _mapping(result.get("contracts"))
    minute = _mapping(contracts.get("minute_to_30m"))
    return _mapping(minute.get("symbols"))


def _minute_validation(
    result: dict[str, Any],
    symbol: str,
    section: str,
) -> dict[str, Any]:
    symbol_contract = _mapping(_minute_symbol_contracts(result).get(symbol))
    return _mapping(_mapping(symbol_contract.get(section)).get("validation"))


def _combined_dual_convention(result: dict[str, Any]) -> dict[str, Any]:
    symbols: dict[str, Any] = {}
    trade_date: str | None = None
    candidate_matches = 0
    for symbol in SYMBOLS:
        symbol_contract = _mapping(_minute_symbol_contracts(result).get(symbol))
        aggregate = _mapping(symbol_contract.get("local_1m_to_30m"))
        dual = _mapping(aggregate.get("dual_first_bucket"))
        if trade_date is None and isinstance(dual.get("trade_date"), str):
            trade_date = dual["trade_date"]
        detail = _mapping(_mapping(dual.get("symbols")).get(symbol))
        if not detail:
            continue
        symbols[symbol] = detail
        candidate = _mapping(detail.get("vendor_candidate_09_30_to_10_00"))
        candidate_matches += int(
            bool(
                candidate.get("all_fields_match")
                or (
                    candidate.get("ohlc_match")
                    and candidate.get("volume_match")
                    and candidate.get("amount_match")
                )
            )
        )
    if symbols and candidate_matches == len(symbols):
        verdict = "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED"
    elif candidate_matches:
        verdict = "FIRST_BUCKET_CONVENTION_PARTIAL"
    else:
        verdict = "FIRST_BUCKET_CONVENTION_UNRESOLVED"
    return {
        "contract": "tickflow_phase1_1_dual_first_bucket_v1",
        "trade_date": trade_date,
        "continuous_definition": "09:31-10:00",
        "vendor_candidate_definition": "09:30-10:00",
        "symbols": symbols,
        "symbol_count": len(symbols),
        "candidate_match_symbol_count": candidate_matches,
        "verdict": verdict,
        "interpretation": (
            "LIKELY_VENDOR_FIRST_BUCKET_INCLUDES_09_30"
            if verdict == "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED"
            else "VENDOR_FIRST_BUCKET_CONVENTION_UNRESOLVED"
        ),
        "vendor_contract_confirmed": False,
        "timestamps_shifted": False,
        "missing_minutes_filled": False,
        "raw_values_modified": False,
    }


def _numeric_contract_artifacts(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    one_000403 = _minute_validation(result, "000403.SZ", "one_minute")
    direct_000403 = _minute_validation(result, "000403.SZ", "direct_30m")
    one_300059 = _minute_validation(result, "300059.SZ", "one_minute")
    contracts = _mapping(result.get("contracts"))
    factors = _mapping(contracts.get("ex_factors"))
    factor = _mapping(factors.get("000403.SZ"))
    factor_checks = _mapping(factor.get("checks"))
    return {
        "000403SZ_ohlc_violation_distribution.json": {
            "contract": "tickflow_phase1_1_ohlc_numeric_tolerance_v1",
            "symbol": "000403.SZ",
            "name": EXPECTED_NAMES["000403.SZ"],
            "absolute_epsilon": OHLC_ABS_EPSILON,
            "one_minute": _mapping(
                one_000403.get("ohlc_violation_distribution")
            ),
            "direct_30m": _mapping(
                direct_000403.get("ohlc_violation_distribution")
            ),
            "raw_values_modified": False,
            "relative_tolerance_used_for_ohlc_validity": False,
        },
        "300059SZ_amount_tolerance.json": {
            "contract": "tickflow_phase1_1_amount_zero_tolerance_v1",
            "symbol": "300059.SZ",
            "name": EXPECTED_NAMES["300059.SZ"],
            "zero_epsilon": AMOUNT_ZERO_EPSILON,
            "one_minute": _mapping(one_300059.get("amount_tolerance")),
            "raw_values_modified": False,
            "negative_values_zeroed": False,
        },
        "000403SZ_ex_factor_final.json": {
            "contract": "tickflow_phase1_1_ex_factor_final_v1",
            "symbol": "000403.SZ",
            "name": EXPECTED_NAMES["000403.SZ"],
            "EX_FACTOR_CONTRACT": "PASSED" if factor.get("passed") else "FAILED",
            "OLD_FAILURE": "VALIDATOR_ERROR",
            "factor_count": factor.get("factor_count"),
            "factor_dates": factor.get("factor_dates"),
            "checks": {
                "factor_timestamps_valid": factor_checks.get(
                    "factor_timestamps_valid"
                ),
                "factor_dates_unique": factor_checks.get("factor_dates_unique"),
                "factor_dates_sorted": factor_checks.get("factor_dates_sorted"),
                "factor_values_positive": factor_checks.get(
                    "factor_values_positive"
                ),
                "repeat_result_stable": factor_checks.get(
                    "repeat_result_stable"
                ),
                "qfq_repeat_result_stable": factor_checks.get(
                    "qfq_repeat_result_stable"
                ),
                "qfq_relation_passed": factor_checks.get(
                    "qfq_relation_passed"
                ),
                "qfq_relation": factor_checks.get("qfq_relation"),
            },
            "upstream_order_preserved": True,
            "raw_values_modified": False,
        },
        "minute_30m_dual_convention_validation.json": _combined_dual_convention(
            result
        ),
    }


def _report_markdown(
    result: dict[str, Any],
    *,
    cumulative_request_count: int | None = None,
) -> str:
    final_status = str(
        result.get(
            "final_status",
            "PHASE1_VALIDATOR_FIXED_PENDING_RECHECK",
        )
    )
    contracts = _mapping(result.get("contracts"))
    components = _mapping(contracts.get("components"))
    security = _mapping(result.get("security_gate"))
    minute = _mapping(contracts.get("minute_to_30m"))
    factors = _mapping(contracts.get("ex_factors"))
    factor = _mapping(factors.get("000403.SZ"))
    artifacts = _numeric_contract_artifacts(result)
    ohlc = artifacts["000403SZ_ohlc_violation_distribution.json"]
    one_ohlc = _mapping(ohlc.get("one_minute"))
    direct_ohlc = _mapping(ohlc.get("direct_30m"))
    one_stats = _mapping(one_ohlc.get("row_violation_stats"))
    direct_stats = _mapping(direct_ohlc.get("row_violation_stats"))
    thresholds = _mapping(one_ohlc.get("threshold_counts"))
    amount = _mapping(
        artifacts["300059SZ_amount_tolerance.json"].get("one_minute")
    )
    dual = artifacts["minute_30m_dual_convention_validation.json"]
    observation = _mapping(result.get("observation_readiness"))
    postprocess = _mapping(result.get("validator_postprocess"))
    original_live_status = postprocess.get("original_live_status")
    postprocess_note = (
        "正式 live 原始判定为 "
        f"`{original_live_status}`；该状态来自本地校验器先判连续首桶、"
        "后计算 09:30 候选的顺序错误。专项测试修复后，仅用同一份已采集证据"
        f"重算为 `{final_status}`，API 未重跑，原始数据未修改。"
        if original_live_status and original_live_status != final_status
        else "正式 live 与本地物化判定一致；未执行额外 API 重跑。"
    )
    audit = result.get("request_audit")
    audit = audit if isinstance(audit, list) else []
    run_request_count = int(result.get("tickflow_request_count", len(audit)))
    request_count_line = (
        f"{run_request_count}（本轮）/ {cumulative_request_count}（累计）"
        if cumulative_request_count is not None
        else str(run_request_count)
    )
    factor_passed = factor.get("passed") is True
    one_classification = one_ohlc.get("classification")
    direct_classification = direct_ohlc.get("classification")
    amount_classification = amount.get("classification")
    dual_matches = (
        dual.get("verdict") == "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED"
    )
    observation_ready = bool(observation.get("ready"))
    component_rows = [
        (
            "日线",
            _status_word(components.get("daily")),
            "原始与 qfq 日线合同",
        ),
        (
            "除权因子",
            _status_word(components.get("ex_factors")),
            f"000403.SZ factor_count={factor.get('factor_count')}",
        ),
        (
            "单标的 1m",
            _status_word(components.get("one_minute")),
            "三票 intraday 串行请求",
        ),
        (
            "直接 30m",
            _status_word(components.get("direct_30m")),
            "三票 intraday 串行请求",
        ),
        (
            "1m→30m OHLC",
            _status_word(components.get("minute_to_30m_ohlc")),
            "不移动时间戳、不填补分钟",
        ),
        (
            "09:30 首桶候选",
            _status_word(components.get("first_bucket_09_30_candidate")),
            str(dual.get("verdict")),
        ),
        (
            "五档一次性合同",
            _status_word(components.get("depth5")),
            "只检查字段与新鲜度，不撮合",
        ),
    ]
    return f"""# TickFlow Phase 1.1 分钟数据合同收口

核验时间：{result.get("generated_at")}

## 1. 最终状态

```text
{final_status}
```

当前结论只允许启动三票、3—5 个交易日的分钟数据观察；供应商首桶、单位和
`intraday_batch` 权限口径仍待确认。它不代表 Paper Trading、交易或生产就绪。

{postprocess_note}

## 2. 数值合同

| 项目 | 结论 | 关键证据 |
|---|---|---|
| 派林生物 1m OHLC | `{one_classification}` | 异常行={one_ohlc.get("anomaly_row_count")}；max={one_stats.get("max")}；epsilon={OHLC_ABS_EPSILON} |
| 派林生物 30m OHLC | `{direct_classification}` | 异常行={direct_ohlc.get("anomaly_row_count")}；max={direct_stats.get("max")}；epsilon={OHLC_ABS_EPSILON} |
| 东方财富 amount | `{amount_classification}` | warning={amount.get("epsilon_warning_count")}；failed={amount.get("failed_count")}；epsilon={AMOUNT_ZERO_EPSILON} |
| 派林生物除权因子 | `{"PASSED" if factor_passed else "FAILED"}` | count={factor.get("factor_count")}；旧失败=`VALIDATOR_ERROR` |
| 首桶双口径 | `{dual.get("verdict")}` | candidate={dual.get("candidate_match_symbol_count")}/{dual.get("symbol_count")}；官方确认=false |

原始 OHLCV、amount、时间戳和上游顺序均未修改。没有把负 amount 归零、填补
缺失分钟、删除数据行或扩大业务异常阈值。OHLC 合同仅使用绝对容差
`1e-10`；amount 零值容差为 `1e-6`。

## 3. 合同总览

{_markdown_table(component_rows)}

`intraday_batch` 状态为 `{minute.get("intraday_batch_status")}`，但
`intraday_batch_dependency={str(minute.get("intraday_batch_dependency")).lower()}`；
当前三票使用单标的 `intraday`，因此该扩展权限不阻塞观察。

## 4. 11 项强制回答

1. 218 条 OHLC 最大越界：`{one_stats.get("max")}`。
2. 超过 `1e-10`：`{thresholds.get("gt_1e_10")}` 条。
3. 超过 `0.001`：`{thresholds.get("gt_0_001")}` 条；大于等于 `0.001` 为 `{thresholds.get("ge_0_001")}` 条。
4. 大于等于 `0.01`：`{thresholds.get("ge_0_01")}` 条。
5. 东方财富负 amount：`{amount_classification}`；原始负值保留，未归零。
6. 除权因子：`{"PASSED" if factor_passed else "FAILED"}`；旧失败确认是本地日期校验器错误。
7. 09:30 首桶：`{"三票均可机械解释量额差异" if dual_matches else "未能解释全部三票差异"}`，但不是供应商合同确认。
8. 单标的分钟合同：`{"可进入连续观察" if observation_ready else "尚不可进入连续观察"}`。
9. `intraday_batch`：仍是扩展权限问题，不是当前三票执行依赖。
10. 3—5 日观察资格：`{"READY" if observation_ready else "BLOCKED"}`。
11. Paper Trading 资格：`NOT_STARTED / NOT_READY`。

## 5. 请求与安全边界

```text
requests={request_count_line}
single_process=YES
single_pipeline=YES
SDK_retry_count=0
HTTP_429={"DETECTED" if result.get("http_429_detected") else "NONE"}
AI=NOT_CONFIGURED
Paper Trading=NOT_STARTED
cloud_redeploy=NO
Integrated Gold=DISABLED / external_send_count=0
real_key=NOT_EXPOSED
```

认证闸门仅保留布尔值和文件元数据：configured=
`{str(security.get("authentication_configured")).lower()}`，session=
`{_status_word(security.get("current_https_session_valid"))}`，TickFlow Key=
`{"CONFIGURED" if security.get("has_tickflow_key") else "BLOCKED"}`，AI Key=
`{"PRESENT" if security.get("has_ai_key") else "NOT_CONFIGURED"}`。报告不包含密码、
Cookie、Session、Authorization header、完整 API Key 或完整 Settings 响应。

## 6. 适用边界

本结论只覆盖 000403.SZ、600489.SH、300059.SZ 的本次收盘后样本。它不证明
全市场、长期稳定性、交易可用性或官方 30m 桶语义。观察阶段尚未自动启动。

证据：

```text
reports/phase1_numeric_contracts/
reports/phase1_data_contracts/
reports/phase1_tickflow_request_audit.json
reports/tickflow_vendor_support_ticket.md
```
"""


def _vendor_ticket_markdown(result: dict[str, Any]) -> str:
    artifacts = _numeric_contract_artifacts(result)
    ohlc = artifacts["000403SZ_ohlc_violation_distribution.json"]
    amount = artifacts["300059SZ_amount_tolerance.json"]
    one = _mapping(ohlc.get("one_minute"))
    direct = _mapping(ohlc.get("direct_30m"))
    one_stats = _mapping(one.get("row_violation_stats"))
    direct_stats = _mapping(direct.get("row_violation_stats"))
    amount_one = _mapping(amount.get("one_minute"))
    return f"""# TickFlow Pro 数据合同问题工单草稿

> 本文件仅为本地草稿，未自动发送。

## 脱敏复现范围

- SDK：0.1.24
- 日期：{result.get("generated_at")}
- 时区：Asia/Shanghai
- 标的：000403.SZ、600489.SH、300059.SZ
- 请求：单进程、单 pipeline、SDK 自动重试关闭
- 原始数据：未修改、未填补、未移动时间戳、负数未归零

## 已确认的本地结论

- 000403.SZ 1m OHLC：`{one.get("classification")}`，异常行
  `{one.get("anomaly_row_count")}`，最大越界 `{one_stats.get("max")}`。
- 000403.SZ 30m OHLC：`{direct.get("classification")}`，异常行
  `{direct.get("anomaly_row_count")}`，最大越界 `{direct_stats.get("max")}`。
- 300059.SZ amount：`{amount_one.get("classification")}`，原始负值
  `{amount_one.get("negative_values")}`。
- 上述现象由 SDK 路径一致返回，按浮点尾差记录；校验器只应用
  `OHLC_ABS_EPSILON={OHLC_ABS_EPSILON}` 和
  `AMOUNT_ZERO_EPSILON={AMOUNT_ZERO_EPSILON}`，未改写值。
- 000403.SZ 除权因子旧失败已确认是本地日期解析器错误，不再作为供应商问题。

## 请求官方确认

1. Pro 套餐是否包含 `intraday_batch`？
2. 单标的 `intraday` 与 `intraday_batch` 权限为何不同？
3. 第一根 30 分钟 K 是否包含 09:30 数据？
4. `volume` 的权威单位是股、手还是其他？
5. `amount` 的权威单位是什么？
6. 官方推荐的 OHLC/amount 浮点精度容差是什么？
7. 30 分钟首桶时间标签、边界及集合竞价口径是什么？

本草稿不包含 API Key、Authorization、Cookie、Session、用户密码、内部地址、
服务器信息或完整原始响应，且不会自动发送。
"""


def _auth_readiness_markdown(result: dict[str, Any]) -> str:
    security = result.get("security_gate")
    security = security if isinstance(security, dict) else {}
    return f"""# TickFlow Phase 1 认证与 Secret Readiness

核验时间：{result.get("generated_at")}

## 状态

```text
{"READY" if security.get("passed") else "BLOCKED"}
```

| 项 | 结果 |
|---|---|
| authentication configured | `{str(security.get("authentication_configured")).lower()}` |
| current HTTPS Session | `{_status_word(security.get("current_https_session_valid"))}` |
| has TickFlow Key | `{str(security.get("has_tickflow_key")).lower()}` |
| masked key exists | `{str(security.get("masked_tickflow_key_present")).lower()}` |
| has AI Key | `{str(security.get("has_ai_key")).lower()}` |
| auth.json | regular=`{security.get("auth_json", {}).get("regular_file")}`, mode=`{security.get("auth_json", {}).get("mode")}` |
| secrets.json | regular=`{security.get("secrets_json", {}).get("regular_file")}`, mode=`{security.get("secrets_json", {}).get("mode")}` |
| top-level symlink count | `{security.get("user_data_top_level_symlink_count")}` |
| sensitive log shape scan | `{_status_word(security.get("recent_log_credential_shape_scan_passed"))}` |
| Integrated Gold enabled | `{str(security.get("integrated_gold_enabled")).lower()}` |
| Integrated Gold external send count | `{security.get("integrated_gold_external_send_count")}` |

核验只记录布尔值、owner/mode 等文件元数据和敏感形态计数。没有读取、回显或
记录密码、Cookie、Session、Authorization header 或完整 API Key。
"""


def _apply_phase11_contract_outcomes(result: dict[str, Any]) -> None:
    contracts = _mapping(result.get("contracts"))
    minute = _mapping(contracts.get("minute_to_30m"))
    if minute.get("intraday_batch_dependency") is not False:
        return
    symbols = _mapping(minute.get("symbols"))
    summaries = _mapping(minute.get("summary_by_symbol"))
    if not symbols or not summaries:
        return

    for symbol in SYMBOLS:
        symbol_contract = _mapping(symbols.get(symbol))
        aggregate = _mapping(symbol_contract.get("local_1m_to_30m"))
        selected = _mapping(aggregate.get("selected_comparison"))
        dual = _mapping(aggregate.get("dual_first_bucket"))
        summary = _mapping(summaries.get(symbol))
        if (
            not symbol_contract
            or not aggregate
            or not selected
            or not dual
            or not summary
        ):
            continue
        ohlc_passed = _ohlc_contract_with_09_30_candidate(
            selected,
            dual,
            symbol,
        )
        candidate_passed = (
            dual.get("verdict")
            == "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED"
        )
        fields = _mapping(selected.get("fields"))
        sums_pending = all(
            _mapping(fields.get(field)).get("compared") == 8
            and _mapping(fields.get(field)).get("matched") == 7
            and _field_reconciled_by_09_30_candidate(
                selected,
                dual,
                symbol,
                field,
            )
            for field in ("volume", "amount")
        )
        vendor_pending = bool(
            ohlc_passed and candidate_passed and sums_pending
        )
        aggregate["passed"] = ohlc_passed and candidate_passed
        aggregate["ohlc_contract"] = "PASSED" if ohlc_passed else "FAILED"
        aggregate["volume_amount_first_bucket"] = (
            "VENDOR_CONVENTION_PENDING"
            if vendor_pending
            else "UNRESOLVED"
        )
        summary["one_minute_passed"] = bool(
            _mapping(symbol_contract.get("one_minute")).get("passed")
        )
        summary["direct_30m_passed"] = bool(
            _mapping(symbol_contract.get("direct_30m")).get("passed")
        )
        summary["aggregation_ohlc_passed"] = ohlc_passed
        summary["first_bucket_candidate_passed"] = candidate_passed
        summary["vendor_convention_pending"] = vendor_pending

    all_symbols_present = all(symbol in summaries for symbol in SYMBOLS)
    one_minute_passed = all_symbols_present and all(
        bool(_mapping(summaries.get(symbol)).get("one_minute_passed"))
        for symbol in SYMBOLS
    )
    direct_30m_passed = all_symbols_present and all(
        bool(_mapping(summaries.get(symbol)).get("direct_30m_passed"))
        for symbol in SYMBOLS
    )
    aggregate_ohlc_passed = all_symbols_present and all(
        bool(
            _mapping(summaries.get(symbol)).get(
                "aggregation_ohlc_passed"
            )
        )
        for symbol in SYMBOLS
    )
    first_bucket_passed = all_symbols_present and all(
        bool(
            _mapping(summaries.get(symbol)).get(
                "first_bucket_candidate_passed"
            )
        )
        for symbol in SYMBOLS
    )
    vendor_pending = all_symbols_present and all(
        bool(
            _mapping(summaries.get(symbol)).get(
                "vendor_convention_pending"
            )
        )
        for symbol in SYMBOLS
    )
    components = _mapping(contracts.get("components"))
    components["one_minute"] = one_minute_passed
    components["direct_30m"] = direct_30m_passed
    components["minute_to_30m_ohlc"] = aggregate_ohlc_passed
    components["first_bucket_09_30_candidate"] = first_bucket_passed
    observation_ready = bool(
        components
        and all(value is True for value in components.values())
        and vendor_pending
        and not result.get("http_429_detected")
    )
    minute["passed"] = bool(
        one_minute_passed
        and direct_30m_passed
        and aggregate_ohlc_passed
        and first_bucket_passed
        and vendor_pending
    )
    observation = _mapping(result.get("observation_readiness"))
    observation.update(
        {
            "ready": observation_ready,
            "vendor_convention_pending": vendor_pending,
            "intraday_batch_dependency": False,
            "intraday_batch_status": "ENTITLEMENT_PENDING",
            "paper_trading_ready": False,
        }
    )
    result["observation_readiness"] = observation
    original_status = str(result.get("final_status"))
    if original_status in {
        "PHASE1_UPSTREAM_DATA_BLOCKED",
        "PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING",
    }:
        result["final_status"] = (
            "PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING"
            if observation_ready
            else "PHASE1_UPSTREAM_DATA_BLOCKED"
        )
    result["validator_postprocess"] = {
        "original_live_status": original_status,
        "recalculated_status": result.get("final_status"),
        "rule": (
            "A field that mismatches only in the observed 09:31-10:00 first "
            "bucket may be reconciled when the unmodified 09:30-10:00 "
            "candidate matches the direct vendor bar."
        ),
        "raw_values_modified": False,
        "timestamps_shifted": False,
        "missing_minutes_filled": False,
    }


def _apply_endpoint_capability_outcomes(result: dict[str, Any]) -> None:
    audit = result.get("request_audit")
    if not isinstance(audit, list):
        return
    by_capability = {
        record.get("capability"): record
        for record in audit
        if isinstance(record, dict) and record.get("capability")
    }
    contracts = result.get("contracts")
    if not isinstance(contracts, dict):
        return
    minute = contracts.get("minute_to_30m")
    if not isinstance(minute, dict):
        return
    if minute.get("intraday_batch_dependency") is False:
        return
    symbols = minute.get("symbols")
    summaries = minute.get("summary_by_symbol")
    if not isinstance(symbols, dict) or not isinstance(summaries, dict):
        return

    required = {
        "historical_1m": "klines.batch.1m.historical",
        "current_1m": "klines.intraday_batch.1m.current",
        "historical_30m": "klines.batch.30m.direct",
        "current_30m": "klines.intraday_batch.30m.current",
    }
    endpoint_contracts: dict[str, Any] = {}
    for label, capability in required.items():
        record = by_capability.get(capability)
        endpoint_contracts[label] = {
            "capability": capability,
            "passed": bool(record and record.get("status") == "PASSED"),
            "status": record.get("status") if record else "NOT_RUN",
            "error": record.get("error") if record else None,
        }
    minute["endpoint_capabilities"] = endpoint_contracts
    for _symbol, contract in symbols.items():
        if not isinstance(contract, dict):
            continue
        one = contract.get("one_minute")
        direct = contract.get("direct_30m")
        aggregate = contract.get("local_1m_to_30m")
        if isinstance(one, dict):
            one["data_contract_passed_using_available_data"] = bool(one.get("passed"))
            one["historical_batch_endpoint_passed"] = endpoint_contracts["historical_1m"]["passed"]
            one["dedicated_current_endpoint_passed"] = endpoint_contracts["current_1m"]["passed"]
            one["current_endpoint_status"] = endpoint_contracts["current_1m"]["status"]
        if isinstance(direct, dict):
            direct["data_contract_passed_using_available_data"] = bool(direct.get("passed"))
            direct["historical_batch_endpoint_passed"] = endpoint_contracts["historical_30m"][
                "passed"
            ]
            direct["dedicated_current_endpoint_passed"] = endpoint_contracts["current_30m"][
                "passed"
            ]
            direct["current_endpoint_status"] = endpoint_contracts["current_30m"]["status"]
        if isinstance(aggregate, dict):
            aggregate["source_batch_endpoints_passed"] = (
                endpoint_contracts["historical_1m"]["passed"]
                and endpoint_contracts["historical_30m"]["passed"]
            )

    components = contracts.get("components")
    if isinstance(components, dict):
        components["one_minute"] = all(
            bool(value.get("one_minute_passed")) for value in summaries.values()
        )
        components["direct_30m"] = all(
            bool(value.get("direct_30m_passed")) for value in summaries.values()
        )
        components["minute_to_30m"] = all(
            bool(value.get("aggregation_passed")) for value in summaries.values()
        )
        if result.get("final_status") in {
            "PHASE1_DATA_CONTRACT_PASSED",
            "PHASE1_DATA_BLOCKED",
        }:
            result["final_status"] = (
                "PHASE1_DATA_CONTRACT_PASSED"
                if all(value is True for value in components.values())
                else "PHASE1_DATA_BLOCKED"
            )
    minute["passed"] = all(
        bool(value.get("one_minute_passed"))
        and bool(value.get("direct_30m_passed"))
        and bool(value.get("aggregation_passed"))
        for value in summaries.values()
    )


def _merge_request_audit(
    reports_root: Path,
    result: dict[str, Any],
) -> dict[str, Any]:
    audit_path = reports_root / "phase1_tickflow_request_audit.json"
    prior: dict[str, Any] = {}
    if audit_path.is_file():
        try:
            loaded = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            prior = loaded
    prior_records = prior.get("records")
    prior_records = prior_records if isinstance(prior_records, list) else []
    new_records = result.get("request_audit")
    new_records = new_records if isinstance(new_records, list) else []
    prior_count = int(prior.get("tickflow_request_count", len(prior_records)))
    new_count = int(result.get("tickflow_request_count", len(new_records)))
    existing_phase11 = _mapping(prior.get("phase1_1"))
    existing_records = existing_phase11.get("records")
    existing_records = (
        existing_records if isinstance(existing_records, list) else []
    )
    same_phase11_run = bool(
        existing_phase11.get("generated_at") == result.get("generated_at")
        and existing_phase11.get("pipeline") == PIPELINE
    )
    if (
        same_phase11_run
        and existing_records
        and prior_records[-len(existing_records) :] == existing_records
    ):
        prior_records = prior_records[: -len(existing_records)]
        prior_count = max(
            0,
            prior_count
            - int(
                existing_phase11.get(
                    "request_count",
                    len(existing_records),
                )
            ),
        )
    prior_pipeline_value = prior.get("pipelines")
    if isinstance(prior_pipeline_value, list):
        prior_pipeline_count = len(prior_pipeline_value)
        prior_pipeline_evidence = prior_pipeline_value
    elif isinstance(prior_pipeline_value, (int, float)):
        prior_pipeline_count = int(prior_pipeline_value)
        prior_pipeline_evidence = []
    else:
        prior_pipeline_count = 1 if prior_count else 0
        prior_pipeline_evidence = []
    if same_phase11_run:
        prior_pipeline_count = max(0, prior_pipeline_count - 1)
    breakdown = prior.get("request_count_breakdown")
    breakdown = dict(breakdown) if isinstance(breakdown, dict) else {}
    if same_phase11_run:
        breakdown.pop("phase1_1_minute_contract_closeout", None)
    breakdown["phase1_1_minute_contract_closeout"] = new_count
    security = _mapping(result.get("security_gate"))
    return {
        "checked_at": result.get("generated_at"),
        "final_status": result.get("final_status"),
        "pipeline": PIPELINE,
        "pipelines": prior_pipeline_count + 1,
        "prior_pipeline_evidence": prior_pipeline_evidence,
        "symbol_count": len(SYMBOLS),
        "requested_symbols": list(SYMBOLS),
        "phase1_1_request_count": new_count,
        "tickflow_request_count": prior_count + new_count,
        "request_count_breakdown": breakdown,
        "http_429_detected": bool(
            prior.get("http_429_detected")
            or result.get("http_429_detected")
        ),
        "retry_count": 0,
        "rate_limit_wait_ms": 0,
        "attempts": result.get("attempts", []),
        "records": [*prior_records, *new_records],
        "phase1_1": {
            "generated_at": result.get("generated_at"),
            "pipeline": PIPELINE,
            "request_count": new_count,
            "records": new_records,
            "attempts": result.get("attempts", []),
            "http_429_detected": result.get("http_429_detected", False),
            "intraday_batch_dependency": False,
            "credential_values_recorded": False,
            "validator_postprocess": result.get("validator_postprocess"),
        },
        "gates": security,
        "boundaries": result.get("boundaries"),
        "cloud_redeployed": False,
        "credential_values_recorded": False,
        "integrated_gold_enabled": False,
        "integrated_gold_external_send_count": 0,
        "real_key_exposed": False,
    }


def materialize(result_path: Path, reports_root: Path) -> dict[str, Any]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("live result must be a JSON object")
    _apply_endpoint_capability_outcomes(result)
    _apply_phase11_contract_outcomes(result)
    final_status = result.get("final_status")
    allowed = {
        "PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING",
        "PHASE1_DATA_CONTRACT_PASSED",
        "PHASE1_UPSTREAM_DATA_BLOCKED",
        "PHASE1_VALIDATOR_FIXED_PENDING_RECHECK",
        "PHASE1_RATE_LIMIT_BLOCKED",
    }
    if final_status not in allowed:
        raise ValueError("live result has an unsupported final status")

    contracts_dir = reports_root / "phase1_data_contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    contracts = result.get("contracts")
    contracts = contracts if isinstance(contracts, dict) else {}
    daily = contracts.get("daily")
    daily = daily if isinstance(daily, dict) else {}
    factors = contracts.get("ex_factors")
    factors = factors if isinstance(factors, dict) else {}
    for symbol in SYMBOLS:
        compact = symbol.replace(".", "")
        if symbol in daily:
            _write_json(
                contracts_dir / f"{compact}_daily_contract.json",
                daily[symbol],
            )
        if symbol in factors:
            _write_json(
                contracts_dir / f"{compact}_ex_factors_contract.json",
                factors[symbol],
            )
    if "minute_to_30m" in contracts:
        _write_json(
            contracts_dir / "minute_to_30m_validation.json",
            contracts["minute_to_30m"],
        )
    if "depth5" in contracts:
        _write_json(
            contracts_dir / "depth5_contract.json",
            contracts["depth5"],
        )
    if "instruments" in contracts or "quotes" in contracts:
        _write_json(
            contracts_dir / "instrument_and_quote_contract.json",
            {
                "instruments": contracts.get("instruments"),
                "quotes": contracts.get("quotes"),
            },
        )

    numeric_dir = reports_root / "phase1_numeric_contracts"
    numeric_dir.mkdir(parents=True, exist_ok=True)
    numeric_artifacts = _numeric_contract_artifacts(result)
    for filename, artifact in numeric_artifacts.items():
        _write_json(numeric_dir / filename, artifact)

    security = result.get("security_gate")
    security = security if isinstance(security, dict) else {}
    gate_status = {
        "checked_at": security.get("checked_at"),
        "status": "PASSED" if security.get("passed") else final_status,
        "target": result.get("target"),
        "authentication": {
            "configured": security.get("authentication_configured"),
            "current_session_valid": security.get("current_https_session_valid"),
        },
        "tickflow_pro": {
            "has_tickflow_key": security.get("has_tickflow_key"),
            "masked_key_present": security.get("masked_tickflow_key_present"),
            "has_ai_key": security.get("has_ai_key"),
        },
        "file_metadata": {
            "auth.json": security.get("auth_json"),
            "secrets.json": security.get("secrets_json"),
        },
        "user_data": {
            "top_level_symlink_count": security.get("user_data_top_level_symlink_count"),
            "sensitive_top_level_symlink_count": security.get("sensitive_top_level_symlink_count"),
        },
        "log_scan_passed": security.get("recent_log_credential_shape_scan_passed"),
        "integrated_gold": {
            "enabled": security.get("integrated_gold_enabled"),
            "external_send_count": security.get("integrated_gold_external_send_count"),
        },
        "requested_symbols": list(SYMBOLS),
        "tickflow_requests_made": result.get("tickflow_request_count", 0),
        "contracts_generated": len(list(contracts_dir.glob("*_contract.json"))),
        "real_key_read_for_display": False,
        "real_key_exposed": False,
        "cloud_redeployed": False,
    }
    _write_json(contracts_dir / "gate_status.json", gate_status)

    request_audit = _merge_request_audit(reports_root, result)
    _write_json(
        reports_root / "phase1_tickflow_request_audit.json",
        request_audit,
    )
    closeout_markdown = _report_markdown(
        result,
        cumulative_request_count=request_audit["tickflow_request_count"],
    )
    (reports_root / "tickflow_phase1_numeric_contract_closeout.md").write_text(
        closeout_markdown,
        encoding="utf-8",
    )
    (reports_root / "tickflow_phase1_data_contract_eval.md").write_text(
        closeout_markdown.replace(
            "# TickFlow Phase 1.1 分钟数据合同收口",
            "# TickFlow Phase 1 Pro 数据合同验收\n\n"
            "> 本文件已由 Phase 1.1 数值合同收口结果更新。",
            1,
        ),
        encoding="utf-8",
    )
    (reports_root / "tickflow_vendor_support_ticket.md").write_text(
        _vendor_ticket_markdown(result),
        encoding="utf-8",
    )
    (reports_root / "phase1_auth_secret_readiness.md").write_text(
        _auth_readiness_markdown(result),
        encoding="utf-8",
    )
    return {
        "status": final_status,
        "main_report": str(
            reports_root / "tickflow_phase1_numeric_contract_closeout.md"
        ),
        "data_contract_report": str(
            reports_root / "tickflow_phase1_data_contract_eval.md"
        ),
        "vendor_ticket": str(reports_root / "tickflow_vendor_support_ticket.md"),
        "numeric_contracts_dir": str(numeric_dir),
        "contracts_dir": str(contracts_dir),
        "request_audit": str(reports_root / "phase1_tickflow_request_audit.json"),
    }


def _compact_from_rows(rows: list[dict[str, Any]]) -> dict[str, list[Any]]:
    return {
        field: [row[field] for row in rows]
        for field in (
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        )
    }


def run_self_test() -> dict[str, Any]:
    assert _normalize_trade_date(20260727) == "2026-07-27"
    assert _normalize_trade_date(1785081600) is not None
    assert _normalize_trade_date(1785081600000) is not None
    assert _normalize_trade_date("2026-07-27") == "2026-07-27"
    day = date(2026, 7, 27)
    minute_rows: list[dict[str, Any]] = []
    for bucket in range(8):
        label = _bucket_label(day, bucket, "end_labeled")
        start = label - timedelta(minutes=29)
        for index in range(30):
            timestamp = start + timedelta(minutes=index)
            base = 10 + bucket * 0.1 + index * 0.001
            minute_rows.append(
                {
                    "timestamp": int(timestamp.timestamp() * 1000),
                    "open": base,
                    "high": base + 0.02,
                    "low": base - 0.02,
                    "close": base + 0.01,
                    "volume": 100 + index,
                    "amount": (100 + index) * (base + 0.005),
                }
            )
    aggregate = _aggregate_30m(
        minute_rows,
        trade_date=day.isoformat(),
        convention="end_labeled",
    )
    direct = [
        {
            key: value
            for key, value in row.items()
            if key in ("timestamp", "open", "high", "low", "close", "volume", "amount")
        }
        for row in aggregate["bars"]
    ]
    comparison = _compare_aggregated_to_direct(aggregate, direct)
    assert comparison["passed"]

    daily_dates = (date(2026, 7, 24), date(2026, 7, 27))
    raw = [
        {
            "timestamp": int(datetime.combine(value, daytime(15, 0), SHANGHAI).timestamp() * 1000),
            "open": 100.0 if index == 0 else 50.0,
            "high": 101.0 if index == 0 else 51.0,
            "low": 99.0 if index == 0 else 49.0,
            "close": 100.0 if index == 0 else 50.0,
            "volume": 1000,
            "amount": 100000,
        }
        for index, value in enumerate(daily_dates)
    ]
    factors = [
        {
            "timestamp": raw[1]["timestamp"],
            "trade_date": daily_dates[1].isoformat(),
            "ex_factor": 2.0,
        }
    ]
    qfq = _expected_qfq_rows(raw, factors)
    relation = _comparison_stats(qfq, qfq, ("open", "high", "low", "close"))
    assert relation["all_price_fields_match_rate"] == 1.0

    class Fake429Error(Exception):
        status_code = 429
        code = "RATE_LIMITED"

    auditor = RequestAuditor()
    try:
        auditor.call(
            "self_test_429",
            1,
            lambda: (_ for _ in ()).throw(Fake429Error()),
        )
    except RateLimitAbortError:
        pass
    else:
        raise AssertionError("429 did not abort")
    assert auditor.rate_limit_detected
    assert auditor.records[-1]["retry_count"] == 0
    return {
        "status": "SELF_TEST_OK",
        "aggregate_30m": True,
        "qfq_relation": True,
        "factor_date_normalization": True,
        "first_429_aborts": True,
        "secret_values_used": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--materialize", type=Path)
    mode.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--session-valid-confirmed",
        action="store_true",
        help="Operator assertion from an ephemeral authenticated HTTPS probe.",
    )
    parser.add_argument(
        "--log-scan-passed",
        action="store_true",
        help="Operator assertion from the immediately preceding sanitized log scan.",
    )
    parser.add_argument(
        "--prior-attempt-local-failure",
        action="store_true",
        help=(
            "Account for the preceding 12-call attempt that reached local "
            "contract evaluation before a validator TypeError."
        ),
    )
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=None,
    )
    args = parser.parse_args(argv)
    if args.self_test:
        print(json.dumps(run_self_test(), sort_keys=True))
        return 0
    if args.materialize:
        if args.reports_root is not None:
            reports_root = args.reports_root
        elif Path.cwd().name == "backend":
            reports_root = Path.cwd().parent / "reports"
        else:
            reports_root = Path.cwd() / "reports"
        output = materialize(args.materialize, reports_root)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0
    result = run_live(
        session_valid_confirmed=args.session_valid_confirmed,
        log_scan_passed=args.log_scan_passed,
        prior_attempt_local_failure=args.prior_attempt_local_failure,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return (
        0
        if result["final_status"]
        in {
            "PHASE1_DATA_CONTRACT_PASSED",
            "PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING",
        }
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
