"""Deterministic Phase 2A Facts built from an approved local snapshot."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl

from app.indicators.pipeline import compute_indicators
from app.services.phase2_snapshot import (
    SOURCE_TYPE,
    validate_snapshot_directory,
)

FACTS_SCHEMA_VERSION = 1
TRADE_DATE = "2026-07-31"
TIMEZONE = "Asia/Shanghai"
SNAPSHOT_ROOT = "reports/phase2_source_snapshot/2026-07-31"
SNAPSHOT_MANIFEST = f"{SNAPSHOT_ROOT}/manifest.json"
PIPELINE_SOURCE = "backend/app/indicators/pipeline.py"
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
MISSING_SOURCE_INPUTS: list[str] = []
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


def _indicator_spec(
    *,
    inputs: list[str],
    parameters: dict[str, Any],
    data_basis: str,
    unit: str,
    dimensionless: bool = False,
) -> dict[str, Any]:
    return {
        "input_fields": inputs,
        "parameters": parameters,
        "data_basis": data_basis,
        "price_basis": "qfq" if data_basis == "qfq" else "not_applicable",
        "unit": unit,
        "dimensionless": dimensionless,
    }


INDICATOR_SPECS = {
    "ma5": _indicator_spec(
        inputs=["close"],
        parameters={"window": 5, "method": "simple"},
        data_basis="qfq",
        unit="qfq_price",
    ),
    "ma10": _indicator_spec(
        inputs=["close"],
        parameters={"window": 10, "method": "simple"},
        data_basis="qfq",
        unit="qfq_price",
    ),
    "ma20": _indicator_spec(
        inputs=["close"],
        parameters={"window": 20, "method": "simple"},
        data_basis="qfq",
        unit="qfq_price",
    ),
    "ma60": _indicator_spec(
        inputs=["close"],
        parameters={"window": 60, "method": "simple"},
        data_basis="qfq",
        unit="qfq_price",
    ),
    "macd_dif": _indicator_spec(
        inputs=["close"],
        parameters={
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "ema_adjust": False,
            "histogram_multiplier": 2,
        },
        data_basis="qfq",
        unit="qfq_price",
    ),
    "macd_dea": _indicator_spec(
        inputs=["close"],
        parameters={
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "ema_adjust": False,
            "histogram_multiplier": 2,
        },
        data_basis="qfq",
        unit="qfq_price",
    ),
    "macd_hist": _indicator_spec(
        inputs=["close"],
        parameters={
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "ema_adjust": False,
            "histogram_multiplier": 2,
        },
        data_basis="qfq",
        unit="qfq_price",
    ),
    "rsi6": _indicator_spec(
        inputs=["close"],
        parameters={
            "period": 6,
            "smoothing": "ewm_alpha_1_over_period",
            "zero_loss_floor": "1e-12",
        },
        data_basis="qfq",
        unit="dimensionless",
        dimensionless=True,
    ),
    "rsi14": _indicator_spec(
        inputs=["close"],
        parameters={
            "period": 14,
            "smoothing": "ewm_alpha_1_over_period",
            "zero_loss_floor": "1e-12",
        },
        data_basis="qfq",
        unit="dimensionless",
        dimensionless=True,
    ),
    "boll_upper": _indicator_spec(
        inputs=["close"],
        parameters={
            "window": 20,
            "standard_deviations": 2,
            "standard_deviation_ddof": 1,
        },
        data_basis="qfq",
        unit="qfq_price",
    ),
    "boll_middle": _indicator_spec(
        inputs=["close"],
        parameters={
            "window": 20,
            "standard_deviations": 2,
            "standard_deviation_ddof": 1,
        },
        data_basis="qfq",
        unit="qfq_price",
    ),
    "boll_lower": _indicator_spec(
        inputs=["close"],
        parameters={
            "window": 20,
            "standard_deviations": 2,
            "standard_deviation_ddof": 1,
        },
        data_basis="qfq",
        unit="qfq_price",
    ),
    "atr14": _indicator_spec(
        inputs=["high", "low", "close"],
        parameters={"period": 14, "smoothing": "wilder_ewm", "ema_adjust": False},
        data_basis="qfq",
        unit="qfq_price",
    ),
    "volume_ma5": _indicator_spec(
        inputs=["volume"],
        parameters={"window": 5, "method": "simple"},
        data_basis="raw",
        unit="VENDOR_CONFIRMATION_PENDING",
    ),
    "volume_ma10": _indicator_spec(
        inputs=["volume"],
        parameters={"window": 10, "method": "simple"},
        data_basis="raw",
        unit="VENDOR_CONFIRMATION_PENDING",
    ),
    "volume_ratio": _indicator_spec(
        inputs=["volume"],
        parameters={
            "lookback": 5,
            "denominator": "previous_period_mean_excluding_current",
        },
        data_basis="raw",
        unit="dimensionless",
        dimensionless=True,
    ),
}


class Phase2FactsError(ValueError):
    """Raised when approved evidence cannot support a Facts document."""


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
    value = json.loads(
        path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
    )
    if not isinstance(value, dict):
        raise Phase2FactsError(f"expected JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_set_hash(manifest: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        manifest,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _phase1_source_paths(symbol: str) -> list[str]:
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


def _source_manifest(
    repo_root: Path, paths: list[str], roles: Mapping[str, str] | None = None
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for relative in paths:
        item = {
            "path": relative,
            "sha256": _sha256_file(_safe_source_path(repo_root, relative)),
        }
        if roles is not None:
            item["role"] = roles[relative]
        result.append(item)
    return result


def _snapshot_paths(symbol: str) -> dict[str, str]:
    compact = symbol.replace(".", "")
    return {
        "manifest": SNAPSHOT_MANIFEST,
        "snapshot_validation": f"{SNAPSHOT_ROOT}/snapshot_validation.json",
        "raw_daily": f"{SNAPSHOT_ROOT}/{compact}_raw_daily.json",
        "qfq_daily": f"{SNAPSHOT_ROOT}/{compact}_qfq_daily.json",
        "indicator_implementation": PIPELINE_SOURCE,
    }


def _snapshot_inputs(
    repo_root: Path, symbol: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
    snapshot_dir = repo_root / SNAPSHOT_ROOT
    if snapshot_dir.is_symlink():
        raise Phase2FactsError("snapshot source directory must not be a symlink")
    validation = validate_snapshot_directory(snapshot_dir)
    if validation.get("status") != "SNAPSHOT_VALID":
        raise Phase2FactsError(
            f"snapshot source validation failed: {validation.get('errors', [])}"
        )
    paths = _snapshot_paths(symbol)
    manifest = _load_json(_safe_source_path(repo_root, paths["manifest"]))
    raw_document = _load_json(_safe_source_path(repo_root, paths["raw_daily"]))
    qfq_document = _load_json(_safe_source_path(repo_root, paths["qfq_daily"]))
    hashes = {
        role: _sha256_file(_safe_source_path(repo_root, path))
        for role, path in paths.items()
    }
    return manifest, raw_document, qfq_document, hashes


def _phase1_contracts(repo_root: Path, symbol: str) -> dict[str, Any]:
    paths = _phase1_source_paths(symbol)
    index = _load_json(_safe_source_path(repo_root, paths[0]))
    summary = _load_json(_safe_source_path(repo_root, paths[1]))
    contract = _load_json(_safe_source_path(repo_root, paths[2]))
    comparison = _load_json(_safe_source_path(repo_root, paths[3]))
    request_audit = _load_json(_safe_source_path(repo_root, paths[4]))
    if index.get("final_status") != "PHASE1_OBSERVATION_PASSED":
        raise Phase2FactsError("Phase 1 observation is not passed")
    if index.get("valid_observation_days") != 5:
        raise Phase2FactsError("Phase 1 observation day count is not five")
    if summary.get("observation_date") != TRADE_DATE or summary.get("status") != "DAY_PASSED":
        raise Phase2FactsError("Day 5 summary is not a passing observation")
    if contract.get("symbol") != symbol or contract.get("status") != "PASSED":
        raise Phase2FactsError(f"symbol contract is not passing: {symbol}")
    if comparison.get("observation_date") != TRADE_DATE:
        raise Phase2FactsError("minute comparison trade date is stale")
    if request_audit.get("http_429") != 0 or request_audit.get("retry_count") != 0:
        raise Phase2FactsError("Day 5 request audit is not clean")
    return {
        "paths": paths,
        "index": index,
        "contract": contract,
        "comparison": comparison,
    }


def _symbol_manifest_entry(manifest: Mapping[str, Any], symbol: str) -> tuple[int, dict[str, Any]]:
    entries = manifest.get("symbols")
    if not isinstance(entries, list):
        raise Phase2FactsError("snapshot symbol manifest is invalid")
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and entry.get("symbol") == symbol:
            return index, entry
    raise Phase2FactsError(f"snapshot symbol is missing: {symbol}")


def _compute_indicator_values(
    symbol: str,
    raw_rows: list[dict[str, Any]],
    qfq_rows: list[dict[str, Any]],
) -> dict[str, float]:
    raw_by_date = {row["trade_date"]: row for row in raw_rows}
    if len(raw_by_date) != len(raw_rows):
        raise Phase2FactsError("raw snapshot contains duplicate dates")
    rows: list[dict[str, Any]] = []
    for qfq in qfq_rows:
        trade_date = qfq["trade_date"]
        raw = raw_by_date.get(trade_date)
        if raw is None:
            raise Phase2FactsError("raw and qfq snapshot dates are not aligned")
        rows.append(
            {
                "symbol": symbol,
                "date": trade_date,
                "open": float(qfq["open"]),
                "high": float(qfq["high"]),
                "low": float(qfq["low"]),
                "close": float(qfq["close"]),
                "volume": float(raw["volume"]),
            }
        )
    needed = set(INDICATOR_COLUMNS.values())
    computed = compute_indicators(pl.DataFrame(rows), needed=needed)
    latest = computed.filter(pl.col("date") == TRADE_DATE)
    if latest.height != 1:
        raise Phase2FactsError("computed indicator output is missing the trade date")
    row = latest.to_dicts()[0]
    values: dict[str, float] = {}
    for name, column in INDICATOR_COLUMNS.items():
        value = row.get(column)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Phase2FactsError(f"indicator is not numeric: {name}")
        result = float(value)
        if not math.isfinite(result):
            raise Phase2FactsError(f"indicator is not finite: {name}")
        values[name] = result
    return values


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _walk_values(value: Any, pointer: str = ""):
    yield pointer or "/", value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk_values(
                child, f"{pointer}/{_pointer_escape(str(key))}"
            )
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


def numeric_paths(value: Mapping[str, Any]) -> dict[str, int | float]:
    """Return every numeric claim outside the provenance registry."""
    result: dict[str, int | float] = {}
    for pointer, item in _walk_values(value):
        if pointer.startswith("/numeric_provenance"):
            continue
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            result[pointer] = item
    return result


def _direct_provenance(
    source_file: str,
    source_sha256: str,
    source_json_path: str,
    *,
    price_basis: str,
) -> dict[str, Any]:
    return {
        "kind": "direct_evidence",
        "calculation_function": "identity",
        "source_file": source_file,
        "source_sha256": source_sha256,
        "source_json_path": source_json_path,
        "price_basis": price_basis,
    }


def _constant_provenance(name: str) -> dict[str, Any]:
    return {
        "kind": "schema_constant",
        "calculation_function": "schema_constant",
        "constant": name,
        "price_basis": "not_applicable",
    }


def _indicator_provenance(
    name: str,
    item: Mapping[str, Any],
    paths: Mapping[str, str],
    hashes: Mapping[str, str],
) -> dict[str, Any]:
    roles = ["qfq_daily"] if item["data_basis"] == "qfq" else ["raw_daily"]
    return {
        "kind": "deterministic_calculation",
        "calculation_function": "app.indicators.pipeline.compute_indicators",
        "implementation_column": INDICATOR_COLUMNS[name],
        "source_files": [
            {
                "role": role,
                "path": paths[role],
                "sha256": hashes[role],
                "source_json_path": "/rows",
            }
            for role in roles
        ],
        "implementation_source": {
            "path": paths["indicator_implementation"],
            "sha256": hashes["indicator_implementation"],
        },
        "input_fields": item["input_fields"],
        "parameters": item["parameters"],
        "price_basis": item["price_basis"],
        "data_basis": item["data_basis"],
        "result_precision": item["result_precision"],
    }


def _parameter_provenance(
    name: str,
    item: Mapping[str, Any],
    paths: Mapping[str, str],
    hashes: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "kind": "implementation_parameter",
        "calculation_function": "app.indicators.pipeline.compute_indicators",
        "implementation_column": INDICATOR_COLUMNS[name],
        "source_file": paths["indicator_implementation"],
        "source_sha256": hashes["indicator_implementation"],
        "price_basis": item["price_basis"],
    }


def _numeric_provenance(
    facts: Mapping[str, Any],
    *,
    symbol: str,
    symbol_index: int,
    snapshot_paths: Mapping[str, str],
    snapshot_hashes: Mapping[str, str],
    phase1_paths: list[str],
    phase1_hashes: Mapping[str, str],
    latest_row_index: int,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "/facts_schema_version": _constant_provenance("FACTS_SCHEMA_VERSION"),
        "/source_evidence/indicator_source/cloud_mutation_count": _direct_provenance(
            snapshot_paths["manifest"],
            snapshot_hashes["manifest"],
            "/cloud_mutation_count",
            price_basis="not_applicable",
        ),
        "/source_evidence/indicator_source/tickflow_api_request_count": _direct_provenance(
            snapshot_paths["manifest"],
            snapshot_hashes["manifest"],
            "/tickflow_api_request_count",
            price_basis="not_applicable",
        ),
        "/source_evidence/indicator_source/row_count": _direct_provenance(
            snapshot_paths["manifest"],
            snapshot_hashes["manifest"],
            f"/symbols/{symbol_index}/raw/row_count",
            price_basis="not_applicable",
        ),
        "/source_evidence/phase1_contract_evidence/observation_days": _direct_provenance(
            phase1_paths[0],
            phase1_hashes[phase1_paths[0]],
            "/valid_observation_days",
            price_basis="not_applicable",
        ),
    }
    for field in ("open", "high", "low", "close", "volume", "amount"):
        basis = "raw" if field in {"open", "high", "low", "close"} else "raw_unconfirmed_unit"
        provenance[f"/daily/{field}"] = _direct_provenance(
            snapshot_paths["raw_daily"],
            snapshot_hashes["raw_daily"],
            f"/rows/{latest_row_index}/{field}",
            price_basis=basis,
        )

    contract_path = phase1_paths[2]
    comparison_path = phase1_paths[3]
    phase1_direct = {
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
    for pointer, (source_file, source_pointer, basis) in phase1_direct.items():
        provenance[pointer] = _direct_provenance(
            source_file,
            phase1_hashes[source_file],
            source_pointer,
            price_basis=basis,
        )

    for name, item in facts["indicators"].items():
        provenance[f"/indicators/{name}/value"] = _indicator_provenance(
            name, item, snapshot_paths, snapshot_hashes
        )
        provenance[f"/indicators/{name}/history_window/row_count"] = _direct_provenance(
            snapshot_paths["manifest"],
            snapshot_hashes["manifest"],
            f"/symbols/{symbol_index}/raw/row_count",
            price_basis=item["price_basis"],
        )
        for parameter, value in item["parameters"].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                provenance[
                    f"/indicators/{name}/parameters/{_pointer_escape(parameter)}"
                ] = _parameter_provenance(
                    name, item, snapshot_paths, snapshot_hashes
                )

    provenance["/generation/new_tickflow_api_request_count"] = _direct_provenance(
        snapshot_paths["manifest"],
        snapshot_hashes["manifest"],
        "/tickflow_api_request_count",
        price_basis="not_applicable",
    )
    provenance["/generation/cloud_mutation_count"] = _direct_provenance(
        snapshot_paths["manifest"],
        snapshot_hashes["manifest"],
        "/cloud_mutation_count",
        price_basis="not_applicable",
    )
    provenance["/generation/ai_calls"] = _constant_provenance("PHASE2A_AI_DISABLED")
    numeric = numeric_paths(facts)
    missing = sorted(set(numeric) - set(provenance))
    if missing:
        raise Phase2FactsError(f"numeric provenance is incomplete: {missing}")
    return dict(sorted(provenance.items()))


def build_symbol_facts(repo_root: Path, symbol: str) -> dict[str, Any]:
    """Build one fixed-scope Facts document with no network or cloud write."""
    if symbol not in FIXED_SYMBOLS:
        raise Phase2FactsError(f"symbol is outside the fixed Phase 2A scope: {symbol}")
    repo_root = repo_root.resolve(strict=True)
    manifest, raw_document, qfq_document, snapshot_hashes = _snapshot_inputs(
        repo_root, symbol
    )
    phase1 = _phase1_contracts(repo_root, symbol)
    symbol_index, symbol_manifest = _symbol_manifest_entry(manifest, symbol)
    raw_rows = raw_document.get("rows")
    qfq_rows = qfq_document.get("rows")
    if not isinstance(raw_rows, list) or not isinstance(qfq_rows, list):
        raise Phase2FactsError("snapshot rows are invalid")
    if len(raw_rows) != 251 or len(qfq_rows) != 251:
        raise Phase2FactsError("snapshot history does not contain 251 aligned rows")
    values = _compute_indicator_values(symbol, raw_rows, qfq_rows)
    latest = raw_rows[-1]
    snapshot_paths = _snapshot_paths(symbol)
    snapshot_roles = {path: role for role, path in snapshot_paths.items()}
    indicator_source_files = _source_manifest(
        repo_root, list(snapshot_paths.values()), snapshot_roles
    )
    phase1_files = _source_manifest(repo_root, phase1["paths"])
    phase1_hashes = {item["path"]: item["sha256"] for item in phase1_files}

    indicators: dict[str, Any] = {}
    for name, value in values.items():
        spec = INDICATOR_SPECS[name]
        item = {
            "status": "VALID",
            "value": value,
            "implementation": {
                "module": "app.indicators.pipeline",
                "function": "compute_indicators",
                "output_column": INDICATOR_COLUMNS[name],
            },
            "input_fields": list(spec["input_fields"]),
            "parameters": dict(spec["parameters"]),
            "data_basis": spec["data_basis"],
            "price_basis": spec["price_basis"],
            "history_window": {
                "row_count": len(raw_rows),
                "first_trade_date": raw_rows[0]["trade_date"],
                "last_trade_date": raw_rows[-1]["trade_date"],
            },
            "result_precision": "float64_unrounded",
            "unit": spec["unit"],
            "dimensionless": spec["dimensionless"],
        }
        if name.startswith("volume_"):
            item["volume_unit"] = "VENDOR_CONFIRMATION_PENDING"
            item["source_unit_unconfirmed"] = True
        indicators[name] = item

    contract = phase1["contract"]
    comparison = phase1["comparison"]
    daily_contract = contract["daily"]
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
        "content_readiness": "READY_FOR_AI_REVIEW",
        "missing_source_inputs": [],
        "source_evidence": {
            "indicator_source": {
                "source_type": SOURCE_TYPE,
                "source_snapshot_manifest": SNAPSHOT_MANIFEST,
                "manifest_sha256": snapshot_hashes["manifest"],
                "normalized_business_sha256": manifest[
                    "normalized_business_sha256"
                ],
                "phase1_original_payload_recovered": False,
                "phase1_byte_equivalent": False,
                "request_mode": "offline_existing_cloud_evidence",
                "cloud_access_mode": manifest["cloud_access_mode"],
                "cloud_mutation_count": manifest["cloud_mutation_count"],
                "tickflow_api_request_count": manifest[
                    "tickflow_api_request_count"
                ],
                "row_count": symbol_manifest["raw"]["row_count"],
                "first_trade_date": symbol_manifest["raw"]["first_trade_date"],
                "last_trade_date": symbol_manifest["raw"]["last_trade_date"],
                "source_files": indicator_source_files,
                "source_hashes": {
                    "manifest": snapshot_hashes["manifest"],
                    "raw_daily": snapshot_hashes["raw_daily"],
                    "qfq_daily": snapshot_hashes["qfq_daily"],
                    "indicator_implementation": snapshot_hashes[
                        "indicator_implementation"
                    ],
                },
            },
            "phase1_contract_evidence": {
                "phase1_status": phase1["index"]["final_status"],
                "observation_days": phase1["index"]["valid_observation_days"],
                "source_files": phase1_files,
                "source_set_sha256": _source_set_hash(phase1_files),
            },
        },
        "price_basis": {
            "daily_ohlcv": "raw",
            "indicator_prices": "qfq",
            "indicator_volume": "raw",
            "minute": "none",
        },
        "units": {
            "volume": "VENDOR_CONFIRMATION_PENDING",
            "amount": "VENDOR_CONFIRMATION_PENDING",
        },
        "daily": {
            "status": "AVAILABLE",
            "contract_status": daily_contract["status"],
            "trade_date": latest["trade_date"],
            "price_basis": "raw",
            "open": float(latest["open"]),
            "high": float(latest["high"]),
            "low": float(latest["low"]),
            "close": float(latest["close"]),
            "volume": float(latest["volume"]),
            "amount": float(latest["amount"]),
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
            "vendor_contract_confirmed": minute_comparison[
                "vendor_contract_confirmed"
            ],
        },
        "indicators": indicators,
        "key_levels": {
            "status": "NOT_IMPLEMENTED",
            "observed_supports": [],
            "observed_resistances": [],
            "calculation_method": None,
            "reason": "No approved deterministic key-level algorithm",
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
            "mode": "offline_existing_cloud_evidence",
            "new_tickflow_api_request_count": 0,
            "cloud_mutation_count": 0,
            "ai_calls": 0,
        },
    }
    facts["numeric_provenance"] = _numeric_provenance(
        facts,
        symbol=symbol,
        symbol_index=symbol_index,
        snapshot_paths=snapshot_paths,
        snapshot_hashes=snapshot_hashes,
        phase1_paths=phase1["paths"],
        phase1_hashes=phase1_hashes,
        latest_row_index=len(raw_rows) - 1,
    )
    canonical_json_bytes(facts)
    return facts


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
    """Independently rebuild and validate all deterministic Facts claims."""
    if not isinstance(facts, Mapping):
        return ["facts_not_object"]
    errors = _scan_unsafe_fields(facts)
    symbol = facts.get("symbol")
    if symbol not in FIXED_SYMBOLS:
        return sorted(set([*errors, "symbol_out_of_scope"]))
    try:
        expected = build_symbol_facts(repo_root, symbol)
    except (OSError, Phase2FactsError, ValueError, KeyError, TypeError):
        return sorted(set([*errors, "deterministic_rebuild_failed"]))

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
    if facts.get("content_readiness") != "READY_FOR_AI_REVIEW":
        errors.append("content_readiness_invalid")
    if facts.get("missing_source_inputs") != []:
        errors.append("missing_source_inputs_invalid")

    source_evidence = facts.get("source_evidence")
    indicator_source: Mapping[str, Any] = {}
    phase1_source: Mapping[str, Any] = {}
    if not isinstance(source_evidence, Mapping):
        errors.append("source_evidence_invalid")
    else:
        raw_indicator = source_evidence.get("indicator_source")
        raw_phase1 = source_evidence.get("phase1_contract_evidence")
        if isinstance(raw_indicator, Mapping):
            indicator_source = raw_indicator
        else:
            errors.append("indicator_source_invalid")
        if isinstance(raw_phase1, Mapping):
            phase1_source = raw_phase1
        else:
            errors.append("phase1_contract_evidence_invalid")

    if indicator_source.get("source_type") != SOURCE_TYPE:
        errors.append("indicator_source_type_invalid")
    if indicator_source.get("phase1_original_payload_recovered") is not False:
        errors.append("phase1_original_payload_recovered_must_be_false")
    if indicator_source.get("phase1_byte_equivalent") is not False:
        errors.append("phase1_byte_equivalent_must_be_false")
    if indicator_source.get("request_mode") != "offline_existing_cloud_evidence":
        errors.append("request_mode_invalid")
    if indicator_source.get("cloud_access_mode") != "read_only":
        errors.append("cloud_access_mode_invalid")
    if indicator_source.get("cloud_mutation_count") != 0:
        errors.append("cloud_mutation_count_nonzero")
    if indicator_source.get("tickflow_api_request_count") != 0:
        errors.append("tickflow_api_request_count_nonzero")

    expected_indicator = expected["source_evidence"]["indicator_source"]
    source_files = indicator_source.get("source_files")
    if source_files != expected_indicator["source_files"]:
        errors.append("indicator_source_files_invalid")
    source_hashes = indicator_source.get("source_hashes")
    if source_hashes != expected_indicator["source_hashes"]:
        errors.append("source_hash_mismatch")
    for item in source_files if isinstance(source_files, list) else []:
        if not isinstance(item, Mapping):
            errors.append("source_manifest_entry_invalid")
            continue
        relative = item.get("path")
        recorded_hash = item.get("sha256")
        if not isinstance(relative, str) or not isinstance(recorded_hash, str):
            errors.append("source_manifest_entry_invalid")
            continue
        try:
            actual_hash = _sha256_file(_safe_source_path(repo_root, relative))
        except Phase2FactsError:
            errors.append(f"source_path_invalid:{relative}")
        else:
            if actual_hash != recorded_hash:
                errors.append("source_hash_mismatch")
                errors.append(f"source_hash_mismatch:{relative}")

    if phase1_source.get("phase1_status") != "PHASE1_OBSERVATION_PASSED":
        errors.append("phase1_status_invalid")
    if phase1_source.get("observation_days") != 5:
        errors.append("observation_days_invalid")
    if phase1_source != expected["source_evidence"]["phase1_contract_evidence"]:
        errors.append("phase1_contract_evidence_invalid")

    if facts.get("price_basis") != {
        "daily_ohlcv": "raw",
        "indicator_prices": "qfq",
        "indicator_volume": "raw",
        "minute": "none",
    }:
        errors.append("raw_qfq_basis_invalid")
    units = facts.get("units")
    if not isinstance(units, Mapping):
        errors.append("units_invalid")
    else:
        if units.get("volume") != "VENDOR_CONFIRMATION_PENDING":
            errors.append("volume_unit_invalid")
        if units.get("amount") != "VENDOR_CONFIRMATION_PENDING":
            errors.append("amount_unit_invalid")

    daily = facts.get("daily")
    if not isinstance(daily, Mapping):
        errors.append("daily_invalid")
    else:
        if daily.get("status") != "AVAILABLE":
            errors.append("daily_status_invalid")
        if daily.get("price_basis") != "raw":
            errors.append("daily_price_basis_invalid")
        if daily.get("trade_date") != TRADE_DATE:
            errors.append("daily_trade_date_invalid")
        for field in ("open", "high", "low", "close", "volume", "amount"):
            if daily.get(field) != expected["daily"][field]:
                errors.append(f"daily_value_mismatch:{field}")

    indicators = facts.get("indicators")
    if not isinstance(indicators, Mapping) or set(indicators) != set(INDICATOR_COLUMNS):
        errors.append("indicator_set_invalid")
    else:
        for name in INDICATOR_COLUMNS:
            item = indicators.get(name)
            expected_item = expected["indicators"][name]
            if not isinstance(item, Mapping):
                errors.append(f"indicator_contract_invalid:{name}")
                continue
            value = item.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"indicator_value_invalid:{name}")
            elif isinstance(value, float) and not math.isfinite(value):
                errors.append(f"non_finite_number:/indicators/{name}/value")
            elif not math.isclose(
                float(value), float(expected_item["value"]), rel_tol=0, abs_tol=1e-12
            ):
                errors.append(f"indicator_value_mismatch:{name}")
            for field in (
                "status",
                "implementation",
                "input_fields",
                "parameters",
                "data_basis",
                "price_basis",
                "history_window",
                "result_precision",
                "unit",
            ):
                if item.get(field) != expected_item[field]:
                    errors.append(f"indicator_metadata_invalid:{name}:{field}")
        volume_ratio = indicators.get("volume_ratio")
        if not isinstance(volume_ratio, Mapping) or volume_ratio.get("dimensionless") is not True:
            errors.append("volume_ratio_dimensionless_invalid")
        for name in ("volume_ma5", "volume_ma10", "volume_ratio"):
            item = indicators.get(name)
            if not isinstance(item, Mapping):
                continue
            if item.get("volume_unit") != "VENDOR_CONFIRMATION_PENDING":
                errors.append(f"volume_indicator_unit_invalid:{name}")
            if item.get("source_unit_unconfirmed") is not True:
                errors.append(f"volume_indicator_source_unit_invalid:{name}")

    if facts.get("key_levels") != {
        "status": "NOT_IMPLEMENTED",
        "observed_supports": [],
        "observed_resistances": [],
        "calculation_method": None,
        "reason": "No approved deterministic key-level algorithm",
    }:
        errors.append("key_levels_invalid")
    if facts.get("scope") != expected["scope"]:
        errors.append("scope_invalid")
    if facts.get("vendor_pending") != VENDOR_PENDING:
        errors.append("vendor_pending_invalid")
    if facts.get("trading") is not False:
        errors.append("trading_must_be_false")
    generation = facts.get("generation")
    if not isinstance(generation, Mapping):
        errors.append("generation_invalid")
    else:
        if generation.get("mode") != "offline_existing_cloud_evidence":
            errors.append("generation_mode_invalid")
        if generation.get("new_tickflow_api_request_count") != 0:
            errors.append("tickflow_api_request_count_nonzero")
        if generation.get("cloud_mutation_count") != 0:
            errors.append("cloud_mutation_count_nonzero")
        if generation.get("ai_calls") != 0:
            errors.append("ai_call_count_nonzero")

    numeric = numeric_paths(facts)
    expected_numeric = numeric_paths(expected)
    provenance = facts.get("numeric_provenance")
    expected_provenance = expected["numeric_provenance"]
    if not isinstance(provenance, Mapping):
        errors.append("numeric_provenance_invalid")
        provenance = {}
    for pointer, value in numeric.items():
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"non_finite_number:{pointer}")
        if pointer not in provenance:
            errors.append(f"numeric_provenance_missing:{pointer}")
        if pointer in expected_numeric and value != expected_numeric[pointer]:
            errors.append(f"numeric_provenance_value_mismatch:{pointer}")
    for pointer in provenance:
        if pointer not in numeric:
            errors.append(f"numeric_provenance_orphan:{pointer}")
        elif provenance.get(pointer) != expected_provenance.get(pointer):
            errors.append(f"numeric_provenance_contract_mismatch:{pointer}")

    try:
        canonical_json_bytes(facts)
    except (TypeError, ValueError):
        errors.append("canonical_serialization_error")
    if facts != expected:
        errors.append("facts_not_equal_to_deterministic_rebuild")
    return sorted(set(errors))
