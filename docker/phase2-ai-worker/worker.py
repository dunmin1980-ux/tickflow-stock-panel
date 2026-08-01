"""Standard-library Fake Provider for the isolated Phase 2 Claims worker."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any

INPUT_PATH = Path("/input/projection.json")
OUTPUT_PATH = Path("/output/candidate.json")
MAX_BYTES = 1_048_576
EPSILON = 1e-10
ALLOWED_MODES = {"candidate", "probe"}
FIXED_SYMBOLS = {
    "000403.SZ": "派林生物",
    "600489.SH": "中金黄金",
    "300059.SZ": "东方财富",
}


def _rule(
    claim_type: str,
    refs: list[str],
    unit: str,
    basis: str,
    calculation: str | None,
    template: str,
    precision: int | None,
    timeframe: str,
    kind: str,
) -> dict[str, Any]:
    return {
        "claim_type": claim_type,
        "refs": refs,
        "unit": unit,
        "basis": basis,
        "calculation": calculation,
        "template": template,
        "precision": precision,
        "timeframe": timeframe,
        "kind": kind,
    }


RULES: dict[str, dict[str, Any]] = {
    "daily_close": _rule(
        "NUMERIC_OBSERVATION",
        ["/daily/close"],
        "raw_price",
        "raw",
        None,
        "daily_close_v1",
        2,
        "1d",
        "numeric",
    ),
    "daily_open_to_close_percent": _rule(
        "NUMERIC_OBSERVATION",
        ["/daily/open", "/daily/close"],
        "percent",
        "raw",
        "percentage_difference_v1",
        "daily_open_to_close_percent_v1",
        4,
        "1d",
        "numeric",
    ),
}

for _indicator in (
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "boll_upper",
    "boll_middle",
    "boll_lower",
    "atr14",
):
    RULES[_indicator] = _rule(
        "NUMERIC_OBSERVATION",
        [f"/indicators/{_indicator}/value"],
        "qfq_price",
        "qfq",
        None,
        "indicator_numeric_v1",
        4,
        "1d",
        "numeric",
    )
for _indicator in ("rsi6", "rsi14"):
    RULES[_indicator] = _rule(
        "NUMERIC_OBSERVATION",
        [f"/indicators/{_indicator}/value"],
        "dimensionless",
        "none",
        None,
        "indicator_numeric_v1",
        4,
        "1d",
        "numeric",
    )
for _predicate, _ref, _timeframe in (
    ("minute_1m_bar_count", "/minute_1m/bar_count", "1m"),
    ("minute_30m_bar_count", "/minute_30m/bar_count", "30m"),
):
    RULES[_predicate] = _rule(
        "NUMERIC_OBSERVATION",
        [_ref],
        "count",
        "none",
        None,
        "data_quality_count_v1",
        0,
        _timeframe,
        "numeric",
    )
for _predicate, _ref, _timeframe in (
    ("daily_contract_status", "/daily/contract_status", "1d"),
    ("minute_1m_contract_status", "/minute_1m/status", "1m"),
    ("minute_30m_contract_status", "/minute_30m/status", "30m"),
    ("adjustment_factor_status", "/adjustment_factors/status", "1d"),
    ("data_freshness", "/data_freshness", "1d"),
):
    RULES[_predicate] = _rule(
        "ENUM_STATE",
        [_ref],
        "none",
        "none",
        None,
        "contract_state_v1",
        None,
        _timeframe,
        "enum",
    )
for _predicate, _ref, _timeframe in (
    ("adjustment_repeat_stable", "/adjustment_factors/repeat_stable", "1d"),
    (
        "adjustment_second_adjustment_detected",
        "/adjustment_factors/second_adjustment_detected",
        "1d",
    ),
    (
        "minute_30m_first_bucket_mechanically_explained",
        "/minute_30m/first_bucket_mechanically_explained",
        "30m",
    ),
):
    RULES[_predicate] = _rule(
        "BOOLEAN_STATE",
        [_ref],
        "none",
        "none",
        None,
        "boolean_state_v1",
        None,
        _timeframe,
        "boolean",
    )
for _prefix, _timeframe, _fields in (
    (
        "minute_1m",
        "1m",
        (
            "duplicate_timestamp_count",
            "lunch_break_bar_count",
            "material_ohlc_anomaly_count",
            "material_negative_amount_count",
        ),
    ),
    (
        "minute_30m",
        "30m",
        (
            "duplicate_timestamp_count",
            "lunch_break_bar_count",
            "ohlc_mismatch_count",
            "non_first_bucket_volume_amount_mismatch_count",
            "material_ohlc_anomaly_count",
            "material_negative_amount_count",
        ),
    ),
):
    for _field in _fields:
        _predicate = f"{_prefix}_{_field}"
        RULES[_predicate] = _rule(
            "DATA_QUALITY_STATE",
            [f"/{_prefix}/{_field}"],
            "count",
            "none",
            None,
            "data_quality_count_v1",
            0,
            _timeframe,
            "numeric",
        )
for _predicate, _ref in (
    ("market_scope", "/scope/market_scope"),
    ("financial_scope", "/scope/financial_scope"),
    ("news_scope", "/scope/news_scope"),
):
    RULES[_predicate] = _rule(
        "SCOPE_NOTICE",
        [_ref],
        "none",
        "none",
        None,
        "scope_notice_v1",
        None,
        "none",
        "scope",
    )
RULES.update(
    {
        "industry_scope": _rule(
            "SCOPE_NOTICE",
            ["/scope/industry_scope"],
            "none",
            "none",
            "normalize_scope_v1",
            "scope_notice_v1",
            None,
            "none",
            "scope",
        ),
        "macd_dif_vs_dea": _rule(
            "SAME_BASIS_COMPARISON",
            ["/indicators/macd_dif/value", "/indicators/macd_dea/value"],
            "qfq_price",
            "qfq",
            "compare_numbers_v1",
            "same_basis_comparison_v1",
            4,
            "1d",
            "comparison",
        ),
        "ma_value_order": _rule(
            "ORDERED_RELATION",
            [
                "/indicators/ma5/value",
                "/indicators/ma10/value",
                "/indicators/ma20/value",
                "/indicators/ma60/value",
            ],
            "qfq_price",
            "qfq",
            "ordered_relation_v1",
            "ordered_relation_v1",
            4,
            "1d",
            "ordered",
        ),
        "vendor_pending": _rule(
            "VENDOR_PENDING_NOTICE",
            ["/vendor_pending"],
            "none",
            "none",
            None,
            "vendor_pending_v1",
            None,
            "none",
            "vendor",
        ),
    }
)

DIRECT_PREDICATES = (
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "rsi6",
    "rsi14",
    "boll_upper",
    "boll_middle",
    "boll_lower",
    "atr14",
    "minute_1m_bar_count",
    "minute_30m_bar_count",
    "daily_contract_status",
    "minute_1m_contract_status",
    "minute_30m_contract_status",
    "adjustment_factor_status",
    "data_freshness",
    "adjustment_repeat_stable",
    "adjustment_second_adjustment_detected",
    "minute_30m_first_bucket_mechanically_explained",
    "minute_1m_duplicate_timestamp_count",
    "minute_1m_lunch_break_bar_count",
    "minute_1m_material_ohlc_anomaly_count",
    "minute_1m_material_negative_amount_count",
    "minute_30m_duplicate_timestamp_count",
    "minute_30m_lunch_break_bar_count",
    "minute_30m_ohlc_mismatch_count",
    "minute_30m_non_first_bucket_volume_amount_mismatch_count",
    "minute_30m_material_ohlc_anomaly_count",
    "minute_30m_material_negative_amount_count",
)
EXPECTED_PREDICATES = (
    "market_scope",
    "financial_scope",
    "news_scope",
    "industry_scope",
    "daily_close",
    "daily_open_to_close_percent",
    *DIRECT_PREDICATES,
    "macd_dif_vs_dea",
    "ma_value_order",
    "vendor_pending",
)


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _projection_sha256(projection: Mapping[str, Any]) -> str:
    payload = dict(projection)
    payload.pop("projection_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _pointer(document: Any, pointer: str) -> Any:
    current = document
    for token in pointer.removeprefix("/").split("/"):
        if isinstance(current, Mapping):
            current = current[token]
        elif isinstance(current, list):
            current = current[int(token)]
        else:
            raise ValueError("projection pointer traverses scalar")
    return current


def _claim_common(
    symbol: str,
    trade_date: str,
    sequence: int,
    predicate: str,
    facts_sha256: str,
    refs: list[str],
    *,
    calculation: str | None = None,
) -> dict[str, Any]:
    rule = RULES[predicate]
    slug = predicate.replace("_", "-")
    if predicate == "daily_open_to_close_percent":
        slug = "daily-open-close-percent"
    return {
        "claim_id": (
            f'{symbol.replace(".", "")}-{trade_date.replace("-", "")}-'
            f"{sequence:03d}-{slug}"
        ),
        "claim_type": rule["claim_type"],
        "subject": {"entity_type": "stock", "symbol": symbol},
        "predicate": predicate,
        "provenance": {
            "facts_sha256": facts_sha256,
            "fact_refs": refs,
            "calculation_ref": calculation,
            "price_basis": rule["basis"],
        },
        "rendering": {
            "template_id": rule["template"],
            "display_precision": rule["precision"],
        },
        "scope": {"timeframe": rule["timeframe"], "as_of": trade_date},
        "validation_status": "VALIDATED",
    }


def _direct_claim(
    facts: Mapping[str, Any],
    symbol: str,
    trade_date: str,
    sequence: int,
    predicate: str,
    facts_sha256: str,
) -> dict[str, Any]:
    rule = RULES[predicate]
    value = _pointer(facts, rule["refs"][0])
    claim = _claim_common(
        symbol,
        trade_date,
        sequence,
        predicate,
        facts_sha256,
        list(rule["refs"]),
    )
    if rule["kind"] == "numeric":
        claim["object"] = {
            "value_type": "number",
            "value": value,
            "unit": rule["unit"],
        }
    elif rule["kind"] == "boolean":
        claim["object"] = {
            "value_type": "boolean",
            "value": value,
            "unit": "none",
        }
    elif rule["kind"] == "enum":
        claim["object"] = {
            "value_type": "enum",
            "value": value,
            "unit": "none",
        }
    elif rule["kind"] == "scope":
        claim["object"] = {
            "value_type": "scope",
            "scope_key": predicate,
            "value": value,
        }
    elif rule["kind"] == "vendor":
        claim["object"] = {
            "value_type": "vendor_pending",
            "items": value,
        }
    else:
        raise ValueError("unsupported claim kind")
    return claim


def _compare(left: float, right: float) -> str:
    if math.isclose(left, right, rel_tol=0.0, abs_tol=EPSILON):
        return "EQUAL_WITHIN_EPSILON"
    return "ABOVE" if left > right else "BELOW"


def _validate_projection(projection: Mapping[str, Any]) -> None:
    if projection.get("projection_schema_version") != 1:
        raise ValueError("projection schema invalid")
    if projection.get("symbol") not in FIXED_SYMBOLS:
        raise ValueError("projection symbol invalid")
    if projection.get("name") != FIXED_SYMBOLS[projection["symbol"]]:
        raise ValueError("projection name invalid")
    if projection.get("timezone") != "Asia/Shanghai":
        raise ValueError("projection timezone invalid")
    if projection.get("projection_sha256") != _projection_sha256(projection):
        raise ValueError("projection hash invalid")
    if set(projection.get("allowed_predicates", [])) != set(EXPECTED_PREDICATES):
        raise ValueError("projection predicates invalid")
    if not isinstance(projection.get("safe_facts"), Mapping):
        raise ValueError("projection facts invalid")


def generate_candidate(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Generate one deterministic candidate; the host remains authoritative."""
    _validate_projection(projection)
    facts = projection["safe_facts"]
    symbol = str(projection["symbol"])
    trade_date = str(projection["trade_date"])
    facts_sha256 = str(projection["facts_sha256"])
    claims: list[dict[str, Any]] = []
    sequence = 1

    for predicate in ("market_scope", "financial_scope", "news_scope"):
        claims.append(
            _direct_claim(
                facts,
                symbol,
                trade_date,
                sequence,
                predicate,
                facts_sha256,
            )
        )
        sequence += 1

    industry = _claim_common(
        symbol,
        trade_date,
        sequence,
        "industry_scope",
        facts_sha256,
        ["/scope/industry_scope"],
        calculation="normalize_scope_v1",
    )
    industry["object"] = {
        "value_type": "scope",
        "scope_key": "industry_scope",
        "value": "unavailable",
    }
    claims.append(industry)
    sequence += 1

    claims.append(
        _direct_claim(
            facts,
            symbol,
            trade_date,
            sequence,
            "daily_close",
            facts_sha256,
        )
    )
    sequence += 1

    open_value = float(_pointer(facts, "/daily/open"))
    close_value = float(_pointer(facts, "/daily/close"))
    if math.isclose(open_value, 0.0, rel_tol=0.0, abs_tol=EPSILON):
        raise ValueError("daily open cannot be zero")
    percent = _claim_common(
        symbol,
        trade_date,
        sequence,
        "daily_open_to_close_percent",
        facts_sha256,
        ["/daily/open", "/daily/close"],
        calculation="percentage_difference_v1",
    )
    percent["object"] = {
        "value_type": "number",
        "value": (close_value - open_value) / abs(open_value) * 100.0,
        "unit": "percent",
    }
    claims.append(percent)
    sequence += 1

    for predicate in DIRECT_PREDICATES:
        claims.append(
            _direct_claim(
                facts,
                symbol,
                trade_date,
                sequence,
                predicate,
                facts_sha256,
            )
        )
        sequence += 1

    dif_ref, dea_ref = RULES["macd_dif_vs_dea"]["refs"]
    dif_value = _pointer(facts, dif_ref)
    dea_value = _pointer(facts, dea_ref)
    comparison = _claim_common(
        symbol,
        trade_date,
        sequence,
        "macd_dif_vs_dea",
        facts_sha256,
        [dif_ref, dea_ref],
        calculation="compare_numbers_v1",
    )
    comparison["object"] = {
        "value_type": "comparison",
        "left": {
            "label_id": "macd_dif",
            "fact_ref": dif_ref,
            "value": dif_value,
            "unit": "qfq_price",
            "price_basis": "qfq",
        },
        "relation": _compare(float(dif_value), float(dea_value)),
        "right": {
            "label_id": "macd_dea",
            "fact_ref": dea_ref,
            "value": dea_value,
            "unit": "qfq_price",
            "price_basis": "qfq",
        },
    }
    claims.append(comparison)
    sequence += 1

    ordered_pairs = sorted(
        (
            (ref.split("/")[2], ref, _pointer(facts, ref))
            for ref in RULES["ma_value_order"]["refs"]
        ),
        key=lambda item: (-float(item[2]), item[0]),
    )
    ordered_values = [float(item[2]) for item in ordered_pairs]
    if any(
        left <= right or math.isclose(left, right, rel_tol=0.0, abs_tol=EPSILON)
        for left, right in pairwise(ordered_values)
    ):
        raise ValueError("moving averages are not strictly ordered")
    ordered = _claim_common(
        symbol,
        trade_date,
        sequence,
        "ma_value_order",
        facts_sha256,
        [item[1] for item in ordered_pairs],
        calculation="ordered_relation_v1",
    )
    ordered["object"] = {
        "value_type": "ordered_relation",
        "operands": [
            {
                "label_id": label,
                "fact_ref": ref,
                "value": value,
                "unit": "qfq_price",
                "price_basis": "qfq",
            }
            for label, ref, value in ordered_pairs
        ],
        "relation": "DESCENDING",
    }
    claims.append(ordered)
    sequence += 1

    claims.append(
        _direct_claim(
            facts,
            symbol,
            trade_date,
            sequence,
            "vendor_pending",
            facts_sha256,
        )
    )
    return {
        "candidate_schema_version": 1,
        "projection_sha256": projection["projection_sha256"],
        "symbol": symbol,
        "name": projection["name"],
        "trade_date": trade_date,
        "timezone": projection["timezone"],
        "claims": claims,
        "trading_advice": False,
    }


