from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.providers.ark_contract import (
    build_ark_approval_candidate,
    build_ark_responses_contract,
    validate_ark_approval_candidate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_committed_ark_contract_is_canonical_and_current() -> None:
    path = REPO_ROOT / "docker/phase2-ark-egress-proxy/responses-contract.json"
    observed = json.loads(path.read_text(encoding="utf-8"))
    assert observed == build_ark_responses_contract(REPO_ROOT)
    assert observed["policy"]["provider_id"] == "volcengine_ark"
    assert observed["response_format"]["type"] == "json_schema"
    assert observed["response_format"]["strict"] is True


def test_committed_ark_candidate_binds_all_required_hashes() -> None:
    path = REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
    observed = json.loads(path.read_text(encoding="utf-8"))
    assert observed == build_ark_approval_candidate(
        REPO_ROOT,
        proxy_image_id=observed["proxy_image_id"],
        relay_image_id=observed["relay_image_id"],
    )
    required = {
        "ark_responses_contract_sha256",
        "ark_proxy_policy_sha256",
        "ark_adapter_source_sha256",
        "ark_launcher_source_sha256",
        "orchestrator_source_sha256",
        "runtime_contract_sha256",
        "readiness_contract_sha256",
        "facts_sha256",
        "projection_sha256",
        "typed_claims_schema_sha256",
    }
    assert set(observed["artifact_hashes"]) == required
    assert len(hashlib.sha256(path.read_bytes()).hexdigest()) == 64
    assert "approval_scope" not in observed


def test_ark_candidate_validator_rejects_any_field_tampering() -> None:
    observed = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json").read_text()
    )
    observed["provider_attempt_count"] = 1
    with pytest.raises(ValueError, match="ark_approval_candidate_invalid"):
        validate_ark_approval_candidate(REPO_ROOT, observed)


def test_committed_ark_scope_binds_final_candidate_bytes() -> None:
    candidate_path = REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
    scope_path = REPO_ROOT / "reports/phase2_provider_ark/approval_scope_preflight.json"
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    assert (
        scope["approval_candidate_sha256"]
        == hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    )
    assert scope["provider_id"] == "volcengine_ark"
    assert scope["exact_model_id"] == "doubao-seed-2-1-turbo-260628"
    assert scope["endpoint_alias"] == "ark_responses_cn_beijing_v1"
    assert scope["historical_attempts"] == 0
    assert scope["attempt_availability"] == "AVAILABLE"
    assert scope["approval_installed"] is False


def test_ark_proxy_derivation_check_is_cwd_independent() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend/scripts/prepare_phase2_ark_proxy_source.py"),
        ],
        cwd=REPO_ROOT / "backend",
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_committed_candidate_uses_the_mock_validated_relay_image() -> None:
    candidate = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json").read_text()
    )
    mock = json.loads((REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_text())
    assert candidate["proxy_image_id"] == mock["proxy_image_id"]
    assert candidate["relay_image_id"] == mock["relay_image_id"]
    assert (
        mock["orchestrator_source_sha256"]
        == candidate["artifact_hashes"]["orchestrator_source_sha256"]
    )
    assert (
        mock["runtime_contract_sha256"] == candidate["artifact_hashes"]["runtime_contract_sha256"]
    )
    assert mock["facts_sha256"] == candidate["artifact_hashes"]["facts_sha256"]
    assert mock["projection_sha256"] == candidate["artifact_hashes"]["projection_sha256"]
    scope = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/approval_scope_preflight.json").read_text()
    )
    assert (
        scope["approval_candidate_sha256"]
        == hashlib.sha256(
            (REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json").read_bytes()
        ).hexdigest()
    )
    assert (
        scope["mock_e2e_sha256"]
        == hashlib.sha256(
            (REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_bytes()
        ).hexdigest()
    )
