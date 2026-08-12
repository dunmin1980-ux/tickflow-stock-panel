from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.providers.ark_contract import (
    build_ark_approval_candidate_bytes,
    build_ark_approval_scope_evidence,
)
from app.services.phase2_canary_orchestrator import (
    ApprovalScopedLedgerNamespace,
    ApprovalScopeIdentity,
    compute_approval_scope_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ark_approval_scope_is_provider_specific() -> None:
    candidate = "1" * 64
    ark = ApprovalScopeIdentity(
        approval_candidate_sha256=candidate,
        symbol="000403.SZ",
        provider_id="volcengine_ark",
        exact_model_id="doubao-seed-2-1-turbo-260628",
        endpoint_alias="ark_responses_cn_beijing_v1",
    )
    openai = ApprovalScopeIdentity(
        approval_candidate_sha256=candidate,
        symbol="000403.SZ",
        provider_id="openai",
        exact_model_id="gpt-5.6-terra",
        endpoint_alias="openai_responses_v1",
    )
    assert compute_approval_scope_id(ark) != compute_approval_scope_id(openai)


def test_ark_scope_changes_with_candidate_hash() -> None:
    values = [
        compute_approval_scope_id(
            ApprovalScopeIdentity(
                approval_candidate_sha256=character * 64,
                symbol="000403.SZ",
                provider_id="volcengine_ark",
                exact_model_id="doubao-seed-2-1-turbo-260628",
                endpoint_alias="ark_responses_cn_beijing_v1",
            )
        )
        for character in "123"
    ]
    assert len(set(values)) == 3


def test_ark_approval_candidate_is_deterministic_and_not_installed() -> None:
    arguments = {
        "proxy_image_id": "sha256:" + "1" * 64,
        "relay_image_id": "sha256:" + "2" * 64,
    }
    first = build_ark_approval_candidate_bytes(REPO_ROOT, **arguments)
    second = build_ark_approval_candidate_bytes(REPO_ROOT, **arguments)
    assert first == second
    assert b'"approval_installed": false' in first
    assert b'"provider_attempt_count": 0' in first
    assert b'"ai_call_count": 0' in first


def test_ark_scope_evidence_uses_final_candidate_file_hash() -> None:
    arguments = {
        "proxy_image_id": "sha256:" + "1" * 64,
        "relay_image_id": "sha256:" + "2" * 64,
    }
    candidate = build_ark_approval_candidate_bytes(REPO_ROOT, **arguments)
    evidence = build_ark_approval_scope_evidence(
        candidate,
        mock_e2e_sha256="a" * 64,
    )
    expected_candidate_sha256 = hashlib.sha256(candidate).hexdigest()
    assert evidence["approval_candidate_sha256"] == expected_candidate_sha256
    assert evidence["approval_scope_id"] == compute_approval_scope_id(
        ApprovalScopeIdentity(
            approval_candidate_sha256=expected_candidate_sha256,
            symbol="000403.SZ",
            provider_id="volcengine_ark",
            exact_model_id="doubao-seed-2-1-turbo-260628",
            endpoint_alias="ark_responses_cn_beijing_v1",
        )
    )
    assert evidence["historical_attempts"] == 0
    assert evidence["attempt_availability"] == "AVAILABLE"
    assert evidence["approval_installed"] is False
    assert evidence["ledger_preflight_status"] == "READY"
    assert evidence["global_request_id_count"] == 0
    assert evidence["mock_e2e_sha256"] == "a" * 64
    assert "scope_id_seed_sha256" not in json.dumps(evidence)


@pytest.mark.parametrize("value", [None, "", "A" * 64, "a" * 63, "z" * 64])
def test_ark_scope_rejects_missing_or_invalid_mock_evidence_hash(value) -> None:
    candidate = build_ark_approval_candidate_bytes(
        REPO_ROOT,
        proxy_image_id="sha256:" + "1" * 64,
        relay_image_id="sha256:" + "2" * 64,
    )
    with pytest.raises(ValueError, match="ark_mock_e2e_sha256_invalid"):
        build_ark_approval_scope_evidence(
            candidate,
            mock_e2e_sha256=value,
        )


def test_historical_ark_candidate_identity_is_resolvable(tmp_path: Path) -> None:
    current = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json").read_text()
    )
    raw = build_ark_approval_candidate_bytes(
        REPO_ROOT,
        proxy_image_id=current["proxy_image_id"],
        relay_image_id=current["relay_image_id"],
    )
    root = tmp_path / "candidates"
    root.mkdir(mode=0o700)
    target = root / "approval_candidate.json"
    target.write_bytes(raw)
    namespace = object.__new__(ApprovalScopedLedgerNamespace)
    namespace.candidate_roots = (root,)

    identity = namespace._candidate_identity(hashlib.sha256(raw).hexdigest())

    assert identity.provider_id == "volcengine_ark"
    assert identity.exact_model_id == "doubao-seed-2-1-turbo-260628"
    assert identity.endpoint_alias == "ark_responses_cn_beijing_v1"
