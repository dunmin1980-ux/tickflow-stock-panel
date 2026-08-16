from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.phase2_option_c import (
    MarketBar,
    PaperAction,
    SimulationConfig,
    canonical_option_c_bytes,
)


def _action_payload(*, side: str = "BUY", quantity: object = 100) -> dict[str, object]:
    return {
        "action_schema_version": 1,
        "action_id": "a" * 64,
        "decision_id": "b" * 64,
        "symbol": "000403.SZ",
        "decision_trade_date": date(2026, 8, 3),
        "decision_at": datetime.fromisoformat("2026-08-03T21:15:00+08:00"),
        "timezone": "Asia/Shanghai",
        "side": side,
        "quantity": quantity,
        "reason_refs": ["fixture-positive-signal"],
        "source_fixture": "DETERMINISTIC_TEST_FIXTURE",
        "simulation_only": "SIMULATION ONLY",
        "can_publish": False,
        "trading_advice": False,
    }


def test_simulation_config_is_decimal_and_canonical() -> None:
    config = SimulationConfig.default()

    assert config.initial_cash_cny == Decimal("100000.00")
    assert config.commission_rate == Decimal("0.0003")
    assert config.minimum_commission_cny == Decimal("5.00")
    assert config.sell_stamp_tax_rate == Decimal("0.0005")
    assert config.slippage_rate == Decimal("0")
    assert config.buy_lot_size == 100
    assert config.simulation_only == "SIMULATION ONLY"
    assert config.can_publish is False
    assert config.trading_advice is False

    raw = canonical_option_c_bytes(config)
    assert b'"initial_cash_cny": "100000.00"' in raw
    assert b'"minimum_commission_cny": "5.00"' in raw
    assert b'"commission_rate": "0.0003"' in raw
    assert raw.endswith(b"\n")


@pytest.mark.parametrize("quantity", [True, 1.5, -100, 0, 50])
def test_buy_action_rejects_invalid_lot_quantity(quantity: object) -> None:
    with pytest.raises(ValidationError):
        PaperAction.model_validate(_action_payload(quantity=quantity))


def test_sell_action_allows_positive_integer_quantity_below_one_lot() -> None:
    action = PaperAction.model_validate(_action_payload(side="SELL", quantity=50))

    assert action.quantity == 50


def test_hold_action_requires_zero_quantity() -> None:
    valid = PaperAction.model_validate(_action_payload(side="HOLD", quantity=0))
    assert valid.quantity == 0

    with pytest.raises(ValidationError):
        PaperAction.model_validate(_action_payload(side="HOLD", quantity=100))


def test_action_rejects_non_shanghai_offset() -> None:
    payload = _action_payload()
    payload["decision_at"] = datetime.fromisoformat("2026-08-03T13:15:00+00:00")

    with pytest.raises(ValidationError):
        PaperAction.model_validate(payload)


def test_market_bar_is_strict_raw_open_evidence() -> None:
    bar = MarketBar(
        market_bar_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        symbol="000403.SZ",
        trade_date=date(2026, 8, 4),
        available_at=datetime.fromisoformat("2026-08-04T15:01:00+08:00"),
        timezone="Asia/Shanghai",
        open=Decimal("10.50"),
        close=Decimal("10.80"),
        price_basis="raw",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )

    assert bar.open == Decimal("10.50")
    assert bar.close == Decimal("10.80")

    with pytest.raises(ValidationError):
        MarketBar.model_validate({**bar.model_dump(), "open": 10.5})
