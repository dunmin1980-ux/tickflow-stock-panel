from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = (
    REPO_ROOT / "docker/phase2-ark-tls-connectivity-probe/contracts"
)
BUILDER_PATH = REPO_ROOT / "backend/scripts/build_phase2_ark_tls_probe_v2_offline.py"
CONTRACT_PATHS = {
    "child_receipt": CONTRACT_ROOT / "child-receipt.schema.json",
    "ready_marker": CONTRACT_ROOT / "ready-marker.schema.json",
    "path_validation": CONTRACT_ROOT / "path-validation-contract.json",
    "receipt_transport": CONTRACT_ROOT / "receipt-transport-contract.json",
    "tls": CONTRACT_ROOT / "tls-contract.json",
}

EXPECTED_EVENTS = [
    "probe_started",
    "dns_started",
    "dns_completed",
    "tcp_connect_started",
    "tcp_connect_completed",
    "tls_handshake_started",
    "tls_handshake_completed",
    "certificate_verified",
    "hostname_verified",
    "probe_closed",
    "cleanup_completed",
]
EXPECTED_FAILURE_CATEGORIES = [
    "DNS_RESOLUTION_FAILED",
    "TCP_CONNECT_FAILED",
    "TCP_CONNECT_TIMEOUT",
    "TLS_HANDSHAKE_TIMEOUT",
    "TLS_CERT_VERIFY_FAILED",
    "TLS_HOSTNAME_VERIFY_FAILED",
    "TLS_PROTOCOL_FAILED",
    "TLS_CONNECTION_RESET",
    "TLS_EOF",
    "TLS_OTHER_SSL_ERROR",
    "PROCESS_ERROR",
]


def _read_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"required contract is missing: {path}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required implementation is missing: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def contracts() -> dict[str, dict[str, Any]]:
    return {name: _read_json(path) for name, path in CONTRACT_PATHS.items()}


@pytest.fixture
def builder_module() -> ModuleType:
    return _load_module(BUILDER_PATH, "build_phase2_ark_tls_probe_v2_offline")


def test_all_probe_v2_contracts_are_source_controlled() -> None:
    assert all(path.is_file() for path in CONTRACT_PATHS.values())


