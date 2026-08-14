"""Build and verify the offline Ark TLS Probe v2 approval artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / "reports/phase2_provider_ark/tls_probe_v2"
ATTEMPTS_ROOT = OUTPUT_ROOT / "attempts"
PROBE_IMAGE_ID = (
    "sha256:a8a01a003f7afc385b4e24fd2960bcdfd3eaac40c394f39aa6249fd7b2f0f79b"
)
BASE_IMAGE_DIGEST = (
    "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)
CA_SOURCE = "/etc/ssl/certs/ca-certificates.crt"
TARGET_HOST = "ark.cn-beijing.volces.com"
TARGET_PORT = 443
SCOPE_TYPE = "ark_tls_connectivity_probe_v2"
PROBE_CONTRACT_VERSION = 2
FORK_REMOTE_REF = "refs/remotes/fork/codex/tickflow-phase2-ai-review"
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_IMAGE_LABELS = {
    "org.tickflow.phase2.base-image-digest": BASE_IMAGE_DIGEST,
    "org.tickflow.phase2.provider-id": "volcengine_ark",
    "org.tickflow.phase2.proxy-dockerfile-sha256": (
        "e2aa832c0d6f0bc30b5564fcf2c9620544e6d369158031527e985ca3dfa4ae7b"
    ),
    "org.tickflow.phase2.proxy-source-sha256": (
        "fe479235628c22af20cc31965940f612cf759a6a280c2feb792a3fd2400580e4"
    ),
    "org.tickflow.phase2.responses-contract-sha256": (
        "5c84050fdb705d04b66b3cfc69f9d6260e364b9486113fae5a5975483539855c"
    ),
    "org.tickflow.phase2.proxy-policy-sha256": (
        "406f837966c47ee842c8b7e338a021dbf9007872135a0349d4326cd0abda061c"
    ),
    "org.tickflow.phase2.runtime-contract-sha256": (
        "3cb3064e4e68bb2e9a07a9153f9c2bcef8de127ac0fd779b2c41e1ab9e0ac681"
    ),
    "org.tickflow.phase2.readiness-contract-sha256": (
        "c667b982d81f14d485e43c40deba637141675582cea044e2090e44ec465b7a20"
    ),
}

SOURCE_BINDING_PATHS = {
    "approval_builder_source": (
        "backend/scripts/build_phase2_ark_tls_probe_v2_offline.py"
    ),
    "probe_runner_source": "docker/phase2-ark-tls-connectivity-probe/probe.py",
    "probe_host_orchestrator": (
        "backend/scripts/run_phase2_ark_tls_connectivity_probe.py"
    ),
    "receipt_transport_harness_source": (
        "backend/scripts/run_phase2_ark_receipt_transport_harness.py"
    ),
    "receipt_transport_fixture_source": (
        "docker/phase2-ark-tls-connectivity-probe/receipt_transport_fixture.py"
    ),
    "child_receipt_schema": (
        "docker/phase2-ark-tls-connectivity-probe/contracts/"
        "child-receipt.schema.json"
    ),
    "ready_marker_schema": (
        "docker/phase2-ark-tls-connectivity-probe/contracts/"
        "ready-marker.schema.json"
    ),
    "path_validation_contract": (
        "docker/phase2-ark-tls-connectivity-probe/contracts/"
        "path-validation-contract.json"
    ),
    "receipt_transport_contract": (
        "docker/phase2-ark-tls-connectivity-probe/contracts/"
        "receipt-transport-contract.json"
    ),
    "tls_contract": (
        "docker/phase2-ark-tls-connectivity-probe/contracts/tls-contract.json"
    ),
    "container_dockerfile": "docker/phase2-ark-egress-proxy/Dockerfile",
    "container_source": "docker/phase2-ark-egress-proxy/proxy.py",
    "existing_ark_build_provenance": (
        "reports/phase2_provider_ark/ark_build_provenance.json"
    ),
    "historical_probe_baseline": (
        "reports/phase2_provider_ark/tls_receipt_transport/"
        "historical_probe_baseline.json"
    ),
    "receipt_transport_verification": (
        "reports/phase2_provider_ark/tls_receipt_transport/verification.json"
    ),
    "local_harness_evidence": (
        "reports/phase2_provider_ark/tls_receipt_transport/local_harness.json"
    ),
}
REQUIRED_SOURCE_BINDING_KEYS = tuple(SOURCE_BINDING_PATHS)
REQUIRED_ARTIFACT_HASH_KEYS = (
    "probe_runner_source_sha256",
    "probe_host_orchestrator_sha256",
    "receipt_transport_source_sha256",
    "receipt_schema_sha256",
    "ready_marker_schema_sha256",
    "path_validation_contract_sha256",
    "container_dockerfile_sha256",
    "container_source_sha256",
    "container_image_id",
    "base_image_digest",
    "build_provenance_sha256",
    "tls_contract_sha256",
    "historical_probe_baseline_sha256",
    "receipt_transport_verification_sha256",
    "local_harness_evidence_sha256",
)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _assert_hex(value: str, pattern: re.Pattern[str], category: str) -> None:
    if pattern.fullmatch(value) is None:
        raise ValueError(category)


def source_binding(repo_root: Path, relative_path: str) -> dict[str, str]:
    root = repo_root.resolve(strict=True)
    logical = repo_root / relative_path
    try:
        logical.relative_to(repo_root)
    except ValueError as error:
        raise ValueError("source_binding_escape") from error
    if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise ValueError("source_binding_escape")
    current = repo_root
    for part in Path(relative_path).parts:
        current = current / part
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise ValueError("source_binding_missing") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("source_binding_symlink")
    resolved = logical.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("source_binding_escape") from error
    metadata = os.lstat(logical)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("source_binding_not_regular")
    return {"path": Path(relative_path).as_posix(), "sha256": _sha256_file(logical)}


def _source_bindings(repo_root: Path) -> dict[str, dict[str, str]]:
    return {
        name: source_binding(repo_root, path)
        for name, path in SOURCE_BINDING_PATHS.items()
    }


def verify_historical_probe_baseline(
    repo_root: Path,
    baseline_path: Path,
    *,
    expected_file_count: int = 7,
) -> int:
    if baseline_path.is_symlink() or not baseline_path.is_file():
        raise ValueError("historical_probe_baseline_invalid")
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("historical_probe_baseline_invalid") from error
    files = baseline.get("files") if isinstance(baseline, dict) else None
    if (
        baseline.get("baseline_schema_version") != 1
        or baseline.get("file_count") != expected_file_count
        or baseline.get("historical_probe_id")
        != "5dd641ca9e4b4ccba1035fff4d695409"
        or baseline.get("historical_status")
        != (
            "PHASE2B_ARK_TLS_PROBE_UNKNOWN / PROCESS_ERROR / CONSUMED / "
            "PRESERVED"
        )
        or not isinstance(files, list)
        or len(files) != expected_file_count
    ):
        raise ValueError("historical_probe_baseline_invalid")
    expected_paths: set[Path] = set()
    logical_paths: list[Path] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("historical_probe_baseline_invalid")
        relative = item.get("path")
        expected_sha = item.get("sha256")
        if (
            not isinstance(relative, str)
            or not isinstance(expected_sha, str)
            or _HEX_64.fullmatch(expected_sha) is None
        ):
            raise ValueError("historical_probe_baseline_invalid")
        binding = source_binding(repo_root, relative)
        if binding["sha256"] != expected_sha:
            raise ValueError("historical_probe_evidence_mutated")
        logical = repo_root / relative
        expected_paths.add(logical.resolve(strict=True))
        logical_paths.append(logical)
    common_root = Path(os.path.commonpath([str(path.parent) for path in logical_paths]))
    actual_paths: set[Path] = set()
    for candidate in common_root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError("historical_probe_evidence_mutated")
        if candidate.is_file():
            actual_paths.add(candidate.resolve(strict=True))
    if actual_paths != expected_paths:
        raise ValueError("historical_probe_evidence_set_changed")
    return len(expected_paths)


def verify_receipt_transport_evidence(
    *,
    verification_path: Path,
    harness_path: Path,
) -> dict[str, Any]:
    try:
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        harness = json.loads(harness_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("receipt_transport_evidence_invalid") from error
    scenarios = harness.get("scenarios") if isinstance(harness, dict) else None
    prohibited = (
        verification.get("prohibited_operations")
        if isinstance(verification, dict)
        else None
    )
    residue = verification.get("residue") if isinstance(verification, dict) else None
    valid = (
        verification.get("final_status")
        == "PHASE2B_ARK_TLS_PROBE_RECEIPT_CHANNEL_READY"
        and verification.get("historical_probe", {}).get("file_count") == 7
        and verification.get("historical_probe", {}).get("evidence") == "UNCHANGED"
        and verification.get("root_cause", {}).get("category")
        == "HOST_CANONICAL_PATH_VALIDATION_REJECTED_MACOS_SYSTEM_ALIAS"
        and verification.get("publication_contract", {}).get(
            "exit_zero_implies_durable_receipt_bundle"
        )
        is True
        and verification.get("host_lifecycle", {}).get("archive_before_cleanup")
        == "PASSED"
        and isinstance(prohibited, dict)
        and all(value == 0 for value in prohibited.values())
        and isinstance(residue, dict)
        and residue.get("docker_api_verified") is True
        and residue.get("container_count") == 0
        and residue.get("network_count") == 0
        and residue.get("temporary_directory_count") == 0
        and harness.get("status") == "PASSED"
        and harness.get("network_mode") == "none"
        and harness.get("scenario_count") == 9
        and harness.get("happy_path_runs") == 3
        and harness.get("happy_path_all_exit_zero") is True
        and harness.get("happy_path_all_archived_before_cleanup") is True
        and harness.get("fault_injection_all_fail_closed") is True
        and harness.get("historical_evidence") == "UNCHANGED"
        and harness.get("provider_attempt_count") == 0
        and harness.get("ai_call_count") == 0
        and harness.get("secret_content_read_count") == 0
        and harness.get("authorization_constructed_count") == 0
        and harness.get("http_request_sent_count") == 0
        and harness.get("real_public_network_success_count") == 0
        and harness.get("container_residue_count") == 0
        and harness.get("network_residue_count") == 0
        and harness.get("temporary_residue_count") == 0
        and isinstance(scenarios, list)
        and len(scenarios) == 9
        and all(
            isinstance(item, dict)
            and item.get("network_mode") == "none"
            and item.get("public_operation_count") == 0
            for item in scenarios
        )
    )
    if not valid:
        raise ValueError("receipt_transport_evidence_invalid")
    return {
        "receipt_channel": "VERIFIED",
        "root_cause": "MACOS_VAR_PRIVATE_VAR_CANONICAL_ALIAS",
        "historical_evidence": "UNCHANGED",
        "scenario_count": 9,
        "happy_path_runs": 3,
        "real_public_network_success_count": 0,
        "container_residue_count": 0,
        "network_residue_count": 0,
        "temporary_residue_count": 0,
    }


def _run_local(
    command: list[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _require_success(
    result: subprocess.CompletedProcess[str],
    category: str,
) -> None:
    if result.returncode != 0:
        raise ValueError(category)


def inspect_probe_image(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = _run_local,
) -> dict[str, Any]:
    command = ["docker", "image", "inspect", PROBE_IMAGE_ID]
    result = run(command, timeout=15)
    _require_success(result, "probe_image_inspect_failed")
    try:
        values = json.loads(result.stdout)
        value = values[0]
        labels = value["Config"]["Labels"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("probe_image_inspect_invalid") from error
    if value.get("Id") != PROBE_IMAGE_ID:
        raise ValueError("probe_image_identity_mismatch")
    if labels != EXPECTED_IMAGE_LABELS:
        raise ValueError("probe_image_labels_mismatch")
    if value.get("Architecture") != "amd64" or value.get("Os") != "linux":
        raise ValueError("probe_image_platform_mismatch")
    repo_digests = value.get("RepoDigests")
    if not isinstance(repo_digests, list) or not repo_digests:
        raise ValueError("probe_image_repo_digest_missing")
    return {
        "image_id": value["Id"],
        "architecture": value["Architecture"],
        "os": value["Os"],
        "repo_digests": list(repo_digests),
        "labels": dict(labels),
    }


def extract_ca_identity(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = _run_local,
    temporary_parent: Path | None = None,
    container_name: str | None = None,
) -> dict[str, Any]:
    parent = temporary_parent.resolve(strict=True) if temporary_parent else None
    temporary_root = Path(
        tempfile.mkdtemp(prefix="phase2-ark-ca-inspect-", dir=parent)
    ).resolve(strict=True)
    name = container_name or f"phase2-ark-ca-inspect-{os.getpid()}"
    destination = temporary_root / "ca-certificates.crt"
    created = False
    primary_error: BaseException | None = None
    try:
        create = run(
            [
                "docker",
                "create",
                "--name",
                name,
                "--network",
                "none",
                PROBE_IMAGE_ID,
            ],
            timeout=15,
        )
        _require_success(create, "ca_container_create_failed")
        created = True
        copied = run(
            ["docker", "cp", f"{name}:{CA_SOURCE}", str(destination)],
            timeout=15,
        )
        _require_success(copied, "ca_bundle_copy_failed")
        metadata = os.lstat(destination)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_size <= 0
        ):
            raise ValueError("ca_bundle_invalid")
        identity = {
            "source": CA_SOURCE,
            "sha256": _sha256_file(destination),
            "size_bytes": metadata.st_size,
            "file_type": "regular",
        }
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if created:
            removed = run(["docker", "rm", name], timeout=15)
            if removed.returncode != 0 and primary_error is None:
                raise ValueError("ca_container_cleanup_failed")
        if destination.exists():
            destination.unlink()
        temporary_root.rmdir()
    return identity


def _validate_image_and_ca(
    image_identity: Mapping[str, Any],
    ca_identity: Mapping[str, Any],
) -> None:
    if (
        image_identity.get("image_id") != PROBE_IMAGE_ID
        or image_identity.get("architecture") != "amd64"
        or image_identity.get("os") != "linux"
        or image_identity.get("labels") != EXPECTED_IMAGE_LABELS
    ):
        raise ValueError("probe_image_identity_invalid")
    if (
        ca_identity.get("source") != CA_SOURCE
        or ca_identity.get("file_type") != "regular"
        or not isinstance(ca_identity.get("size_bytes"), int)
        or ca_identity["size_bytes"] <= 0
        or not isinstance(ca_identity.get("sha256"), str)
        or _HEX_64.fullmatch(ca_identity["sha256"]) is None
    ):
        raise ValueError("ca_bundle_identity_invalid")


def _receipt_transport_source_sha256(
    bindings: Mapping[str, Mapping[str, str]],
) -> str:
    source_names = (
        "probe_runner_source",
        "probe_host_orchestrator",
        "receipt_transport_harness_source",
        "receipt_transport_fixture_source",
    )
    return _sha256_bytes(
        canonical_json_bytes({name: bindings[name] for name in source_names})
    )


def build_probe_build_provenance(
    repo_root: Path,
    *,
    source_git_head: str,
    image_identity: Mapping[str, Any],
    ca_identity: Mapping[str, Any],
) -> dict[str, Any]:
    _assert_hex(source_git_head, _HEX_40, "source_git_head_invalid")
    _validate_image_and_ca(image_identity, ca_identity)
    bindings = _source_bindings(repo_root)
    if (
        bindings["container_dockerfile"]["sha256"]
        != EXPECTED_IMAGE_LABELS["org.tickflow.phase2.proxy-dockerfile-sha256"]
        or bindings["container_source"]["sha256"]
        != EXPECTED_IMAGE_LABELS["org.tickflow.phase2.proxy-source-sha256"]
    ):
        raise ValueError("probe_image_source_binding_mismatch")
    return {
        "probe_build_provenance_schema_version": 1,
        "build_path": "APPROVED_IMMUTABLE_IMAGE_WITH_READ_ONLY_PROBE_SOURCE",
        "source_git_head": source_git_head,
        "container_image_id": PROBE_IMAGE_ID,
        "base_image_digest": BASE_IMAGE_DIGEST,
        "architecture": image_identity["architecture"],
        "os": image_identity["os"],
        "repo_digests": list(image_identity["repo_digests"]),
        "image_labels": dict(image_identity["labels"]),
        "container_dockerfile": bindings["container_dockerfile"],
        "container_source": bindings["container_source"],
        "mounted_probe_source": bindings["probe_runner_source"],
        "existing_ark_build_provenance": bindings[
            "existing_ark_build_provenance"
        ],
        "ca_bundle_identity": dict(ca_identity),
        "docker_access_mode": "READ_ONLY_IMAGE_INSPECT_AND_STOPPED_CONTAINER_COPY",
        "container_started": False,
        "network_mode": "none",
        "real_public_network_success_count": 0,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "secret_content_read": False,
    }


def build_probe_candidate(
    repo_root: Path,
    *,
    source_git_head: str,
    image_identity: Mapping[str, Any],
    ca_identity: Mapping[str, Any],
    build_provenance_sha256: str,
) -> dict[str, Any]:
    _assert_hex(source_git_head, _HEX_40, "source_git_head_invalid")
    _assert_hex(
        build_provenance_sha256,
        _HEX_64,
        "build_provenance_sha256_invalid",
    )
    _validate_image_and_ca(image_identity, ca_identity)
    bindings = _source_bindings(repo_root)
    artifact_hashes = {
        "probe_runner_source_sha256": bindings["probe_runner_source"]["sha256"],
        "probe_host_orchestrator_sha256": bindings[
            "probe_host_orchestrator"
        ]["sha256"],
        "receipt_transport_source_sha256": _receipt_transport_source_sha256(
            bindings
        ),
        "receipt_schema_sha256": bindings["child_receipt_schema"]["sha256"],
        "ready_marker_schema_sha256": bindings["ready_marker_schema"]["sha256"],
        "path_validation_contract_sha256": bindings[
            "path_validation_contract"
        ]["sha256"],
        "container_dockerfile_sha256": bindings["container_dockerfile"]["sha256"],
        "container_source_sha256": bindings["container_source"]["sha256"],
        "container_image_id": PROBE_IMAGE_ID,
        "base_image_digest": BASE_IMAGE_DIGEST,
        "build_provenance_sha256": build_provenance_sha256,
        "tls_contract_sha256": bindings["tls_contract"]["sha256"],
        "historical_probe_baseline_sha256": bindings[
            "historical_probe_baseline"
        ]["sha256"],
        "receipt_transport_verification_sha256": bindings[
            "receipt_transport_verification"
        ]["sha256"],
        "local_harness_evidence_sha256": bindings[
            "local_harness_evidence"
        ]["sha256"],
    }
    if set(artifact_hashes) != set(REQUIRED_ARTIFACT_HASH_KEYS):
        raise ValueError("probe_artifact_hashes_incomplete")
    if set(bindings) != set(REQUIRED_SOURCE_BINDING_KEYS):
        raise ValueError("probe_source_bindings_incomplete")
    return {
        "probe_approval_candidate_schema_version": 2,
        "candidate_status": "TLS_PROBE_CANDIDATE_READY_FOR_REVIEW",
        "approval_installed": False,
        "scope_type": SCOPE_TYPE,
        "source_git_head": source_git_head,
        "probe_contract_version": PROBE_CONTRACT_VERSION,
        "target_host": TARGET_HOST,
        "target_port": TARGET_PORT,
        "sni_hostname": TARGET_HOST,
        "hostname_verification_target": TARGET_HOST,
        "ca_source": CA_SOURCE,
        "ca_bundle_identity": dict(ca_identity),
        "tls_minimum": "TLSv1_2",
        "tls_maximum": "MAXIMUM_SUPPORTED",
        "verify_mode": "CERT_REQUIRED",
        "check_hostname": True,
        "retry_count": 0,
        "maximum_probe_attempts": 1,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "authorization_constructed": False,
        "http_request_sent": False,
        "secret_content_read": False,
        "real_public_network_success_count": 0,
        "historical_probe_id": "5dd641ca9e4b4ccba1035fff4d695409",
        "historical_probe_status": (
            "PHASE2B_ARK_TLS_PROBE_UNKNOWN / PROCESS_ERROR / CONSUMED / PRESERVED"
        ),
        "receipt_transport_root_cause": "MACOS_VAR_PRIVATE_VAR_CANONICAL_ALIAS",
        "source_bindings": bindings,
        "artifact_hashes": artifact_hashes,
    }


def build_probe_scope(
    candidate_raw: bytes,
    *,
    attempts_root: Path,
) -> dict[str, Any]:
    candidate_sha256 = _sha256_bytes(candidate_raw)
    identity = {
        "scope_type": SCOPE_TYPE,
        "approval_candidate_sha256": candidate_sha256,
        "target_host": TARGET_HOST,
        "target_port": TARGET_PORT,
    }
    scope_id = _sha256_bytes(canonical_json_bytes(identity))
    scope_path = attempts_root / scope_id
    if os.path.lexists(scope_path):
        raise ValueError("probe_scope_attempt_unavailable")
    return {
        "probe_approval_scope_schema_version": 1,
        **identity,
        "approval_scope_id": scope_id,
        "approval_installed": False,
        "historical_attempts": 0,
        "attempt_availability": "AVAILABLE",
        "retry_count": 0,
        "maximum_probe_attempts": 1,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "provider_scope_shared": False,
        "ai_canary_scope_shared": False,
        "openai_scope_shared": False,
    }


def build_offline_artifacts(
    repo_root: Path,
    *,
    source_git_head: str,
    image_identity: Mapping[str, Any],
    ca_identity: Mapping[str, Any],
) -> dict[str, Any]:
    verify_historical_probe_baseline(
        repo_root,
        repo_root / SOURCE_BINDING_PATHS["historical_probe_baseline"],
    )
    transport_summary = verify_receipt_transport_evidence(
        verification_path=(
            repo_root / SOURCE_BINDING_PATHS["receipt_transport_verification"]
        ),
        harness_path=repo_root / SOURCE_BINDING_PATHS["local_harness_evidence"],
    )
    provenance = build_probe_build_provenance(
        repo_root,
        source_git_head=source_git_head,
        image_identity=image_identity,
        ca_identity=ca_identity,
    )
    provenance_raw = canonical_json_bytes(provenance)
    candidate = build_probe_candidate(
        repo_root,
        source_git_head=source_git_head,
        image_identity=image_identity,
        ca_identity=ca_identity,
        build_provenance_sha256=_sha256_bytes(provenance_raw),
    )
    candidate_raw = canonical_json_bytes(candidate)
    scope = build_probe_scope(candidate_raw, attempts_root=repo_root / ATTEMPTS_ROOT.relative_to(REPO_ROOT))
    preflight = {
        "probe_offline_preflight_schema_version": 1,
        "status": "TLS_PROBE_CANDIDATE_PREPARED_OFFLINE",
        "source_git_head": source_git_head,
        "approval_candidate_sha256": _sha256_bytes(candidate_raw),
        "approval_scope_id": scope["approval_scope_id"],
        "probe_historical_attempts": 0,
        "probe_availability": "AVAILABLE",
        "receipt_channel": transport_summary["receipt_channel"],
        "receipt_transport_root_cause": transport_summary["root_cause"],
        "historical_evidence": transport_summary["historical_evidence"],
        "local_harness_scenario_count": transport_summary["scenario_count"],
        "local_harness_happy_path_runs": transport_summary["happy_path_runs"],
        "ca_trust": "PRESENT",
        "hostname_verification": "ENABLED",
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "authorization_constructed": False,
        "http_request_sent": False,
        "secret_content_read": False,
        "real_public_network_success_count": 0,
        "provider_http": "NOT_RUN",
    }
    return {
        "build_provenance": provenance,
        "approval_candidate": candidate,
        "approval_scope": scope,
        "offline_preflight": preflight,
    }


def verify_offline_artifacts(
    repo_root: Path,
    *,
    artifacts: Mapping[str, Any],
    source_git_head: str,
    image_identity: Mapping[str, Any],
    ca_identity: Mapping[str, Any],
) -> None:
    expected = build_offline_artifacts(
        repo_root,
        source_git_head=source_git_head,
        image_identity=image_identity,
        ca_identity=ca_identity,
    )
    if canonical_json_bytes(artifacts) != canonical_json_bytes(expected):
        raise ValueError("probe_artifact_mismatch")


def atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".staging", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _artifact_paths(output_root: Path) -> dict[str, Path]:
    return {
        "build_provenance": output_root / "build_provenance.json",
        "approval_candidate": output_root / "approval_candidate.json",
        "approval_scope": output_root / "approval_scope.json",
        "offline_preflight": output_root / "offline_preflight.json",
    }


def _artifact_generation(
    paths: Mapping[str, Path],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    source_head = artifacts.get("approval_candidate", {}).get("source_git_head")
    if not isinstance(source_head, str) or _HEX_40.fullmatch(source_head) is None:
        raise ValueError("probe_artifact_source_head_invalid")
    hashes = {
        path.name: _sha256_bytes(canonical_json_bytes(artifacts[name]))
        for name, path in paths.items()
    }
    return {
        "artifact_generation_schema_version": 1,
        "source_git_head": source_head,
        "artifact_hashes": hashes,
        "artifact_set_sha256": _sha256_bytes(canonical_json_bytes(hashes)),
    }


def publish_offline_artifacts(output_root: Path, artifacts: Mapping[str, Any]) -> None:
    paths = _artifact_paths(output_root)
    if set(artifacts) != set(paths):
        raise ValueError("probe_artifact_set_invalid")
    generation = _artifact_generation(paths, artifacts)
    for name, path in paths.items():
        atomic_write_bytes(path, canonical_json_bytes(artifacts[name]))
    atomic_write_bytes(
        output_root / "artifact_generation.json",
        canonical_json_bytes(generation),
    )


def read_published_artifacts(output_root: Path) -> dict[str, Any]:
    generation_path = output_root / "artifact_generation.json"
    if generation_path.is_symlink() or not generation_path.is_file():
        raise ValueError("probe_artifact_generation_missing")
    try:
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("probe_artifact_generation_invalid") from error
    values: dict[str, Any] = {}
    paths = _artifact_paths(output_root)
    for name, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError("probe_artifact_missing")
        values[name] = json.loads(path.read_text(encoding="utf-8"))
    expected = _artifact_generation(paths, values)
    if generation != expected:
        raise ValueError("probe_artifact_generation_mismatch")
    return values


def verify_git_source_state(
    repo_root: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    status = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    _require_success(status, "git_status_unavailable")
    if status.stdout:
        raise ValueError("git_worktree_dirty")
    head = run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    _require_success(head, "git_head_unavailable")
    fork = run(
        ["git", "rev-parse", FORK_REMOTE_REF],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    _require_success(fork, "git_fork_head_unavailable")
    head_value = head.stdout.strip()
    fork_value = fork.stdout.strip()
    _assert_hex(head_value, _HEX_40, "git_head_invalid")
    _assert_hex(fork_value, _HEX_40, "git_fork_head_invalid")
    if head_value != fork_value:
        raise ValueError("git_fork_head_mismatch")
    return head_value


def _build_current(
    repo_root: Path,
    *,
    source_head: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    image_identity = inspect_probe_image()
    ca_identity = extract_ca_identity()
    artifacts = build_offline_artifacts(
        repo_root,
        source_git_head=source_head,
        image_identity=image_identity,
        ca_identity=ca_identity,
    )
    return artifacts, image_identity, ca_identity


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.build:
        source_head = verify_git_source_state(REPO_ROOT)
        artifacts, image_identity, ca_identity = _build_current(
            REPO_ROOT,
            source_head=source_head,
        )
        publish_offline_artifacts(OUTPUT_ROOT, artifacts)
    else:
        verify_git_source_state(REPO_ROOT)
        published = read_published_artifacts(OUTPUT_ROOT)
        source_head = published["approval_candidate"].get("source_git_head")
        if not isinstance(source_head, str):
            raise ValueError("git_head_invalid")
        artifacts, image_identity, ca_identity = _build_current(
            REPO_ROOT,
            source_head=source_head,
        )
        verify_offline_artifacts(
            REPO_ROOT,
            artifacts=published,
            source_git_head=source_head,
            image_identity=image_identity,
            ca_identity=ca_identity,
        )
    candidate_raw = canonical_json_bytes(artifacts["approval_candidate"])
    print(
        json.dumps(
            {
                "status": "TLS_PROBE_CANDIDATE_PREPARED_OFFLINE",
                "approval_candidate_sha256": _sha256_bytes(candidate_raw),
                "approval_scope_id": artifacts["approval_scope"][
                    "approval_scope_id"
                ],
                "provider_attempt_count": 0,
                "ai_call_count": 0,
                "provider_http": "NOT_RUN",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
