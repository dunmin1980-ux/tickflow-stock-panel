"""Host-side runtime contracts for the restricted Phase 2B Provider Relay."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.schemas.phase2_claims import ClaimsDocument, WorkerClaimsCandidate
from app.schemas.phase2_provider_relay import RelayExecutionReceipt
from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_claims_renderer import (
    Phase2ClaimsRenderError,
    render_claims_document,
    validate_rendered_document,
)
from app.services.phase2_claims_service import (
    CLAIMS_VALID,
    Phase2ClaimsError,
    canonical_json_bytes,
    validate_claims_document,
)
from app.services.phase2_isolation_runtime import validate_isolated_candidate
from app.services.phase2_provider_relay_protocol import (
    Phase2ProviderRelayError,
    build_relay_request,
    parse_single_json_object,
    validate_relay_response,
)

RUNTIME_USER = "65532:65532"
EXPECTED_PIDS_LIMIT = 16
EXPECTED_MEMORY = 128 * 1024 * 1024
EXPECTED_NANO_CPUS = 500_000_000
RELAY_IMAGE = "tickflow-phase2-provider-relay:runtime-v1"
PROXY_IMAGE = "tickflow-phase2-egress-proxy:runtime-v1"
MOCK_IMAGE = "tickflow-phase2-mock-provider:runtime-v1"
PHASE2B_PROVIDER_RELAY_READY = "PHASE2B_PROVIDER_RELAY_READY"
PHASE2B_PROVIDER_EGRESS_BLOCKED = "PHASE2B_PROVIDER_EGRESS_BLOCKED"
PHASE2B_PROVIDER_SECRET_BLOCKED = "PHASE2B_PROVIDER_SECRET_BLOCKED"
PHASE2B_PROVIDER_RELAY_E2E_BLOCKED = "PHASE2B_PROVIDER_RELAY_E2E_BLOCKED"
_BASE_IMAGE_DIGEST = (
    "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)
_BASE_DIGEST_LABEL = "org.tickflow.phase2.base-image-digest"
_MAXIMUM_BYTES = 1_048_576
_FIXED_FACTS_FILE = "reports/phase2_facts/000403SZ_facts.json"
_SCENARIOS = (
    "valid_typed_candidate",
    "markdown_instead_of_json",
    "extra_free_text_field",
    "unknown_claim_type",
    "unknown_predicate",
    "wrong_symbol",
    "wrong_projection_sha",
    "raw_qfq_mismatch",
    "unsupported_fact_pointer",
    "trading_claim",
    "oversized_response",
    "timeout",
    "http_429",
    "http_500",
    "invalid_json",
    "multiple_json_documents",
    "empty_response",
    "wrong_request_id",
    "wrong_facts_sha",
    "sensitive_shape",
    "redirect_response",
    "external_url_response",
    "valid_typed_candidate",
)
_PROBE_EXPECTED = {
    "proxy_exact": "ALLOWED",
    "direct_provider": "NOT_REACHABLE",
    "arbitrary_hostname": "NOT_REACHABLE",
    "public_test_ip": "NOT_REACHABLE",
    "host_gateway": "NOT_REACHABLE",
    "docker_control_file": "BLOCKED",
    "docker_control_tcp": "NOT_REACHABLE",
    "proxy_wrong_path": "BLOCKED",
    "proxy_wrong_method": "BLOCKED",
}
_RUNTIME_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SENSITIVE_ENV_NAME = re.compile(
    r"(?:^|_)(?:api_?key|authorization|cookie|credential|password|secret|session|token)(?:$|_)",
    re.IGNORECASE,
)

Component = Literal["relay", "proxy", "mock"]


class Phase2ProviderRelayRuntimeError(ValueError):
    """Raised when a runtime contract cannot be constructed safely."""


@dataclass(frozen=True)
class ProviderRelayResult:
    status: str
    errors: list[str]
    cleanup_complete: bool
    evidence: dict[str, Any]


def _regular_path(value: Path, field: str) -> Path:
    path = Path(os.path.abspath(os.fspath(value)))
    if path.is_symlink():
        raise Phase2ProviderRelayRuntimeError(f"{field}_must_not_be_symlink")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise Phase2ProviderRelayRuntimeError(
            f"{field}_must_be_regular_file"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise Phase2ProviderRelayRuntimeError(f"{field}_must_be_regular_file")
    return path.resolve(strict=True)


@dataclass(frozen=True)
class RelayRuntimePaths:
    request: Path
    projection: Path
    response: Path
    receipt: Path

    def __post_init__(self) -> None:
        for field in ("request", "projection", "response", "receipt"):
            object.__setattr__(
                self,
                field,
                _regular_path(getattr(self, field), field),
            )


@dataclass(frozen=True)
class ProxyRuntimePaths:
    auth: Path
    receipt: Path

    def __post_init__(self) -> None:
        for field in ("auth", "receipt"):
            object.__setattr__(
                self,
                field,
                _regular_path(getattr(self, field), field),
            )


@dataclass(frozen=True)
class MockRuntimePaths:
    auth: Path
    scenario: Path
    receipt: Path

    def __post_init__(self) -> None:
        for field in ("auth", "scenario", "receipt"):
            object.__setattr__(
                self,
                field,
                _regular_path(getattr(self, field), field),
            )


@dataclass(frozen=True)
class ContainerContractEvidence:
    component: Component
    contract_valid: bool
    errors: list[str]
    mount_count: int
    network_count: int
    published_port_count: int
    device_count: int
    non_root: bool
    rootfs_readonly: bool
    cap_drop_all: bool
    no_new_privileges: bool
    pids_limited: bool
    memory_limited: bool
    cpu_limited: bool
    ipc_none: bool
    restart_disabled: bool
    privileged_disabled: bool
    mounts_valid: bool
    networks_valid: bool
    state_valid: bool
    environment_clean: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NetworkTopologyEvidence:
    topology_valid: bool
    errors: list[str]
    internal_network_count: int
    relay_network_member_count: int
    provider_network_member_count: int
    relay_can_resolve_mock: bool
    mock_can_resolve_relay: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecretBoundaryEvidence:
    boundary_valid: bool
    errors: list[str]
    secret_present: bool
    secret_value_hit_count: int
    secret_digest_recorded: bool
    relay_has_secret_mount: bool
    proxy_has_readonly_secret_mount: bool
    mock_has_readonly_secret_mount: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_name(value: str) -> None:
    if not isinstance(value, str) or not _RUNTIME_NAME.fullmatch(value):
        raise Phase2ProviderRelayRuntimeError("runtime_name_invalid")


def _common_create_prefix(name: str, network: str) -> list[str]:
    _validate_name(name)
    _validate_name(network)
    return [
        "docker",
        "create",
        "--name",
        name,
        "--network",
        network,
        "--read-only",
        "--user",
        RUNTIME_USER,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(EXPECTED_PIDS_LIMIT),
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--ipc",
        "none",
        "--restart",
        "no",
    ]


def _mount(source: Path, destination: str, *, readonly: bool) -> str:
    suffix = ",readonly" if readonly else ""
    return f"type=bind,src={source},dst={destination}{suffix}"


def build_network_create_command(network: str) -> list[str]:
    _validate_name(network)
    return [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        network,
    ]


def build_network_connect_command(network: str, container: str) -> list[str]:
    _validate_name(network)
    _validate_name(container)
    return ["docker", "network", "connect", network, container]


def build_relay_create_command(
    name: str,
    network: str,
    paths: RelayRuntimePaths,
    *,
    mode: Literal["run", "probe"],
) -> list[str]:
    if mode not in {"run", "probe"}:
        raise Phase2ProviderRelayRuntimeError("relay_mode_invalid")
    return [
        *_common_create_prefix(name, network),
        "--mount",
        _mount(paths.request, "/input/request.json", readonly=True),
        "--mount",
        _mount(paths.projection, "/input/projection.json", readonly=True),
        "--mount",
        _mount(paths.response, "/output/response.json", readonly=False),
        "--mount",
        _mount(paths.receipt, "/output/receipt.json", readonly=False),
        RELAY_IMAGE,
        mode,
    ]


def build_proxy_create_command(
    name: str,
    network: str,
    paths: ProxyRuntimePaths,
) -> list[str]:
    return [
        *_common_create_prefix(name, network),
        "--mount",
        _mount(paths.auth, "/run/phase2/provider-auth", readonly=True),
        "--mount",
        _mount(paths.receipt, "/output/receipt.json", readonly=False),
        PROXY_IMAGE,
    ]


def build_mock_create_command(
    name: str,
    network: str,
    paths: MockRuntimePaths,
) -> list[str]:
    return [
        *_common_create_prefix(name, network),
        "--mount",
        _mount(paths.auth, "/run/phase2/provider-auth", readonly=True),
        "--mount",
        _mount(paths.scenario, "/input/scenario.json", readonly=True),
        "--mount",
        _mount(paths.receipt, "/output/receipt.json", readonly=False),
        MOCK_IMAGE,
    ]


def _inspect_item(payload: Any) -> Mapping[str, Any] | None:
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], Mapping)
    ):
        return None
    return payload[0]


def _expected_image(component: Component) -> str:
    return {"relay": RELAY_IMAGE, "proxy": PROXY_IMAGE, "mock": MOCK_IMAGE}[
        component
    ]


def _expected_mounts(
    component: Component,
    paths: RelayRuntimePaths | ProxyRuntimePaths | MockRuntimePaths,
) -> dict[str, tuple[Path, bool]]:
    if component == "relay" and isinstance(paths, RelayRuntimePaths):
        return {
            "/input/request.json": (paths.request, False),
            "/input/projection.json": (paths.projection, False),
            "/output/response.json": (paths.response, True),
            "/output/receipt.json": (paths.receipt, True),
        }
    if component == "proxy" and isinstance(paths, ProxyRuntimePaths):
        return {
            "/run/phase2/provider-auth": (paths.auth, False),
            "/output/receipt.json": (paths.receipt, True),
        }
    if component == "mock" and isinstance(paths, MockRuntimePaths):
        return {
            "/run/phase2/provider-auth": (paths.auth, False),
            "/input/scenario.json": (paths.scenario, False),
            "/output/receipt.json": (paths.receipt, True),
        }
    raise Phase2ProviderRelayRuntimeError("runtime_paths_component_mismatch")


def validate_container_inspect(
    payload: Any,
    *,
    component: Component,
    paths: RelayRuntimePaths | ProxyRuntimePaths | MockRuntimePaths,
    expected_primary_network: str,
    expected_networks: set[str],
    expected_state: Literal["created", "running", "exited"],
    expected_exit_code: int = 0,
) -> ContainerContractEvidence:
    """Validate Docker inspect while returning no identity or source values."""
    if component not in {"relay", "proxy", "mock"}:
        raise Phase2ProviderRelayRuntimeError("runtime_component_invalid")
    _validate_name(expected_primary_network)
    for network in expected_networks:
        _validate_name(network)
    expected_mounts = _expected_mounts(component, paths)
    errors: list[str] = []
    item = _inspect_item(payload)
    if item is None:
        errors.append("runtime_inspect_invalid")
        item = {}
    config = item.get("Config") if isinstance(item.get("Config"), Mapping) else {}
    host = (
        item.get("HostConfig")
        if isinstance(item.get("HostConfig"), Mapping)
        else {}
    )
    state = item.get("State") if isinstance(item.get("State"), Mapping) else {}
    network_settings = (
        item.get("NetworkSettings")
        if isinstance(item.get("NetworkSettings"), Mapping)
        else {}
    )
    mounts = item.get("Mounts") if isinstance(item.get("Mounts"), list) else []

    non_root = config.get("User") == RUNTIME_USER
    rootfs_readonly = host.get("ReadonlyRootfs") is True
    cap_drop_all = {
        str(value).upper() for value in host.get("CapDrop", [])
    } == {"ALL"}
    security_options = {
        str(value).lower() for value in host.get("SecurityOpt", [])
    }
    no_new_privileges = bool(
        security_options & {"no-new-privileges", "no-new-privileges:true"}
    ) and "no-new-privileges:false" not in security_options
    pids_limited = host.get("PidsLimit") == EXPECTED_PIDS_LIMIT
    memory_limited = host.get("Memory") == EXPECTED_MEMORY
    cpu_limited = host.get("NanoCpus") == EXPECTED_NANO_CPUS
    ipc_none = host.get("IpcMode") == "none"
    restart = host.get("RestartPolicy")
    restart_disabled = (
        isinstance(restart, Mapping)
        and restart.get("Name") in {"", "no"}
        and restart.get("MaximumRetryCount", 0) == 0
    )
    privileged_disabled = host.get("Privileged") is False
    devices = host.get("Devices") if isinstance(host.get("Devices"), list) else []
    device_count = len(devices)
    host_ports = (
        host.get("PortBindings")
        if isinstance(host.get("PortBindings"), Mapping)
        else {}
    )
    network_ports = (
        network_settings.get("Ports")
        if isinstance(network_settings.get("Ports"), Mapping)
        else {}
    )
    published_port_count = len(set(host_ports) | set(network_ports))
    image_valid = config.get("Image") == _expected_image(component)
    environment = config.get("Env") if isinstance(config.get("Env"), list) else []
    environment_clean = all(
        isinstance(value, str)
        and not _SENSITIVE_ENV_NAME.search(value.partition("=")[0])
        for value in environment
    )
    networks = network_settings.get("Networks")
    actual_networks = set(networks) if isinstance(networks, Mapping) else set()
    networks_valid = (
        host.get("NetworkMode") == expected_primary_network
        and actual_networks == expected_networks
    )
    state_valid = state.get("Status") == expected_state
    if expected_state == "exited":
        state_valid = state_valid and state.get("ExitCode") == expected_exit_code

    checks = {
        "runtime_user_invalid": non_root,
        "runtime_image_invalid": image_valid,
        "runtime_rootfs_not_readonly": rootfs_readonly,
        "runtime_cap_drop_invalid": cap_drop_all,
        "runtime_security_opt_invalid": no_new_privileges,
        "runtime_pids_limit_invalid": pids_limited,
        "runtime_memory_limit_invalid": memory_limited,
        "runtime_cpu_limit_invalid": cpu_limited,
        "runtime_ipc_mode_invalid": ipc_none,
        "runtime_restart_policy_invalid": restart_disabled,
        "runtime_privileged": privileged_disabled,
        "runtime_ports_published": published_port_count == 0,
        "runtime_devices_present": device_count == 0,
        "runtime_network_membership_invalid": networks_valid,
        "runtime_state_invalid": state_valid,
        "runtime_sensitive_environment": environment_clean,
    }
    errors.extend(code for code, passed in checks.items() if not passed)

    mount_by_destination = {
        mount.get("Destination"): mount
        for mount in mounts
        if isinstance(mount, Mapping) and isinstance(mount.get("Destination"), str)
    }
    mount_set_valid = (
        len(mounts) == len(expected_mounts)
        and set(mount_by_destination) == set(expected_mounts)
    )
    if not mount_set_valid:
        errors.append("runtime_mount_set_invalid")
    source_valid = mount_set_valid
    mode_valid = mount_set_valid
    if mount_set_valid:
        for destination, (expected_source, writable) in expected_mounts.items():
            mount = mount_by_destination[destination]
            source_valid = source_valid and (
                mount.get("Type") == "bind"
                and Path(str(mount.get("Source"))).resolve(strict=False)
                == expected_source
            )
            mode_valid = mode_valid and mount.get("RW") is writable
    if not source_valid:
        errors.append("runtime_mount_source_invalid")
    if not mode_valid:
        errors.append("runtime_mount_mode_invalid")
    mounts_valid = mount_set_valid and source_valid and mode_valid

    unique_errors = sorted(set(errors))
    return ContainerContractEvidence(
        component=component,
        contract_valid=not unique_errors,
        errors=unique_errors,
        mount_count=len(mounts),
        network_count=len(actual_networks),
        published_port_count=published_port_count,
        device_count=device_count,
        non_root=non_root,
        rootfs_readonly=rootfs_readonly,
        cap_drop_all=cap_drop_all,
        no_new_privileges=no_new_privileges,
        pids_limited=pids_limited,
        memory_limited=memory_limited,
        cpu_limited=cpu_limited,
        ipc_none=ipc_none,
        restart_disabled=restart_disabled,
        privileged_disabled=privileged_disabled,
        mounts_valid=mounts_valid,
        networks_valid=networks_valid,
        state_valid=state_valid,
        environment_clean=environment_clean,
    )


def _network_item(payload: Any) -> Mapping[str, Any] | None:
    return _inspect_item(payload)


def _network_members(item: Mapping[str, Any]) -> set[str]:
    containers = item.get("Containers")
    if not isinstance(containers, Mapping):
        return set()
    return {
        str(value.get("Name"))
        for value in containers.values()
        if isinstance(value, Mapping) and isinstance(value.get("Name"), str)
    }


def validate_network_topology(
    relay_payload: Any,
    provider_payload: Any,
    *,
    relay_network: str,
    provider_network: str,
    relay_name: str,
    proxy_name: str,
    mock_name: str,
) -> NetworkTopologyEvidence:
    """Require the Proxy to be the sole bridge between two internal networks."""
    for value in (
        relay_network,
        provider_network,
        relay_name,
        proxy_name,
        mock_name,
    ):
        _validate_name(value)
    errors: list[str] = []
    relay_item = _network_item(relay_payload)
    provider_item = _network_item(provider_payload)
    if relay_item is None:
        errors.append("relay_proxy_network_inspect_invalid")
        relay_item = {}
    if provider_item is None:
        errors.append("proxy_provider_network_inspect_invalid")
        provider_item = {}
    relay_members = _network_members(relay_item)
    provider_members = _network_members(provider_item)
    checks = {
        "relay_proxy_network_name_invalid": relay_item.get("Name")
        == relay_network,
        "proxy_provider_network_name_invalid": provider_item.get("Name")
        == provider_network,
        "relay_proxy_network_not_internal": relay_item.get("Internal") is True,
        "proxy_provider_network_not_internal": provider_item.get("Internal")
        is True,
        "relay_proxy_network_driver_invalid": relay_item.get("Driver") == "bridge",
        "proxy_provider_network_driver_invalid": provider_item.get("Driver")
        == "bridge",
        "relay_proxy_network_attachable": relay_item.get("Attachable") is False,
        "proxy_provider_network_attachable": provider_item.get("Attachable")
        is False,
        "relay_proxy_network_ingress": relay_item.get("Ingress") is False,
        "proxy_provider_network_ingress": provider_item.get("Ingress") is False,
        "relay_proxy_network_members_invalid": relay_members
        == {relay_name, proxy_name},
        "proxy_provider_network_members_invalid": provider_members
        == {proxy_name, mock_name},
    }
    errors.extend(code for code, passed in checks.items() if not passed)
    unique_errors = sorted(set(errors))
    return NetworkTopologyEvidence(
        topology_valid=not unique_errors,
        errors=unique_errors,
        internal_network_count=sum(
            item.get("Internal") is True for item in (relay_item, provider_item)
        ),
        relay_network_member_count=len(relay_members),
        provider_network_member_count=len(provider_members),
        relay_can_resolve_mock=mock_name in relay_members,
        mock_can_resolve_relay=relay_name in provider_members,
    )


def _command_has_readonly_auth(command: Sequence[str]) -> bool:
    return any(
        value.endswith("dst=/run/phase2/provider-auth,readonly")
        for value in command
    )


def _serialized(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


def _nested_keys(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [
            *(str(key) for key in value),
            *(key for item in value.values() for key in _nested_keys(item)),
        ]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [key for item in value for key in _nested_keys(item)]
    return []


def _read_auth_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= 4096:
            return b""
        raw = os.read(descriptor, 4097)
        return raw if len(raw) == metadata.st_size else b""
    finally:
        os.close(descriptor)


def validate_secret_boundary(
    *,
    relay_paths: RelayRuntimePaths,
    proxy_paths: ProxyRuntimePaths,
    mock_paths: MockRuntimePaths,
    commands: Sequence[Sequence[str]],
    inspect_payloads: Sequence[Any],
    serialized_artifacts: Sequence[bytes | str],
    canary: str,
) -> SecretBoundaryEvidence:
    """Scan an in-memory credential value without returning it or its digest."""
    if not isinstance(canary, str) or not canary:
        raise Phase2ProviderRelayRuntimeError("secret_canary_invalid")
    canary_bytes = canary.encode("utf-8")
    try:
        auth_bytes = _read_auth_bytes(proxy_paths.auth)
    except OSError:
        auth_bytes = b""
    secret_present = (
        proxy_paths.auth == mock_paths.auth
        and bool(auth_bytes)
        and hmac.compare_digest(auth_bytes, canary_bytes)
    )
    relay_commands = [command for command in commands if RELAY_IMAGE in command]
    proxy_commands = [command for command in commands if PROXY_IMAGE in command]
    mock_commands = [command for command in commands if MOCK_IMAGE in command]
    relay_has_secret_mount = any(
        _command_has_readonly_auth(command) for command in relay_commands
    )
    proxy_has_readonly_secret_mount = (
        len(proxy_commands) == 1 and _command_has_readonly_auth(proxy_commands[0])
    )
    mock_has_readonly_secret_mount = (
        len(mock_commands) == 1 and _command_has_readonly_auth(mock_commands[0])
    )
    scan_values = [
        *(_serialized(command) for command in commands),
        *(_serialized(payload) for payload in inspect_payloads),
        *(_serialized(artifact) for artifact in serialized_artifacts),
    ]
    hit_count = sum(canary_bytes in value for value in scan_values)
    digest_keys = re.compile(r"(?:secret|auth).*(?:sha|hash|digest)", re.IGNORECASE)
    secret_digest_recorded = any(
        digest_keys.search(key)
        for payload in inspect_payloads
        for key in _nested_keys(payload)
    )
    errors: list[str] = []
    if not secret_present:
        errors.append("secret_file_binding_invalid")
    if relay_has_secret_mount:
        errors.append("relay_secret_mount_forbidden")
    if commands and not proxy_has_readonly_secret_mount:
        errors.append("proxy_secret_mount_invalid")
    if commands and not mock_has_readonly_secret_mount:
        errors.append("mock_secret_mount_invalid")
    if hit_count:
        errors.append("secret_value_exposed")
    if secret_digest_recorded:
        errors.append("secret_digest_recorded")
    unique_errors = sorted(set(errors))
    return SecretBoundaryEvidence(
        boundary_valid=not unique_errors,
        errors=unique_errors,
        secret_present=secret_present,
        secret_value_hit_count=hit_count,
        secret_digest_recorded=secret_digest_recorded,
        relay_has_secret_mount=relay_has_secret_mount,
        proxy_has_readonly_secret_mount=proxy_has_readonly_secret_mount,
        mock_has_readonly_secret_mount=mock_has_readonly_secret_mount,
    )


def _default_executor(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=30,
    )


def _resolve_roots(
    repo_root: Path,
    reports_root: Path,
    *,
    allow_test_output_root: bool,
) -> tuple[Path, Path]:
    if repo_root.is_symlink() or reports_root.is_symlink():
        raise Phase2ProviderRelayRuntimeError("runtime_root_symlink_forbidden")
    resolved_repo = repo_root.resolve(strict=True)
    resolved_reports = reports_root.resolve(strict=True)
    if not resolved_repo.is_dir() or not resolved_reports.is_dir():
        raise Phase2ProviderRelayRuntimeError("runtime_root_invalid")
    if not allow_test_output_root and resolved_reports != resolved_repo / "reports":
        raise Phase2ProviderRelayRuntimeError(
            "reports_root_must_be_repository_reports"
        )
    return resolved_repo, resolved_reports


def _read_regular(path: Path, error_code: str, maximum: int = _MAXIMUM_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise Phase2ProviderRelayRuntimeError(error_code)
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    try:
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= maximum:
                raise Phase2ProviderRelayRuntimeError(error_code)
            raw = os.read(descriptor, maximum + 1)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise Phase2ProviderRelayRuntimeError(error_code) from exc
    if len(raw) != metadata.st_size:
        raise Phase2ProviderRelayRuntimeError(error_code)
    return raw


def _write_durable(path: Path, raw: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, mode, follow_symlinks=False)


def _json_object(raw: bytes | str, error_code: str) -> dict[str, Any]:
    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
    try:
        return parse_single_json_object(raw_bytes, maximum_bytes=_MAXIMUM_BYTES)
    except Phase2ProviderRelayError as exc:
        raise Phase2ProviderRelayRuntimeError(error_code) from exc


def _execute(
    executor: Callable[[list[str]], subprocess.CompletedProcess[str]],
    command: list[str],
    *,
    error_code: str,
    allowed_returncodes: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = executor(command)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase2ProviderRelayRuntimeError(error_code) from exc
    accepted = allowed_returncodes if allowed_returncodes is not None else {0}
    if result.returncode not in accepted:
        raise Phase2ProviderRelayRuntimeError(error_code)
    return result


def _image_runtime_evidence(raw: str, image: str) -> dict[str, Any]:
    payload = _json_object_list(raw, "provider_image_inspect_invalid")
    if len(payload) != 1 or not isinstance(payload[0], Mapping):
        raise Phase2ProviderRelayRuntimeError("provider_image_inspect_invalid")
    item = payload[0]
    config = item.get("Config")
    if not isinstance(config, Mapping):
        raise Phase2ProviderRelayRuntimeError("provider_image_config_invalid")
    expected_entrypoint = {
        RELAY_IMAGE: ["/usr/bin/python3", "/relay/relay.py"],
        PROXY_IMAGE: ["/usr/bin/python3", "/proxy/proxy.py"],
        MOCK_IMAGE: ["/usr/bin/python3", "/app/mock_provider.py"],
    }[image]
    image_id = item.get("Id")
    labels = config.get("Labels")
    base_digest = labels.get(_BASE_DIGEST_LABEL) if isinstance(labels, Mapping) else None
    environment = config.get("Env")
    expected_environment = {
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONHASHSEED=0",
    }
    checks = {
        "image_id_invalid": isinstance(image_id, str)
        and bool(_IMAGE_SHA256.fullmatch(image_id)),
        "image_user_invalid": config.get("User") == RUNTIME_USER,
        "image_entrypoint_invalid": config.get("Entrypoint") == expected_entrypoint,
        "image_environment_invalid": isinstance(environment, list)
        and set(environment) == expected_environment,
        "image_base_digest_invalid": base_digest == _BASE_IMAGE_DIGEST,
    }
    errors = sorted(code for code, passed in checks.items() if not passed)
    if errors:
        raise Phase2ProviderRelayRuntimeError(errors[0])
    return {
        "image_id": image_id,
        "base_image_digest": base_digest,
        "runtime_user": RUNTIME_USER,
        "entrypoint_valid": True,
        "environment_clean": True,
    }


def _json_object_list(raw: str, error_code: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise Phase2ProviderRelayRuntimeError(error_code) from exc
    if not isinstance(value, list):
        raise Phase2ProviderRelayRuntimeError(error_code)
    return value


def _claims_document(
    candidate: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> ClaimsDocument:
    try:
        parsed = WorkerClaimsCandidate.model_validate(candidate)
        scope = projection["safe_facts"]["scope"]
        return ClaimsDocument.model_validate(
            {
                "claims_schema_version": 1,
                "source_system": "tickflow-stock-panel",
                "symbol": parsed.symbol,
                "name": parsed.name,
                "trade_date": parsed.trade_date,
                "timezone": parsed.timezone,
                "facts_binding": {
                    "facts_file": _FIXED_FACTS_FILE,
                    "facts_sha256": projection["facts_sha256"],
                    "facts_schema_version": 1,
                },
                "scope": {
                    "market_scope": scope["market_scope"],
                    "financial_scope": scope["financial_scope"],
                    "news_scope": scope["news_scope"],
                    "industry_scope": "unavailable",
                },
                "claims": [item.model_dump(mode="json") for item in parsed.claims],
                "vendor_pending": projection["safe_facts"]["vendor_pending"],
                "trading_advice": False,
            }
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise Phase2ProviderRelayRuntimeError(
            "candidate_document_adapter_invalid"
        ) from exc


def _render_valid_candidate(
    repo_root: Path,
    candidate: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> str:
    document = _claims_document(candidate, projection)
    try:
        validation = validate_claims_document(repo_root, document)
        if validation.status != CLAIMS_VALID or validation.errors:
            raise Phase2ProviderRelayRuntimeError("claims_validation_blocked")
        rendered = render_claims_document(document, validation)
        if validate_rendered_document(document, validation, rendered):
            raise Phase2ProviderRelayRuntimeError("renderer_validation_blocked")
        return rendered
    except (Phase2ClaimsError, Phase2ClaimsRenderError) as exc:
        raise Phase2ProviderRelayRuntimeError("candidate_render_blocked") from exc


@dataclass(frozen=True)
class _ScenarioOutcome:
    scenario: str
    mode: Literal["run", "probe"]
    accepted: bool
    expected_rejection: bool
    errors: list[str]
    relay_receipt: dict[str, Any]
    host_validation: dict[str, Any]
    candidate_sha256: str
    rendered_sha256: str
    rendered: str
    probe_results: dict[str, str]
    contract: dict[str, Any]
    topology: dict[str, Any]
    secret_boundary: dict[str, Any]
    cleanup_complete: bool


def _wait_ready(path: Path, error_code: str) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            value = _json_object(path.read_bytes(), error_code)
        except (OSError, Phase2ProviderRelayRuntimeError):
            time.sleep(0.025)
            continue
        if value.get("status") == "READY" and value.get("auth_present") is True:
            return
        time.sleep(0.025)
    raise Phase2ProviderRelayRuntimeError(error_code)


def _relay_receipt(path: Path) -> dict[str, Any]:
    try:
        value = RelayExecutionReceipt.model_validate(
            _json_object(_read_regular(path, "relay_receipt_invalid"), "relay_receipt_invalid")
        )
    except ValidationError as exc:
        raise Phase2ProviderRelayRuntimeError("relay_receipt_invalid") from exc
    return value.model_dump(mode="json")


def _service_receipt(path: Path, component: Literal["proxy", "mock"]) -> dict[str, Any]:
    value = _json_object(
        _read_regular(path, f"{component}_receipt_invalid"),
        f"{component}_receipt_invalid",
    )
    allowed = {
        "proxy": {
            "receipt_schema_version",
            "method_allowed",
            "path_allowed",
            "upstream_attempt_count",
            "response_category",
            "response_size",
            "auth_present",
        },
        "mock": {
            "receipt_schema_version",
            "request_count",
            "method_allowed",
            "path_allowed",
            "auth_valid",
            "envelope_valid",
            "scenario",
            "response_category",
            "http_status",
            "response_size",
        },
    }[component]
    if set(value) != allowed or value.get("receipt_schema_version") != 1:
        raise Phase2ProviderRelayRuntimeError(f"{component}_receipt_invalid")
    return {field: value[field] for field in sorted(allowed)}


def _cleanup_runtime(
    executor: Callable[[list[str]], subprocess.CompletedProcess[str]],
    containers: list[str],
    networks: list[str],
) -> bool:
    cleanup_complete = True
    for container in reversed(containers):
        try:
            result = executor(["docker", "rm", "-f", container])
            cleanup_complete = cleanup_complete and result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            cleanup_complete = False
    for network in reversed(networks):
        try:
            result = executor(["docker", "network", "rm", network])
            cleanup_complete = cleanup_complete and result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            cleanup_complete = False
    return cleanup_complete


def _run_stack(
    repo_root: Path,
    temporary_root: Path,
    projection: Mapping[str, Any],
    projection_bytes: bytes,
    *,
    scenario: str,
    mode: Literal["run", "probe"],
    ordinal: int,
    canary: str,
    auth_path: Path,
    request_id: str,
    executor: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> _ScenarioOutcome:
    suffix = uuid.uuid4().hex[:12]
    relay_network = f"phase2-relay-proxy-{suffix}"
    provider_network = f"phase2-proxy-provider-{suffix}"
    relay_name = f"phase2-relay-{suffix}"
    proxy_name = f"phase2-proxy-{suffix}"
    mock_name = f"phase2-mock-{suffix}"
    run_dir = temporary_root / f"{ordinal:02d}-{suffix}"
    run_dir.mkdir(mode=0o700)
    request = build_relay_request(projection, request_id=request_id)
    request_path = run_dir / "request.json"
    projection_path = run_dir / "projection.json"
    response_path = run_dir / "response.json"
    relay_receipt_path = run_dir / "relay-receipt.json"
    proxy_receipt_path = run_dir / "proxy-receipt.json"
    scenario_path = run_dir / "scenario.json"
    mock_receipt_path = run_dir / "mock-receipt.json"
    _write_durable(
        request_path,
        canonical_json_bytes(request.model_dump(mode="json")),
        0o444,
    )
    _write_durable(projection_path, projection_bytes, 0o444)
    _write_durable(response_path, b"\n", 0o666)
    _write_durable(relay_receipt_path, b"\n", 0o666)
    _write_durable(proxy_receipt_path, b"\n", 0o666)
    _write_durable(
        scenario_path,
        canonical_json_bytes({"scenario": scenario}),
        0o444,
    )
    _write_durable(mock_receipt_path, b"\n", 0o666)
    relay_paths = RelayRuntimePaths(
        request_path,
        projection_path,
        response_path,
        relay_receipt_path,
    )
    proxy_paths = ProxyRuntimePaths(auth_path, proxy_receipt_path)
    mock_paths = MockRuntimePaths(auth_path, scenario_path, mock_receipt_path)
    create_commands = [
        build_mock_create_command(mock_name, provider_network, mock_paths),
        build_proxy_create_command(proxy_name, provider_network, proxy_paths),
        build_relay_create_command(
            relay_name,
            relay_network,
            relay_paths,
            mode=mode,
        ),
    ]
    containers: list[str] = []
    networks: list[str] = []
    captured: list[bytes | str] = []
    inspect_payloads: list[Any] = []
    cleanup_complete = True
    failure: str | None = None
    outcome: _ScenarioOutcome | None = None
    try:
        for network in (relay_network, provider_network):
            result = _execute(
                executor,
                build_network_create_command(network),
                error_code="network_create_failed",
            )
            captured.extend((result.stdout, result.stderr))
            networks.append(network)

        result = _execute(
            executor,
            create_commands[0],
            error_code="mock_create_failed",
        )
        captured.extend((result.stdout, result.stderr))
        containers.append(mock_name)
        result = _execute(
            executor,
            ["docker", "start", mock_name],
            error_code="mock_start_failed",
        )
        captured.extend((result.stdout, result.stderr))
        _wait_ready(mock_receipt_path, "mock_ready_blocked")

        result = _execute(
            executor,
            create_commands[1],
            error_code="proxy_create_failed",
        )
        captured.extend((result.stdout, result.stderr))
        containers.append(proxy_name)
        result = _execute(
            executor,
            ["docker", "start", proxy_name],
            error_code="proxy_start_failed",
        )
        captured.extend((result.stdout, result.stderr))
        _wait_ready(proxy_receipt_path, "proxy_ready_blocked")
        result = _execute(
            executor,
            build_network_connect_command(relay_network, proxy_name),
            error_code="proxy_network_connect_failed",
        )
        captured.extend((result.stdout, result.stderr))

        result = _execute(
            executor,
            create_commands[2],
            error_code="relay_create_failed",
        )
        captured.extend((result.stdout, result.stderr))
        containers.append(relay_name)

        mock_inspect_result = _execute(
            executor,
            ["docker", "inspect", mock_name],
            error_code="mock_inspect_failed",
        )
        proxy_inspect_result = _execute(
            executor,
            ["docker", "inspect", proxy_name],
            error_code="proxy_inspect_failed",
        )
        relay_created_result = _execute(
            executor,
            ["docker", "inspect", relay_name],
            error_code="relay_inspect_failed",
        )
        for result in (
            mock_inspect_result,
            proxy_inspect_result,
            relay_created_result,
        ):
            captured.extend((result.stdout, result.stderr))
        mock_inspect = _json_object_list(
            mock_inspect_result.stdout,
            "mock_inspect_invalid",
        )
        proxy_inspect = _json_object_list(
            proxy_inspect_result.stdout,
            "proxy_inspect_invalid",
        )
        relay_created = _json_object_list(
            relay_created_result.stdout,
            "relay_inspect_invalid",
        )
        inspect_payloads.extend((mock_inspect, proxy_inspect, relay_created))
        contract_items = {
            "mock": validate_container_inspect(
                mock_inspect,
                component="mock",
                paths=mock_paths,
                expected_primary_network=provider_network,
                expected_networks={provider_network},
                expected_state="running",
            ),
            "proxy": validate_container_inspect(
                proxy_inspect,
                component="proxy",
                paths=proxy_paths,
                expected_primary_network=provider_network,
                expected_networks={provider_network, relay_network},
                expected_state="running",
            ),
            "relay_created": validate_container_inspect(
                relay_created,
                component="relay",
                paths=relay_paths,
                expected_primary_network=relay_network,
                expected_networks={relay_network},
                expected_state="created",
            ),
        }
        if any(not item.contract_valid for item in contract_items.values()):
            raise Phase2ProviderRelayRuntimeError("runtime_contract_invalid")

        relay_network_result = _execute(
            executor,
            ["docker", "network", "inspect", relay_network],
            error_code="relay_network_inspect_failed",
        )
        provider_network_result = _execute(
            executor,
            ["docker", "network", "inspect", provider_network],
            error_code="provider_network_inspect_failed",
        )
        captured.extend(
            (
                relay_network_result.stdout,
                relay_network_result.stderr,
                provider_network_result.stdout,
                provider_network_result.stderr,
            )
        )
        topology = validate_network_topology(
            _json_object_list(
                relay_network_result.stdout,
                "relay_network_inspect_invalid",
            ),
            _json_object_list(
                provider_network_result.stdout,
                "provider_network_inspect_invalid",
            ),
            relay_network=relay_network,
            provider_network=provider_network,
            relay_name=relay_name,
            proxy_name=proxy_name,
            mock_name=mock_name,
        )
        if not topology.topology_valid:
            raise Phase2ProviderRelayRuntimeError("network_topology_invalid")

        relay_run = _execute(
            executor,
            ["docker", "start", "--attach", relay_name],
            error_code="relay_start_failed",
            allowed_returncodes={0, 2},
        )
        captured.extend((relay_run.stdout, relay_run.stderr))
        if mode == "probe" and relay_run.returncode != 0:
            raise Phase2ProviderRelayRuntimeError("network_probe_failed")
        if scenario == "valid_typed_candidate" and relay_run.returncode != 0:
            raise Phase2ProviderRelayRuntimeError("valid_scenario_rejected")

        relay_exited_result = _execute(
            executor,
            ["docker", "inspect", relay_name],
            error_code="relay_exit_inspect_failed",
        )
        captured.extend((relay_exited_result.stdout, relay_exited_result.stderr))
        relay_exited = _json_object_list(
            relay_exited_result.stdout,
            "relay_exit_inspect_invalid",
        )
        inspect_payloads.append(relay_exited)
        relay_contract = validate_container_inspect(
            relay_exited,
            component="relay",
            paths=relay_paths,
            expected_primary_network=relay_network,
            expected_networks={relay_network},
            expected_state="exited",
            expected_exit_code=relay_run.returncode,
        )
        if not relay_contract.contract_valid:
            raise Phase2ProviderRelayRuntimeError("relay_exit_contract_invalid")
        contract_items["relay_exited"] = relay_contract

        relay_receipt = _relay_receipt(relay_receipt_path)
        proxy_receipt = _service_receipt(proxy_receipt_path, "proxy")
        mock_receipt = _service_receipt(mock_receipt_path, "mock")
        receipt_artifact = canonical_json_bytes(
            {
                "scenario": scenario,
                "mode": mode,
                "relay": relay_receipt,
                "proxy": proxy_receipt,
                "mock": mock_receipt,
            }
        )
        captured.extend(
            (
                relay_receipt_path.read_bytes(),
                proxy_receipt_path.read_bytes(),
                mock_receipt_path.read_bytes(),
                receipt_artifact,
            )
        )
        secret = validate_secret_boundary(
            relay_paths=relay_paths,
            proxy_paths=proxy_paths,
            mock_paths=mock_paths,
            commands=create_commands,
            inspect_payloads=inspect_payloads,
            serialized_artifacts=captured,
            canary=canary,
        )
        if not secret.boundary_valid:
            raise Phase2ProviderRelayRuntimeError("secret_boundary_invalid")

        host_validation: dict[str, Any] = {}
        candidate_sha256 = ""
        rendered_sha256 = ""
        rendered = ""
        probe_results: dict[str, str] = {}
        accepted = False
        expected_rejection = scenario != "valid_typed_candidate" and mode == "run"
        errors: list[str] = []
        if mode == "probe":
            probe = _json_object(
                _read_regular(response_path, "network_probe_output_invalid"),
                "network_probe_output_invalid",
            )
            results = probe.get("results")
            if probe.get("probe_schema_version") != 1 or results != _PROBE_EXPECTED:
                raise Phase2ProviderRelayRuntimeError("network_probe_policy_invalid")
            probe_results = dict(results)
            accepted = True
        elif relay_run.returncode == 0:
            response = _json_object(
                _read_regular(response_path, "relay_response_invalid"),
                "relay_response_invalid",
            )
            try:
                parsed_candidate = validate_relay_response(response, request)
            except Phase2ProviderRelayError as exc:
                errors.append(exc.code)
            else:
                candidate = parsed_candidate.model_dump(mode="json")
                host = validate_isolated_candidate(repo_root, candidate, projection)
                host_validation = host.to_dict()
                candidate_sha256 = host.candidate_sha256
                if host.errors:
                    errors.extend(host.errors)
                else:
                    rendered = _render_valid_candidate(
                        repo_root,
                        candidate,
                        projection,
                    )
                    rendered_sha256 = hashlib.sha256(
                        rendered.encode("utf-8")
                    ).hexdigest()
                    if rendered_sha256 != host.rendered_sha256:
                        raise Phase2ProviderRelayRuntimeError(
                            "renderer_hash_binding_invalid"
                        )
                    accepted = True
        else:
            errors.append(str(relay_receipt.get("error_category") or "REJECTED"))

        if expected_rejection and accepted:
            raise Phase2ProviderRelayRuntimeError("invalid_scenario_accepted")
        if not expected_rejection and mode == "run" and not accepted:
            raise Phase2ProviderRelayRuntimeError("valid_scenario_host_blocked")
        outcome = _ScenarioOutcome(
            scenario=scenario,
            mode=mode,
            accepted=accepted,
            expected_rejection=expected_rejection,
            errors=sorted(set(errors)),
            relay_receipt=relay_receipt,
            host_validation=host_validation,
            candidate_sha256=candidate_sha256,
            rendered_sha256=rendered_sha256,
            rendered=rendered,
            probe_results=probe_results,
            contract={
                key: value.to_dict() for key, value in contract_items.items()
            },
            topology=topology.to_dict(),
            secret_boundary=secret.to_dict(),
            cleanup_complete=False,
        )
    except Phase2ProviderRelayRuntimeError as exc:
        failure = str(exc)
    finally:
        cleanup_complete = _cleanup_runtime(executor, containers, networks)
        shutil.rmtree(run_dir, ignore_errors=True)

    if not cleanup_complete:
        raise Phase2ProviderRelayRuntimeError("runtime_cleanup_failed")
    if failure is not None:
        raise Phase2ProviderRelayRuntimeError(failure)
    if outcome is None:
        raise Phase2ProviderRelayRuntimeError("scenario_result_missing")
    return _ScenarioOutcome(
        **{
            **asdict(outcome),
            "cleanup_complete": True,
        }
    )


def _tracked_secret_hits(repo_root: Path, canary: bytes) -> int:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return 1
    if result.returncode != 0:
        return 1
    hits = 0
    for raw_name in result.stdout.split(b"\0"):
        if not raw_name:
            continue
        try:
            path = repo_root / os.fsdecode(raw_name)
            if path.is_symlink() or not path.is_file():
                continue
            if canary in path.read_bytes():
                hits += 1
        except OSError:
            return 1
    return hits


def _write_artifact(root: Path, relative: str, raw: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_durable(path, raw, 0o644)


def _sanitized_receipt(outcome: _ScenarioOutcome, ordinal: int) -> dict[str, Any]:
    return {
        "receipt_schema_version": 1,
        "run_ordinal": ordinal,
        "scenario": outcome.scenario,
        "mode": outcome.mode,
        "accepted": outcome.accepted,
        "expected_rejection": outcome.expected_rejection,
        "errors": outcome.errors,
        "relay": outcome.relay_receipt,
        "host_validation": outcome.host_validation,
        "runtime_contract_valid": all(
            item.get("contract_valid") is True
            for item in outcome.contract.values()
        ),
        "topology_valid": outcome.topology.get("topology_valid") is True,
        "secret_boundary_valid": (
            outcome.secret_boundary.get("boundary_valid") is True
        ),
        "cleanup_complete": outcome.cleanup_complete,
    }


def _rejected_record(outcome: _ScenarioOutcome) -> dict[str, Any]:
    return {
        "rejection_schema_version": 1,
        "scenario": outcome.scenario,
        "status": "REJECTED",
        "route": "mock_preview/rejected",
        "error_codes": outcome.errors,
        "relay_error_category": outcome.relay_receipt.get("error_category"),
        "provider_http_status": outcome.relay_receipt.get("provider_http_status"),
        "response_size": outcome.relay_receipt.get("response_size"),
        "response_sha256": outcome.relay_receipt.get("response_sha256"),
        "retry_count": outcome.relay_receipt.get("retry_count"),
        "provider_attempt_count": outcome.relay_receipt.get(
            "provider_attempt_count"
        ),
        "host_validation": outcome.host_validation,
    }


def _publish_provider_relay(
    reports_root: Path,
    outcomes: Sequence[_ScenarioOutcome],
    evidence: dict[str, Any],
    publisher: Callable[[Path, Path], None],
) -> None:
    staging = Path(
        tempfile.mkdtemp(prefix=".phase2_provider_relay.staging-", dir=reports_root)
    )
    try:
        _write_artifact(
            staging,
            "runtime_evidence.json",
            canonical_json_bytes(evidence),
        )
        valid_written = False
        rejected_index = 0
        for ordinal, outcome in enumerate(outcomes, start=1):
            if outcome.mode == "run" and outcome.expected_rejection:
                rejected_index += 1
                _write_artifact(
                    staging,
                    (
                        "mock_preview/rejected/"
                        f"{rejected_index:02d}_{outcome.scenario}.json"
                    ),
                    canonical_json_bytes(_rejected_record(outcome)),
                )
            elif outcome.mode == "run" and outcome.accepted and not valid_written:
                _write_artifact(
                    staging,
                    "mock_preview/inbox/000403SZ_派林生物_provider_relay.md",
                    outcome.rendered.encode("utf-8"),
                )
                valid_written = True
            receipt_name = (
                "network_probe"
                if outcome.mode == "probe"
                else outcome.scenario
            )
            _write_artifact(
                staging,
                f"mock_preview/receipts/{ordinal:02d}_{receipt_name}.json",
                canonical_json_bytes(_sanitized_receipt(outcome, ordinal)),
            )
        publisher(staging, reports_root / "phase2_provider_relay")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def run_mock_provider_relay(
    repo_root: Path,
    reports_root: Path,
    *,
    _executor: Callable[[list[str]], subprocess.CompletedProcess[str]] = (
        _default_executor
    ),
    _allow_test_output_root: bool = False,
    _publisher: Callable[[Path, Path], None] = atomic_publish_directory,
    _secret_factory: Callable[[], str] = lambda: secrets.token_urlsafe(48),
    _request_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> ProviderRelayResult:
    """Run the fixed local Mock matrix once and atomically publish evidence."""
    errors: list[str] = []
    evidence: dict[str, Any] = {}
    cleanup_complete = True
    status = PHASE2B_PROVIDER_RELAY_E2E_BLOCKED
    try:
        repo_root, reports_root = _resolve_roots(
            repo_root,
            reports_root,
            allow_test_output_root=_allow_test_output_root,
        )
        docker_result = _execute(
            _executor,
            ["docker", "version", "--format", "{{json .Server}}"],
            error_code="docker_runtime_unavailable",
        )
        docker = _json_object(docker_result.stdout, "docker_server_info_invalid")
        if not all(
            isinstance(docker.get(field), str) and docker[field]
            for field in ("Version", "Os", "Arch")
        ):
            raise Phase2ProviderRelayRuntimeError("docker_server_info_invalid")

        image_evidence: dict[str, Any] = {}
        image_inspects: list[Any] = []
        for component, image in (
            ("relay", RELAY_IMAGE),
            ("proxy", PROXY_IMAGE),
            ("mock", MOCK_IMAGE),
        ):
            result = _execute(
                _executor,
                ["docker", "image", "inspect", image],
                error_code=f"{component}_image_unavailable",
            )
            image_inspects.append(
                _json_object_list(result.stdout, f"{component}_image_inspect_invalid")
            )
            image_evidence[component] = _image_runtime_evidence(
                result.stdout,
                image,
            )
        dockerfile_paths = {
            "relay": repo_root / "docker/phase2-provider-relay/Dockerfile",
            "proxy": repo_root / "docker/phase2-egress-proxy/Dockerfile",
            "mock": repo_root / "docker/phase2-mock-provider/Dockerfile",
        }
        for component, path in dockerfile_paths.items():
            image_evidence[component]["dockerfile_sha256"] = hashlib.sha256(
                _read_regular(path, "dockerfile_invalid", 65_536)
            ).hexdigest()

        facts_path = repo_root / _FIXED_FACTS_FILE
        facts_bytes = _read_regular(facts_path, "facts_source_invalid")
        facts = _json_object(facts_bytes, "facts_source_invalid")
        facts_sha256 = hashlib.sha256(facts_bytes).hexdigest()
        projection = build_worker_projection(
            facts,
            facts_sha256,
            facts_bytes=facts_bytes,
        )
        projection_bytes = canonical_json_bytes(projection)
        projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()

        outcomes: list[_ScenarioOutcome] = []
        canary = _secret_factory()
        if not isinstance(canary, str) or len(canary) < 32:
            raise Phase2ProviderRelayRuntimeError("secret_canary_invalid")
        canary_bytes = canary.encode("utf-8")
        with tempfile.TemporaryDirectory(
            prefix="tickflow-phase2-provider-relay-"
        ) as raw_temporary:
            temporary_root = Path(raw_temporary)
            os.chmod(temporary_root, 0o700)
            auth_path = temporary_root / "provider-auth"
            _write_durable(auth_path, canary_bytes, 0o444)
            for ordinal, scenario in enumerate(_SCENARIOS, start=1):
                outcomes.append(
                    _run_stack(
                        repo_root,
                        temporary_root,
                        projection,
                        projection_bytes,
                        scenario=scenario,
                        mode="run",
                        ordinal=ordinal,
                        canary=canary,
                        auth_path=auth_path,
                        request_id=_request_id_factory(),
                        executor=_executor,
                    )
                )
            outcomes.append(
                _run_stack(
                    repo_root,
                    temporary_root,
                    projection,
                    projection_bytes,
                    scenario="valid_typed_candidate",
                    mode="probe",
                    ordinal=len(_SCENARIOS) + 1,
                    canary=canary,
                    auth_path=auth_path,
                    request_id=_request_id_factory(),
                    executor=_executor,
                )
            )

            valid = [
                outcome
                for outcome in outcomes
                if outcome.mode == "run" and not outcome.expected_rejection
            ]
            invalid = [
                outcome
                for outcome in outcomes
                if outcome.mode == "run" and outcome.expected_rejection
            ]
            probe = next(outcome for outcome in outcomes if outcome.mode == "probe")
            candidate_hash_match = (
                len(valid) == 2
                and len({outcome.candidate_sha256 for outcome in valid}) == 1
                and bool(valid[0].candidate_sha256)
            )
            renderer_hash_match = (
                len(valid) == 2
                and len({outcome.rendered_sha256 for outcome in valid}) == 1
                and bool(valid[0].rendered_sha256)
            )
            if not candidate_hash_match or not renderer_hash_match:
                raise Phase2ProviderRelayRuntimeError(
                    "valid_scenario_determinism_failed"
                )
            invalid_accepted = sum(outcome.accepted for outcome in invalid)
            invalid_rejected = sum(not outcome.accepted for outcome in invalid)
            if len(invalid) != 21 or invalid_rejected != 21 or invalid_accepted:
                raise Phase2ProviderRelayRuntimeError("mock_matrix_fail_open")
            if probe.probe_results != _PROBE_EXPECTED:
                raise Phase2ProviderRelayRuntimeError("network_probe_policy_invalid")
            if any(
                not outcome.secret_boundary.get("boundary_valid")
                for outcome in outcomes
            ):
                raise Phase2ProviderRelayRuntimeError("secret_boundary_invalid")

            evidence = {
                "status": PHASE2B_PROVIDER_RELAY_READY,
                "errors": [],
                "scope": {
                    "symbol": "000403.SZ",
                    "provider": "local_mock_only",
                    "scenario_run_count": len(outcomes),
                },
                "docker": {
                    "server_version": docker["Version"],
                    "os": docker["Os"],
                    "arch": docker["Arch"],
                },
                "images": image_evidence,
                "source_binding": {
                    "facts_sha256": facts_sha256,
                    "projection_sha256": projection_sha256,
                    "symbol": projection["symbol"],
                },
                "runtime_contract": {
                    "all_runs_valid": all(
                        all(
                            item.get("contract_valid") is True
                            for item in outcome.contract.values()
                        )
                        for outcome in outcomes
                    ),
                    "relay_mount_count": 4,
                    "proxy_mount_count": 2,
                    "mock_mount_count": 3,
                    "non_root": True,
                    "rootfs_readonly": True,
                    "retry_count": 0,
                },
                "network_topology": {
                    "all_runs_valid": all(
                        outcome.topology.get("topology_valid") is True
                        for outcome in outcomes
                    ),
                    "network_count_per_run": 2,
                    "networks_internal": True,
                    "relay_can_resolve_mock": False,
                    "mock_can_resolve_relay": False,
                    "probe_results": probe.probe_results,
                },
                "secret_boundary": {
                    "secret_present": True,
                    "secret_value_hit_count": 0,
                    "secret_digest_recorded": False,
                    "secret_file_cleanup": True,
                    "relay_has_secret_mount": False,
                    "proxy_has_readonly_secret_mount": True,
                    "mock_has_readonly_secret_mount": True,
                },
                "mock_matrix": {
                    "valid_run_count": len(valid),
                    "invalid_rejected_count": invalid_rejected,
                    "invalid_accepted_count": invalid_accepted,
                    "provider_attempt_count": len(outcomes),
                    "retry_count": 0,
                    "scenarios": [
                        {
                            "scenario": outcome.scenario,
                            "mode": outcome.mode,
                            "accepted": outcome.accepted,
                            "expected_rejection": outcome.expected_rejection,
                            "errors": outcome.errors,
                        }
                        for outcome in outcomes
                    ],
                },
                "determinism": {
                    "candidate_hash_match": candidate_hash_match,
                    "renderer_hash_match": renderer_hash_match,
                    "candidate_sha256": valid[0].candidate_sha256,
                    "rendered_sha256": valid[0].rendered_sha256,
                },
                "cleanup": {
                    "cleanup_complete": True,
                    "container_residue_count": 0,
                    "network_residue_count": 0,
                    "secret_file_residue_count": 0,
                    "temporary_file_residue_count": 0,
                },
                "external_actions": {
                    "tickflow_api_request_count": 0,
                    "real_ai_call_count": 0,
                    "real_provider_attempt_count": 0,
                    "real_public_network_success_count": 0,
                    "cloud_mutation_count": 0,
                    "obsidian_real_vault_write": False,
                    "external_send_count": 0,
                    "integrated_gold_enabled": False,
                    "paper_trading_started": False,
                },
            }
            serialized_evidence = canonical_json_bytes(evidence)
            if canary_bytes in serialized_evidence or _tracked_secret_hits(
                repo_root,
                canary_bytes,
            ):
                raise Phase2ProviderRelayRuntimeError("secret_value_exposed")
        if auth_path.exists() or temporary_root.exists():
            cleanup_complete = False
            raise Phase2ProviderRelayRuntimeError("temporary_cleanup_failed")

        try:
            _publish_provider_relay(
                reports_root,
                outcomes,
                evidence,
                _publisher,
            )
        except OSError as exc:
            raise Phase2ProviderRelayRuntimeError(
                "atomic_publication_failed"
            ) from exc
        status = PHASE2B_PROVIDER_RELAY_READY
    except Phase2ProviderRelayRuntimeError as exc:
        errors.append(str(exc))
        if str(exc) in {"network_probe_policy_invalid", "network_topology_invalid"}:
            status = PHASE2B_PROVIDER_EGRESS_BLOCKED
        elif str(exc) in {"secret_boundary_invalid", "secret_value_exposed"}:
            status = PHASE2B_PROVIDER_SECRET_BLOCKED
        else:
            status = PHASE2B_PROVIDER_RELAY_E2E_BLOCKED
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"provider_relay_internal_error:{type(exc).__name__}")
        status = PHASE2B_PROVIDER_RELAY_E2E_BLOCKED
    return ProviderRelayResult(
        status=status,
        errors=sorted(set(errors)),
        cleanup_complete=cleanup_complete,
        evidence=evidence,
    )
