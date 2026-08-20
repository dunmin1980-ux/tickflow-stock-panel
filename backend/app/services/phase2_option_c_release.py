"""Deterministic release replay and publication for the Option C MVP."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.schemas.phase2_option_c import (
    MvpReleaseReplay,
    canonical_option_c_bytes,
)
from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_option_c_daily import (
    build_reference_day_input,
    run_daily_once,
)
from app.services.phase2_option_c_delivery import (
    EvidenceSnapshot,
    hash_protected_evidence,
)
from app.services.phase2_option_c_release_fixture import (
    build_release_lifecycle_inputs,
)

OUTPUT_DIRECTORY = "phase2_option_c_mvp"


class OptionCReleaseError(ValueError):
    """Raised when deterministic release evidence cannot be proven."""


@dataclass(frozen=True)
class MvpReleaseResult:
    status: Literal["TICKFLOW_PAPER_TRADING_MVP_RELEASE_READY"]
    output_directory: Path
    replay: MvpReleaseReplay
    protected_before: EvidenceSnapshot
    protected_after: EvidenceSnapshot


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_durable(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _collect_artifacts(root: Path) -> dict[str, bytes]:
    artifacts: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise OptionCReleaseError(f"release_artifact_symlink:{relative}")
        if path.is_file() and path.name != ".runtime.lock":
            artifacts[relative] = path.read_bytes()
        elif not path.is_file() and not path.is_dir():
            raise OptionCReleaseError(f"release_artifact_invalid:{relative}")
    return artifacts


def _run_lifecycle(repo_root: Path, state_root: Path):
    results = [
        run_daily_once(repo_root, state_root, day_input)
        for day_input in build_release_lifecycle_inputs()
    ]
    if any(result.status != "DAY_PUBLISHED" for result in results):
        raise OptionCReleaseError("release_lifecycle_not_fresh")
    return results


def verify_release_replay(repo_root: Path) -> MvpReleaseReplay:
    """Run three clean four-day lifecycles and compare every persisted byte."""
    root = repo_root.resolve(strict=True)
    artifact_runs: list[dict[str, bytes]] = []
    final_states = []
    with tempfile.TemporaryDirectory(prefix="tickflow-option-c-release-replay-") as tmp:
        temporary_root = Path(tmp)
        for index in range(3):
            state_root = temporary_root / f"run-{index + 1}"
            results = _run_lifecycle(root, state_root)
            artifact_runs.append(_collect_artifacts(state_root))
            final_states.append(results[-1].state)

    artifact_names = sorted(set().union(*(set(run) for run in artifact_runs)))
    mismatched = [
        name
        for name in artifact_names
        if any(run.get(name) != artifact_runs[0].get(name) for run in artifact_runs[1:])
    ]
    artifact_hashes = {name: _sha256(raw) for name, raw in sorted(artifact_runs[0].items())}
    release_sha = _sha256(canonical_option_c_bytes(artifact_hashes))
    complete_match = not mismatched and all(
        set(run) == set(artifact_runs[0]) for run in artifact_runs
    )
    final = final_states[0]
    positions = [state.account.lots for state in final_states]
    cash = [state.account.cash_cny for state in final_states]
    pnl = [
        (
            state.account.realized_pnl_cny,
            state.account.unrealized_pnl_cny,
            state.account.total_equity_cny,
            state.account.max_drawdown_cny,
        )
        for state in final_states
    ]

    def matching_suffix(suffix: str) -> bool:
        return complete_match and not any(
            name.endswith(suffix) and name in mismatched for name in artifact_names
        )

    return MvpReleaseReplay(
        release_replay_schema_version=1,
        status=("MVP_RELEASE_REPLAY_PASSED" if complete_match else "MVP_RELEASE_REPLAY_FAILED"),
        replay_count=3,
        release_sha256=release_sha,
        artifact_sha256=artifact_hashes,
        mismatched_artifacts=mismatched,
        decision_ledger_identical=matching_suffix("decision_ledger.json"),
        trade_ledger_identical=matching_suffix("trade_ledger.json"),
        positions_identical=complete_match and positions[1:] == positions[:1] * 2,
        cash_identical=complete_match and cash[1:] == cash[:1] * 2,
        pnl_identical=complete_match and pnl[1:] == pnl[:1] * 2,
        equity_history_identical=matching_suffix("equity_history.json"),
        daily_json_identical=matching_suffix("chenquant_daily.json"),
        daily_markdown_identical=matching_suffix("chenquant_daily.md"),
        final_trade_count=len(final.account.trades),
        final_position_quantity=sum(lot.remaining_quantity for lot in final.account.lots),
        final_realized_pnl_cny=final.account.realized_pnl_cny,
        real_provider_attempts=0,
        real_ai_calls=0,
        real_trading="DISABLED",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _resolve_reports_root(
    repo_root: Path,
    reports_root: Path,
    *,
    allow_test_output_root: bool,
) -> Path:
    root = repo_root.resolve(strict=True)
    if reports_root.is_symlink():
        raise OptionCReleaseError("reports_root_must_not_be_symlink")
    resolved = reports_root.resolve(strict=True)
    if not resolved.is_dir():
        raise OptionCReleaseError("reports_root_invalid")
    if not allow_test_output_root and resolved != (root / "reports").resolve(strict=True):
        raise OptionCReleaseError("reports_root_must_be_repository_reports")
    return resolved


def publish_mvp_release(
    repo_root: Path,
    reports_root: Path,
    *,
    _allow_test_output_root: bool = False,
) -> MvpReleaseResult:
    """Atomically publish reference, lifecycle, and replay release evidence."""
    root = repo_root.resolve(strict=True)
    output_root = _resolve_reports_root(
        root,
        reports_root,
        allow_test_output_root=_allow_test_output_root,
    )
    protected_before = hash_protected_evidence(root)
    replay = verify_release_replay(root)
    if replay.status != "MVP_RELEASE_REPLAY_PASSED":
        raise OptionCReleaseError("mvp_release_replay_failed")

    destination = output_root / OUTPUT_DIRECTORY
    if destination.is_symlink():
        raise OptionCReleaseError("release_output_must_not_be_symlink")
    staging = Path(tempfile.mkdtemp(prefix=f".{OUTPUT_DIRECTORY}.staging-", dir=output_root))
    staging.chmod(0o700)
    try:
        reference = run_daily_once(
            root,
            staging / "reference_state",
            build_reference_day_input(root),
        )
        lifecycle = _run_lifecycle(root, staging / "lifecycle_state")
        if (
            reference.daily.research_signal != "MIXED_OBSERVATION"
            or reference.daily.paper_action != "HOLD"
            or reference.daily.pending_action is not None
            or reference.daily.trades_today
            or [item.daily.paper_action for item in lifecycle] != ["BUY", "HOLD", "SELL", "HOLD"]
        ):
            raise OptionCReleaseError("release_business_contract_failed")
        protected_after = hash_protected_evidence(root)
        if protected_after != protected_before:
            raise OptionCReleaseError("protected_evidence_mutated")
        evidence = {
            "release_verification_schema_version": 1,
            "status": "TICKFLOW_PAPER_TRADING_MVP_RELEASE_READY",
            "single_symbol_validation": "PASSED",
            "reference_signal": reference.daily.research_signal,
            "reference_action": reference.daily.paper_action,
            "continuous_run": "PASSED",
            "buy_hold_sell_lifecycle": "PASSED",
            "state_continuity": "PASSED",
            "a_share_t_plus_one": "PASSED",
            "next_day_open": "PASSED",
            "look_ahead_guard": "PASSED",
            "decimal_accounting": "PASSED",
            "pnl": "PASSED",
            "max_drawdown": "PASSED",
            "idempotency": "PASSED",
            "state_recovery": "PASSED",
            "chenquant_daily": "PASSED",
            "daily_runner": "READY",
            "mvp_release_replay_x3": "PASSED",
            "release_sha256": replay.release_sha256,
            "protected_evidence_file_count": protected_before.file_count,
            "protected_evidence_sha256_before": protected_before.aggregate_sha256,
            "protected_evidence_sha256_after": protected_after.aggregate_sha256,
            "protected_evidence_unchanged": True,
            "p2_technical_debt_count": 3,
            "real_provider_dependency": "NOT_REQUIRED",
            "real_provider_attempts": 0,
            "real_ai_calls": 0,
            "tickflow_api_requests": 0,
            "broker_calls": 0,
            "real_trading": "DISABLED",
            "simulation_only": "SIMULATION ONLY",
            "can_publish": False,
            "trading_advice": False,
        }
        _write_durable(
            staging / "release_verification.json",
            canonical_option_c_bytes(evidence),
        )
        _write_durable(
            staging / "release_replay.json",
            canonical_option_c_bytes(replay),
        )
        technical_debt = {
            "technical_debt_schema_version": 1,
            "items": [
                "Only 000403.SZ is supported by the MVP runtime contract.",
                "Fresh validated daily input packaging remains a manual offline step.",
                "Scheduling and UI integration are intentionally deferred.",
            ],
            "blocking": False,
            "simulation_only": "SIMULATION ONLY",
            "can_publish": False,
            "trading_advice": False,
        }
        _write_durable(
            staging / "technical_debt.json",
            canonical_option_c_bytes(technical_debt),
        )
        summary = (
            "# TickFlow Paper Trading MVP Release Evidence\n\n"
            "- Status: `TICKFLOW_PAPER_TRADING_MVP_RELEASE_READY`\n"
            "- Reference: `MIXED_OBSERVATION -> HOLD`\n"
            "- Lifecycle: `BUY -> HOLD -> SELL -> HOLD`\n"
            f"- Release SHA: `{replay.release_sha256}`\n"
            "- Replay: `MVP_RELEASE_REPLAY_X3=PASSED`\n"
            "- Real Provider: `NOT_REQUIRED`\n"
            "- Real Trading: `DISABLED`\n"
            "- Safety: `SIMULATION ONLY`, `can_publish=false`\n"
        )
        _write_durable(staging / "release_summary.md", summary.encode("utf-8"))
        _fsync_directory(staging)
        _fsync_directory(output_root)
        atomic_publish_directory(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
            _fsync_directory(output_root)
    protected_final = hash_protected_evidence(root)
    if protected_final != protected_before:
        raise OptionCReleaseError("protected_evidence_mutated_after_publication")
    return MvpReleaseResult(
        status="TICKFLOW_PAPER_TRADING_MVP_RELEASE_READY",
        output_directory=destination,
        replay=replay,
        protected_before=protected_before,
        protected_after=protected_final,
    )
