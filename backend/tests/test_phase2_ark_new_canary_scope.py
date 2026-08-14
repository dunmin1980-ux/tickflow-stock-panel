from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

from app.providers.ark_contract import (
    build_ark_approval_candidate_bytes,
    build_ark_approval_scope_evidence,
    load_ark_tls_probe_evidence_binding,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = (
    REPO_ROOT / "backend/scripts/build_phase2_ark_new_canary_scope_offline.py"
)
TLS_SCOPE_ID = "abd999450d007f23cadccd86d22136608c3fbb04b1d92c0078153088781f919c"
TLS_PROBE_ID = "d73e91f766854ff7a64c0e725939eb64"
TLS_RECEIPT_SHA256 = (
    "ef1310f780687e494965d399c1c02d7d7666abeb7a018310ab7b5a4d9abaa6e3"
)


def _current_image_ids() -> dict[str, str]:
    value = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/ark_build_provenance.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "proxy_image_id": value["proxy_image_id"],
        "relay_image_id": value["relay_image_id"],
    }


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ark_new_scope_builder", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_candidate_binds_tls_probe_and_complete_single_file_sources() -> None:
    candidate_raw = build_ark_approval_candidate_bytes(
        REPO_ROOT,
        current_git_head=_git_head(),
        **_current_image_ids(),
    )
    candidate = json.loads(candidate_raw)

    assert candidate["ark_approval_candidate_schema_version"] == 2
    assert candidate["status"] == "CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL"
    assert candidate["current_git_head"] == _git_head()
    assert candidate["base_image_digest"].startswith("sha256:")
    assert candidate["tls_probe"] == {
        "probe_id": TLS_PROBE_ID,
        "scope_id": TLS_SCOPE_ID,
        "result": "PASSED",
        "receipt_sha256": TLS_RECEIPT_SHA256,
        "attempt_status": "CONSUMED",
        "preservation_status": "PRESERVED",
        "reuse_status": "NON_REUSABLE",
    }
    required_hashes = {
        "facts_sha256",
        "projection_sha256",
        "typed_claims_schema_sha256",
        "ark_adapter_source_sha256",
        "ark_launcher_source_sha256",
        "ark_proxy_policy_sha256",
        "ark_responses_contract_sha256",
        "orchestrator_source_sha256",
        "runtime_contract_sha256",
        "readiness_contract_sha256",
        "build_provenance_sha256",
        "artifact_generation_sha256",
        "tls_probe_receipt_sha256",
        "tls_probe_evidence_sha256",
    }
    assert required_hashes <= set(candidate["artifact_hashes"])
    assert candidate["artifact_hashes"]["tls_probe_receipt_sha256"] == (
        TLS_RECEIPT_SHA256
    )
    assert candidate["artifact_hashes"]["mock_e2e_sha256"] == hashlib.sha256(
        (REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_bytes()
    ).hexdigest()
    assert candidate["artifact_hashes"]["orchestrator_source_sha256"] == (
        hashlib.sha256(
            (
                REPO_ROOT
                / "backend/app/services/phase2_canary_orchestrator.py"
            ).read_bytes()
        ).hexdigest()
    )
    bindings = candidate["source_bindings"]
    required_bindings = {
        "facts",
        "ark_adapter_source",
        "ark_launcher_source",
        "ark_proxy_policy_source",
        "orchestrator_source",
        "runtime_contract",
        "readiness_contract",
        "responses_contract",
        "claims_schema_source",
        "projection_builder_source",
        "build_provenance",
        "artifact_generation",
        "tls_probe_evidence",
        "proxy_dockerfile",
        "proxy_source",
        "relay_dockerfile",
        "relay_source",
    }
    assert required_bindings <= set(bindings)
    assert all(set(value) == {"path", "sha256"} for value in bindings.values())
    assert all(len(value["sha256"]) == 64 for value in bindings.values())


def test_new_scope_binds_candidate_head_tls_result_and_zero_attempts() -> None:
    candidate_raw = build_ark_approval_candidate_bytes(
        REPO_ROOT,
        current_git_head=_git_head(),
        **_current_image_ids(),
    )
    scope = build_ark_approval_scope_evidence(
        candidate_raw,
        repo_root=REPO_ROOT,
        mock_e2e_sha256=hashlib.sha256(
            (REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_bytes()
        ).hexdigest(),
    )

    assert scope["current_git_head"] == _git_head()
    assert scope["tls_probe_id"] == TLS_PROBE_ID
    assert scope["tls_probe_scope_id"] == TLS_SCOPE_ID
    assert scope["tls_probe_result"] == "PASSED"
    assert scope["historical_attempts"] == 0
    assert scope["attempt_availability"] == "AVAILABLE"
    assert scope["attempt_directory"] == "ABSENT"
    assert scope["retry_count"] == 0
    assert scope["maximum_provider_attempts"] == 1


def test_tls_probe_binding_is_canonical_and_security_closed() -> None:
    binding, raw = load_ark_tls_probe_evidence_binding(REPO_ROOT)

    assert raw.endswith(b"\n")
    assert binding["probe_id"] == TLS_PROBE_ID
    assert binding["approval_scope_id"] == TLS_SCOPE_ID
    assert binding["terminal_status"] == "PROBE_PASSED"
    assert binding["receipt_sha256"] == TLS_RECEIPT_SHA256
    assert binding["provider_attempt_count"] == 0
    assert binding["ai_call_count"] == 0
    assert binding["http_request_sent"] is False
    assert binding["secret_content_read"] is False
    assert binding["authorization_constructed"] is False
    assert binding["historical_evidence"] == "UNCHANGED"


def test_offline_builder_validates_receipt_without_network_or_secret_read(
    tmp_path: Path,
) -> None:
    builder = _load_builder()
    receipt = {
        "probe_receipt_schema_version": 1,
        "probe_id": TLS_PROBE_ID,
        "probe_attempt_count": 1,
        "terminal_status": "PROBE_PASSED",
        "tls_error_category": "NONE",
        "tls_version": "TLSv1.3",
        "cipher_name": "TLS_AES_256_GCM_SHA384",
        "san_contains_hostname": True,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "retry_count": 0,
        "secret_content_read": False,
        "authorization_constructed": False,
        "http_request_sent": False,
        "business_body_sent": False,
        "target": {
            "host": "ark.cn-beijing.volces.com",
            "port": 443,
            "sni": "ark.cn-beijing.volces.com",
            "hostname_verification_target": "ark.cn-beijing.volces.com",
        },
        "events": {
            name: {"occurred": True, "category": "PASSED", "monotonic_ns": index}
            for index, name in enumerate(builder.REQUIRED_TLS_EVENTS, start=1)
        },
        "certificate_not_before": "Apr 22 00:00:00 2026 GMT",
        "certificate_not_after": "Nov  6 23:59:59 2026 GMT",
        "verify_code": None,
        "verify_message": None,
    }
    path = tmp_path / "receipt.json"
    path.write_bytes(builder.canonical_json_bytes(receipt))

    result = builder.validate_tls_probe_receipt(path)

    assert result["probe_id"] == TLS_PROBE_ID
    assert result["terminal_status"] == "PROBE_PASSED"
    assert result["provider_attempt_count"] == 0
    assert result["http_request_sent"] is False


def test_keychain_gate_checks_presence_without_requesting_secret() -> None:
    builder = _load_builder()
    observed: list[str] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    assert builder.keychain_secret_present(run=runner) is True
    assert observed == [
        "security",
        "find-generic-password",
        "-s",
        "tickflow-phase2-canary-volcengine-ark",
    ]
    assert "-w" not in observed
    assert "-g" not in observed


def test_preserve_superseded_artifact_is_content_addressed_and_idempotent(
    tmp_path: Path,
) -> None:
    builder = _load_builder()
    source = tmp_path / "current.json"
    source.write_bytes(b'{"value":1}\n')
    destination_root = tmp_path / "superseded"

    destination = builder.preserve_content_addressed(
        source,
        destination_root,
        suffix="artifact-generation",
    )
    repeated = builder.preserve_content_addressed(
        source,
        destination_root,
        suffix="artifact-generation",
    )

    expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert destination == destination_root / f"{expected_sha}-artifact-generation.json"
    assert repeated == destination
    assert destination.read_bytes() == source.read_bytes()


def test_history_binding_reads_the_dedicated_seven_file_tls_probe_baseline() -> None:
    builder = _load_builder()

    paths = builder._historical_tls_probe_files()

    assert len(paths) == 7
    assert all("tls_connectivity_probe" in path for path in paths)


def test_offline_builder_source_has_no_live_provider_or_tls_probe_path() -> None:
    source = BUILDER_PATH.read_text(encoding="utf-8")
    prohibited = (
        "urllib.request",
        "http.client",
        "requests.",
        "socket.",
        "openssl",
        "curl",
        "run_phase2_ark_single_symbol_canary",
        "run_phase2_ark_tls_connectivity_probe",
        "read_ark_keychain_secret_once",
    )
    assert all(token not in source for token in prohibited)
