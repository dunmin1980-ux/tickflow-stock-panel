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
from app.services.phase2_openai_proxy_artifact import (
    OpenAIProxyArtifactCandidate,
)
from scripts.validate_phase2_canary_provider_config import (
    CanaryPreflightError,
    ProxyArtifactApproval,
    RestrictedCanaryRuntimeRunner,
    count_secret_exposure,
    exercise_placeholder_secret_injection,
    keychain_secret_exists,
    read_git_gate,
    read_provider_approval,
    read_proxy_artifact_approval,
    read_runtime_residue,
    run_canary_preflight,
    runtime_candidate_path_hash_allowlist,
    validate_offline_canary_inputs,
    validate_proxy_artifact_approval,
    validate_runtime_candidate_hash_allowlist,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _stub_runtime_candidate_hash_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        canary_preflight,
        "validate_runtime_candidate_hash_allowlist",
        lambda _root, _candidate: {},
    )


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
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(
        json.dumps(payload or _approved_config(), sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _artifact_candidate() -> dict[str, Any]:
    return json.loads(
        (
            REPO_ROOT
            / "reports/phase2_openai_proxy_artifact/approval_candidate.json"
        ).read_text(encoding="utf-8")
    )


def _artifact_approval() -> dict[str, Any]:
    return {**_artifact_candidate(), "approval_status": "APPROVED"}


def _artifact_candidate_model() -> OpenAIProxyArtifactCandidate:
    return OpenAIProxyArtifactCandidate.model_validate_json(
        json.dumps(_artifact_candidate())
    )


def _artifact_approval_model(
    payload: dict[str, Any] | None = None,
) -> ProxyArtifactApproval:
    return ProxyArtifactApproval.model_validate_json(
        json.dumps(payload or _artifact_approval())
    )


def _write_artifact_approval(
    path: Path,
    payload: dict[str, Any] | None = None,
) -> None:
    _write_approval(path, payload or _artifact_approval())


def _expected_git_diff() -> str:
    return "".join(
        f"{status}\t{path}\n"
        for path, status in canary_preflight._ALLOWED_PREFLIGHT_DELTA.items()
    )


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
        stdout = _expected_git_diff()
    else:
        raise AssertionError(f"unexpected git command: {command}")
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def _artifact_image_inspect() -> list[dict[str, Any]]:
    candidate = _artifact_candidate()
    base_layers = ["sha256:" + str(index % 10) * 64 for index in range(43)]
    return [
        {
            "Id": candidate["image_id"],
            "RepoTags": [candidate["image_name"]],
            "RootFS": {
                "Type": "layers",
                "Layers": [
                    *base_layers,
                    *("sha256:" + value * 64 for value in "abcdef"),
                ],
            },
            "Config": {
                "User": candidate["runtime_user"],
                "Entrypoint": candidate["entrypoint"],
                "Cmd": None,
                "Env": [
                    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
                    "LANG=C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "PYTHONHASHSEED=0",
                ],
                "Labels": {
                    "org.tickflow.phase2.base-image-digest": candidate[
                        "base_image_digest"
                    ],
                    "org.tickflow.phase2.proxy-source-sha256": candidate[
                        "proxy_source_sha256"
                    ],
                    "org.tickflow.phase2.responses-contract-sha256": candidate[
                        "responses_contract_sha256"
                    ],
                    "org.tickflow.phase2.proxy-policy-sha256": candidate[
                        "proxy_policy_sha256"
                    ],
                    "org.tickflow.phase2.runtime-contract-sha256": (
                        canary_preflight.compute_artifact_input_hashes(
                            REPO_ROOT
                        ).runtime_contract_sha256
                    ),
                },
            },
        }
    ]


def _artifact_base_inspect() -> list[dict[str, Any]]:
    candidate = _artifact_candidate()
    return [
        {
            "Id": candidate["base_image_digest"],
            "RepoDigests": [
                "gcr.io/distroless/python3-debian12@"
                + candidate["base_image_digest"]
            ],
            "RootFS": {
                "Type": "layers",
                "Layers": [
                    "sha256:" + str(index % 10) * 64 for index in range(43)
                ],
            },
        }
    ]


def _artifact_runtime_runner(
    calls: list[list[str]],
):
    def runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(list(command))
        if command[:4] == [
            "docker",
            "image",
            "inspect",
            (
                "gcr.io/distroless/python3-debian12@"
                + _artifact_candidate()["base_image_digest"]
            ),
        ]:
            stdout = json.dumps(_artifact_base_inspect())
        elif command[:4] == [
            "docker",
            "image",
            "inspect",
            _artifact_candidate()["image_id"],
        ]:
            stdout = json.dumps(_artifact_image_inspect())
        elif command[:2] == ["docker", "history"]:
            stdout = json.dumps({"CreatedBy": "COPY verified inputs"}) + "\n"
        elif command[:2] == ["docker", "cp"]:
            source = command[2]
            destination = Path(command[3])
            if source.endswith(":/proxy/proxy.py"):
                original = REPO_ROOT / "docker/phase2-openai-egress-proxy/proxy.py"
            elif source.endswith(":/proxy/responses-contract.json"):
                original = (
                    REPO_ROOT
                    / "docker/phase2-openai-egress-proxy/responses-contract.json"
                )
            elif source.endswith(":/proxy/runtime-contract.json"):
                original = (
                    REPO_ROOT
                    / "docker/phase2-openai-egress-proxy/runtime-contract.json"
                )
            else:
                raise AssertionError(f"unexpected copy source: {source}")
            destination.write_bytes(original.read_bytes())
            stdout = ""
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    return runner


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
    assert request["store"] is False
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


@pytest.mark.parametrize("store_value", [None, True])
def test_preflight_rejects_missing_or_enabled_provider_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store_value: bool | None,
) -> None:
    config_path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    _write_approval(config_path)
    approval = CanaryProviderApproval.model_validate(_approved_config())
    request = build_responses_request_contract(approval)
    request.pop("store", None)
    if store_value is not None:
        request["store"] = store_value
    monkeypatch.setattr(
        canary_preflight,
        "build_responses_request_contract",
        lambda _approval: deepcopy(request),
    )
    monkeypatch.setattr(
        canary_preflight,
        "exercise_placeholder_secret_injection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("privacy gate must stop before Secret injection")
        ),
    )

    result = run_canary_preflight(
        repo_root=REPO_ROOT,
        config_path=config_path,
        account="macbookpro",
        current_uid=os.getuid(),
        keychain_runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
        ),
        git_runner=lambda command, **_kwargs: _clean_git_result(command),
    )

    assert result.status == "PHASE2B_CANARY_CONFIG_BLOCKED"
    assert result.errors == ["responses_schema_not_ready"]
    assert result.strict_json_schema == "BLOCKED"
    assert result.provider_attempt_count == 0
    assert result.ai_call_count == 0
    assert result.provider_http == "NOT_RUN"


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


