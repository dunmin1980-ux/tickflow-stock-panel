"""Deterministic single-symbol daily input preparation for the Visual Workbench."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.indicators.pipeline import compute_indicators
from app.schemas.phase2_claims import Claim
from app.schemas.phase2_option_c import (
    ContinuousDayInput,
    ContinuousPaperState,
    DeterministicMarketFixture,
    MarketBar,
    ResearchInterpretation,
    ResearchSignal,
    ValuationBar,
    canonical_option_c_bytes,
)
from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_claims_service import (
    CLAIM_SLUG_BY_PREDICATE,
    CLAIMS_INVALID,
    CLAIMS_VALID,
    PREDICATE_RULES,
    ClaimsValidationResult,
    _claim_common,
    _free_text_count,
    _sensitive_hit_count,
    _trading_claim_count,
    _validate_claim,
    canonical_json_bytes,
    execute_calculation,
)
from app.services.phase2_facts import INDICATOR_COLUMNS, INDICATOR_SPECS, VENDOR_PENDING

SYMBOL = "000403.SZ"
NAME = "派林生物"
TIMEZONE = "Asia/Shanghai"
SOURCE_FIXTURE = "VALIDATED_DAILY_INPUT"
SOURCE_PROVIDER = "stocksdk_tencent_fallback"
MINIMUM_HISTORY_ROWS = 80
MARKET_CLOSE_GATE = time(15, 10)
SHANGHAI_OFFSET = timedelta(hours=8)
ADJUSTMENT_FACTOR_CHANGE_THRESHOLD = Decimal("0.005")

DAILY_CLAIM_PREDICATES = (
    "market_scope",
    "financial_scope",
    "news_scope",
    "industry_scope",
    "daily_close",
    "daily_open_to_close_percent",
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
    "macd_dif_vs_dea",
    "vendor_pending",
)
DAILY_ARTIFACT_NAMES = frozenset(
    {
        "raw_daily.json",
        "qfq_daily.json",
        "facts.json",
        "projection.json",
        "claims.json",
        "claims_validation.json",
        "input.json",
    }
)


class VisualDailyInputError(ValueError):
    """Raised before state mutation when daily evidence is unsafe or unavailable."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VisualFactsBinding(_StrictModel):
    facts_file: Literal["facts.json"]
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    facts_schema_version: Literal[1]


class ValidatedDailyClaimsDocument(_StrictModel):
    claims_schema_version: Literal[1]
    claims_profile: Literal["VALIDATED_DAILY_RESEARCH_V1"]
    source_system: Literal["tickflow-stock-panel"]
    symbol: Literal["000403.SZ"]
    name: Literal["派林生物"]
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: Literal["Asia/Shanghai"]
    facts_binding: VisualFactsBinding
    claims: list[Claim] = Field(min_length=1)
    vendor_pending: list[str] = Field(min_length=4, max_length=4)
    simulation_only: Literal["SIMULATION ONLY"]
    can_publish: Literal[False]
    trading_advice: Literal[False]


class ValidatedDailyManifest(_StrictModel):
    manifest_schema_version: Literal[1]
    manifest_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["VALIDATED_DAILY_INPUT"]
    source_fixture: Literal["VALIDATED_DAILY_INPUT"]
    source_provider: Literal["stocksdk_tencent_fallback"]
    symbol: Literal["000403.SZ"]
    name: Literal["派林生物"]
    requested_date: date
    trade_date: date
    fetched_at: datetime
    evidence_available_at: datetime
    source_request_count: int = Field(ge=1, le=3)
    raw_row_count: int = Field(ge=MINIMUM_HISTORY_ROWS)
    qfq_row_count: int = Field(ge=MINIMUM_HISTORY_ROWS)
    first_trade_date: date
    previous_trade_date: date
    next_trade_date: date
    indicator_pipeline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: dict[str, str]
    simulation_only: Literal["SIMULATION ONLY"]
    can_publish: Literal[False]
    trading_advice: Literal[False]

    def computed_identity(self) -> str:
        payload = self.model_dump(mode="json", exclude={"manifest_identity"})
        return _sha(canonical_json_bytes(payload))


@dataclass(frozen=True)
class DailyMarketSnapshot:
    source_provider: str
    symbol: str
    requested_date: date
    fetched_at: datetime
    raw_rows: list[dict[str, Any]]
    qfq_rows: list[dict[str, Any]]
    trading_dates: list[date]
    request_count: int


class DailyMarketGateway(Protocol):
    def fetch(self, symbol: str, requested_date: date) -> DailyMarketSnapshot: ...


@dataclass(frozen=True)
class ValidatedDailyBundle:
    manifest: ValidatedDailyManifest
    facts: dict[str, Any]
    projection: dict[str, Any]
    claims: ValidatedDailyClaimsDocument
    validation: ClaimsValidationResult
    day_input: ContinuousDayInput


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pipeline_sha() -> str:
    path = Path(__file__).resolve().parents[1] / "indicators" / "pipeline.py"
    return _sha(path.read_bytes())


