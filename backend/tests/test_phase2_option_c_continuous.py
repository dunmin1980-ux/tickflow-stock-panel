from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.schemas.phase2_option_c import (
    ContinuousDayInput,
    DeterministicMarketFixture,
    MarketBar,
    ResearchSignal,
    SimulationConfig,
    ValuationBar,
    canonical_option_c_bytes,
)
from app.services.phase2_option_c_continuous import (
    ContinuousRunError,
    new_continuous_state,
    run_continuous_day,
)


def _sha(payload: object) -> str:
    return hashlib.sha256(canonical_option_c_bytes(payload)).hexdigest()


def _signal(
    trade_date: date,
    code: str,
    character: str,
    fixture_identity: str = "9" * 64,
) -> ResearchSignal:
    return ResearchSignal.model_validate(
        {
            "signal_schema_version": 1,
            "signal_id": character * 64,
            "interpretation_id": chr(ord(character) + 1) * 64,
            "source_fixture": "DETERMINISTIC_TEST_FIXTURE",
            "symbol": "000403.SZ",
            "trade_date": trade_date,
            "code": code,
            "reason_refs": [f"fixture-{trade_date.isoformat()}-{code}"],
            "facts_sha256": "c" * 64,
            "claims_sha256": "d" * 64,
            "fixture_identity": fixture_identity,
            "simulation_only": "SIMULATION ONLY",
            "can_publish": False,
            "trading_advice": False,
        }
    )


