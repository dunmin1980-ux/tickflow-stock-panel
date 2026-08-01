"""Pure launcher contracts for a future single-call OpenAI Canary."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.services.phase2_provider_relay_runner import (
    EXPECTED_MEMORY,
    EXPECTED_NANO_CPUS,
    EXPECTED_PIDS_LIMIT,
    RUNTIME_USER,
    ContainerContractEvidence,
)

OPENAI_PROXY_IMAGE = "tickflow-phase2-openai-egress-proxy:canary-v1"
_RUNTIME_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROXY_NAME_PREFIX = "phase2-openai-proxy-"
_RELAY_NETWORK_PREFIX = "phase2-canary-relay-"
_EGRESS_NETWORK_PREFIX = "phase2-canary-egress-"
_EXPECTED_ENVIRONMENT = {
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
    "LANG=C.UTF-8",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
}


class OpenAICanaryRuntimeError(ValueError):
    """Raised when a future Canary command or inspect contract is unsafe."""


def _validate_name(value: str) -> None:
    if not isinstance(value, str) or _RUNTIME_NAME.fullmatch(value) is None:
        raise OpenAICanaryRuntimeError("runtime_name_invalid")


def _validate_role_name(value: str, prefix: str, category: str) -> None:
    _validate_name(value)
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise OpenAICanaryRuntimeError(category)


def _regular_path(value: Path, field: str, *, require_mode_0600: bool) -> Path:
    path = Path(os.path.abspath(os.fspath(value)))
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise OpenAICanaryRuntimeError(f"{field}_must_be_regular_file") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise OpenAICanaryRuntimeError(f"{field}_must_not_be_symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise OpenAICanaryRuntimeError(f"{field}_must_be_regular_file")
    if require_mode_0600 and stat.S_IMODE(metadata.st_mode) != 0o600:
        raise OpenAICanaryRuntimeError(f"{field}_mode_invalid")
    return path.resolve(strict=True)


@dataclass(frozen=True)
class OpenAICanaryRuntimePaths:
    auth: Path
    receipt: Path

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "auth",
            _regular_path(self.auth, "auth", require_mode_0600=True),
        )
        object.__setattr__(
            self,
            "receipt",
            _regular_path(self.receipt, "receipt", require_mode_0600=False),
        )


def _mount(source: Path, destination: str, *, readonly: bool) -> str:
    suffix = ",readonly" if readonly else ""
    return f"type=bind,src={source},dst={destination}{suffix}"


def _common_create_prefix(name: str, relay_network: str) -> list[str]:
    _validate_role_name(name, _PROXY_NAME_PREFIX, "proxy_name_invalid")
    _validate_name(relay_network)
    return [
        "docker",
        "create",
        "--name",
        name,
        "--network",
        relay_network,
        "--network-alias",
        "phase2-egress-proxy",
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


def build_openai_proxy_create_command(
    name: str,
    relay_network: str,
    paths: OpenAICanaryRuntimePaths,
    *,
    image_id: str,
) -> list[str]:
    """Build, but never execute, the hardened dedicated Proxy create command."""
    if not isinstance(paths, OpenAICanaryRuntimePaths):
        raise OpenAICanaryRuntimeError("runtime_paths_invalid")
    if not isinstance(image_id, str) or _IMAGE_ID.fullmatch(image_id) is None:
        raise OpenAICanaryRuntimeError("image_id_invalid")
    return [
        *_common_create_prefix(name, relay_network),
        "--mount",
        _mount(paths.auth, "/run/phase2/provider-auth", readonly=True),
        "--mount",
        _mount(paths.receipt, "/output/receipt.json", readonly=False),
        image_id,
    ]


def build_canary_relay_network_command(network: str) -> list[str]:
    _validate_role_name(
        network,
        _RELAY_NETWORK_PREFIX,
        "relay_network_name_invalid",
    )
    return [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        network,
    ]


def build_canary_egress_network_command(network: str) -> list[str]:
    _validate_role_name(
        network,
        _EGRESS_NETWORK_PREFIX,
        "egress_network_name_invalid",
    )
    return [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        network,
    ]


def build_openai_proxy_egress_attach_command(
    network: str,
    container: str,
) -> list[str]:
    _validate_role_name(
        network,
        _EGRESS_NETWORK_PREFIX,
        "egress_network_name_invalid",
    )
    _validate_role_name(
        container,
        _PROXY_NAME_PREFIX,
        "proxy_name_invalid",
    )
    return ["docker", "network", "connect", network, container]


def _inspect_item(payload: Any) -> dict[str, Any] | None:
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        return None
    return payload[0]


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _restart_disabled(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("Name") in {"", "no"}
        and value.get("MaximumRetryCount", 0) == 0
    )


def _expected_mounts(paths: OpenAICanaryRuntimePaths) -> dict[str, tuple[Path, bool]]:
    return {
        "/run/phase2/provider-auth": (paths.auth, False),
        "/output/receipt.json": (paths.receipt, True),
    }


def validate_openai_proxy_inspect(
    payload: Any,
    *,
    paths: OpenAICanaryRuntimePaths,
    relay_network: str,
    egress_network: str,
    expected_image_id: str,
    expected_state: Literal["created", "running", "exited"],
) -> ContainerContractEvidence:
    """Validate an inspect-shaped value without returning names or paths."""
    if not isinstance(paths, OpenAICanaryRuntimePaths):
        raise OpenAICanaryRuntimeError("runtime_paths_invalid")
    _validate_name(relay_network)
    _validate_role_name(
        egress_network,
        _EGRESS_NETWORK_PREFIX,
        "egress_network_name_invalid",
    )
    if (
        not isinstance(expected_image_id, str)
        or _IMAGE_ID.fullmatch(expected_image_id) is None
    ):
        raise OpenAICanaryRuntimeError("image_id_invalid")
    if expected_state not in {"created", "running", "exited"}:
        raise OpenAICanaryRuntimeError("runtime_state_invalid")
    errors: list[str] = []
    item = _inspect_item(payload)
    if item is None:
        errors.append("runtime_inspect_invalid")
        item = {}
    config = _mapping(item.get("Config"))
    host = _mapping(item.get("HostConfig"))
    state = _mapping(item.get("State"))
    network_settings = _mapping(item.get("NetworkSettings"))
    mounts = item.get("Mounts") if isinstance(item.get("Mounts"), list) else []

    non_root = config.get("User") == RUNTIME_USER
    image_valid = (
        config.get("Image") == expected_image_id
        and item.get("Image") == expected_image_id
    )
    entrypoint_valid = (
        config.get("Entrypoint") == ["/usr/bin/python3", "/proxy/proxy.py"]
        and config.get("Cmd") is None
        and item.get("Path") == "/usr/bin/python3"
        and item.get("Args") == ["/proxy/proxy.py"]
    )
    environment = config.get("Env")
    environment_clean = (
        isinstance(environment, list)
        and len(environment) == len(_EXPECTED_ENVIRONMENT)
        and set(environment) == _EXPECTED_ENVIRONMENT
    )
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
    restart_disabled = _restart_disabled(host.get("RestartPolicy"))
    privileged_disabled = host.get("Privileged") is False
    devices = host.get("Devices") if isinstance(host.get("Devices"), list) else []
    device_count = len(devices)
    host_ports = _mapping(host.get("PortBindings"))
    network_ports = _mapping(network_settings.get("Ports"))
    published_port_count = len(set(host_ports) | set(network_ports))

    networks = network_settings.get("Networks")
    network_map = networks if isinstance(networks, dict) else {}
    actual_networks = set(network_map)
    networks_valid = (
        host.get("NetworkMode") == relay_network
        and actual_networks == {relay_network, egress_network}
    )
    relay_value = _mapping(network_map.get(relay_network))
    egress_value = _mapping(network_map.get(egress_network))
    relay_aliases = relay_value.get("Aliases")
    egress_aliases = egress_value.get("Aliases")
    network_aliases_valid = (
        isinstance(relay_aliases, list)
        and "phase2-egress-proxy" in relay_aliases
        and isinstance(egress_aliases, list)
        and "phase2-egress-proxy" not in egress_aliases
    )
    state_valid = state.get("Status") == expected_state
    if expected_state == "exited":
        state_valid = state_valid and state.get("ExitCode") == 0

    checks = {
        "runtime_user_invalid": non_root,
        "runtime_image_invalid": image_valid,
        "runtime_entrypoint_invalid": entrypoint_valid,
        "runtime_environment_invalid": environment_clean,
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
        "runtime_network_alias_invalid": network_aliases_valid,
        "runtime_state_invalid": state_valid,
    }
    errors.extend(code for code, passed in checks.items() if not passed)

    expected_mounts = _expected_mounts(paths)
    mount_by_destination = {
        mount.get("Destination"): mount
        for mount in mounts
        if isinstance(mount, dict) and isinstance(mount.get("Destination"), str)
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
        for destination, (source, writable) in expected_mounts.items():
            mount = mount_by_destination[destination]
            source_valid = source_valid and (
                mount.get("Type") == "bind"
                and Path(str(mount.get("Source"))).resolve(strict=False) == source
            )
            mode_valid = mode_valid and mount.get("RW") is writable
    if not source_valid:
        errors.append("runtime_mount_source_invalid")
    if not mode_valid:
        errors.append("runtime_mount_mode_invalid")
    mounts_valid = mount_set_valid and source_valid and mode_valid

    unique_errors = sorted(set(errors))
    return ContainerContractEvidence(
        component="proxy",
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
        network_aliases_valid=network_aliases_valid,
    )
