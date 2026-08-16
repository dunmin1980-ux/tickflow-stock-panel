"""Strict contracts for the deterministic Phase 2B Option C sidecar."""

from __future__ import annotations

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


class SimulationConfig(SimulationSafety):
    config_version: Literal[1]
    initial_cash_cny: Decimal
    buy_lot_size: Literal[100]
    buy_lots_per_signal: int = Field(ge=1)
    sell_lots_per_signal: int = Field(ge=1)
    commission_rate: Decimal
    minimum_commission_cny: Decimal
    sell_stamp_tax_rate: Decimal
    slippage_rate: Literal[Decimal("0")]
    currency: Literal["CNY"]

    @field_validator(
        "initial_cash_cny",
        "commission_rate",
        "minimum_commission_cny",
        "sell_stamp_tax_rate",
        "slippage_rate",
    )
    @classmethod
    def validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("simulation_decimal_must_be_finite_and_non_negative")
        return value

    @classmethod
    def default(cls) -> SimulationConfig:
        return cls(
            config_version=1,
            initial_cash_cny=Decimal("100000.00"),
            buy_lot_size=100,
            buy_lots_per_signal=1,
            sell_lots_per_signal=1,
            commission_rate=Decimal("0.0003"),
            minimum_commission_cny=Decimal("5.00"),
            sell_stamp_tax_rate=Decimal("0.0005"),
            slippage_rate=Decimal("0"),
            currency="CNY",
            simulation_only="SIMULATION ONLY",
            can_publish=False,
            trading_advice=False,
        )


def _validate_shanghai_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != SHANGHAI_OFFSET:
        raise ValueError("timestamp_must_use_asia_shanghai_offset")
    return value


class PaperAction(SimulationSafety):
    action_schema_version: Literal[1]
    action_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: Literal["000403.SZ"]
    decision_trade_date: date
    decision_at: datetime
    timezone: Literal["Asia/Shanghai"]
    side: ActionSide
    quantity: int = Field(ge=0)
    reason_refs: list[str] = Field(min_length=1)
    source_fixture: FixtureSource

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


class MarketBar(SimulationSafety):
    market_bar_schema_version: Literal[1]
    source_fixture: FixtureSource
    symbol: Literal["000403.SZ"]
    trade_date: date
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
