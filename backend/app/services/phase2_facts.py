"""Deterministic Phase 2A stock Facts construction and validation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

FACTS_SCHEMA_VERSION = 1
TRADE_DATE = "2026-07-31"
TIMEZONE = "Asia/Shanghai"
FIXED_SYMBOLS = {
    "000403.SZ": "派林生物",
    "600489.SH": "中金黄金",
    "300059.SZ": "东方财富",
}
VENDOR_PENDING = [
    "intraday_batch_entitlement",
    "first_30m_bucket_includes_09_30",
    "volume_unit",
    "amount_unit",
]
MISSING_SOURCE_INPUTS = [
    "retained_raw_daily_ohlcv_values",
    "retained_qfq_daily_ohlcv_series",
]
INDICATOR_COLUMNS = {
    "ma5": "ma5",
    "ma10": "ma10",
    "ma20": "ma20",
    "ma60": "ma60",
    "macd_dif": "macd_dif",
    "macd_dea": "macd_dea",
    "macd_hist": "macd_hist",
    "rsi6": "rsi_6",
    "rsi14": "rsi_14",
    "boll_upper": "boll_upper",
    "boll_middle": "ma20",
    "boll_lower": "boll_lower",
    "atr14": "atr_14",
    "volume_ma5": "vol_ma5",
    "volume_ma10": "vol_ma10",
    "volume_ratio": "vol_ratio_5d",
}


class Phase2FactsError(ValueError):
    """Raised when retained Phase 1 evidence cannot support a Facts document."""


def _reject_json_constant(value: str) -> None:
    raise Phase2FactsError(f"non-finite JSON constant: {value}")


def _safe_source_path(repo_root: Path, relative: str) -> Path:
    root = repo_root.resolve(strict=True)
    candidate = repo_root / relative
    if candidate.is_symlink():
        raise Phase2FactsError(f"source evidence must not be a symlink: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise Phase2FactsError(f"invalid source evidence path: {relative}") from exc
    if not resolved.is_file():
        raise Phase2FactsError(f"source evidence is not a regular file: {relative}")
    return resolved


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    if not isinstance(value, dict):
        raise Phase2FactsError(f"expected JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_paths(symbol: str) -> list[str]:
    compact = symbol.replace(".", "")
    return [
        "reports/phase1_observation/observation_index.json",
        f"reports/phase1_observation/{TRADE_DATE}/daily_summary.json",
        f"reports/phase1_observation/{TRADE_DATE}/{compact}_contract.json",
        f"reports/phase1_observation/{TRADE_DATE}/minute_30m_comparison.json",
        f"reports/phase1_observation/{TRADE_DATE}/request_audit.json",
        "reports/phase1_tickflow_request_audit.json",
        "reports/tickflow_phase1_observation_final.md",
    ]


def _source_manifest(repo_root: Path, symbol: str) -> list[dict[str, str]]:
    return [
        {"path": relative, "sha256": _sha256_file(_safe_source_path(repo_root, relative))}
        for relative in _source_paths(symbol)
    ]


def _source_set_hash(manifest: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        manifest,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _direct_provenance(
    manifest_by_path: Mapping[str, str],
    source_file: str,
    source_json_path: str,
    *,
    price_basis: str,
) -> dict[str, str]:
    return {
        "kind": "direct_evidence",
        "calculation_function": "identity",
        "source_file": source_file,
        "source_sha256": manifest_by_path[source_file],
        "source_json_path": source_json_path,
        "price_basis": price_basis,
    }


def _constant_provenance(name: str, *, price_basis: str = "not_applicable") -> dict[str, str]:
    return {
        "kind": "schema_constant",
        "calculation_function": "schema_constant",
        "constant": name,
        "price_basis": price_basis,
    }


def _indicator_contract(name: str, column: str) -> dict[str, Any]:
    return {
        "status": "NOT_IMPLEMENTED",
        "value": None,
        "price_basis": "none" if name.startswith("volume_") else "qfq",
        "implementation": f"app.indicators.pipeline.compute_indicators:{column}",
        "required_input": "retained_qfq_daily_ohlcv_series",
        "reason": "Phase 1 retained contracts do not contain the qfq OHLCV input series",
    }


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically and reject non-finite numbers."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def build_symbol_facts(repo_root: Path, symbol: str) -> dict[str, Any]:
    if symbol not in FIXED_SYMBOLS:
        raise Phase2FactsError(f"symbol is outside the fixed Phase 2A scope: {symbol}")

    manifest = _source_manifest(repo_root, symbol)
    manifest_by_path = {item["path"]: item["sha256"] for item in manifest}
    index_path, summary_path, contract_path, comparison_path, request_path, _, _ = (
        _source_paths(symbol)
    )
    index = _load_json(_safe_source_path(repo_root, index_path))
    summary = _load_json(_safe_source_path(repo_root, summary_path))
    contract = _load_json(_safe_source_path(repo_root, contract_path))
    comparison = _load_json(_safe_source_path(repo_root, comparison_path))
    request_audit = _load_json(_safe_source_path(repo_root, request_path))

    if index.get("final_status") != "PHASE1_OBSERVATION_PASSED":
        raise Phase2FactsError("Phase 1 observation is not passed")
    if index.get("valid_observation_days") != 5:
        raise Phase2FactsError("Phase 1 observation day count is not five")
    if summary.get("observation_date") != TRADE_DATE or summary.get("status") != "DAY_PASSED":
        raise Phase2FactsError("Day 5 summary is not a passing 2026-07-31 observation")
    if contract.get("symbol") != symbol or contract.get("status") != "PASSED":
        raise Phase2FactsError(f"symbol contract is not passing: {symbol}")
    if comparison.get("observation_date") != TRADE_DATE:
        raise Phase2FactsError("minute comparison trade date is stale")
    if request_audit.get("http_429") != 0 or request_audit.get("retry_count") != 0:
        raise Phase2FactsError("Day 5 request audit is not clean")

    daily = contract["daily"]
    ex_factors = contract["ex_factors"]
    one_minute = contract["one_minute"]
    direct_30m = contract["direct_30m"]
    minute_comparison = comparison["symbols"][symbol]

    facts: dict[str, Any] = {
        "facts_schema_version": FACTS_SCHEMA_VERSION,
        "source_system": "tickflow-stock-panel",
        "symbol": symbol,
        "name": FIXED_SYMBOLS[symbol],
        "trade_date": TRADE_DATE,
        "timezone": TIMEZONE,
        "data_freshness": "fresh",
        "content_readiness": "BLOCKED_SOURCE_EVIDENCE",
        "missing_source_inputs": list(MISSING_SOURCE_INPUTS),
        "source_evidence": {
            "phase1_status": index["final_status"],
            "observation_days": index["valid_observation_days"],
            "source_files": manifest,
            "source_set_sha256": _source_set_hash(manifest),
        },
        "price_basis": {
            "daily_ohlcv": "none",
            "indicator_prices": "qfq",
            "indicator_volume": "none",
            "minute": "none",
        },
        "daily": {
            "status": "UNAVAILABLE_SOURCE_EVIDENCE",
            "contract_status": daily["status"],
            "latest_raw_trade_date": daily["latest_raw_trade_date"],
            "latest_qfq_trade_date": daily["latest_qfq_trade_date"],
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": None,
            "amount": None,
            "reason": "Retained Phase 1 evidence does not include the Day 5 OHLCV values",
        },
        "adjustment_factors": {
            "status": ex_factors["status"],
            "factor_count": ex_factors["factor_count"],
            "qfq_relation_match_rate": ex_factors["qfq_relation_match_rate"],
            "repeat_stable": ex_factors["repeat_stable"],
            "second_adjustment_detected": ex_factors["second_adjustment_detected"],
        },
        "minute_1m": {
            "status": one_minute["status"],
            "period": one_minute["period"],
            "bar_count": one_minute["bar_count"],
            "latest_timestamp": one_minute["latest_timestamp"],
            "duplicate_timestamp_count": one_minute["duplicate_timestamps"],
            "lunch_break_bar_count": one_minute["lunch_break_bars"],
            "material_ohlc_anomaly_count": one_minute["material_ohlc_anomalies"],
            "material_negative_amount_count": one_minute["material_negative_amount"],
            "epsilon_ohlc_warning_count": one_minute["epsilon_ohlc_warning_count"],
            "epsilon_amount_warning_count": one_minute["epsilon_amount_warning_count"],
        },
        "minute_30m": {
            "status": direct_30m["status"],
            "period": direct_30m["period"],
            "bar_count": direct_30m["bar_count"],
            "latest_timestamp": direct_30m["latest_timestamp"],
            "duplicate_timestamp_count": direct_30m["duplicate_timestamps"],
            "lunch_break_bar_count": direct_30m["lunch_break_bars"],
            "material_ohlc_anomaly_count": direct_30m["material_ohlc_anomalies"],
            "material_negative_amount_count": direct_30m["material_negative_amount"],
            "ohlc_mismatch_count": minute_comparison["ohlc_mismatches"],
            "non_first_bucket_volume_amount_mismatch_count": minute_comparison[
                "non_first_bucket_volume_amount_mismatches"
            ],
            "first_bucket_rule": minute_comparison["first_bucket_rule"],
            "first_bucket_mechanically_explained": minute_comparison[
                "first_bucket_mechanically_explained"
            ],
            "vendor_contract_confirmed": minute_comparison["vendor_contract_confirmed"],
        },
        "indicators": {
            name: _indicator_contract(name, column)
            for name, column in INDICATOR_COLUMNS.items()
        },
        "key_levels": {
            "status": "NOT_IMPLEMENTED",
            "observed_supports": [],
            "observed_resistances": [],
            "calculation_method": None,
            "reason": "No approved deterministic key-level method has retained input data",
        },
        "scope": {
            "market_scope": "incomplete",
            "financial_scope": "unavailable",
            "news_scope": "unavailable",
            "industry_scope": "manual_verification_required",
        },
        "vendor_pending": list(VENDOR_PENDING),
        "trading": False,
        "generation": {
            "mode": "offline_phase1_evidence_only",
            "new_tickflow_api_request_count": 0,
            "ai_calls": 0,
        },
    }

    direct_fields = {
        "/source_evidence/observation_days": (
            index_path,
            "/valid_observation_days",
            "not_applicable",
        ),
        "/adjustment_factors/factor_count": (
            contract_path,
            "/ex_factors/factor_count",
            "qfq",
        ),
        "/adjustment_factors/qfq_relation_match_rate": (
            contract_path,
            "/ex_factors/qfq_relation_match_rate",
            "qfq",
        ),
        "/minute_1m/bar_count": (contract_path, "/one_minute/bar_count", "none"),
        "/minute_1m/duplicate_timestamp_count": (
            contract_path,
            "/one_minute/duplicate_timestamps",
            "none",
        ),
        "/minute_1m/lunch_break_bar_count": (
            contract_path,
            "/one_minute/lunch_break_bars",
            "none",
        ),
        "/minute_1m/material_ohlc_anomaly_count": (
            contract_path,
            "/one_minute/material_ohlc_anomalies",
            "none",
        ),
        "/minute_1m/material_negative_amount_count": (
            contract_path,
            "/one_minute/material_negative_amount",
            "none",
        ),
        "/minute_1m/epsilon_ohlc_warning_count": (
            contract_path,
            "/one_minute/epsilon_ohlc_warning_count",
            "none",
        ),
        "/minute_1m/epsilon_amount_warning_count": (
            contract_path,
            "/one_minute/epsilon_amount_warning_count",
            "none",
        ),
        "/minute_30m/bar_count": (contract_path, "/direct_30m/bar_count", "none"),
        "/minute_30m/duplicate_timestamp_count": (
            contract_path,
            "/direct_30m/duplicate_timestamps",
            "none",
        ),
        "/minute_30m/lunch_break_bar_count": (
            contract_path,
            "/direct_30m/lunch_break_bars",
            "none",
        ),
        "/minute_30m/material_ohlc_anomaly_count": (
            contract_path,
            "/direct_30m/material_ohlc_anomalies",
            "none",
        ),
        "/minute_30m/material_negative_amount_count": (
            contract_path,
            "/direct_30m/material_negative_amount",
            "none",
        ),
        "/minute_30m/ohlc_mismatch_count": (
            comparison_path,
            f"/symbols/{symbol}/ohlc_mismatches",
            "none",
        ),
        "/minute_30m/non_first_bucket_volume_amount_mismatch_count": (
            comparison_path,
            f"/symbols/{symbol}/non_first_bucket_volume_amount_mismatches",
            "none",
        ),
    }
    numeric_provenance = {
        path: _direct_provenance(
            manifest_by_path,
            source_file,
            source_json_path,
            price_basis=price_basis,
        )
        for path, (source_file, source_json_path, price_basis) in direct_fields.items()
    }
    numeric_provenance["/facts_schema_version"] = _constant_provenance(
        "FACTS_SCHEMA_VERSION"
    )
    numeric_provenance["/generation/new_tickflow_api_request_count"] = _constant_provenance(
        "OFFLINE_BUILD_ZERO_REQUESTS"
    )
    numeric_provenance["/generation/ai_calls"] = _constant_provenance(
        "PHASE2A_AI_DISABLED"
    )
    facts["numeric_provenance"] = dict(sorted(numeric_provenance.items()))

    canonical_json_bytes(facts)
    return facts


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _walk_values(value: Any, pointer: str = ""):
    yield pointer or "/", value
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_pointer = f"{pointer}/{_pointer_escape(str(key))}"
            yield from _walk_values(child, child_pointer)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_values(child, f"{pointer}/{index}")


def _pointer_get(value: Any, pointer: str) -> Any:
    current = value
    if pointer in {"", "/"}:
        return current
    for raw_part in pointer.lstrip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _numeric_paths(value: Mapping[str, Any]) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for pointer, item in _walk_values(value):
        if pointer.startswith("/numeric_provenance"):
            continue
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            result[pointer] = item
    return result


def _scan_unsafe_fields(facts: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    secret_keys = {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "session",
        "secret",
        "token",
    }
    trading_keys = {
        "buy",
        "buy_price",
        "sell",
        "sell_price",
        "position",
        "add_position",
        "reduce_position",
        "stop_loss",
        "take_profit",
        "target_price",
        "trading_advice",
        "operation_advice",
        "买入",
        "卖出",
        "加仓",
        "减仓",
        "仓位",
        "止损",
        "止盈",
        "目标价",
        "操作建议",
    }
    forbidden_text = (
        "买入",
        "卖出",
        "加仓",
        "减仓",
        "止损位",
        "止盈位",
        "目标价",
        "操作建议",
    )
    for pointer, value in _walk_values(facts):
        if pointer == "/":
            continue
        key = pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
        normalized = key.lower().replace("-", "_")
        if normalized in secret_keys:
            errors.append(f"secret_field:{pointer}")
        if normalized in trading_keys:
            errors.append(f"forbidden_trading_field:{pointer}")
        if isinstance(value, str) and any(term in value for term in forbidden_text):
            errors.append(f"forbidden_trading_text:{pointer}")
    return errors


def validate_facts_document(
    repo_root: Path,
    facts: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(facts, Mapping):
        return ["facts_not_object"]

    symbol = facts.get("symbol")
    if symbol not in FIXED_SYMBOLS:
        errors.append("symbol_out_of_scope")
        return sorted(set(errors))

    if facts.get("facts_schema_version") != FACTS_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if facts.get("source_system") != "tickflow-stock-panel":
        errors.append("source_system_invalid")
    if facts.get("name") != FIXED_SYMBOLS[symbol]:
        errors.append("name_invalid")
    if facts.get("trade_date") != TRADE_DATE:
        errors.append("trade_date_invalid")
    if facts.get("timezone") != TIMEZONE:
        errors.append("timezone_invalid")
    if facts.get("data_freshness") != "fresh":
        errors.append("data_freshness_invalid")
    if facts.get("content_readiness") != "BLOCKED_SOURCE_EVIDENCE":
        errors.append("content_readiness_invalid")
    if facts.get("missing_source_inputs") != MISSING_SOURCE_INPUTS:
        errors.append("missing_source_inputs_invalid")

    source_evidence = facts.get("source_evidence")
    manifest: list[dict[str, str]] = []
    if not isinstance(source_evidence, Mapping):
        errors.append("source_evidence_invalid")
    else:
        raw_manifest = source_evidence.get("source_files")
        if isinstance(raw_manifest, list) and all(isinstance(item, dict) for item in raw_manifest):
            manifest = raw_manifest
        else:
            errors.append("source_manifest_invalid")

    expected_paths = _source_paths(symbol)
    manifest_paths = [item.get("path") for item in manifest]
    if manifest_paths != expected_paths:
        errors.append("source_file_set_invalid")

    manifest_by_path: dict[str, str] = {}
    for item in manifest:
        relative = item.get("path")
        recorded_hash = item.get("sha256")
        if not isinstance(relative, str) or not isinstance(recorded_hash, str):
            errors.append("source_manifest_entry_invalid")
            continue
        manifest_by_path[relative] = recorded_hash
        try:
            actual_hash = _sha256_file(_safe_source_path(repo_root, relative))
        except Phase2FactsError:
            errors.append(f"source_path_invalid:{relative}")
            continue
        if actual_hash != recorded_hash:
            errors.append("source_hash_mismatch")
            errors.append(f"source_hash_mismatch:{relative}")

    if manifest and source_evidence.get("source_set_sha256") != _source_set_hash(manifest):
        errors.append("source_set_hash_mismatch")
    if source_evidence.get("phase1_status") != "PHASE1_OBSERVATION_PASSED":
        errors.append("phase1_status_invalid")
    if source_evidence.get("observation_days") != 5:
        errors.append("observation_days_invalid")

    if facts.get("price_basis") != {
        "daily_ohlcv": "none",
        "indicator_prices": "qfq",
        "indicator_volume": "none",
        "minute": "none",
    }:
        errors.append("raw_qfq_basis_invalid")

    daily = facts.get("daily")
    if not isinstance(daily, Mapping):
        errors.append("daily_contract_invalid")
    else:
        if daily.get("status") != "UNAVAILABLE_SOURCE_EVIDENCE":
            errors.append("daily_status_invalid")
        for field in ("open", "high", "low", "close", "volume", "amount"):
            if daily.get(field) is not None:
                errors.append(f"daily_value_without_retained_source:/daily/{field}")
        if daily.get("latest_raw_trade_date") != TRADE_DATE:
            errors.append("latest_raw_trade_date_invalid")
        if daily.get("latest_qfq_trade_date") != TRADE_DATE:
            errors.append("latest_qfq_trade_date_invalid")

    indicators = facts.get("indicators")
    if not isinstance(indicators, Mapping) or set(indicators) != set(INDICATOR_COLUMNS):
        errors.append("indicator_set_invalid")
    else:
        for name, column in INDICATOR_COLUMNS.items():
            item = indicators[name]
            if not isinstance(item, Mapping):
                errors.append(f"indicator_contract_invalid:{name}")
                continue
            if item.get("status") != "NOT_IMPLEMENTED":
                errors.append(f"indicator_status_invalid:{name}")
            if item.get("value") is not None:
                errors.append(f"indicator_value_without_retained_source:{name}")
            expected_basis = "none" if name.startswith("volume_") else "qfq"
            if item.get("price_basis") != expected_basis:
                errors.append(f"indicator_price_basis_invalid:{name}")
            expected_implementation = f"app.indicators.pipeline.compute_indicators:{column}"
            if item.get("implementation") != expected_implementation:
                errors.append(f"indicator_implementation_invalid:{name}")

    expected_scope = {
        "market_scope": "incomplete",
        "financial_scope": "unavailable",
        "news_scope": "unavailable",
        "industry_scope": "manual_verification_required",
    }
    if facts.get("scope") != expected_scope:
        errors.append("scope_invalid")
    if facts.get("vendor_pending") != VENDOR_PENDING:
        errors.append("vendor_pending_invalid")
    if facts.get("trading") is not False:
        errors.append("trading_must_be_false")
    generation = facts.get("generation")
    if not isinstance(generation, Mapping):
        errors.append("generation_invalid")
    else:
        if generation.get("new_tickflow_api_request_count") != 0:
            errors.append("tickflow_api_request_count_nonzero")
        if generation.get("ai_calls") != 0:
            errors.append("ai_call_count_nonzero")

    errors.extend(_scan_unsafe_fields(facts))

    numeric_values = _numeric_paths(facts)
    for pointer, value in numeric_values.items():
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"non_finite_number:{pointer}")

    provenance = facts.get("numeric_provenance")
    if not isinstance(provenance, Mapping):
        errors.append("numeric_provenance_invalid")
        provenance = {}
    for pointer in numeric_values:
        if pointer not in provenance:
            errors.append(f"numeric_provenance_missing:{pointer}")
    for pointer in provenance:
        if pointer not in numeric_values:
            errors.append(f"numeric_provenance_orphan:{pointer}")

    source_cache: dict[str, dict[str, Any]] = {}
    constant_values = {
        "/facts_schema_version": FACTS_SCHEMA_VERSION,
        "/generation/new_tickflow_api_request_count": 0,
        "/generation/ai_calls": 0,
    }
    for pointer, value in numeric_values.items():
        entry = provenance.get(pointer)
        if not isinstance(entry, Mapping):
            continue
        kind = entry.get("kind")
        if kind == "schema_constant":
            if entry.get("calculation_function") != "schema_constant":
                errors.append(f"numeric_provenance_calculation_invalid:{pointer}")
            if constant_values.get(pointer) != value:
                errors.append(f"numeric_provenance_constant_invalid:{pointer}")
            continue
        if kind != "direct_evidence":
            errors.append(f"numeric_provenance_kind_invalid:{pointer}")
            continue
        if entry.get("calculation_function") != "identity":
            errors.append(f"numeric_provenance_calculation_invalid:{pointer}")
        source_file = entry.get("source_file")
        source_hash = entry.get("source_sha256")
        source_pointer = entry.get("source_json_path")
        if not all(isinstance(item, str) for item in (source_file, source_hash, source_pointer)):
            errors.append(f"numeric_provenance_source_invalid:{pointer}")
            continue
        if manifest_by_path.get(source_file) != source_hash:
            errors.append(f"numeric_provenance_source_mismatch:{pointer}")
            continue
        try:
            if source_file not in source_cache:
                source_cache[source_file] = _load_json(_safe_source_path(repo_root, source_file))
            source_value = _pointer_get(source_cache[source_file], source_pointer)
        except (Phase2FactsError, KeyError, IndexError, TypeError, ValueError):
            errors.append(f"numeric_provenance_source_invalid:{pointer}")
            continue
        if source_value != value:
            errors.append(f"numeric_provenance_value_mismatch:{pointer}")

    try:
        canonical_json_bytes(facts)
    except (TypeError, ValueError):
        errors.append("canonical_serialization_error")

    try:
        expected = build_symbol_facts(repo_root, symbol)
    except Phase2FactsError:
        errors.append("deterministic_rebuild_failed")
    else:
        if facts != expected:
            errors.append("facts_not_equal_to_deterministic_rebuild")

    return sorted(set(errors))
