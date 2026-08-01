from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.phase2_openai_canary_runner import (
    OPENAI_PROXY_IMAGE,
    OpenAICanaryRuntimeError,
    OpenAICanaryRuntimePaths,
    build_canary_egress_network_command,
    build_canary_relay_network_command,
    build_openai_proxy_create_command,
    build_openai_proxy_egress_attach_command,
    validate_openai_proxy_inspect,
)

PROXY_NAME = "phase2-openai-proxy-test"
RELAY_NETWORK = "relay-net"
EGRESS_NETWORK = "phase2-canary-egress-test"
EXPECTED_ENVIRONMENT = [
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
    "LANG=C.UTF-8",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
]


def _file(path: Path, content: str = "{}\n", *, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


@pytest.fixture
def runtime_paths(tmp_path: Path) -> OpenAICanaryRuntimePaths:
    return OpenAICanaryRuntimePaths(
        auth=_file(tmp_path / "provider-auth", "synthetic-value"),
        receipt=_file(tmp_path / "proxy-receipt.json"),
    )


def _mount_arguments(command: list[str]) -> list[str]:
    return [
        command[index + 1]
        for index, item in enumerate(command)
        if item == "--mount"
    ]


def test_openai_proxy_command_has_only_two_single_file_mounts(
    runtime_paths: OpenAICanaryRuntimePaths,
) -> None:
    command = build_openai_proxy_create_command(
        PROXY_NAME,
        RELAY_NETWORK,
        runtime_paths,
    )

    mounts = _mount_arguments(command)
    assert len(mounts) == 2
    assert mounts[0].endswith("dst=/run/phase2/provider-auth,readonly")
    assert mounts[1].endswith("dst=/output/receipt.json")
    assert command[-1] == OPENAI_PROXY_IMAGE
    assert command.count("--network") == 1
    assert command[command.index("--network") + 1] == RELAY_NETWORK
    assert command[command.index("--network-alias") + 1] == "phase2-egress-proxy"


def test_openai_proxy_command_is_hardened_and_contains_no_override_surface(
    runtime_paths: OpenAICanaryRuntimePaths,
) -> None:
    command = build_openai_proxy_create_command(
        PROXY_NAME,
        RELAY_NETWORK,
        runtime_paths,
    )
    joined = " ".join(command)

    for expected in (
        "--read-only",
        "--user 65532:65532",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit 16",
        "--memory 128m",
        "--cpus 0.5",
        "--ipc none",
        "--restart no",
    ):
        assert expected in joined
    for forbidden in (
        "--privileged",
        "--publish",
        "--device",
        "--env",
        "--env-file",
        "--label",
        "docker.sock",
        "/Users/",
        "/home/",
        "/workspace",
        "/vault",
        "Authorization",
        "synthetic-value",
    ):
        assert forbidden not in joined
    assert command[-1] == OPENAI_PROXY_IMAGE


def test_network_commands_are_exact_and_only_relay_is_internal() -> None:
    assert build_canary_relay_network_command("phase2-canary-relay-test") == [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        "phase2-canary-relay-test",
    ]
    assert build_canary_egress_network_command(EGRESS_NETWORK) == [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        EGRESS_NETWORK,
    ]
    assert "--internal" not in build_canary_egress_network_command(EGRESS_NETWORK)


def test_egress_attach_command_is_exact_and_has_no_alias_or_override() -> None:
    assert build_openai_proxy_egress_attach_command(
        EGRESS_NETWORK,
        PROXY_NAME,
    ) == [
        "docker",
        "network",
        "connect",
        EGRESS_NETWORK,
        PROXY_NAME,
    ]


@pytest.mark.parametrize(
    ("network", "container"),
    [
        ("phase2-canary-relay-test", PROXY_NAME),
        (EGRESS_NETWORK, "phase2-relay-test"),
        (EGRESS_NETWORK, "arbitrary-container"),
        ("192.0.2.1", PROXY_NAME),
        ("--alias", PROXY_NAME),
        (EGRESS_NETWORK, "--driver-opt"),
    ],
)
def test_egress_attach_rejects_every_non_proxy_or_non_egress_target(
    network: str,
    container: str,
) -> None:
    with pytest.raises(OpenAICanaryRuntimeError):
        build_openai_proxy_egress_attach_command(network, container)


@pytest.mark.parametrize(
    "invalid_name",
    ["", "UPPER", "contains space", "../escape", "a" * 64],
)
def test_create_and_network_builders_reject_invalid_names(
    invalid_name: str,
    runtime_paths: OpenAICanaryRuntimePaths,
) -> None:
    with pytest.raises(OpenAICanaryRuntimeError):
        build_openai_proxy_create_command(
            invalid_name,
            RELAY_NETWORK,
            runtime_paths,
        )
    with pytest.raises(OpenAICanaryRuntimeError):
        build_canary_relay_network_command(invalid_name)


def test_runtime_paths_reject_symlinks_non_regular_files_and_auth_mode(
    tmp_path: Path,
) -> None:
    regular = _file(tmp_path / "regular")
    symlink = tmp_path / "linked"
    symlink.symlink_to(regular)

    with pytest.raises(OpenAICanaryRuntimeError):
        OpenAICanaryRuntimePaths(auth=symlink, receipt=regular)
    with pytest.raises(OpenAICanaryRuntimeError):
        OpenAICanaryRuntimePaths(auth=tmp_path, receipt=regular)
    wrong_mode = _file(tmp_path / "wrong-mode", "synthetic", mode=0o644)
    with pytest.raises(OpenAICanaryRuntimeError):
        OpenAICanaryRuntimePaths(auth=wrong_mode, receipt=regular)


def _inspect_payload(
    paths: OpenAICanaryRuntimePaths,
    *,
    state: str = "created",
) -> list[dict]:
    return [
        {
            "Path": "/usr/bin/python3",
            "Args": ["/proxy/proxy.py"],
            "Config": {
                "User": "65532:65532",
                "Image": OPENAI_PROXY_IMAGE,
                "Entrypoint": ["/usr/bin/python3", "/proxy/proxy.py"],
                "Cmd": None,
                "Env": list(EXPECTED_ENVIRONMENT),
                "Labels": {},
            },
            "HostConfig": {
                "NetworkMode": RELAY_NETWORK,
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
                    "Source": str(paths.auth),
                    "Destination": "/run/phase2/provider-auth",
                    "RW": False,
                },
                {
                    "Type": "bind",
                    "Source": str(paths.receipt),
                    "Destination": "/output/receipt.json",
                    "RW": True,
                },
            ],
            "NetworkSettings": {
                "Networks": {
                    RELAY_NETWORK: {"Aliases": [PROXY_NAME, "phase2-egress-proxy"]},
                    EGRESS_NETWORK: {"Aliases": [PROXY_NAME]},
                },
                "Ports": {},
            },
            "State": {"Status": state, "ExitCode": 0},
        }
    ]


