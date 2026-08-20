"""Pure multi-day state transitions for the Option C paper account."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.schemas.phase2_option_c import (
    ContinuousDayInput,
    ContinuousPaperState,
    DecisionLedgerEntry,
    EquityHistoryPoint,
    PaperAction,
    PendingActionRecord,
    SimulationConfig,
    TradeRecord,
)
from app.services.phase2_paper_trading import (
    PaperTradingError,
    derive_action,
    execute_action,
    mark_to_market,
    new_account,
)

SHANGHAI_OFFSET = timedelta(hours=8)


class ContinuousRunError(ValueError):
    """Raised when a daily transition would violate the continuous contract."""


@dataclass(frozen=True)
class ContinuousDayResult:
    day_input: ContinuousDayInput
    action: PaperAction
    executed_trades: list[TradeRecord]
    state: ContinuousPaperState


def new_continuous_state(config: SimulationConfig) -> ContinuousPaperState:
    """Create one empty state that must be carried forward across days."""
    return ContinuousPaperState(
        continuous_state_schema_version=1,
        symbol="000403.SZ",
        account=new_account(config),
        pending_action=None,
        decision_ledger=[],
        equity_history=[],
        completed_input_identities=[],
        last_completed_trade_date=None,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _validate_day_boundary(
    state: ContinuousPaperState,
    day_input: ContinuousDayInput,
) -> None:
    if (
        state.last_completed_trade_date is not None
        and day_input.trade_date <= state.last_completed_trade_date
    ):
        raise ContinuousRunError("duplicate_or_regressed_trade_date")
    if day_input.input_identity in state.completed_input_identities:
        raise ContinuousRunError("duplicate_input_identity")
    timestamps = (
        day_input.facts_available_at,
        day_input.projection_available_at,
        day_input.signal_generated_at,
        day_input.valuation_bar.available_at,
    )
    if any(
        timestamp.tzinfo is None
        or timestamp.utcoffset() != SHANGHAI_OFFSET
        or timestamp > day_input.decision_at
        for timestamp in timestamps
    ):
        raise ContinuousRunError("future_evidence_not_allowed")
    if state.pending_action is None and day_input.execution_fixture is not None:
        raise ContinuousRunError("unexpected_execution_fixture")
    if state.pending_action is not None and day_input.execution_fixture is None:
        raise ContinuousRunError("pending_execution_fixture_required")


def _execute_pending(
    state: ContinuousPaperState,
    day_input: ContinuousDayInput,
):
    account = state.account
    if state.pending_action is None:
        return account, []
    before_count = len(account.trades)
    try:
        account = execute_action(
            account,
            state.pending_action.action,
            day_input.execution_fixture,
            signal=state.pending_action.signal,
        )
    except PaperTradingError as exc:
        raise ContinuousRunError(str(exc)) from exc
    executed = list(account.trades[before_count:])
    if (
        len(executed) != 1
        or executed[0].trade_date != day_input.trade_date
        or executed[0].execution_timestamp > day_input.decision_at
    ):
        raise ContinuousRunError("pending_execution_date_mismatch")
    return account, executed


def run_continuous_day(
    state: ContinuousPaperState,
    day_input: ContinuousDayInput,
) -> ContinuousDayResult:
    """Run one post-close transition without mutating the prior state."""
    _validate_day_boundary(state, day_input)
    account, executed_trades = _execute_pending(state, day_input)
    try:
        account = mark_to_market(
            account,
            day_input.valuation_bar,
            valuation_at=day_input.decision_at,
        )
        action = derive_action(
            day_input.signal,
            account,
            account.config,
            decision_at=day_input.decision_at,
        )
        if action.side == "HOLD":
            account = execute_action(
                account,
                action,
                None,
                signal=day_input.signal,
            )
            pending = None
        else:
            pending = PendingActionRecord(
                pending_action_schema_version=1,
                queued_input_identity=day_input.input_identity,
                action=action,
                signal=day_input.signal,
                simulation_only="SIMULATION ONLY",
                can_publish=False,
                trading_advice=False,
            )
    except PaperTradingError as exc:
        raise ContinuousRunError(str(exc)) from exc

    decision_entry = DecisionLedgerEntry(
        decision_entry_schema_version=1,
        trade_date=day_input.trade_date,
        decision_at=day_input.decision_at,
        input_identity=day_input.input_identity,
        facts_sha256=day_input.facts_sha256,
        projection_sha256=day_input.projection_sha256,
        claims_sha256=day_input.claims_sha256,
        fixture_identity=day_input.fixture_identity,
        signal_id=day_input.signal.signal_id,
        action_id=action.action_id,
        order_id=action.order_id,
        research_signal=day_input.signal.code,
        paper_action=action.side,
        pending_execution=action.side != "HOLD",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    equity_point = EquityHistoryPoint(
        equity_point_schema_version=1,
        trade_date=day_input.trade_date,
        cash_cny=account.cash_cny,
        market_value_cny=account.market_value_cny,
        realized_pnl_cny=account.realized_pnl_cny,
        unrealized_pnl_cny=account.unrealized_pnl_cny,
        total_equity_cny=account.total_equity_cny,
        peak_equity_cny=account.peak_equity_cny,
        current_drawdown_cny=account.current_drawdown_cny,
        max_drawdown_cny=account.max_drawdown_cny,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    next_state = ContinuousPaperState(
        continuous_state_schema_version=1,
        symbol=state.symbol,
        account=account,
        pending_action=pending,
        decision_ledger=[*state.decision_ledger, decision_entry],
        equity_history=[*state.equity_history, equity_point],
        completed_input_identities=[
            *state.completed_input_identities,
            day_input.input_identity,
        ],
        last_completed_trade_date=day_input.trade_date,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    return ContinuousDayResult(
        day_input=day_input,
        action=action,
        executed_trades=executed_trades,
        state=next_state,
    )
