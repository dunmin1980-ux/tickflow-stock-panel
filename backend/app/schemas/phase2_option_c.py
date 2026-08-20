"""Strict contracts for the deterministic Phase 2B Option C sidecar."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MONEY_QUANTUM = Decimal("0.01")
SHANGHAI_OFFSET = timedelta(hours=8)

FixtureSource = Literal[
    "DETERMINISTIC_REFERENCE_FIXTURE",
    "DETERMINISTIC_TEST_FIXTURE",
]
ActionSide = Literal["BUY", "HOLD", "SELL"]
InterpretationCode = Literal[
    "MIXED_TECHNICAL_STRUCTURE",
    "POSITIVE_TEST_STRUCTURE",
    "RISK_TEST_STRUCTURE",
]
SignalCode = Literal[
    "POSITIVE_OBSERVATION",
    "MIXED_OBSERVATION",
    "RISK_OBSERVATION",
    "INVALID",
]
VendorPendingItem = Literal[
    "intraday_batch_entitlement",
    "first_30m_bucket_includes_09_30",
    "volume_unit",
    "amount_unit",
]


def money(value: Decimal | str | int) -> Decimal:
    """Return one finite CNY amount with deterministic cent rounding."""
    if isinstance(value, (bool, float)):
        raise TypeError("money_requires_decimal_string_or_integer")
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("money_must_be_finite")
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


class StrictOptionCModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SimulationSafety(StrictOptionCModel):
    simulation_only: Literal["SIMULATION ONLY"]
    can_publish: Literal[False]
    trading_advice: Literal[False]


class PaperTradingConfig(SimulationSafety):
    config_version: Literal[1]
    initial_cash: Decimal
    lot_size: Literal[100]
    buy_lots_per_signal: int = Field(ge=1)
    sell_lots_per_signal: int = Field(ge=1)
    commission_rate: Decimal
    minimum_commission: Decimal
    sell_fee_rate: Decimal
    t_plus_one: Literal[True]
    execution_policy: Literal["NEXT_TRADING_DAY_OPEN"]
    slippage_rate: Literal[Decimal("0")]
    currency: Literal["CNY"]

    @field_validator(
        "initial_cash",
        "commission_rate",
        "minimum_commission",
        "sell_fee_rate",
        "slippage_rate",
    )
    @classmethod
    def validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("simulation_decimal_must_be_finite_and_non_negative")
        return value

    @classmethod
    def default(cls) -> PaperTradingConfig:
        return cls(
            config_version=1,
            initial_cash=Decimal("100000.00"),
            lot_size=100,
            buy_lots_per_signal=1,
            sell_lots_per_signal=1,
            commission_rate=Decimal("0.0003"),
            minimum_commission=Decimal("5.00"),
            sell_fee_rate=Decimal("0.0005"),
            t_plus_one=True,
            execution_policy="NEXT_TRADING_DAY_OPEN",
            slippage_rate=Decimal("0"),
            currency="CNY",
            simulation_only="SIMULATION ONLY",
            can_publish=False,
            trading_advice=False,
        )


SimulationConfig = PaperTradingConfig


class ReferenceFixtureManifest(SimulationSafety):
    fixture_manifest_version: Literal[1]
    source: Literal["DETERMINISTIC_REFERENCE_FIXTURE"]
    symbol: Literal["000403.SZ"]
    name: Literal["派林生物"]
    trade_date: date
    timezone: Literal["Asia/Shanghai"]
    evidence_available_at: datetime
    facts_file: Literal["reports/phase2_facts/000403SZ_facts.json"]
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_file: Literal[
        "reports/phase2_claims/fixtures/000403SZ_claims.json"
    ]
    claims_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_artifact_simulation_only: Literal["SIMULATION ONLY"]
    claims_artifact_can_publish: Literal[False]
    claims_artifact_trading_advice: Literal[False]
    claims_document_schema_file: Literal[
        "reports/phase2_claims/schema/phase2_claims.schema.json"
    ]
    claims_document_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    worker_candidate_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_daily_summary_file: Literal[
        "reports/phase1_observation/2026-07-31/daily_summary.json"
    ]
    source_daily_summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_renderer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim_count: int = Field(ge=1)
    facts_pointer_binding_count: int = Field(ge=1)
    vendor_pending: list[VendorPendingItem] = Field(min_length=4, max_length=4)

    @field_validator("evidence_available_at")
    @classmethod
    def validate_evidence_available_at(cls, value: datetime) -> datetime:
        return _validate_shanghai_datetime(value)


def _validate_shanghai_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != SHANGHAI_OFFSET:
        raise ValueError("timestamp_must_use_asia_shanghai_offset")
    return value


class PaperAction(SimulationSafety):
    action_schema_version: Literal[1]
    action_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    order_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: Literal["000403.SZ"]
    decision_trade_date: date
    decision_at: datetime
    timezone: Literal["Asia/Shanghai"]
    side: ActionSide
    quantity: int = Field(ge=0)
    reason_refs: list[str] = Field(min_length=1)
    source_fixture: FixtureSource
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signal_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("decision_at")
    @classmethod
    def validate_decision_at(cls, value: datetime) -> datetime:
        return _validate_shanghai_datetime(value)

    @model_validator(mode="after")
    def validate_side_quantity(self) -> PaperAction:
        if self.side == "BUY" and (
            self.quantity <= 0 or self.quantity % 100 != 0
        ):
            raise ValueError("buy_quantity_must_be_positive_board_lot")
        if self.side == "SELL" and self.quantity <= 0:
            raise ValueError("sell_quantity_must_be_positive_integer")
        if self.side == "HOLD" and self.quantity != 0:
            raise ValueError("hold_quantity_must_be_zero")
        return self


class ResearchInterpretation(SimulationSafety):
    interpretation_schema_version: Literal[1]
    interpretation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fixture: FixtureSource
    symbol: Literal["000403.SZ"]
    trade_date: date
    code: InterpretationCode
    reason_refs: list[str] = Field(min_length=1)
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResearchSignal(SimulationSafety):
    signal_schema_version: Literal[1]
    signal_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    interpretation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fixture: FixtureSource
    symbol: Literal["000403.SZ"]
    trade_date: date
    code: SignalCode
    reason_refs: list[str] = Field(min_length=1)
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResearchDecision(SimulationSafety):
    decision_schema_version: Literal[1]
    decision_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: Literal["000403.SZ"]
    name: Literal["派林生物"]
    decision_trade_date: date
    decision_at: datetime
    timezone: Literal["Asia/Shanghai"]
    fixture_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    interpretation: ResearchInterpretation
    signal: ResearchSignal
    action: PaperAction

    @field_validator("decision_at")
    @classmethod
    def validate_decision_at(cls, value: datetime) -> datetime:
        return _validate_shanghai_datetime(value)


class MarketBar(SimulationSafety):
    market_bar_schema_version: Literal[1]
    source_fixture: FixtureSource
    scenario_fixture_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: Literal["000403.SZ"]
    trade_date: date
    previous_trade_date: date
    available_at: datetime
    timezone: Literal["Asia/Shanghai"]
    open: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    price_basis: Literal["raw"]

    @field_validator("available_at")
    @classmethod
    def validate_available_at(cls, value: datetime) -> datetime:
        return _validate_shanghai_datetime(value)

    @field_validator("open", "close")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("market_price_must_be_finite")
        return money(value)

    @model_validator(mode="after")
    def validate_dates(self) -> MarketBar:
        if self.previous_trade_date >= self.trade_date:
            raise ValueError("previous_trade_date_must_precede_trade_date")
        if self.available_at.date() != self.trade_date:
            raise ValueError("bar_availability_date_mismatch")
        return self


class DeterministicMarketFixture(SimulationSafety):
    market_fixture_schema_version: Literal[1]
    source_fixture: Literal["DETERMINISTIC_TEST_FIXTURE"]
    symbol: Literal["000403.SZ"]
    scenario_fixture_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    trade_dates: list[date] = Field(min_length=2)
    bars: list[MarketBar]

    @classmethod
    def create(cls, **values: Any) -> DeterministicMarketFixture:
        provisional = cls.model_construct(fixture_identity="0" * 64, **values)
        payload = provisional.model_dump(mode="json", exclude={"fixture_identity"})
        identity = hashlib.sha256(canonical_option_c_bytes(payload)).hexdigest()
        return cls(fixture_identity=identity, **values)

    def computed_fixture_identity(self) -> str:
        payload = self.model_dump(mode="json", exclude={"fixture_identity"})
        return hashlib.sha256(canonical_option_c_bytes(payload)).hexdigest()

    @model_validator(mode="after")
    def validate_market_calendar_and_bars(self) -> DeterministicMarketFixture:
        if self.trade_dates != sorted(set(self.trade_dates)):
            raise ValueError("market_trade_dates_must_be_sorted_unique")
        bar_dates = [bar.trade_date for bar in self.bars]
        if len(bar_dates) != len(set(bar_dates)):
            raise ValueError("market_bar_dates_must_be_unique")
        expected_identity = self.computed_fixture_identity()
        if self.fixture_identity != expected_identity:
            raise ValueError("market_fixture_identity_mismatch")
        date_index = {trade_date: index for index, trade_date in enumerate(self.trade_dates)}
        for bar in self.bars:
            index = date_index.get(bar.trade_date)
            if (
                bar.source_fixture != self.source_fixture
                or bar.scenario_fixture_identity
                != self.scenario_fixture_identity
                or bar.symbol != self.symbol
                or index is None
                or index == 0
                or bar.previous_trade_date != self.trade_dates[index - 1]
            ):
                raise ValueError("market_bar_calendar_mismatch")
        return self


class PositionLot(SimulationSafety):
    lot_schema_version: Literal[1]
    lot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_action_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: Literal["000403.SZ"]
    acquired_trade_date: date
    sellable_from_trade_date: date
    original_quantity: int = Field(gt=0)
    remaining_quantity: int = Field(ge=0)
    remaining_cost_cny: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_remaining_position(self) -> PositionLot:
        if self.sellable_from_trade_date <= self.acquired_trade_date:
            raise ValueError("sellable_date_must_follow_acquisition")
        if self.remaining_quantity > self.original_quantity:
            raise ValueError("remaining_quantity_exceeds_original")
        if self.remaining_quantity == 0 and self.remaining_cost_cny != 0:
            raise ValueError("closed_lot_cost_must_be_zero")
        if self.remaining_quantity > 0 and self.remaining_cost_cny <= 0:
            raise ValueError("open_lot_cost_must_be_positive")
        return self


class TradeRecord(SimulationSafety):
    trade_schema_version: Literal[1]
    execution_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    order_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    order_timestamp: datetime
    execution_timestamp: datetime
    trade_date: date
    symbol: Literal["000403.SZ"]
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    execution_price: Decimal = Field(gt=0)
    gross_amount_cny: Decimal = Field(gt=0)
    commission_cny: Decimal = Field(ge=0)
    stamp_tax_cny: Decimal = Field(ge=0)
    fees_cny: Decimal = Field(ge=0)
    sell_cost_cny: Decimal = Field(ge=0)
    cost_basis_cny: Decimal = Field(ge=0)
    net_cash_flow_cny: Decimal
    realized_pnl_cny: Decimal
    reason_refs: list[str] = Field(min_length=1)
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signal_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    market_fixture_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("order_timestamp", "execution_timestamp")
    @classmethod
    def validate_trade_timestamp(cls, value: datetime) -> datetime:
        return _validate_shanghai_datetime(value)

    @model_validator(mode="after")
    def validate_accounting_identity(self) -> TradeRecord:
        if self.execution_timestamp.date() != self.trade_date:
            raise ValueError("execution_timestamp_trade_date_mismatch")
        if self.order_timestamp >= self.execution_timestamp:
            raise ValueError("order_must_precede_execution")
        fees = money(self.commission_cny + self.stamp_tax_cny)
        if self.fees_cny != fees:
            raise ValueError("trade_fee_components_mismatch")
        if self.side == "BUY":
            expected_cost = money(self.gross_amount_cny + fees)
            if (
                self.stamp_tax_cny != 0
                or self.sell_cost_cny != 0
                or self.cost_basis_cny != expected_cost
                or self.net_cash_flow_cny != -expected_cost
                or self.realized_pnl_cny != 0
            ):
                raise ValueError("buy_trade_accounting_mismatch")
        else:
            expected_proceeds = money(self.gross_amount_cny - fees)
            if (
                self.sell_cost_cny != fees
                or self.net_cash_flow_cny != expected_proceeds
                or self.realized_pnl_cny
                != money(expected_proceeds - self.cost_basis_cny)
            ):
                raise ValueError("sell_trade_accounting_mismatch")
        return self


class PaperAccount(SimulationSafety):
    account_schema_version: Literal[1]
    config: SimulationConfig
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cash_cny: Decimal = Field(ge=0)
    lots: list[PositionLot]
    trades: list[TradeRecord]
    processed_action_ids: list[str]
    processed_decision_ids: list[str]
    processed_order_ids: list[str]
    processed_idempotency_keys: list[str]
    realized_pnl_cny: Decimal
    unrealized_pnl_cny: Decimal
    market_value_cny: Decimal = Field(ge=0)
    total_equity_cny: Decimal = Field(ge=0)
    peak_equity_cny: Decimal = Field(ge=0)
    current_drawdown_cny: Decimal = Field(ge=0)
    max_drawdown_cny: Decimal = Field(ge=0)
    mark_price: Decimal | None
    mark_trade_date: date | None

    @field_validator(
        "processed_action_ids",
        "processed_decision_ids",
        "processed_order_ids",
        "processed_idempotency_keys",
    )
    @classmethod
    def validate_unique_processed_identities(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("processed_identities_must_be_unique")
        return value


class PriceState(SimulationSafety):
    mark_price: Decimal = Field(gt=0)
    price_basis: Literal["raw"]
    trade_date: date
    source: Literal["PHASE2_FROZEN_FACTS"]


class DailyFactsSummary(SimulationSafety):
    daily_close: Decimal = Field(gt=0)
    daily_close_basis: Literal["raw"]
    macd_relation: Literal["ABOVE", "BELOW", "EQUAL_WITHIN_EPSILON"]
    rsi6: Decimal
    ma_order: list[Literal["ma60", "ma5", "ma10", "ma20"]]
    market_scope: Literal["incomplete"]

    @field_validator("daily_close", "rsi6")
    @classmethod
    def validate_finite_fact(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("daily_fact_must_be_finite")
        return value


class PaperPositionSummary(SimulationSafety):
    cash_cny: Decimal = Field(ge=0)
    quantity: int = Field(ge=0)
    eligible_quantity: int = Field(ge=0)
    ineligible_quantity: int = Field(ge=0)
    position_cost_cny: Decimal = Field(ge=0)
    market_value_cny: Decimal = Field(ge=0)
    realized_pnl_cny: Decimal
    unrealized_pnl_cny: Decimal
    total_equity_cny: Decimal = Field(ge=0)
    cumulative_return_percent: Decimal
    current_drawdown_cny: Decimal = Field(ge=0)
    max_drawdown_cny: Decimal = Field(ge=0)


class ChenQuantDaily(SimulationSafety):
    daily_schema_version: Literal[1]
    status: Literal["OPTION_C_REFERENCE_READY"]
    report_date: date
    symbol: Literal["000403.SZ"]
    name: Literal["派林生物"]
    timezone: Literal["Asia/Shanghai"]
    price_state: PriceState
    facts_summary: DailyFactsSummary
    validated_claim_ids: list[str] = Field(min_length=1)
    interpretation: Literal["MIXED_TECHNICAL_STRUCTURE"]
    research_signal: Literal["MIXED_OBSERVATION"]
    paper_action: Literal["HOLD"]
    action_reason_refs: list[str] = Field(min_length=1)
    position: PaperPositionSummary
    simulated_trades: list[TradeRecord]
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    vendor_pending: list[VendorPendingItem] = Field(min_length=4, max_length=4)
    risk_notices: list[
        Literal[
            "MARKET_SCOPE_INCOMPLETE",
            "FINANCIAL_DATA_UNAVAILABLE",
            "NEWS_DATA_UNAVAILABLE",
            "VENDOR_SEMANTICS_PENDING",
            "SIMULATION_NOT_INVESTMENT_ADVICE",
        ]
    ]
    next_observation_conditions: list[
        Literal[
            "RECHECK_VALIDATED_CLAIMS",
            "VERIFY_VENDOR_PENDING",
            "WAIT_FOR_NEXT_APPROVED_DAILY_EVIDENCE",
        ]
    ]
    real_provider_integration: Literal["DEFERRED_FROZEN"]
    real_provider_attempts: Literal[0]
    real_ai_calls: Literal[0]
    real_trading: Literal["DISABLED"]


class ReplayValidation(SimulationSafety):
    replay_schema_version: Literal[1]
    status: Literal[
        "DETERMINISTIC_REPLAY_PASSED",
        "DETERMINISTIC_REPLAY_FAILED",
    ]
    replay_count: int = Field(ge=0)
    artifact_sha256: dict[str, str]
    mismatched_artifacts: list[str]
    trade_ledger_identical: bool
    position_identical: bool
    pnl_identical: bool
    json_identical: bool
    markdown_identical: bool
    errors: list[str]


def canonical_option_c_bytes(
    value: BaseModel | Mapping[str, Any],
) -> bytes:
    """Serialize an Option C object deterministically with no non-finite JSON."""
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
