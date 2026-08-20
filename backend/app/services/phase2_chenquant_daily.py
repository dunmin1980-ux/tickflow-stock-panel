"""Deterministic ChenQuant Daily output and replay validation."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from app.schemas.phase2_option_c import (
    ChenQuantDaily,
    DailyFactsSummary,
    PaperAccount,
    PaperPositionSummary,
    PriceState,
    ReplayValidation,
    ResearchDecision,
    SimulationConfig,
    ValuationBar,
    canonical_option_c_bytes,
    money,
)
from app.services.phase2_option_c_fixture import (
    ReferenceFixtureBundle,
    load_reference_fixture,
    verify_reference_projection,
)
from app.services.phase2_paper_trading import execute_action, mark_to_market, new_account
from app.services.phase2_research_decision import build_research_decision

REFERENCE_DECISION_AT = datetime.fromisoformat("2026-07-31T21:15:00+08:00")
ZERO = Decimal("0.00")
PERCENT_QUANTUM = Decimal("0.0001")
REQUIRED_REPLAY_ARTIFACTS = frozenset(
    {
        "account_config.json",
        "chenquant_daily.json",
        "chenquant_daily.md",
        "decision.json",
        "paper_account.json",
        "trade_ledger.json",
    }
)


@dataclass(frozen=True)
class OptionCRun:
    bundle: ReferenceFixtureBundle
    config: SimulationConfig
    decision: ResearchDecision
    account: PaperAccount
    daily: ChenQuantDaily
    artifacts: dict[str, bytes]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims_by_predicate(bundle: ReferenceFixtureBundle) -> dict[str, dict[str, Any]]:
    return {
        claim.predicate: claim.model_dump(mode="json")
        for claim in bundle.claims.claims
    }


def _position_summary(
    account: PaperAccount,
    report_date: date,
) -> PaperPositionSummary:
    quantity = sum(lot.remaining_quantity for lot in account.lots)
    eligible = sum(
        lot.remaining_quantity
        for lot in account.lots
        if lot.acquired_trade_date < report_date
    )
    position_cost = sum(
        (lot.remaining_cost_cny for lot in account.lots),
        start=ZERO,
    )
    cumulative_return = (
        (account.total_equity_cny - account.config.initial_cash)
        / account.config.initial_cash
        * Decimal("100")
    ).quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP)
    return PaperPositionSummary(
        cash_cny=money(account.cash_cny),
        quantity=quantity,
        eligible_quantity=eligible,
        ineligible_quantity=quantity - eligible,
        position_cost_cny=money(position_cost),
        market_value_cny=money(account.market_value_cny),
        realized_pnl_cny=money(account.realized_pnl_cny),
        unrealized_pnl_cny=money(account.unrealized_pnl_cny),
        total_equity_cny=money(account.total_equity_cny),
        cumulative_return_percent=cumulative_return,
        current_drawdown_cny=money(account.current_drawdown_cny),
        max_drawdown_cny=money(account.max_drawdown_cny),
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def build_chenquant_daily(
    bundle: ReferenceFixtureBundle,
    decision: ResearchDecision,
    account: PaperAccount,
) -> ChenQuantDaily:
    """Build the closed single-symbol daily report from validated snapshots."""
    verify_reference_projection(bundle)
    claims = _claims_by_predicate(bundle)
    daily_close = Decimal(str(bundle.projection["safe_facts"]["daily"]["close"]))
    rsi6 = Decimal(
        str(bundle.projection["safe_facts"]["indicators"]["rsi6"]["value"])
    )
    macd_relation = claims["macd_dif_vs_dea"]["object"]["relation"]
    ma_order = [
        operand["label_id"]
        for operand in claims["ma_value_order"]["object"]["operands"]
    ]
    return ChenQuantDaily(
        daily_schema_version=1,
        status="OPTION_C_REFERENCE_READY",
        report_date=bundle.manifest.trade_date,
        symbol="000403.SZ",
        name="派林生物",
        timezone="Asia/Shanghai",
        price_state=PriceState(
            mark_price=money(daily_close),
            price_basis="raw",
            trade_date=bundle.manifest.trade_date,
            source="PHASE2_FROZEN_FACTS",
            simulation_only="SIMULATION ONLY",
            can_publish=False,
            trading_advice=False,
        ),
        facts_summary=DailyFactsSummary(
            daily_close=money(daily_close),
            daily_close_basis="raw",
            macd_relation=macd_relation,
            rsi6=rsi6,
            ma_order=ma_order,
            market_scope=claims["market_scope"]["object"]["value"],
            simulation_only="SIMULATION ONLY",
            can_publish=False,
            trading_advice=False,
        ),
        validated_claim_ids=[claim.claim_id for claim in bundle.claims.claims],
        interpretation=decision.interpretation.code,
        research_signal=decision.signal.code,
        paper_action=decision.action.side,
        action_reason_refs=decision.action.reason_refs,
        position=_position_summary(account, bundle.manifest.trade_date),
        simulated_trades=list(account.trades),
        facts_sha256=bundle.manifest.facts_sha256,
        projection_sha256=bundle.manifest.projection_sha256,
        claims_sha256=bundle.manifest.claims_sha256,
        fixture_manifest_sha256=decision.fixture_manifest_sha256,
        config_sha256=account.config_sha256,
        decision_id=decision.decision_id,
        vendor_pending=list(bundle.manifest.vendor_pending),
        risk_notices=[
            "MARKET_SCOPE_INCOMPLETE",
            "FINANCIAL_DATA_UNAVAILABLE",
            "NEWS_DATA_UNAVAILABLE",
            "VENDOR_SEMANTICS_PENDING",
            "SIMULATION_NOT_INVESTMENT_ADVICE",
        ],
        next_observation_conditions=[
            "RECHECK_VALIDATED_CLAIMS",
            "VERIFY_VENDOR_PENDING",
            "WAIT_FOR_NEXT_APPROVED_DAILY_EVIDENCE",
        ],
        real_provider_integration="DEFERRED_FROZEN",
        real_provider_attempts=0,
        real_ai_calls=0,
        real_trading="DISABLED",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def render_chenquant_daily(report: ChenQuantDaily) -> str:
    """Render fixed Markdown from one strict Daily model."""
    position = report.position
    reason_lines = "\n".join(f"- `{item}`" for item in report.action_reason_refs)
    vendor_lines = "\n".join(f"- `{item}`" for item in report.vendor_pending)
    risk_lines = "\n".join(f"- `{item}`" for item in report.risk_notices)
    next_lines = "\n".join(
        f"- `{item}`" for item in report.next_observation_conditions
    )
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
        "# ChenQuant 个股模拟日报\n\n"
        "## 数据状态\n\n"
        f"- 标的: {report.name} ({report.symbol})\n"
        f"- 收盘盯市: {report.price_state.mark_price} CNY (raw)\n"
        f"- 已验证 Claims: {len(report.validated_claim_ids)} 项\n"
        f"- MACD 关系: {report.facts_summary.macd_relation}\n"
        f"- RSI6: {report.facts_summary.rsi6}\n"
        f"- 均线次序: {' > '.join(report.facts_summary.ma_order)}\n\n"
        "## 研究状态\n\n"
        f"- Interpretation: {report.interpretation}\n"
        f"- Signal: {report.research_signal}\n"
        f"- Paper Action: {report.paper_action}\n"
        f"{reason_lines}\n\n"
        "## 模拟账户\n\n"
        f"- 现金: {position.cash_cny} CNY\n"
        f"- 数量: {position.quantity}\n"
        f"- 持仓成本: {position.position_cost_cny} CNY\n"
        f"- 已实现盈亏: {position.realized_pnl_cny} CNY\n"
        f"- 未实现盈亏: {position.unrealized_pnl_cny} CNY\n"
        f"- 总权益: {position.total_equity_cny} CNY\n"
        f"- 累计收益: {position.cumulative_return_percent}%\n"
        f"- 最大回撤: {position.max_drawdown_cny} CNY\n"
        f"- 当日模拟成交: {len(report.simulated_trades)} 笔\n\n"
        "## 风险标识\n\n"
        f"{risk_lines}\n\n"
        "## 供应商待确认\n\n"
        f"{vendor_lines}\n\n"
        "## 下一观察条件\n\n"
        f"{next_lines}\n\n"
        "## 免责声明\n\n"
        "本文仅为确定性模拟研究记录, 不构成投资建议, 不连接真实证券交易。\n"
    )


def run_reference_simulation(repo_root: Path) -> OptionCRun:
    """Run the frozen HOLD-only Option C path without external access."""
    bundle = load_reference_fixture(repo_root)
    config = SimulationConfig.default()
    decision = build_research_decision(bundle, REFERENCE_DECISION_AT)
    account = execute_action(
        new_account(config),
        decision.action,
        None,
        signal=decision.signal,
    )
    daily_facts = bundle.projection["safe_facts"]["daily"]
    account = mark_to_market(
        account,
        ValuationBar(
            valuation_bar_schema_version=1,
            source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
            scenario_fixture_identity=decision.fixture_manifest_sha256,
            symbol="000403.SZ",
            trade_date=bundle.manifest.trade_date,
            previous_trade_date=date(2026, 7, 30),
            available_at=bundle.manifest.evidence_available_at,
            timezone="Asia/Shanghai",
            close=Decimal(str(daily_facts["close"])),
            price_basis="raw",
            simulation_only="SIMULATION ONLY",
            can_publish=False,
            trading_advice=False,
        ),
        valuation_at=REFERENCE_DECISION_AT,
    )
    daily = build_chenquant_daily(bundle, decision, account)
    ledger = {
        "trade_ledger_schema_version": 1,
        "symbol": "000403.SZ",
        "trade_count": len(account.trades),
        "trades": [trade.model_dump(mode="json") for trade in account.trades],
        "simulation_only": "SIMULATION ONLY",
        "can_publish": False,
        "trading_advice": False,
    }
    artifacts = {
        "account_config.json": canonical_option_c_bytes(config),
        "decision.json": canonical_option_c_bytes(decision),
        "paper_account.json": canonical_option_c_bytes(account),
        "trade_ledger.json": canonical_option_c_bytes(ledger),
        "chenquant_daily.json": canonical_option_c_bytes(daily),
        "chenquant_daily.md": render_chenquant_daily(daily).encode("utf-8"),
    }
    return OptionCRun(
        bundle=bundle,
        config=config,
        decision=decision,
        account=account,
        daily=daily,
        artifacts=artifacts,
    )


def validate_replay(runs: Sequence[OptionCRun]) -> ReplayValidation:
    """Compare exactly three complete runs without normalizing any bytes."""
    if len(runs) != 3:
        return ReplayValidation(
            replay_schema_version=1,
            status="DETERMINISTIC_REPLAY_FAILED",
            replay_count=len(runs),
            artifact_sha256={},
            mismatched_artifacts=[],
            trade_ledger_identical=False,
            position_identical=False,
            pnl_identical=False,
            json_identical=False,
            markdown_identical=False,
            errors=["replay_count_must_equal_three"],
            simulation_only="SIMULATION ONLY",
            can_publish=False,
            trading_advice=False,
        )
    artifact_names = sorted(set().union(*(run.artifacts for run in runs)))
    missing_required = sorted(
        REQUIRED_REPLAY_ARTIFACTS
        - set.intersection(*(set(run.artifacts) for run in runs))
    )
    unexpected_artifacts = sorted(
        set(artifact_names) - REQUIRED_REPLAY_ARTIFACTS
    )
    mismatched = [
        name
        for name in artifact_names
        if any(
            name not in run.artifacts or run.artifacts[name] != runs[0].artifacts.get(name)
            for run in runs[1:]
        )
    ]
    artifact_hashes = {
        name: _sha256(runs[0].artifacts[name])
        for name in sorted(runs[0].artifacts)
    }
    complete_artifact_set = not missing_required and not unexpected_artifacts
    json_names = [name for name in artifact_names if name.endswith(".json")]
    json_identical = complete_artifact_set and not any(
        name in mismatched for name in json_names
    )
    replay_passed = complete_artifact_set and not mismatched
    return ReplayValidation(
        replay_schema_version=1,
        status=(
            "DETERMINISTIC_REPLAY_PASSED"
            if replay_passed
            else "DETERMINISTIC_REPLAY_FAILED"
        ),
        replay_count=3,
        artifact_sha256=artifact_hashes,
        mismatched_artifacts=mismatched,
        trade_ledger_identical=(
            complete_artifact_set and "trade_ledger.json" not in mismatched
        ),
        position_identical=(
            complete_artifact_set and "paper_account.json" not in mismatched
        ),
        pnl_identical=(
            complete_artifact_set
            and "paper_account.json" not in mismatched
            and "chenquant_daily.json" not in mismatched
        ),
        json_identical=json_identical,
        markdown_identical=(
            complete_artifact_set and "chenquant_daily.md" not in mismatched
        ),
        errors=(
            []
            if replay_passed
            else [
                *(["replay_required_artifacts_missing"] if missing_required else []),
                *(["replay_unexpected_artifacts"] if unexpected_artifacts else []),
                *(["replay_artifact_mismatch"] if mismatched else []),
            ]
        ),
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
