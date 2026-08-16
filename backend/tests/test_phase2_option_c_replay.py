from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.services.phase2_chenquant_daily import (
    run_reference_simulation,
    validate_replay,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_reference_simulation_replay_x3_is_byte_identical() -> None:
    runs = [run_reference_simulation(REPO_ROOT) for _ in range(3)]
    result = validate_replay(runs)

    assert result.status == "DETERMINISTIC_REPLAY_PASSED"
    assert result.replay_count == 3
    assert result.mismatched_artifacts == []
    assert result.trade_ledger_identical is True
    assert result.position_identical is True
    assert result.pnl_identical is True
    assert result.json_identical is True
    assert result.markdown_identical is True
    assert sorted(result.artifact_sha256) == sorted(runs[0].artifacts)
    assert result.simulation_only == "SIMULATION ONLY"
    assert result.can_publish is False
    assert result.trading_advice is False


def test_replay_reports_mismatch_without_normalizing_it() -> None:
    runs = [run_reference_simulation(REPO_ROOT) for _ in range(3)]
    altered_artifacts = dict(runs[2].artifacts)
    altered_artifacts["trade_ledger.json"] += b" "
    runs[2] = replace(runs[2], artifacts=altered_artifacts)

    result = validate_replay(runs)

    assert result.status == "DETERMINISTIC_REPLAY_FAILED"
    assert result.mismatched_artifacts == ["trade_ledger.json"]
    assert result.trade_ledger_identical is False
    assert result.json_identical is False


def test_replay_requires_exactly_three_runs() -> None:
    result = validate_replay([run_reference_simulation(REPO_ROOT)])

    assert result.status == "DETERMINISTIC_REPLAY_FAILED"
    assert result.errors == ["replay_count_must_equal_three"]