def test_receipt_and_marker_schemas_are_closed(
    contracts: dict[str, dict[str, Any]],
) -> None:
    receipt = contracts["child_receipt"]
    marker = contracts["ready_marker"]
    assert receipt["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert receipt["additionalProperties"] is False
    assert receipt["required"] == sorted(receipt["properties"])
    assert marker["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert marker["additionalProperties"] is False
    assert marker["required"] == sorted(marker["properties"])


def test_receipt_schema_fixes_events_and_failure_taxonomy(
    contracts: dict[str, dict[str, Any]],
) -> None:
    receipt = contracts["child_receipt"]
    events = receipt["properties"]["events"]
    terminal = receipt["properties"]["terminal_status"]["enum"]
    tls_error = receipt["properties"]["tls_error_category"]["enum"]
    assert events["required"] == EXPECTED_EVENTS
    assert events["x-event-order"] == EXPECTED_EVENTS
    assert events["additionalProperties"] is False
    assert terminal == ["PROBE_PASSED", *EXPECTED_FAILURE_CATEGORIES]
    assert tls_error == ["NONE", *EXPECTED_FAILURE_CATEGORIES]
    assert "TLS_FAILED" not in terminal
    assert "TLS_FAILED" not in tls_error


def test_receipt_schema_forbids_provider_ai_secret_and_http_activity(
    contracts: dict[str, dict[str, Any]],
) -> None:
    properties = contracts["child_receipt"]["properties"]
    assert properties["provider_attempt_count"] == {"const": 0}
    assert properties["ai_call_count"] == {"const": 0}
    assert properties["secret_content_read"] == {"const": False}
    assert properties["authorization_constructed"] == {"const": False}
    assert properties["http_request_sent"] == {"const": False}
    assert properties["business_body_sent"] == {"const": False}
    serialized = json.dumps(contracts["child_receipt"], sort_keys=True).lower()
    assert "api_key" not in serialized
    assert "bearer" not in serialized


def test_ready_marker_schema_fixes_publisher_and_mode(
    contracts: dict[str, dict[str, Any]],
) -> None:
    marker = contracts["ready_marker"]["properties"]
    assert marker["publisher_uid"] == {"const": 65532}
    assert marker["publisher_gid"] == {"const": 65532}
    assert marker["directory_uid"] == {"const": 65532}
    assert marker["directory_gid"] == {"const": 65532}
    assert marker["directory_mode"] == {"const": "0700"}
    assert marker["publication_complete"] == {"const": True}


def test_path_validation_contract_allows_only_verified_macos_alias(
    contracts: dict[str, dict[str, Any]],
) -> None:
    contract = contracts["path_validation"]
    assert contract["trusted_system_aliases"] == [
        {"logical": "/var", "canonical": "/private/var", "platform": "darwin"}
    ]
    assert contract["resolution_order"] == [
        "resolve_trusted_system_temp_root",
        "assert_controlled_path_is_within_canonical_root",
        "reject_controlled_path_symlinks",
        "reject_non_regular_files",
        "reject_unexpected_output_names",
    ]
    assert contract["user_symlinks_allowed"] is False
    assert contract["path_escape_allowed"] is False
    assert contract["unexpected_parent_allowed"] is False


def test_receipt_transport_contract_is_durable_and_minimal(
    contracts: dict[str, dict[str, Any]],
) -> None:
    contract = contracts["receipt_transport"]
    assert contract["container_receipt_path"] == "/output/child-receipt.json"
    assert contract["ready_marker_path"] == "/output/receipt.ready"
    assert contract["mount"] == {
        "type": "bind",
        "destination": "/output",
        "source_kind": "dedicated_empty_temporary_directory",
        "read_only": False,
        "other_writable_bind_mounts": 0,
    }
    assert contract["container_uid"] == 65532
    assert contract["container_gid"] == 65532
    assert contract["directory_mode"] == "0700"
    assert contract["receipt_mode"] == "0600"
    assert contract["ready_marker_mode"] == "0600"
    assert contract["publication_order"] == [
        "write_temp",
        "flush_temp",
        "fsync_temp",
        "atomic_rename_same_directory",
        "fsync_parent_directory",
        "publish_ready_marker_durably",
        "process_exit_zero",
    ]
    assert contract["host_lifecycle_order"] == [
        "start_container",
        "wait_container",
        "capture_exit_code",
        "inspect_receipt_location",
        "validate_receipt_bundle",
        "archive_receipt_bundle",
        "persist_terminal_evidence",
        "cleanup_runtime",
    ]
    assert contract["archive_completed_before_cleanup_started"] is True
    assert contract["exit_zero_implies_durable_receipt_bundle"] is True


def test_tls_contract_is_fixed_and_http_free(
    contracts: dict[str, dict[str, Any]],
) -> None:
    contract = contracts["tls"]
    assert contract["target_host"] == "ark.cn-beijing.volces.com"
    assert contract["target_port"] == 443
    assert contract["sni_hostname"] == "ark.cn-beijing.volces.com"
    assert (
        contract["hostname_verification_target"]
        == "ark.cn-beijing.volces.com"
    )
    assert contract["ca_source"] == "/etc/ssl/certs/ca-certificates.crt"
    assert contract["tls_minimum"] == "TLSv1_2"
    assert contract["tls_maximum"] == "MAXIMUM_SUPPORTED"
    assert contract["verify_mode"] == "CERT_REQUIRED"
    assert contract["check_hostname"] is True
    assert contract["retry_count"] == 0
    assert contract["maximum_probe_attempts"] == 1
    assert contract["allowed_operations"] == [
        "dns_resolution",
        "tcp_connect",
        "tls_handshake",
        "certificate_verification",
        "hostname_verification",
        "clean_tls_close",
    ]
    assert contract["forbidden_operations"] == [
        "http_get",
        "http_head",
        "http_post",
        "ark_responses_api",
        "authorization",
        "api_key_read",
        "prompt",
        "facts",
        "projection",
        "claims",
    ]
    assert contract["event_order"] == EXPECTED_EVENTS
    assert contract["failure_categories"] == EXPECTED_FAILURE_CATEGORIES


def _image_identity(builder_module: ModuleType) -> dict[str, Any]:
    return {
        "image_id": builder_module.PROBE_IMAGE_ID,
        "architecture": "amd64",
        "os": "linux",
        "repo_digests": [
            "tickflow-phase2-ark-egress-proxy@"
            + builder_module.PROBE_IMAGE_ID
        ],
        "labels": dict(builder_module.EXPECTED_IMAGE_LABELS),
    }


def _ca_identity() -> dict[str, Any]:
    return {
        "source": "/etc/ssl/certs/ca-certificates.crt",
        "sha256": "a" * 64,
        "size_bytes": 216591,
        "file_type": "regular",
    }


def test_builder_source_is_required() -> None:
    assert BUILDER_PATH.is_file(), "offline builder must be source controlled"


def test_canonical_json_bytes_are_deterministic(builder_module: ModuleType) -> None:
    left = builder_module.canonical_json_bytes({"b": 2, "a": [1, True]})
    right = builder_module.canonical_json_bytes({"a": [1, True], "b": 2})
    assert left == right == b'{"a":[1,true],"b":2}\n'


def test_source_binding_rejects_symlink_and_escape(
    builder_module: ModuleType,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "source.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    link = root / "link.py"
    link.symlink_to(source)
    binding = builder_module.source_binding(root, "source.py")
    assert binding == {
        "path": "source.py",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    with pytest.raises(ValueError, match="source_binding_symlink"):
        builder_module.source_binding(root, "link.py")
    with pytest.raises(ValueError, match="source_binding_escape"):
        builder_module.source_binding(root, "../outside.py")


def test_image_inspection_requires_exact_identity_and_labels(
    builder_module: ModuleType,
) -> None:
    payload = [
        {
            "Id": builder_module.PROBE_IMAGE_ID,
            "Architecture": "amd64",
            "Os": "linux",
            "RepoDigests": [
                "tickflow-phase2-ark-egress-proxy@"
                + builder_module.PROBE_IMAGE_ID
            ],
            "Config": {"Labels": dict(builder_module.EXPECTED_IMAGE_LABELS)},
        }
    ]

    def run_ok(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        assert command == ["docker", "image", "inspect", builder_module.PROBE_IMAGE_ID]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    identity = builder_module.inspect_probe_image(run=run_ok)
    assert identity == _image_identity(builder_module)
    payload[0]["Config"]["Labels"]["org.tickflow.phase2.base-image-digest"] = (
        "sha256:" + "0" * 64
    )
    with pytest.raises(ValueError, match="probe_image_labels_mismatch"):
        builder_module.inspect_probe_image(run=run_ok)


def test_ca_extraction_uses_stopped_network_none_container(
    builder_module: ModuleType,
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:2] == ["docker", "cp"]:
            destination = Path(command[-1])
            destination.write_bytes(b"trusted-ca-bundle")
        return subprocess.CompletedProcess(command, 0, "container-id\n", "")

    identity = builder_module.extract_ca_identity(
        run=fake_run,
        temporary_parent=tmp_path,
        container_name="phase2-ark-ca-test",
    )
    assert identity["source"] == "/etc/ssl/certs/ca-certificates.crt"
    assert identity["sha256"] == hashlib.sha256(b"trusted-ca-bundle").hexdigest()
    assert identity["size_bytes"] == len(b"trusted-ca-bundle")
    assert commands[0][:4] == [
        "docker",
        "create",
        "--name",
        "phase2-ark-ca-test",
    ]
    assert "--network" in commands[0]
    assert commands[0][commands[0].index("--network") + 1] == "none"
    assert [command[:2] for command in commands] == [
        ["docker", "create"],
        ["docker", "cp"],
        ["docker", "rm"],
    ]
    flattened = " ".join(part for command in commands for part in command)
    assert "docker run" not in flattened
    assert "docker start" not in flattened


def test_candidate_has_complete_bindings_and_fixed_probe_contract(
    builder_module: ModuleType,
) -> None:
    source_head = "1" * 40
    provenance = builder_module.build_probe_build_provenance(
        REPO_ROOT,
        source_git_head=source_head,
        image_identity=_image_identity(builder_module),
        ca_identity=_ca_identity(),
    )
    provenance_raw = builder_module.canonical_json_bytes(provenance)
    candidate = builder_module.build_probe_candidate(
        REPO_ROOT,
        source_git_head=source_head,
        image_identity=_image_identity(builder_module),
        ca_identity=_ca_identity(),
        build_provenance_sha256=hashlib.sha256(provenance_raw).hexdigest(),
    )
    assert set(candidate["artifact_hashes"]) == set(
        builder_module.REQUIRED_ARTIFACT_HASH_KEYS
    )
    assert set(candidate["source_bindings"]) == set(
        builder_module.REQUIRED_SOURCE_BINDING_KEYS
    )
    assert candidate["source_git_head"] == source_head
    assert candidate["target_host"] == "ark.cn-beijing.volces.com"
    assert candidate["target_port"] == 443
    assert candidate["sni_hostname"] == "ark.cn-beijing.volces.com"
    assert candidate["hostname_verification_target"] == (
        "ark.cn-beijing.volces.com"
    )
    assert candidate["retry_count"] == 0
    assert candidate["maximum_probe_attempts"] == 1
    assert candidate["provider_attempt_count"] == 0
    assert candidate["ai_call_count"] == 0
    assert candidate["approval_installed"] is False
    assert candidate["receipt_transport_root_cause"] == (
        "MACOS_VAR_PRIVATE_VAR_CANONICAL_ALIAS"
    )


def test_probe_scope_is_deterministic_available_and_provider_independent(
    builder_module: ModuleType,
    tmp_path: Path,
) -> None:
    candidate_raw = b'{"candidate":"probe-v2"}\n'
    first = builder_module.build_probe_scope(candidate_raw, attempts_root=tmp_path)
    second = builder_module.build_probe_scope(candidate_raw, attempts_root=tmp_path)
    assert first == second
    assert first["scope_type"] == "ark_tls_connectivity_probe_v2"
    assert first["historical_attempts"] == 0
    assert first["attempt_availability"] == "AVAILABLE"
    assert first["provider_attempt_count"] == 0
    assert first["ai_call_count"] == 0
    assert first["approval_scope_id"] != hashlib.sha256(
        b"single_symbol_ark_canary_runtime_v1"
    ).hexdigest()
    scope_dir = tmp_path / first["approval_scope_id"]
    scope_dir.mkdir()
    (scope_dir / "ledger.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="probe_scope_attempt_unavailable"):
        builder_module.build_probe_scope(candidate_raw, attempts_root=tmp_path)


def test_atomic_publication_preserves_old_file_on_replace_failure(
    builder_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "candidate.json"
    target.write_bytes(b"old\n")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace blocked")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace blocked"):
        builder_module.atomic_write_bytes(target, b"new\n")
    assert target.read_bytes() == b"old\n"


def test_git_source_gate_requires_clean_worktree_and_matching_fork(
    builder_module: ModuleType,
    tmp_path: Path,
) -> None:
    head = "1" * 40
    calls: list[tuple[list[str], Path]] = []

    def matched(
        command: list[str], *, cwd: Path, timeout: int, **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd))
        stdout = ""
        if command == ["git", "rev-parse", "HEAD"]:
            stdout = head + "\n"
        if command == [
            "git",
            "rev-parse",
            "refs/remotes/fork/codex/tickflow-phase2-ai-review",
        ]:
            stdout = head + "\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    assert builder_module.verify_git_source_state(tmp_path, run=matched) == head
    assert all(cwd == tmp_path for _, cwd in calls)

    def dirty(
        command: list[str], *, cwd: Path, timeout: int, **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, " M source.py\n", "")
        return matched(command, cwd=cwd, timeout=timeout)

    with pytest.raises(ValueError, match="git_worktree_dirty"):
        builder_module.verify_git_source_state(tmp_path, run=dirty)

    def mismatch(
        command: list[str], *, cwd: Path, timeout: int, **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        if command == [
            "git",
            "rev-parse",
            "refs/remotes/fork/codex/tickflow-phase2-ai-review",
        ]:
            return subprocess.CompletedProcess(command, 0, "2" * 40 + "\n", "")
        return matched(command, cwd=cwd, timeout=timeout)

    with pytest.raises(ValueError, match="git_fork_head_mismatch"):
        builder_module.verify_git_source_state(tmp_path, run=mismatch)


def test_published_artifacts_require_matching_generation_manifest(
    builder_module: ModuleType,
    tmp_path: Path,
) -> None:
    artifacts = {
        "build_provenance": {"value": 1},
        "approval_candidate": {"source_git_head": "1" * 40},
        "approval_scope": {"value": 3},
        "offline_preflight": {"value": 4},
    }

    builder_module.publish_offline_artifacts(tmp_path, artifacts)

    assert builder_module.read_published_artifacts(tmp_path) == artifacts
    manifest = _read_json(tmp_path / "artifact_generation.json")
    assert manifest["artifact_generation_schema_version"] == 1
    assert manifest["source_git_head"] == "1" * 40
    assert set(manifest["artifact_hashes"]) == {
        "approval_candidate.json",
        "approval_scope.json",
        "build_provenance.json",
        "offline_preflight.json",
    }
    (tmp_path / "approval_scope.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="probe_artifact_generation_mismatch"):
        builder_module.read_published_artifacts(tmp_path)


def test_interrupted_artifact_set_publication_fails_closed(
    builder_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = {
        "build_provenance": {"value": 1},
        "approval_candidate": {"source_git_head": "1" * 40},
        "approval_scope": {"value": 1},
        "offline_preflight": {"value": 1},
    }
    second = {
        "build_provenance": {"value": 2},
        "approval_candidate": {"source_git_head": "2" * 40},
        "approval_scope": {"value": 2},
        "offline_preflight": {"value": 2},
    }
    builder_module.publish_offline_artifacts(tmp_path, first)
    real_write = builder_module.atomic_write_bytes
    writes = 0

    def interrupted(path: Path, raw: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("publication interrupted")
        real_write(path, raw)

    monkeypatch.setattr(builder_module, "atomic_write_bytes", interrupted)
    with pytest.raises(OSError, match="publication interrupted"):
        builder_module.publish_offline_artifacts(tmp_path, second)
    with pytest.raises(ValueError, match="probe_artifact_generation_mismatch"):
        builder_module.read_published_artifacts(tmp_path)


def test_artifact_verification_rejects_source_mutation(
    builder_module: ModuleType,
) -> None:
    source_head = "2" * 40
    image = _image_identity(builder_module)
    ca = _ca_identity()
    artifacts = builder_module.build_offline_artifacts(
        REPO_ROOT,
        source_git_head=source_head,
        image_identity=image,
        ca_identity=ca,
    )
    builder_module.verify_offline_artifacts(
        REPO_ROOT,
        artifacts=artifacts,
        source_git_head=source_head,
        image_identity=image,
        ca_identity=ca,
    )
    tampered = json.loads(json.dumps(artifacts))
    tampered["approval_candidate"]["target_port"] = 444
    with pytest.raises(ValueError, match="probe_artifact_mismatch"):
        builder_module.verify_offline_artifacts(
            REPO_ROOT,
            artifacts=tampered,
            source_git_head=source_head,
            image_identity=image,
            ca_identity=ca,
        )


def test_historical_probe_baseline_verifies_exact_seven_files(
    builder_module: ModuleType,
    tmp_path: Path,
) -> None:
    history = tmp_path / "history"
    history.mkdir()
    first = history / "first.json"
    second = history / "second.json"
    first.write_bytes(b"first\n")
    second.write_bytes(b"second\n")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "baseline_schema_version": 1,
                "file_count": 2,
                "historical_probe_id": "5dd641ca9e4b4ccba1035fff4d695409",
                "historical_status": (
                    "PHASE2B_ARK_TLS_PROBE_UNKNOWN / PROCESS_ERROR / "
                    "CONSUMED / PRESERVED"
                ),
                "files": [
                    {
                        "path": "history/first.json",
                        "sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
                    },
                    {
                        "path": "history/second.json",
                        "sha256": hashlib.sha256(second.read_bytes()).hexdigest(),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    assert builder_module.verify_historical_probe_baseline(
        tmp_path, baseline, expected_file_count=2
    ) == 2
    second.write_bytes(b"mutated\n")
    with pytest.raises(ValueError, match="historical_probe_evidence_mutated"):
        builder_module.verify_historical_probe_baseline(
            tmp_path, baseline, expected_file_count=2
        )


def test_receipt_transport_evidence_requires_network_none_and_zero_activity(
    builder_module: ModuleType,
    tmp_path: Path,
) -> None:
    source_root = REPO_ROOT / "reports/phase2_provider_ark/tls_receipt_transport"
    verification = json.loads((source_root / "verification.json").read_text())
    harness = json.loads((source_root / "local_harness.json").read_text())
    verification_path = tmp_path / "verification.json"
    harness_path = tmp_path / "local_harness.json"
    verification_path.write_text(json.dumps(verification), encoding="utf-8")
    harness_path.write_text(json.dumps(harness), encoding="utf-8")
    summary = builder_module.verify_receipt_transport_evidence(
        verification_path=verification_path,
        harness_path=harness_path,
    )
    assert summary == {
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
    harness["scenarios"][0]["network_mode"] = "bridge"
    harness_path.write_text(json.dumps(harness), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_transport_evidence_invalid"):
        builder_module.verify_receipt_transport_evidence(
            verification_path=verification_path,
            harness_path=harness_path,
        )