def test_proxy_artifact_approval_is_exact_candidate_plus_approved() -> None:
    approval = _artifact_approval_model()

    assert approval.approval_status == "APPROVED"
    assert approval.model_dump(mode="json", exclude={"approval_status"}) == (
        _artifact_candidate()
    )


@pytest.mark.parametrize(
    "field",
    [
        "image_id",
        "base_image_digest",
        "proxy_source_sha256",
        "dockerfile_sha256",
        "responses_contract_sha256",
        "proxy_policy_sha256",
        "launcher_source_sha256",
    ],
)
def test_proxy_artifact_approval_rejects_every_identity_mutation(
    field: str,
) -> None:
    payload = _artifact_approval()
    value = payload[field]
    assert isinstance(value, str)
    payload[field] = value[:-1] + ("0" if value[-1] != "0" else "1")
    try:
        approval = _artifact_approval_model(payload)
    except ValidationError:
        return
    with pytest.raises(CanaryPreflightError, match="artifact_approval_mismatch"):
        validate_proxy_artifact_approval(_artifact_candidate_model(), approval)


def test_secure_proxy_artifact_approval_reader_returns_no_secret_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "TickFlowPhase2Canary/proxy-artifact-approval.json"
    _write_artifact_approval(path)

    loaded = read_proxy_artifact_approval(path, current_uid=os.getuid())

    assert loaded.approval.model_dump(mode="json") == _artifact_approval()
    assert loaded.directory_mode == "0700"
    assert loaded.file_mode == "0600"
    assert loaded.owner_matches is True
    assert loaded.parents_not_symlinks is True
    assert loaded.config_secret_scan_clean is True


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("missing", "artifact_approval_missing"),
        ("file_mode", "artifact_approval_file_mode_invalid"),
        ("directory_mode", "artifact_approval_directory_mode_invalid"),
        ("malformed", "artifact_approval_json_invalid"),
        ("extra", "artifact_approval_contract_invalid"),
        ("status", "artifact_approval_contract_invalid"),
    ],
)
def test_proxy_artifact_approval_reader_fails_closed(
    tmp_path: Path,
    mutation: str,
    error_code: str,
) -> None:
    path = tmp_path / "TickFlowPhase2Canary/proxy-artifact-approval.json"
    if mutation != "missing":
        payload = _artifact_approval()
        if mutation == "extra":
            payload["local_path"] = "/private/path"
        elif mutation == "status":
            payload["approval_status"] = "REVIEWED"
        _write_artifact_approval(path, payload)
        if mutation == "file_mode":
            path.chmod(0o644)
        elif mutation == "directory_mode":
            path.parent.chmod(0o755)
        elif mutation == "malformed":
            path.write_text("{", encoding="utf-8")
            path.chmod(0o600)

    with pytest.raises(CanaryPreflightError, match=error_code):
        read_proxy_artifact_approval(path, current_uid=os.getuid())


