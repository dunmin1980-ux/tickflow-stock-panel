"""Standalone deterministic A-share T+1 Paper Trading ledger."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.schemas.phase2_option_c import (
    DeterministicMarketFixture,
    MarketBar,
    PaperAccount,
    PaperAction,
    PositionLot,
    ResearchSignal,
    SimulationConfig,
    TradeRecord,
    ValuationBar,
    canonical_option_c_bytes,
    money,
)

ZERO = Decimal("0.00")


class PaperTradingError(ValueError):
    """Raised when a deterministic paper-account transition is invalid."""


def _identity(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_option_c_bytes(value)).hexdigest()


def new_account(config: SimulationConfig) -> PaperAccount:
    """Create an empty simulation account from one immutable configuration."""
    config_sha256 = hashlib.sha256(canonical_option_c_bytes(config)).hexdigest()
    initial = money(config.initial_cash)
    return PaperAccount(
        account_schema_version=1,
        config=config,
        config_sha256=config_sha256,
        cash_cny=initial,
        lots=[],
        trades=[],
        processed_action_ids=[],
        processed_decision_ids=[],
        processed_order_ids=[],
        processed_idempotency_keys=[],
        realized_pnl_cny=ZERO,
        unrealized_pnl_cny=ZERO,
        market_value_cny=ZERO,
        total_equity_cny=initial,
        peak_equity_cny=initial,
        current_drawdown_cny=ZERO,
        max_drawdown_cny=ZERO,
        mark_price=None,
        mark_trade_date=None,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _rebuild_account(account: PaperAccount, **updates: Any) -> PaperAccount:
    return PaperAccount.model_validate({**account.model_dump(), **updates})


def _commission(gross: Decimal, config: SimulationConfig) -> Decimal:
    calculated = money(gross * config.commission_rate)
    return max(calculated, money(config.minimum_commission))


def _execution_timestamp(bar: MarketBar) -> datetime:
    return bar.available_at


def _resolve_execution_bar(
    action: PaperAction,
    fixture: DeterministicMarketFixture,
) -> MarketBar:
    if action.source_fixture not in {
        "DETERMINISTIC_TEST_FIXTURE",
        "VALIDATED_DAILY_INPUT",
    }:
        raise PaperTradingError("executable_action_requires_validated_fixture")
    if fixture.fixture_identity != fixture.computed_fixture_identity():
        raise PaperTradingError("fixture_identity_mismatch")
    if (
        fixture.source_fixture != action.source_fixture
        or fixture.symbol != action.symbol
        or fixture.scenario_fixture_identity != action.fixture_identity
    ):
        raise PaperTradingError("fixture_identity_mismatch")
    try:
        decision_index = fixture.trade_dates.index(action.decision_trade_date)
        execution_date = fixture.trade_dates[decision_index + 1]
    except (ValueError, IndexError) as exc:
        raise PaperTradingError("NO_EXECUTION_PRICE_AVAILABLE") from exc
    bar = next(
        (candidate for candidate in fixture.bars if candidate.trade_date == execution_date),
        None,
    )
    if bar is None or bar.previous_trade_date != action.decision_trade_date:
        raise PaperTradingError("NO_EXECUTION_PRICE_AVAILABLE")
    return bar


def _next_trade_date_after(
    fixture: DeterministicMarketFixture,
    trade_date: date,
) -> date:
    try:
        return fixture.trade_dates[fixture.trade_dates.index(trade_date) + 1]
    except (ValueError, IndexError) as exc:
        raise PaperTradingError("NO_SELLABLE_TRADE_DATE_AVAILABLE") from exc


def _validate_account_config(
    account: PaperAccount,
    config: SimulationConfig,
) -> None:
    expected = hashlib.sha256(canonical_option_c_bytes(config)).hexdigest()
    if account.config != config or account.config_sha256 != expected:
        raise PaperTradingError("account_config_identity_mismatch")


def _validate_action_provenance(
    account: PaperAccount,
    action: PaperAction,
    signal: ResearchSignal,
) -> None:
    expected = derive_action(
        signal,
        account,
        account.config,
        decision_at=action.decision_at,
    )
    if canonical_option_c_bytes(action) != canonical_option_c_bytes(expected):
        raise PaperTradingError("action_provenance_mismatch")


def _validate_account_temporal_boundary(
    account: PaperAccount,
    signal: ResearchSignal,
    decision_at: datetime,
) -> None:
    if account.mark_trade_date is not None and account.mark_trade_date > signal.trade_date:
        raise PaperTradingError("account_state_after_decision")
    if any(lot.acquired_trade_date > signal.trade_date for lot in account.lots):
        raise PaperTradingError("account_state_after_decision")
    if any(
        trade.trade_date > signal.trade_date
        or trade.order_timestamp > decision_at
        or trade.execution_timestamp > decision_at
        for trade in account.trades
    ):
        raise PaperTradingError("account_state_after_decision")


def eligible_quantity(
    account: PaperAccount,
    symbol: str,
    trade_date: date,
) -> int:
    """Return shares acquired strictly before the requested trade date."""
    if symbol != "000403.SZ":
        raise PaperTradingError("symbol_outside_single_symbol_scope")
    return sum(
        lot.remaining_quantity
        for lot in account.lots
        if lot.symbol == symbol and lot.sellable_from_trade_date <= trade_date
    )


def derive_action(
    signal: ResearchSignal,
    account: PaperAccount,
    config: SimulationConfig,
    *,
    decision_at: datetime,
) -> PaperAction:
    """Map one closed research signal to an action using deterministic rules."""
    _validate_account_config(account, config)
    if (
        decision_at.tzinfo is None
        or decision_at.utcoffset() != timedelta(hours=8)
        or decision_at.date() != signal.trade_date
    ):
        raise PaperTradingError("decision_timestamp_invalid")
    _validate_account_temporal_boundary(account, signal, decision_at)
    if signal.code in {"POSITIVE_OBSERVATION", "RISK_OBSERVATION"} and (
        signal.source_fixture
        not in {"DETERMINISTIC_TEST_FIXTURE", "VALIDATED_DAILY_INPUT"}
    ):
        raise PaperTradingError("test_signal_source_required")
    total_quantity = sum(lot.remaining_quantity for lot in account.lots)
    if signal.code == "POSITIVE_OBSERVATION" and total_quantity == 0:
        side = "BUY"
        quantity = config.lot_size * config.buy_lots_per_signal
    elif signal.code == "RISK_OBSERVATION" and total_quantity > 0:
        side = "SELL"
        quantity = min(
            total_quantity,
            config.lot_size * config.sell_lots_per_signal,
        )
    else:
        side = "HOLD"
        quantity = 0
    account_sha256 = hashlib.sha256(canonical_option_c_bytes(account)).hexdigest()
    decision_id = _identity(
        {
            "identity_type": "paper_rule_decision_v1",
            "signal_id": signal.signal_id,
            "signal_code": signal.code,
            "fixture_identity": signal.fixture_identity,
            "account_sha256": account_sha256,
            "config_sha256": account.config_sha256,
            "decision_at": decision_at.isoformat(),
        }
    )
    idempotency_key = _identity(
        {
            "identity_type": "paper_idempotency_v1",
            "symbol": signal.symbol,
            "decision_date": signal.trade_date.isoformat(),
            "signal_identity": signal.signal_id,
            "action": {"side": side, "quantity": quantity},
            "fixture_identity": signal.fixture_identity,
            "paper_config_identity": account.config_sha256,
        }
    )
    order_id = (
        None
        if side == "HOLD"
        else _identity(
            {
                "identity_type": "paper_order_v1",
                "idempotency_key": idempotency_key,
            }
        )
    )
    action_id = _identity(
        {
            "identity_type": "paper_action_v1",
            "decision_id": decision_id,
            "order_id": order_id,
            "idempotency_key": idempotency_key,
            "signal_id": signal.signal_id,
            "side": side,
            "quantity": quantity,
            "reason_refs": signal.reason_refs,
        }
    )
    return PaperAction(
        action_schema_version=1,
        action_id=action_id,
        decision_id=decision_id,
        order_id=order_id,
        idempotency_key=idempotency_key,
        symbol=signal.symbol,
        decision_trade_date=signal.trade_date,
        decision_at=decision_at,
        timezone="Asia/Shanghai",
        side=side,
        quantity=quantity,
        reason_refs=signal.reason_refs,
        source_fixture=signal.source_fixture,
        facts_sha256=signal.facts_sha256,
        claims_sha256=signal.claims_sha256,
        signal_id=signal.signal_id,
        fixture_identity=signal.fixture_identity,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _with_mark(
    account: PaperAccount,
    *,
    cash: Decimal,
    lots: list[PositionLot],
    trades: list[TradeRecord],
    processed_action_ids: list[str],
    processed_decision_ids: list[str],
    processed_order_ids: list[str],
    processed_idempotency_keys: list[str],
    realized_pnl: Decimal,
    mark_price: Decimal,
    mark_trade_date: date,
) -> PaperAccount:
    quantity = sum(lot.remaining_quantity for lot in lots)
    position_cost = sum(
        (lot.remaining_cost_cny for lot in lots),
        start=ZERO,
    )
    market_value = money(mark_price * quantity)
    unrealized = money(market_value - position_cost)
    equity = money(cash + market_value)
    peak = max(account.peak_equity_cny, equity)
    drawdown = money(peak - equity)
    max_drawdown = max(account.max_drawdown_cny, drawdown)
    return _rebuild_account(
        account,
        cash_cny=money(cash),
        lots=lots,
        trades=trades,
        processed_action_ids=processed_action_ids,
        processed_decision_ids=processed_decision_ids,
        processed_order_ids=processed_order_ids,
        processed_idempotency_keys=processed_idempotency_keys,
        realized_pnl_cny=money(realized_pnl),
        unrealized_pnl_cny=unrealized,
        market_value_cny=market_value,
        total_equity_cny=equity,
        peak_equity_cny=peak,
        current_drawdown_cny=drawdown,
        max_drawdown_cny=max_drawdown,
        mark_price=money(mark_price),
        mark_trade_date=mark_trade_date,
    )


def _buy(
    account: PaperAccount,
    action: PaperAction,
    bar: MarketBar,
    fixture: DeterministicMarketFixture,
) -> PaperAccount:
    if action.order_id is None:
        raise PaperTradingError("executable_action_requires_order_id")
    order_id = action.order_id
    price = money(bar.open)
    gross = money(price * action.quantity)
    commission = _commission(gross, account.config)
    required = money(gross + commission)
    if account.cash_cny < required:
        raise PaperTradingError("insufficient_cash")
    execution_id = _identity(
        {
            "identity_type": "paper_execution_v1",
            "action_id": action.action_id,
            "market_fixture_identity": fixture.fixture_identity,
            "bar_trade_date": bar.trade_date.isoformat(),
            "price": str(price),
        }
    )
    lot = PositionLot(
        lot_schema_version=1,
        lot_id=_identity(
            {
                "identity_type": "paper_position_lot_v1",
                "execution_id": execution_id,
            }
        ),
        source_action_id=action.action_id,
        symbol=action.symbol,
        acquired_trade_date=bar.trade_date,
        sellable_from_trade_date=_next_trade_date_after(fixture, bar.trade_date),
        original_quantity=action.quantity,
        remaining_quantity=action.quantity,
        remaining_cost_cny=required,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    trade = TradeRecord(
        trade_schema_version=1,
        execution_id=execution_id,
        action_id=action.action_id,
        order_id=order_id,
        idempotency_key=action.idempotency_key,
        order_timestamp=action.decision_at,
        execution_timestamp=_execution_timestamp(bar),
        trade_date=bar.trade_date,
        symbol=action.symbol,
        side="BUY",
        quantity=action.quantity,
        execution_price=price,
        gross_amount_cny=gross,
        commission_cny=commission,
        stamp_tax_cny=ZERO,
        fees_cny=commission,
        sell_cost_cny=ZERO,
        cost_basis_cny=required,
        net_cash_flow_cny=money(-required),
        realized_pnl_cny=ZERO,
        reason_refs=action.reason_refs,
        facts_sha256=action.facts_sha256,
        claims_sha256=action.claims_sha256,
        signal_id=action.signal_id,
        fixture_identity=action.fixture_identity,
        market_fixture_identity=fixture.fixture_identity,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    return _with_mark(
        account,
        cash=money(account.cash_cny - required),
        lots=[*account.lots, lot],
        trades=[*account.trades, trade],
        processed_action_ids=[*account.processed_action_ids, action.action_id],
        processed_decision_ids=[*account.processed_decision_ids, action.decision_id],
        processed_order_ids=[*account.processed_order_ids, order_id],
        processed_idempotency_keys=[
            *account.processed_idempotency_keys,
            action.idempotency_key,
        ],
        realized_pnl=account.realized_pnl_cny,
        mark_price=price,
        mark_trade_date=bar.trade_date,
    )


def _sell(
    account: PaperAccount,
    action: PaperAction,
    bar: MarketBar,
    fixture: DeterministicMarketFixture,
) -> PaperAccount:
    if action.order_id is None:
        raise PaperTradingError("executable_action_requires_order_id")
    order_id = action.order_id
    if eligible_quantity(account, action.symbol, bar.trade_date) < action.quantity:
        raise PaperTradingError("t1_eligible_quantity_insufficient")
    price = money(bar.open)
    gross = money(price * action.quantity)
    commission = _commission(gross, account.config)
    stamp_tax = money(gross * account.config.sell_fee_rate)
    net_proceeds = money(gross - commission - stamp_tax)
    remaining_to_sell = action.quantity
    allocated_cost = ZERO
    updated_lots: list[PositionLot] = []
    for lot in account.lots:
        if remaining_to_sell == 0 or lot.sellable_from_trade_date > bar.trade_date:
            updated_lots.append(lot)
            continue
        sold = min(remaining_to_sell, lot.remaining_quantity)
        if sold == lot.remaining_quantity:
            lot_cost = lot.remaining_cost_cny
        else:
            lot_cost = money(
                lot.remaining_cost_cny * sold / lot.remaining_quantity
            )
        allocated_cost = money(allocated_cost + lot_cost)
        remaining_quantity = lot.remaining_quantity - sold
        remaining_cost = money(lot.remaining_cost_cny - lot_cost)
        remaining_to_sell -= sold
        if remaining_quantity:
            updated_lots.append(
                PositionLot.model_validate(
                    {
                        **lot.model_dump(),
                        "remaining_quantity": remaining_quantity,
                        "remaining_cost_cny": remaining_cost,
                    }
                )
            )
    if remaining_to_sell:
        raise PaperTradingError("t1_eligible_quantity_insufficient")
    trade_realized = money(net_proceeds - allocated_cost)
    execution_id = _identity(
        {
            "identity_type": "paper_execution_v1",
            "action_id": action.action_id,
            "market_fixture_identity": fixture.fixture_identity,
            "bar_trade_date": bar.trade_date.isoformat(),
            "price": str(price),
        }
    )
    trade = TradeRecord(
        trade_schema_version=1,
        execution_id=execution_id,
        action_id=action.action_id,
        order_id=order_id,
        idempotency_key=action.idempotency_key,
        order_timestamp=action.decision_at,
        execution_timestamp=_execution_timestamp(bar),
        trade_date=bar.trade_date,
        symbol=action.symbol,
        side="SELL",
        quantity=action.quantity,
        execution_price=price,
        gross_amount_cny=gross,
        commission_cny=commission,
        stamp_tax_cny=stamp_tax,
        fees_cny=money(commission + stamp_tax),
        sell_cost_cny=money(commission + stamp_tax),
        cost_basis_cny=allocated_cost,
        net_cash_flow_cny=net_proceeds,
        realized_pnl_cny=trade_realized,
        reason_refs=action.reason_refs,
        facts_sha256=action.facts_sha256,
        claims_sha256=action.claims_sha256,
        signal_id=action.signal_id,
        fixture_identity=action.fixture_identity,
        market_fixture_identity=fixture.fixture_identity,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    return _with_mark(
        account,
        cash=money(account.cash_cny + net_proceeds),
        lots=updated_lots,
        trades=[*account.trades, trade],
        processed_action_ids=[*account.processed_action_ids, action.action_id],
        processed_decision_ids=[*account.processed_decision_ids, action.decision_id],
        processed_order_ids=[*account.processed_order_ids, order_id],
        processed_idempotency_keys=[
            *account.processed_idempotency_keys,
            action.idempotency_key,
        ],
        realized_pnl=money(account.realized_pnl_cny + trade_realized),
        mark_price=price,
        mark_trade_date=bar.trade_date,
    )


def mark_to_market(
    account: PaperAccount,
    bar: ValuationBar,
    *,
    valuation_at: datetime,
) -> PaperAccount:
    """Apply one close mark without changing cash, cost, or trade history."""
    _validate_account_config(account, account.config)
    if (
        valuation_at.tzinfo is None
        or valuation_at.utcoffset() != timedelta(hours=8)
        or valuation_at.date() != bar.trade_date
    ):
        raise PaperTradingError("valuation_timestamp_invalid")
    if bar.available_at > valuation_at:
        raise PaperTradingError("valuation_data_not_available")
    if bar.symbol != "000403.SZ":
        raise PaperTradingError("mark_symbol_mismatch")
    if account.mark_trade_date is not None and bar.trade_date < account.mark_trade_date:
        raise PaperTradingError("mark_date_regression")
    return _with_mark(
        account,
        cash=account.cash_cny,
        lots=list(account.lots),
        trades=list(account.trades),
        processed_action_ids=list(account.processed_action_ids),
        processed_decision_ids=list(account.processed_decision_ids),
        processed_order_ids=list(account.processed_order_ids),
        processed_idempotency_keys=list(account.processed_idempotency_keys),
        realized_pnl=account.realized_pnl_cny,
        mark_price=bar.close,
        mark_trade_date=bar.trade_date,
    )


def execute_action(
    account: PaperAccount,
    action: PaperAction,
    market_fixture: DeterministicMarketFixture | None,
    *,
    signal: ResearchSignal,
) -> PaperAccount:
    """Apply one deterministic action without mutating the input account."""
    _validate_account_config(account, account.config)
    if action.action_id in account.processed_action_ids:
        raise PaperTradingError("duplicate_action")
    if action.decision_id in account.processed_decision_ids:
        raise PaperTradingError("duplicate_decision")
    if (
        action.order_id is not None
        and action.order_id in account.processed_order_ids
    ):
        raise PaperTradingError("duplicate_order")
    if action.idempotency_key in account.processed_idempotency_keys:
        raise PaperTradingError("duplicate_idempotency_key")
    _validate_action_provenance(account, action, signal)
    if action.side == "HOLD":
        if market_fixture is not None:
            raise PaperTradingError("hold_must_not_have_market_fixture")
        return _rebuild_account(
            account,
            processed_action_ids=[*account.processed_action_ids, action.action_id],
            processed_decision_ids=[
                *account.processed_decision_ids,
                action.decision_id,
            ],
            processed_order_ids=list(account.processed_order_ids),
            processed_idempotency_keys=[
                *account.processed_idempotency_keys,
                action.idempotency_key,
            ],
        )
    if market_fixture is None:
        raise PaperTradingError("market_fixture_required")
    execution_bar = _resolve_execution_bar(action, market_fixture)
    if action.side == "BUY":
        return _buy(account, action, execution_bar, market_fixture)
    return _sell(account, action, execution_bar, market_fixture)
