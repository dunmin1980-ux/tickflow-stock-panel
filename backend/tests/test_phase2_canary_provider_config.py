from __future__ import annotations

import json
import os
import socket
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import scripts.validate_phase2_canary_provider_config as canary_preflight
from app.schemas.phase2_canary_provider_config import (
    CanaryEgressPolicy,
    CanaryProviderApproval,
    build_responses_request_contract,
    validate_exact_egress,
)
from app.schemas.phase2_claims import WorkerClaimsCandidate
from scripts.validate_phase2_canary_provider_config import (
    CanaryPreflightError,
    count_secret_exposure,
    exercise_placeholder_secret_injection,
    inspect_runtime_proxy_policy,
    keychain_secret_exists,
    read_git_gate,
    read_provider_approval,
    read_runtime_proxy_image_identity,
    read_runtime_residue,
    run_canary_preflight,
    validate_offline_canary_inputs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _approved_config() -> dict[str, Any]:
    return {
        "config_version": 1,
        "provider_id": "openai",
        "exact_model_id": "gpt-5.6-terra",
        "approved_endpoint_alias": "openai_responses_v1",
        "endpoint_host": "api.openai.com",
        "endpoint_port": 443,
        "endpoint_path": "/v1/responses",
        "http_method": "POST",
        "tls_verification": True,
        "follow_redirects": False,
        "response_mode": "json_schema_strict",
        "streaming": False,
        "tools_enabled": False,
        "web_browsing_enabled": False,
        "file_tools_enabled": False,
        "retry_count": 0,
        "max_provider_attempts": 1,
        "approved_symbol": "000403.SZ",
        "secret_source": "macos_keychain",
        "secret_service": "tickflow-phase2-canary-openai",
        "can_publish": False,
    }


def _walk_schema(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_schema(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_schema(child)


def _write_approval(path: Path, payload: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(
        json.dumps(payload or _approved_config(), sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _clean_git_result(command: list[str]) -> subprocess.CompletedProcess:
    if command[-2:] == ["rev-parse", "HEAD"]:
        stdout = "c" * 40 + "\n"
    elif command[-2:] == ["branch", "--show-current"]:
        stdout = "codex/tickflow-phase2-ai-review\n"
    elif (
        command[-3:] == ["status", "--porcelain", "--untracked-files=all"]
        or "merge-base" in command
    ):
        stdout = ""
    elif "diff" in command:
        stdout = (
            "A\tbackend/app/schemas/phase2_canary_provider_config.py\n"
            "A\tbackend/scripts/validate_phase2_canary_provider_config.py\n"
        )
    else:
        raise AssertionError(f"unexpected git command: {command}")
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def test_exact_provider_approval_is_accepted_and_frozen() -> None:
    approval = CanaryProviderApproval.model_validate(_approved_config())

    assert approval.model_dump(mode="json") == _approved_config()
    with pytest.raises(ValidationError):
        approval.exact_model_id = "gpt-5.5"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("config_version", 2),
        ("provider_id", "openai_compat"),
        ("exact_model_id", "gpt-5.5"),
        ("exact_model_id", "latest"),
        ("approved_endpoint_alias", "default"),
        ("endpoint_host", "example.invalid"),
        ("endpoint_port", 80),
        ("endpoint_path", "/v1/chat/completions"),
        ("http_method", "GET"),
        ("tls_verification", False),
        ("follow_redirects", True),
        ("response_mode", "json"),
        ("streaming", True),
        ("tools_enabled", True),
        ("web_browsing_enabled", True),
        ("file_tools_enabled", True),
        ("retry_count", 1),
        ("max_provider_attempts", 2),
        ("approved_symbol", "600489.SH"),
        ("secret_source", "environment"),
        ("secret_service", "another-service"),
        ("can_publish", True),
    ],
)
def test_provider_approval_rejects_every_relaxed_or_alternate_value(
    field: str,
    value: object,
) -> None:
    payload = _approved_config()
    payload[field] = value

    with pytest.raises(ValidationError):
        CanaryProviderApproval.model_validate(payload)


def test_provider_approval_rejects_missing_and_unknown_fields() -> None:
    missing = _approved_config()
    missing.pop("exact_model_id")
    extra = _approved_config()
    extra["api_url"] = "https://example.invalid"

    with pytest.raises(ValidationError):
        CanaryProviderApproval.model_validate(missing)
    with pytest.raises(ValidationError):
        CanaryProviderApproval.model_validate(extra)


def test_responses_contract_uses_typed_candidate_schema_without_mutating_it() -> None:
    approval = CanaryProviderApproval.model_validate(_approved_config())
    host_schema_before = deepcopy(
        WorkerClaimsCandidate.model_json_schema(mode="validation")
    )

    request = build_responses_request_contract(approval)

    assert request["model"] == "gpt-5.6-terra"
    assert request["stream"] is False
    assert request["tools"] == []
    assert request["text"]["format"]["type"] == "json_schema"
    assert request["text"]["format"]["name"] == (
        "tickflow_phase2_claims_candidate"
    )
    assert request["text"]["format"]["strict"] is True
    assert WorkerClaimsCandidate.model_json_schema(
        mode="validation"
    ) == host_schema_before


def test_responses_schema_is_closed_required_and_provider_compatible() -> None:
    approval = CanaryProviderApproval.model_validate(_approved_config())
    schema = build_responses_request_contract(approval)["text"]["format"][
        "schema"
    ]

    assert schema["type"] == "object"
    for item in _walk_schema(schema):
        if not isinstance(item, dict):
            continue
        assert "default" not in item
        assert "discriminator" not in item
        assert "oneOf" not in item
        if item.get("type") == "object" or "properties" in item:
            properties = item.get("properties", {})
            assert item.get("additionalProperties") is False
            assert item.get("required") == list(properties)


def test_egress_policy_is_exact_and_rejects_every_bypass() -> None:
    approval = CanaryProviderApproval.model_validate(_approved_config())
    policy = CanaryEgressPolicy.from_approval(approval)

    assert validate_exact_egress(
        policy,
        scheme="https",
        host="api.openai.com",
        port=443,
        path="/v1/responses",
        method="POST",
        tls_verification=True,
        follow_redirects=False,
    )
    rejected = [
        {"scheme": "http"},
        {"host": "*.openai.com"},
        {"host": "127.0.0.1"},
        {"host": "host.docker.internal"},
        {"port": 80},
        {"path": "/v1/chat/completions"},
        {"method": "GET"},
        {"tls_verification": False},
        {"follow_redirects": True},
    ]
    baseline = {
        "scheme": "https",
        "host": "api.openai.com",
        "port": 443,
        "path": "/v1/responses",
        "method": "POST",
        "tls_verification": True,
        "follow_redirects": False,
    }
    for mutation in rejected:
        attempt = {**baseline, **mutation}
        assert validate_exact_egress(policy, **attempt) is False

    assert policy.allow_response_url_navigation is False
    assert policy.allow_arbitrary_public_ip is False
    assert policy.allow_host_access is False
    assert policy.allow_docker_control is False


def test_secure_approval_reader_returns_only_validated_public_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    _write_approval(path)

    loaded = read_provider_approval(path, current_uid=os.getuid())

    assert loaded.approval.model_dump(mode="json") == _approved_config()
    assert loaded.directory_mode == "0700"
    assert loaded.file_mode == "0600"
    assert loaded.owner_matches is True
    assert loaded.parents_not_symlinks is True
    assert loaded.config_secret_scan_clean is True


def test_approval_reader_rejects_missing_config(tmp_path: Path) -> None:
    path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"

    with pytest.raises(CanaryPreflightError, match="config_missing"):
        read_provider_approval(path, current_uid=os.getuid())


def test_approval_reader_rejects_config_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real/provider-approval.json"
    _write_approval(real)
    path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    path.parent.mkdir(mode=0o700)
    path.symlink_to(real)

    with pytest.raises(CanaryPreflightError, match="config_symlink_forbidden"):
        read_provider_approval(path, current_uid=os.getuid())


def test_approval_reader_rejects_symlinked_parent(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real = real_dir / "provider-approval.json"
    _write_approval(real)
    linked_dir = tmp_path / "TickFlowPhase2Canary"
    linked_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(CanaryPreflightError, match="config_parent_symlink_forbidden"):
        read_provider_approval(
            linked_dir / "provider-approval.json",
            current_uid=os.getuid(),
        )


@pytest.mark.parametrize(
    ("directory_mode", "file_mode", "current_uid", "error_code"),
    [
        (0o755, 0o600, os.getuid(), "config_directory_mode_invalid"),
        (0o700, 0o644, os.getuid(), "config_file_mode_invalid"),
        (0o700, 0o600, os.getuid() + 1, "config_owner_invalid"),
    ],
)
def test_approval_reader_rejects_unsafe_permissions_or_owner(
    tmp_path: Path,
    directory_mode: int,
    file_mode: int,
    current_uid: int,
    error_code: str,
) -> None:
    path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    _write_approval(path)
    path.parent.chmod(directory_mode)
    path.chmod(file_mode)

    with pytest.raises(CanaryPreflightError, match=error_code):
        read_provider_approval(path, current_uid=current_uid)


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "sk-test-value",
        "Authorization",
        "Bearer token",
        "api_key",
        "api-key",
        "secret_value",
        "OPENAI_API_KEY",
    ],
)
def test_approval_reader_rejects_every_sensitive_shape_before_schema_parse(
    tmp_path: Path,
    sensitive_value: str,
) -> None:
    path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    payload = _approved_config()
    payload["note"] = sensitive_value
    _write_approval(path, payload)

    with pytest.raises(CanaryPreflightError, match="config_secret_shape_detected"):
        read_provider_approval(path, current_uid=os.getuid())


def test_approval_reader_rejects_malformed_or_non_contract_json(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "malformed/provider-approval.json"
    _write_approval(malformed)
    malformed.write_text("{", encoding="utf-8")
    malformed.chmod(0o600)
    alternate = tmp_path / "alternate/provider-approval.json"
    payload = _approved_config()
    payload["provider_id"] = "openai_compat"
    _write_approval(alternate, payload)

    with pytest.raises(CanaryPreflightError, match="config_json_invalid"):
        read_provider_approval(malformed, current_uid=os.getuid())
    with pytest.raises(CanaryPreflightError, match="config_contract_invalid"):
        read_provider_approval(alternate, current_uid=os.getuid())


def test_keychain_gate_checks_only_entry_existence_and_discards_output() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    assert keychain_secret_exists(
        account="macbookpro",
        service="tickflow-phase2-canary-openai",
        runner=runner,
    )
    assert calls == [
        (
            [
                "security",
                "find-generic-password",
                "-a",
                "macbookpro",
                "-s",
                "tickflow-phase2-canary-openai",
            ],
            {
                "check": False,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "text": False,
                "shell": False,
                "timeout": 5,
            },
        )
    ]
    assert "-w" not in calls[0][0]


def test_keychain_gate_returns_false_without_reading_missing_secret() -> None:
    def runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 44)

    assert keychain_secret_exists(
        account="macbookpro",
        service="tickflow-phase2-canary-openai",
        runner=runner,
    ) is False


def test_placeholder_secret_injection_is_single_file_readonly_and_cleans_up() -> None:
    approval = CanaryProviderApproval.model_validate(_approved_config())

    evidence = exercise_placeholder_secret_injection(REPO_ROOT, approval)

    assert evidence.to_dict() == {
        "secret_present": True,
        "secret_injection_mode": "temporary_readonly_single_file",
        "secret_file_regular": True,
        "secret_file_mode": "0600",
        "proxy_readonly_mount": True,
        "relay_has_secret_mount": False,
        "runtime_secret_boundary_verified": True,
        "secret_value_logged": False,
        "secret_hash_recorded": False,
        "secret_artifact_hit_count": 0,
        "secret_log_hit_count": 0,
        "workspace_hit_count": 0,
        "secret_cleanup": "PASSED",
        "secret_file_residue_count": 0,
        "temporary_directory_residue_count": 0,
    }


def test_secret_exposure_scanner_detects_inspect_log_or_artifact_leaks() -> None:
    placeholder = "".join(("PHASE2_", "TEST_SECRET_", "DO_NOT_USE"))

    assert count_secret_exposure(
        placeholder,
        [
            {"Config": {"Env": [f"TOKEN={placeholder}"]}},
            f"stderr contained {placeholder}",
            b"clean",
        ],
    ) == 2
    assert count_secret_exposure(placeholder, [{"secret_present": True}, ""]) == 0


def test_runtime_residue_reader_is_read_only_and_reports_clean_state() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    evidence = read_runtime_residue(runner=runner)

    assert evidence.to_dict() == {
        "provider_container_residue_count": 0,
        "provider_network_residue_count": 0,
        "provider_process_residue_count": 0,
    }
    assert calls == [
        ["docker", "ps", "-a", "--format", "{{.Image}}\t{{.Names}}"],
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        ["ps", "-axo", "command="],
    ]


def test_runtime_residue_reader_counts_only_phase2_provider_runtime() -> None:
    outputs = iter(
        [
            "tickflow-phase2-provider-relay:runtime-v1\tphase2-relay-1\n"
            "redis:7\tredis\n",
            "phase2-provider-net\nbridge\n",
            "python phase2-mock-provider.py\npython unrelated.py\n",
        ]
    )

    def runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=next(outputs),
            stderr="",
        )

    evidence = read_runtime_residue(runner=runner)

    assert evidence.provider_container_residue_count == 1
    assert evidence.provider_network_residue_count == 1
    assert evidence.provider_process_residue_count == 1


def test_git_gate_binds_preflight_to_clean_allowed_branch_delta() -> None:
    current_head = "c" * 40
    calls: list[list[str]] = []
    outputs = iter(
        [
            subprocess.CompletedProcess([], 0, stdout=current_head + "\n", stderr=""),
            subprocess.CompletedProcess(
                [],
                0,
                stdout="codex/tickflow-phase2-ai-review\n",
                stderr="",
            ),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    "A\tbackend/app/schemas/phase2_canary_provider_config.py\n"
                    "A\tbackend/scripts/validate_phase2_canary_provider_config.py\n"
                ),
                stderr="",
            ),
        ]
    )

    def runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(command)
        result = next(outputs)
        result.args = command
        return result

    evidence = read_git_gate(REPO_ROOT, runner=runner)

    assert evidence.ready is True
    assert evidence.base_head == (
        "b5c7e7bf3833f63c706535da9e1be818ddb2ec4f"
    )
    assert evidence.current_head == current_head
    assert evidence.branch == "codex/tickflow-phase2-ai-review"
    assert evidence.worktree_clean is True
    assert evidence.base_is_ancestor is True
    assert evidence.changed_paths_allowed is True
    assert any(
        command[-3:] == ["status", "--porcelain", "--untracked-files=all"]
        for command in calls
    )


