from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.schemas.phase2_option_c import SimulationConfig, canonical_option_c_bytes
from app.services.phase2_option_c_daily import (
    build_reference_day_input,
    render_mvp_daily,
    run_daily_once,
)
from app.services.phase2_option_c_release_fixture import (
    build_release_lifecycle_inputs,
)
from app.services.phase2_option_c_state import OptionCStateError, OptionCStateStore

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_reference_daily_runner_validates_frozen_claims_and_records_hold(
    tmp_path: Path,
) -> None:
    day_input = build_reference_day_input(REPO_ROOT)
    result = run_daily_once(
        REPO_ROOT,
        tmp_path / "state",
        day_input,
    )

    assert result.status == "DAY_PUBLISHED"
    assert result.daily.report_date == date(2026, 7, 31)
    assert result.daily.source_fixture == "DETERMINISTIC_REFERENCE_FIXTURE"
    assert result.daily.research_signal == "MIXED_OBSERVATION"
    assert result.daily.paper_action == "HOLD"
    assert result.daily.pending_action is None
    assert result.daily.trades_today == []
    assert result.daily.position.quantity == 0
    assert result.daily.position.sellable_quantity == 0
    assert result.daily.position.cash_cny == result.daily.position.total_equity_cny
    assert result.daily.facts_identity == day_input.facts_sha256
    assert result.daily.claims_identity == day_input.claims_sha256
    assert result.daily.signal_identity == day_input.signal.signal_id
    assert result.daily.input_identity == day_input.input_identity
    assert result.daily.real_provider_attempts == 0
    assert result.daily.real_ai_calls == 0
    assert result.daily.real_trading == "DISABLED"
    assert result.daily.can_publish is False

    markdown = render_mvp_daily(result.daily)
    for label in (
        "SIMULATION ONLY",
        "Facts identity",
        "Claims identity",
        "Research Signal",
        "Paper Action",
        "Pending Action",
        "Sellable Quantity",
        "Cost Basis",
        "Market Value",
        "Realized PnL",
        "Unrealized PnL",
        "Total Equity",
        "Cumulative Return",
        "Max Drawdown",
        "Trades Today",
        "Risk Notes",
        "Next Observation Condition",
        "can_publish: false",
    ):
        assert label in markdown


def test_daily_runner_is_idempotent_for_same_input(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    day_input = build_reference_day_input(REPO_ROOT)
    first = run_daily_once(REPO_ROOT, state_root, day_input)
    before = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in state_root.rglob("*")
        if path.is_file() and path.name != ".runtime.lock"
    }

    (state_root / "current_state.json").unlink()
    second = run_daily_once(REPO_ROOT, state_root, day_input)
    after = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in state_root.rglob("*")
        if path.is_file() and path.name != ".runtime.lock"
    }

    assert first.status == "DAY_PUBLISHED"
    assert second.status == "DAY_ALREADY_PUBLISHED"
    assert second.daily == first.daily
    assert second.state == first.state
    assert after == before
    assert (state_root / "current_state.json").read_bytes() == canonical_option_c_bytes(
        second.state
    )
    assert len(second.state.decision_ledger) == 1
    assert second.state.account.trades == []


def test_lifecycle_daily_reports_pending_execution_and_pnl(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    results = [
        run_daily_once(REPO_ROOT, state_root, day_input)
        for day_input in build_release_lifecycle_inputs()
    ]

    assert [result.daily.paper_action for result in results] == [
        "BUY",
        "HOLD",
        "SELL",
        "HOLD",
    ]
    assert [result.daily.pending_action for result in results] == [
        "BUY",
        None,
        "SELL",
        None,
    ]
    assert [len(result.daily.trades_today) for result in results] == [0, 1, 0, 1]
    assert results[1].daily.trades_today[0].side == "BUY"
    assert results[1].daily.position.quantity == 100
    assert results[1].daily.position.sellable_quantity == 0
    assert results[2].daily.position.sellable_quantity == 100
    assert results[3].daily.trades_today[0].side == "SELL"
    assert results[3].daily.position.quantity == 0
    assert results[3].daily.position.realized_pnl_cny == Decimal("39.45")
    assert results[3].daily.position.total_equity_cny == Decimal("100039.45")
    assert results[3].daily.position.max_drawdown_cny == Decimal("25.55")
    assert results[3].state.account.trades[0] == results[1].state.account.trades[0]
    assert len(results[3].state.decision_ledger) == 4


def test_daily_runner_recovers_after_current_pointer_loss(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    inputs = build_release_lifecycle_inputs()
    first = run_daily_once(REPO_ROOT, state_root, inputs[0])
    (state_root / "current_state.json").unlink()

    second = run_daily_once(REPO_ROOT, state_root, inputs[1])

    assert first.daily.pending_action == "BUY"
    assert second.daily.trades_today[0].side == "BUY"
    assert len(second.state.decision_ledger) == 2
    assert len(second.state.account.trades) == 1


def test_daily_runner_lock_blocks_second_process_before_transition(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    store = OptionCStateStore(state_root)
    with (
        store.locked(),
        pytest.raises(
            OptionCStateError,
            match="daily_runner_already_locked",
        ),
    ):
        run_daily_once(
            REPO_ROOT,
            state_root,
            build_reference_day_input(REPO_ROOT),
        )
    assert not (state_root / "days" / "2026-07-31").exists()


def test_daily_cli_runs_reference_once_and_reports_idempotent_second_run(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    command = [
        str(REPO_ROOT / "backend" / ".venv" / "bin" / "python"),
        str(REPO_ROOT / "backend" / "scripts" / "run_phase2_option_c_daily.py"),
        "--repo-root",
        str(REPO_ROOT),
        "--state-dir",
        str(state_root),
        "--reference",
    ]
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "backend")}

    first = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    second = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert json.loads(first.stdout)["status"] == "DAY_PUBLISHED"
    assert json.loads(second.stdout)["status"] == "DAY_ALREADY_PUBLISHED"
    assert json.loads(second.stdout)["paper_action"] == "HOLD"
    state = OptionCStateStore(state_root).load_or_initialize(SimulationConfig.default())
    assert canonical_option_c_bytes(state) == (state_root / "current_state.json").read_bytes()