@pytest.mark.parametrize("state", ["created", "running", "exited"])
def test_openai_proxy_inspect_accepts_exact_contract(
    state: str,
    runtime_paths: OpenAICanaryRuntimePaths,
) -> None:
    evidence = validate_openai_proxy_inspect(
        _inspect_payload(runtime_paths, state=state),
        paths=runtime_paths,
        relay_network=RELAY_NETWORK,
        egress_network=EGRESS_NETWORK,
        expected_state=state,
    )

    assert evidence.contract_valid is True
    assert evidence.errors == []
    assert evidence.component == "proxy"
    assert evidence.mount_count == 2
    assert evidence.network_count == 2
    assert evidence.published_port_count == 0
    assert evidence.device_count == 0


@pytest.mark.parametrize(
    ("case", "error_code"),
    [
        ("root_user", "runtime_user_invalid"),
        ("wrong_image", "runtime_image_invalid"),
        ("wrong_entrypoint", "runtime_entrypoint_invalid"),
        ("command_override", "runtime_entrypoint_invalid"),
        ("sensitive_env", "runtime_environment_invalid"),
        ("extra_env", "runtime_environment_invalid"),
        ("writable_root", "runtime_rootfs_not_readonly"),
        ("capabilities", "runtime_cap_drop_invalid"),
        ("privileged", "runtime_privileged"),
        ("security_opt", "runtime_security_opt_invalid"),
        ("pids", "runtime_pids_limit_invalid"),
        ("memory", "runtime_memory_limit_invalid"),
        ("cpu", "runtime_cpu_limit_invalid"),
        ("ipc", "runtime_ipc_mode_invalid"),
        ("restart", "runtime_restart_policy_invalid"),
        ("published_port", "runtime_ports_published"),
        ("device", "runtime_devices_present"),
        ("extra_mount", "runtime_mount_set_invalid"),
        ("auth_writable", "runtime_mount_mode_invalid"),
        ("wrong_mount_source", "runtime_mount_source_invalid"),
        ("missing_egress", "runtime_network_membership_invalid"),
        ("extra_network", "runtime_network_membership_invalid"),
        ("wrong_primary", "runtime_network_membership_invalid"),
        ("missing_alias", "runtime_network_alias_invalid"),
        ("wrong_state", "runtime_state_invalid"),
    ],
)
def test_openai_proxy_inspect_rejects_every_single_field_mutation(
    case: str,
    error_code: str,
    runtime_paths: OpenAICanaryRuntimePaths,
) -> None:
    payload = _inspect_payload(runtime_paths)
    item = payload[0]
    if case == "root_user":
        item["Config"]["User"] = "0:0"
    elif case == "wrong_image":
        item["Config"]["Image"] = "unapproved:latest"
    elif case == "wrong_entrypoint":
        item["Config"]["Entrypoint"] = ["/bin/sh"]
    elif case == "command_override":
        item["Config"]["Cmd"] = ["--override"]
    elif case == "sensitive_env":
        item["Config"]["Env"].append("OPENAI_API_KEY=synthetic")
    elif case == "extra_env":
        item["Config"]["Env"].append("EXTRA=value")
    elif case == "writable_root":
        item["HostConfig"]["ReadonlyRootfs"] = False
    elif case == "capabilities":
        item["HostConfig"]["CapDrop"] = []
    elif case == "privileged":
        item["HostConfig"]["Privileged"] = True
    elif case == "security_opt":
        item["HostConfig"]["SecurityOpt"] = []
    elif case == "pids":
        item["HostConfig"]["PidsLimit"] = 0
    elif case == "memory":
        item["HostConfig"]["Memory"] = 0
    elif case == "cpu":
        item["HostConfig"]["NanoCpus"] = 0
    elif case == "ipc":
        item["HostConfig"]["IpcMode"] = "private"
    elif case == "restart":
        item["HostConfig"]["RestartPolicy"] = {"Name": "always"}
    elif case == "published_port":
        item["HostConfig"]["PortBindings"] = {"8080/tcp": [{}]}
    elif case == "device":
        item["HostConfig"]["Devices"] = [{"PathOnHost": "/dev/null"}]
    elif case == "extra_mount":
        item["Mounts"].append(
            {
                "Type": "bind",
                "Source": "/forbidden",
                "Destination": "/workspace",
                "RW": False,
            }
        )
    elif case == "auth_writable":
        item["Mounts"][0]["RW"] = True
    elif case == "wrong_mount_source":
        item["Mounts"][0]["Source"] = "/forbidden"
    elif case == "missing_egress":
        item["NetworkSettings"]["Networks"].pop(EGRESS_NETWORK)
    elif case == "extra_network":
        item["NetworkSettings"]["Networks"]["bridge"] = {"Aliases": []}
    elif case == "wrong_primary":
        item["HostConfig"]["NetworkMode"] = EGRESS_NETWORK
    elif case == "missing_alias":
        item["NetworkSettings"]["Networks"][RELAY_NETWORK]["Aliases"] = [
            PROXY_NAME
        ]
    elif case == "wrong_state":
        item["State"]["Status"] = "running"
    else:  # pragma: no cover - parameter list is closed above
        raise AssertionError(case)

    evidence = validate_openai_proxy_inspect(
        payload,
        paths=runtime_paths,
        relay_network=RELAY_NETWORK,
        egress_network=EGRESS_NETWORK,
        expected_state="created",
    )

    assert evidence.contract_valid is False
    assert error_code in evidence.errors
    serialized = json.dumps(evidence.to_dict(), sort_keys=True)
    assert str(runtime_paths.auth) not in serialized
    assert str(runtime_paths.receipt) not in serialized
    assert RELAY_NETWORK not in serialized
    assert EGRESS_NETWORK not in serialized


def test_inspect_rejects_non_list_or_multiple_items(
    runtime_paths: OpenAICanaryRuntimePaths,
) -> None:
    for payload in ({}, [], [{}, {}]):
        evidence = validate_openai_proxy_inspect(
            payload,
            paths=runtime_paths,
            relay_network=RELAY_NETWORK,
            egress_network=EGRESS_NETWORK,
            expected_state="created",
        )
        assert evidence.contract_valid is False
        assert "runtime_inspect_invalid" in evidence.errors


def test_launcher_module_exposes_no_execution_or_bypass_cli() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "app/services/phase2_openai_canary_runner.py"
    )
    source = module_path.read_text(encoding="utf-8")

    for forbidden in (
        "subprocess",
        "os.system",
        "--force",
        "--skip-gates",
        "--retry",
        "def main(",
    ):
        assert forbidden not in source
