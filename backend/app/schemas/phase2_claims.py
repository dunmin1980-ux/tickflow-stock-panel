"""Closed Pydantic schema for deterministic Phase 2 typed claims."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_CLAIM_TYPES = {
    "NUMERIC_OBSERVATION",
    "BOOLEAN_STATE",
    "ENUM_STATE",
    "SAME_BASIS_COMPARISON",
    "ORDERED_RELATION",
    "SCOPE_NOTICE",
    "VENDOR_PENDING_NOTICE",
    "DATA_QUALITY_STATE",
}

FORBIDDEN_CLAIM_TYPES = {
    "RECOMMENDATION",
    "TRADE_ACTION",
    "POSITION_SIZING",
    "BUY_SELL_SIGNAL",
    "TARGET_PRICE",
    "STOP_LOSS",
    "FORECAST",
    "PREDICTION",
    "CATALYST",
    "NEWS_INTERPRETATION",
    "FINANCIAL_INTERPRETATION",
    "INDUSTRY_RANKING",
    "SUPPORT_LEVEL",
    "RESISTANCE_LEVEL",
}

PriceBasis = Literal["raw", "qfq", "none"]
ValueUnit = Literal[
    "raw_price",
    "qfq_price",
    "percent",
    "dimensionless",
    "count",
    "none",
]
CalculationRef = Literal[
    "compare_numbers_v1",
    "ordered_relation_v1",
    "difference_v1",
    "percentage_difference_v1",
    "threshold_compare_v1",
    "normalize_scope_v1",
]
TemplateId = Literal[
    "daily_close_v1",
    "daily_open_to_close_percent_v1",
    "indicator_numeric_v1",
    "contract_state_v1",
    "boolean_state_v1",
    "same_basis_comparison_v1",
    "ordered_relation_v1",
    "scope_notice_v1",
    "vendor_pending_v1",
    "data_quality_count_v1",
]
EnumValue = Literal[
    "ABOVE",
    "BELOW",
    "EQUAL_WITHIN_EPSILON",
    "POSITIVE",
    "NEGATIVE",
    "NEUTRAL",
    "INCOMPLETE",
    "UNAVAILABLE",
    "PENDING_CONFIRMATION",
    "AVAILABLE",
    "PASSED",
    "FRESH",
    "fresh",
]
VendorPendingItem = Literal[
    "intraday_batch_entitlement",
    "first_30m_bucket_includes_09_30",
    "volume_unit",
    "amount_unit",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FactsBinding(StrictModel):
    facts_file: str = Field(
        pattern=r"^reports/phase2_facts/\d{6}(?:SZ|SH|BJ)_facts\.json$"
    )
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    facts_schema_version: Literal[1]


class DataScope(StrictModel):
    market_scope: Literal["incomplete"]
    financial_scope: Literal["unavailable"]
    news_scope: Literal["unavailable"]
    industry_scope: Literal["unavailable"]


class StockSubject(StrictModel):
    entity_type: Literal["stock"]
    symbol: str = Field(pattern=r"^\d{6}\.(?:SZ|SH|BJ)$")


class ClaimScope(StrictModel):
    timeframe: Literal["1d", "1m", "30m", "cross_timeframe", "none"]
    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ClaimProvenance(StrictModel):
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_refs: list[str] = Field(min_length=1)
    calculation_ref: CalculationRef | None
    price_basis: PriceBasis

    @field_validator("fact_refs")
    @classmethod
    def validate_fact_refs(cls, value: list[str]) -> list[str]:
        if any(not item.startswith("/") for item in value):
            raise ValueError("fact refs must be absolute JSON Pointers")
        return value


class RenderingSpec(StrictModel):
    template_id: TemplateId
    display_precision: int | None = Field(default=None, ge=0, le=8)


class NumericValue(StrictModel):
    value_type: Literal["number"]
    value: int | float
    unit: ValueUnit

    @field_validator("value")
    @classmethod
    def validate_finite_number(cls, value: int | float) -> int | float:
        if isinstance(value, bool) or not math.isfinite(value):
            raise ValueError("numeric value must be finite")
        return value


class BooleanValue(StrictModel):
    value_type: Literal["boolean"]
    value: bool
    unit: Literal["none"]


class StateValue(StrictModel):
    value_type: Literal["enum"]
    value: EnumValue
    unit: Literal["none"]


class NumericOperand(StrictModel):
    label_id: Literal[
        "daily_open",
        "daily_close",
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "macd_dif",
        "macd_dea",
        "rsi6",
        "rsi14",
    ]
    fact_ref: str = Field(pattern=r"^/")
    value: int | float
    unit: ValueUnit
    price_basis: PriceBasis

    @field_validator("value")
    @classmethod
    def validate_finite_number(cls, value: int | float) -> int | float:
        if isinstance(value, bool) or not math.isfinite(value):
            raise ValueError("operand value must be finite")
        return value


class ComparisonValue(StrictModel):
    value_type: Literal["comparison"]
    left: NumericOperand
    relation: Literal["ABOVE", "BELOW", "EQUAL_WITHIN_EPSILON"]
    right: NumericOperand


class OrderedValue(StrictModel):
    value_type: Literal["ordered_relation"]
    operands: list[NumericOperand] = Field(min_length=2)
    relation: Literal["DESCENDING", "ASCENDING"]


class ScopeValue(StrictModel):
    value_type: Literal["scope"]
    scope_key: Literal[
        "market_scope",
        "financial_scope",
        "news_scope",
        "industry_scope",
    ]
    value: Literal["incomplete", "unavailable"]


class VendorPendingValue(StrictModel):
    value_type: Literal["vendor_pending"]
    items: list[VendorPendingItem] = Field(min_length=4, max_length=4)


class ClaimBase(StrictModel):
    claim_id: str = Field(
        pattern=r"^\d{6}(?:SZ|SH|BJ)-\d{8}-\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    subject: StockSubject
    provenance: ClaimProvenance
    rendering: RenderingSpec
    scope: ClaimScope
    validation_status: Literal["VALIDATED"]


class NumericObservationClaim(ClaimBase):
    claim_type: Literal["NUMERIC_OBSERVATION"]
    predicate: Literal[
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
        "minute_1m_bar_count",
        "minute_30m_bar_count",
    ]
    object: NumericValue


class BooleanStateClaim(ClaimBase):
    claim_type: Literal["BOOLEAN_STATE"]
    predicate: Literal[
        "adjustment_repeat_stable",
        "adjustment_second_adjustment_detected",
        "minute_30m_first_bucket_mechanically_explained",
    ]
    object: BooleanValue


class EnumStateClaim(ClaimBase):
    claim_type: Literal["ENUM_STATE"]
    predicate: Literal[
        "daily_contract_status",
        "minute_1m_contract_status",
        "minute_30m_contract_status",
        "adjustment_factor_status",
        "data_freshness",
    ]
    object: StateValue


class SameBasisComparisonClaim(ClaimBase):
    claim_type: Literal["SAME_BASIS_COMPARISON"]
    predicate: Literal["macd_dif_vs_dea"]
    object: ComparisonValue


class OrderedRelationClaim(ClaimBase):
    claim_type: Literal["ORDERED_RELATION"]
    predicate: Literal["ma_value_order"]
    object: OrderedValue


class ScopeNoticeClaim(ClaimBase):
    claim_type: Literal["SCOPE_NOTICE"]
    predicate: Literal[
        "market_scope",
        "financial_scope",
        "news_scope",
        "industry_scope",
    ]
    object: ScopeValue


class VendorPendingNoticeClaim(ClaimBase):
    claim_type: Literal["VENDOR_PENDING_NOTICE"]
    predicate: Literal["vendor_pending"]
    object: VendorPendingValue


class DataQualityStateClaim(ClaimBase):
    claim_type: Literal["DATA_QUALITY_STATE"]
    predicate: Literal[
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
    ]
    object: NumericValue


Claim = Annotated[
    NumericObservationClaim
    | BooleanStateClaim
    | EnumStateClaim
    | SameBasisComparisonClaim
    | OrderedRelationClaim
    | ScopeNoticeClaim
    | VendorPendingNoticeClaim
    | DataQualityStateClaim,
    Field(discriminator="claim_type"),
]


class ClaimsDocument(StrictModel):
    claims_schema_version: Literal[1]
    source_system: Literal["tickflow-stock-panel"]
    symbol: str = Field(pattern=r"^\d{6}\.(?:SZ|SH|BJ)$")
    name: str = Field(min_length=1, max_length=40)
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: Literal["Asia/Shanghai"]
    facts_binding: FactsBinding
    scope: DataScope
    claims: list[Claim] = Field(min_length=1)
    vendor_pending: list[VendorPendingItem] = Field(min_length=4, max_length=4)
    trading_advice: Literal[False]


class WorkerClaimsCandidate(StrictModel):
    candidate_schema_version: Literal[1]
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str = Field(pattern=r"^\d{6}\.(?:SZ|SH|BJ)$")
    name: str = Field(min_length=1, max_length=40)
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: Literal["Asia/Shanghai"]
    claims: list[Claim] = Field(min_length=1)
    trading_advice: Literal[False]


def claims_json_schema() -> dict[str, Any]:
    """Return the closed validation schema for a publishable Claims document."""
    return ClaimsDocument.model_json_schema(mode="validation")
