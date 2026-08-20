from __future__ import annotations

import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

from app.services.phase2_option_c_delivery import hash_protected_evidence
from app.services.phase2_option_c_release import (
    publish_mvp_release,
    verify_release_replay,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_release_replay_x3_is_byte_identical_from_empty_account() -> None:
    replay = verify_release_replay(REPO_ROOT)

    assert replay.status == "MVP_RELEASE_REPLAY_PASSED"
    assert replay.replay_count == 3
    assert replay.mismatched_artifacts == []
    assert replay.decision_ledger_identical is True
    assert replay.trade_ledger_identical is True
    assert replay.positions_identical is True
    assert replay.cash_identical is True
    assert replay.pnl_identical is True
    assert replay.equity_history_identical is True
    assert replay.daily_json_identical is True
    assert replay.daily_markdown_identical is True
    assert len(replay.release_sha256) == 64
    assert replay.final_trade_count == 2
    assert replay.final_position_quantity == 0
    assert replay.final_realized_pnl_cny == Decimal("39.45")
    assert replay.real_provider_attempts == 0
    assert replay.real_ai_calls == 0
    assert replay.real_trading == "DISABLED"


def test_release_publication_separates_reference_and_test_lifecycle(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    protected_before = hash_protected_evidence(REPO_ROOT)

    result = publish_mvp_release(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )

    assert result.status == "TICKFLOW_PAPER_TRADING_MVP_RELEASE_READY"
    assert result.protected_before == protected_before
    assert result.protected_after == protected_before
    assert hash_protected_evidence(REPO_ROOT) == protected_before
    output = reports_root / "phase2_option_c_mvp"
    reference_text = "".join(
        path.read_text(encoding="utf-8")
        for path in (output / "reference_state").rglob("*")
        if path.is_file()
    )
    lifecycle_text = "".join(
        path.read_text(encoding="utf-8")
        for path in (output / "lifecycle_state").rglob("*")
        if path.is_file()
    )
    assert "DETERMINISTIC_TEST_FIXTURE" not in reference_text
    assert "DETERMINISTIC_REFERENCE_FIXTURE" in reference_text
    assert "DETERMINISTIC_TEST_FIXTURE" in lifecycle_text
    assert "DETERMINISTIC_REFERENCE_FIXTURE" not in lifecycle_text
    assert "MIXED_OBSERVATION" in reference_text
    assert '"paper_action": "HOLD"' in reference_text
    assert '"side": "BUY"' in lifecycle_text
    assert '"side": "SELL"' in lifecycle_text
    evidence = json.loads((output / "release_verification.json").read_bytes())
    assert evidence["status"] == "TICKFLOW_PAPER_TRADING_MVP_RELEASE_READY"
    assert evidence["mvp_release_replay_x3"] == "PASSED"
    assert evidence["real_provider_attempts"] == 0
    assert evidence["real_ai_calls"] == 0
    assert evidence["real_trading"] == "DISABLED"


def test_release_verifier_cli_publishes_machine_evidence(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    command = [
        str(REPO_ROOT / "backend" / ".venv" / "bin" / "python"),
        str(REPO_ROOT / "backend" / "scripts" / "verify_phase2_option_c_mvp.py"),
        "--repo-root",
        str(REPO_ROOT),
        "--reports-root",
        str(reports_root),
        "--allow-test-output-root",
    ]
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "backend")}

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["status"] == "TICKFLOW_PAPER_TRADING_MVP_RELEASE_READY"
    assert summary["mvp_release_replay_x3"] == "PASSED"
    assert len(summary["release_sha256"]) == 64
    assert summary["protected_evidence_unchanged"] is True
    assert summary["real_provider_attempts"] == 0
    assert summary["real_ai_calls"] == 0
    assert summary["real_trading"] == "DISABLED"
