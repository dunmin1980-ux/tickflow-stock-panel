from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "backend/scripts/run_phase2_ark_tls_probe_mock_e2e.py"
EVIDENCE_PATH = (
    REPO_ROOT
    / "reports/phase2_provider_ark/tls_connectivity_probe/mock_e2e.json"
)


def test_mock_runner_and_evidence_exist() -> None:
    assert SCRIPT_PATH.is_file(), "Docker internal-only mock runner is required"
    assert EVIDENCE_PATH.is_file(), "six-scenario mock evidence is required"


def test_mock_evidence_proves_exact_six_scenario_contract() -> None:
    assert EVIDENCE_PATH.is_file(), "six-scenario mock evidence is required"
    value = json.loads(EVIDENCE_PATH.read_bytes())
    assert value["mock_e2e_schema_version"] == 1
    assert value["status"] == "PASSED"
    assert value["proxy_image_id"] == (
        "sha256:a8a01a003f7afc385b4e24fd2960bcdfd3eaac40c394f39aa6249fd7b2f0f79b"
    )
    assert value["real_public_network_success_count"] == 0
    assert value["provider_attempt_count"] == 0
    assert value["ai_call_count"] == 0
    assert value["retry_count"] == 0
    assert value["container_residue_count"] == 0
    assert value["network_residue_count"] == 0
    assert value["temporary_residue_count"] == 0
    assert value["scenario_count"] == 6
    assert {item["name"]: item["tls_error_category"] for item in value["scenarios"]} == {
        "trusted_ca_correct_hostname": "NONE",
        "unknown_ca": "TLS_CERT_VERIFY_FAILED",
        "hostname_mismatch": "TLS_HOSTNAME_VERIFY_FAILED",
        "connection_refused": "TCP_CONNECT_FAILED",
        "missing_dns_alias": "DNS_RESOLUTION_FAILED",
        "handshake_timeout": "TLS_HANDSHAKE_TIMEOUT",
    }
    assert all(item["network_internal"] is True for item in value["scenarios"])
    assert all(item["probe_attempt_count"] == 1 for item in value["scenarios"])
    assert all(item["retry_count"] == 0 for item in value["scenarios"])