def _valuation(
    trade_date: date,
    previous_trade_date: date,
    close: str,
    fixture_identity: str = "9" * 64,
) -> ValuationBar:
    return ValuationBar(
        valuation_bar_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity=fixture_identity,
        symbol="000403.SZ",
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        available_at=datetime.fromisoformat(f"{trade_date.isoformat()}T15:01:00+08:00"),
        timezone="Asia/Shanghai",
        close=Decimal(close),
        price_basis="raw",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _execution_fixture(
    decision_date: date,
    execution_date: date,
    next_trade_date: date,
    open_price: str,
    fixture_identity: str = "9" * 64,
) -> DeterministicMarketFixture:
    bar = MarketBar(
        market_bar_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity=fixture_identity,
        symbol="000403.SZ",
        trade_date=execution_date,
        previous_trade_date=decision_date,
        available_at=datetime.fromisoformat(f"{execution_date.isoformat()}T09:30:00+08:00"),
        timezone="Asia/Shanghai",
        open=Decimal(open_price),
        price_basis="raw",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    return DeterministicMarketFixture.create(
        market_fixture_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity=fixture_identity,
        symbol="000403.SZ",
        trade_dates=[decision_date, execution_date, next_trade_date],
        bars=[bar],
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _day_input(
    trade_date: date,
    previous_trade_date: date,
    signal_code: str,
    signal_character: str,
    close: str,
    *,
    execution_fixture: DeterministicMarketFixture | None = None,
    facts_available_at: datetime | None = None,
    fixture_identity: str = "9" * 64,
) -> ContinuousDayInput:
    decision_at = datetime.fromisoformat(f"{trade_date.isoformat()}T15:15:00+08:00")
    signal = _signal(trade_date, signal_code, signal_character, fixture_identity)
    values = {
        "continuous_day_input_schema_version": 1,
        "source_fixture": "DETERMINISTIC_TEST_FIXTURE",
        "symbol": "000403.SZ",
        "trade_date": trade_date,
        "decision_at": decision_at,
        "facts_available_at": facts_available_at or decision_at,
        "projection_available_at": decision_at,
        "signal_generated_at": decision_at,
        "facts_sha256": signal.facts_sha256,
        "projection_sha256": "a" * 64,
        "claims_sha256": signal.claims_sha256,
        "fixture_identity": signal.fixture_identity,
        "signal": signal,
        "execution_fixture": execution_fixture,
        "valuation_bar": _valuation(
            trade_date,
            previous_trade_date,
            close,
            fixture_identity,
        ),
        "simulation_only": "SIMULATION ONLY",
        "can_publish": False,
        "trading_advice": False,
    }
    return ContinuousDayInput.create(**values)


def test_continuous_buy_hold_sell_lifecycle_preserves_state() -> None:
    config = SimulationConfig.default()
    state = new_continuous_state(config)

    day1 = run_continuous_day(
        state,
        _day_input(
            date(2026, 8, 3),
            date(2026, 7, 31),
            "POSITIVE_OBSERVATION",
            "1",
            "10.00",
        ),
    )
    assert day1.action.side == "BUY"
    assert day1.executed_trades == []
    assert day1.state.pending_action is not None
    assert day1.state.account.cash_cny == Decimal("100000.00")

    day2 = run_continuous_day(
        day1.state,
        _day_input(
            date(2026, 8, 4),
            date(2026, 8, 3),
            "MIXED_OBSERVATION",
            "2",
            "10.80",
            execution_fixture=_execution_fixture(
                date(2026, 8, 3),
                date(2026, 8, 4),
                date(2026, 8, 5),
                "10.50",
            ),
        ),
    )
    assert day2.action.side == "HOLD"
    assert [trade.side for trade in day2.executed_trades] == ["BUY"]
    assert day2.state.pending_action is None
    assert day2.state.account.cash_cny == Decimal("98945.00")
    assert day2.state.account.lots[0].sellable_from_trade_date == date(2026, 8, 5)
    assert day2.state.account.unrealized_pnl_cny == Decimal("25.00")

    day3 = run_continuous_day(
        day2.state,
        _day_input(
            date(2026, 8, 5),
            date(2026, 8, 4),
            "RISK_OBSERVATION",
            "3",
            "11.20",
        ),
    )
    assert day3.action.side == "SELL"
    assert day3.executed_trades == []
    assert day3.state.pending_action is not None
    assert day3.state.account.cash_cny == Decimal("98945.00")
    assert day3.state.account.total_equity_cny == Decimal("100065.00")

    day4 = run_continuous_day(
        day3.state,
        _day_input(
            date(2026, 8, 6),
            date(2026, 8, 5),
            "MIXED_OBSERVATION",
            "4",
            "10.90",
            execution_fixture=_execution_fixture(
                date(2026, 8, 5),
                date(2026, 8, 6),
                date(2026, 8, 7),
                "11.00",
            ),
        ),
    )

    assert day4.action.side == "HOLD"
    assert [trade.side for trade in day4.executed_trades] == ["SELL"]
    assert day4.state.pending_action is None
    assert day4.state.account.lots == []
    assert day4.state.account.cash_cny == Decimal("100039.45")
    assert day4.state.account.realized_pnl_cny == Decimal("39.45")
    assert day4.state.account.unrealized_pnl_cny == Decimal("0.00")
    assert day4.state.account.total_equity_cny == Decimal("100039.45")
    assert len(day4.state.decision_ledger) == 4
    assert len(day4.state.account.trades) == 2
    assert [point.trade_date for point in day4.state.equity_history] == [
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
    ]
    assert day4.state.completed_input_identities == [
        day1.day_input.input_identity,
        day2.day_input.input_identity,
        day3.day_input.input_identity,
        day4.day_input.input_identity,
    ]


def test_continuous_day_rejects_same_day_and_future_evidence() -> None:
    state = new_continuous_state(SimulationConfig.default())
    day = _day_input(
        date(2026, 8, 3),
        date(2026, 7, 31),
        "MIXED_OBSERVATION",
        "1",
        "10.00",
    )
    result = run_continuous_day(state, day)

    with pytest.raises(ContinuousRunError, match="duplicate_or_regressed_trade_date"):
        run_continuous_day(result.state, day)

    with pytest.raises(ContinuousRunError, match="future_evidence_not_allowed"):
        run_continuous_day(
            state,
            _day_input(
                date(2026, 8, 3),
                date(2026, 7, 31),
                "MIXED_OBSERVATION",
                "1",
                "10.00",
                facts_available_at=datetime.fromisoformat("2026-08-03T15:16:00+08:00"),
            ),
        )


def test_continuous_day_rejects_execution_fixture_without_pending_action() -> None:
    state = new_continuous_state(SimulationConfig.default())
    day = _day_input(
        date(2026, 8, 4),
        date(2026, 8, 3),
        "MIXED_OBSERVATION",
        "1",
        "10.80",
        execution_fixture=_execution_fixture(
            date(2026, 8, 3),
            date(2026, 8, 4),
            date(2026, 8, 5),
            "10.50",
        ),
    )

    with pytest.raises(ContinuousRunError, match="unexpected_execution_fixture"):
        run_continuous_day(state, day)


def test_day_input_identity_is_content_bound() -> None:
    day = _day_input(
        date(2026, 8, 3),
        date(2026, 7, 31),
        "MIXED_OBSERVATION",
        "1",
        "10.00",
    )
    payload = day.model_dump(mode="json", exclude={"input_identity"})

    assert day.input_identity == _sha(payload)
    with pytest.raises(ValueError, match="continuous_day_input_identity_mismatch"):
        ContinuousDayInput.model_validate({**day.model_dump(), "input_identity": "0" * 64})


def test_pending_execution_binds_prior_fixture_not_current_day_fixture() -> None:
    state = new_continuous_state(SimulationConfig.default())
    day1 = run_continuous_day(
        state,
        _day_input(
            date(2026, 8, 3),
            date(2026, 7, 31),
            "POSITIVE_OBSERVATION",
            "1",
            "10.00",
            fixture_identity="8" * 64,
        ),
    )

    day2_input = _day_input(
        date(2026, 8, 4),
        date(2026, 8, 3),
        "MIXED_OBSERVATION",
        "2",
        "10.80",
        fixture_identity="9" * 64,
        execution_fixture=_execution_fixture(
            date(2026, 8, 3),
            date(2026, 8, 4),
            date(2026, 8, 5),
            "10.50",
            fixture_identity="8" * 64,
        ),
    )
    day2 = run_continuous_day(day1.state, day2_input)

    assert day2.executed_trades[0].fixture_identity == "8" * 64
    assert day2.executed_trades[0].market_fixture_identity == (
        day2_input.execution_fixture.fixture_identity
    )
    assert day2.day_input.fixture_identity == "9" * 64
