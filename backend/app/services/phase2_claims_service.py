"""Deterministic construction and validation for Phase 2 typed claims."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.schemas.phase2_claims import (
    FORBIDDEN_CLAIM_TYPES,
    BooleanStateClaim,
    ClaimsDocument,
    DataQualityStateClaim,
    EnumStateClaim,
    NumericObservationClaim,
    OrderedRelationClaim,
    SameBasisComparisonClaim,
    ScopeNoticeClaim,
    VendorPendingNoticeClaim,
    claims_json_schema,
)
from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_facts import (
    FACTS_SCHEMA_VERSION,
    FIXED_SYMBOLS,
    TIMEZONE,
    VENDOR_PENDING,
    canonical_json_bytes,
    validate_facts_document,
)

CLAIMS_SCHEMA_VERSION = 1
CLAIMS_VALID = "CLAIMS_VALID"
CLAIMS_INVALID = "CLAIMS_INVALID"
RAW_QFQ_MISMATCH = "CLAIM_REJECTED_RAW_QFQ_MISMATCH"
CALCULATION_EPSILON = 1e-10

_FREE_TEXT_FIELDS = {
    "conclusion",
    "analysis",
    "reason",
    "recommendation",
    "outlook",
    "human_note",
    "debug_note",
    "validator_message",
    "human_review_note",
}
_SECRET_PATTERNS = (
    re.compile(r"Authorization\s*:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"Cookie\s*:", re.IGNORECASE),
    re.compile(r"tf_session\s*=", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=]", re.IGNORECASE),
    re.compile(r"BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY", re.IGNORECASE),
)


class Phase2ClaimsError(ValueError):
    """Raised when deterministic Claims cannot be constructed safely."""


@dataclass(frozen=True)
class CalculationSpec:
    input_count: int | None
    minimum_input_count: int
    allowed_value_types: tuple[str, ...]
    same_price_basis_required: bool
    allowed_units: tuple[str, ...]
    output_type: str
    epsilon: float | None
    function: Callable[[Sequence[Any]], Any]


def _compare_numbers(values: Sequence[Any]) -> str:
    left, right = (float(item) for item in values)
    if math.isclose(left, right, rel_tol=0.0, abs_tol=CALCULATION_EPSILON):
        return "EQUAL_WITHIN_EPSILON"
    return "ABOVE" if left > right else "BELOW"


def _ordered_relation(values: Sequence[Any]) -> str:
    numbers = [float(item) for item in values]
    descending = all(
        left > right and not math.isclose(
            left, right, rel_tol=0.0, abs_tol=CALCULATION_EPSILON
        )
        for left, right in pairwise(numbers)
    )
    ascending = all(
        left < right and not math.isclose(
            left, right, rel_tol=0.0, abs_tol=CALCULATION_EPSILON
        )
        for left, right in pairwise(numbers)
    )
    if descending:
        return "DESCENDING"
    if ascending:
        return "ASCENDING"
    raise Phase2ClaimsError("ordered relation inputs are not strictly ordered")


def _difference(values: Sequence[Any]) -> float:
    return float(values[0]) - float(values[1])


def _percentage_difference(values: Sequence[Any]) -> float:
    left, right = (float(item) for item in values)
    if math.isclose(left, 0.0, rel_tol=0.0, abs_tol=CALCULATION_EPSILON):
        raise Phase2ClaimsError("percentage denominator must not be zero")
    return (right - left) / abs(left) * 100.0


def _normalize_scope(values: Sequence[Any]) -> str:
    if list(values) != ["manual_verification_required"]:
        raise Phase2ClaimsError("scope normalization input is not approved")
    return "unavailable"


CALCULATION_REGISTRY: dict[str, CalculationSpec] = {
    "compare_numbers_v1": CalculationSpec(
        input_count=2,
        minimum_input_count=2,
        allowed_value_types=("number",),
        same_price_basis_required=True,
        allowed_units=("raw_price", "qfq_price", "dimensionless"),
        output_type="enum",
        epsilon=CALCULATION_EPSILON,
        function=_compare_numbers,
    ),
    "ordered_relation_v1": CalculationSpec(
        input_count=None,
        minimum_input_count=2,
        allowed_value_types=("number",),
        same_price_basis_required=True,
        allowed_units=("raw_price", "qfq_price", "dimensionless"),
        output_type="ordered_relation",
        epsilon=CALCULATION_EPSILON,
        function=_ordered_relation,
    ),
    "difference_v1": CalculationSpec(
        input_count=2,
        minimum_input_count=2,
        allowed_value_types=("number",),
        same_price_basis_required=True,
        allowed_units=("raw_price", "qfq_price", "dimensionless"),
        output_type="number",
        epsilon=CALCULATION_EPSILON,
        function=_difference,
    ),
    "percentage_difference_v1": CalculationSpec(
        input_count=2,
        minimum_input_count=2,
        allowed_value_types=("number",),
        same_price_basis_required=True,
        allowed_units=("raw_price", "qfq_price", "dimensionless"),
        output_type="number",
        epsilon=CALCULATION_EPSILON,
        function=_percentage_difference,
    ),
    "threshold_compare_v1": CalculationSpec(
        input_count=2,
        minimum_input_count=2,
        allowed_value_types=("number",),
        same_price_basis_required=True,
        allowed_units=("dimensionless",),
        output_type="enum",
        epsilon=CALCULATION_EPSILON,
        function=_compare_numbers,
    ),
    "normalize_scope_v1": CalculationSpec(
        input_count=1,
        minimum_input_count=1,
        allowed_value_types=("string",),
        same_price_basis_required=False,
        allowed_units=("none",),
        output_type="scope",
        epsilon=None,
        function=_normalize_scope,
    ),
}


def execute_calculation(
    calculation_ref: str,
    values: Sequence[Any],
    *,
    price_bases: Sequence[str],
    units: Sequence[str],
) -> Any:
    """Execute one registered calculation after enforcing its metadata."""
    spec = CALCULATION_REGISTRY.get(calculation_ref)
    if spec is None:
        raise Phase2ClaimsError(f"unknown calculation: {calculation_ref}")
    if len(values) < spec.minimum_input_count or (
        spec.input_count is not None and len(values) != spec.input_count
    ):
        raise Phase2ClaimsError("calculation input count mismatch")
    if len(price_bases) != len(values) or len(units) != len(values):
        raise Phase2ClaimsError("calculation metadata count mismatch")
    if spec.same_price_basis_required:
        concrete_bases = {basis for basis in price_bases if basis != "none"}
        if len(concrete_bases) > 1:
            raise Phase2ClaimsError(RAW_QFQ_MISMATCH)
    if any(unit not in spec.allowed_units for unit in units):
        raise Phase2ClaimsError("calculation unit is not allowed")
    return spec.function(values)


@dataclass(frozen=True)
class PredicateRule:
    claim_type: str
    fact_refs: tuple[str, ...]
    unit: str
    price_basis: str
    calculation_ref: str | None
    template_id: str
    display_precision: int | None
    timeframe: str
    object_kind: str
    dynamic_ref_order: bool = False


def _rule(
    claim_type: str,
    refs: Sequence[str],
    unit: str,
    basis: str,
    calculation: str | None,
    template: str,
    precision: int | None,
    timeframe: str,
    object_kind: str,
    *,
    dynamic_ref_order: bool = False,
) -> PredicateRule:
    return PredicateRule(
        claim_type=claim_type,
        fact_refs=tuple(refs),
        unit=unit,
        price_basis=basis,
        calculation_ref=calculation,
        template_id=template,
        display_precision=precision,
        timeframe=timeframe,
        object_kind=object_kind,
        dynamic_ref_order=dynamic_ref_order,
    )


PREDICATE_RULES: dict[str, PredicateRule] = {
    "daily_close": _rule(
        "NUMERIC_OBSERVATION", ["/daily/close"], "raw_price", "raw", None,
        "daily_close_v1", 2, "1d", "numeric"
    ),
    "daily_open_to_close_percent": _rule(
        "NUMERIC_OBSERVATION", ["/daily/open", "/daily/close"], "percent", "raw",
        "percentage_difference_v1", "daily_open_to_close_percent_v1", 4, "1d", "numeric"
    ),
}

for _indicator in (
    "ma5", "ma10", "ma20", "ma60", "macd_dif", "macd_dea", "macd_hist",
    "boll_upper", "boll_middle", "boll_lower", "atr14",
):
    PREDICATE_RULES[_indicator] = _rule(
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
    PREDICATE_RULES[_indicator] = _rule(
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
    PREDICATE_RULES[_predicate] = _rule(
        "NUMERIC_OBSERVATION", [_ref], "count", "none", None,
        "data_quality_count_v1", 0, _timeframe, "numeric"
    )
for _predicate, _ref, _timeframe in (
    ("daily_contract_status", "/daily/contract_status", "1d"),
    ("minute_1m_contract_status", "/minute_1m/status", "1m"),
    ("minute_30m_contract_status", "/minute_30m/status", "30m"),
    ("adjustment_factor_status", "/adjustment_factors/status", "1d"),
    ("data_freshness", "/data_freshness", "1d"),
):
    PREDICATE_RULES[_predicate] = _rule(
        "ENUM_STATE", [_ref], "none", "none", None,
        "contract_state_v1", None, _timeframe, "enum"
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
    PREDICATE_RULES[_predicate] = _rule(
        "BOOLEAN_STATE", [_ref], "none", "none", None,
        "boolean_state_v1", None, _timeframe, "boolean"
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
        PREDICATE_RULES[_predicate] = _rule(
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
PREDICATE_RULES.update(
    {
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
            dynamic_ref_order=True,
        ),
        "market_scope": _rule(
            "SCOPE_NOTICE", ["/scope/market_scope"], "none", "none", None,
            "scope_notice_v1", None, "none", "scope"
        ),
        "financial_scope": _rule(
            "SCOPE_NOTICE", ["/scope/financial_scope"], "none", "none", None,
            "scope_notice_v1", None, "none", "scope"
        ),
        "news_scope": _rule(
            "SCOPE_NOTICE", ["/scope/news_scope"], "none", "none", None,
            "scope_notice_v1", None, "none", "scope"
        ),
        "industry_scope": _rule(
            "SCOPE_NOTICE", ["/scope/industry_scope"], "none", "none",
            "normalize_scope_v1", "scope_notice_v1", None, "none", "scope"
        ),
        "vendor_pending": _rule(
            "VENDOR_PENDING_NOTICE", ["/vendor_pending"], "none", "none", None,
            "vendor_pending_v1", None, "none", "vendor"
        ),
    }
)


@dataclass(frozen=True)
class ClaimsValidationResult:
    status: Literal["CLAIMS_VALID", "CLAIMS_INVALID"]
    errors: list[str]
    normalized_sha256: str | None
    claim_count: int
    facts_pointer_binding_count: int
    free_text_field_count: int
    unsourced_claim_count: int
    trading_claim_count: int
    raw_qfq_mismatch_count: int
    sensitive_hit_count: int
    can_publish: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _facts_path(repo_root: Path, symbol: str) -> Path:
    root = repo_root.resolve(strict=True)
    compact = symbol.replace(".", "")
    candidate = root / "reports/phase2_facts" / f"{compact}_facts.json"
    if candidate.is_symlink():
        raise Phase2ClaimsError("facts_file_must_be_regular")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise Phase2ClaimsError("facts_file_invalid") from exc
    if not resolved.is_file():
        raise Phase2ClaimsError("facts_file_must_be_regular")
    return resolved


def _load_facts(repo_root: Path, symbol: str) -> tuple[dict[str, Any], Path, str]:
    path = _facts_path(repo_root, symbol)
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Phase2ClaimsError("facts_json_invalid") from exc
    if not isinstance(value, dict):
        raise Phase2ClaimsError("facts_json_invalid")
    errors = validate_facts_document(repo_root.resolve(strict=True), value)
    if errors:
        raise Phase2ClaimsError("facts_document_invalid")
    return value, path, _sha256_bytes(raw)


def _decode_pointer_token(token: str) -> str:
    if re.search(r"~(?![01])", token):
        raise Phase2ClaimsError("invalid JSON Pointer escape")
    return token.replace("~1", "/").replace("~0", "~")


def _resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise Phase2ClaimsError("JSON Pointer must be absolute")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = _decode_pointer_token(raw_token)
        if isinstance(current, Mapping):
            if token not in current:
                raise Phase2ClaimsError("JSON Pointer key not found")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                raise Phase2ClaimsError("JSON Pointer index not found")
            current = current[int(token)]
        else:
            raise Phase2ClaimsError("JSON Pointer traverses a scalar")
    return current


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _values_equal(actual: Any, expected: Any) -> bool:
    if _is_number(actual) and _is_number(expected):
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    return actual == expected


def _walk_keys(value: Any) -> Sequence[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            found.append(str(key))
            found.extend(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_keys(item))
    return found


def _free_text_count(value: Any) -> int:
    return sum(key in _FREE_TEXT_FIELDS for key in _walk_keys(value))


def _trading_claim_count(value: Any) -> int:
    if not isinstance(value, Mapping):
        return 0
    claims = value.get("claims")
    if not isinstance(claims, list):
        return 0
    return sum(
        isinstance(claim, Mapping)
        and str(claim.get("claim_type")) in FORBIDDEN_CLAIM_TYPES
        for claim in claims
    )


def _sensitive_hit_count(value: Any) -> int:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return 0
    return sum(bool(pattern.search(text)) for pattern in _SECRET_PATTERNS)


def _claim_common(
    symbol: str,
    trade_date: str,
    sequence: int,
    slug: str,
    predicate: str,
    facts_sha256: str,
    fact_refs: list[str],
    *,
    calculation_ref: str | None = None,
) -> dict[str, Any]:
    rule = PREDICATE_RULES[predicate]
    return {
        "claim_id": (
            f'{symbol.replace(".", "")}-{trade_date.replace("-", "")}-'
            f"{sequence:03d}-{slug}"
        ),
        "claim_type": rule.claim_type,
        "subject": {"entity_type": "stock", "symbol": symbol},
        "predicate": predicate,
        "provenance": {
            "facts_sha256": facts_sha256,
            "fact_refs": fact_refs,
            "calculation_ref": calculation_ref,
            "price_basis": rule.price_basis,
        },
        "rendering": {
            "template_id": rule.template_id,
            "display_precision": rule.display_precision,
        },
        "scope": {"timeframe": rule.timeframe, "as_of": trade_date},
        "validation_status": "VALIDATED",
    }


def build_claims_document(repo_root: Path, symbol: str) -> ClaimsDocument:
    """Build one deterministic typed Claims fixture from committed Facts."""
    if symbol not in FIXED_SYMBOLS:
        raise Phase2ClaimsError(f"symbol is outside fixed scope: {symbol}")
    facts, facts_path, facts_sha256 = _load_facts(repo_root, symbol)
    trade_date = str(facts["trade_date"])
    claims: list[dict[str, Any]] = []
    sequence = 1

    def add_direct(predicate: str, slug: str) -> None:
        nonlocal sequence
        rule = PREDICATE_RULES[predicate]
        value = _resolve_pointer(facts, rule.fact_refs[0])
        claim = _claim_common(
            symbol,
            trade_date,
            sequence,
            slug,
            predicate,
            facts_sha256,
            list(rule.fact_refs),
        )
        if rule.object_kind == "numeric":
            claim["object"] = {
                "value_type": "number",
                "value": value,
                "unit": rule.unit,
            }
        elif rule.object_kind == "boolean":
            claim["object"] = {
                "value_type": "boolean",
                "value": value,
                "unit": "none",
            }
        elif rule.object_kind == "enum":
            claim["object"] = {
                "value_type": "enum",
                "value": value,
                "unit": "none",
            }
        elif rule.object_kind == "scope":
            claim["object"] = {
                "value_type": "scope",
                "scope_key": predicate,
                "value": value,
            }
        elif rule.object_kind == "vendor":
            claim["object"] = {
                "value_type": "vendor_pending",
                "items": value,
            }
        else:
            raise Phase2ClaimsError(f"unsupported direct object kind: {predicate}")
        claims.append(claim)
        sequence += 1

    for predicate in (
        "market_scope",
        "financial_scope",
        "news_scope",
    ):
        add_direct(predicate, predicate.replace("_", "-"))

    industry_rule = PREDICATE_RULES["industry_scope"]
    industry_value = execute_calculation(
        "normalize_scope_v1",
        [_resolve_pointer(facts, industry_rule.fact_refs[0])],
        price_bases=["none"],
        units=["none"],
    )
    industry_claim = _claim_common(
        symbol,
        trade_date,
        sequence,
        "industry-scope",
        "industry_scope",
        facts_sha256,
        list(industry_rule.fact_refs),
        calculation_ref="normalize_scope_v1",
    )
    industry_claim["object"] = {
        "value_type": "scope",
        "scope_key": "industry_scope",
        "value": industry_value,
    }
    claims.append(industry_claim)
    sequence += 1

    add_direct("daily_close", "daily-close")
    percent_rule = PREDICATE_RULES["daily_open_to_close_percent"]
    percent_inputs = [_resolve_pointer(facts, ref) for ref in percent_rule.fact_refs]
    percent_value = execute_calculation(
        "percentage_difference_v1",
        percent_inputs,
        price_bases=["raw", "raw"],
        units=["raw_price", "raw_price"],
    )
    percent_claim = _claim_common(
        symbol,
        trade_date,
        sequence,
        "daily-open-close-percent",
        "daily_open_to_close_percent",
        facts_sha256,
        list(percent_rule.fact_refs),
        calculation_ref="percentage_difference_v1",
    )
    percent_claim["object"] = {
        "value_type": "number",
        "value": percent_value,
        "unit": "percent",
    }
    claims.append(percent_claim)
    sequence += 1

    for predicate in (
        "ma5", "ma10", "ma20", "ma60", "macd_dif", "macd_dea", "macd_hist",
        "rsi6", "rsi14", "boll_upper", "boll_middle", "boll_lower", "atr14",
        "minute_1m_bar_count", "minute_30m_bar_count",
        "daily_contract_status", "minute_1m_contract_status",
        "minute_30m_contract_status", "adjustment_factor_status", "data_freshness",
        "adjustment_repeat_stable", "adjustment_second_adjustment_detected",
        "minute_30m_first_bucket_mechanically_explained",
        "minute_1m_duplicate_timestamp_count", "minute_1m_lunch_break_bar_count",
        "minute_1m_material_ohlc_anomaly_count",
        "minute_1m_material_negative_amount_count",
        "minute_30m_duplicate_timestamp_count", "minute_30m_lunch_break_bar_count",
        "minute_30m_ohlc_mismatch_count",
        "minute_30m_non_first_bucket_volume_amount_mismatch_count",
        "minute_30m_material_ohlc_anomaly_count",
        "minute_30m_material_negative_amount_count",
    ):
        add_direct(predicate, predicate.replace("_", "-"))

    comparison_rule = PREDICATE_RULES["macd_dif_vs_dea"]
    comparison_values = [
        _resolve_pointer(facts, ref) for ref in comparison_rule.fact_refs
    ]
    relation = execute_calculation(
        "compare_numbers_v1",
        comparison_values,
        price_bases=["qfq", "qfq"],
        units=["qfq_price", "qfq_price"],
    )
    comparison_claim = _claim_common(
        symbol,
        trade_date,
        sequence,
        "macd-dif-vs-dea",
        "macd_dif_vs_dea",
        facts_sha256,
        list(comparison_rule.fact_refs),
        calculation_ref="compare_numbers_v1",
    )
    comparison_claim["object"] = {
        "value_type": "comparison",
        "left": {
            "label_id": "macd_dif",
            "fact_ref": comparison_rule.fact_refs[0],
            "value": comparison_values[0],
            "unit": "qfq_price",
            "price_basis": "qfq",
        },
        "relation": relation,
        "right": {
            "label_id": "macd_dea",
            "fact_ref": comparison_rule.fact_refs[1],
            "value": comparison_values[1],
            "unit": "qfq_price",
            "price_basis": "qfq",
        },
    }
    claims.append(comparison_claim)
    sequence += 1

    ordered_rule = PREDICATE_RULES["ma_value_order"]
    ordered_pairs = sorted(
        (
            (ref.split("/")[2], ref, _resolve_pointer(facts, ref))
            for ref in ordered_rule.fact_refs
        ),
        key=lambda item: (-float(item[2]), item[0]),
    )
    ordered_refs = [item[1] for item in ordered_pairs]
    ordered_values = [item[2] for item in ordered_pairs]
    ordered_relation = execute_calculation(
        "ordered_relation_v1",
        ordered_values,
        price_bases=["qfq"] * len(ordered_values),
        units=["qfq_price"] * len(ordered_values),
    )
    ordered_claim = _claim_common(
        symbol,
        trade_date,
        sequence,
        "ma-value-order",
        "ma_value_order",
        facts_sha256,
        ordered_refs,
        calculation_ref="ordered_relation_v1",
    )
    ordered_claim["object"] = {
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
        "relation": ordered_relation,
    }
    claims.append(ordered_claim)
    sequence += 1

    add_direct("vendor_pending", "vendor-pending")
    claims.sort(key=lambda item: item["claim_id"])
    relative_facts_path = facts_path.relative_to(repo_root.resolve(strict=True)).as_posix()
    return ClaimsDocument.model_validate(
        {
            "claims_schema_version": CLAIMS_SCHEMA_VERSION,
            "source_system": "tickflow-stock-panel",
            "symbol": symbol,
            "name": facts["name"],
            "trade_date": trade_date,
            "timezone": TIMEZONE,
            "facts_binding": {
                "facts_file": relative_facts_path,
                "facts_sha256": facts_sha256,
                "facts_schema_version": FACTS_SCHEMA_VERSION,
            },
            "scope": {
                "market_scope": "incomplete",
                "financial_scope": "unavailable",
                "news_scope": "unavailable",
                "industry_scope": "unavailable",
            },
            "claims": claims,
            "vendor_pending": list(VENDOR_PENDING),
            "trading_advice": False,
        }
    )


def build_claims_fixtures(repo_root: Path) -> dict[str, ClaimsDocument]:
    """Build all fixed-scope Claims fixtures in stable symbol order."""
    return {
        symbol: build_claims_document(repo_root, symbol)
        for symbol in FIXED_SYMBOLS
    }


def _validate_claim(
    claim: Any,
    facts: Mapping[str, Any],
    document: ClaimsDocument,
) -> tuple[list[str], int, bool]:
    errors: list[str] = []
    predicate = claim.predicate
    rule = PREDICATE_RULES.get(predicate)
    if rule is None:
        return [f"predicate_unknown:{predicate}"], 0, True
    if claim.claim_type != rule.claim_type:
        errors.append(f"claim_type_mismatch:{predicate}")
    if claim.subject.symbol != document.symbol:
        errors.append(f"claim_subject_mismatch:{predicate}")
    if claim.provenance.facts_sha256 != document.facts_binding.facts_sha256:
        errors.append(f"claim_facts_sha256_mismatch:{predicate}")
    if claim.provenance.calculation_ref != rule.calculation_ref:
        errors.append(f"claim_calculation_mismatch:{predicate}")
    if claim.provenance.price_basis != rule.price_basis:
        errors.append(f"claim_price_basis_mismatch:{predicate}")
    if claim.rendering.template_id != rule.template_id:
        errors.append(f"claim_template_mismatch:{predicate}")
    if claim.rendering.display_precision != rule.display_precision:
        errors.append(f"claim_precision_mismatch:{predicate}")
    if claim.scope.timeframe != rule.timeframe or claim.scope.as_of != document.trade_date:
        errors.append(f"claim_scope_mismatch:{predicate}")

    refs = list(claim.provenance.fact_refs)
    if rule.dynamic_ref_order:
        refs_match = len(refs) == len(set(refs)) and set(refs) == set(rule.fact_refs)
    else:
        refs_match = refs == list(rule.fact_refs)
    if not refs_match:
        errors.append(f"claim_fact_refs_mismatch:{predicate}")

    resolved: list[Any] = []
    binding_count = 0
    for ref in refs:
        try:
            resolved.append(_resolve_pointer(facts, ref))
            binding_count += 1
        except Phase2ClaimsError:
            errors.append(f"facts_pointer_invalid:{predicate}:{ref}")

    basis_mismatch = False
    object_value = claim.object
    if isinstance(claim, (NumericObservationClaim, DataQualityStateClaim)):
        if object_value.unit != rule.unit:
            errors.append(f"claim_unit_mismatch:{predicate}")
        expected: Any = None
        if rule.calculation_ref and len(resolved) == len(refs):
            input_units = ["raw_price"] * len(resolved)
            try:
                expected = execute_calculation(
                    rule.calculation_ref,
                    resolved,
                    price_bases=[rule.price_basis] * len(resolved),
                    units=input_units,
                )
            except Phase2ClaimsError as exc:
                errors.append(f"claim_calculation_failed:{predicate}:{exc}")
        elif len(resolved) == 1:
            expected = resolved[0]
        if expected is None or not _values_equal(object_value.value, expected):
            errors.append(f"claim_value_mismatch:{predicate}")
    elif isinstance(claim, BooleanStateClaim):
        if len(resolved) != 1 or object_value.value is not resolved[0]:
            errors.append(f"claim_value_mismatch:{predicate}")
    elif isinstance(claim, EnumStateClaim):
        if len(resolved) != 1 or object_value.value != resolved[0]:
            errors.append(f"claim_value_mismatch:{predicate}")
    elif isinstance(claim, ScopeNoticeClaim):
        expected_scope: Any = resolved[0] if len(resolved) == 1 else None
        if rule.calculation_ref and len(resolved) == 1:
            try:
                expected_scope = execute_calculation(
                    rule.calculation_ref,
                    resolved,
                    price_bases=["none"],
                    units=["none"],
                )
            except Phase2ClaimsError as exc:
                errors.append(f"claim_calculation_failed:{predicate}:{exc}")
        if object_value.scope_key != predicate or object_value.value != expected_scope:
            errors.append(f"claim_value_mismatch:{predicate}")
    elif isinstance(claim, VendorPendingNoticeClaim):
        if len(resolved) != 1 or object_value.items != resolved[0]:
            errors.append(f"claim_value_mismatch:{predicate}")
    elif isinstance(claim, SameBasisComparisonClaim):
        operands = [object_value.left, object_value.right]
        if [operand.fact_ref for operand in operands] != refs:
            errors.append(f"claim_operand_refs_mismatch:{predicate}")
        operand_bases = [operand.price_basis for operand in operands]
        if len(set(operand_bases)) > 1 or any(
            basis != rule.price_basis for basis in operand_bases
        ):
            errors.append(RAW_QFQ_MISMATCH)
            basis_mismatch = True
        if any(operand.unit != rule.unit for operand in operands):
            errors.append(f"claim_unit_mismatch:{predicate}")
        if len(resolved) == 2:
            for operand, expected in zip(operands, resolved, strict=True):
                if not _values_equal(operand.value, expected):
                    errors.append(f"claim_value_mismatch:{predicate}")
            try:
                relation = execute_calculation(
                    "compare_numbers_v1",
                    resolved,
                    price_bases=operand_bases,
                    units=[operand.unit for operand in operands],
                )
            except Phase2ClaimsError as exc:
                errors.append(str(exc))
                if str(exc) == RAW_QFQ_MISMATCH:
                    basis_mismatch = True
            else:
                if object_value.relation != relation:
                    errors.append(f"claim_value_mismatch:{predicate}")
    elif isinstance(claim, OrderedRelationClaim):
        operands = object_value.operands
        if [operand.fact_ref for operand in operands] != refs:
            errors.append(f"claim_operand_refs_mismatch:{predicate}")
        operand_bases = [operand.price_basis for operand in operands]
        if len(set(operand_bases)) > 1 or any(
            basis != rule.price_basis for basis in operand_bases
        ):
            errors.append(RAW_QFQ_MISMATCH)
            basis_mismatch = True
        if any(operand.unit != rule.unit for operand in operands):
            errors.append(f"claim_unit_mismatch:{predicate}")
        if len(resolved) == len(operands):
            for operand, expected in zip(operands, resolved, strict=True):
                if not _values_equal(operand.value, expected):
                    errors.append(f"claim_value_mismatch:{predicate}")
            try:
                relation = execute_calculation(
                    "ordered_relation_v1",
                    resolved,
                    price_bases=operand_bases,
                    units=[operand.unit for operand in operands],
                )
            except Phase2ClaimsError as exc:
                errors.append(str(exc))
                if str(exc) == RAW_QFQ_MISMATCH:
                    basis_mismatch = True
            else:
                if object_value.relation != relation:
                    errors.append(f"claim_value_mismatch:{predicate}")
    return errors, binding_count, basis_mismatch


def validate_claims_document(
    repo_root: Path,
    value: ClaimsDocument | Mapping[str, Any],
) -> ClaimsValidationResult:
    """Rebind and validate every claim against the immutable Facts file."""
    raw_value = value.model_dump(mode="json") if isinstance(value, ClaimsDocument) else value
    free_text_count = _free_text_count(raw_value)
    trading_count = _trading_claim_count(raw_value)
    sensitive_count = _sensitive_hit_count(raw_value)
    try:
        document = ClaimsDocument.model_validate(raw_value)
    except ValidationError:
        return ClaimsValidationResult(
            status=CLAIMS_INVALID,
            errors=["claims_schema_invalid"],
            normalized_sha256=None,
            claim_count=len(raw_value.get("claims", [])) if isinstance(raw_value, Mapping) and isinstance(raw_value.get("claims"), list) else 0,
            facts_pointer_binding_count=0,
            free_text_field_count=free_text_count,
            unsourced_claim_count=0,
            trading_claim_count=trading_count,
            raw_qfq_mismatch_count=0,
            sensitive_hit_count=sensitive_count,
        )

    errors: list[str] = []
    if free_text_count:
        errors.append("free_text_field_forbidden")
    if trading_count:
        errors.append("trading_claim_forbidden")
    if sensitive_count:
        errors.append("sensitive_shape_detected")
    if document.symbol not in FIXED_SYMBOLS:
        errors.append("symbol_outside_fixed_scope")
        facts = {}
        facts_sha256 = ""
        facts_path = None
    else:
        try:
            facts, facts_path, facts_sha256 = _load_facts(repo_root, document.symbol)
        except Phase2ClaimsError as exc:
            errors.append(str(exc))
            facts = {}
            facts_sha256 = ""
            facts_path = None

    if facts_path is not None:
        expected_relative = facts_path.relative_to(repo_root.resolve(strict=True)).as_posix()
        if document.facts_binding.facts_file != expected_relative:
            errors.append("facts_file_binding_mismatch")
        if document.facts_binding.facts_sha256 != facts_sha256:
            errors.append("facts_sha256_mismatch")
        if document.facts_binding.facts_schema_version != facts.get("facts_schema_version"):
            errors.append("facts_schema_version_mismatch")
        for field in ("symbol", "name", "trade_date", "timezone"):
            if getattr(document, field) != facts.get(field):
                errors.append(f"document_{field}_mismatch")
        if document.vendor_pending != facts.get("vendor_pending") or document.vendor_pending != VENDOR_PENDING:
            errors.append("vendor_pending_mismatch")
        expected_scope = {
            "market_scope": "incomplete",
            "financial_scope": "unavailable",
            "news_scope": "unavailable",
            "industry_scope": "unavailable",
        }
        if document.scope.model_dump(mode="json") != expected_scope:
            errors.append("document_scope_mismatch")

    claim_ids = [claim.claim_id for claim in document.claims]
    if len(claim_ids) != len(set(claim_ids)):
        errors.append("claim_id_duplicate")
    if claim_ids != sorted(claim_ids):
        errors.append("claim_order_invalid")

    pointer_bindings = 0
    unsourced_claims = 0
    basis_mismatches = 0
    for claim in document.claims:
        claim_errors, bound_count, basis_mismatch = _validate_claim(
            claim,
            facts,
            document,
        )
        pointer_bindings += bound_count
        if any(
            error.startswith(("facts_pointer_invalid:", "claim_fact_refs_mismatch:", "claim_value_mismatch:"))
            for error in claim_errors
        ):
            unsourced_claims += 1
        if basis_mismatch:
            basis_mismatches += 1
        errors.extend(claim_errors)

    normalized = canonical_json_bytes(document.model_dump(mode="json"))
    unique_errors = sorted(set(errors))
    return ClaimsValidationResult(
        status=CLAIMS_VALID if not unique_errors else CLAIMS_INVALID,
        errors=unique_errors,
        normalized_sha256=_sha256_bytes(normalized),
        claim_count=len(document.claims),
        facts_pointer_binding_count=pointer_bindings,
        free_text_field_count=free_text_count,
        unsourced_claim_count=unsourced_claims,
        trading_claim_count=trading_count,
        raw_qfq_mismatch_count=basis_mismatches,
        sensitive_hit_count=sensitive_count,
    )


def _resolve_reports_root(
    repo_root: Path,
    reports_root: Path,
    *,
    allow_test_output_root: bool,
) -> Path:
    repo_root = repo_root.resolve(strict=True)
    if reports_root.is_symlink():
        raise Phase2ClaimsError("reports_root_must_not_be_symlink")
    resolved = reports_root.resolve(strict=True)
    if not allow_test_output_root and resolved != repo_root / "reports":
        raise Phase2ClaimsError("reports_root_must_be_repository_reports")
    return resolved


def _write_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        __import__("os").fsync(handle.fileno())


def publish_claims_fixtures(
    repo_root: Path,
    reports_root: Path,
    *,
    _allow_test_output_root: bool = False,
) -> dict[str, Any]:
    """Atomically publish canonical schema, fixtures, and a pre-render index."""
    repo_root = repo_root.resolve(strict=True)
    reports_root = _resolve_reports_root(
        repo_root,
        reports_root,
        allow_test_output_root=_allow_test_output_root,
    )
    fixtures = build_claims_fixtures(repo_root)
    schema_bytes = canonical_json_bytes(claims_json_schema())
    entries: list[dict[str, Any]] = []
    fixture_bytes: dict[str, bytes] = {}
    for symbol, document in fixtures.items():
        validation = validate_claims_document(repo_root, document)
        if validation.status != CLAIMS_VALID:
            raise Phase2ClaimsError(f"generated fixture is invalid: {symbol}")
        filename = f'{symbol.replace(".", "")}_claims.json'
        data = canonical_json_bytes(document.model_dump(mode="json"))
        fixture_bytes[filename] = data
        entries.append(
            {
                "symbol": symbol,
                "name": FIXED_SYMBOLS[symbol],
                "fixture_file": f"fixtures/{filename}",
                "fixture_sha256": _sha256_bytes(data),
                "normalized_sha256": validation.normalized_sha256,
                "claim_count": validation.claim_count,
                "validation_status": validation.status,
                "render_status": "NOT_RENDERED",
                "preview_route": None,
            }
        )
    index = {
        "claims_schema_version": CLAIMS_SCHEMA_VERSION,
        "status": CLAIMS_VALID,
        "rendering_status": "NOT_RENDERED",
        "schema_file": "schema/phase2_claims.schema.json",
        "schema_sha256": _sha256_bytes(schema_bytes),
        "symbol_scope": list(FIXED_SYMBOLS),
        "new_tickflow_api_request_count": 0,
        "new_ai_call_count": 0,
        "new_provider_attempt_count": 0,
        "cloud_mutation_count": 0,
        "obsidian_real_vault_write": False,
        "paper_trading_started": False,
        "integrated_gold_enabled": False,
        "external_send_count": 0,
        "entries": entries,
    }
    staging = Path(
        tempfile.mkdtemp(prefix=".phase2_claims.staging-", dir=reports_root)
    )
    try:
        _write_durable(
            staging / "schema/phase2_claims.schema.json",
            schema_bytes,
        )
        for filename, data in fixture_bytes.items():
            _write_durable(staging / "fixtures" / filename, data)
        _write_durable(
            staging / "claims_index.json",
            canonical_json_bytes(index),
        )
        atomic_publish_directory(staging, reports_root / "phase2_claims")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return index


def _load_json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase2ClaimsError("artifact_must_be_regular_file")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite constant: {value}")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Phase2ClaimsError("artifact_json_invalid") from exc
    if not isinstance(value, dict):
        raise Phase2ClaimsError("artifact_json_must_be_object")
    return value


def validate_claims_directory(repo_root: Path, claims_root: Path) -> dict[str, Any]:
    """Validate the complete schema/fixture artifact set without external calls."""
    repo_root = repo_root.resolve(strict=True)
    errors: list[str] = []
    if claims_root.is_symlink() or not claims_root.is_dir():
        return {
            "status": CLAIMS_INVALID,
            "errors": ["claims_directory_invalid"],
        }
    claims_root = claims_root.resolve(strict=True)
    allowed_root_names = {"schema", "fixtures", "rendered", "claims_index.json"}
    if {item.name for item in claims_root.iterdir()} - allowed_root_names:
        errors.append("claims_artifact_file_set_invalid")
    schema_path = claims_root / "schema/phase2_claims.schema.json"
    fixture_root = claims_root / "fixtures"
    index_path = claims_root / "claims_index.json"
    try:
        index = _load_json_object(index_path)
        schema_value = _load_json_object(schema_path)
    except Phase2ClaimsError as exc:
        return {"status": CLAIMS_INVALID, "errors": [str(exc)]}
    expected_schema_bytes = canonical_json_bytes(claims_json_schema())
    if schema_value != claims_json_schema() or schema_path.read_bytes() != expected_schema_bytes:
        errors.append("claims_schema_artifact_mismatch")
    if index.get("schema_sha256") != _sha256_bytes(expected_schema_bytes):
        errors.append("claims_index_schema_hash_mismatch")
    expected_filenames = {
        f'{symbol.replace(".", "")}_claims.json' for symbol in FIXED_SYMBOLS
    }
    if fixture_root.is_symlink() or not fixture_root.is_dir():
        errors.append("claims_fixture_directory_invalid")
        actual_filenames: set[str] = set()
    else:
        actual_filenames = {item.name for item in fixture_root.iterdir()}
    if actual_filenames != expected_filenames:
        errors.append("claims_fixture_file_set_invalid")
    index_entries = index.get("entries")
    if not isinstance(index_entries, list):
        index_entries = []
        errors.append("claims_index_entries_invalid")
    index_by_symbol = {
        item.get("symbol"): item
        for item in index_entries
        if isinstance(item, dict) and isinstance(item.get("symbol"), str)
    }
    symbol_status: dict[str, str] = {}
    total_claims = 0
    total_bindings = 0
    total_unsourced = 0
    total_free_text = 0
    total_trading = 0
    total_sensitive = 0
    total_basis_mismatch = 0
    for symbol in FIXED_SYMBOLS:
        filename = f'{symbol.replace(".", "")}_claims.json'
        path = fixture_root / filename
        try:
            value = _load_json_object(path)
        except Phase2ClaimsError as exc:
            errors.append(f"fixture_invalid:{symbol}:{exc}")
            symbol_status[symbol] = CLAIMS_INVALID
            continue
        validation = validate_claims_document(repo_root, value)
        symbol_status[symbol] = validation.status
        if validation.status != CLAIMS_VALID:
            errors.extend(f"{symbol}:{item}" for item in validation.errors)
        total_claims += validation.claim_count
        total_bindings += validation.facts_pointer_binding_count
        total_unsourced += validation.unsourced_claim_count
        total_free_text += validation.free_text_field_count
        total_trading += validation.trading_claim_count
        total_sensitive += validation.sensitive_hit_count
        total_basis_mismatch += validation.raw_qfq_mismatch_count
        entry = index_by_symbol.get(symbol)
        expected_entry = {
            "symbol": symbol,
            "name": FIXED_SYMBOLS[symbol],
            "fixture_file": f"fixtures/{filename}",
            "fixture_sha256": _sha256_bytes(path.read_bytes()),
            "normalized_sha256": validation.normalized_sha256,
            "claim_count": validation.claim_count,
            "validation_status": validation.status,
            "render_status": entry.get("render_status") if isinstance(entry, dict) else None,
            "preview_route": entry.get("preview_route") if isinstance(entry, dict) else None,
        }
        if not isinstance(entry, dict) or any(
            entry.get(key) != expected for key, expected in expected_entry.items()
        ):
            errors.append(f"claims_index_entry_mismatch:{symbol}")
    for key, expected in {
        "claims_schema_version": CLAIMS_SCHEMA_VERSION,
        "status": CLAIMS_VALID,
        "symbol_scope": list(FIXED_SYMBOLS),
        "new_tickflow_api_request_count": 0,
        "new_ai_call_count": 0,
        "new_provider_attempt_count": 0,
        "cloud_mutation_count": 0,
        "obsidian_real_vault_write": False,
        "paper_trading_started": False,
        "integrated_gold_enabled": False,
        "external_send_count": 0,
    }.items():
        if index.get(key) != expected:
            errors.append(f"claims_index_field_invalid:{key}")
    unique_errors = sorted(set(errors))
    return {
        "status": CLAIMS_VALID if not unique_errors else CLAIMS_INVALID,
        "errors": unique_errors,
        "symbols": symbol_status,
        "claim_count": total_claims,
        "facts_pointer_binding_count": total_bindings,
        "free_text_field_count": total_free_text,
        "unsourced_claim_count": total_unsourced,
        "trading_claim_count": total_trading,
        "raw_qfq_mismatch_count": total_basis_mismatch,
        "sensitive_hit_count": total_sensitive,
        "new_tickflow_api_request_count": 0,
        "new_ai_call_count": 0,
        "new_provider_attempt_count": 0,
        "cloud_mutation_count": 0,
    }
