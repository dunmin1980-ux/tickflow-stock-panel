from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.phase2_option_c import (
    DeterministicMarketFixture,
    MarketBar,
    PaperAccount,
    PaperAction,
    PaperTradingConfig,
    ResearchSignal,
    SimulationConfig,
    ValuationBar,
    canonical_option_c_bytes,
)
from app.services.phase2_paper_trading import (
    PaperTradingError,
    derive_action,
    eligible_quantity,
    execute_action,
    mark_to_market,
    new_account,
)


def _action_payload(
    *,
    side: str = "BUY",
    quantity: object = 100,
    action_character: str = "a",
    signal_character: str = "e",
    decision_trade_date: date = date(2026, 8, 3),
) -> dict[str, object]:
    return {
        "action_schema_version": 1,
        "action_id": action_character * 64,
        "decision_id": "b" * 64,
        "order_id": "8" * 64,
        "idempotency_key": "7" * 64,
        "symbol": "000403.SZ",
        "decision_trade_date": decision_trade_date,
        "decision_at": datetime.fromisoformat(
            f"{decision_trade_date.isoformat()}T15:15:00+08:00"
        ),
        "timezone": "Asia/Shanghai",
        "side": side,
        "quantity": quantity,
        "reason_refs": ["fixture-positive-signal"],
        "source_fixture": "DETERMINISTIC_TEST_FIXTURE",
        "facts_sha256": "c" * 64,
        "claims_sha256": "d" * 64,
        "signal_id": signal_character * 64,
        "fixture_identity": "9" * 64,
        "simulation_only": "SIMULATION ONLY",
        "can_publish": False,
        "trading_advice": False,
    }


def _signal(
    code: str,
    *,
    trade_date: date = date(2026, 8, 3),
    source_fixture: str = "DETERMINISTIC_TEST_FIXTURE",
    signal_character: str = "e",
    fixture_identity: str = "9" * 64,
) -> ResearchSignal:
    return ResearchSignal.model_validate(
        {
            "signal_schema_version": 1,
            "signal_id": signal_character * 64,
            "interpretation_id": "f" * 64,
            "source_fixture": source_fixture,
            "symbol": "000403.SZ",
            "trade_date": trade_date,
            "code": code,
            "reason_refs": ["deterministic-test-rule"],
            "facts_sha256": "c" * 64,
            "claims_sha256": "d" * 64,
            "fixture_identity": fixture_identity,
            "simulation_only": "SIMULATION ONLY",
            "can_publish": False,
            "trading_advice": False,
        }
    )


def test_signal_binds_fixture_identity() -> None:
    signal = _signal("MIXED_OBSERVATION")

    assert signal.fixture_identity == "9" * 64


def _derived_action(
    account: PaperAccount,
    code: str,
    *,
    trade_date: date = date(2026, 8, 3),
    source_fixture: str = "DETERMINISTIC_TEST_FIXTURE",
    signal_character: str = "e",
) -> tuple[ResearchSignal, PaperAction]:
    signal = _signal(
        code,
        trade_date=trade_date,
        source_fixture=source_fixture,
        signal_character=signal_character,
    )
    action = derive_action(
        signal,
        account,
        account.config,
        decision_at=datetime.fromisoformat(
            f"{trade_date.isoformat()}T15:15:00+08:00"
        ),
    )
    return signal, action


