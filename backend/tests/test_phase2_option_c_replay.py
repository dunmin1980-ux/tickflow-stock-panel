from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.services.phase2_chenquant_daily import (
    run_reference_simulation,
    validate_replay,
)
from app.services.phase2_option_c_delivery import (
    OptionCDeliveryError,
    hash_protected_evidence,
    publish_option_c_run,
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


def test_atomic_delivery_publishes_only_closed_artifact_tree(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    protected_before = hash_protected_evidence(REPO_ROOT)

    result = publish_option_c_run(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )

    output = reports_root / "phase2_option_c"
    expected = {
        "reference_typed_claims.json",
        "reference_fixture_manifest.json",
        "account_config.json",
        "decision.json",
        "paper_account.json",
        "trade_ledger.json",
        "chenquant_daily.json",
        "chenquant_daily.md",
        "replay_validation.json",
        "evidence_index.json",
    }
    assert result.status == "PHASE2B_OPTION_C_PAPER_TRADING_READY"
    assert {path.name for path in output.iterdir()} == expected
    assert all(path.is_file() and not path.is_symlink() for path in output.iterdir())
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in output.iterdir())
    assert not list(reports_root.glob(".phase2_option_c.staging-*"))
    assert "DETERMINISTIC_TEST_FIXTURE" not in "".join(
        path.read_text(encoding="utf-8") for path in output.iterdir()
    )
    assert result.protected_before == protected_before
    assert result.protected_after == protected_before
    assert hash_protected_evidence(REPO_ROOT) == protected_before


def test_atomic_delivery_is_repository_idempotent(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()

    first = publish_option_c_run(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )
    first_bytes = {
        path.name: path.read_bytes()
        for path in (reports_root / "phase2_option_c").iterdir()
    }
    second = publish_option_c_run(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )
    second_bytes = {
        path.name: path.read_bytes()
        for path in (reports_root / "phase2_option_c").iterdir()
    }

    assert first.artifact_sha256 == second.artifact_sha256
    assert first_bytes == second_bytes


def test_atomic_delivery_failure_preserves_previous_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.phase2_option_c_delivery as delivery

    reports_root = tmp_path / "reports"
    destination = reports_root / "phase2_option_c"
    destination.mkdir(parents=True)
    marker = destination / "old.txt"
    marker.write_text("old\n", encoding="utf-8")

    def fail_publish(_staging: Path, _destination: Path) -> None:
        raise OSError("injected_atomic_failure")

    monkeypatch.setattr(delivery, "atomic_publish_directory", fail_publish)

    with pytest.raises(OSError, match="injected_atomic_failure"):
        publish_option_c_run(
            REPO_ROOT,
            reports_root,
            _allow_test_output_root=True,
        )

    assert marker.read_text(encoding="utf-8") == "old\n"
    assert {path.name for path in destination.iterdir()} == {"old.txt"}
    assert not list(reports_root.glob(".phase2_option_c.staging-*"))


def test_delivery_rejects_symlinked_reports_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    reports_root = tmp_path / "reports"
    reports_root.symlink_to(target, target_is_directory=True)

    with pytest.raises(OptionCDeliveryError, match="reports_root_must_not_be_symlink"):
        publish_option_c_run(
            REPO_ROOT,
            reports_root,
            _allow_test_output_root=True,
        )
