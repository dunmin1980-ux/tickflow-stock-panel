from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.services.phase2_provider_relay_runner import (
    MockRuntimePaths,
    Phase2ProviderRelayRuntimeError,
    ProxyRuntimePaths,
    RelayRuntimePaths,
    build_mock_create_command,
    build_network_connect_command,
    build_network_create_command,
    build_proxy_create_command,
    build_relay_create_command,
    validate_container_inspect,
    validate_network_topology,
    validate_secret_boundary,
)

RELAY_IMAGE = "tickflow-phase2-provider-relay:runtime-v1"
PROXY_IMAGE = "tickflow-phase2-egress-proxy:runtime-v1"
MOCK_IMAGE = "tickflow-phase2-mock-provider:runtime-v1"


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
                "Networks": {network: {} for network in networks},
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
