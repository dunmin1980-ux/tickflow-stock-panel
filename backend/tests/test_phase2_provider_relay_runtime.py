from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import uuid
from pathlib import Path
from types import ModuleType

import pytest

import app.services.phase2_provider_relay_runner as provider_relay_runner
from app.services.phase2_ai_worker_protocol import FakeClaimsWorker
from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_provider_relay_protocol import (
    Phase2ProviderRelayError,
    build_relay_request,
    parse_single_json_object,
    validate_relay_response,
)
from app.services.phase2_provider_relay_runner import (
    PHASE2B_PROVIDER_RELAY_E2E_BLOCKED,
    PHASE2B_PROVIDER_RELAY_READY,
    MockRuntimePaths,
    Phase2ProviderRelayRuntimeError,
    ProviderRelayResult,
    ProxyRuntimePaths,
    RelayRuntimePaths,
    build_mock_create_command,
    build_network_connect_command,
    build_network_create_command,
    build_proxy_create_command,
    build_relay_create_command,
    run_mock_provider_relay,
    validate_container_inspect,
    validate_network_topology,
    validate_secret_boundary,
)
from scripts.run_phase2_mock_provider_relay import parse_args as parse_run_args
from scripts.validate_phase2_provider_relay import parse_args as parse_validate_args
from scripts.validate_phase2_provider_relay import validate_provider_relay_artifacts

RELAY_IMAGE = "tickflow-phase2-provider-relay:runtime-v1"
PROXY_IMAGE = "tickflow-phase2-egress-proxy:runtime-v1"
MOCK_IMAGE = "tickflow-phase2-mock-provider:runtime-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_PROVIDER_PATH = REPO_ROOT / "docker/phase2-mock-provider/mock_provider.py"


def _file(path: Path, value: bytes = b"{}\n", mode: int = 0o600) -> Path:
    path.write_bytes(value)
    path.chmod(mode)
    return path


def _paths(tmp_path: Path):
    auth = _file(tmp_path / "provider-auth", uuid.uuid4().hex.encode(), 0o400)
    relay = RelayRuntimePaths(
        request=_file(tmp_path / "request.json", mode=0o444),
        projection=_file(tmp_path / "projection.json", mode=0o444),
        response=_file(tmp_path / "response.json", b"\n", 0o666),
        receipt=_file(tmp_path / "relay-receipt.json", b"\n", 0o666),
    )
    proxy = ProxyRuntimePaths(
        auth=auth,
        receipt=_file(tmp_path / "proxy-receipt.json", b"\n", 0o666),
    )
    mock = MockRuntimePaths(
        auth=auth,
        scenario=_file(tmp_path / "scenario.json", mode=0o444),
        receipt=_file(tmp_path / "mock-receipt.json", b"\n", 0o666),
    )
    return relay, proxy, mock


def test_readiness_gate_tolerates_slow_docker_desktop_bind_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _file(tmp_path / "ready.json", b"\n", 0o666)
    clock = iter((0.0, 0.0, 4.0))
    monkeypatch.setattr(
        provider_relay_runner.time,
        "monotonic",
        lambda: next(clock),
    )
    monkeypatch.setattr(
        provider_relay_runner.time,
        "sleep",
        lambda _seconds: receipt.write_bytes(
            canonical_json_bytes(
                {
                    "receipt_schema_version": 1,
                    "status": "READY",
                    "auth_present": True,
                }
            )
        ),
    )

    provider_relay_runner._wait_ready(receipt, "ready_blocked")


def test_terminal_receipt_waits_for_timeout_provider_to_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _file(
        tmp_path / "mock-receipt.json",
        canonical_json_bytes(
            {
                "receipt_schema_version": 1,
                "status": "READY",
                "auth_present": True,
            }
        ),
        0o666,
    )
    terminal = {
        "receipt_schema_version": 1,
        "request_count": 1,
        "method_allowed": True,
        "path_allowed": True,
        "auth_valid": True,
        "envelope_valid": True,
        "scenario": "timeout",
        "response_category": "SCENARIO_RESPONSE",
        "http_status": 200,
        "response_size": 1024,
    }
    clock = iter((0.0, 0.0, 4.0))
    monkeypatch.setattr(
        provider_relay_runner.time,
        "monotonic",
        lambda: next(clock),
    )
    monkeypatch.setattr(
        provider_relay_runner.time,
        "sleep",
        lambda _seconds: receipt.write_bytes(canonical_json_bytes(terminal)),
    )

    value = provider_relay_runner._wait_service_receipt(receipt, "mock")

    assert value["scenario"] == "timeout"