def test_git_gate_rejects_dirty_or_unapproved_drift() -> None:
    outputs = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="c" * 40 + "\n", stderr=""),
            subprocess.CompletedProcess(
                [],
                0,
                stdout="codex/tickflow-phase2-ai-review\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                stdout=" M backend/app/main.py\n",
                stderr="",
            ),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(
                [],
                0,
                stdout="M\tbackend/app/main.py\n",
                stderr="",
            ),
        ]
    )

    evidence = read_git_gate(
        REPO_ROOT,
        runner=lambda command, **_kwargs: next(outputs),
    )

    assert evidence.ready is False
    assert evidence.worktree_clean is False
    assert evidence.changed_paths_allowed is False


def test_actual_runtime_proxy_is_mock_only_and_blocks_openai_canary() -> None:
    evidence = inspect_runtime_proxy_policy(
        REPO_ROOT / "docker/phase2-egress-proxy/proxy.py"
    )

    assert evidence.status == "MOCK_ONLY"
    assert evidence.approved_endpoint_enforced is False
    assert evidence.actual_host == "phase2-mock-provider"
    assert evidence.actual_port == 8081
    assert evidence.actual_path == "/v1/typed-claims"
    assert evidence.connection_type == "HTTPConnection"
    assert evidence.tls_verification_enforced is False


