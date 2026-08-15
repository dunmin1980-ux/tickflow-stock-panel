#!/usr/bin/env python3
"""Run an HTTP-free Ark Probe/Proxy TLS comparison on Docker internal networks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = (
    REPO_ROOT
    / "reports/phase2_provider_ark/proxy_tls_differential/local_parity.json"
)
IMAGE_ID = "sha256:a8a01a003f7afc385b4e24fd2960bcdfd3eaac40c394f39aa6249fd7b2f0f79b"
TARGET_HOST = "ark.cn-beijing.volces.com"
PROXY_SOURCE = REPO_ROOT / "docker/phase2-ark-egress-proxy/proxy.py"
PROBE_SOURCE = REPO_ROOT / "docker/phase2-ark-tls-connectivity-probe/probe.py"
CLIENT_SOURCE = REPO_ROOT / "docker/phase2-ark-proxy-tls-mock/client.py"
SERVER_SOURCE = REPO_ROOT / "docker/phase2-ark-proxy-tls-mock/server.py"
EXPECTED_CATEGORIES = {
    "trusted_proxy": "PASSED",
    "trusted_probe": "PROBE_PASSED",
    "unknown_ca": "TLS_CERT_VERIFY_FAILED",
    "hostname_mismatch": "TLS_HOSTNAME_VERIFY_FAILED",
    "connection_reset": "TLS_CONNECTION_RESET",
    "handshake_timeout": "TLS_HANDSHAKE_TIMEOUT",
    "protocol_incompatibility": "TLS_PROTOCOL_FAILED",
    "connection_refused": "PROVIDER_CONNECT_FAILED",
}


def network_create_command(name: str) -> list[str]:
    return [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        name,
    ]


def _run(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _mount(source: Path, destination: str) -> str:
    return f"type=bind,src={source},dst={destination},readonly"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _openssl(*arguments: str, cwd: Path) -> None:
    executable = shutil.which("openssl")
    if executable is None:
        raise RuntimeError("openssl_missing")
    _run([executable, *arguments], timeout=30.0)


def _create_ca(root: Path, name: str) -> tuple[Path, Path]:
    key = root / f"{name}.key"
    certificate = root / f"{name}.crt"
    _openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(certificate), "-days", "2",
        "-sha256", "-subj", f"/CN={name}", cwd=root,
    )
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
        "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(request), "-sha256",
        "-subj", f"/CN={hostname}", cwd=root,
    )
    _openssl(
        "x509", "-req", "-in", str(request), "-CA", str(ca_certificate),
        "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(certificate),
        "-days", "2", "-sha256", "-extfile", str(extension), cwd=root,
    )
    certificate.chmod(0o644)
    key.chmod(0o644)
    return key, certificate


def _wait_ready(container: str) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        result = _run(["docker", "logs", container], check=False, timeout=5.0)
        if "READY" in result.stdout:
            return
        time.sleep(0.05)
    raise RuntimeError("mock_server_not_ready")


def _start_server(
    *,
    name: str,
    network: str,
    mode: str,
    key: Path | None,
    certificate: Path | None,
) -> None:
    command = [
        "docker", "run", "--detach", "--name", name,
        "--network", network, "--network-alias", TARGET_HOST,
        "--read-only", "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE",
        "--security-opt", "no-new-privileges", "--user", "0:0",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint", "/usr/bin/python3",
        "--mount", _mount(SERVER_SOURCE, "/work/server.py"),
    ]
    if key is not None and certificate is not None:
        command.extend(
            [
                "--mount", _mount(key, "/work/server.key"),
                "--mount", _mount(certificate, "/work/server.crt"),
            ]
        )
    command.extend([IMAGE_ID, "/work/server.py", "--mode", mode])
    if key is not None and certificate is not None:
        command.extend(["--key", "/work/server.key", "--cert", "/work/server.crt"])
    _run(command)
    _wait_ready(name)


def _run_client(
    *,
    network: str,
    implementation: str,
    ca_bundle: Path,
    timeout: float,
    host: str = TARGET_HOST,
) -> dict[str, Any]:
    command = [
        "docker", "run", "--rm", "--network", network,
        "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "65532:65532",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint", "/usr/bin/python3",
        "--mount", _mount(PROXY_SOURCE, "/work/proxy.py"),
        "--mount", _mount(PROBE_SOURCE, "/work/probe.py"),
        "--mount", _mount(CLIENT_SOURCE, "/work/client.py"),
        "--mount", _mount(ca_bundle, "/etc/ssl/certs/ca-certificates.crt"),
        IMAGE_ID, "/work/client.py", "--implementation", implementation,
        "--timeout", str(timeout), "--host", host,
    ]
    completed = _run(command, timeout=max(10.0, timeout + 5.0))
    return json.loads(completed.stdout)


def runtime_residue_counts(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = _run,
) -> dict[str, int]:
    containers = run(
        ["docker", "container", "ls", "--all", "--format", "{{.Names}}"],
        check=True,
    ).stdout.splitlines()
    networks = run(
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        check=True,
    ).stdout.splitlines()
    return {
        "container_count": sum(
            name.startswith("phase2-ark-tls-mock-") for name in containers
        ),
        "network_count": sum(
            name.startswith("phase2-ark-tls-parity-") for name in networks
        ),
    }


def cleanup_and_verify(
    name: str,
    network: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = _run,
) -> None:
    run(["docker", "rm", "--force", name], check=False)
    run(["docker", "network", "rm", network], check=False)
    if runtime_residue_counts(run=run) != {
        "container_count": 0,
        "network_count": 0,
    }:
        raise RuntimeError("parity_runtime_residue")


def _case(
    *,
    case_name: str,
    mode: str | None,
    key: Path | None,
    certificate: Path | None,
    ca_bundle: Path,
    implementation: str = "proxy",
    timeout: float = 1.0,
    host: str = TARGET_HOST,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    network = f"phase2-ark-tls-parity-{suffix}"
    container = f"phase2-ark-tls-mock-{suffix}"
    _run(network_create_command(network))
    try:
        inspected = json.loads(_run(["docker", "network", "inspect", network]).stdout)[0]
        if inspected.get("Internal") is not True:
            raise RuntimeError("network_not_internal")
        if mode is not None:
            _start_server(
                name=container,
                network=network,
                mode=mode,
                key=key,
                certificate=certificate,
            )
        result = _run_client(
            network=network,
            implementation=implementation,
            ca_bundle=ca_bundle,
            timeout=timeout,
            host=host,
        )
        server_observed_sni = None
        if mode == "tls":
            logs = _run(["docker", "logs", container], check=False).stdout.splitlines()
            observed = [
                line.removeprefix("SNI_OBSERVED:")
                for line in logs
                if line.startswith("SNI_OBSERVED:")
            ]
            if len(observed) == 1 and observed[0] != "NONE":
                server_observed_sni = observed[0]
        result["case"] = case_name
        result["docker_network_internal"] = True
        result["server_observed_sni"] = server_observed_sni
        return result
    finally:
        cleanup_and_verify(container, network)


def validate_case_results(results: Mapping[str, Mapping[str, Any]]) -> None:
    if set(results) != set(EXPECTED_CATEGORIES):
        raise ValueError("parity_result_invalid")
    for name, expected in EXPECTED_CATEGORIES.items():
        result = results[name]
        if (
            result.get("category") != expected
            or result.get("docker_network_internal") is not True
            or result.get("secret_content_read") is not False
            or result.get("authorization_constructed") is not False
            or result.get("http_request_sent") is not False
            or result.get("provider_attempt_count") != 0
            or result.get("ai_call_count") != 0
            or result.get("real_public_network_success_count") != 0
        ):
            raise ValueError("parity_result_invalid")
    trusted_proxy = results["trusted_proxy"]
    trusted_probe = results["trusted_probe"]
    if (
        trusted_proxy.get("tls_version") != trusted_probe.get("tls_version")
        or trusted_proxy.get("cipher_name") != trusted_probe.get("cipher_name")
        or trusted_proxy.get("ca_bundle_sha256") != trusted_probe.get("ca_bundle_sha256")
        or trusted_proxy.get("sni_hostname") != trusted_probe.get("sni_hostname")
        or trusted_proxy.get("hostname_verification_target")
        != trusted_probe.get("hostname_verification_target")
        or trusted_proxy.get("server_observed_sni") != TARGET_HOST
        or trusted_probe.get("server_observed_sni") != TARGET_HOST
        or results["hostname_mismatch"].get("server_observed_sni") != TARGET_HOST
    ):
        raise ValueError("parity_result_invalid")


def run_harness(output_path: Path = OUTPUT_PATH) -> dict[str, Any]:
    if _run(["docker", "image", "inspect", IMAGE_ID], check=False).returncode != 0:
        raise RuntimeError("proxy_image_missing")
    with tempfile.TemporaryDirectory(prefix="tickflow-ark-tls-parity-") as name:
        root = Path(name)
        ca1_key, ca1_certificate = _create_ca(root, "trusted-root")
        ca2_key, ca2_certificate = _create_ca(root, "unknown-root")
        trusted_key, trusted_certificate = _create_server_certificate(
            root, "trusted", TARGET_HOST, ca1_key, ca1_certificate
        )
        unknown_key, unknown_certificate = _create_server_certificate(
            root, "unknown", TARGET_HOST, ca2_key, ca2_certificate
        )
        mismatch_key, mismatch_certificate = _create_server_certificate(
            root, "mismatch", "wrong.internal.test", ca1_key, ca1_certificate
        )
        results = {
            "trusted_proxy": _case(
                case_name="trusted_proxy", mode="tls", key=trusted_key,
                certificate=trusted_certificate, ca_bundle=ca1_certificate,
            ),
            "trusted_probe": _case(
                case_name="trusted_probe", mode="tls", key=trusted_key,
                certificate=trusted_certificate, ca_bundle=ca1_certificate,
                implementation="probe",
            ),
            "unknown_ca": _case(
                case_name="unknown_ca", mode="tls", key=unknown_key,
                certificate=unknown_certificate, ca_bundle=ca1_certificate,
            ),
            "hostname_mismatch": _case(
                case_name="hostname_mismatch", mode="tls", key=mismatch_key,
                certificate=mismatch_certificate, ca_bundle=ca1_certificate,
            ),
            "connection_reset": _case(
                case_name="connection_reset", mode="reset", key=None,
                certificate=None, ca_bundle=ca1_certificate,
            ),
            "handshake_timeout": _case(
                case_name="handshake_timeout", mode="timeout", key=None,
                certificate=None, ca_bundle=ca1_certificate, timeout=0.5,
            ),
            "protocol_incompatibility": _case(
                case_name="protocol_incompatibility", mode="protocol", key=None,
                certificate=None, ca_bundle=ca1_certificate,
            ),
            "connection_refused": _case(
                case_name="connection_refused", mode=None, key=None,
                certificate=None, ca_bundle=ca1_certificate, host="127.0.0.1",
            ),
        }
        validate_case_results(results)
        residue = runtime_residue_counts()
        if residue != {"container_count": 0, "network_count": 0}:
            raise RuntimeError("parity_runtime_residue")
        report = {
            "parity_schema_version": 1,
            "status": "LOCAL_PROXY_TLS_PATH_VALIDATED",
            "proxy_image_id": IMAGE_ID,
            "proxy_source_sha256": hashlib.sha256(PROXY_SOURCE.read_bytes()).hexdigest(),
            "probe_source_sha256": hashlib.sha256(PROBE_SOURCE.read_bytes()).hexdigest(),
            "all_networks_internal": True,
            "secret_content_read": False,
            "authorization_constructed": False,
            "http_request_sent": False,
            "new_provider_attempts": 0,
            "new_ai_calls": 0,
            "real_public_network_success_count": 0,
            "runtime_residue": residue,
            "cases": results,
        }
        _write_json(output_path, report)
        return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    report = run_harness(args.output)
    print(report["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
