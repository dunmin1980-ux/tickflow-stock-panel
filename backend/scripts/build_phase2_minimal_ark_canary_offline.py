#!/usr/bin/env python3
"""Build the minimal Ark Canary Candidate and Scope without external access."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_minimal_ark_approval import (
    MinimalArkApprovalError,
    build_historical_evidence_manifest,
    build_minimal_candidate,
    build_minimal_scope,
    run_minimal_mock_e2e,
    validate_minimal_candidate,
    validate_minimal_scope,
)

BRANCH = "codex/tickflow-phase2-ai-review"


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise MinimalArkApprovalError("git_preflight_failed")
    return result.stdout.strip()


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".staging", dir=path.parent
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def build_offline_artifacts(
    repo_root: Path,
    output_root: Path,
    attempts_root: Path,
) -> dict[str, Any]:
    head = _git(repo_root, "rev-parse", "HEAD")
    remote = _git(repo_root, "rev-parse", f"fork/{BRANCH}")
    if head != remote:
        raise MinimalArkApprovalError("local_remote_head_mismatch")
    if _git(repo_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise MinimalArkApprovalError("worktree_not_clean")
    manifest = build_historical_evidence_manifest(repo_root)
    mock = run_minimal_mock_e2e(repo_root, output_root / ".mock-runtime")
    if mock["status"] != "MOCK_E2E_PASSED":
        raise MinimalArkApprovalError("mock_e2e_failed")
    manifest_sha = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    mock_sha = hashlib.sha256(canonical_json_bytes(mock)).hexdigest()
    candidate = build_minimal_candidate(
        repo_root,
        source_git_head=head,
        historical_evidence_manifest_sha256=manifest_sha,
        mock_e2e_sha256=mock_sha,
    )
    candidate_errors = validate_minimal_candidate(
        candidate,
        repo_root,
        expected_git_head=head,
        historical_evidence_manifest_sha256=manifest_sha,
        mock_e2e_sha256=mock_sha,
    )
    if candidate_errors:
        raise MinimalArkApprovalError("candidate_validation_failed")
    candidate_sha = hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()
    scope = build_minimal_scope(candidate_sha, attempts_root)
    scope_errors = validate_minimal_scope(scope, candidate_sha, attempts_root)
    if scope_errors:
        raise MinimalArkApprovalError("scope_validation_failed")
    verification = {
        "verification_version": 1,
        "status": "MINIMAL_ARK_CANARY_OFFLINE_VERIFIED",
        "source_git_head": head,
        "local_fork_remote": "MATCHED",
        "worktree": "CLEAN",
        "approval_candidate_sha256": candidate_sha,
        "approval_scope_id": scope["scope_id"],
        "scope_historical_attempts": 0,
        "scope_availability": "AVAILABLE",
        "mock_e2e": "PASSED",
        "real_ark_provider_attempts": 0,
        "real_ai_calls": 0,
        "provider_http": "NOT_RUN",
        "keychain_secret_read": False,
        "can_publish": False,
    }
    for name, value in (
        ("historical_evidence_manifest.json", manifest),
        ("mock_e2e.json", mock),
        ("approval_candidate.json", candidate),
        ("approval_scope.json", scope),
        ("verification.json", verification),
    ):
        _atomic_write(output_root / name, value)
    return verification


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--attempts-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_offline_artifacts(
            args.repo_root.resolve(),
            args.output_root.resolve(),
            args.attempts_root.expanduser().resolve(),
        )
    except MinimalArkApprovalError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
