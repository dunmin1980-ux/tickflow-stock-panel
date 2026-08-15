#!/usr/bin/env python3
"""Future one-shot launcher for an explicitly approved minimal Ark Canary."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_minimal_ark_approval import (
    MinimalArkApprovalError,
    load_minimal_artifact,
    validate_installed_approval,
    validate_minimal_candidate,
    validate_minimal_scope,
)
from app.services.phase2_minimal_ark_canary import (
    ArkHostTransport,
    execute_minimal_ark_canary,
    read_minimal_ark_keychain_secret_once,
)

_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_BRANCH = "codex/tickflow-phase2-ai-review"


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise MinimalArkApprovalError("runtime_git_state_invalid")
    return result.stdout.strip()


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    if path.parent.is_symlink() or path.is_symlink():
        raise MinimalArkApprovalError("global_lock_path_invalid")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = os.lstat(path.parent)
    if not stat.S_ISDIR(metadata.st_mode):
        raise MinimalArkApprovalError("global_lock_path_invalid")
    os.chmod(path.parent, 0o700, follow_symlinks=False)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise MinimalArkApprovalError("global_lock_unavailable") from exc
    finally:
        os.close(descriptor)


def _preflight(
    repo_root: Path,
    candidate_path: Path,
    scope_path: Path,
    approval_path: Path,
    attempts_root: Path,
) -> tuple[dict, dict, dict]:
    approval = validate_installed_approval(
        approval_path, candidate_path, scope_path
    )
    candidate = load_minimal_artifact(candidate_path)
    scope = load_minimal_artifact(scope_path)
    current_head = _git(repo_root, "rev-parse", "HEAD")
    remote_head = _git(repo_root, "rev-parse", f"fork/{_BRANCH}")
    worktree_status = _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if (
        current_head != candidate.get("source_git_head")
        or remote_head != current_head
        or worktree_status
    ):
        raise MinimalArkApprovalError("runtime_git_head_mismatch")
    if (
        hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()
        != approval["approval_candidate_sha256"]
        or hashlib.sha256(canonical_json_bytes(scope)).hexdigest()
        != approval["approval_scope_sha256"]
    ):
        raise MinimalArkApprovalError("runtime_artifact_hash_mismatch")
    artifacts = candidate.get("artifact_hashes", {})
    errors = validate_minimal_candidate(
        candidate,
        repo_root,
        expected_git_head=current_head,
        historical_evidence_manifest_sha256=artifacts.get(
            "historical_evidence_manifest_sha256", ""
        ),
        mock_e2e_sha256=artifacts.get("mock_e2e_sha256", ""),
    )
    candidate_sha = approval["approval_candidate_sha256"]
    errors.extend(validate_minimal_scope(scope, candidate_sha, attempts_root))
    if errors:
        raise MinimalArkApprovalError("runtime_preflight_failed")
    return approval, candidate, scope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--attempts-root", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--request-id")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        approval, _candidate, scope = _preflight(
            args.repo_root.resolve(),
            args.candidate.expanduser().absolute(),
            args.scope.expanduser().absolute(),
            args.approval.expanduser().absolute(),
            args.attempts_root.expanduser().absolute(),
        )
        if not args.execute:
            print(
                json.dumps(
                    {
                        "status": "PREFLIGHT_PASSED",
                        "provider_http": "NOT_RUN",
                        "secret_content_read": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if not isinstance(args.request_id, str) or _REQUEST_ID.fullmatch(
            args.request_id
        ) is None:
            raise MinimalArkApprovalError("request_id_invalid")
        with _exclusive_lock(args.lock.expanduser().absolute()):
            result = execute_minimal_ark_canary(
                repo_root=args.repo_root.resolve(),
                attempts_root=args.attempts_root.expanduser().absolute(),
                scope_id=scope["scope_id"],
                candidate_sha256=approval["approval_candidate_sha256"],
                request_id=args.request_id,
                transport=ArkHostTransport(),
                secret_reader=read_minimal_ark_keychain_secret_once,
                clock=lambda: datetime.now(UTC).isoformat(),
            )
        print(
            json.dumps(
                {
                    "status": result.status,
                    "attempt_count": result.attempt_count,
                    "provider_http_status": result.provider_http_status,
                    "candidate_validation_state": result.candidate_validation_state,
                    "claims_validation_state": result.claims_validation_state,
                    "renderer_validation_state": result.renderer_validation_state,
                    "can_publish": False,
                },
                sort_keys=True,
            )
        )
        return 0 if result.status == "SUCCEEDED" else 1
    except (MinimalArkApprovalError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