def _bar(
    *,
    trade_date: date = date(2026, 8, 4),
    previous_trade_date: date = date(2026, 8, 3),
    open_price: str = "10.50",
) -> MarketBar:
    return MarketBar(
        market_bar_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity="9" * 64,
        symbol="000403.SZ",
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        available_at=datetime.fromisoformat(
            f"{trade_date.isoformat()}T09:30:00+08:00"
        ),
        timezone="Asia/Shanghai",
        open=Decimal(open_price),
        price_basis="raw",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _valuation_bar(
    *,
    trade_date: date = date(2026, 8, 4),
    previous_trade_date: date = date(2026, 8, 3),
    close_price: str = "10.80",
    available_at: str | None = None,
) -> ValuationBar:
    return ValuationBar(
        valuation_bar_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity="9" * 64,
        symbol="000403.SZ",
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        available_at=datetime.fromisoformat(
            available_at or f"{trade_date.isoformat()}T15:01:00+08:00"
        ),
        timezone="Asia/Shanghai",
        close=Decimal(close_price),
        price_basis="raw",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _market_fixture(
    *,
    bars: list[MarketBar] | None = None,
    trade_dates: list[date] | None = None,
) -> DeterministicMarketFixture:
    selected_dates = trade_dates or [
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
    ]
    return DeterministicMarketFixture.create(
        market_fixture_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity="9" * 64,
        symbol="000403.SZ",
        trade_dates=selected_dates,
        bars=bars if bars is not None else [_bar()],
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def test_simulation_config_is_decimal_and_canonical() -> None:
    config = PaperTradingConfig.default()

    assert config.initial_cash == Decimal("100000.00")
    assert config.commission_rate == Decimal("0.0003")
    assert config.minimum_commission == Decimal("5.00")
    assert config.sell_fee_rate == Decimal("0.0005")
    assert config.slippage_rate == Decimal("0")
    assert config.lot_size == 100
    assert config.t_plus_one is True
    assert config.execution_policy == "NEXT_TRADING_DAY_OPEN"
    assert config.simulation_only == "SIMULATION ONLY"
    assert config.can_publish is False
    assert config.trading_advice is False

    raw = canonical_option_c_bytes(config)
    assert b'"initial_cash": "100000.00"' in raw
    assert b'"minimum_commission": "5.00"' in raw
    assert b'"sell_fee_rate": "0.0005"' in raw
    assert b'"execution_policy": "NEXT_TRADING_DAY_OPEN"' in raw
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
    valid_payload = _action_payload(side="HOLD", quantity=0)
    valid_payload["order_id"] = None
    valid = PaperAction.model_validate(valid_payload)
    assert valid.quantity == 0
    assert valid.order_id is None

    with pytest.raises(ValidationError):
        PaperAction.model_validate({**valid_payload, "quantity": 100})

    with pytest.raises(ValidationError, match="hold_must_not_have_order_id"):
        PaperAction.model_validate(_action_payload(side="HOLD", quantity=0))

    with pytest.raises(ValidationError, match="executable_action_requires_order_id"):
        PaperAction.model_validate({**_action_payload(), "order_id": None})


def test_action_rejects_non_shanghai_offset() -> None:
    payload = _action_payload()
    payload["decision_at"] = datetime.fromisoformat("2026-08-03T13:15:00+00:00")

    with pytest.raises(ValidationError):
        PaperAction.model_validate(payload)


def test_market_bar_is_strict_raw_open_evidence() -> None:
    bar = MarketBar(
        market_bar_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity="9" * 64,
        symbol="000403.SZ",
        trade_date=date(2026, 8, 4),
        previous_trade_date=date(2026, 8, 3),
        available_at=datetime.fromisoformat("2026-08-04T09:30:00+08:00"),
        timezone="Asia/Shanghai",
        open=Decimal("10.50"),
        price_basis="raw",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )

    assert bar.open == Decimal("10.50")

    with pytest.raises(ValidationError):
        MarketBar.model_validate({**bar.model_dump(), "open": 10.5})

    with pytest.raises(
        ValidationError,
        match="execution_open_must_be_available_at_09_30",
    ):
        MarketBar.model_validate(
            {
                **bar.model_dump(),
                "available_at": datetime.fromisoformat(
                    "2026-08-04T15:01:00+08:00"
                ),
            }
        )


def test_valuation_bar_rejects_close_before_market_close() -> None:
    with pytest.raises(
        ValidationError,
        match="close_must_not_be_available_before_market_close",
    ):
        _valuation_bar(available_at="2026-08-04T14:59:59+08:00")


def test_market_fixture_is_ordered_unique_and_closed() -> None:
    fixture = _market_fixture()

    assert fixture.trade_dates == [
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
    ]
    assert fixture.bars[0].trade_date == date(2026, 8, 4)
    assert fixture.scenario_fixture_identity == "9" * 64
    assert fixture.fixture_identity != fixture.scenario_fixture_identity


def test_execution_bar_is_available_at_next_day_open() -> None:
    bar = _market_fixture().bars[0]

    assert bar.available_at == datetime.fromisoformat("2026-08-04T09:30:00+08:00")


def test_execution_bar_excludes_future_close() -> None:
    bar = _market_fixture().bars[0]

    assert "close" not in bar.model_dump()


def test_market_fixture_identity_is_content_bound_and_rejects_stale_hash() -> None:
    first = _market_fixture(bars=[_bar(open_price="10.50")])
    second = _market_fixture(bars=[_bar(open_price="99.00")])

    assert first.fixture_identity != second.fixture_identity
    with pytest.raises(ValidationError, match="market_fixture_identity_mismatch"):
        DeterministicMarketFixture.model_validate(
            {
                **first.model_dump(),
                "bars": [
                    {
                        **first.bars[0].model_dump(),
                        "open": Decimal("99.00"),
                    }
                ],
            }
        )


def test_new_account_has_only_virtual_cash() -> None:
    config = SimulationConfig.default()
    account = new_account(config)

    assert account.cash_cny == Decimal("100000.00")
    assert account.lots == []
    assert account.trades == []
    assert account.processed_action_ids == []
    assert account.processed_decision_ids == []
    assert account.processed_order_ids == []
    assert account.processed_idempotency_keys == []
    assert account.realized_pnl_cny == Decimal("0.00")
    assert account.unrealized_pnl_cny == Decimal("0.00")
    assert account.market_value_cny == Decimal("0.00")
    assert account.total_equity_cny == Decimal("100000.00")
    assert account.peak_equity_cny == Decimal("100000.00")
    assert account.max_drawdown_cny == Decimal("0.00")
    assert account.config_sha256 == __import__("hashlib").sha256(
        canonical_option_c_bytes(config)
    ).hexdigest()


def test_action_idempotency_key_binds_required_contract_fields() -> None:
    account = new_account(PaperTradingConfig.default())
    signal, action = _derived_action(account, "POSITIVE_OBSERVATION")
    expected = hashlib.sha256(
        canonical_option_c_bytes(
            {
                "identity_type": "paper_idempotency_v1",
                "symbol": "000403.SZ",
                "decision_date": "2026-08-03",
                "signal_identity": signal.signal_id,
                "action": {"side": "BUY", "quantity": 100},
                "fixture_identity": signal.fixture_identity,
                "paper_config_identity": account.config_sha256,
            }
        )
    ).hexdigest()

    assert action.idempotency_key == expected
    assert action.order_id == hashlib.sha256(
        canonical_option_c_bytes(
            {
                "identity_type": "paper_order_v1",
                "idempotency_key": expected,
            }
        )
    ).hexdigest()
    assert action.fixture_identity == signal.fixture_identity


def test_hold_is_idempotently_recorded_without_trade() -> None:
    account = new_account(SimulationConfig.default())
    signal, action = _derived_action(
        account,
        "MIXED_OBSERVATION",
        source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
    )

    updated = execute_action(account, action, None, signal=signal)

    assert updated.cash_cny == account.cash_cny
    assert updated.lots == []
    assert updated.trades == []
    assert updated.processed_action_ids == [action.action_id]
    assert action.order_id is None
    assert updated.processed_order_ids == []

    with pytest.raises(PaperTradingError, match="duplicate_action"):
        execute_action(updated, action, None, signal=signal)


def test_duplicate_decision_is_blocked_before_ledger_mutation() -> None:
    account = new_account(SimulationConfig.default())
    signal, action = _derived_action(
        account,
        "MIXED_OBSERVATION",
        source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
    )
    duplicate = account.model_copy(
        update={"processed_decision_ids": [action.decision_id]}
    )

    with pytest.raises(PaperTradingError, match="duplicate_decision"):
        execute_action(duplicate, action, None, signal=signal)
    assert duplicate.trades == []


def test_duplicate_order_is_blocked_before_ledger_mutation() -> None:
    account = new_account(SimulationConfig.default())
    signal, action = _derived_action(account, "POSITIVE_OBSERVATION")
    duplicate = account.model_copy(
        update={"processed_order_ids": [action.order_id]}
    )

    with pytest.raises(PaperTradingError, match="duplicate_order"):
        execute_action(duplicate, action, _market_fixture(), signal=signal)
    assert duplicate.trades == []


def test_action_rejects_tampered_account_config_identity() -> None:
    valid = new_account(SimulationConfig.default())
    signal, action = _derived_action(
        valid,
        "MIXED_OBSERVATION",
        source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
    )
    account = valid.model_copy(
        update={"config_sha256": "0" * 64}
    )

    with pytest.raises(PaperTradingError, match="account_config_identity_mismatch"):
        execute_action(account, action, None, signal=signal)


def test_buy_executes_at_next_trading_day_open_with_decimal_fees() -> None:
    config = SimulationConfig.default()
    account = new_account(config)
    signal, action = _derived_action(account, "POSITIVE_OBSERVATION")

    fixture = _market_fixture()
    updated = execute_action(account, action, fixture, signal=signal)

    assert updated.cash_cny == Decimal("98945.00")
    assert len(updated.lots) == 1
    assert updated.lots[0].remaining_quantity == 100
    assert updated.lots[0].remaining_cost_cny == Decimal("1055.00")
    assert updated.lots[0].sellable_from_trade_date == date(2026, 8, 5)
    assert len(updated.trades) == 1
    trade = updated.trades[0]
    assert trade.side == "BUY"
    assert trade.quantity == 100
    assert trade.execution_price == Decimal("10.50")
    assert trade.gross_amount_cny == Decimal("1050.00")
    assert trade.commission_cny == Decimal("5.00")
    assert trade.stamp_tax_cny == Decimal("0.00")
    assert trade.net_cash_flow_cny == Decimal("-1055.00")
    assert trade.realized_pnl_cny == Decimal("0.00")
    assert trade.facts_sha256 == "c" * 64
    assert trade.claims_sha256 == "d" * 64
    assert trade.signal_id == "e" * 64
    assert trade.order_id == action.order_id
    assert trade.idempotency_key == action.idempotency_key
    assert trade.fixture_identity == "9" * 64
    assert trade.market_fixture_identity == fixture.fixture_identity
    assert trade.fees_cny == Decimal("5.00")
    assert trade.sell_cost_cny == Decimal("0.00")
    assert trade.cost_basis_cny == Decimal("1055.00")
    assert updated.market_value_cny == Decimal("1050.00")
    assert updated.unrealized_pnl_cny == Decimal("-5.00")
    assert updated.total_equity_cny == Decimal("99995.00")
    assert updated.peak_equity_cny == Decimal("100000.00")
    assert updated.current_drawdown_cny == Decimal("5.00")
    assert updated.max_drawdown_cny == Decimal("5.00")

    with pytest.raises(ValidationError, match="trade_fee_components_mismatch"):
        type(trade).model_validate(
            {**trade.model_dump(), "fees_cny": Decimal("4.99")}
        )


def test_execute_action_rejects_action_not_derived_from_supplied_signal() -> None:
    config = SimulationConfig.default()
    account = new_account(config)
    signal = _signal("POSITIVE_OBSERVATION")
    forged = PaperAction.model_validate(_action_payload(action_character="9"))

    with pytest.raises(PaperTradingError, match="action_provenance_mismatch"):
        execute_action(account, forged, _market_fixture(), signal=signal)


def test_execution_fixture_rejects_future_close_value() -> None:
    fixture = _market_fixture()
    payload = fixture.model_dump()
    payload["bars"][0]["close"] = Decimal("99.00")

    with pytest.raises(ValidationError, match="extra_forbidden"):
        DeterministicMarketFixture.model_validate(payload)


def test_mark_to_market_rejects_data_unavailable_at_valuation_time() -> None:
    account = new_account(SimulationConfig.default())
    bar = _valuation_bar()

    with pytest.raises(PaperTradingError, match="valuation_data_not_available"):
        mark_to_market(
            account,
            bar,
            valuation_at=datetime.fromisoformat("2026-08-04T15:00:00+08:00"),
        )


def test_missing_next_trading_day_open_is_blocked_without_skipping() -> None:
    account = new_account(SimulationConfig.default())
    signal, action = _derived_action(account, "POSITIVE_OBSERVATION")
    fixture = _market_fixture(
        trade_dates=[
            date(2026, 7, 31),
            date(2026, 8, 3),
            date(2026, 8, 4),
            date(2026, 8, 5),
        ],
        bars=[
            _bar(
                trade_date=date(2026, 8, 3),
                previous_trade_date=date(2026, 7, 31),
            ),
            _bar(
                trade_date=date(2026, 8, 5),
                previous_trade_date=date(2026, 8, 4),
            ),
        ],
    )

    with pytest.raises(PaperTradingError, match="NO_EXECUTION_PRICE_AVAILABLE"):
        execute_action(account, action, fixture, signal=signal)


def test_execution_fixture_identity_mismatch_is_blocked() -> None:
    account = new_account(SimulationConfig.default())
    signal, action = _derived_action(account, "POSITIVE_OBSERVATION")
    mismatched = _market_fixture().model_copy(
        update={"fixture_identity": "8" * 64}
    )

    with pytest.raises(PaperTradingError, match="fixture_identity_mismatch"):
        execute_action(account, action, mismatched, signal=signal)


def test_buy_rejects_insufficient_cash_without_mutation() -> None:
    config = SimulationConfig.model_validate(
        {
            **SimulationConfig.default().model_dump(),
            "initial_cash": Decimal("1000.00"),
        }
    )
    account = new_account(config)
    signal, action = _derived_action(account, "POSITIVE_OBSERVATION")

    with pytest.raises(PaperTradingError, match="insufficient_cash"):
        execute_action(account, action, _market_fixture(), signal=signal)

    assert account.cash_cny == Decimal("1000.00")
    assert account.lots == []
    assert account.trades == []


def test_deterministic_rule_maps_closed_signals_to_actions() -> None:
    config = SimulationConfig.default()
    empty = new_account(config)

    buy = derive_action(
        _signal("POSITIVE_OBSERVATION"),
        empty,
        config,
        decision_at=datetime.fromisoformat("2026-08-03T15:15:00+08:00"),
    )
    hold = derive_action(
        _signal(
            "MIXED_OBSERVATION",
            source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
        ),
        empty,
        config,
        decision_at=datetime.fromisoformat("2026-08-03T15:15:00+08:00"),
    )

    assert buy.side == "BUY"
    assert buy.quantity == 100
    assert hold.side == "HOLD"
    assert hold.quantity == 0

    with pytest.raises(PaperTradingError, match="test_signal_source_required"):
        derive_action(
            _signal(
                "POSITIVE_OBSERVATION",
                source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
            ),
            empty,
            config,
            decision_at=datetime.fromisoformat("2026-08-03T15:15:00+08:00"),
        )


def test_rule_rejects_account_marked_after_signal_trade_date() -> None:
    config = SimulationConfig.default()
    future_account = mark_to_market(
        new_account(config),
        _valuation_bar(),
        valuation_at=datetime.fromisoformat("2026-08-04T15:15:00+08:00"),
    )

    with pytest.raises(PaperTradingError, match="account_state_after_decision"):
        derive_action(
            _signal("POSITIVE_OBSERVATION"),
            future_account,
            config,
            decision_at=datetime.fromisoformat("2026-08-03T15:15:00+08:00"),
        )


def test_rule_rejects_future_lot_or_trade_hidden_behind_past_mark() -> None:
    config = SimulationConfig.default()
    initial = new_account(config)
    signal, action = _derived_action(initial, "POSITIVE_OBSERVATION")
    future_account = execute_action(initial, action, _market_fixture(), signal=signal)
    past_mark = date(2026, 8, 3)
    signal = _signal("RISK_OBSERVATION")

    for hidden_future in (
        future_account.model_copy(
            update={"mark_trade_date": past_mark, "trades": []}
        ),
        future_account.model_copy(
            update={"mark_trade_date": past_mark, "lots": []}
        ),
    ):
        with pytest.raises(PaperTradingError, match="account_state_after_decision"):
            derive_action(
                signal,
                hidden_future,
                config,
                decision_at=datetime.fromisoformat(
                    "2026-08-03T15:15:00+08:00"
                ),
            )


def test_t1_eligibility_and_partial_then_full_exit() -> None:
    config = SimulationConfig.model_validate(
        {
            **SimulationConfig.default().model_dump(),
            "buy_lots_per_signal": 2,
        }
    )
    account = new_account(config)
    buy_signal, buy = _derived_action(account, "POSITIVE_OBSERVATION")
    account = execute_action(account, buy, _market_fixture(), signal=buy_signal)

    assert eligible_quantity(account, "000403.SZ", date(2026, 8, 4)) == 0
    assert eligible_quantity(account, "000403.SZ", date(2026, 8, 5)) == 200

    risk = _signal(
        "RISK_OBSERVATION",
        trade_date=date(2026, 8, 4),
        signal_character="1",
    )
    partial_sell = derive_action(
        risk,
        account,
        config,
        decision_at=datetime.fromisoformat("2026-08-04T15:15:00+08:00"),
    )
    assert partial_sell.side == "SELL"
    assert partial_sell.quantity == 100

    with pytest.raises(PaperTradingError, match="NO_EXECUTION_PRICE_AVAILABLE"):
        execute_action(
            account,
            partial_sell,
            _market_fixture(
                trade_dates=[date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)],
                bars=[
                    _bar(
                        trade_date=date(2026, 8, 4),
                        previous_trade_date=date(2026, 8, 3),
                        open_price="11.00",
                    )
                ],
            ),
            signal=risk,
        )

    account = execute_action(
        account,
        partial_sell,
        _market_fixture(
            trade_dates=[
                date(2026, 8, 3),
                date(2026, 8, 4),
                date(2026, 8, 5),
            ],
            bars=[
                _bar(
                    trade_date=date(2026, 8, 5),
                    previous_trade_date=date(2026, 8, 4),
                    open_price="11.00",
                )
            ],
        ),
        signal=risk,
    )
    assert account.cash_cny == Decimal("98989.45")
    assert account.lots[0].remaining_quantity == 100
    assert account.lots[0].remaining_cost_cny == Decimal("1052.50")
    assert account.trades[-1].commission_cny == Decimal("5.00")
    assert account.trades[-1].stamp_tax_cny == Decimal("0.55")
    assert account.trades[-1].fees_cny == Decimal("5.55")
    assert account.trades[-1].sell_cost_cny == Decimal("5.55")
    assert account.trades[-1].cost_basis_cny == Decimal("1052.50")
    assert account.trades[-1].fixture_identity == "9" * 64
    assert account.trades[-1].net_cash_flow_cny == Decimal("1094.45")
    assert account.trades[-1].realized_pnl_cny == Decimal("41.95")
    assert account.realized_pnl_cny == Decimal("41.95")
    assert account.unrealized_pnl_cny == Decimal("47.50")
    assert account.total_equity_cny == Decimal("100089.45")

    final_risk = _signal(
        "RISK_OBSERVATION",
        trade_date=date(2026, 8, 5),
        signal_character="2",
    )
    full_sell = derive_action(
        final_risk,
        account,
        config,
        decision_at=datetime.fromisoformat("2026-08-05T15:15:00+08:00"),
    )
    account = execute_action(
        account,
        full_sell,
        _market_fixture(
            trade_dates=[date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6)],
            bars=[
                _bar(
                    trade_date=date(2026, 8, 6),
                    previous_trade_date=date(2026, 8, 5),
                    open_price="10.00",
                )
            ],
        ),
        signal=final_risk,
    )

    assert account.lots == []
    assert account.cash_cny == Decimal("99983.95")
    assert account.realized_pnl_cny == Decimal("-16.05")
    assert account.unrealized_pnl_cny == Decimal("0.00")
    assert account.market_value_cny == Decimal("0.00")
    assert account.total_equity_cny == Decimal("99983.95")
    assert account.peak_equity_cny == Decimal("100089.45")
    assert account.current_drawdown_cny == Decimal("105.50")
    assert account.max_drawdown_cny == Decimal("105.50")


def test_t1_insufficient_sellable_holdings_is_blocked() -> None:
    account = new_account(SimulationConfig.default())
    buy_signal, buy = _derived_action(account, "POSITIVE_OBSERVATION")
    account = execute_action(account, buy, _market_fixture(), signal=buy_signal)
    deferred_lot = account.lots[0].model_copy(
        update={"sellable_from_trade_date": date(2026, 8, 6)}
    )
    account = account.model_copy(update={"lots": [deferred_lot]})
    sell_signal, sell = _derived_action(
        account,
        "RISK_OBSERVATION",
        trade_date=date(2026, 8, 4),
        signal_character="1",
    )
    fixture = _market_fixture(
        trade_dates=[date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)],
        bars=[
            _bar(
                trade_date=date(2026, 8, 5),
                previous_trade_date=date(2026, 8, 4),
            )
        ],
    )

    with pytest.raises(PaperTradingError, match="t1_eligible_quantity_insufficient"):
        execute_action(account, sell, fixture, signal=sell_signal)
    assert len(account.trades) == 1