def _blocked_operation(operation: Any) -> dict[str, Any]:
    try:
        operation()
    except OSError as exc:
        return {
            "blocked": True,
            "errno": errno.errorcode.get(exc.errno or 0, "OS_ERROR"),
        }
    except Exception:
        return {"blocked": True, "errno": "RUNTIME_ERROR"}
    return {"blocked": False, "errno": "NONE"}


def _append_forbidden(path: Path) -> None:
    with path.open("ab") as handle:
        handle.write(b"forbidden")


def run_probe() -> dict[str, Any]:
    """Exercise actual OS boundaries and return only sanitized outcomes."""
    forbidden_reads = {
        "host_home": Path("/Users/host-user/private-canary"),
        "repository": Path("/workspace/.git/HEAD"),
        "ssh": Path("/root/.ssh/id_rsa"),
        "config": Path("/root/.config/private-canary"),
        "vault": Path("/vault/.obsidian/workspace.json"),
        "docker_socket": Path("/var/run/docker.sock"),
    }
    reads = {
        name: _blocked_operation(path.read_bytes)
        for name, path in forbidden_reads.items()
    }

    def connect_external() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(1.0)
            connection.connect(("1.1.1.1", 443))

    shells = {
        name: _blocked_operation(
            lambda path=path: subprocess.run(
                [path, "-c", "exit 0"],
                check=True,
                capture_output=True,
                timeout=1.0,
            )
        )
        for name, path in {
            "sh": "/bin/sh",
            "bash": "/bin/bash",
            "busybox": "/bin/busybox",
        }.items()
    }
    writes = {
        "projection": _blocked_operation(
            lambda: INPUT_PATH.open("ab").write(b"forbidden")
        ),
        "tmp": _blocked_operation(
            lambda: Path("/tmp/phase2-forbidden").write_bytes(b"forbidden")
        ),
        "worker": _blocked_operation(
            lambda: Path("/worker/phase2-forbidden").write_bytes(b"forbidden")
        ),
        "worker_source": _blocked_operation(
            lambda: _append_forbidden(Path("/worker/worker.py"))
        ),
        "etc": _blocked_operation(
            lambda: Path("/etc/phase2-forbidden").write_bytes(b"forbidden")
        ),
        "extra_output": _blocked_operation(
            lambda: Path("/output/extra.json").write_bytes(b"forbidden")
        ),
    }
    return {
        "probe_schema_version": 1,
        "network": _blocked_operation(connect_external),
        "shells": shells,
        "forbidden_reads": reads,
        "forbidden_writes": writes,
    }


def _read_json(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        size = os.fstat(descriptor).st_size
        if size <= 0 or size > MAX_BYTES:
            raise ValueError("input size invalid")
        raw = os.read(descriptor, MAX_BYTES + 1)
    finally:
        os.close(descriptor)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("input must be object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    raw = _canonical_bytes(value)
    if len(raw) > MAX_BYTES:
        raise ValueError("output size invalid")
    flags = os.O_WRONLY | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    mode = arguments[0] if arguments else "candidate"
    if len(arguments) > 1 or mode not in ALLOWED_MODES:
        return 2
    try:
        result = (
            generate_candidate(_read_json(INPUT_PATH))
            if mode == "candidate"
            else run_probe()
        )
        _write_json(OUTPUT_PATH, result)
    except Exception:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
