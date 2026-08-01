"""Host-side runtime contracts for the restricted Phase 2B Provider Relay."""

from __future__ import annotations

import hmac
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

RUNTIME_USER = "65532:65532"
EXPECTED_PIDS_LIMIT = 16
EXPECTED_MEMORY = 128 * 1024 * 1024
EXPECTED_NANO_CPUS = 500_000_000
RELAY_IMAGE = "tickflow-phase2-provider-relay:runtime-v1"
PROXY_IMAGE = "tickflow-phase2-egress-proxy:runtime-v1"
MOCK_IMAGE = "tickflow-phase2-mock-provider:runtime-v1"
_RUNTIME_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_SENSITIVE_ENV_NAME = re.compile(
    r"(?:^|_)(?:api_?key|authorization|cookie|credential|password|secret|session|token)(?:$|_)",
    re.IGNORECASE,
)

Component = Literal["relay", "proxy", "mock"]


class Phase2ProviderRelayRuntimeError(ValueError):
    """Raised when a runtime contract cannot be constructed safely."""


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
        state_valid = state_valid and state.get("ExitCode") == 0

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
