"""Minimal fake-worker boundary for future isolated Phase 2 AI claims."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.schemas.phase2_claims import WorkerClaimsCandidate
from app.services.phase2_claims_service import (
    CALCULATION_EPSILON,
    EXPECTED_PREDICATE_ORDER,
    OPERAND_LABEL_BY_REF,
    PREDICATE_RULES,
    RAW_QFQ_MISMATCH,
    Phase2ClaimsError,
    build_claim_payloads,
    canonical_json_bytes,
    execute_calculation,
    expected_claim_id,
)
from app.services.phase2_facts import FIXED_SYMBOLS

ISOLATION_DESIGN_READY = "ISOLATION_DESIGN_READY"
ISOLATION_RUNTIME_NOT_YET_VERIFIED = "ISOLATION_RUNTIME_NOT_YET_VERIFIED"
WORKER_CANDIDATE_VALID = "WORKER_CANDIDATE_VALID"
WORKER_CANDIDATE_INVALID = "WORKER_CANDIDATE_INVALID"
_MAX_PROJECTION_BYTES = 1_048_576
_SHA256_LENGTH = 64

_INDICATORS = (
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
)
_MINUTE_1M_FIELDS = (
    "bar_count",
    "status",
    "duplicate_timestamp_count",
    "lunch_break_bar_count",
    "material_ohlc_anomaly_count",
    "material_negative_amount_count",
)
_MINUTE_30M_FIELDS = (
    "bar_count",
    "status",
    "first_bucket_mechanically_explained",
    "duplicate_timestamp_count",
    "lunch_break_bar_count",
    "ohlc_mismatch_count",
    "non_first_bucket_volume_amount_mismatch_count",
    "material_ohlc_anomaly_count",
    "material_negative_amount_count",
)
_ADJUSTMENT_FIELDS = (
    "status",
    "repeat_stable",
    "second_adjustment_detected",
)
_SCOPE_FIELDS = (
    "market_scope",
    "financial_scope",
    "news_scope",
    "industry_scope",
)
_ROOT_FIELDS = {
    "projection_schema_version",
    "projection_sha256",
    "facts_sha256",
    "symbol",
    "name",
    "trade_date",
    "timezone",
    "safe_facts",
    "allowed_predicates",
}
_SAFE_FACT_FIELDS = {
    "daily",
    "indicators",
    "minute_1m",
    "minute_30m",
    "adjustment_factors",
    "data_freshness",
    "scope",
    "vendor_pending",
}
class Phase2WorkerProtocolError(ValueError):
    """Raised for a fail-closed worker projection boundary violation."""


@dataclass(frozen=True)
class WorkerCandidateValidationResult:
    status: Literal["WORKER_CANDIDATE_VALID", "WORKER_CANDIDATE_INVALID"]
    errors: list[str]
    claim_count: int
    ai_call_count: int = 0
    provider_attempt_count: int = 0
    external_send_count: int = 0
    isolation_design_status: str = ISOLATION_DESIGN_READY
    isolation_runtime_status: str = ISOLATION_RUNTIME_NOT_YET_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _copy_fields(source: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    try:
        return {field: source[field] for field in fields}
    except (KeyError, TypeError) as exc:
        raise Phase2WorkerProtocolError("facts_missing_approved_field") from exc


def compute_projection_sha256(projection: Mapping[str, Any]) -> str:
    """Hash projection content without its self-referential hash field."""
    payload = dict(projection)
    payload.pop("projection_sha256", None)
    return _sha256_bytes(canonical_json_bytes(payload))


def build_worker_projection(
    facts: Mapping[str, Any],
    facts_sha256: str,
    *,
    facts_bytes: bytes,
) -> dict[str, Any]:
    """Build the only data shape a future isolated worker may read."""
    symbol = facts.get("symbol")
    if symbol not in FIXED_SYMBOLS or facts.get("name") != FIXED_SYMBOLS[symbol]:
        raise Phase2WorkerProtocolError("facts_symbol_scope_invalid")
    if (
        not isinstance(facts_sha256, str)
        or len(facts_sha256) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in facts_sha256)
    ):
        raise Phase2WorkerProtocolError("facts_sha256_invalid")
    if not isinstance(facts_bytes, bytes):
        raise Phase2WorkerProtocolError("facts_bytes_invalid")
    if _sha256_bytes(facts_bytes) != facts_sha256:
        raise Phase2WorkerProtocolError("facts_sha256_mismatch")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite constant")

    try:
        parsed_facts = json.loads(facts_bytes, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Phase2WorkerProtocolError("facts_bytes_invalid") from exc
    if parsed_facts != facts:
        raise Phase2WorkerProtocolError("facts_bytes_mismatch")
    try:
        indicators = {
            name: {"value": facts["indicators"][name]["value"]}
            for name in _INDICATORS
        }
        safe_facts = {
            "daily": _copy_fields(
                facts["daily"],
                ("open", "close", "contract_status"),
            ),
            "indicators": indicators,
            "minute_1m": _copy_fields(facts["minute_1m"], _MINUTE_1M_FIELDS),
            "minute_30m": _copy_fields(facts["minute_30m"], _MINUTE_30M_FIELDS),
            "adjustment_factors": _copy_fields(
                facts["adjustment_factors"],
                _ADJUSTMENT_FIELDS,
            ),
            "data_freshness": facts["data_freshness"],
            "scope": _copy_fields(facts["scope"], _SCOPE_FIELDS),
            "vendor_pending": list(facts["vendor_pending"]),
        }
    except (KeyError, TypeError) as exc:
        raise Phase2WorkerProtocolError("facts_projection_shape_invalid") from exc
    projection: dict[str, Any] = {
        "projection_schema_version": 1,
        "facts_sha256": facts_sha256,
        "symbol": symbol,
        "name": facts.get("name"),
        "trade_date": facts.get("trade_date"),
        "timezone": facts.get("timezone"),
        "safe_facts": safe_facts,
        "allowed_predicates": sorted(PREDICATE_RULES),
    }
    projection["projection_sha256"] = compute_projection_sha256(projection)
    errors = _projection_errors(projection)
    if errors:
        raise Phase2WorkerProtocolError("facts_projection_contract_invalid")
    return projection


def _mapping_keys(value: Any) -> set[str]:
    return set(value) if isinstance(value, Mapping) else set()


def _finite_tree(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_tree(item) for item in value)
    return False


def _projection_errors(projection: Any) -> list[str]:
    if not isinstance(projection, Mapping):
        return ["projection_not_object"]
    errors: list[str] = []
    if _mapping_keys(projection) != _ROOT_FIELDS:
        errors.append("projection_root_fields_invalid")
    if projection.get("projection_schema_version") != 1:
        errors.append("projection_schema_version_invalid")
    symbol = projection.get("symbol")
    if symbol not in FIXED_SYMBOLS or projection.get("name") != FIXED_SYMBOLS.get(symbol):
        errors.append("projection_symbol_scope_invalid")
    if projection.get("timezone") != "Asia/Shanghai":
        errors.append("projection_timezone_invalid")
    trade_date = projection.get("trade_date")
    if not isinstance(trade_date, str) or len(trade_date) != 10:
        errors.append("projection_trade_date_invalid")
    facts_sha256 = projection.get("facts_sha256")
    if (
        not isinstance(facts_sha256, str)
        or len(facts_sha256) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in facts_sha256)
    ):
        errors.append("projection_facts_sha256_invalid")
    if projection.get("allowed_predicates") != sorted(PREDICATE_RULES):
        errors.append("projection_predicates_invalid")
    safe = projection.get("safe_facts")
    if _mapping_keys(safe) != _SAFE_FACT_FIELDS:
        errors.append("projection_safe_facts_invalid")
        safe = {}
    expected_nested = {
        "daily": {"open", "close", "contract_status"},
        "indicators": set(_INDICATORS),
        "minute_1m": set(_MINUTE_1M_FIELDS),
        "minute_30m": set(_MINUTE_30M_FIELDS),
        "adjustment_factors": set(_ADJUSTMENT_FIELDS),
        "scope": set(_SCOPE_FIELDS),
    }
    for field, expected in expected_nested.items():
        if _mapping_keys(safe.get(field)) != expected:
            errors.append(f"projection_safe_field_invalid:{field}")
    indicators = safe.get("indicators")
    if isinstance(indicators, Mapping):
        for name in _INDICATORS:
            if _mapping_keys(indicators.get(name)) != {"value"}:
                errors.append(f"projection_indicator_invalid:{name}")
    if not _finite_tree(safe):
        errors.append("projection_non_finite_value")
    if projection.get("projection_sha256") != compute_projection_sha256(projection):
        errors.append("projection_sha256_mismatch")
    return sorted(set(errors))


def _assert_no_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise Phase2WorkerProtocolError("projection_path_unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise Phase2WorkerProtocolError("projection_symlink_forbidden")


def read_exact_projection(path: Path, allowed_path: Path) -> dict[str, Any]:
    """Read one exact regular projection with no-follow descriptor checks."""
    candidate = Path(os.path.abspath(os.fspath(path)))
    allowed = Path(os.path.abspath(os.fspath(allowed_path)))
    if candidate != allowed:
        raise Phase2WorkerProtocolError("projection_path_not_allowed")
    _assert_no_symlink_components(candidate)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise Phase2WorkerProtocolError("projection_open_failed") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise Phase2WorkerProtocolError("projection_not_regular_file")
        if metadata.st_size > _MAX_PROJECTION_BYTES:
            raise Phase2WorkerProtocolError("projection_too_large")
        chunks: list[bytes] = []
        remaining = _MAX_PROJECTION_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > _MAX_PROJECTION_BYTES:
            raise Phase2WorkerProtocolError("projection_too_large")
        after_open = os.lstat(candidate)
        if (after_open.st_dev, after_open.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise Phase2WorkerProtocolError("projection_replaced_during_read")
    finally:
        os.close(descriptor)

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite constant")

    try:
        value = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Phase2WorkerProtocolError("projection_json_invalid") from exc
    errors = _projection_errors(value)
    if errors:
        raise Phase2WorkerProtocolError("projection_contract_invalid")
    return dict(value)


def _resolve_pointer(root: Mapping[str, Any], pointer: str) -> Any:
    if not pointer.startswith("/") or "~" in pointer:
        raise Phase2WorkerProtocolError("projection_pointer_invalid")
    current: Any = root
    for part in pointer[1:].split("/"):
        if not isinstance(current, Mapping) or part not in current:
            raise Phase2WorkerProtocolError("projection_pointer_invalid")
        current = current[part]
    return current


def _metadata_for_ref(ref: str) -> tuple[str, str]:
    if ref.startswith("/daily/"):
        return "raw_price", "raw"
    if ref.startswith("/indicators/"):
        indicator = ref.split("/")[2]
        if indicator in {"rsi6", "rsi14"}:
            return "dimensionless", "none"
        return "qfq_price", "qfq"
    return "none", "none"


def _same_value(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=0.0,
            abs_tol=CALCULATION_EPSILON,
        )
    return actual == expected


def _rule_errors(claim: Any, safe_facts: Mapping[str, Any]) -> list[str]:
    predicate = claim.predicate
    rule = PREDICATE_RULES.get(predicate)
    if rule is None:
        return [f"claim_predicate_unknown:{predicate}"]
    errors: list[str] = []
    if claim.claim_type != rule.claim_type:
        errors.append(f"claim_type_mismatch:{predicate}")
    refs = list(claim.provenance.fact_refs)
    if rule.dynamic_ref_order:
        refs_match = len(refs) == len(rule.fact_refs) and set(refs) == set(rule.fact_refs)
    else:
        refs_match = refs == list(rule.fact_refs)
    if not refs_match:
        errors.append(f"claim_fact_refs_mismatch:{predicate}")
    if claim.provenance.calculation_ref != rule.calculation_ref:
        errors.append(f"claim_calculation_mismatch:{predicate}")
    if claim.provenance.price_basis != rule.price_basis:
        errors.append(f"claim_basis_mismatch:{predicate}")
    if claim.rendering.template_id != rule.template_id:
        errors.append(f"claim_template_mismatch:{predicate}")
    if claim.rendering.display_precision != rule.display_precision:
        errors.append(f"claim_precision_mismatch:{predicate}")
    if claim.scope.timeframe != rule.timeframe:
        errors.append(f"claim_timeframe_mismatch:{predicate}")
    try:
        values = [_resolve_pointer(safe_facts, ref) for ref in refs]
    except Phase2WorkerProtocolError:
        return [*errors, f"claim_pointer_invalid:{predicate}"]
    units, bases = zip(*(_metadata_for_ref(ref) for ref in refs), strict=True)
    expected: Any
    if rule.calculation_ref is None:
        expected = values[0]
    else:
        try:
            expected = execute_calculation(
                rule.calculation_ref,
                values,
                price_bases=bases,
                units=units,
            )
        except Phase2ClaimsError as exc:
            errors.append(str(exc))
            return errors
    obj = claim.object
    if claim.claim_type in {"NUMERIC_OBSERVATION", "DATA_QUALITY_STATE"}:
        if obj.unit != rule.unit:
            errors.append(f"claim_unit_mismatch:{predicate}")
        if not _same_value(obj.value, expected):
            errors.append(f"claim_value_mismatch:{predicate}")
    elif claim.claim_type in {"BOOLEAN_STATE", "ENUM_STATE"}:
        if not _same_value(obj.value, expected):
            errors.append(f"claim_value_mismatch:{predicate}")
    elif claim.claim_type == "SCOPE_NOTICE":
        if obj.scope_key != predicate or not _same_value(obj.value, expected):
            errors.append(f"claim_value_mismatch:{predicate}")
    elif claim.claim_type == "VENDOR_PENDING_NOTICE":
        if list(obj.items) != list(expected):
            errors.append(f"claim_value_mismatch:{predicate}")
    elif claim.claim_type == "SAME_BASIS_COMPARISON":
        operands = [obj.left, obj.right]
        if [item.fact_ref for item in operands] != refs:
            errors.append(f"claim_operand_ref_mismatch:{predicate}")
        operand_bases = {item.price_basis for item in operands if item.price_basis != "none"}
        if len(operand_bases) > 1:
            errors.append(RAW_QFQ_MISMATCH)
        for item, ref, value in zip(operands, refs, values, strict=True):
            unit, basis = _metadata_for_ref(ref)
            if (
                item.label_id != OPERAND_LABEL_BY_REF.get(ref)
                or item.unit != unit
                or item.price_basis != basis
                or not _same_value(item.value, value)
            ):
                errors.append(f"claim_operand_mismatch:{predicate}")
        if obj.relation != expected:
            errors.append(f"claim_value_mismatch:{predicate}")
    elif claim.claim_type == "ORDERED_RELATION":
        operands = list(obj.operands)
        if [item.fact_ref for item in operands] != refs:
            errors.append(f"claim_operand_ref_mismatch:{predicate}")
        for item, ref, value in zip(operands, refs, values, strict=True):
            unit, basis = _metadata_for_ref(ref)
            if (
                item.label_id != OPERAND_LABEL_BY_REF.get(ref)
                or item.unit != unit
                or item.price_basis != basis
                or not _same_value(item.value, value)
            ):
                errors.append(f"claim_operand_mismatch:{predicate}")
        if obj.relation != expected:
            errors.append(f"claim_value_mismatch:{predicate}")
    return errors


def _parse_worker_output(value: Any) -> dict[str, Any]:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Phase2WorkerProtocolError("worker_output_not_json_object") from exc
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            raise Phase2WorkerProtocolError("worker_output_not_json_object")
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise Phase2WorkerProtocolError("worker_output_not_json_object") from exc
    if not isinstance(value, dict):
        raise Phase2WorkerProtocolError("worker_output_not_json_object")
    return value


def validate_worker_candidate(
    value: Any,
    projection: Mapping[str, Any],
) -> WorkerCandidateValidationResult:
    """Validate a JSON-only candidate against one immutable projection."""
    projection_errors = _projection_errors(projection)
    if projection_errors:
        return WorkerCandidateValidationResult(
            status=WORKER_CANDIDATE_INVALID,
            errors=projection_errors,
            claim_count=0,
        )
    try:
        payload = _parse_worker_output(value)
    except Phase2WorkerProtocolError as exc:
        return WorkerCandidateValidationResult(
            status=WORKER_CANDIDATE_INVALID,
            errors=[str(exc)],
            claim_count=0,
        )
    try:
        candidate = WorkerClaimsCandidate.model_validate(payload)
    except ValidationError:
        claim_count = len(payload.get("claims", [])) if isinstance(payload.get("claims"), list) else 0
        return WorkerCandidateValidationResult(
            status=WORKER_CANDIDATE_INVALID,
            errors=["worker_candidate_schema_invalid"],
            claim_count=claim_count,
        )
    errors: list[str] = []
    for field in ("projection_sha256", "symbol", "name", "trade_date", "timezone"):
        if getattr(candidate, field) != projection.get(field):
            errors.append(f"worker_candidate_{field}_mismatch")
    claim_ids = [claim.claim_id for claim in candidate.claims]
    predicates = [claim.predicate for claim in candidate.claims]
    expected_ids = [
        expected_claim_id(candidate.symbol, candidate.trade_date, index, predicate)
        for index, predicate in enumerate(EXPECTED_PREDICATE_ORDER, start=1)
    ]
    if claim_ids != expected_ids or len(claim_ids) != len(set(claim_ids)):
        errors.append("worker_candidate_claim_ids_invalid")
    if predicates != list(EXPECTED_PREDICATE_ORDER):
        errors.append("worker_candidate_predicate_set_invalid")
    safe_facts = projection["safe_facts"]
    for claim in candidate.claims:
        if claim.subject.symbol != projection["symbol"]:
            errors.append(f"claim_subject_mismatch:{claim.predicate}")
        if claim.scope.as_of != projection["trade_date"]:
            errors.append(f"claim_date_mismatch:{claim.predicate}")
        if claim.provenance.facts_sha256 != projection["facts_sha256"]:
            errors.append(f"claim_facts_sha256_mismatch:{claim.predicate}")
        errors.extend(_rule_errors(claim, safe_facts))
    unique_errors = sorted(set(errors))
    return WorkerCandidateValidationResult(
        status=(
            WORKER_CANDIDATE_VALID if not unique_errors else WORKER_CANDIDATE_INVALID
        ),
        errors=unique_errors,
        claim_count=len(candidate.claims),
    )


class FakeClaimsWorker:
    """Deterministic protocol fixture; it performs no model or provider call."""

    ai_call_count = 0
    provider_attempt_count = 0
    external_send_count = 0
    isolation_design_status = ISOLATION_DESIGN_READY
    isolation_runtime_status = ISOLATION_RUNTIME_NOT_YET_VERIFIED

    def run(self, projection: Mapping[str, Any]) -> str:
        errors = _projection_errors(projection)
        if errors:
            raise Phase2WorkerProtocolError("projection_contract_invalid")
        symbol = str(projection["symbol"])
        trade_date = str(projection["trade_date"])
        claims = build_claim_payloads(
            projection["safe_facts"],
            symbol,
            trade_date,
            str(projection["facts_sha256"]),
        )
        candidate = {
            "candidate_schema_version": 1,
            "projection_sha256": projection["projection_sha256"],
            "symbol": symbol,
            "name": projection["name"],
            "trade_date": trade_date,
            "timezone": projection["timezone"],
            "claims": claims,
            "trading_advice": False,
        }
        return canonical_json_bytes(candidate).decode("utf-8")
