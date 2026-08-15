#!/usr/bin/env python3
"""Run the Production Proxy TLS Probe against internal double-network mocks."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "reports/phase2_provider_ark/proxy_tls_probe/mock_e2e.json"
CASE_ROOT = OUTPUT_PATH.parent / ".mock-cases.staging"
PROVENANCE_PATH = (
    REPO_ROOT / "reports/phase2_provider_ark/proxy_tls_probe/build_provenance.json"
)
SERVER_PATH = REPO_ROOT / "docker/phase2-ark-proxy-tls-mock/server.py"
TARGET_HOST = "ark.cn-beijing.volces.com"
EXPECTED_CASES = {
    "happy_path_1": "PROBE_PASSED",
    "happy_path_2": "PROBE_PASSED",
    "happy_path_3": "PROBE_PASSED",
    "relay_internal_plus_egress": "PROBE_PASSED",
    "unknown_ca": "TLS_CERT_VERIFY_FAILED",
    "hostname_mismatch": "TLS_HOSTNAME_VERIFY_FAILED",
    "connection_reset": "TLS_CONNECTION_RESET",
    "handshake_timeout": "TLS_HANDSHAKE_TIMEOUT",
    "protocol_incompatibility": "TLS_PROTOCOL_FAILED",
    "connection_refused": "PROVIDER_CONNECT_FAILED",
    "egress_network_missing": "TOPOLOGY_BLOCKED",
    "wrong_network_attachment": "TOPOLOGY_BLOCKED",
}


def runtime_names(suffix: str) -> dict[str, str]:
    if not suffix:
        raise ValueError("runtime_suffix_invalid")
    return {
        "egress_network": f"phase2-proxy-tls-mock-egress-{suffix}",
        "mock_server": f"phase2-proxy-tls-mock-server-{suffix}",
        "proxy": f"phase2-proxy-tls-mock-client-{suffix}",
        "relay_network": f"phase2-proxy-tls-mock-relay-{suffix}",
        "wrong_network": f"phase2-proxy-tls-mock-wrong-{suffix}",
    }


def mock_network_create_commands(names: Mapping[str, str]) -> list[list[str]]:
    return [
        [
            "docker",
            "network",
            "create",
            "--driver",
            "bridge",
            "--internal",
            names["relay_network"],
        ],
        [
            "docker",
            "network",
            "create",
            "--driver",
            "bridge",
            "--internal",
            names["egress_network"],
        ],
    ]


def validate_mock_topology(
    *,
    names: Mapping[str, str],
    network_inspect: Mapping[str, Mapping[str, Any]],
    container_inspect: Any,
) -> None:
    expected_networks = {names["relay_network"], names["egress_network"]}
    if set(network_inspect) != expected_networks or any(
        value.get("Driver") != "bridge" or value.get("Internal") is not True
        for value in network_inspect.values()
    ):
        raise ValueError("mock_topology_invalid")
    try:
        attachments = container_inspect[0]["NetworkSettings"]["Networks"]
    except (IndexError, KeyError, TypeError) as error:
        raise ValueError("mock_topology_invalid") from error
    if not isinstance(attachments, dict) or set(attachments) != expected_networks:
        raise ValueError("mock_topology_invalid")


def topology_status(
    *,
    names: Mapping[str, str],
    network_inspect: Mapping[str, Mapping[str, Any]],
    container_inspect: Any,
) -> str:
    try:
        validate_mock_topology(
            names=names,
            network_inspect=network_inspect,
            container_inspect=container_inspect,
        )
    except ValueError:
        return "TOPOLOGY_BLOCKED"
    return "VERIFIED"


def _run(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=check,
        text=True,
        timeout=timeout,
    )


def _mount(source: Path, destination: str, *, readonly: bool = True) -> str:
    options = ["type=bind", f"src={source}", f"dst={destination}"]
    if readonly:
        options.append("readonly")
    return ",".join(options)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".staging", dir=path.parent
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _openssl(*arguments: str) -> None:
    executable = shutil.which("openssl")
    if executable is None:
        raise RuntimeError("openssl_missing")
    _run([executable, *arguments], timeout=30.0)


def _create_ca(root: Path, name: str) -> tuple[Path, Path]:
    key = root / f"{name}.key"
    certificate = root / f"{name}.crt"
    _openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(certificate),
        "-days",
        "2",
        "-sha256",
        "-subj",
        f"/CN={name}",
    )
    key.chmod(0o644)
    certificate.chmod(0o644)
    return key, certificate


def _create_server_certificate(
    root: Path,
    name: str,
    hostname: str,
    ca_key: Path,
    ca_certificate: Path,
) -> tuple[Path, Path]:
    key = root / f"{name}.key"
    request = root / f"{name}.csr"
    certificate = root / f"{name}.crt"
    extension = root / f"{name}.ext"
    extension.write_text(
        f"subjectAltName=DNS:{hostname}\nextendedKeyUsage=serverAuth\n",
        encoding="ascii",
    )
    _openssl(
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(request),
        "-sha256",
        "-subj",
        f"/CN={hostname}",
    )
    _openssl(
        "x509",
        "-req",
        "-in",
        str(request),
        "-CA",
        str(ca_certificate),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(certificate),
        "-days",
        "2",
        "-sha256",
        "-extfile",
        str(extension),
    )
    key.chmod(0o644)
    certificate.chmod(0o644)
    return key, certificate


def _load_image_id() -> str:
    value = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    image_id = value.get("image_identity", {}).get("image_id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise RuntimeError("build_provenance_invalid")
    return image_id


def _wait_ready(container: str) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        result = _run(["docker", "logs", container], check=False, timeout=5.0)
        if "READY" in result.stdout:
            return
        time.sleep(0.05)
    raise RuntimeError("mock_server_not_ready")


def _create_networks(names: Mapping[str, str]) -> None:
    for command in mock_network_create_commands(names):
        _run(command)


def _start_server(
    *,
    names: Mapping[str, str],
    image_id: str,
    mode: str,
    key: Path | None,
    certificate: Path | None,
) -> None:
    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        names["mock_server"],
        "--network",
        names["egress_network"],
        "--network-alias",
        TARGET_HOST,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "NET_BIND_SERVICE",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "0:0",
        "--entrypoint",
        "/usr/bin/python3",
        "--mount",
        _mount(SERVER_PATH, "/mock/server.py"),
    ]
    if key is not None and certificate is not None:
        command.extend(
            [
                "--mount",
                _mount(key, "/mock/server.key"),
                "--mount",
                _mount(certificate, "/mock/server.crt"),
            ]
        )
    command.extend([image_id, "/mock/server.py", "--mode", mode])
    if mode == "timeout":
        command.extend(["--hold", "12"])
    elif mode == "refused":
        command.extend(["--hold", "5"])
    if key is not None and certificate is not None:
        command.extend(["--key", "/mock/server.key", "--cert", "/mock/server.crt"])
    _run(command)
    _wait_ready(names["mock_server"])


def _create_proxy(
    *,
    names: Mapping[str, str],
    image_id: str,
    output_dir: Path,
    ca_bundle: Path,
    probe_id: str,
) -> None:
    output_dir.mkdir(mode=0o733)
    command = [
        "docker",
        "container",
        "create",
        "--name",
        names["proxy"],
        "--network",
        names["relay_network"],
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65532:65532",
        "--entrypoint",
        "/usr/bin/python3",
        "--mount",
        _mount(ca_bundle, "/etc/ssl/certs/ca-certificates.crt"),
        "--mount",
        _mount(output_dir, "/output", readonly=False),
        image_id,
        "/probe/probe.py",
        "--probe-id",
        probe_id,
    ]
    _run(command)


def _network_metadata(names: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for role in ("relay_network", "egress_network"):
        result = _run(["docker", "network", "inspect", names[role]])
        values[names[role]] = json.loads(result.stdout)[0]
    return values


def _connect_and_validate(names: Mapping[str, str]) -> None:
    _run(
        [
            "docker",
            "network",
            "connect",
            names["egress_network"],
            names["proxy"],
        ]
    )
    inspected = json.loads(
        _run(["docker", "container", "inspect", names["proxy"]]).stdout
    )
    validate_mock_topology(
        names=names,
        network_inspect=_network_metadata(names),
        container_inspect=inspected,
    )


def _cleanup(names: Mapping[str, str]) -> None:
    for role in ("proxy", "mock_server"):
        _run(["docker", "rm", "--force", names[role]], check=False)
    for role in ("wrong_network", "egress_network", "relay_network"):
        _run(["docker", "network", "rm", names[role]], check=False)


def _case(
    *,
    case_name: str,
    image_id: str,
    root: Path,
    ca_bundle: Path,
    mode: str | None,
    key: Path | None,
    certificate: Path | None,
    topology: str = "valid",
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    names = runtime_names(suffix)
    _create_networks(names)
    probe_id = uuid.uuid4().hex
    output_dir = root / f"output-{case_name}-{suffix}"
    try:
        if mode is not None:
            _start_server(
                names=names,
                image_id=image_id,
                mode=mode,
                key=key,
                certificate=certificate,
            )
        _create_proxy(
            names=names,
            image_id=image_id,
            output_dir=output_dir,
            ca_bundle=ca_bundle,
            probe_id=probe_id,
        )
        if topology == "missing":
            inspected = json.loads(
                _run(["docker", "container", "inspect", names["proxy"]]).stdout
            )
            status = topology_status(
                names=names,
                network_inspect=_network_metadata(names),
                container_inspect=inspected,
            )
            return {"case": case_name, "terminal_status": status}
        if topology == "wrong":
            _run(
                [
                    "docker",
                    "network",
                    "create",
                    "--driver",
                    "bridge",
                    "--internal",
                    names["wrong_network"],
                ]
            )
            _run(
                [
                    "docker",
                    "network",
                    "connect",
                    names["wrong_network"],
                    names["proxy"],
                ]
            )
            inspected = json.loads(
                _run(["docker", "container", "inspect", names["proxy"]]).stdout
            )
            status = topology_status(
                names=names,
                network_inspect=_network_metadata(names),
                container_inspect=inspected,
            )
            return {"case": case_name, "terminal_status": status}
        _connect_and_validate(names)
        completed = _run(
            ["docker", "start", "--attach", names["proxy"]],
            check=False,
            timeout=15.0,
        )
        receipt_path = output_dir / "receipt.json"
        if not receipt_path.is_file():
            raise RuntimeError("probe_receipt_missing")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["case"] = case_name
        receipt["container_exit_code"] = completed.returncode
        receipt["double_network_topology"] = "VERIFIED"
        return receipt
    finally:
        _cleanup(names)


def _residue_counts() -> dict[str, int]:
    containers = _run(
        ["docker", "container", "ls", "--all", "--format", "{{.Names}}"]
    ).stdout.splitlines()
    networks = _run(
        ["docker", "network", "ls", "--format", "{{.Name}}"]
    ).stdout.splitlines()
    return {
        "container_count": sum(
            name.startswith("phase2-proxy-tls-mock-") for name in containers
        ),
        "network_count": sum(
            name.startswith("phase2-proxy-tls-mock-") for name in networks
        ),
    }


def validate_results(results: Mapping[str, Mapping[str, Any]]) -> None:
    if set(results) != set(EXPECTED_CASES):
        raise ValueError("mock_result_invalid")
    for case, expected in EXPECTED_CASES.items():
        validate_case_result(case, results[case], expected=expected)


def validate_case_result(
    case: str,
    result: Mapping[str, Any],
    *,
    expected: str,
) -> None:
    if result.get("case") != case or result.get("terminal_status") != expected:
        raise ValueError("mock_result_invalid")
    if expected != "TOPOLOGY_BLOCKED" and (
        result.get("provider_attempt_count") != 0
        or result.get("ai_call_count") != 0
        or result.get("secret_content_read") is not False
        or result.get("authorization_constructed") is not False
        or result.get("http_request_sent") is not False
        or result.get("real_public_network_success_count") != 0
        or result.get("double_network_topology") != "VERIFIED"
    ):
        raise ValueError("mock_result_invalid")


def build_mock_report(
    *,
    results: Mapping[str, Mapping[str, Any]],
    image_id: str,
    residue: Mapping[str, int],
) -> dict[str, Any]:
    validate_results(results)
    if residue != {"container_count": 0, "network_count": 0}:
        raise RuntimeError("mock_runtime_residue")
    return {
        "ai_call_count": 0,
        "authorization_constructed": False,
        "double_network_topology": "VERIFIED",
        "happy_path_runs": 3,
        "happy_path_status": "PASSED",
        "http_request_sent": False,
        "mock_e2e_schema_version": 1,
        "mock_networks_internal": True,
        "production_topology_mock": "PASSED",
        "provider_attempt_count": 0,
        "proxy_image_id": image_id,
        "real_public_network_success_count": 0,
        "residue": dict(residue),
        "results": dict(results),
        "secret_content_read": False,
        "status": "PRODUCTION_PROXY_TLS_PROBE_MOCK_PASSED",
    }


def _run_named_case(case_name: str, *, image_id: str, root: Path) -> dict[str, Any]:
    trusted_ca_key, trusted_ca = _create_ca(root, "trusted-root")
    unknown_ca_key, unknown_ca = _create_ca(root, "unknown-root")
    trusted_key, trusted_certificate = _create_server_certificate(
        root,
        "trusted",
        TARGET_HOST,
        trusted_ca_key,
        trusted_ca,
    )
    unknown_key, unknown_certificate = _create_server_certificate(
        root,
        "unknown",
        TARGET_HOST,
        unknown_ca_key,
        unknown_ca,
    )
    mismatch_key, mismatch_certificate = _create_server_certificate(
        root,
        "mismatch",
        "wrong.internal.test",
        trusted_ca_key,
        trusted_ca,
    )
    common = {
        "case_name": case_name,
        "image_id": image_id,
        "root": root,
        "ca_bundle": trusted_ca,
    }
    if case_name in {
        "happy_path_1",
        "happy_path_2",
        "happy_path_3",
        "relay_internal_plus_egress",
    }:
        return _case(
            **common,
            mode="tls",
            key=trusted_key,
            certificate=trusted_certificate,
        )
    if case_name == "unknown_ca":
        return _case(
            **common,
            mode="tls",
            key=unknown_key,
            certificate=unknown_certificate,
        )
    if case_name == "hostname_mismatch":
        return _case(
            **common,
            mode="tls",
            key=mismatch_key,
            certificate=mismatch_certificate,
        )
    if case_name == "connection_reset":
        return _case(**common, mode="reset", key=None, certificate=None)
    if case_name == "handshake_timeout":
        return _case(**common, mode="timeout", key=None, certificate=None)
    if case_name == "protocol_incompatibility":
        return _case(**common, mode="protocol", key=None, certificate=None)
    if case_name == "connection_refused":
        return _case(**common, mode="refused", key=None, certificate=None)
    if case_name == "egress_network_missing":
        return _case(
            **common,
            mode=None,
            key=None,
            certificate=None,
            topology="missing",
        )
    if case_name == "wrong_network_attachment":
        return _case(
            **common,
            mode=None,
            key=None,
            certificate=None,
            topology="wrong",
        )
    raise ValueError("mock_case_unknown")


def run_case_harness(
    case_name: str,
    *,
    case_root: Path = CASE_ROOT,
) -> dict[str, Any]:
    expected = EXPECTED_CASES.get(case_name)
    if expected is None:
        raise ValueError("mock_case_unknown")
    case_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    case_path = case_root / f"{case_name}.json"
    if os.path.lexists(case_path):
        raise ValueError("mock_case_already_exists")
    image_id = _load_image_id()
    with tempfile.TemporaryDirectory(
        prefix=f"phase2-proxy-tls-probe-{case_name}-"
    ) as name:
        result = _run_named_case(case_name, image_id=image_id, root=Path(name))
    validate_case_result(case_name, result, expected=expected)
    residue = _residue_counts()
    if residue != {"container_count": 0, "network_count": 0}:
        raise RuntimeError("mock_runtime_residue")
    _write_json(case_path, result)
    return result


def _read_case(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    if path.is_symlink() or not path.is_file() or metadata.st_mode & 0o022:
        raise ValueError("mock_case_artifact_invalid")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("mock_case_artifact_invalid") from error
    expected_raw = (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if not isinstance(value, dict) or raw != expected_raw:
        raise ValueError("mock_case_artifact_invalid")
    return value


def finalize_case_results(
    *,
    case_root: Path = CASE_ROOT,
    output_path: Path = OUTPUT_PATH,
    image_id: str | None = None,
    residue: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    results = {
        case: _read_case(case_root / f"{case}.json") for case in EXPECTED_CASES
    }
    effective_residue = dict(residue) if residue is not None else _residue_counts()
    report = build_mock_report(
        results=results,
        image_id=image_id or _load_image_id(),
        residue=effective_residue,
    )
    _write_json(output_path, report)
    for case in EXPECTED_CASES:
        (case_root / f"{case}.json").unlink()
    case_root.rmdir()
    return report


def run_harness(output_path: Path = OUTPUT_PATH) -> dict[str, Any]:
    image_id = _load_image_id()
    with tempfile.TemporaryDirectory(prefix="phase2-proxy-tls-probe-mock-") as name:
        root = Path(name)
        trusted_ca_key, trusted_ca = _create_ca(root, "trusted-root")
        unknown_ca_key, unknown_ca = _create_ca(root, "unknown-root")
        trusted_key, trusted_certificate = _create_server_certificate(
            root,
            "trusted",
            TARGET_HOST,
            trusted_ca_key,
            trusted_ca,
        )
        unknown_key, unknown_certificate = _create_server_certificate(
            root,
            "unknown",
            TARGET_HOST,
            unknown_ca_key,
            unknown_ca,
        )
        mismatch_key, mismatch_certificate = _create_server_certificate(
            root,
            "mismatch",
            "wrong.internal.test",
            trusted_ca_key,
            trusted_ca,
        )

        def run_case(
            case_name: str,
            *,
            mode: str | None,
            key: Path | None = None,
            certificate: Path | None = None,
            ca_bundle: Path = trusted_ca,
            topology: str = "valid",
        ) -> dict[str, Any]:
            return _case(
                case_name=case_name,
                image_id=image_id,
                root=root,
                ca_bundle=ca_bundle,
                mode=mode,
                key=key,
                certificate=certificate,
                topology=topology,
            )

        results = {
            "happy_path_1": run_case(
                "happy_path_1",
                mode="tls",
                key=trusted_key,
                certificate=trusted_certificate,
            ),
            "happy_path_2": run_case(
                "happy_path_2",
                mode="tls",
                key=trusted_key,
                certificate=trusted_certificate,
            ),
            "happy_path_3": run_case(
                "happy_path_3",
                mode="tls",
                key=trusted_key,
                certificate=trusted_certificate,
            ),
            "relay_internal_plus_egress": run_case(
                "relay_internal_plus_egress",
                mode="tls",
                key=trusted_key,
                certificate=trusted_certificate,
            ),
            "unknown_ca": run_case(
                "unknown_ca",
                mode="tls",
                key=unknown_key,
                certificate=unknown_certificate,
            ),
            "hostname_mismatch": run_case(
                "hostname_mismatch",
                mode="tls",
                key=mismatch_key,
                certificate=mismatch_certificate,
            ),
            "connection_reset": run_case("connection_reset", mode="reset"),
            "handshake_timeout": run_case("handshake_timeout", mode="timeout"),
            "protocol_incompatibility": run_case(
                "protocol_incompatibility", mode="protocol"
            ),
            "connection_refused": run_case(
                "connection_refused", mode="refused"
            ),
            "egress_network_missing": run_case(
                "egress_network_missing", mode=None, topology="missing"
            ),
            "wrong_network_attachment": run_case(
                "wrong_network_attachment", mode=None, topology="wrong"
            ),
        }
        residue = _residue_counts()
        report = build_mock_report(
            results=results,
            image_id=image_id,
            residue=residue,
        )
        _write_json(output_path, report)
        return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--case", choices=tuple(EXPECTED_CASES))
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if args.case and args.finalize:
        raise SystemExit("--case and --finalize are mutually exclusive")
    if args.case:
        result = run_case_harness(args.case)
        print(result["terminal_status"])
        return 0
    if args.finalize:
        report = finalize_case_results(output_path=args.output)
        print(report["status"])
        return 0
    report = run_harness(args.output)
    print(report["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
