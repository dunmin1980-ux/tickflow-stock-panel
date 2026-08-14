#!/usr/bin/env python3
"""Exercise the Ark TLS probe on Docker internal-only mock networks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_SOURCE = REPO_ROOT / "docker/phase2-ark-tls-connectivity-probe/probe.py"
OUTPUT_PATH = (
    REPO_ROOT / "reports/phase2_provider_ark/tls_connectivity_probe/mock_e2e.json"
)
IMAGE_ID = "sha256:a8a01a003f7afc385b4e24fd2960bcdfd3eaac40c394f39aa6249fd7b2f0f79b"
TARGET_HOST = "ark.cn-beijing.volces.com"

TLS_SERVER_SOURCE = r'''from __future__ import annotations
import socket, ssl
listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
listener.bind(("0.0.0.0",443)); listener.listen(1)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/mock/server.crt","/mock/server.key")
connection,_address=listener.accept()
try:
 tls=context.wrap_socket(connection,server_side=True)
 try:
  raw=tls.unwrap(); raw.close()
 except (OSError,ssl.SSLError):
  tls.close()
except (OSError,ssl.SSLError):
 connection.close()
listener.close()
'''

STALL_SERVER_SOURCE = r'''import socket,time
listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
listener.bind(("0.0.0.0",443)); listener.listen(1)
connection,_address=listener.accept(); time.sleep(2); connection.close(); listener.close()
'''

TIMEOUT_ENTRY_SOURCE = r'''from __future__ import annotations
import importlib.util
from pathlib import Path
path=Path("/probe/probe.py")
spec=importlib.util.spec_from_file_location("probe",path)
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
probe_id=module._read_probe_id()
receipt=module.execute_probe(probe_id,policy=module.ProbePolicy(1.0,0.3,0.3))
module._atomic_write_json(module.OUTPUT_PATH,receipt)
raise SystemExit(0 if receipt["terminal_status"]=="PROBE_PASSED" else 2)
'''


class MockProbeError(RuntimeError):
    pass


def _run(command: Sequence[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _require(result: subprocess.CompletedProcess[str], code: str) -> None:
    if result.returncode != 0:
        raise MockProbeError(code)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        raw = (json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _generate_certificate(root: Path, *, hostname: str, prefix: str) -> tuple[Path, Path]:
    certificate = root / f"{prefix}.crt"
    private_key = root / f"{prefix}.key"
    result = _run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
            "-nodes", "-days", "1", "-subj", f"/CN={hostname}",
            "-addext", f"subjectAltName=DNS:{hostname}",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign",
            "-addext", "extendedKeyUsage=serverAuth",
            "-keyout", str(private_key), "-out", str(certificate),
        ],
        timeout=20,
    )
    _require(result, "mock_certificate_generation_failed")
    private_key.chmod(0o600); certificate.chmod(0o644)
    return certificate, private_key


def _mount(source: Path, destination: str, *, readonly: bool = True) -> str:
    value = f"type=bind,src={source},dst={destination}"
    return value + (",readonly" if readonly else "")


def _remove_container(name: str) -> None:
    if _run(["docker", "container", "inspect", name], timeout=10).returncode == 0:
        _run(["docker", "container", "rm", "--force", name], timeout=15)


def _remove_network(name: str) -> None:
    if _run(["docker", "network", "inspect", name], timeout=10).returncode == 0:
        _run(["docker", "network", "rm", name], timeout=15)


def _start_tls_server(
    *,
    name: str,
    network: str,
    source: Path,
    certificate: Path,
    private_key: Path,
) -> None:
    command = [
        "docker", "run", "-d", "--name", name, "--network", network,
        "--network-alias", TARGET_HOST, "--read-only", "--user", "0:0",
        "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE",
        "--security-opt", "no-new-privileges", "--restart", "no",
        "--pids-limit", "16", "--memory", "64m", "--cpus", "0.25",
        "--mount", _mount(source, "/mock/server.py"),
        "--mount", _mount(certificate, "/mock/server.crt"),
        "--mount", _mount(private_key, "/mock/server.key"),
        "--entrypoint", "/usr/bin/python3", IMAGE_ID, "/mock/server.py",
    ]
    _require(_run(command), "mock_tls_server_start_failed")
    time.sleep(0.5)


def _start_stall_server(*, name: str, network: str, source: Path) -> None:
    command = [
        "docker", "run", "-d", "--name", name, "--network", network,
        "--network-alias", TARGET_HOST, "--read-only", "--user", "0:0",
        "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE",
        "--security-opt", "no-new-privileges", "--restart", "no",
        "--mount", _mount(source, "/mock/server.py"),
        "--entrypoint", "/usr/bin/python3", IMAGE_ID, "/mock/server.py",
    ]
    _require(_run(command), "mock_stall_server_start_failed")
    time.sleep(0.5)


def _start_refused_alias(*, name: str, network: str) -> None:
    command = [
        "docker", "run", "-d", "--name", name, "--network", network,
        "--network-alias", TARGET_HOST, "--read-only", "--user", "65532:65532",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--restart", "no", "--entrypoint", "/usr/bin/python3", IMAGE_ID,
        "-c", "import time; time.sleep(5)",
    ]
    _require(_run(command), "mock_refused_alias_start_failed")
    time.sleep(0.3)


def _run_client(
    *,
    name: str,
    network: str,
    output: Path,
    ca_file: Path | None,
    timeout_entry: Path | None,
) -> subprocess.CompletedProcess[str]:
    command = [
        "docker", "run", "--name", name, "--network", network, "--read-only",
        "--user", "65532:65532", "--cap-drop", "ALL", "--security-opt",
        "no-new-privileges", "--restart", "no", "--pids-limit", "16",
        "--memory", "64m", "--cpus", "0.25", "--ipc", "none",
        "--mount", _mount(PROBE_SOURCE, "/probe/probe.py"),
        "--mount", _mount(output, "/output", readonly=False),
    ]
    if ca_file is not None:
        command.extend(
            ["--mount", _mount(ca_file, "/etc/ssl/certs/ca-certificates.crt")]
        )
    entrypoint = "/probe/probe.py"
    if timeout_entry is not None:
        command.extend(["--mount", _mount(timeout_entry, "/mock/timeout_entry.py")])
        entrypoint = "/mock/timeout_entry.py"
    command.extend(["--entrypoint", "/usr/bin/python3", IMAGE_ID, entrypoint])
    return _run(command, timeout=10)


def _run_scenario(
    *,
    root: Path,
    name: str,
    expected_category: str,
    certificate: Path | None = None,
    private_key: Path | None = None,
    ca_file: Path | None = None,
    server_kind: str = "tls",
    timeout_entry: Path | None = None,
    tls_server_source: Path,
    stall_server_source: Path,
) -> dict[str, Any]:
    probe_id = uuid.uuid4().hex
    suffix = probe_id[:12]
    network = f"phase2-ark-tls-mock-net-{suffix}"
    server = f"phase2-ark-tls-mock-server-{suffix}"
    client = f"phase2-ark-tls-mock-client-{suffix}"
    output = root / name
    output.mkdir(mode=0o777); output.chmod(0o777)
    probe_id_path = output / "probe-id"
    probe_id_path.write_text(probe_id + "\n", encoding="ascii"); probe_id_path.chmod(0o444)
    _require(_run(["docker", "network", "create", "--internal", network]), "mock_network_failed")
    inspect = _run(["docker", "network", "inspect", "--format", "{{.Internal}}", network])
    network_internal = inspect.returncode == 0 and inspect.stdout.strip() == "true"
    try:
        if server_kind == "tls":
            assert certificate is not None and private_key is not None
            _start_tls_server(
                name=server, network=network, source=tls_server_source,
                certificate=certificate, private_key=private_key,
            )
        elif server_kind == "stall":
            _start_stall_server(name=server, network=network, source=stall_server_source)
        elif server_kind == "refused":
            _start_refused_alias(name=server, network=network)
        elif server_kind != "dns_missing":
            raise MockProbeError("mock_server_kind_invalid")
        _run_client(
            name=client, network=network, output=output,
            ca_file=ca_file, timeout_entry=timeout_entry,
        )
        receipt_path = output / "child-receipt.json"
        if not receipt_path.is_file():
            raise MockProbeError("mock_child_receipt_missing")
        receipt = json.loads(receipt_path.read_bytes())
        category = receipt.get("tls_error_category")
        if category != expected_category:
            raise MockProbeError(f"mock_category_mismatch:{name}:{category}")
        return {
            "name": name,
            "probe_attempt_count": receipt.get("probe_attempt_count"),
            "retry_count": receipt.get("retry_count"),
            "tls_error_category": category,
            "terminal_status": receipt.get("terminal_status"),
            "network_internal": network_internal,
        }
    finally:
        _remove_container(client); _remove_container(server); _remove_network(network)


def main() -> int:
    image = _run(["docker", "image", "inspect", IMAGE_ID, "--format", "{{.Id}}"])
    _require(image, "mock_image_missing")
    if image.stdout.strip() != IMAGE_ID:
        raise MockProbeError("mock_image_identity_mismatch")
    scenarios: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="phase2-ark-tls-probe-mock-") as name:
        root = Path(name); root.chmod(0o700)
        tls_server_source = root / "tls_server.py"
        stall_server_source = root / "stall_server.py"
        timeout_entry = root / "timeout_entry.py"
        tls_server_source.write_text(TLS_SERVER_SOURCE, encoding="utf-8")
        stall_server_source.write_text(STALL_SERVER_SOURCE, encoding="utf-8")
        timeout_entry.write_text(TIMEOUT_ENTRY_SOURCE, encoding="utf-8")
        correct_cert, correct_key = _generate_certificate(
            root, hostname=TARGET_HOST, prefix="correct"
        )
        wrong_cert, wrong_key = _generate_certificate(
            root, hostname="wrong.local", prefix="wrong"
        )
        cases = (
            dict(name="trusted_ca_correct_hostname", expected_category="NONE", certificate=correct_cert, private_key=correct_key, ca_file=correct_cert),
            dict(name="unknown_ca", expected_category="TLS_CERT_VERIFY_FAILED", certificate=correct_cert, private_key=correct_key),
            dict(name="hostname_mismatch", expected_category="TLS_HOSTNAME_VERIFY_FAILED", certificate=wrong_cert, private_key=wrong_key, ca_file=wrong_cert),
            dict(name="connection_refused", expected_category="TCP_CONNECT_FAILED", server_kind="refused"),
            dict(name="missing_dns_alias", expected_category="DNS_RESOLUTION_FAILED", server_kind="dns_missing"),
            dict(name="handshake_timeout", expected_category="TLS_HANDSHAKE_TIMEOUT", server_kind="stall", timeout_entry=timeout_entry),
        )
        for case in cases:
            scenarios.append(
                _run_scenario(
                    root=root,
                    tls_server_source=tls_server_source,
                    stall_server_source=stall_server_source,
                    **case,
                )
            )
    container_residue = len(
        _run(["docker", "ps", "-a", "--filter", "name=phase2-ark-tls-mock-", "--format", "{{.ID}}"], timeout=10).stdout.split()
    )
    network_residue = len(
        _run(["docker", "network", "ls", "--filter", "name=phase2-ark-tls-mock-net-", "--format", "{{.ID}}"], timeout=10).stdout.split()
    )
    value = {
        "mock_e2e_schema_version": 1,
        "status": "PASSED" if all(item["network_internal"] for item in scenarios) and not container_residue and not network_residue else "FAILED",
        "proxy_image_id": IMAGE_ID,
        "probe_source_sha256": hashlib.sha256(PROBE_SOURCE.read_bytes()).hexdigest(),
        "scenario_count": len(scenarios),
        "real_public_network_success_count": 0,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "retry_count": 0,
        "container_residue_count": container_residue,
        "network_residue_count": network_residue,
        "temporary_residue_count": 0,
        "scenarios": scenarios,
    }
    _atomic_json(OUTPUT_PATH, value)
    print(json.dumps({"status": value["status"], "output": str(OUTPUT_PATH)}, sort_keys=True))
    return 0 if value["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