def test_proxy_artifact_approval_reader_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real/proxy-artifact-approval.json"
    _write_artifact_approval(real)
    path = tmp_path / "TickFlowPhase2Canary/proxy-artifact-approval.json"
    path.parent.mkdir(mode=0o700)
    path.symlink_to(real)

    with pytest.raises(
        CanaryPreflightError,
        match="artifact_approval_symlink_forbidden",
    ):
        read_proxy_artifact_approval(path, current_uid=os.getuid())


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


def test_placeholder_secret_injection_is_single_file_readonly_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = CanaryProviderApproval.model_validate(_approved_config())
    observed: list[list[str]] = []
    original = canary_preflight.build_openai_proxy_create_command

    def capture(*args: Any, **kwargs: Any) -> list[str]:
        command = original(*args, **kwargs)
        observed.append(command)
        return command

    monkeypatch.setattr(
        canary_preflight,
        "build_openai_proxy_create_command",
        capture,
    )

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
    assert len(observed) == 1
    assert observed[0][-1] == "sha256:" + "0" * 64
    assert "tickflow-phase2-openai-egress-proxy:canary-v1" not in observed[0]
    assert any(
        value.endswith("dst=/run/phase2/provider-auth,readonly")
        for value in observed[0]
    )


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


@pytest.mark.parametrize(
    "command",
    [
        ["docker", "start", "phase2-openai-proxy-x"],
        ["docker", "run", "tickflow-phase2-openai-egress-proxy:canary-v1"],
        ["docker", "network", "create", "phase2-canary-egress-x"],
        ["docker", "network", "connect", "net", "container"],
        ["docker", "pull", "example.invalid/image"],
        ["docker", "login"],
    ],
)
def test_restricted_runtime_runner_rejects_every_active_or_network_command(
    command: list[str],
) -> None:
    runner = RestrictedCanaryRuntimeRunner(
        lambda observed, **_kwargs: subprocess.CompletedProcess(observed, 0)
    )

    with pytest.raises(CanaryPreflightError, match="runtime_command_forbidden"):
        runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=10,
        )


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
                stdout=_expected_git_diff(),
                stderr="",
            ),
        ]
    )

    def runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(command)
        result = next(outputs)
        result.args = command
        return result

    evidence = read_git_gate(
        REPO_ROOT,
        runner=runner,
        hash_allowlist_validator=lambda _root, _candidate: {},
    )

    assert evidence.ready is True
    assert evidence.base_head == (
        "165dc46ec9c10806ddcc4ff5d2171bc24ec7a87e"
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
                stdout=_expected_git_diff(),
                stderr="",
            ),
        ]
    )

    evidence = read_git_gate(
        REPO_ROOT,
        runner=lambda command, **_kwargs: next(outputs),
        hash_allowlist_validator=lambda _root, _candidate: {},
    )

    assert evidence.ready is False
    assert evidence.worktree_clean is False
    assert evidence.changed_paths_allowed is True


def test_git_gate_rejects_runtime_hash_allowlist_failure() -> None:
    outputs = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="c" * 40 + "\n", stderr=""),
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
                stdout=_expected_git_diff(),
                stderr="",
            ),
        ]
    )

    def reject_hashes(_root: Path, _candidate: Path) -> dict[Path, str]:
        raise CanaryPreflightError("runtime_preflight_hash_allowlist_invalid")

    evidence = read_git_gate(
        REPO_ROOT,
        runner=lambda command, **_kwargs: next(outputs),
        hash_allowlist_validator=reject_hashes,
    )

    assert evidence.ready is False
    assert evidence.changed_paths_allowed is False


