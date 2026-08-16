from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.phase2_chenquant_daily import (
    build_chenquant_daily,
    render_chenquant_daily,
    run_reference_simulation,
)
from app.services.phase2_option_c_fixture import ReferenceFixtureError

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_reference_daily_contains_complete_non_publishable_hold_state() -> None:
    run = run_reference_simulation(REPO_ROOT)
    daily = run.daily

    assert daily.status == "OPTION_C_REFERENCE_READY"
    assert daily.report_date.isoformat() == "2026-07-31"
    assert daily.symbol == "000403.SZ"
    assert daily.name == "派林生物"
    assert daily.price_state.mark_price == Decimal("10.43")
    assert daily.price_state.price_basis == "raw"
    assert daily.facts_summary.daily_close == Decimal("10.43")
    assert daily.facts_summary.macd_relation == "ABOVE"
    assert daily.facts_summary.rsi6 == Decimal("76.21830812329453")
    assert daily.facts_summary.ma_order == ["ma60", "ma5", "ma10", "ma20"]
    assert daily.facts_summary.market_scope == "incomplete"
    assert len(daily.validated_claim_ids) == 42
    assert daily.interpretation == "MIXED_TECHNICAL_STRUCTURE"
    assert daily.research_signal == "MIXED_OBSERVATION"
    assert daily.paper_action == "HOLD"
    assert daily.simulated_trades == []
    assert daily.position.cash_cny == Decimal("100000.00")
    assert daily.position.quantity == 0
    assert daily.position.position_cost_cny == Decimal("0.00")
    assert daily.position.realized_pnl_cny == Decimal("0.00")
    assert daily.position.unrealized_pnl_cny == Decimal("0.00")
    assert daily.position.total_equity_cny == Decimal("100000.00")
    assert daily.position.cumulative_return_percent == Decimal("0.0000")
    assert daily.position.max_drawdown_cny == Decimal("0.00")
    assert daily.simulation_only == "SIMULATION ONLY"
    assert daily.can_publish is False
    assert daily.trading_advice is False
    assert daily.real_provider_attempts == 0
    assert daily.real_ai_calls == 0
    assert daily.real_trading == "DISABLED"


def test_reference_run_records_hold_without_execution() -> None:
    run = run_reference_simulation(REPO_ROOT)

    assert run.decision.action.side == "HOLD"
    assert run.account.processed_action_ids == [run.decision.action.action_id]
    assert run.account.trades == []
    assert run.account.lots == []
    assert run.account.mark_price == Decimal("10.43")
    assert run.account.mark_trade_date.isoformat() == "2026-07-31"
    assert "DETERMINISTIC_TEST_FIXTURE" not in b"".join(run.artifacts.values()).decode(
        "utf-8"
    )


def test_chenquant_markdown_is_fixed_safe_and_deterministic() -> None:
    first = run_reference_simulation(REPO_ROOT)
    second = run_reference_simulation(REPO_ROOT)
    rendered = render_chenquant_daily(first.daily)

    assert rendered == render_chenquant_daily(second.daily)
    assert rendered.encode("utf-8") == first.artifacts["chenquant_daily.md"]
    assert "SIMULATION ONLY" in rendered
    assert "can_publish: false" in rendered
    assert "trading_advice: false" in rendered
    assert "不构成投资建议" in rendered
    assert "MIXED_OBSERVATION" in rendered
    assert "HOLD" in rendered
    assert "100000.00" in rendered
    assert "10.43" in rendered
    assert "http://" not in rendered
    assert "https://" not in rendered
    assert "<script" not in rendered.lower()
    for forbidden in ("买入", "卖出", "加仓", "减仓", "止损位", "操作建议"):
        assert forbidden not in rendered


def test_reference_artifact_set_is_closed_and_canonical() -> None:
    run = run_reference_simulation(REPO_ROOT)

    assert sorted(run.artifacts) == [
        "account_config.json",
        "chenquant_daily.json",
        "chenquant_daily.md",
        "decision.json",
        "paper_account.json",
        "trade_ledger.json",
    ]
    assert all(raw.endswith(b"\n") for raw in run.artifacts.values())


def test_daily_rejects_projection_content_with_stale_self_hash() -> None:
    run = run_reference_simulation(REPO_ROOT)
    altered_projection = deepcopy(run.bundle.projection)
    altered_projection["safe_facts"]["indicators"]["rsi6"]["value"] = 1

    with pytest.raises(ReferenceFixtureError, match="projection_identity_mismatch"):
        build_chenquant_daily(
            replace(run.bundle, projection=altered_projection),
            run.decision,
            run.account,
        )
