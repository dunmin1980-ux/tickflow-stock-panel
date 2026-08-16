"""Atomic offline publication for deterministic Option C artifacts."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.schemas.phase2_option_c import canonical_option_c_bytes
from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_chenquant_daily import (
    ReplayValidation,
    run_reference_simulation,
    validate_replay,
)
from app.services.phase2_option_c_fixture import build_reference_artifacts

PROTECTED_PATHS = (
    "reports/phase1_observation",
    "reports/phase1_tickflow_request_audit.json",
    "reports/phase2_facts",
    "reports/phase2_claims",
    "reports/phase2_provider_canary",
    "reports/phase2_provider_ark",
    "reports/phase2_provider_relay",
    "reports/phase2_isolation_runtime",
    "reports/phase2_obsidian_preview",
)
OUTPUT_DIRECTORY = "phase2_option_c"


class OptionCDeliveryError(ValueError):
    """Raised when Option C evidence cannot be published safely."""


@dataclass(frozen=True)
class EvidenceSnapshot:
    file_count: int
    aggregate_sha256: str
    file_sha256: dict[str, str]


@dataclass(frozen=True)
class DeliveryResult:
    status: Literal["PHASE2B_OPTION_C_PAPER_TRADING_READY"]
    output_directory: Path
    artifact_sha256: dict[str, str]
    replay: ReplayValidation
    protected_before: EvidenceSnapshot
    protected_after: EvidenceSnapshot


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _regular_file_hashes(repo_root: Path, relative: str) -> dict[str, str]:
    root = repo_root.resolve(strict=True)
    path = root / relative
    if path.is_symlink():
        raise OptionCDeliveryError(f"protected_path_symlink:{relative}")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise OptionCDeliveryError(f"protected_path_invalid:{relative}") from exc
    if resolved.is_file():
        return {relative: _sha256(resolved.read_bytes())}
    if not resolved.is_dir():
        raise OptionCDeliveryError(f"protected_path_invalid:{relative}")
    hashes: dict[str, str] = {}
    for entry in sorted(resolved.rglob("*")):
        entry_relative = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            raise OptionCDeliveryError(f"protected_path_symlink:{entry_relative}")
        if entry.is_file():
            hashes[entry_relative] = _sha256(entry.read_bytes())
        elif not entry.is_dir():
            raise OptionCDeliveryError(f"protected_entry_invalid:{entry_relative}")
    return hashes


def hash_protected_evidence(repo_root: Path) -> EvidenceSnapshot:
    """Hash all frozen source and provider evidence without modifying it."""
    hashes: dict[str, str] = {}
    for relative in PROTECTED_PATHS:
        hashes.update(_regular_file_hashes(repo_root, relative))
    ordered = dict(sorted(hashes.items()))
    return EvidenceSnapshot(
        file_count=len(ordered),
        aggregate_sha256=_sha256(canonical_option_c_bytes(ordered)),
        file_sha256=ordered,
    )


def _resolve_reports_root(
    repo_root: Path,
    reports_root: Path,
    *,
    allow_test_output_root: bool,
) -> Path:
    root = repo_root.resolve(strict=True)
    if reports_root.is_symlink():
        raise OptionCDeliveryError("reports_root_must_not_be_symlink")
    try:
        resolved = reports_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise OptionCDeliveryError("reports_root_invalid") from exc
    if not resolved.is_dir():
        raise OptionCDeliveryError("reports_root_invalid")
    if not allow_test_output_root and resolved != (root / "reports").resolve(strict=True):
        raise OptionCDeliveryError("reports_root_must_be_repository_reports")
    return resolved


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


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _build_artifacts(
    repo_root: Path,
    protected_before: EvidenceSnapshot,
) -> tuple[dict[str, bytes], ReplayValidation, EvidenceSnapshot]:
    runs = [run_reference_simulation(repo_root) for _ in range(3)]
    replay = validate_replay(runs)
    if replay.status != "DETERMINISTIC_REPLAY_PASSED":
        raise OptionCDeliveryError("deterministic_replay_failed")
    artifacts = {
        **build_reference_artifacts(runs[0].bundle),
        **runs[0].artifacts,
        "replay_validation.json": canonical_option_c_bytes(replay),
    }
    protected_after = hash_protected_evidence(repo_root)
    if protected_after != protected_before:
        raise OptionCDeliveryError("protected_evidence_mutated")
    artifact_hashes = {
        name: _sha256(raw) for name, raw in sorted(artifacts.items())
    }
    evidence_index = {
        "evidence_index_version": 1,
        "status": "PHASE2B_OPTION_C_PAPER_TRADING_READY",
        "option_a": "EXHAUSTED",
        "option_c": "ACTIVE",
        "real_provider_integration": "DEFERRED_FROZEN",
        "ark_minimal_path": "FROZEN",
        "paper_trading_mode": "SIMULATION_ONLY",
        "single_symbol": "000403.SZ",
        "business_action": "HOLD",
        "replay_status": replay.status,
        "replay_count": replay.replay_count,
        "protected_evidence_file_count": protected_before.file_count,
        "protected_evidence_sha256_before": protected_before.aggregate_sha256,
        "protected_evidence_sha256_after": protected_after.aggregate_sha256,
        "protected_evidence_unchanged": True,
        "artifact_sha256": artifact_hashes,
        "tickflow_api_requests": 0,
        "real_provider_attempts": 0,
        "real_ai_calls": 0,
        "broker_calls": 0,
        "cloud_deployments": 0,
        "real_trading": "DISABLED",
        "simulation_only": "SIMULATION ONLY",
        "can_publish": False,
        "trading_advice": False,
    }
    artifacts["evidence_index.json"] = canonical_option_c_bytes(evidence_index)
    return artifacts, replay, protected_after


def publish_option_c_run(
    repo_root: Path,
    reports_root: Path,
    *,
    _allow_test_output_root: bool = False,
) -> DeliveryResult:
    """Build, verify, and atomically publish the closed Option C artifact tree."""
    root = repo_root.resolve(strict=True)
    output_root = _resolve_reports_root(
        root,
        reports_root,
        allow_test_output_root=_allow_test_output_root,
    )
    destination = output_root / OUTPUT_DIRECTORY
    if destination.is_symlink():
        raise OptionCDeliveryError("output_directory_must_not_be_symlink")
    protected_before = hash_protected_evidence(root)
    artifacts, replay, protected_after = _build_artifacts(root, protected_before)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{OUTPUT_DIRECTORY}.staging-", dir=output_root)
    )
    staging.chmod(0o700)
    try:
        for name, raw in sorted(artifacts.items()):
            _write_durable(staging / name, raw)
        _fsync_directory(staging)
        _fsync_directory(output_root)
        atomic_publish_directory(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
            _fsync_directory(output_root)
    final_hashes = {
        path.name: _sha256(path.read_bytes())
        for path in sorted(destination.iterdir())
        if path.is_file() and not path.is_symlink()
    }
    if set(final_hashes) != set(artifacts) or any(
        final_hashes[name] != _sha256(raw) for name, raw in artifacts.items()
    ):
        raise OptionCDeliveryError("published_artifact_hash_mismatch")
    if hash_protected_evidence(root) != protected_after:
        raise OptionCDeliveryError("protected_evidence_mutated_after_publication")
    return DeliveryResult(
        status="PHASE2B_OPTION_C_PAPER_TRADING_READY",
        output_directory=destination,
        artifact_sha256=dict(sorted(final_hashes.items())),
        replay=replay,
        protected_before=protected_before,
        protected_after=protected_after,
    )
