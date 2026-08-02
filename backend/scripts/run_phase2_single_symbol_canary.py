"""Run the one approved Phase 2B single-symbol Canary exactly once."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_canary_orchestrator import (
    CanaryOrchestratorConfig,
    DockerCanaryBackend,
    load_installed_runtime_approval,
    read_keychain_secret_once,
    run_single_symbol_canary,
)
from app.services.phase2_canary_runtime_artifact import (
    verify_runtime_artifact_candidate,
)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError("runtime_directory_symlink_forbidden")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def main(argv: list[str] | None = None) -> int:
    if argv not in (None, []):
        return 2
    repo_root = Path(__file__).resolve().parents[2]
    app_support = Path.home() / "Library/Application Support/TickFlowPhase2Canary"
    candidate_path = (
        repo_root
        / "reports/phase2_provider_canary/runtime_contract_candidate.json"
    )
    approval_path = app_support / "runtime-contract-approval.json"
    state_root = _private_directory(app_support / "runtime-v1")
    work_root = _private_directory(app_support / "work-v1")
    canary_output = _private_directory(
        repo_root / "reports/phase2_provider_canary/live_canary"
    )
    facts_path = repo_root / "reports/phase2_facts/000403SZ_facts.json"

    artifacts = load_installed_runtime_approval(
        candidate_path=candidate_path,
        approval_path=approval_path,
    )
    facts_bytes = facts_path.read_bytes()
    facts = json.loads(facts_bytes)
    projection = build_worker_projection(
        facts,
        hashlib.sha256(facts_bytes).hexdigest(),
        facts_bytes=facts_bytes,
    )
    config = CanaryOrchestratorConfig(
        repo_root=repo_root,
        state_root=state_root,
        work_root=work_root,
        canary_output_root=canary_output,
        facts_path=facts_path,
    )

    result = run_single_symbol_canary(
        config=config,
        artifacts=artifacts,
        projection=projection,
        backend=DockerCanaryBackend(),
        artifact_verifier=lambda approved: verify_runtime_artifact_candidate(
            repo_root,
            candidate_path,
            expected_candidate_sha256=approved.approval_candidate_sha256,
        ),
        secret_reader=read_keychain_secret_once,
        wall_clock=_timestamp,
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
