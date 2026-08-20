"""Offline daily runner and ChenQuant Daily delivery for Option C."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.schemas.phase2_option_c import (
    ContinuousDayInput,
    ContinuousPaperState,
    MvpChenQuantDaily,
    MvpPositionSummary,
    SimulationConfig,
    ValuationBar,
    canonical_option_c_bytes,
    money,
)
from app.services.phase2_chenquant_daily import REFERENCE_DECISION_AT
from app.services.phase2_option_c_continuous import (
    ContinuousDayResult,
    run_continuous_day,
)
from app.services.phase2_option_c_fixture import load_reference_fixture
from app.services.phase2_option_c_state import OptionCStateStore
from app.services.phase2_paper_trading import eligible_quantity
from app.services.phase2_research_decision import build_research_decision

PERCENT_QUANTUM = Decimal("0.0001")


class OptionCDailyError(ValueError):
    """Raised when a daily input or persisted daily output is invalid."""


@dataclass(frozen=True)
class DailyRunResult:
    status: Literal["DAY_PUBLISHED", "DAY_ALREADY_PUBLISHED"]
    daily: MvpChenQuantDaily
    state: ContinuousPaperState
    output_directory: Path


def build_reference_day_input(repo_root: Path) -> ContinuousDayInput:
    """Revalidate frozen Facts/Projection/Claims and build the reference HOLD input."""
    bundle = load_reference_fixture(repo_root)
    decision = build_research_decision(bundle, REFERENCE_DECISION_AT)
    daily_close = bundle.projection["safe_facts"]["daily"]["close"]
    return ContinuousDayInput.create(
        continuous_day_input_schema_version=1,
        source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
        symbol="000403.SZ",
        trade_date=bundle.manifest.trade_date,
        decision_at=REFERENCE_DECISION_AT,
        facts_available_at=bundle.manifest.evidence_available_at,
        projection_available_at=bundle.manifest.evidence_available_at,
        signal_generated_at=REFERENCE_DECISION_AT,
        facts_sha256=bundle.manifest.facts_sha256,
        projection_sha256=bundle.manifest.projection_sha256,
        claims_sha256=bundle.manifest.claims_sha256,
        fixture_identity=decision.fixture_manifest_sha256,
        signal=decision.signal,
        execution_fixture=None,
        valuation_bar=ValuationBar(
            valuation_bar_schema_version=1,
            source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
            scenario_fixture_identity=decision.fixture_manifest_sha256,
            symbol="000403.SZ",
            trade_date=bundle.manifest.trade_date,
            previous_trade_date=date(2026, 7, 30),
            available_at=bundle.manifest.evidence_available_at,
            timezone="Asia/Shanghai",
            close=Decimal(str(daily_close)),
            price_basis="raw",
            simulation_only="SIMULATION ONLY",
            can_publish=False,
            trading_advice=False,
        ),
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _position_summary(result: ContinuousDayResult) -> MvpPositionSummary:
    account = result.state.account
    quantity = sum(lot.remaining_quantity for lot in account.lots)
    sellable = eligible_quantity(account, "000403.SZ", result.day_input.trade_date)
    cost_basis = money(sum((lot.remaining_cost_cny for lot in account.lots), start=Decimal("0")))
    cumulative = (
        (account.total_equity_cny - account.config.initial_cash)
        / account.config.initial_cash
        * Decimal("100")
    ).quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP)
    return MvpPositionSummary(
        cash_cny=account.cash_cny,
        quantity=quantity,
        sellable_quantity=sellable,
        ineligible_quantity=quantity - sellable,
        cost_basis_cny=cost_basis,
        market_value_cny=account.market_value_cny,
        realized_pnl_cny=account.realized_pnl_cny,
        unrealized_pnl_cny=account.unrealized_pnl_cny,
        total_equity_cny=account.total_equity_cny,
        cumulative_return_percent=cumulative,
        current_drawdown_cny=account.current_drawdown_cny,
        max_drawdown_cny=account.max_drawdown_cny,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def build_mvp_daily(result: ContinuousDayResult) -> MvpChenQuantDaily:
    """Build one strict non-publishable Daily from a completed transition."""
    pending_side = (
        result.state.pending_action.action.side if result.state.pending_action is not None else None
    )
    risk_notes = [
        "SIMULATION_ONLY",
        "REAL_TRADING_DISABLED",
        (
            "FROZEN_REFERENCE_EVIDENCE"
            if result.day_input.source_fixture == "DETERMINISTIC_REFERENCE_FIXTURE"
            else (
                "VALIDATED_DAILY_INPUT"
                if result.day_input.source_fixture == "VALIDATED_DAILY_INPUT"
                else "DETERMINISTIC_TEST_FIXTURE_ONLY"
            )
        ),
    ]
    next_conditions = [
        (
            "EXECUTE_PENDING_AT_NEXT_TRADING_DAY_OPEN"
            if pending_side is not None
            else "LOAD_NEXT_VALIDATED_DAILY_INPUT"
        )
    ]
    return MvpChenQuantDaily(
        mvp_daily_schema_version=1,
        status="TICKFLOW_PAPER_TRADING_MVP_DAILY_READY",
        report_date=result.day_input.trade_date,
        symbol="000403.SZ",
        name="派林生物",
        timezone="Asia/Shanghai",
        source_fixture=result.day_input.source_fixture,
        facts_identity=result.day_input.facts_sha256,
        projection_identity=result.day_input.projection_sha256,
        claims_identity=result.day_input.claims_sha256,
        signal_identity=result.day_input.signal.signal_id,
        input_identity=result.day_input.input_identity,
        fixture_identity=result.day_input.fixture_identity,
        account_state_sha256=hashlib.sha256(canonical_option_c_bytes(result.state)).hexdigest(),
        research_signal=result.day_input.signal.code,
        paper_action=result.action.side,
        pending_action=pending_side,
        position=_position_summary(result),
        trades_today=result.executed_trades,
        decision_ledger_count=len(result.state.decision_ledger),
        trade_ledger_count=len(result.state.account.trades),
        risk_notes=risk_notes,
        next_observation_conditions=next_conditions,
        real_provider_dependency="NOT_REQUIRED",
        real_provider_attempts=0,
        real_ai_calls=0,
        real_trading="DISABLED",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def render_mvp_daily(report: MvpChenQuantDaily) -> str:
    """Render deterministic Markdown with the complete MVP account state."""
    position = report.position
    risk = "\n".join(f"- `{item}`" for item in report.risk_notes)
    next_conditions = "\n".join(f"- `{item}`" for item in report.next_observation_conditions)
    return (
        "---\n"
        "type: chenquant-paper-daily\n"
        "source_system: tickflow-stock-panel\n"
        "simulation_only: SIMULATION ONLY\n"
        "can_publish: false\n"
        "trading_advice: false\n"
        f"date: {report.report_date.isoformat()}\n"
        f"symbol: {report.symbol}\n"
        "---\n\n"
        "# ChenQuant Paper Trading Daily\n\n"
        f"- Date: {report.report_date.isoformat()}\n"
        f"- Symbol: {report.name} ({report.symbol})\n"
        f"- Facts identity: `{report.facts_identity}`\n"
        f"- Claims identity: `{report.claims_identity}`\n"
        f"- Research Signal: `{report.research_signal}`\n"
        f"- Paper Action: `{report.paper_action}`\n"
        f"- Pending Action: `{report.pending_action or 'NONE'}`\n\n"
        "## Paper Account\n\n"
        f"- Position: {position.quantity}\n"
        f"- Sellable Quantity: {position.sellable_quantity}\n"
        f"- Cash: {position.cash_cny} CNY\n"
        f"- Cost Basis: {position.cost_basis_cny} CNY\n"
        f"- Market Value: {position.market_value_cny} CNY\n"
        f"- Realized PnL: {position.realized_pnl_cny} CNY\n"
        f"- Unrealized PnL: {position.unrealized_pnl_cny} CNY\n"
        f"- Total Equity: {position.total_equity_cny} CNY\n"
        f"- Cumulative Return: {position.cumulative_return_percent}%\n"
        f"- Max Drawdown: {position.max_drawdown_cny} CNY\n"
        f"- Trades Today: {len(report.trades_today)}\n\n"
        "## Risk Notes\n\n"
        f"{risk}\n\n"
        "## Next Observation Condition\n\n"
        f"{next_conditions}\n\n"
        "## Disclaimer\n\n"
        "SIMULATION ONLY. This record cannot be published and cannot place real trades.\n"
    )


def _parse_daily(path: Path) -> MvpChenQuantDaily:
    if path.is_symlink() or not path.is_file():
        raise OptionCDailyError("daily_json_must_be_regular")
    try:
        value = json.loads(path.read_bytes())
        return MvpChenQuantDaily.model_validate(value, strict=False)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise OptionCDailyError("published_daily_invalid") from exc


def load_day_input(path: Path) -> ContinuousDayInput:
    """Load one canonical offline input without following a symlink."""
    if path.is_symlink() or not path.is_file():
        raise OptionCDailyError("daily_input_must_be_regular")
    try:
        value = json.loads(path.read_bytes())
        return ContinuousDayInput.model_validate(value, strict=False)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise OptionCDailyError("daily_input_invalid") from exc


def run_daily_once(
    repo_root: Path,
    state_root: Path,
    day_input: ContinuousDayInput,
) -> DailyRunResult:
    """Run or idempotently retrieve one complete offline trading day."""
    root = repo_root.resolve(strict=True)
    if day_input.source_fixture == "DETERMINISTIC_REFERENCE_FIXTURE":
        expected = build_reference_day_input(root)
        if canonical_option_c_bytes(expected) != canonical_option_c_bytes(day_input):
            raise OptionCDailyError("reference_input_identity_mismatch")
    store = OptionCStateStore(state_root)
    with store.locked():
        existing = store.find_published_day(
            day_input.trade_date,
            day_input.input_identity,
        )
        if existing is not None:
            recovered = store.load_or_initialize(SimulationConfig.default())
            if recovered != existing.state:
                raise OptionCDailyError("idempotent_state_recovery_mismatch")
            daily = _parse_daily(existing.path / "chenquant_daily.json")
            expected_state_sha = hashlib.sha256(
                canonical_option_c_bytes(existing.state)
            ).hexdigest()
            if daily.account_state_sha256 != expected_state_sha:
                raise OptionCDailyError("published_daily_state_identity_mismatch")
            return DailyRunResult(
                status="DAY_ALREADY_PUBLISHED",
                daily=daily,
                state=existing.state,
                output_directory=existing.path,
            )
        state = store.load_or_initialize(SimulationConfig.default())
        transition = run_continuous_day(state, day_input)
        daily = build_mvp_daily(transition)
        markdown = render_mvp_daily(daily)
        publication = store.publish_day(
            day_input,
            transition,
            daily_json=canonical_option_c_bytes(daily),
            daily_markdown=markdown.encode("utf-8"),
        )
        return DailyRunResult(
            status=publication.status,
            daily=daily,
            state=publication.state,
            output_directory=publication.path,
        )
