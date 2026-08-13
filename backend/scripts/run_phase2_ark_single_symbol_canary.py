#!/usr/bin/env python3
"""Run one separately approved Ark single-symbol Canary exactly once."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.providers.ark_contract import (
    ApprovedArkCandidateSnapshot,
    load_installed_ark_approval,
    validate_ark_approval_candidate,
)
from app.providers.ark_provider import (
    ARK_ENDPOINT_ALIAS,
    ARK_EXACT_MODEL_ID,
    ARK_PROVIDER_ID,
)
from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_canary_orchestrator import (
    ApprovedCanaryArtifacts,
    ArkDockerCanaryBackend,
    CanaryOrchestratorConfig,
    read_ark_keychain_secret_once,
    run_single_symbol_canary,
)

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_HISTORICAL_CATEGORIES = {"evidence", "inbox", "receipts", "rejected"}
_RECEIPT_FILES = {
    "archive-status.json",
    "child-metadata.json",
    "proxy-receipt.json",
    "relay-receipt.json",
}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError("runtime_directory_symlink_forbidden")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _directory_without_symlink(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RuntimeError("committed_runtime_mode_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("committed_runtime_mode_invalid")
    path.chmod(0o700)


def _regular_without_symlink(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RuntimeError("committed_runtime_mode_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("committed_runtime_mode_invalid")
    path.chmod(0o600)


def normalize_committed_runtime_modes(repo_root: Path) -> None:
    """Restore private modes Git cannot preserve, without touching bytes."""
    root = repo_root.resolve(strict=True)
    attempts = root / "reports/phase2_provider_canary/attempts"
    _directory_without_symlink(attempts)
    for scope in sorted(attempts.iterdir(), key=lambda item: item.name):
        if _HEX_64.fullmatch(scope.name) is None:
            raise RuntimeError("committed_runtime_mode_invalid")
        _directory_without_symlink(scope)
        for request in sorted(scope.iterdir(), key=lambda item: item.name):
            if _HEX_32.fullmatch(request.name) is None:
                raise RuntimeError("committed_runtime_mode_invalid")
            _directory_without_symlink(request)
            if {entry.name for entry in request.iterdir()} != {"ledger.json"}:
                raise RuntimeError("committed_runtime_mode_invalid")
            _regular_without_symlink(request / "ledger.json")

    history = root / "reports/phase2_provider_ark/live_canary"
    _directory_without_symlink(history)
    for category in sorted(history.iterdir(), key=lambda item: item.name):
        if category.name not in _HISTORICAL_CATEGORIES:
            raise RuntimeError("committed_runtime_mode_invalid")
        _directory_without_symlink(category)
        if category.name == "receipts":
            for request in sorted(category.iterdir(), key=lambda item: item.name):
                if _HEX_32.fullmatch(request.name) is None:
                    raise RuntimeError("committed_runtime_mode_invalid")
                _directory_without_symlink(request)
                entries = tuple(request.iterdir())
                if not entries or any(
                    entry.name not in _RECEIPT_FILES for entry in entries
                ):
                    raise RuntimeError("committed_runtime_mode_invalid")
                for entry in entries:
                    _regular_without_symlink(entry)
            continue
        for entry in sorted(category.iterdir(), key=lambda item: item.name):
            if (
                _HEX_32.fullmatch(entry.stem) is None
                or entry.suffix not in ({".json", ".md"} if category.name == "inbox" else {".json"})
            ):
                raise RuntimeError("committed_runtime_mode_invalid")
            _regular_without_symlink(entry)


def _approved_artifacts(approved: ApprovedArkCandidateSnapshot) -> ApprovedCanaryArtifacts:
    candidate = approved.candidate
    artifact_hashes = candidate.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        raise ValueError("ark_approval_candidate_invalid")
    return ApprovedCanaryArtifacts(
        approval_candidate_sha256=approved.candidate_sha256,
        facts_sha256=artifact_hashes["facts_sha256"],
        projection_sha256=artifact_hashes["projection_sha256"],
        proxy_image_id=candidate["proxy_image_id"],
        relay_image_id=candidate["relay_image_id"],
        timeout_contract_sha256=artifact_hashes["runtime_contract_sha256"],
        readiness_contract_sha256=artifact_hashes["readiness_contract_sha256"],
        orchestrator_source_sha256=artifact_hashes["orchestrator_source_sha256"],
    )


def main(argv: list[str] | None = None) -> int:
    if argv not in (None, []):
        return 2
    repo_root = Path(__file__).resolve().parents[2]
    ark_support = Path.home() / "Library/Application Support/TickFlowPhase2CanaryArk"
    shared_support = Path.home() / "Library/Application Support/TickFlowPhase2Canary"
    candidate_path = repo_root / "reports/phase2_provider_ark/approval_candidate.json"
    approval_path = ark_support / "runtime-approval.json"

    normalize_committed_runtime_modes(repo_root)

    try:
        approved = load_installed_ark_approval(candidate_path, approval_path)
        validate_ark_approval_candidate(repo_root, approved.candidate)
        artifacts = _approved_artifacts(approved)
    except (KeyError, OSError, TypeError, ValueError):
        return 2

    facts_path = repo_root / "reports/phase2_facts/000403SZ_facts.json"
    facts_bytes = facts_path.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_bytes),
        hashlib.sha256(facts_bytes).hexdigest(),
        facts_bytes=facts_bytes,
    )
    output_root = _private_directory(repo_root / "reports/phase2_provider_ark/live_canary")
    attempts_root = _private_directory(repo_root / "reports/phase2_provider_canary/attempts")
    config = CanaryOrchestratorConfig(
        repo_root=repo_root,
        state_root=_private_directory(shared_support / "runtime-v1"),
        attempts_root=attempts_root,
        work_root=_private_directory(ark_support / "work-v1"),
        canary_output_root=output_root,
        historical_evidence_root=_private_directory(
            repo_root / "reports/phase2_provider_canary/live_canary"
        ),
        additional_historical_evidence_roots=(output_root,),
        candidate_roots=(
            repo_root / "reports/phase2_provider_canary/superseded",
            repo_root / "reports/phase2_provider_canary",
            repo_root / "reports/phase2_provider_ark",
        ),
        facts_path=facts_path,
    )

    result = run_single_symbol_canary(
        config=config,
        artifacts=artifacts,
        projection=projection,
        backend=ArkDockerCanaryBackend(),
        artifact_verifier=lambda approved: (
            approved == artifacts
            or (_ for _ in ()).throw(ValueError("ark_runtime_artifact_mismatch"))
        ),
        secret_reader=read_ark_keychain_secret_once,
        wall_clock=_timestamp,
        provider_id=ARK_PROVIDER_ID,
        exact_model_id=ARK_EXACT_MODEL_ID,
        endpoint_alias=ARK_ENDPOINT_ALIAS,
    )
    print(
        json.dumps(
            asdict(result),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            default=str,
        )
    )
    return 0 if result.terminal_state == "SUCCEEDED" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
