from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_minimal_ark_approval import (
    MinimalArkApprovalError,
    build_historical_evidence_manifest,
    build_minimal_candidate,
    build_minimal_scope,
    run_minimal_mock_e2e,
    validate_installed_approval,
    validate_minimal_candidate,
    validate_minimal_scope,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _sha(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _candidate_inputs(tmp_path: Path) -> tuple[dict, str, str]:
    manifest = build_historical_evidence_manifest(REPO_ROOT)
    mock = {
        "mock_e2e_version": 1,
        "status": "MOCK_E2E_PASSED",
        "real_provider_attempts": 0,
        "real_ai_calls": 0,
    }
    manifest_sha = _sha(manifest)
    mock_sha = _sha(mock)
    candidate = build_minimal_candidate(
        REPO_ROOT,
        source_git_head="a" * 40,
        historical_evidence_manifest_sha256=manifest_sha,
        mock_e2e_sha256=mock_sha,
    )
    return candidate, manifest_sha, mock_sha


def test_candidate_is_deterministic_minimal_and_fully_bound(tmp_path: Path) -> None:
    candidate, manifest_sha, mock_sha = _candidate_inputs(tmp_path)
    repeated, _, _ = _candidate_inputs(tmp_path)

    assert canonical_json_bytes(candidate) == canonical_json_bytes(repeated)
    assert candidate["provider_id"] == "volcengine_ark"
    assert candidate["exact_model_id"] == "doubao-seed-2-1-turbo-260628"
    assert candidate["endpoint"] == (
        "https://ark.cn-beijing.volces.com/api/v3/responses"
    )
    assert candidate["symbol"] == "000403.SZ"
    assert candidate["trade_date"] == "2026-07-31"
    assert candidate["execution_contract"] == {
        "can_publish": False,
        "maximum_attempts": 1,
        "retry_count": 0,
        "store": False,
        "stream": False,
        "strict_json_schema": True,
        "tools": [],
    }
    assert candidate["artifact_hashes"]["facts_sha256"] == (
        "adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956"
    )
    assert candidate["artifact_hashes"]["projection_sha256"] == (
        "0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f"
    )
    assert candidate["artifact_hashes"]["historical_evidence_manifest_sha256"] == (
        manifest_sha
    )
    assert candidate["artifact_hashes"]["mock_e2e_sha256"] == mock_sha
    assert set(candidate["source_bindings"]) == {
        "ark_adapter",
        "claims_renderer",
        "claims_schema",
        "claims_validator",
        "minimal_approval",
        "minimal_builder",
        "minimal_launcher",
        "minimal_service",
        "request_contract",
        "worker_protocol",
    }
    serialized = json.dumps(candidate, sort_keys=True)
    for forbidden in (
        '"proxy"',
        '"relay"',
        '"tls_probe"',
        '"network_attachment"',
        '"dispatch_gate"',
        '"docker"',
        '"build_provenance"',
    ):
        assert forbidden not in serialized
    assert validate_minimal_candidate(
        candidate,
        REPO_ROOT,
        expected_git_head="a" * 40,
        historical_evidence_manifest_sha256=manifest_sha,
        mock_e2e_sha256=mock_sha,
    ) == []


def test_candidate_mutation_is_rejected(tmp_path: Path) -> None:
    candidate, manifest_sha, mock_sha = _candidate_inputs(tmp_path)
    candidate["execution_contract"]["retry_count"] = 1

    errors = validate_minimal_candidate(
        candidate,
        REPO_ROOT,
        expected_git_head="a" * 40,
        historical_evidence_manifest_sha256=manifest_sha,
        mock_e2e_sha256=mock_sha,
    )

    assert "candidate_schema_invalid" in errors


def test_new_scope_is_deterministic_available_and_attempt_free(tmp_path: Path) -> None:
    candidate, _, _ = _candidate_inputs(tmp_path)
    candidate_sha = _sha(candidate)
    attempts_root = tmp_path / "attempts"

    first = build_minimal_scope(candidate_sha, attempts_root)
    second = build_minimal_scope(candidate_sha, attempts_root)

    assert first == second
    assert first["availability"] == "AVAILABLE"
    assert first["historical_attempts"] == 0
    assert first["maximum_attempts"] == 1
    assert first["retry_count"] == 0
    assert validate_minimal_scope(first, candidate_sha, attempts_root) == []

    (attempts_root / first["scope_id"]).mkdir(parents=True)
    blocked = build_minimal_scope(candidate_sha, attempts_root)
    assert blocked["availability"] == "BLOCKED"
    assert blocked["historical_attempts"] == 1


def test_mock_e2e_runs_real_validators_three_times_without_keychain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_keychain_read(*_args, **_kwargs):
        raise AssertionError("Keychain must not be read by offline Mock E2E")

    monkeypatch.setattr(
        "app.services.phase2_minimal_ark_canary.read_minimal_ark_keychain_secret_once",
        forbidden_keychain_read,
    )
    runtime_root = tmp_path / "mock-runtime"

    evidence = run_minimal_mock_e2e(REPO_ROOT, runtime_root)

    assert evidence["status"] == "MOCK_E2E_PASSED"
    assert evidence["happy_path_runs"] == 3
    assert evidence["rendered_sha256_unique_count"] == 1
    assert all(run["provider_attempt_count"] == 1 for run in evidence["happy_path"])
    assert all(run["status"] == "SUCCEEDED" for run in evidence["happy_path"])
    assert {case["status"] for case in evidence["failure_cases"]} == {
        "HTTP_ERROR",
        "TIMEOUT",
        "CANDIDATE_REJECTED",
        "CLAIMS_REJECTED",
    }
    assert all(case["provider_attempt_count"] == 1 for case in evidence["failure_cases"])
    assert evidence["total_mock_attempts"] == 7
    assert evidence["real_provider_attempts"] == 0
    assert evidence["real_ai_calls"] == 0
    assert evidence["keychain_secret_read"] is False
    assert evidence["secret_hits"] == 0
    assert evidence["temporary_residue"] == 0
    assert not runtime_root.exists()


def test_historical_manifest_is_complete_deterministic_and_regular() -> None:
    first = build_historical_evidence_manifest(REPO_ROOT)
    second = build_historical_evidence_manifest(REPO_ROOT)

    assert first == second
    assert first["status"] == "FROZEN_PRESERVED"
    assert first["file_count"] > 40
    assert len(first["files"]) == first["file_count"]
    assert all(item["sha256"] for item in first["files"])
    assert all(not Path(item["path"]).is_absolute() for item in first["files"])


def test_private_approval_validation_never_returns_secret_material(
    tmp_path: Path,
) -> None:
    candidate, _, _ = _candidate_inputs(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_bytes(canonical_json_bytes(candidate))
    candidate_sha = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    scope = build_minimal_scope(candidate_sha, tmp_path / "attempts")
    scope_path = tmp_path / "scope.json"
    scope_path.write_bytes(canonical_json_bytes(scope))
    scope_sha = hashlib.sha256(scope_path.read_bytes()).hexdigest()
    approval_dir = tmp_path / "approval"
    approval_dir.mkdir(mode=0o700)
    approval_path = approval_dir / "approval.json"
    approval_path.write_bytes(
        canonical_json_bytes(
            {
                "approval_version": 1,
                "approval_status": "APPROVED",
                "approval_candidate_sha256": candidate_sha,
                "approval_scope_sha256": scope_sha,
                "approval_scope_id": scope["scope_id"],
            }
        )
    )
    os.chmod(approval_path, 0o600)

    validated = validate_installed_approval(
        approval_path,
        candidate_path,
        scope_path,
    )

    assert validated == {
        "approval_candidate_sha256": candidate_sha,
        "approval_scope_id": scope["scope_id"],
        "approval_scope_sha256": scope_sha,
        "approval_status": "APPROVED",
        "approval_version": 1,
    }
    assert stat.S_IMODE(os.lstat(approval_path).st_mode) == 0o600

    os.chmod(approval_path, 0o644)
    with pytest.raises(MinimalArkApprovalError, match="approval_file_invalid"):
        validate_installed_approval(approval_path, candidate_path, scope_path)


def test_future_launcher_help_is_offline_and_loadable() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase2_minimal_ark_canary.py",
            "--help",
        ],
        cwd=REPO_ROOT / "backend",
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "--execute" in result.stdout
    assert "--request-id" in result.stdout


def test_future_launcher_preflight_does_not_read_secret_or_execute(
    tmp_path: Path,
) -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = build_historical_evidence_manifest(REPO_ROOT)
    manifest_sha = _sha(manifest)
    mock_sha = _sha(
        {
            "mock_e2e_version": 1,
            "status": "MOCK_E2E_PASSED",
            "real_provider_attempts": 0,
            "real_ai_calls": 0,
        }
    )
    candidate = build_minimal_candidate(
        REPO_ROOT,
        source_git_head=head,
        historical_evidence_manifest_sha256=manifest_sha,
        mock_e2e_sha256=mock_sha,
    )
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_bytes(canonical_json_bytes(candidate))
    candidate_sha = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    attempts_root = tmp_path / "attempts"
    scope = build_minimal_scope(candidate_sha, attempts_root)
    scope_path = tmp_path / "scope.json"
    scope_path.write_bytes(canonical_json_bytes(scope))
    approval_dir = tmp_path / "approval"
    approval_dir.mkdir(mode=0o700)
    approval_path = approval_dir / "approval.json"
    approval_path.write_bytes(
        canonical_json_bytes(
            {
                "approval_version": 1,
                "approval_status": "APPROVED",
                "approval_candidate_sha256": candidate_sha,
                "approval_scope_sha256": hashlib.sha256(
                    scope_path.read_bytes()
                ).hexdigest(),
                "approval_scope_id": scope["scope_id"],
            }
        )
    )
    os.chmod(approval_path, 0o600)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase2_minimal_ark_canary.py",
            "--repo-root",
            str(REPO_ROOT),
            "--candidate",
            str(candidate_path),
            "--scope",
            str(scope_path),
            "--approval",
            str(approval_path),
            "--attempts-root",
            str(attempts_root),
            "--lock",
            str(tmp_path / "runtime/lock"),
        ],
        cwd=REPO_ROOT / "backend",
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "provider_http": "NOT_RUN",
        "secret_content_read": False,
        "status": "PREFLIGHT_PASSED",
    }
    assert not attempts_root.exists()
