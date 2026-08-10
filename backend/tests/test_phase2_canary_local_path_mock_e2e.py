from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.phase2_ai_worker_protocol import build_worker_projection
from scripts.run_phase2_canary_local_path_mock_e2e import (
    _EXPECTED_MOCK_PROVIDER_COMMANDS,
    _HISTORICAL_FROZEN_SHA256,
    _HISTORICAL_REQUESTS,
    _MOCK_TOPOLOGY_DELTAS,
    _PRESERVED_REAL_TOPOLOGY_IDENTITIES,
    LocalMockError,
    _approved_artifacts,
    _frozen_manifest_matches,
    _historical_manifest,
    _load_mock_approved_artifacts,
    _mock_scope_candidate_sha256,
    _mock_scoped_artifacts,
    _provider_response,
    _run_pre_fix_replay,
    _topology_command_diffs_verified,
    _topology_deltas_verified,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FACTS_PATH = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"


def _projection() -> dict:
    raw = FACTS_PATH.read_bytes()
    return build_worker_projection(
        json.loads(raw),
        hashlib.sha256(raw).hexdigest(),
        facts_bytes=raw,
    )


def test_local_mock_response_is_exact_completed_responses_envelope() -> None:
    response = _provider_response(_projection())

    assert response["model"] == "gpt-5.6-terra"
    assert response["status"] == "completed"
    assert response["tools"] == []
    assert response["output"][1]["content"][0]["type"] == "output_text"
    candidate = json.loads(response["output"][1]["content"][0]["text"])
    assert candidate["symbol"] == "000403.SZ"
    assert candidate["trade_date"] == "2026-07-31"


def test_local_mock_scope_freezes_both_consumed_requests() -> None:
    assert _HISTORICAL_REQUESTS == (
        "d59766101b63450e8148541d589a90bf",
        "3d3e6adbe5b74c98ad18a2efe0fd1d0c",
    )


def test_historical_manifest_is_bound_to_frozen_hashes_and_absence_markers() -> None:
    manifest = _historical_manifest(REPO_ROOT)

    assert manifest == _HISTORICAL_FROZEN_SHA256
    assert manifest[
        "reports/phase2_provider_canary/live_canary/evidence/"
        "3d3e6adbe5b74c98ad18a2efe0fd1d0c.json"
    ] == "bcabab31b35bf8805b1474d508f1e8067c62299aa7d501425f98cb77e8b1a01e"
    assert not any(
        path.endswith("proxy-receipt.json") for path in manifest
    )
    assert _frozen_manifest_matches(manifest) is True
    assert _frozen_manifest_matches(
        {**manifest, "reports/phase2_provider_canary/live_canary/unknown.json": "0" * 64}
    ) is False


def test_local_mock_approved_artifacts_bind_readiness_contract() -> None:
    candidate_raw = b'{"candidate":"test"}\n'
    candidate = {
        "artifact_identity": {
            "facts_sha256": "1" * 64,
            "projection_sha256": "2" * 64,
            "proxy_image_id": "sha256:" + "3" * 64,
            "relay_image_id": "sha256:" + "4" * 64,
            "timeout_contract_sha256": "5" * 64,
            "readiness_contract_sha256": "6" * 64,
            "orchestrator_source_sha256": "7" * 64,
        }
    }

    artifacts = _approved_artifacts(candidate_raw, candidate)

    assert artifacts.readiness_contract_sha256 == "6" * 64


def test_mock_scope_candidates_are_deterministic_distinct_and_secret_free() -> None:
    production_candidate_sha256 = "8" * 64

    observed = tuple(
        _mock_scope_candidate_sha256(production_candidate_sha256, index)
        for index in range(1, 4)
    )

    assert len(set(observed)) == 3
    assert observed == tuple(
        _mock_scope_candidate_sha256(production_candidate_sha256, index)
        for index in range(1, 4)
    )
    assert all(value != production_candidate_sha256 for value in observed)


def test_mock_scoped_artifacts_change_only_the_approval_identity() -> None:
    candidate_raw = b'{"candidate":"test"}\n'
    candidate = {
        "artifact_identity": {
            "facts_sha256": "1" * 64,
            "projection_sha256": "2" * 64,
            "proxy_image_id": "sha256:" + "3" * 64,
            "relay_image_id": "sha256:" + "4" * 64,
            "timeout_contract_sha256": "5" * 64,
            "readiness_contract_sha256": "6" * 64,
            "orchestrator_source_sha256": "7" * 64,
        }
    }
    production = _approved_artifacts(candidate_raw, candidate)

    scoped = _mock_scoped_artifacts(production, run_index=2)

    assert scoped.approval_candidate_sha256 == _mock_scope_candidate_sha256(
        production.approval_candidate_sha256,
        2,
    )
    assert scoped.model_dump(exclude={"approval_candidate_sha256"}) == (
        production.model_dump(exclude={"approval_candidate_sha256"})
    )


def test_local_mock_requires_external_candidate_sha_before_approval_load(
    tmp_path: Path,
) -> None:
    candidate_path = (
        REPO_ROOT
        / "reports/phase2_provider_canary/runtime_contract_candidate.json"
    )

    with pytest.raises(LocalMockError, match="external_candidate_sha256_mismatch"):
        _load_mock_approved_artifacts(
            candidate_path=candidate_path,
            expected_candidate_sha256="0" * 64,
            approval_root=tmp_path / "approval",
        )


def test_local_mock_uses_real_v2_approval_loader_contract(tmp_path: Path) -> None:
    source_candidate_path = (
        REPO_ROOT
        / "reports/phase2_provider_canary/runtime_contract_candidate.json"
    )
    candidate = json.loads(source_candidate_path.read_bytes())
    candidate["runtime_candidate_schema_version"] = 2
    candidate["status"] = (
        "PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL"
    )
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    expected = hashlib.sha256(candidate_path.read_bytes()).hexdigest()

    artifacts = _load_mock_approved_artifacts(
        candidate_path=candidate_path,
        expected_candidate_sha256=expected,
        approval_root=tmp_path / "approval",
    )

    assert artifacts.approval_candidate_sha256 == expected
    assert artifacts.readiness_contract_sha256 == (
        candidate["artifact_identity"]["readiness_contract_sha256"]
    )


def test_local_mock_topology_delta_manifest_is_exactly_three_declared_changes() -> None:
    assert _MOCK_TOPOLOGY_DELTAS == (
        {
            "component": "provider_endpoint",
            "production": "api.openai.com:443",
            "mock": "internal Mock Provider with alias api.openai.com:443",
        },
        {
            "component": "proxy_egress_network",
            "production": "dedicated egress network",
            "mock": "internal-only Docker network",
        },
        {
            "component": "proxy_trust_store",
            "production": "immutable image trust store",
            "mock": "ephemeral test CA bind-mounted read-only",
        },
    )
    assert _PRESERVED_REAL_TOPOLOGY_IDENTITIES == (
        "one_shot_orchestrator",
        "runtime_approval_loader_v2",
        "proxy_image_id",
        "relay_image_id",
        "runtime_contract_sha256",
        "readiness_contract_sha256",
        "request_contract",
        "projection_sha256",
        "attempt_ledger",
        "claims_validator",
        "deterministic_renderer",
    )
    components = {item["component"] for item in _MOCK_TOPOLOGY_DELTAS}
    assert _topology_deltas_verified(components) is True
    assert _topology_deltas_verified(components - {"proxy_trust_store"}) is False
    assert _topology_deltas_verified({*components, "undeclared_delta"}) is False


def test_local_mock_topology_command_diff_rejects_undeclared_token_change() -> None:
    valid = [
        {
            "component": "proxy_egress_network",
            "original_commands": [["docker", "network", "create", "<EGRESS_NETWORK>"]],
            "transformed_commands": [
                ["docker", "network", "create", "--internal", "<EGRESS_NETWORK>"]
            ],
        },
        {
            "component": "proxy_trust_store",
            "original_commands": [["docker", "create", "<PROXY_IMAGE>"]],
            "transformed_commands": [
                [
                    "docker",
                    "create",
                    "--mount",
                    "type=bind,src=<TEST_CA>,dst=/etc/ssl/certs/ca-certificates.crt,readonly",
                    "<PROXY_IMAGE>",
                ]
            ],
        },
        {
            "component": "provider_endpoint",
            "original_commands": [],
            "transformed_commands": [
                [
                    "docker",
                    "create",
                    "--name",
                    "<MOCK_NAME>",
                    "--network",
                    "<EGRESS_NETWORK>",
                    "--network-alias",
                    "api.openai.com",
                    "--read-only",
                    "--user",
                    "0:0",
                    "--cap-drop",
                    "ALL",
                    "--cap-add",
                    "NET_BIND_SERVICE",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "16",
                    "--memory",
                    "64m",
                    "--cpus",
                    "0.25",
                    "--ipc",
                    "none",
                    "--restart",
                    "no",
                    "--mount",
                    "type=bind,src=<MOCK_SERVER>,dst=/mock/server.py,readonly",
                    "--mount",
                    "type=bind,src=<TEST_CA>,dst=/mock/server.crt,readonly",
                    "--mount",
                    "type=bind,src=<TEST_TLS_KEY>,dst=/mock/server.key,readonly",
                    "--mount",
                    "type=bind,src=<MOCK_RESPONSE>,dst=/mock/response.json,readonly",
                    "--mount",
                    "type=bind,src=<MOCK_OUTPUT>,dst=/output",
                    "<BASE_IMAGE>",
                    "/mock/server.py",
                ],
                ["docker", "start", "<MOCK_NAME>"],
            ],
        },
    ]

    assert _topology_command_diffs_verified(valid) is True
    mutated = json.loads(json.dumps(valid))
    mutated[1]["transformed_commands"][0].insert(-1, "--privileged")
    assert _topology_command_diffs_verified(mutated) is False


def test_local_mock_topology_command_diff_preserves_network_driver_option() -> None:
    records = [
        {
            "component": "proxy_egress_network",
            "original_commands": [
                [
                    "docker",
                    "network",
                    "create",
                    "--driver",
                    "bridge",
                    "<EGRESS_NETWORK>",
                ]
            ],
            "transformed_commands": [
                [
                    "docker",
                    "network",
                    "create",
                    "--driver",
                    "bridge",
                    "--internal",
                    "<EGRESS_NETWORK>",
                ]
            ],
        },
        {
            "component": "proxy_trust_store",
            "original_commands": [["docker", "create", "<PROXY_IMAGE>"]],
            "transformed_commands": [
                [
                    "docker",
                    "create",
                    "--mount",
                    "type=bind,src=<TEST_CA>,dst=/etc/ssl/certs/ca-certificates.crt,readonly",
                    "<PROXY_IMAGE>",
                ]
            ],
        },
        {
            "component": "provider_endpoint",
            "original_commands": [],
            "transformed_commands": _EXPECTED_MOCK_PROVIDER_COMMANDS,
        },
    ]

    assert _topology_command_diffs_verified(records) is True


def test_pre_fix_replay_reproduces_econnrefused_timeout_misclassification() -> None:
    evidence = _run_pre_fix_replay(REPO_ROOT)

    assert evidence["status"] == "PRE_FIX_FAILURE_REPRODUCED"
    assert evidence["replay_mode"] == "DETERMINISTIC_LOCAL_FAULT_REPLAY"
    assert evidence["public_network_request_count"] == 0
    assert evidence["injected_errno"] == 111
    assert evidence["observed_old_error_category"] == "RELAY_TIMEOUT"
    assert evidence["expected_fixed_error_category"] == (
        "RELAY_PROXY_CONNECT_FAILED"
    )
    assert evidence["superseded_candidate_sha256"] == (
        "e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b"
    )