def test_mark_to_market_updates_unrealized_pnl_and_max_drawdown() -> None:
    initial = new_account(SimulationConfig.default())
    signal, action = _derived_action(initial, "POSITIVE_OBSERVATION")
    account = execute_action(
        initial,
        action,
        _market_fixture(),
        signal=signal,
    )

    lower = mark_to_market(
        account,
        _valuation_bar(close_price="9.00"),
        valuation_at=datetime.fromisoformat("2026-08-04T15:15:00+08:00"),
    )
    assert lower.unrealized_pnl_cny == Decimal("-155.00")
    assert lower.total_equity_cny == Decimal("99845.00")
    assert lower.max_drawdown_cny == Decimal("155.00")

    higher = mark_to_market(
        lower,
        _valuation_bar(
            trade_date=date(2026, 8, 5),
            previous_trade_date=date(2026, 8, 4),
            close_price="12.00",
        ),
        valuation_at=datetime.fromisoformat("2026-08-05T15:15:00+08:00"),
    )
    assert higher.unrealized_pnl_cny == Decimal("145.00")
    assert higher.total_equity_cny == Decimal("100145.00")
    assert higher.peak_equity_cny == Decimal("100145.00")
    assert higher.max_drawdown_cny == Decimal("155.00")


def test_commission_above_minimum_is_decimal() -> None:
    initial = new_account(SimulationConfig.default())
    signal, action = _derived_action(initial, "POSITIVE_OBSERVATION")
    account = execute_action(
        initial,
        action,
        _market_fixture(
            bars=[_bar(open_price="500.00")]
        ),
        signal=signal,
    )

    assert account.trades[0].gross_amount_cny == Decimal("50000.00")
    assert account.trades[0].commission_cny == Decimal("15.00")