@pytest.mark.parametrize(
    "changed_diff",
    [
        _expected_git_diff()
        + "A\tbackend/app/services/undeclared_runtime.py\n",
        _expected_git_diff().replace(
            "A\tdocker/phase2-openai-egress-proxy/readiness-contract.json\n",
            "",
        ),
        _expected_git_diff().replace(
            "A\tdocker/phase2-openai-egress-proxy/readiness-contract.json\n",
            "D\tdocker/phase2-openai-egress-proxy/readiness-contract.json\n",
        ),
        _expected_git_diff().replace(
            "A\tdocker/phase2-openai-egress-proxy/readiness-contract.json\n",
            "R100\tdocker/phase2-openai-egress-proxy/readiness-contract.json\t"
            "docker/phase2-openai-egress-proxy/renamed-readiness.json\n",
        ),
    ],
)
def test_git_gate_rejects_unknown_missing_deleted_or_renamed_paths(
    changed_diff: str,
) -> None:
    outputs = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="c" * 40 + "\n", stderr=""),
            subprocess.CompletedProcess(
                [],
                0,
                stdout="codex/tickflow-phase2-ai-review\n",
                stderr="",
            ),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=changed_diff, stderr=""),
        ]
    )

    evidence = read_git_gate(
        REPO_ROOT,
        runner=lambda command, **_kwargs: next(outputs),
        hash_allowlist_validator=lambda _root, _candidate: {},
    )

    assert evidence.ready is False
    assert evidence.changed_paths_allowed is False


def test_runtime_candidate_path_hash_allowlist_is_exact_and_includes_readiness() -> None:
    hashes = canary_preflight.compute_runtime_artifact_input_hashes(REPO_ROOT)

    allowlist = runtime_candidate_path_hash_allowlist(hashes)

    assert allowlist[Path("docker/phase2-openai-egress-proxy/readiness-contract.json")] == (
        hashes.readiness_contract_sha256
    )
    assert set(allowlist) == {
        Path("docker/phase2-openai-egress-proxy/proxy.py"),
        Path("docker/phase2-openai-egress-proxy/Dockerfile"),
        Path("docker/phase2-openai-egress-proxy/responses-contract.json"),
        Path("docker/phase2-openai-egress-proxy/runtime-contract.json"),
        Path("docker/phase2-openai-egress-proxy/readiness-contract.json"),
        Path("docker/phase2-canary-relay/relay.py"),
        Path("docker/phase2-canary-relay/Dockerfile"),
        Path("docker/phase2-canary-relay/runtime-contract.json"),
        Path("backend/app/services/phase2_canary_runtime_contract.json"),
        Path("backend/app/services/phase2_canary_orchestrator.py"),
        Path("backend/app/services/phase2_canary_observability.py"),
        Path("backend/scripts/run_phase2_single_symbol_canary.py"),
        Path("backend/app/services/phase2_canary_runtime_artifact.py"),
        Path("backend/scripts/build_phase2_canary_runtime_offline.py"),
        Path("docs/phase2-single-call-orchestrator-runbook.md"),
    }


def test_runtime_candidate_hash_allowlist_rejects_unknown_readiness_hash(
    tmp_path: Path,
) -> None:
    candidate = json.loads(
        (
            REPO_ROOT
            / "reports/phase2_provider_canary/runtime_contract_candidate.json"
        ).read_bytes()
    )
    candidate["artifact_identity"]["readiness_contract_sha256"] = "f" * 64
    candidate["inputs"]["readiness_contract_sha256"] = "f" * 64
    candidate["proxy_image"]["readiness_contract_sha256"] = "f" * 64
    candidate["proxy_contents"]["readiness_contract_sha256"] = "f" * 64
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        CanaryPreflightError,
        match="runtime_preflight_hash_allowlist_invalid",
    ):
        validate_runtime_candidate_hash_allowlist(REPO_ROOT, candidate_path)


def test_offline_canary_inputs_preserve_facts_but_cannot_reopen_consumed_preflight() -> None:
    evidence = validate_offline_canary_inputs(REPO_ROOT)

    assert evidence.status == "BLOCKED"
    assert evidence.errors == [
        "canary_output_not_empty",
        "external_action_evidence_invalid",
    ]
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
    assert evidence.canary_output_empty is False
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