def test_unpinned_https_proxy_cannot_self_approve_with_dead_constants(
    tmp_path: Path,
) -> None:
    proxy = tmp_path / "proxy.py"
    proxy.write_text(
        "\n".join(
            [
                "import http.client",
                "import ssl",
                'ALLOWED_METHOD = "POST"',
                'UPSTREAM_HOST = "api.openai.com"',
                "UPSTREAM_PORT = 443",
                'UPSTREAM_PATH = "/v1/responses"',
                "FOLLOW_REDIRECTS = False",
                "RETRY_COUNT = 0",
                "context = ssl.create_default_context()",
                "connection = http.client.HTTPSConnection(",
                "    UPSTREAM_HOST, UPSTREAM_PORT, context=context",
                ")",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    evidence = inspect_runtime_proxy_policy(proxy)

    assert evidence.status == "UNPINNED"
    assert evidence.approved_endpoint_enforced is False
    assert evidence.artifact_hash_approved is False
    assert evidence.connection_type == "HTTPSConnection"
    assert evidence.tls_verification_enforced is True


def test_runtime_proxy_requires_exact_source_image_and_label_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = tmp_path / "proxy.py"
    proxy.write_text(
        "\n".join(
            [
                "import http.client",
                "import ssl",
                'ALLOWED_METHOD = "POST"',
                'UPSTREAM_HOST = "api.openai.com"',
                "UPSTREAM_PORT = 443",
                'UPSTREAM_PATH = "/v1/responses"',
                "FOLLOW_REDIRECTS = False",
                "RETRY_COUNT = 0",
                "context = ssl.create_default_context()",
                "connection = http.client.HTTPSConnection(",
                "    UPSTREAM_HOST, UPSTREAM_PORT, context=context",
                ")",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    source_sha256 = canary_preflight._sha256(proxy)
    image_digest = "sha256:" + "d" * 64
    monkeypatch.setattr(
        canary_preflight,
        "_APPROVED_RUNTIME_PROXY_SHA256",
        source_sha256,
    )
    monkeypatch.setattr(
        canary_preflight,
        "_APPROVED_RUNTIME_PROXY_IMAGE_DIGEST",
        image_digest,
    )

    ready = inspect_runtime_proxy_policy(
        proxy,
        image_digest=image_digest,
        image_source_sha256=source_sha256,
    )
    wrong_image = inspect_runtime_proxy_policy(
        proxy,
        image_digest="sha256:" + "e" * 64,
        image_source_sha256=source_sha256,
    )
    proxy.write_text(proxy.read_text(encoding="utf-8") + "# one byte drift\n")
    changed_source = inspect_runtime_proxy_policy(
        proxy,
        image_digest=image_digest,
        image_source_sha256=source_sha256,
    )

    assert ready.status == "READY"
    assert ready.approved_endpoint_enforced is True
    assert ready.artifact_hash_approved is True
    assert ready.image_digest_approved is True
    assert ready.image_source_hash_matches is True
    assert wrong_image.status == "IMAGE_UNVERIFIED"
    assert wrong_image.approved_endpoint_enforced is False
    assert changed_source.status == "UNPINNED"
    assert changed_source.approved_endpoint_enforced is False


def test_runtime_proxy_image_identity_reads_only_digest_and_source_label() -> None:
    image_digest = "sha256:" + "d" * 64
    source_sha256 = "a" * 64
    observed: list[str] = []

    def runner(
        command: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess:
        observed.extend(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{image_digest}\t{source_sha256}\n",
            stderr="",
        )

    assert read_runtime_proxy_image_identity(runner=runner) == (
        image_digest,
        source_sha256,
    )
    assert observed[:4] == [
        "docker",
        "image",
        "inspect",
        "tickflow-phase2-egress-proxy:runtime-v1",
    ]
    assert "{{json .}}" not in observed
    assert "Config.Env" not in " ".join(observed)


def test_offline_canary_inputs_rebuild_projection_and_preserve_all_evidence() -> None:
    evidence = validate_offline_canary_inputs(REPO_ROOT)

    assert evidence.status == "READY"
    assert evidence.errors == []
    assert evidence.facts_status == "VALID"
    assert evidence.trade_date == "2026-07-31"
    assert evidence.facts_sha256 == (
        "adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956"
    )
    assert evidence.projection_status == "VALID"
    assert evidence.projection_repeat_stable is True
    assert evidence.projection_sha256 == (
        "0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f"
    )
    assert evidence.projection_bytes_sha256 == (
        "5db35e91eabce452e5be3a3020b5c10711797941431df9bd5bb52f67625b42e1"
    )
    assert evidence.claims_schema_sha256 == (
        "5988b34ad5bb1703e3795e9b54a18c7dcdb0009c65ee55662f7e71facebc43c3"
    )
    assert evidence.renderer_sha256 == (
        "53d94e60fce7914582c85beea977ca65f7a6713c7066ee65df2c84a8ec6c361e"
    )
    assert evidence.provider_relay_status == "PHASE2B_PROVIDER_RELAY_READY"
    assert evidence.canary_output_empty is True
    assert evidence.provider_attempt_count == 0
    assert evidence.ai_call_count == 0
    assert evidence.tickflow_api_request_count == 0
    assert evidence.real_public_network_success_count == 0


def test_offline_canary_protected_evidence_matches_nine_frozen_sets() -> None:
    protected = validate_offline_canary_inputs(REPO_ROOT).protected_evidence

    assert protected == {
        "phase1_observation": {
            "file_count": 36,
            "sha256": "8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5",
            "status": "UNCHANGED",
        },
        "phase1_request_audits": {
            "file_count": 7,
            "sha256": "dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d",
            "status": "UNCHANGED",
        },
        "phase2_facts": {
            "file_count": 4,
            "sha256": "10c6664754b8e1391f55d9b1b8b98f1577b749f0dceafbb77288749ae5376ef7",
            "status": "UNCHANGED",
        },
        "typed_claims": {
            "file_count": 8,
            "sha256": "39ef9b714798f193c3ba283c1035fad42372cedf4f7a17a36fc008411ed41bbf",
            "status": "UNCHANGED",
        },
        "typed_inbox": {
            "file_count": 3,
            "sha256": "a9bfddecc5a9046badf5f7cf4485fab029508681dae8a5a5206a93d4ab2e2291",
            "status": "UNCHANGED",
        },
        "historical_ai_markdown": {
            "file_count": 3,
            "sha256": "9a0bc445f7580c55db630b8e3fbfac545fe71d54b36588919dd7ec79ef57feb5",
            "status": "UNCHANGED",
        },
        "historical_rejected": {
            "file_count": 3,
            "sha256": "6e2e0c7b311a76ccff061917f3893f97aca4ba84782c769ee004960d335f164c",
            "status": "UNCHANGED",
        },
        "isolation_runtime_evidence": {
            "file_count": 1,
            "sha256": "9cf174d4def6908e84522366105b41ed33a00f3be8aab9dc01454c4f75f5533c",
            "status": "UNCHANGED",
        },
        "provider_relay_evidence": {
            "file_count": 1,
            "sha256": "4ae713b5ad4641a2c4f5f84f0d86bc6cdf5e309bcc23aab7fe596f6138fd897e",
            "status": "UNCHANGED",
        },
    }


def test_combined_preflight_blocks_mock_only_proxy_without_opening_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    _write_approval(config_path)

    def keychain_runner(
        command: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 0)

    def runtime_runner(
        command: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def forbid_socket(*_args: Any, **_kwargs: Any) -> socket.socket:
        raise AssertionError("network socket forbidden in preflight")

    monkeypatch.setattr(socket, "socket", forbid_socket)

    result = run_canary_preflight(
        repo_root=REPO_ROOT,
        config_path=config_path,
        account="macbookpro",
        current_uid=os.getuid(),
        keychain_runner=keychain_runner,
        runtime_runner=runtime_runner,
        git_runner=lambda command, **_kwargs: _clean_git_result(command),
    )

    assert result.status == "PHASE2B_CANARY_EGRESS_BLOCKED"
    assert result.errors == ["runtime_proxy_policy_not_approved"]
    assert result.provider == "openai"
    assert result.exact_model == "gpt-5.6-terra"
    assert result.endpoint_alias == "openai_responses_v1"
    assert result.endpoint == "api.openai.com:443/v1/responses"
    assert result.provider_config == "VALID"
    assert result.config_permissions == "PASSED"
    assert result.config_secret_scan == "CLEAN"
    assert result.keychain_secret == "PRESENT"
    assert result.secret_content_read is False
    assert result.secret_hash_recorded is False
    assert result.strict_json_schema == "READY"
    assert result.tls == "BLOCKED"
    assert result.redirect == "DISABLED"
    assert result.egress_allowlist == "FAILED"
    assert result.provider_attempt_count == 0
    assert result.ai_call_count == 0
    assert result.retry_count == 0
    assert result.provider_http == "NOT_RUN"
    assert result.tickflow_api_request_count == 0
    assert result.real_public_network_success_count == 0
    assert result.external_action_evidence == (
        "DERIVED_FROM_UNCHANGED_AUDITS_AND_ABSENT_CANARY_RUNTIME"
    )


def test_combined_preflight_missing_keychain_item_requires_user_action(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    _write_approval(config_path)

    def missing_keychain(
        command: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 44)

    result = run_canary_preflight(
        repo_root=REPO_ROOT,
        config_path=config_path,
        account="macbookpro",
        current_uid=os.getuid(),
        keychain_runner=missing_keychain,
        runtime_runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout="",
            stderr="",
        ),
        git_runner=lambda command, **_kwargs: _clean_git_result(command),
    )

    assert result.status == "PHASE2B_AI_USER_ACTION_REQUIRED"
    assert result.keychain_secret == "MISSING"
    assert result.provider_attempt_count == 0
    assert result.ai_call_count == 0
    assert result.provider_http == "NOT_RUN"


def test_combined_preflight_result_is_sanitized_and_path_free(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    _write_approval(config_path)

    result = run_canary_preflight(
        repo_root=REPO_ROOT,
        config_path=config_path,
        account="macbookpro",
        current_uid=os.getuid(),
        keychain_runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
        ),
        runtime_runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout="",
            stderr="",
        ),
        git_runner=lambda command, **_kwargs: _clean_git_result(command),
    )
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    placeholder = "".join(("PHASE2_", "TEST_SECRET_", "DO_NOT_USE"))

    assert str(REPO_ROOT) not in serialized
    assert str(Path.home()) not in serialized
    assert str(tmp_path) not in serialized
    assert placeholder not in serialized
    assert "Authorization" not in serialized
    assert "Bearer " not in serialized


def test_combined_preflight_sanitizes_runtime_probe_failure(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    _write_approval(config_path)

    def fail_runtime(
        _command: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess:
        raise OSError(f"private path must not escape: {tmp_path}")

    result = run_canary_preflight(
        repo_root=REPO_ROOT,
        config_path=config_path,
        account="macbookpro",
        current_uid=os.getuid(),
        keychain_runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
        ),
        runtime_runner=fail_runtime,
        git_runner=lambda command, **_kwargs: _clean_git_result(command),
    )
    serialized = json.dumps(result.to_dict(), sort_keys=True)

    assert result.status == "PHASE2B_CANARY_CONFIG_BLOCKED"
    assert result.errors == ["runtime_residue_probe_failed"]
    assert str(tmp_path) not in serialized
    assert result.tls == "NOT_RUN"
    assert result.egress_allowlist == "NOT_RUN"
    assert result.provider_attempt_count == 0
    assert result.ai_call_count == 0


def test_combined_preflight_sanitizes_git_probe_failure(tmp_path: Path) -> None:
    private_path = tmp_path / "private-git-state"

    def fail_git(
        _command: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess:
        raise OSError(f"private path must not escape: {private_path}")

    result = run_canary_preflight(
        repo_root=REPO_ROOT,
        config_path=tmp_path / "not-read.json",
        account="macbookpro",
        current_uid=os.getuid(),
        git_runner=fail_git,
    )
    serialized = json.dumps(result.to_dict(), sort_keys=True)

    assert result.status == "PHASE2B_CANARY_CONFIG_BLOCKED"
    assert result.errors == ["git_gate_probe_failed"]
    assert str(private_path) not in serialized
    assert result.provider_attempt_count == 0
    assert result.ai_call_count == 0