def _common_create_prefix(name: str, network: str) -> list[str]:
    return [
        "docker",
        "create",
        "--name",
        name,
        "--network",
        network,
        "--read-only",
        "--user",
        "65532:65532",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "16",
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


def test_network_and_container_commands_are_exact_and_closed(tmp_path: Path) -> None:
    relay_paths, proxy_paths, mock_paths = _paths(tmp_path)
    relay_network = "phase2-relay-proxy-a1b2c3"
    provider_network = "phase2-proxy-provider-d4e5f6"
    relay_name = "phase2-relay-a1b2c3"
    proxy_name = "phase2-proxy-a1b2c3"
    mock_name = "phase2-mock-a1b2c3"

    assert build_network_create_command(relay_network) == [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        relay_network,
    ]
    assert build_network_create_command(provider_network) == [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        provider_network,
    ]
    assert build_relay_create_command(
        relay_name,
        relay_network,
        relay_paths,
        mode="run",
    ) == [
        *_common_create_prefix(relay_name, relay_network),
        "--mount",
        _mount(relay_paths.request, "/input/request.json", readonly=True),
        "--mount",
        _mount(relay_paths.projection, "/input/projection.json", readonly=True),
        "--mount",
        _mount(relay_paths.response, "/output/response.json", readonly=False),
        "--mount",
        _mount(relay_paths.receipt, "/output/receipt.json", readonly=False),
        RELAY_IMAGE,
        "run",
    ]
    assert build_proxy_create_command(
        proxy_name,
        provider_network,
        proxy_paths,
    ) == [
        *_common_create_prefix(proxy_name, provider_network),
        "--mount",
        _mount(proxy_paths.auth, "/run/phase2/provider-auth", readonly=True),
        "--mount",
        _mount(proxy_paths.receipt, "/output/receipt.json", readonly=False),
        PROXY_IMAGE,
    ]
    assert build_mock_create_command(
        mock_name,
        provider_network,
        mock_paths,
    ) == [
        *_common_create_prefix(mock_name, provider_network),
        "--network-alias",
        "phase2-mock-provider",
        "--mount",
        _mount(mock_paths.auth, "/run/phase2/provider-auth", readonly=True),
        "--mount",
        _mount(mock_paths.scenario, "/input/scenario.json", readonly=True),
        "--mount",
        _mount(mock_paths.receipt, "/output/receipt.json", readonly=False),
        MOCK_IMAGE,
    ]
    assert build_network_connect_command(relay_network, proxy_name) == [
        "docker",
        "network",
        "connect",
        "--alias",
        "phase2-egress-proxy",
        relay_network,
        proxy_name,
    ]


def test_commands_contain_no_override_or_forbidden_runtime_surface(
    tmp_path: Path,
) -> None:
    relay_paths, proxy_paths, mock_paths = _paths(tmp_path)
    commands = [
        build_relay_create_command(
            "phase2-relay-a1b2c3",
            "phase2-relay-proxy-a1b2c3",
            relay_paths,
            mode="probe",
        ),
        build_proxy_create_command(
            "phase2-proxy-a1b2c3",
            "phase2-proxy-provider-a1b2c3",
            proxy_paths,
        ),
        build_mock_create_command(
            "phase2-mock-a1b2c3",
            "phase2-proxy-provider-a1b2c3",
            mock_paths,
        ),
    ]

    for command in commands:
        assert command.count("--network") == 1
        assert "--read-only" in command
        assert "--privileged" not in command
        assert "--publish" not in command
        assert "-p" not in command
        assert "--env" not in command
        assert "--env-file" not in command
        assert "--device" not in command
        joined = " ".join(command).lower()
        assert all(
            forbidden not in joined
            for forbidden in (
                "network host",
                "/users/",
                "/home/",
                "/workspace",
                "/.ssh",
                "/.config",
                "/.obsidian",
                "docker.sock",
                "postgres",
            )
        )
    assert commands[0].count("--mount") == 4
    assert commands[1].count("--mount") == 2
    assert commands[2].count("--mount") == 3
    assert "/run/phase2/provider-auth" not in " ".join(commands[0])


@pytest.mark.parametrize(
    "invalid_name",
    ["", "UPPER", "contains space", "../escape", "a" * 65],
)
def test_command_builders_reject_invalid_runtime_names(
    tmp_path: Path,
    invalid_name: str,
) -> None:
    relay_paths, _, _ = _paths(tmp_path)

    with pytest.raises(Phase2ProviderRelayRuntimeError, match="runtime_name_invalid"):
        build_relay_create_command(
            invalid_name,
            "phase2-relay-proxy-a1b2c3",
            relay_paths,
            mode="run",
        )


def test_runtime_paths_reject_symlinks_and_non_regular_files(tmp_path: Path) -> None:
    regular = _file(tmp_path / "regular.json")
    symlink = tmp_path / "linked.json"
    symlink.symlink_to(regular)

    with pytest.raises(Phase2ProviderRelayRuntimeError, match="must_not_be_symlink"):
        RelayRuntimePaths(
            request=symlink,
            projection=regular,
            response=regular,
            receipt=regular,
        )
    with pytest.raises(Phase2ProviderRelayRuntimeError, match="must_be_regular_file"):
        ProxyRuntimePaths(auth=tmp_path, receipt=regular)


def _inspect_payload(
    component: str,
    paths,
    *,
    primary_network: str,
    networks: set[str],
    state: str = "created",
) -> list[dict]:
    image = {
        "relay": RELAY_IMAGE,
        "proxy": PROXY_IMAGE,
        "mock": MOCK_IMAGE,
    }[component]
    if component == "relay":
        mount_specs = [
            (paths.request, "/input/request.json", False),
            (paths.projection, "/input/projection.json", False),
            (paths.response, "/output/response.json", True),
            (paths.receipt, "/output/receipt.json", True),
        ]
    elif component == "proxy":
        mount_specs = [
            (paths.auth, "/run/phase2/provider-auth", False),
            (paths.receipt, "/output/receipt.json", True),
        ]
    else:
        mount_specs = [
            (paths.auth, "/run/phase2/provider-auth", False),
            (paths.scenario, "/input/scenario.json", False),
            (paths.receipt, "/output/receipt.json", True),
        ]
    network_values = {network: {"Aliases": []} for network in networks}
    if component == "proxy":
        relay_network = next(
            network for network in networks if "relay-proxy" in network
        )
        network_values[relay_network]["Aliases"] = ["phase2-egress-proxy"]
    elif component == "mock":
        network_values[primary_network]["Aliases"] = ["phase2-mock-provider"]
    return [
        {
            "Config": {
                "User": "65532:65532",
                "Image": image,
                "Env": ["PYTHONDONTWRITEBYTECODE=1", "PYTHONHASHSEED=0"],
                "Labels": {},
            },
            "HostConfig": {
                "NetworkMode": primary_network,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "PidsLimit": 16,
                "Memory": 134_217_728,
                "NanoCpus": 500_000_000,
                "IpcMode": "none",
                "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
                "Privileged": False,
                "PortBindings": {},
                "Devices": [],
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(source),
                    "Destination": destination,
                    "RW": writable,
                }
                for source, destination, writable in mount_specs
            ],
            "NetworkSettings": {
                "Networks": network_values,
                "Ports": {},
            },
            "State": {"Status": state, "ExitCode": 0},
        }
    ]


@pytest.mark.parametrize("component", ["relay", "proxy", "mock"])
def test_container_inspect_accepts_only_exact_runtime_contract(
    tmp_path: Path,
    component: str,
) -> None:
    relay_paths, proxy_paths, mock_paths = _paths(tmp_path)
    paths = {"relay": relay_paths, "proxy": proxy_paths, "mock": mock_paths}[
        component
    ]
    relay_network = "phase2-relay-proxy-a1b2c3"
    provider_network = "phase2-proxy-provider-a1b2c3"
    primary = relay_network if component == "relay" else provider_network
    networks = {
        "relay": {relay_network},
        "proxy": {relay_network, provider_network},
        "mock": {provider_network},
    }[component]

    evidence = validate_container_inspect(
        _inspect_payload(
            component,
            paths,
            primary_network=primary,
            networks=networks,
        ),
        component=component,
        paths=paths,
        expected_primary_network=primary,
        expected_networks=networks,
        expected_state="created",
    )

    assert evidence.contract_valid is True
    assert evidence.errors == []