def test_combined_preflight_rejects_superseded_candidate_before_runtime_create(
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

    def forbid_socket(*_args: Any, **_kwargs: Any) -> socket.socket:
        raise AssertionError("network socket forbidden in preflight")

    monkeypatch.setattr(socket, "socket", forbid_socket)
    runtime_calls: list[list[str]] = []

    result = run_canary_preflight(
        repo_root=REPO_ROOT,
        config_path=config_path,
        account="macbookpro",
        current_uid=os.getuid(),
        keychain_runner=keychain_runner,
        runtime_runner=_artifact_runtime_runner(runtime_calls),
        git_runner=lambda command, **_kwargs: _clean_git_result(command),
    )

    assert result.status == "PHASE2B_CANARY_CONFIG_BLOCKED"
    assert result.errors == [
        "canary_output_not_empty",
        "external_action_evidence_invalid",
    ]
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
    assert result.proxy_artifact_candidate == "NOT_RUN"
    assert result.proxy_artifact_review == "NOT_RUN"
    assert result.proxy_artifact_approval == "NOT_RUN"
    assert result.proxy_image == "NOT_RUN"
    assert result.proxy_launcher == "NOT_RUN"
    assert result.tls == "NOT_RUN"
    assert result.redirect == "NOT_RUN"
    assert result.egress_allowlist == "NOT_RUN"
    assert result.provider_attempt_count == 0
    assert result.ai_call_count == 0
    assert result.retry_count == 0
    assert result.provider_http == "NOT_RUN"
    assert result.tickflow_api_request_count == 0
    assert result.real_public_network_success_count == 0
    assert result.external_action_evidence == "NOT_PROVEN"
    assert not any(command[:2] == ["docker", "create"] for command in runtime_calls)


def test_combined_preflight_does_not_reopen_superseded_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "TickFlowPhase2Canary/provider-approval.json"
    artifact_approval_path = config_path.with_name("proxy-artifact-approval.json")
    _write_approval(config_path)
    _write_artifact_approval(artifact_approval_path)
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network socket forbidden in preflight")
        ),
    )
    runtime_calls: list[list[str]] = []

    result = run_canary_preflight(
        repo_root=REPO_ROOT,
        config_path=config_path,
        artifact_approval_path=artifact_approval_path,
        account="macbookpro",
        current_uid=os.getuid(),
        keychain_runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
        ),
        runtime_runner=_artifact_runtime_runner(runtime_calls),
        git_runner=lambda command, **_kwargs: _clean_git_result(command),
    )

    assert result.status == "PHASE2B_CANARY_CONFIG_BLOCKED"
    assert result.errors == [
        "canary_output_not_empty",
        "external_action_evidence_invalid",
    ]
    assert result.provider == "openai"
    assert result.exact_model == "gpt-5.6-terra"
    assert result.keychain_secret == "PRESENT"
    assert result.secret_content_read is False
    assert result.secret_hash_recorded is False
    assert result.temporary_single_file_injection == "PASSED"
    assert result.strict_json_schema == "READY"
    assert result.proxy_artifact_approval == "NOT_RUN"
    assert result.proxy_image == "NOT_RUN"
    assert result.proxy_launcher == "NOT_RUN"
    assert result.tls == "NOT_RUN"
    assert result.tls_evidence == "NOT_RUN"
    assert result.redirect == "NOT_RUN"
    assert result.egress_allowlist == "NOT_RUN"
    assert result.egress_evidence == "NOT_RUN"
    assert result.provider_attempt_count == 0
    assert result.ai_call_count == 0
    assert result.provider_http == "NOT_RUN"
    assert result.tickflow_api_request_count == 0
    assert result.real_public_network_success_count == 0
    forbidden = {"start", "run", "pull", "login"}
    assert not any(
        len(command) > 1 and command[1] in forbidden for command in runtime_calls
    )
    assert not any(
        command[:3] in (
            ["docker", "network", "create"],
            ["docker", "network", "connect"],
        )
        for command in runtime_calls
    )
    assert not any(command[:2] == ["docker", "create"] for command in runtime_calls)
    assert not any(command[:2] == ["docker", "cp"] for command in runtime_calls)


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
    assert result.errors == [
        "canary_output_not_empty",
        "external_action_evidence_invalid",
    ]
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
