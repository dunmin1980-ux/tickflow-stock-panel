"""Closed deterministic BUY/HOLD/SELL fixture for MVP release verification."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal

from app.schemas.phase2_option_c import (
    ContinuousDayInput,
    DeterministicMarketFixture,
    MarketBar,
    ResearchSignal,
    ValuationBar,
    canonical_option_c_bytes,
)

SCENARIO_IDENTITY = "9" * 64
FACTS_IDENTITY = "c" * 64
PROJECTION_IDENTITY = "a" * 64
CLAIMS_IDENTITY = "d" * 64


def _identity(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_option_c_bytes(payload)).hexdigest()


def _signal(trade_date: date, code: str) -> ResearchSignal:
    common = {
        "source_fixture": "DETERMINISTIC_TEST_FIXTURE",
        "symbol": "000403.SZ",
        "trade_date": trade_date.isoformat(),
        "code": code,
        "facts_sha256": FACTS_IDENTITY,
        "claims_sha256": CLAIMS_IDENTITY,
        "fixture_identity": SCENARIO_IDENTITY,
    }
    interpretation_id = _identity({**common, "identity_type": "mvp_test_interpretation_v1"})
    signal_id = _identity(
        {
            **common,
            "identity_type": "mvp_test_signal_v1",
            "interpretation_id": interpretation_id,
        }
    )
    return ResearchSignal(
        signal_schema_version=1,
        signal_id=signal_id,
        interpretation_id=interpretation_id,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        symbol="000403.SZ",
        trade_date=trade_date,
        code=code,
        reason_refs=[f"mvp-release-{trade_date.isoformat()}-{code}"],
        facts_sha256=FACTS_IDENTITY,
        claims_sha256=CLAIMS_IDENTITY,
        fixture_identity=SCENARIO_IDENTITY,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _valuation(
    trade_date: date,
    previous_trade_date: date,
    close: str,
) -> ValuationBar:
    return ValuationBar(
        valuation_bar_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity=SCENARIO_IDENTITY,
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
) -> DeterministicMarketFixture:
    bar = MarketBar(
        market_bar_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity=SCENARIO_IDENTITY,
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
        scenario_fixture_identity=SCENARIO_IDENTITY,
        symbol="000403.SZ",
        trade_dates=[decision_date, execution_date, next_trade_date],
        bars=[bar],
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _day(
    trade_date: date,
    previous_trade_date: date,
    signal_code: str,
    close: str,
    execution_fixture: DeterministicMarketFixture | None = None,
) -> ContinuousDayInput:
    decision_at = datetime.fromisoformat(f"{trade_date.isoformat()}T15:15:00+08:00")
    signal = _signal(trade_date, signal_code)
    return ContinuousDayInput.create(
        continuous_day_input_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        symbol="000403.SZ",
        trade_date=trade_date,
        decision_at=decision_at,
        facts_available_at=decision_at,
        projection_available_at=decision_at,
        signal_generated_at=decision_at,
        facts_sha256=FACTS_IDENTITY,
        projection_sha256=PROJECTION_IDENTITY,
        claims_sha256=CLAIMS_IDENTITY,
        fixture_identity=SCENARIO_IDENTITY,
        signal=signal,
        execution_fixture=execution_fixture,
        valuation_bar=_valuation(trade_date, previous_trade_date, close),
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def build_release_lifecycle_inputs() -> list[ContinuousDayInput]:
    """Return the immutable four-day release lifecycle without external data."""
    return [
        _day(
            date(2026, 8, 3),
            date(2026, 7, 31),
            "POSITIVE_OBSERVATION",
            "10.00",
        ),
        _day(
            date(2026, 8, 4),
            date(2026, 8, 3),
            "MIXED_OBSERVATION",
            "10.80",
            _execution_fixture(
                date(2026, 8, 3),
                date(2026, 8, 4),
                date(2026, 8, 5),
                "10.50",
            ),
        ),
        _day(
            date(2026, 8, 5),
            date(2026, 8, 4),
            "RISK_OBSERVATION",
            "11.20",
        ),
        _day(
            date(2026, 8, 6),
            date(2026, 8, 5),
            "MIXED_OBSERVATION",
            "10.90",
            _execution_fixture(
                date(2026, 8, 5),
                date(2026, 8, 6),
                date(2026, 8, 7),
                "11.00",
            ),
        ),
    ]