@pytest.mark.parametrize("component", ["proxy", "mock"])
def test_container_inspect_requires_fixed_service_network_alias(
    tmp_path: Path,
    component: str,
) -> None:
    _relay_paths, proxy_paths, mock_paths = _paths(tmp_path)
    paths = {"proxy": proxy_paths, "mock": mock_paths}[component]
    relay_network = "phase2-relay-proxy-a1b2c3"
    provider_network = "phase2-proxy-provider-a1b2c3"
    networks = {
        "proxy": {relay_network, provider_network},
        "mock": {provider_network},
    }[component]
    payload = _inspect_payload(
        component,
        paths,
        primary_network=provider_network,
        networks=networks,
    )
    for value in payload[0]["NetworkSettings"]["Networks"].values():
        value["Aliases"] = []

    evidence = validate_container_inspect(
        payload,
        component=component,
        paths=paths,
        expected_primary_network=provider_network,
        expected_networks=networks,
        expected_state="created",
    )

    assert evidence.contract_valid is False
    assert evidence.network_aliases_valid is False
    assert "runtime_network_alias_invalid" in evidence.errors
    assert evidence.non_root is True
    assert evidence.rootfs_readonly is True
    assert evidence.cap_drop_all is True
    assert evidence.no_new_privileges is True
    assert evidence.published_port_count == 0
    assert evidence.device_count == 0
    assert evidence.mount_count == {"relay": 4, "proxy": 2, "mock": 3}[component]
    serialized = json.dumps(evidence.to_dict(), sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "phase2-relay-proxy" not in serialized


@pytest.mark.parametrize(
    ("section", "field", "value", "error_code"),
    [
        ("Config", "User", "0:0", "runtime_user_invalid"),
        ("Config", "Image", "unapproved:latest", "runtime_image_invalid"),
        ("HostConfig", "ReadonlyRootfs", False, "runtime_rootfs_not_readonly"),
        ("HostConfig", "CapDrop", [], "runtime_cap_drop_invalid"),
        ("HostConfig", "SecurityOpt", [], "runtime_security_opt_invalid"),
        ("HostConfig", "PidsLimit", 0, "runtime_pids_limit_invalid"),
        ("HostConfig", "Memory", 0, "runtime_memory_limit_invalid"),
        ("HostConfig", "NanoCpus", 0, "runtime_cpu_limit_invalid"),
        ("HostConfig", "IpcMode", "private", "runtime_ipc_mode_invalid"),
        (
            "HostConfig",
            "RestartPolicy",
            {"Name": "always", "MaximumRetryCount": 0},
            "runtime_restart_policy_invalid",
        ),
        ("HostConfig", "Privileged", True, "runtime_privileged"),
        ("HostConfig", "PortBindings", {"80/tcp": [{}]}, "runtime_ports_published"),
        ("HostConfig", "Devices", [{"PathOnHost": "/dev/null"}], "runtime_devices_present"),
    ],
)
def test_container_inspect_rejects_each_security_regression(
    tmp_path: Path,
    section: str,
    field: str,
    value: object,
    error_code: str,
) -> None:
    relay_paths, _, _ = _paths(tmp_path)
    network = "phase2-relay-proxy-a1b2c3"
    payload = _inspect_payload(
        "relay",
        relay_paths,
        primary_network=network,
        networks={network},
    )
    payload[0][section][field] = value

    evidence = validate_container_inspect(
        payload,
        component="relay",
        paths=relay_paths,
        expected_primary_network=network,
        expected_networks={network},
        expected_state="created",
    )

    assert error_code in evidence.errors
    assert evidence.contract_valid is False


def test_container_inspect_rejects_mount_network_state_and_sensitive_env_mutations(
    tmp_path: Path,
) -> None:
    relay_paths, _, _ = _paths(tmp_path)
    network = "phase2-relay-proxy-a1b2c3"

    mutations = []
    extra_mount = _inspect_payload(
        "relay", relay_paths, primary_network=network, networks={network}
    )
    extra_mount[0]["Mounts"].append(
        {
            "Type": "bind",
            "Source": "/forbidden",
            "Destination": "/workspace",
            "RW": False,
        }
    )
    mutations.append((extra_mount, "runtime_mount_set_invalid"))

    wrong_mode = _inspect_payload(
        "relay", relay_paths, primary_network=network, networks={network}
    )
    wrong_mode[0]["Mounts"][0]["RW"] = True
    mutations.append((wrong_mode, "runtime_mount_mode_invalid"))

    wrong_source = _inspect_payload(
        "relay", relay_paths, primary_network=network, networks={network}
    )
    wrong_source[0]["Mounts"][0]["Source"] = "/forbidden"
    mutations.append((wrong_source, "runtime_mount_source_invalid"))

    wrong_network = _inspect_payload(
        "relay", relay_paths, primary_network=network, networks={network}
    )
    wrong_network[0]["NetworkSettings"]["Networks"]["bridge"] = {}
    mutations.append((wrong_network, "runtime_network_membership_invalid"))

    wrong_primary = _inspect_payload(
        "relay", relay_paths, primary_network=network, networks={network}
    )
    wrong_primary[0]["HostConfig"]["NetworkMode"] = "bridge"
    mutations.append((wrong_primary, "runtime_network_membership_invalid"))

    network_port = _inspect_payload(
        "relay", relay_paths, primary_network=network, networks={network}
    )
    network_port[0]["NetworkSettings"]["Ports"] = {"8080/tcp": [{}]}
    mutations.append((network_port, "runtime_ports_published"))

    wrong_state = _inspect_payload(
        "relay", relay_paths, primary_network=network, networks={network}
    )
    wrong_state[0]["State"]["Status"] = "running"
    mutations.append((wrong_state, "runtime_state_invalid"))

    sensitive_env = _inspect_payload(
        "relay", relay_paths, primary_network=network, networks={network}
    )
    sensitive_env[0]["Config"]["Env"].append("PROVIDER_TOKEN=forbidden")
    mutations.append((sensitive_env, "runtime_sensitive_environment"))

    for payload, error_code in mutations:
        evidence = validate_container_inspect(
            payload,
            component="relay",
            paths=relay_paths,
            expected_primary_network=network,
            expected_networks={network},
            expected_state="created",
        )
        assert error_code in evidence.errors


def test_container_inspect_rejects_nonzero_exited_state(tmp_path: Path) -> None:
    relay_paths, _, _ = _paths(tmp_path)
    network = "phase2-relay-proxy-a1b2c3"
    payload = _inspect_payload(
        "relay",
        relay_paths,
        primary_network=network,
        networks={network},
        state="exited",
    )
    payload[0]["State"]["ExitCode"] = 7

    evidence = validate_container_inspect(
        payload,
        component="relay",
        paths=relay_paths,
        expected_primary_network=network,
        expected_networks={network},
        expected_state="exited",
    )

    assert "runtime_state_invalid" in evidence.errors


def test_container_inspect_accepts_explicit_rejected_exit_code(tmp_path: Path) -> None:
    relay_paths, _, _ = _paths(tmp_path)
    network = "phase2-relay-proxy-a1b2c3"
    payload = _inspect_payload(
        "relay",
        relay_paths,
        primary_network=network,
        networks={network},
        state="exited",
    )
    payload[0]["State"]["ExitCode"] = 2

    evidence = validate_container_inspect(
        payload,
        component="relay",
        paths=relay_paths,
        expected_primary_network=network,
        expected_networks={network},
        expected_state="exited",
        expected_exit_code=2,
    )

    assert evidence.contract_valid is True
    assert evidence.state_valid is True


def _network_payload(name: str, members: list[str]) -> list[dict]:
    return [
        {
            "Name": name,
            "Driver": "bridge",
            "Internal": True,
            "Attachable": False,
            "Ingress": False,
            "Containers": {
                f"container-{index}": {"Name": member, "IPv4Address": ""}
                for index, member in enumerate(members, start=1)
            },
        }
    ]


def test_network_topology_requires_two_internal_exact_membership_sets() -> None:
    relay_network = "phase2-relay-proxy-a1b2c3"
    provider_network = "phase2-proxy-provider-a1b2c3"
    relay_name = "phase2-relay-a1b2c3"
    proxy_name = "phase2-proxy-a1b2c3"
    mock_name = "phase2-mock-a1b2c3"

    evidence = validate_network_topology(
        _network_payload(relay_network, [relay_name, proxy_name]),
        _network_payload(provider_network, [proxy_name, mock_name]),
        relay_network=relay_network,
        provider_network=provider_network,
        relay_name=relay_name,
        proxy_name=proxy_name,
        mock_name=mock_name,
    )

    assert evidence.topology_valid is True
    assert evidence.errors == []
    assert evidence.internal_network_count == 2
    assert evidence.relay_network_member_count == 2
    assert evidence.provider_network_member_count == 2
    assert evidence.relay_can_resolve_mock is False
    assert evidence.mock_can_resolve_relay is False


def test_network_topology_accepts_created_relay_endpoint_absent_from_network_inspect() -> None:
    relay_network = "phase2-relay-proxy-a1b2c3"
    provider_network = "phase2-proxy-provider-a1b2c3"
    relay_name = "phase2-relay-a1b2c3"
    proxy_name = "phase2-proxy-a1b2c3"
    mock_name = "phase2-mock-a1b2c3"

    evidence = validate_network_topology(
        _network_payload(relay_network, [proxy_name]),
        _network_payload(provider_network, [proxy_name, mock_name]),
        relay_network=relay_network,
        provider_network=provider_network,
        relay_name=relay_name,
        proxy_name=proxy_name,
        mock_name=mock_name,
        relay_runtime_active=False,
    )

    assert evidence.topology_valid is True
    assert evidence.errors == []
    assert evidence.relay_network_member_count == 1


@pytest.mark.parametrize(
    ("network_index", "field", "value", "error_code"),
    [
        (0, "Internal", False, "relay_proxy_network_not_internal"),
        (1, "Internal", False, "proxy_provider_network_not_internal"),
        (0, "Driver", "overlay", "relay_proxy_network_driver_invalid"),
        (1, "Attachable", True, "proxy_provider_network_attachable"),
        (1, "Ingress", True, "proxy_provider_network_ingress"),
    ],
)
def test_network_topology_rejects_policy_mutations(
    network_index: int,
    field: str,
    value: object,
    error_code: str,
) -> None:
    relay_network = "phase2-relay-proxy-a1b2c3"
    provider_network = "phase2-proxy-provider-a1b2c3"
    names = (
        "phase2-relay-a1b2c3",
        "phase2-proxy-a1b2c3",
        "phase2-mock-a1b2c3",
    )
    payloads = [
        _network_payload(relay_network, [names[0], names[1]]),
        _network_payload(provider_network, [names[1], names[2]]),
    ]
    payloads[network_index][0][field] = value

    evidence = validate_network_topology(
        payloads[0],
        payloads[1],
        relay_network=relay_network,
        provider_network=provider_network,
        relay_name=names[0],
        proxy_name=names[1],
        mock_name=names[2],
    )

    assert error_code in evidence.errors
    assert evidence.topology_valid is False


def test_network_topology_rejects_direct_relay_mock_membership() -> None:
    relay_network = "phase2-relay-proxy-a1b2c3"
    provider_network = "phase2-proxy-provider-a1b2c3"
    relay_name = "phase2-relay-a1b2c3"
    proxy_name = "phase2-proxy-a1b2c3"
    mock_name = "phase2-mock-a1b2c3"

    evidence = validate_network_topology(
        _network_payload(relay_network, [relay_name, proxy_name, mock_name]),
        _network_payload(provider_network, [relay_name, proxy_name, mock_name]),
        relay_network=relay_network,
        provider_network=provider_network,
        relay_name=relay_name,
        proxy_name=proxy_name,
        mock_name=mock_name,
    )

    assert "relay_proxy_network_members_invalid" in evidence.errors
    assert "proxy_provider_network_members_invalid" in evidence.errors
    assert evidence.relay_can_resolve_mock is True
    assert evidence.mock_can_resolve_relay is True


def test_secret_boundary_requires_no_relay_mount_and_zero_value_hits(
    tmp_path: Path,
) -> None:
    relay_paths, proxy_paths, mock_paths = _paths(tmp_path)
    canary = uuid.uuid4().hex + uuid.uuid4().hex
    proxy_paths.auth.chmod(0o600)
    proxy_paths.auth.write_text(canary, encoding="utf-8")
    proxy_paths.auth.chmod(0o400)
    commands = [
        build_relay_create_command(
            "phase2-relay-a1b2c3",
            "phase2-relay-proxy-a1b2c3",
            relay_paths,
            mode="run",
        ),
        build_proxy_create_command(
            "phase2-proxy-a1b2c3",
            "phase2-proxy-provider-a1b2c3",
            proxy_paths,
        ),
        build_mock_create_command(
            "phase2-mock-a1b2c3",
            "phase2-proxy-provider-a1b2c3",
            mock_paths,
        ),
    ]
    evidence = validate_secret_boundary(
        relay_paths=relay_paths,
        proxy_paths=proxy_paths,
        mock_paths=mock_paths,
        commands=commands,
        inspect_payloads=[],
        serialized_artifacts=[],
        canary=canary,
    )

    assert evidence.boundary_valid is True
    assert evidence.secret_present is True
    assert evidence.secret_value_hit_count == 0
    assert evidence.secret_digest_recorded is False
    assert evidence.relay_has_secret_mount is False
    assert evidence.proxy_has_readonly_secret_mount is True
    assert evidence.mock_has_readonly_secret_mount is True
    serialized = json.dumps(evidence.to_dict(), sort_keys=True)
    assert canary not in serialized
    assert "sha256" not in serialized.lower()


def test_secret_boundary_rejects_exact_value_in_inspect_or_artifact(
    tmp_path: Path,
) -> None:
    relay_paths, proxy_paths, mock_paths = _paths(tmp_path)
    canary = uuid.uuid4().hex + uuid.uuid4().hex

    evidence = validate_secret_boundary(
        relay_paths=relay_paths,
        proxy_paths=proxy_paths,
        mock_paths=mock_paths,
        commands=[],
        inspect_payloads=[{"Config": {"Env": [f"VALUE={canary}"]}}],
        serialized_artifacts=[b"clean", canary.encode()],
        canary=canary,
    )

    assert evidence.boundary_valid is False
    assert evidence.secret_value_hit_count == 2
    assert "secret_value_exposed" in evidence.errors


def test_secret_boundary_rejects_nested_secret_digest_metadata(
    tmp_path: Path,
) -> None:
    relay_paths, proxy_paths, mock_paths = _paths(tmp_path)
    canary = uuid.uuid4().hex + uuid.uuid4().hex
    proxy_paths.auth.chmod(0o600)
    proxy_paths.auth.write_text(canary, encoding="utf-8")
    proxy_paths.auth.chmod(0o400)

    evidence = validate_secret_boundary(
        relay_paths=relay_paths,
        proxy_paths=proxy_paths,
        mock_paths=mock_paths,
        commands=[],
        inspect_payloads=[
            {"Config": {"Labels": {"provider_secret_sha256": "0" * 64}}}
        ],
        serialized_artifacts=[],
        canary=canary,
    )

    assert evidence.secret_digest_recorded is True
    assert "secret_digest_recorded" in evidence.errors
    assert evidence.boundary_valid is False


def _load_module(path: Path, prefix: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{prefix}_{uuid.uuid4().hex}",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_mounts(command: list[str]) -> dict[str, tuple[Path, bool]]:
    mounts: dict[str, tuple[Path, bool]] = {}
    for index, value in enumerate(command):
        if value != "--mount":
            continue
        parts = command[index + 1].split(",")
        fields = {
            part.partition("=")[0]: part.partition("=")[2]
            for part in parts
            if "=" in part
        }
        mounts[fields["dst"]] = (
            Path(fields["src"]),
            "readonly" in parts,
        )
    return mounts


class FakeProviderRelayDocker:
    def __init__(
        self,
        *,
        fail_when: str | None = None,
        fail_cleanup: bool = False,
        log_payload: str = "",
    ) -> None:
        self.fail_when = fail_when
        self.fail_cleanup = fail_cleanup
        self.log_payload = log_payload
        self.failed = False
        self.commands: list[list[str]] = []
        self.networks: dict[str, dict] = {}
        self.containers: dict[str, dict] = {}
        self.provider_attempt_count = 0
        self.mock_provider = _load_module(
            MOCK_PROVIDER_PATH,
            "fake_runtime_mock_provider",
        )

    @property
    def residual_container_count(self) -> int:
        return len(self.containers)

    @property
    def residual_network_count(self) -> int:
        return len(self.networks)

    def _completed(
        self,
        command: list[str],
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    def _inspect(self, name: str) -> list[dict]:
        container = self.containers[name]
        mounts = [
            {
                "Type": "bind",
                "Source": str(source),
                "Destination": destination,
                "RW": not readonly,
            }
            for destination, (source, readonly) in container["mounts"].items()
        ]
        return [
            {
                "Config": {
                    "User": "65532:65532",
                    "Image": container["image"],
                    "Env": ["PYTHONDONTWRITEBYTECODE=1", "PYTHONHASHSEED=0"],
                    "Labels": {},
                },
                "HostConfig": {
                    "NetworkMode": container["primary_network"],
                    "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "PidsLimit": 16,
                    "Memory": 134_217_728,
                    "NanoCpus": 500_000_000,
                    "IpcMode": "none",
                    "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
                    "Privileged": False,
                    "PortBindings": {},
                    "Devices": [],
                },
                "Mounts": mounts,
                "NetworkSettings": {
                    "Networks": {
                        network: {
                            "Aliases": sorted(container["aliases"].get(network, set()))
                        }
                        for network in container["networks"]
                    },
                    "Ports": {},
                },
                "State": {
                    "Status": container["state"],
                    "ExitCode": container["exit_code"],
                },
            }
        ]

    def _network_inspect(self, name: str) -> list[dict]:
        members = [
            container_name
            for container_name, container in self.containers.items()
            if name in container["networks"] and container["state"] == "running"
        ]
        return _network_payload(name, members)

    def _write_ready(self, container: dict) -> None:
        receipt_path = container["mounts"]["/output/receipt.json"][0]
        receipt_path.write_bytes(
            canonical_json_bytes(
                {
                    "receipt_schema_version": 1,
                    "status": "READY",
                    "auth_present": True,
                }
            )
        )

    def _current_mock(self) -> dict:
        return next(
            container
            for container in self.containers.values()
            if container["image"] == MOCK_IMAGE
        )

    def _current_proxy(self) -> dict:
        return next(
            container
            for container in self.containers.values()
            if container["image"] == PROXY_IMAGE
        )

    def _write_relay_result(self, relay: dict) -> int:
        mounts = relay["mounts"]
        response_path = mounts["/output/response.json"][0]
        receipt_path = mounts["/output/receipt.json"][0]
        request = json.loads(mounts["/input/request.json"][0].read_bytes())
        projection = json.loads(mounts["/input/projection.json"][0].read_bytes())
        if relay["mode"] == "probe":
            self.provider_attempt_count += 1
            mock = self._current_mock()
            proxy = self._current_proxy()
            output = {
                "probe_schema_version": 1,
                "results": {
                    "proxy_exact": "ALLOWED",
                    "direct_provider": "NOT_REACHABLE",
                    "arbitrary_hostname": "NOT_REACHABLE",
                    "public_test_ip": "NOT_REACHABLE",
                    "host_gateway": "NOT_REACHABLE",
                    "docker_control_file": "BLOCKED",
                    "docker_control_tcp": "NOT_REACHABLE",
                    "proxy_wrong_path": "BLOCKED",
                    "proxy_wrong_method": "BLOCKED",
                },
            }
            raw = canonical_json_bytes(output)
            response_path.write_bytes(raw)
            receipt_path.write_bytes(
                canonical_json_bytes(
                    {
                        "receipt_schema_version": 1,
                        "status": "SUCCEEDED",
                        "error_category": None,
                        "provider_http_status": 200,
                        "provider_attempt_count": 1,
                        "retry_count": 0,
                        "response_size": len(raw),
                        "response_sha256": hashlib.sha256(raw).hexdigest(),
                        "started_at": "2026-08-01T08:00:00Z",
                        "completed_at": "2026-08-01T08:00:01Z",
                    }
                )
            )
            proxy["mounts"]["/output/receipt.json"][0].write_bytes(
                canonical_json_bytes(
                    {
                        "receipt_schema_version": 1,
                        "method_allowed": False,
                        "path_allowed": True,
                        "upstream_attempt_count": 0,
                        "response_category": "METHOD_BLOCKED",
                        "response_size": 0,
                        "auth_present": False,
                    }
                )
            )
            mock["mounts"]["/output/receipt.json"][0].write_bytes(
                canonical_json_bytes(
                    {
                        "receipt_schema_version": 1,
                        "request_count": 1,
                        "method_allowed": True,
                        "path_allowed": True,
                        "auth_valid": True,
                        "envelope_valid": True,
                        "scenario": "valid_typed_candidate",
                        "response_category": "SCENARIO_RESPONSE",
                        "http_status": 200,
                        "response_size": 1024,
                    }
                )
            )
            return 0

        mock = self._current_mock()
        proxy = self._current_proxy()
        scenario = json.loads(
            mock["mounts"]["/input/scenario.json"][0].read_bytes()
        )["scenario"]
        envelope = {
            "request": request,
            "projection": projection,
            "generation": {
                "temperature": 0,
                "response_format": "typed_claims_json",
                "tool_use": False,
                "web_browsing": False,
                "file_tools": False,
                "function_calling": False,
                "streaming": False,
                "retry_count": 0,
                "maximum_output_bytes": 1_048_576,
                "timeout_seconds": 2,
            },
        }
        result = self.mock_provider.build_scenario_response(
            envelope,
            scenario,
            lambda value: json.loads(FakeClaimsWorker().run(value)),
        )
        self.provider_attempt_count += 1
        status = result["status"]
        body = result["body"]
        error_category: str | None = None
        if result["delay_seconds"] > 2:
            error_category = "TIMEOUT"
        elif 300 <= status < 400:
            error_category = "REDIRECT_REJECTED"
        elif not 200 <= status < 300:
            error_category = "HTTP_REJECTED"
        elif not body:
            error_category = "EMPTY_RESPONSE"
        elif len(body) > 1_048_576:
            error_category = "RESPONSE_TOO_LARGE"
        else:
            try:
                parsed = parse_single_json_object(body, maximum_bytes=1_048_576)
                validate_relay_response(
                    parsed,
                    build_relay_request(
                        projection,
                        request_id=request["request_id"],
                    ),
                )
            except Phase2ProviderRelayError as exc:
                error_category = (
                    "STRICT_JSON_REJECTED"
                    if exc.code == "strict_json_invalid"
                    else "BINDING_REJECTED"
                )
        exit_code = 0 if error_category is None else 2
        if exit_code == 0:
            response_path.write_bytes(body)
        else:
            response_path.write_bytes(b"\n")
        receipt = {
            "receipt_schema_version": 1,
            "status": "SUCCEEDED" if exit_code == 0 else "REJECTED",
            "error_category": error_category,
            "provider_http_status": None if error_category == "TIMEOUT" else status,
            "provider_attempt_count": 1,
            "retry_count": 0,
            "response_size": len(body) if body and len(body) <= 1_048_576 else None,
            "response_sha256": (
                hashlib.sha256(body).hexdigest()
                if body and len(body) <= 1_048_576
                else None
            ),
            "started_at": "2026-08-01T08:00:00Z",
            "completed_at": "2026-08-01T08:00:01Z",
        }
        receipt_path.write_bytes(canonical_json_bytes(receipt))
        proxy["mounts"]["/output/receipt.json"][0].write_bytes(
            canonical_json_bytes(
                {
                    "receipt_schema_version": 1,
                    "method_allowed": True,
                    "path_allowed": True,
                    "upstream_attempt_count": 1,
                    "response_category": "FORWARDED",
                    "response_size": len(body),
                    "auth_present": True,
                }
            )
        )
        mock["mounts"]["/output/receipt.json"][0].write_bytes(
            canonical_json_bytes(
                {
                    "receipt_schema_version": 1,
                    "request_count": 1,
                    "method_allowed": True,
                    "path_allowed": True,
                    "auth_valid": True,
                    "envelope_valid": True,
                    "scenario": scenario,
                    "response_category": "SCENARIO_RESPONSE",
                    "http_status": status,
                    "response_size": len(body),
                }
            )
        )
        return exit_code

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        joined = " ".join(command)
        if (
            self.fail_cleanup
            and not self.failed
            and (
                command[:3] == ["docker", "rm", "-f"]
                or command[:3] == ["docker", "network", "rm"]
            )
        ):
            self.failed = True
            return self._completed(command, 1, stderr="sanitized cleanup failure")
        if (
            self.fail_when
            and not self.failed
            and self.fail_when in joined
            and command[:2] != ["docker", "rm"]
            and command[:3] != ["docker", "network", "rm"]
        ):
            self.failed = True
            return self._completed(command, 1, stderr="sanitized failure")
        if command[:2] == ["docker", "version"]:
            return self._completed(
                command,
                stdout=json.dumps(
                    {"Version": "29.3.1", "Os": "linux", "Arch": "amd64"}
                ),
            )
        if command[:3] == ["docker", "image", "inspect"]:
            image = command[3]
            index = {RELAY_IMAGE: "1", PROXY_IMAGE: "2", MOCK_IMAGE: "3"}[image]
            entrypoint = {
                RELAY_IMAGE: ["/usr/bin/python3", "/relay/relay.py"],
                PROXY_IMAGE: ["/usr/bin/python3", "/proxy/proxy.py"],
                MOCK_IMAGE: ["/usr/bin/python3", "/app/mock_provider.py"],
            }[image]
            return self._completed(
                command,
                stdout=json.dumps(
                    [
                        {
                            "Id": f"sha256:{index * 64}",
                            "Config": {
                                "User": "65532:65532",
                                "Entrypoint": entrypoint,
                                "Env": [
                                    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                                    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
                                    "LANG=C.UTF-8",
                                    "PYTHONDONTWRITEBYTECODE=1",
                                    "PYTHONHASHSEED=0",
                                ],
                                "Labels": {
                                    "org.tickflow.phase2.base-image-digest": (
                                        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccd"
                                        "ac572511d5e09bda4435c89c5"
                                    )
                                },
                            },
                        }
                    ]
                ),
            )
        if command[:3] == ["docker", "network", "create"]:
            self.networks[command[-1]] = {}
            return self._completed(command, stdout="network-id\n")
        if command[:3] == ["docker", "network", "connect"]:
            container = self.containers[command[-1]]
            network = command[-2]
            container["networks"].add(network)
            container["aliases"][network] = {command[4]}
            return self._completed(command)
        if command[:3] == ["docker", "network", "inspect"]:
            if command[3] not in self.networks:
                return self._completed(command, 1, stderr="not found")
            return self._completed(
                command,
                stdout=json.dumps(self._network_inspect(command[3])),
            )
        if command[:3] == ["docker", "network", "rm"]:
            self.networks.pop(command[3], None)
            return self._completed(command)
        if command[:2] == ["docker", "logs"]:
            return self._completed(command, stdout=self.log_payload)
        if command[:2] == ["docker", "create"]:
            name = command[command.index("--name") + 1]
            network = command[command.index("--network") + 1]
            image = next(
                value
                for value in (RELAY_IMAGE, PROXY_IMAGE, MOCK_IMAGE)
                if value in command
            )
            self.containers[name] = {
                "image": image,
                "primary_network": network,
                "networks": {network},
                "aliases": {
                    network: (
                        {command[command.index("--network-alias") + 1]}
                        if "--network-alias" in command
                        else set()
                    )
                },
                "mounts": _parse_mounts(command),
                "state": "created",
                "exit_code": 0,
                "mode": command[-1] if image == RELAY_IMAGE else None,
            }
            return self._completed(command, stdout="container-id\n")
        if command[:2] == ["docker", "inspect"]:
            if command[2] not in self.containers:
                return self._completed(command, 1, stderr="not found")
            return self._completed(
                command,
                stdout=json.dumps(self._inspect(command[2])),
            )
        if command[:2] == ["docker", "start"]:
            name = command[-1]
            container = self.containers[name]
            if "--attach" in command:
                exit_code = self._write_relay_result(container)
                container["state"] = "exited"
                container["exit_code"] = exit_code
                return self._completed(command, exit_code)
            container["state"] = "running"
            self._write_ready(container)
            return self._completed(command)
        if command[:3] == ["docker", "rm", "-f"]:
            self.containers.pop(command[3], None)
            return self._completed(command)
        raise AssertionError(f"unexpected command: {command}")


def _tree_hash(path: Path) -> str:
    entries = []
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        entries.append(
            {
                "path": item.relative_to(path).as_posix(),
                "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
            }
        )
    return hashlib.sha256(canonical_json_bytes(entries)).hexdigest()


def _request_ids():
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"{counter:032x}"

    return next_id


def test_mock_orchestrator_runs_every_scenario_once_and_publishes_atomically(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    fake = FakeProviderRelayDocker()
    canary = uuid.uuid4().hex + uuid.uuid4().hex
    protected_before = _tree_hash(REPO_ROOT / "reports/phase2_claims")

    result = run_mock_provider_relay(
        REPO_ROOT,
        reports_root,
        _executor=fake,
        _allow_test_output_root=True,
        _secret_factory=lambda: canary,
        _request_id_factory=_request_ids(),
    )

    assert isinstance(result, ProviderRelayResult)
    assert result.status == PHASE2B_PROVIDER_RELAY_READY
    assert result.errors == []
    assert result.cleanup_complete is True
    assert fake.residual_container_count == 0
    assert fake.residual_network_count == 0
    assert fake.provider_attempt_count == 24
    assert sum(command[:3] == ["docker", "start", "--attach"] for command in fake.commands) == 24
    assert sum(command[:2] == ["docker", "logs"] for command in fake.commands) == 72
    assert sum(command[:2] == ["docker", "inspect"] for command in fake.commands) == 168
    assert sum(
        command[:3] == ["docker", "network", "inspect"]
        for command in fake.commands
    ) == 96
    assert all("--env" not in command and "--env-file" not in command for command in fake.commands)

    output = reports_root / "phase2_provider_relay"
    inbox = list((output / "mock_preview/inbox").glob("*.md"))
    rejected = list((output / "mock_preview/rejected").glob("*.json"))
    receipts = list((output / "mock_preview/receipts").glob("*.json"))
    assert len(inbox) == 1
    assert len(rejected) == 21
    assert len(receipts) == 24
    required_audit_fields = {
        "request_id",
        "symbol",
        "projection_sha256",
        "facts_sha256",
        "relay_image_digest",
        "proxy_image_digest",
        "mock_provider_image_digest",
        "relay_contract_hash",
        "proxy_policy_hash",
        "started_at",
        "completed_at",
        "provider_http_status",
        "provider_attempt_count",
        "retry_count",
        "response_size",
        "response_sha256",
        "candidate_sha256",
        "claims_validation_status",
        "renderer_status",
        "route",
        "cleanup_status",
    }
    for receipt_path in receipts:
        receipt = json.loads(receipt_path.read_bytes())
        assert required_audit_fields <= set(receipt)
        assert receipt["symbol"] == "000403.SZ"
        assert len(receipt["request_id"]) == 32
        assert receipt["retry_count"] == 0
        assert receipt["cleanup_status"] == "CLEAN"
        assert all(
            receipt[field].startswith("sha256:")
            for field in (
                "relay_image_digest",
                "proxy_image_digest",
                "mock_provider_image_digest",
            )
        )
    assert not (reports_root / "phase2_obsidian_preview").exists()
    assert protected_before == _tree_hash(REPO_ROOT / "reports/phase2_claims")
    evidence_bytes = (output / "runtime_evidence.json").read_bytes()
    evidence = json.loads(evidence_bytes)
    assert canary.encode() not in evidence_bytes
    assert evidence["status"] == PHASE2B_PROVIDER_RELAY_READY
    assert evidence["mock_matrix"]["valid_run_count"] == 2
    assert evidence["mock_matrix"]["invalid_rejected_count"] == 21
    assert evidence["mock_matrix"]["invalid_accepted_count"] == 0
    assert evidence["determinism"]["candidate_hash_match"] is True
    assert evidence["determinism"]["renderer_hash_match"] is True
    assert evidence["secret_boundary"]["secret_value_hit_count"] == 0
    assert evidence["cleanup"]["container_residue_count"] == 0
    assert evidence["cleanup"]["network_residue_count"] == 0
    assert evidence["external_actions"] == {
        "tickflow_api_request_count": 0,
        "real_ai_call_count": 0,
        "real_provider_attempt_count": 0,
        "real_public_network_success_count": 0,
        "cloud_mutation_count": 0,
        "obsidian_real_vault_write": False,
        "external_send_count": 0,
        "integrated_gold_enabled": False,
        "paper_trading_started": False,
    }
    validation = validate_provider_relay_artifacts(output)
    assert validation.status == PHASE2B_PROVIDER_RELAY_READY
    assert validation.errors == []


@pytest.mark.parametrize(
    "failure_boundary",
    [
        "docker network create",
        "docker create --name phase2-mock",
        "docker start phase2-proxy",
        "docker network connect",
        "docker start --attach phase2-relay",
        "docker inspect phase2-relay",
    ],
)
def test_mock_orchestrator_cleans_every_created_resource_on_failure(
    tmp_path: Path,
    failure_boundary: str,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    fake = FakeProviderRelayDocker(fail_when=failure_boundary)

    result = run_mock_provider_relay(
        REPO_ROOT,
        reports_root,
        _executor=fake,
        _allow_test_output_root=True,
        _secret_factory=lambda: uuid.uuid4().hex + uuid.uuid4().hex,
        _request_id_factory=_request_ids(),
    )

    assert result.status == PHASE2B_PROVIDER_RELAY_E2E_BLOCKED
    assert result.errors
    assert fake.residual_container_count == 0
    assert fake.residual_network_count == 0
    assert result.cleanup_complete is True


def test_mock_orchestrator_blocks_on_cleanup_failure(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    fake = FakeProviderRelayDocker(fail_cleanup=True)

    result = run_mock_provider_relay(
        REPO_ROOT,
        reports_root,
        _executor=fake,
        _allow_test_output_root=True,
        _secret_factory=lambda: uuid.uuid4().hex + uuid.uuid4().hex,
        _request_id_factory=_request_ids(),
    )

    assert result.status == PHASE2B_PROVIDER_RELAY_E2E_BLOCKED
    assert "runtime_cleanup_failed" in result.errors
    assert result.cleanup_complete is False
    assert fake.residual_container_count == 1
    assert not (reports_root / "phase2_provider_relay").exists()


def test_mock_orchestrator_blocks_sensitive_shape_in_container_logs(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    fake = FakeProviderRelayDocker(log_payload="Bearer redacted-shape")

    result = run_mock_provider_relay(
        REPO_ROOT,
        reports_root,
        _executor=fake,
        _allow_test_output_root=True,
        _secret_factory=lambda: uuid.uuid4().hex + uuid.uuid4().hex,
        _request_id_factory=_request_ids(),
    )

    assert result.status == provider_relay_runner.PHASE2B_PROVIDER_SECRET_BLOCKED
    assert "sensitive_shape_exposed" in result.errors
    assert fake.residual_container_count == 0
    assert fake.residual_network_count == 0


def test_atomic_publication_failure_preserves_previous_output(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    destination = reports_root / "phase2_provider_relay"
    destination.mkdir(parents=True)
    marker = destination / "previous.json"
    marker.write_text('{"previous":true}\n', encoding="utf-8")
    previous_hash = _tree_hash(destination)
    fake = FakeProviderRelayDocker()

    def fail_publication(_staging: Path, _destination: Path) -> None:
        raise OSError("injected atomic publication failure")

    result = run_mock_provider_relay(
        REPO_ROOT,
        reports_root,
        _executor=fake,
        _allow_test_output_root=True,
        _publisher=fail_publication,
        _secret_factory=lambda: uuid.uuid4().hex + uuid.uuid4().hex,
        _request_id_factory=_request_ids(),
    )

    assert result.status == PHASE2B_PROVIDER_RELAY_E2E_BLOCKED
    assert "atomic_publication_failed" in result.errors
    assert previous_hash == _tree_hash(destination)
    assert marker.is_file()
    assert fake.residual_container_count == 0
    assert fake.residual_network_count == 0


def test_offline_validator_rejects_missing_per_run_audit_field(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    result = run_mock_provider_relay(
        REPO_ROOT,
        reports_root,
        _executor=FakeProviderRelayDocker(),
        _allow_test_output_root=True,
        _secret_factory=lambda: uuid.uuid4().hex + uuid.uuid4().hex,
        _request_id_factory=_request_ids(),
    )
    assert result.status == PHASE2B_PROVIDER_RELAY_READY
    output = reports_root / "phase2_provider_relay"
    receipt_path = next((output / "mock_preview/receipts").glob("*.json"))
    receipt = json.loads(receipt_path.read_bytes())
    receipt.pop("request_id")
    receipt_path.write_bytes(canonical_json_bytes(receipt))

    validation = validate_provider_relay_artifacts(output)

    assert validation.status == PHASE2B_PROVIDER_RELAY_E2E_BLOCKED
    assert "receipt_audit_fields_invalid" in validation.errors


def test_provider_relay_cli_exposes_no_runtime_or_retry_overrides() -> None:
    run_args = parse_run_args(
        ["--repo-root", str(REPO_ROOT), "--reports-root", str(REPO_ROOT / "reports")]
    )
    validate_args = parse_validate_args(
        [
            "--artifact-root",
            str(REPO_ROOT / "reports/phase2_provider_relay"),
        ]
    )

    assert set(vars(run_args)) == {"repo_root", "reports_root"}
    assert set(vars(validate_args)) == {"artifact_root"}
    for forbidden in ("--retry", "--endpoint", "--image", "--network", "--secret"):
        with pytest.raises(SystemExit):
            parse_run_args([forbidden, "value"])