def _safe_number(value: Any, *, label: str, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise VisualDailyInputError(f"NON_FINITE_MARKET_VALUE:{label}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise VisualDailyInputError(f"NON_FINITE_MARKET_VALUE:{label}") from exc
    if not math.isfinite(number):
        raise VisualDailyInputError(f"NON_FINITE_MARKET_VALUE:{label}")
    return number


def _normalize_rows(rows: list[dict[str, Any]], *, basis: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        raw_date = item.get("trade_date", item.get("date"))
        try:
            trade_date = date.fromisoformat(str(raw_date)[:10])
        except ValueError as exc:
            raise VisualDailyInputError(f"MARKET_DATE_INVALID:{basis}:{index}") from exc
        row = {
            "trade_date": trade_date.isoformat(),
            "open": _safe_number(item.get("open"), label=f"{basis}:open"),
            "high": _safe_number(item.get("high"), label=f"{basis}:high"),
            "low": _safe_number(item.get("low"), label=f"{basis}:low"),
            "close": _safe_number(item.get("close"), label=f"{basis}:close"),
            "volume": _safe_number(item.get("volume"), label=f"{basis}:volume"),
            "amount": _safe_number(
                item.get("amount"),
                label=f"{basis}:amount",
                allow_none=True,
            ),
        }
        if (
            row["open"] <= 0
            or row["close"] <= 0
            or row["high"] < max(row["open"], row["close"], row["low"])
            or row["low"] > min(row["open"], row["close"], row["high"])
            or row["volume"] < 0
            or (row["amount"] is not None and row["amount"] < 0)
        ):
            raise VisualDailyInputError(f"MARKET_OHLCV_INVALID:{basis}:{index}")
        normalized.append(row)
    dates = [row["trade_date"] for row in normalized]
    if dates != sorted(set(dates)):
        raise VisualDailyInputError(f"MARKET_DATES_NOT_SORTED_UNIQUE:{basis}")
    if len(normalized) < MINIMUM_HISTORY_ROWS:
        raise VisualDailyInputError(f"MARKET_HISTORY_TOO_SHORT:{basis}")
    return normalized


def _source_rows_bytes(rows: list[dict[str, Any]]) -> bytes:
    return canonical_json_bytes({"rows": rows})


def _source_rows(raw: bytes, *, basis: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VisualDailyInputError(f"MARKET_EVIDENCE_INVALID:{basis}") from exc
    if not isinstance(payload, dict) or set(payload) != {"rows"}:
        raise VisualDailyInputError(f"MARKET_EVIDENCE_INVALID:{basis}")
    rows = payload["rows"]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise VisualDailyInputError(f"MARKET_EVIDENCE_INVALID:{basis}")
    return _normalize_rows(rows, basis=basis)


def _material_adjustment_factor_change(
    raw_rows: list[dict[str, Any]],
    qfq_rows: list[dict[str, Any]],
    *,
    baseline_trade_date: date,
) -> bool:
    index_by_date = {
        date.fromisoformat(row["trade_date"]): index for index, row in enumerate(raw_rows)
    }
    baseline_index = index_by_date.get(baseline_trade_date)
    if baseline_index is None:
        return True
    factors = []
    for index in (baseline_index, -1):
        factors.append(
            Decimal(str(qfq_rows[index]["close"]))
            / Decimal(str(raw_rows[index]["close"]))
        )
    relative_change = abs((factors[1] / factors[0]) - Decimal("1"))
    return relative_change > ADJUSTMENT_FACTOR_CHANGE_THRESHOLD


def _indicator_values(
    raw_rows: list[dict[str, Any]],
    qfq_rows: list[dict[str, Any]],
) -> dict[str, float]:
    frame = pl.DataFrame(
        [
            {
                "symbol": SYMBOL,
                "date": row["trade_date"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": raw_rows[index]["volume"],
            }
            for index, row in enumerate(qfq_rows)
        ]
    )
    needed = set(INDICATOR_COLUMNS.values())
    computed = compute_indicators(frame, needed=needed)
    latest = computed.tail(1).to_dicts()[0]
    values: dict[str, float] = {}
    for name, column in INDICATOR_COLUMNS.items():
        value = _safe_number(latest.get(column), label=f"indicator:{name}")
        assert value is not None
        values[name] = value
    return values


def _build_facts(
    snapshot: DailyMarketSnapshot,
    raw_rows: list[dict[str, Any]],
    qfq_rows: list[dict[str, Any]],
    *,
    raw_sha: str,
    qfq_sha: str,
    pipeline_sha: str,
) -> dict[str, Any]:
    values = _indicator_values(raw_rows, qfq_rows)
    latest = raw_rows[-1]
    indicators = {
        name: {
            "value": value,
            "price_basis": INDICATOR_SPECS[name]["price_basis"],
            "unit": INDICATOR_SPECS[name]["unit"],
            "calculation_function": "app.indicators.pipeline.compute_indicators",
            "implementation_column": INDICATOR_COLUMNS[name],
            "source_sha256": (
                qfq_sha if INDICATOR_SPECS[name]["data_basis"] == "qfq" else raw_sha
            ),
        }
        for name, value in values.items()
    }
    return {
        "facts_schema_version": 1,
        "facts_profile": "VALIDATED_DAILY_RESEARCH_V1",
        "source_system": "tickflow-stock-panel",
        "source_fixture": SOURCE_FIXTURE,
        "source_provider": snapshot.source_provider,
        "symbol": SYMBOL,
        "name": NAME,
        "trade_date": latest["trade_date"],
        "timezone": TIMEZONE,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "source_evidence": {
            "raw_history_sha256": raw_sha,
            "qfq_history_sha256": qfq_sha,
            "indicator_pipeline_sha256": pipeline_sha,
            "raw_row_count": len(raw_rows),
            "qfq_row_count": len(qfq_rows),
        },
        "price_basis": {
            "daily_ohlcv": "raw",
            "indicator_prices": "qfq",
            "indicator_volume": "raw",
        },
        "units": {
            "volume": "VENDOR_CONFIRMATION_PENDING",
            "amount": "VENDOR_CONFIRMATION_PENDING",
        },
        "daily": {
            "trade_date": latest["trade_date"],
            "open": latest["open"],
            "high": latest["high"],
            "low": latest["low"],
            "close": latest["close"],
            "volume": latest["volume"],
            "amount": latest["amount"],
            "price_basis": "raw",
        },
        "indicators": indicators,
        "scope": {
            "market_scope": "incomplete",
            "financial_scope": "unavailable",
            "news_scope": "unavailable",
            "industry_scope": "manual_verification_required",
        },
        "vendor_pending": list(VENDOR_PENDING),
        "simulation_only": "SIMULATION ONLY",
        "can_publish": False,
        "trading_advice": False,
    }


def _pointer(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.lstrip("/").split("/"):
        current = current[part]
    return current


def _build_claims(facts: dict[str, Any], facts_sha: str) -> ValidatedDailyClaimsDocument:
    trade_date = str(facts["trade_date"])
    claims: list[dict[str, Any]] = []
    for sequence, predicate in enumerate(DAILY_CLAIM_PREDICATES, start=1):
        rule = PREDICATE_RULES[predicate]
        refs = list(rule.fact_refs)
        if predicate == "ma_value_order":
            refs = sorted(
                refs,
                key=lambda ref: (-float(_pointer(facts, ref)), ref.split("/")[2]),
            )
        payload = _claim_common(
            SYMBOL,
            trade_date,
            sequence,
            CLAIM_SLUG_BY_PREDICATE[predicate],
            predicate,
            facts_sha,
            refs,
            calculation_ref=rule.calculation_ref,
        )
        values = [_pointer(facts, ref) for ref in refs]
        if predicate in {"market_scope", "financial_scope", "news_scope"}:
            payload["object"] = {
                "value_type": "scope",
                "scope_key": predicate,
                "value": values[0],
            }
        elif predicate == "industry_scope":
            payload["object"] = {
                "value_type": "scope",
                "scope_key": predicate,
                "value": execute_calculation(
                    "normalize_scope_v1", values, price_bases=["none"], units=["none"]
                ),
            }
        elif predicate == "vendor_pending":
            payload["object"] = {"value_type": "vendor_pending", "items": values[0]}
        elif predicate == "daily_open_to_close_percent":
            payload["object"] = {
                "value_type": "number",
                "value": execute_calculation(
                    "percentage_difference_v1",
                    values,
                    price_bases=["raw", "raw"],
                    units=["raw_price", "raw_price"],
                ),
                "unit": "percent",
            }
        elif predicate == "macd_dif_vs_dea":
            relation = execute_calculation(
                "compare_numbers_v1",
                values,
                price_bases=["qfq", "qfq"],
                units=["qfq_price", "qfq_price"],
            )
            payload["object"] = {
                "value_type": "comparison",
                "left": {
                    "label_id": "macd_dif",
                    "fact_ref": refs[0],
                    "value": values[0],
                    "unit": "qfq_price",
                    "price_basis": "qfq",
                },
                "relation": relation,
                "right": {
                    "label_id": "macd_dea",
                    "fact_ref": refs[1],
                    "value": values[1],
                    "unit": "qfq_price",
                    "price_basis": "qfq",
                },
            }
        elif predicate == "ma_value_order":
            payload["object"] = {
                "value_type": "ordered_relation",
                "operands": [
                    {
                        "label_id": ref.split("/")[2],
                        "fact_ref": ref,
                        "value": value,
                        "unit": "qfq_price",
                        "price_basis": "qfq",
                    }
                    for ref, value in zip(refs, values, strict=True)
                ],
                "relation": execute_calculation(
                    "ordered_relation_v1",
                    values,
                    price_bases=["qfq"] * len(values),
                    units=["qfq_price"] * len(values),
                ),
            }
        else:
            payload["object"] = {
                "value_type": "number",
                "value": values[0],
                "unit": rule.unit,
            }
        claims.append(payload)
    return ValidatedDailyClaimsDocument.model_validate(
        {
            "claims_schema_version": 1,
            "claims_profile": "VALIDATED_DAILY_RESEARCH_V1",
            "source_system": "tickflow-stock-panel",
            "symbol": SYMBOL,
            "name": NAME,
            "trade_date": trade_date,
            "timezone": TIMEZONE,
            "facts_binding": {
                "facts_file": "facts.json",
                "facts_sha256": facts_sha,
                "facts_schema_version": 1,
            },
            "claims": claims,
            "vendor_pending": list(VENDOR_PENDING),
            "simulation_only": "SIMULATION ONLY",
            "can_publish": False,
            "trading_advice": False,
        }
    )


def _validate_claims(
    facts: dict[str, Any],
    document: ValidatedDailyClaimsDocument,
) -> ClaimsValidationResult:
    errors: list[str] = []
    raw = document.model_dump(mode="json")
    free_text = _free_text_count(raw)
    trading = _trading_claim_count(raw)
    sensitive = _sensitive_hit_count(raw)
    expected = _build_claims(facts, document.facts_binding.facts_sha256)
    if canonical_json_bytes(raw) != canonical_json_bytes(expected.model_dump(mode="json")):
        errors.append("claims_deterministic_rebuild_mismatch")
    expected_ids = [
        f"000403SZ-{document.trade_date.replace('-', '')}-{index:03d}-"
        f"{CLAIM_SLUG_BY_PREDICATE[predicate]}"
        for index, predicate in enumerate(DAILY_CLAIM_PREDICATES, start=1)
    ]
    if [claim.claim_id for claim in document.claims] != expected_ids:
        errors.append("claim_id_set_invalid")
    shim = SimpleNamespace(
        symbol=document.symbol,
        trade_date=document.trade_date,
        facts_binding=document.facts_binding,
    )
    pointer_count = 0
    basis_mismatch_count = 0
    unsourced_count = 0
    for claim in document.claims:
        claim_errors, bound, basis_mismatch = _validate_claim(claim, facts, shim)
        pointer_count += bound
        basis_mismatch_count += int(basis_mismatch)
        if any(
            item.startswith(("facts_pointer_invalid:", "claim_value_mismatch:"))
            for item in claim_errors
        ):
            unsourced_count += 1
        errors.extend(claim_errors)
    if free_text:
        errors.append("free_text_field_forbidden")
    if trading:
        errors.append("trading_claim_forbidden")
    if sensitive:
        errors.append("sensitive_shape_detected")
    canonical = canonical_json_bytes(raw)
    unique_errors = sorted(set(errors))
    return ClaimsValidationResult(
        status=CLAIMS_VALID if not unique_errors else CLAIMS_INVALID,
        errors=unique_errors,
        normalized_sha256=_sha(canonical),
        claim_count=len(document.claims),
        facts_pointer_binding_count=pointer_count,
        free_text_field_count=free_text,
        unsourced_claim_count=unsourced_count,
        trading_claim_count=trading,
        raw_qfq_mismatch_count=basis_mismatch_count,
        sensitive_hit_count=sensitive,
    )


def _build_projection(facts: dict[str, Any], facts_sha: str) -> dict[str, Any]:
    projection = {
        "projection_schema_version": 1,
        "projection_profile": "VALIDATED_DAILY_RESEARCH_V1",
        "symbol": SYMBOL,
        "name": NAME,
        "trade_date": facts["trade_date"],
        "timezone": TIMEZONE,
        "facts_sha256": facts_sha,
        "safe_facts": {
            "daily": facts["daily"],
            "indicators": facts["indicators"],
            "scope": facts["scope"],
            "vendor_pending": facts["vendor_pending"],
        },
        "simulation_only": "SIMULATION ONLY",
        "can_publish": False,
        "trading_advice": False,
    }
    projection["projection_sha256"] = _sha(canonical_json_bytes(projection))
    return projection


def _identity(value: dict[str, Any]) -> str:
    return _sha(canonical_json_bytes(value))


def _fixture_identity(
    *,
    trade_date: date,
    raw_sha: str,
    qfq_sha: str,
    facts_sha: str,
    projection_sha: str,
    claims_sha: str,
    pipeline_sha: str,
) -> str:
    return _identity(
        {
            "identity_type": "validated_daily_fixture_v1",
            "source_fixture": SOURCE_FIXTURE,
            "source_provider": SOURCE_PROVIDER,
            "trade_date": trade_date.isoformat(),
            "raw_history_sha256": raw_sha,
            "qfq_history_sha256": qfq_sha,
            "facts_sha256": facts_sha,
            "projection_sha256": projection_sha,
            "claims_sha256": claims_sha,
            "indicator_pipeline_sha256": pipeline_sha,
        }
    )


def _build_signal(
    facts: dict[str, Any],
    claims: ValidatedDailyClaimsDocument,
    *,
    facts_sha: str,
    claims_sha: str,
    fixture_identity: str,
) -> ResearchSignal:
    by_predicate = {claim.predicate: claim for claim in claims.claims}
    macd_relation = by_predicate["macd_dif_vs_dea"].object.relation
    macd_hist = float(by_predicate["macd_hist"].object.value)
    rsi6 = float(by_predicate["rsi6"].object.value)
    if macd_relation == "ABOVE" and macd_hist > 0 and rsi6 >= 50:
        interpretation_code = "POSITIVE_TECHNICAL_STRUCTURE"
        signal_code = "POSITIVE_OBSERVATION"
    elif macd_relation == "BELOW" and macd_hist < 0 and rsi6 < 50:
        interpretation_code = "RISK_TECHNICAL_STRUCTURE"
        signal_code = "RISK_OBSERVATION"
    else:
        interpretation_code = "MIXED_TECHNICAL_STRUCTURE"
        signal_code = "MIXED_OBSERVATION"
    reason_refs = [
        by_predicate[predicate].claim_id
        for predicate in ("macd_dif_vs_dea", "macd_hist", "rsi6")
    ]
    common = {
        "source_fixture": SOURCE_FIXTURE,
        "symbol": SYMBOL,
        "trade_date": facts["trade_date"],
        "facts_sha256": facts_sha,
        "claims_sha256": claims_sha,
        "fixture_identity": fixture_identity,
        "reason_refs": reason_refs,
    }
    interpretation_id = _identity(
        {**common, "identity_type": "research_interpretation_v1", "code": interpretation_code}
    )
    ResearchInterpretation(
        interpretation_schema_version=1,
        interpretation_id=interpretation_id,
        source_fixture=SOURCE_FIXTURE,
        symbol=SYMBOL,
        trade_date=date.fromisoformat(str(facts["trade_date"])),
        code=interpretation_code,
        reason_refs=reason_refs,
        facts_sha256=facts_sha,
        claims_sha256=claims_sha,
        fixture_identity=fixture_identity,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    signal_id = _identity(
        {
            **common,
            "identity_type": "research_signal_v1",
            "interpretation_id": interpretation_id,
            "code": signal_code,
        }
    )
    return ResearchSignal(
        signal_schema_version=1,
        signal_id=signal_id,
        interpretation_id=interpretation_id,
        source_fixture=SOURCE_FIXTURE,
        symbol=SYMBOL,
        trade_date=date.fromisoformat(str(facts["trade_date"])),
        code=signal_code,
        reason_refs=reason_refs,
        facts_sha256=facts_sha,
        claims_sha256=claims_sha,
        fixture_identity=fixture_identity,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _trading_neighbors(trading_dates: list[date], target: date) -> tuple[date, date]:
    dates = sorted(set(trading_dates))
    if dates != trading_dates:
        raise VisualDailyInputError("TRADING_CALENDAR_NOT_SORTED_UNIQUE")
    try:
        index = dates.index(target)
        return dates[index - 1], dates[index + 1]
    except (ValueError, IndexError) as exc:
        raise VisualDailyInputError("TRADING_CALENDAR_INCOMPLETE") from exc


def _build_execution_fixture(
    state: ContinuousPaperState,
    *,
    target: date,
    previous_trade_date: date,
    next_trade_date: date,
    raw_open: float,
) -> DeterministicMarketFixture | None:
    pending = state.pending_action
    if pending is None:
        return None
    action = pending.action
    if action.decision_trade_date != previous_trade_date:
        raise VisualDailyInputError("PENDING_ACTION_NOT_PREVIOUS_TRADING_DAY")
    bar = MarketBar(
        market_bar_schema_version=1,
        source_fixture=action.source_fixture,
        scenario_fixture_identity=action.fixture_identity,
        symbol=SYMBOL,
        trade_date=target,
        previous_trade_date=previous_trade_date,
        available_at=datetime.combine(target, time(9, 30), tzinfo=action.decision_at.tzinfo),
        timezone=TIMEZONE,
        open=Decimal(str(raw_open)),
        price_basis="raw",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    return DeterministicMarketFixture.create(
        market_fixture_schema_version=1,
        source_fixture=action.source_fixture,
        scenario_fixture_identity=action.fixture_identity,
        symbol=SYMBOL,
        trade_dates=[previous_trade_date, target, next_trade_date],
        bars=[bar],
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _write_regular(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _read_regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise VisualDailyInputError(f"ARTIFACT_NOT_REGULAR:{path.name}")
    return path.read_bytes()


@contextmanager
def _exclusive_preparation(input_root: Path) -> Iterator[None]:
    lock_path = input_root / ".preparation.lock"
    if lock_path.is_symlink():
        raise VisualDailyInputError("INPUT_PREPARATION_LOCK_INVALID")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise VisualDailyInputError("INPUT_PREPARATION_LOCK_INVALID") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise VisualDailyInputError("INPUT_PREPARATION_ALREADY_LOCKED") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _manifest_bytes(manifest: ValidatedDailyManifest) -> bytes:
    return canonical_json_bytes(manifest.model_dump(mode="json"))


def load_validated_daily_bundle(input_root: Path, target: date) -> ValidatedDailyBundle:
    day_root = input_root / target.isoformat()
    if day_root.is_symlink() or not day_root.is_dir():
        raise VisualDailyInputError("VALIDATED_DAILY_BUNDLE_MISSING")
    try:
        manifest = ValidatedDailyManifest.model_validate_json(
            _read_regular(day_root / "manifest.json")
        )
    except ValidationError as exc:
        raise VisualDailyInputError("MANIFEST_INVALID") from exc
    if manifest.manifest_identity != manifest.computed_identity():
        raise VisualDailyInputError("MANIFEST_IDENTITY_MISMATCH")
    if set(manifest.artifact_sha256) != DAILY_ARTIFACT_NAMES:
        raise VisualDailyInputError("MANIFEST_ARTIFACT_SET_INVALID")
    artifact_bytes: dict[str, bytes] = {}
    for name, expected in manifest.artifact_sha256.items():
        raw = _read_regular(day_root / name)
        artifact_bytes[name] = raw
        if _sha(raw) != expected:
            raise VisualDailyInputError(f"ARTIFACT_HASH_MISMATCH:{name}")
    try:
        facts = json.loads(artifact_bytes["facts.json"])
        projection = json.loads(artifact_bytes["projection.json"])
        claims = ValidatedDailyClaimsDocument.model_validate_json(
            artifact_bytes["claims.json"]
        )
        validation = ClaimsValidationResult(**json.loads(
            artifact_bytes["claims_validation.json"]
        ))
        day_input = ContinuousDayInput.model_validate_json(
            artifact_bytes["input.json"], strict=False
        )
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise VisualDailyInputError("VALIDATED_DAILY_BUNDLE_INVALID") from exc
    actual_validation = _validate_claims(facts, claims)
    if validation != actual_validation or validation.status != CLAIMS_VALID:
        raise VisualDailyInputError("CLAIMS_VALIDATION_MISMATCH")
    facts_sha = _sha(artifact_bytes["facts.json"])
    claims_sha = _sha(artifact_bytes["claims.json"])
    projection_payload = dict(projection)
    projection_sha = projection_payload.pop("projection_sha256", None)
    expected_safe_facts = {
        "daily": facts.get("daily"),
        "indicators": facts.get("indicators"),
        "scope": facts.get("scope"),
        "vendor_pending": facts.get("vendor_pending"),
    }
    if (
        not isinstance(projection_sha, str)
        or projection_sha != _sha(canonical_json_bytes(projection_payload))
        or projection.get("facts_sha256") != facts_sha
        or projection.get("safe_facts") != expected_safe_facts
        or day_input.projection_sha256 != projection_sha
    ):
        raise VisualDailyInputError("PROJECTION_BINDING_MISMATCH")
    if (
        claims.facts_binding.facts_sha256 != facts_sha
        or day_input.facts_sha256 != facts_sha
        or day_input.claims_sha256 != claims_sha
        or day_input.trade_date != target
        or manifest.trade_date != target
    ):
        raise VisualDailyInputError("VALIDATED_DAILY_BINDING_MISMATCH")
    raw_rows = _source_rows(artifact_bytes["raw_daily.json"], basis="raw")
    qfq_rows = _source_rows(artifact_bytes["qfq_daily.json"], basis="qfq")
    raw_dates = [row["trade_date"] for row in raw_rows]
    qfq_dates = [row["trade_date"] for row in qfq_rows]
    pipeline_sha = _pipeline_sha()
    snapshot = DailyMarketSnapshot(
        source_provider=manifest.source_provider,
        symbol=manifest.symbol,
        requested_date=manifest.requested_date,
        fetched_at=manifest.fetched_at,
        raw_rows=raw_rows,
        qfq_rows=qfq_rows,
        trading_dates=[
            manifest.previous_trade_date,
            manifest.trade_date,
            manifest.next_trade_date,
        ],
        request_count=manifest.source_request_count,
    )
    expected_facts = _build_facts(
        snapshot,
        raw_rows,
        qfq_rows,
        raw_sha=_sha(artifact_bytes["raw_daily.json"]),
        qfq_sha=_sha(artifact_bytes["qfq_daily.json"]),
        pipeline_sha=pipeline_sha,
    )
    expected_projection = _build_projection(expected_facts, facts_sha)
    expected_claims = _build_claims(expected_facts, facts_sha)
    expected_fixture_identity = _fixture_identity(
        trade_date=target,
        raw_sha=_sha(artifact_bytes["raw_daily.json"]),
        qfq_sha=_sha(artifact_bytes["qfq_daily.json"]),
        facts_sha=facts_sha,
        projection_sha=projection_sha,
        claims_sha=claims_sha,
        pipeline_sha=pipeline_sha,
    )
    expected_signal = _build_signal(
        expected_facts,
        expected_claims,
        facts_sha=facts_sha,
        claims_sha=claims_sha,
        fixture_identity=expected_fixture_identity,
    )
    expected_valuation = ValuationBar(
        valuation_bar_schema_version=1,
        source_fixture=SOURCE_FIXTURE,
        scenario_fixture_identity=expected_fixture_identity,
        symbol=SYMBOL,
        trade_date=target,
        previous_trade_date=manifest.previous_trade_date,
        available_at=manifest.fetched_at,
        timezone=TIMEZONE,
        close=Decimal(str(expected_facts["daily"]["close"])),
        price_basis="raw",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    execution_fixture = day_input.execution_fixture
    execution_binding_valid = execution_fixture is None or (
        execution_fixture.trade_dates
        == [manifest.previous_trade_date, target, manifest.next_trade_date]
        and len(execution_fixture.bars) == 1
        and execution_fixture.bars[0].trade_date == target
        and execution_fixture.bars[0].previous_trade_date
        == manifest.previous_trade_date
        and execution_fixture.bars[0].open
        == Decimal(str(expected_facts["daily"]["open"])).quantize(Decimal("0.01"))
    )
    if (
        manifest.requested_date != target
        or manifest.trade_date != target
        or manifest.raw_row_count != len(raw_rows)
        or manifest.qfq_row_count != len(qfq_rows)
        or manifest.first_trade_date != date.fromisoformat(raw_dates[0])
        or raw_dates != qfq_dates
        or raw_dates[-1] != target.isoformat()
        or raw_dates[-2] != manifest.previous_trade_date.isoformat()
        or manifest.indicator_pipeline_sha256 != pipeline_sha
        or artifact_bytes["facts.json"] != canonical_json_bytes(expected_facts)
        or artifact_bytes["projection.json"] != canonical_json_bytes(expected_projection)
        or artifact_bytes["claims.json"]
        != canonical_json_bytes(expected_claims.model_dump(mode="json"))
        or artifact_bytes["claims_validation.json"]
        != canonical_json_bytes(actual_validation.to_dict())
        or day_input.fixture_identity != expected_fixture_identity
        or day_input.signal != expected_signal
        or day_input.valuation_bar != expected_valuation
        or day_input.decision_at != manifest.fetched_at
        or day_input.facts_available_at != manifest.evidence_available_at
        or day_input.projection_available_at != manifest.evidence_available_at
        or day_input.signal_generated_at != manifest.evidence_available_at
        or not execution_binding_valid
    ):
        raise VisualDailyInputError("VALIDATED_DAILY_DERIVATION_MISMATCH")
    return ValidatedDailyBundle(
        manifest=manifest,
        facts=facts,
        projection=projection,
        claims=claims,
        validation=validation,
        day_input=day_input,
    )


class StockSdkTencentDailyGateway:
    """Fetch one raw/qfq history plus the exchange calendar through stock-sdk."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(ZoneInfo(TIMEZONE)))

    def fetch(self, symbol: str, requested_date: date) -> DailyMarketSnapshot:
        from app.plugins.stocksdk import bridge

        common = {
            "op": "visual_daily",
            "symbols": [symbol],
            "start": (requested_date - timedelta(days=550)).strftime("%Y%m%d"),
            "end": requested_date.strftime("%Y%m%d"),
            "concurrency": 1,
        }
        try:
            raw_response = bridge.run_job({**common, "adjust": "none"}, timeout=90)
            qfq_response = bridge.run_job({**common, "adjust": "qfq"}, timeout=90)
            calendar_response = bridge.run_job(
                {
                    "op": "calendar",
                    "start": common["start"],
                    "end": (requested_date + timedelta(days=31)).strftime("%Y%m%d"),
                },
                timeout=30,
            )
        except Exception as exc:
            raise VisualDailyInputError("MARKET_SOURCE_UNAVAILABLE") from exc
        raw_rows = (raw_response.get("rows") or {}).get(symbol) or []
        qfq_rows = (qfq_response.get("rows") or {}).get(symbol) or []
        calendar_rows = calendar_response.get("rows") or []
        try:
            trading_dates = [date.fromisoformat(str(item)) for item in calendar_rows]
        except ValueError as exc:
            raise VisualDailyInputError("TRADING_CALENDAR_INVALID") from exc
        fetched_at = self._now()
        if fetched_at.tzinfo is None:
            raise VisualDailyInputError("MARKET_GATEWAY_CLOCK_INVALID")
        return DailyMarketSnapshot(
            source_provider=SOURCE_PROVIDER,
            symbol=symbol,
            requested_date=requested_date,
            fetched_at=fetched_at.astimezone(ZoneInfo(TIMEZONE)),
            raw_rows=raw_rows,
            qfq_rows=qfq_rows,
            trading_dates=trading_dates,
            request_count=3,
        )


class Phase2VisualDailyInputBuilder:
    def __init__(
        self,
        *,
        input_root: Path,
        market_gateway: DailyMarketGateway | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.input_root = input_root
        self.market_gateway = market_gateway or StockSdkTencentDailyGateway()
        self.now = now or (lambda: datetime.now().astimezone())
        if input_root.is_symlink():
            raise VisualDailyInputError("INPUT_ROOT_MUST_NOT_BE_SYMLINK")
        input_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        input_root.chmod(0o700)

    def readiness(self, target: date) -> dict[str, Any]:
        day_root = self.input_root / target.isoformat()
        if day_root.exists() or day_root.is_symlink():
            bundle = load_validated_daily_bundle(self.input_root, target)
            return {
                "status": "READY_VALIDATED_DAILY",
                "effective_trade_date": bundle.day_input.trade_date.isoformat(),
                "source_fixture": SOURCE_FIXTURE,
                "message": "VALIDATED_DAILY_INPUT_AVAILABLE",
            }
        now = self.now()
        if now.tzinfo is None or now.utcoffset() != SHANGHAI_OFFSET:
            return {"status": "BLOCKED", "message": "SHANGHAI_CLOCK_REQUIRED"}
        if target != now.date():
            return {"status": "BLOCKED", "message": "HISTORICAL_LIVE_PREPARATION_FORBIDDEN"}
        if now.time() < MARKET_CLOSE_GATE:
            return {"status": "WAITING_FOR_CLOSE", "message": "WAIT_UNTIL_15_10"}
        return {"status": "PREPARABLE", "message": "VALIDATED_DAILY_INPUT_CAN_BE_PREPARED"}

    def prepare(
        self,
        target: date,
        state: ContinuousPaperState,
    ) -> ValidatedDailyBundle:
        day_root = self.input_root / target.isoformat()
        if day_root.exists() or day_root.is_symlink():
            return load_validated_daily_bundle(self.input_root, target)
        with _exclusive_preparation(self.input_root):
            if day_root.exists() or day_root.is_symlink():
                return load_validated_daily_bundle(self.input_root, target)
            return self._prepare_unlocked(target, state)

    def _prepare_unlocked(
        self,
        target: date,
        state: ContinuousPaperState,
    ) -> ValidatedDailyBundle:
        day_root = self.input_root / target.isoformat()
        readiness = self.readiness(target)
        if readiness["status"] == "WAITING_FOR_CLOSE":
            raise VisualDailyInputError("MARKET_NOT_CLOSED")
        if readiness["status"] != "PREPARABLE":
            raise VisualDailyInputError(str(readiness["message"]))
        snapshot = self.market_gateway.fetch(SYMBOL, target)
        if (
            snapshot.symbol != SYMBOL
            or snapshot.requested_date != target
            or snapshot.source_provider != SOURCE_PROVIDER
            or snapshot.fetched_at.tzinfo is None
            or snapshot.fetched_at.utcoffset() != SHANGHAI_OFFSET
            or snapshot.fetched_at.date() != target
            or snapshot.fetched_at.time() < MARKET_CLOSE_GATE
        ):
            raise VisualDailyInputError("MARKET_SNAPSHOT_IDENTITY_INVALID")
        raw_rows = _normalize_rows(snapshot.raw_rows, basis="raw")
        qfq_rows = _normalize_rows(snapshot.qfq_rows, basis="qfq")
        raw_dates = [row["trade_date"] for row in raw_rows]
        qfq_dates = [row["trade_date"] for row in qfq_rows]
        if raw_dates != qfq_dates:
            raise VisualDailyInputError("RAW_QFQ_DATE_SET_MISMATCH")
        if raw_dates[-1] != target.isoformat():
            raise VisualDailyInputError("LATEST_TRADE_DATE_MISMATCH")
        if raw_rows[-1]["amount"] is None:
            raise VisualDailyInputError("LATEST_AMOUNT_UNAVAILABLE")
        previous_trade_date, next_trade_date = _trading_neighbors(
            snapshot.trading_dates, target
        )
        if raw_dates[-2] != previous_trade_date.isoformat():
            raise VisualDailyInputError("DAILY_CALENDAR_MISMATCH")
        if (
            state.account.lots
            and state.last_completed_trade_date is not None
            and _material_adjustment_factor_change(
                raw_rows,
                qfq_rows,
                baseline_trade_date=state.last_completed_trade_date,
            )
        ):
            raise VisualDailyInputError("CORPORATE_ACTION_ACCOUNTING_UNSUPPORTED")
        raw_bytes = _source_rows_bytes(raw_rows)
        qfq_bytes = _source_rows_bytes(qfq_rows)
        pipeline_sha = _pipeline_sha()
        facts = _build_facts(
            snapshot,
            raw_rows,
            qfq_rows,
            raw_sha=_sha(raw_bytes),
            qfq_sha=_sha(qfq_bytes),
            pipeline_sha=pipeline_sha,
        )
        facts_bytes = canonical_json_bytes(facts)
        facts_sha = _sha(facts_bytes)
        projection = _build_projection(facts, facts_sha)
        projection_bytes = canonical_json_bytes(projection)
        projection_sha = str(projection["projection_sha256"])
        claims = _build_claims(facts, facts_sha)
        claims_bytes = canonical_json_bytes(claims.model_dump(mode="json"))
        claims_sha = _sha(claims_bytes)
        validation = _validate_claims(facts, claims)
        if validation.status != CLAIMS_VALID or validation.errors:
            raise VisualDailyInputError("CLAIMS_INVALID")
        fixture_identity = _fixture_identity(
            trade_date=target,
            raw_sha=_sha(raw_bytes),
            qfq_sha=_sha(qfq_bytes),
            facts_sha=facts_sha,
            projection_sha=projection_sha,
            claims_sha=claims_sha,
            pipeline_sha=pipeline_sha,
        )
        signal = _build_signal(
            facts,
            claims,
            facts_sha=facts_sha,
            claims_sha=claims_sha,
            fixture_identity=fixture_identity,
        )
        execution_fixture = _build_execution_fixture(
            state,
            target=target,
            previous_trade_date=previous_trade_date,
            next_trade_date=next_trade_date,
            raw_open=float(facts["daily"]["open"]),
        )
        day_input = ContinuousDayInput.create(
            continuous_day_input_schema_version=1,
            source_fixture=SOURCE_FIXTURE,
            symbol=SYMBOL,
            trade_date=target,
            decision_at=snapshot.fetched_at,
            facts_available_at=snapshot.fetched_at,
            projection_available_at=snapshot.fetched_at,
            signal_generated_at=snapshot.fetched_at,
            facts_sha256=facts_sha,
            projection_sha256=projection_sha,
            claims_sha256=claims_sha,
            fixture_identity=fixture_identity,
            signal=signal,
            execution_fixture=execution_fixture,
            valuation_bar=ValuationBar(
                valuation_bar_schema_version=1,
                source_fixture=SOURCE_FIXTURE,
                scenario_fixture_identity=fixture_identity,
                symbol=SYMBOL,
                trade_date=target,
                previous_trade_date=previous_trade_date,
                available_at=snapshot.fetched_at,
                timezone=TIMEZONE,
                close=Decimal(str(facts["daily"]["close"])),
                price_basis="raw",
                simulation_only="SIMULATION ONLY",
                can_publish=False,
                trading_advice=False,
            ),
            simulation_only="SIMULATION ONLY",
            can_publish=False,
            trading_advice=False,
        )
        artifacts = {
            "raw_daily.json": raw_bytes,
            "qfq_daily.json": qfq_bytes,
            "facts.json": facts_bytes,
            "projection.json": projection_bytes,
            "claims.json": claims_bytes,
            "claims_validation.json": canonical_json_bytes(validation.to_dict()),
            "input.json": canonical_option_c_bytes(day_input),
        }
        manifest_values = {
            "manifest_schema_version": 1,
            "status": SOURCE_FIXTURE,
            "source_fixture": SOURCE_FIXTURE,
            "source_provider": SOURCE_PROVIDER,
            "symbol": SYMBOL,
            "name": NAME,
            "requested_date": target,
            "trade_date": target,
            "fetched_at": snapshot.fetched_at,
            "evidence_available_at": snapshot.fetched_at,
            "source_request_count": snapshot.request_count,
            "raw_row_count": len(raw_rows),
            "qfq_row_count": len(qfq_rows),
            "first_trade_date": date.fromisoformat(raw_dates[0]),
            "previous_trade_date": previous_trade_date,
            "next_trade_date": next_trade_date,
            "indicator_pipeline_sha256": pipeline_sha,
            "artifact_sha256": {
                name: _sha(raw) for name, raw in sorted(artifacts.items())
            },
            "simulation_only": "SIMULATION ONLY",
            "can_publish": False,
            "trading_advice": False,
        }
        provisional = ValidatedDailyManifest(
            manifest_identity="0" * 64,
            **manifest_values,
        )
        manifest = ValidatedDailyManifest(
            manifest_identity=provisional.computed_identity(),
            **manifest_values,
        )
        staging = Path(
            tempfile.mkdtemp(prefix=f".{target.isoformat()}.staging-", dir=self.input_root)
        )
        staging.chmod(0o700)
        try:
            for name, raw in sorted({**artifacts, "manifest.json": _manifest_bytes(manifest)}.items()):
                _write_regular(staging / name, raw)
            atomic_publish_directory(staging, day_root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return load_validated_daily_bundle(self.input_root, target)
