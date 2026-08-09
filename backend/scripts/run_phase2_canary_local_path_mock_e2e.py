#!/usr/bin/env python3
"""Exercise the Canary Docker path against an internal one-shot TLS provider."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
import time
import types
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.phase2_ai_worker_protocol import (
    FakeClaimsWorker,
    build_worker_projection,
)
from app.services.phase2_canary_orchestrator import (
    ApprovedCanaryArtifacts,
    CanaryOrchestratorConfig,
    DockerCanaryBackend,
    OrchestratorError,
    load_installed_runtime_approval,
    run_single_symbol_canary,
)
from app.services.phase2_canary_runtime_artifact import (
    BASE_IMAGE_REFERENCE,
    verify_runtime_artifact_candidate,
)
from app.services.phase2_claims_service import canonical_json_bytes

_PROVIDER_EVENTS = (
    "provider_connect_started",
    "provider_connect_completed",
    "tls_completed",
    "request_write_started",
    "request_write_completed",
    "response_headers_received",
    "response_body_completed",
)
_HISTORICAL_REQUESTS = (
    "d59766101b63450e8148541d589a90bf",
    "3d3e6adbe5b74c98ad18a2efe0fd1d0c",
)
_HISTORICAL_FROZEN_SHA256 = {
    "reports/phase2_provider_canary/consumed_unknown_diagnosis.json": (
        "be69267d764634ce39ae8311ddbf2dcba11b1709baa5c999aad11bcd17e8d529"
    ),
    "reports/tickflow_phase2b_consumed_unknown_diagnosis.md": (
        "601eb1b07607a09ff662b10793d67bc912b5958df69b28a391826fdcc8628d93"
    ),
    "reports/tickflow_phase2b_single_symbol_canary_eval.md": (
        "a1ec5092f1c7db423d9b27f946c93bb904969b13bb67fb810888ad83ef7e5bd5"
    ),
    "reports/phase2_provider_canary/superseded/"
    "b30c156bee1f5f2a1c8d44004fcbd25934e56a1caf6739ab58ffe7ead0e881fb.json": (
        "b30c156bee1f5f2a1c8d44004fcbd25934e56a1caf6739ab58ffe7ead0e881fb"
    ),
    "reports/phase2_provider_canary/superseded/"
    "e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b.json": (
        "e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b"
    ),
    "reports/phase2_provider_canary/superseded/"
    "45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127.json": (
        "45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127"
    ),
    "reports/phase2_provider_canary/live_canary/evidence/"
    "d59766101b63450e8148541d589a90bf.json": (
        "0ef11071083053ac7db825b39492e7e5d24ce27d32cc40c6daa90f43138603e4"
    ),
    "reports/phase2_provider_canary/live_canary/rejected/"
    "d59766101b63450e8148541d589a90bf.json": (
        "ad7bada723a39a4fd0b266a3740e59f3e040d4ce81e79e6e594b6a2b3785e2f4"
    ),
    "reports/phase2_provider_canary/live_canary/evidence/"
    "3d3e6adbe5b74c98ad18a2efe0fd1d0c.json": (
        "bcabab31b35bf8805b1474d508f1e8067c62299aa7d501425f98cb77e8b1a01e"
    ),
    "reports/phase2_provider_canary/live_canary/rejected/"
    "3d3e6adbe5b74c98ad18a2efe0fd1d0c.json": (
        "8d8f7760de1736c549f3ec7c4cd5abadc8599c30b0d12cf34bc28cc905070638"
    ),
    "reports/phase2_provider_canary/live_canary/receipts/"
    "3d3e6adbe5b74c98ad18a2efe0fd1d0c/archive-status.json": (
        "f62dfc0ab09307cb0cd21aa9fa43a746bc8e32dd9a263ab31eb7586414739b8d"
    ),
    "reports/phase2_provider_canary/live_canary/receipts/"
    "3d3e6adbe5b74c98ad18a2efe0fd1d0c/child-metadata.json": (
        "c13fd267d78f57550d229fd76ce84d9b3b0d45494e40226cc159ffd0973fdad7"
    ),
    "reports/phase2_provider_canary/live_canary/receipts/"
    "3d3e6adbe5b74c98ad18a2efe0fd1d0c/relay-receipt.json": (
        "25acd73fed9bc65053421b57eff7e59acf00db1d0cf37c3e245c3eb49a1a3c17"
    ),
}
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_PREF_FIX_CANDIDATE_SHA256 = (
    "e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b"
)
_PREF_FIX_RELAY_COMMIT = "c54b8277ead1c44b95ea0ff5c0776d9948c8c3fd"
_PREF_FIX_RELAY_PATH = "docker/phase2-canary-relay/relay.py"
_PREF_FIX_RELAY_SHA256 = (
    "cafd46a67ba08da1d92c2158771af2d529373a87558bbb392d1b5d9e9f81ee2b"
)
_MOCK_TOPOLOGY_DELTAS = (
    {
        "component": "provider_endpoint",
        "production": "api.openai.com:443",
        "mock": "internal Mock Provider with alias api.openai.com:443",
    },
    {
        "component": "proxy_egress_network",
        "production": "dedicated egress network",
        "mock": "internal-only Docker network",
    },
    {
        "component": "proxy_trust_store",
        "production": "immutable image trust store",
        "mock": "ephemeral test CA bind-mounted read-only",
    },
)
_PRESERVED_REAL_TOPOLOGY_IDENTITIES = (
    "one_shot_orchestrator",
    "runtime_approval_loader_v2",
    "proxy_image_id",
    "relay_image_id",
    "runtime_contract_sha256",
    "readiness_contract_sha256",
    "request_contract",
    "projection_sha256",
    "attempt_ledger",
    "claims_validator",
    "deterministic_renderer",
)


def _topology_deltas_verified(observed: set[str]) -> bool:
    expected = {item["component"] for item in _MOCK_TOPOLOGY_DELTAS}
    return observed == expected


_EXPECTED_MOCK_PROVIDER_COMMANDS = [
    [
        "docker",
        "create",
        "--name",
        "<MOCK_NAME>",
        "--network",
        "<EGRESS_NETWORK>",
        "--network-alias",
        "api.openai.com",
        "--read-only",
        "--user",
        "0:0",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "NET_BIND_SERVICE",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "16",
        "--memory",
        "64m",
        "--cpus",
        "0.25",
        "--ipc",
        "none",
        "--restart",
        "no",
        "--mount",
        "type=bind,src=<MOCK_SERVER>,dst=/mock/server.py,readonly",
        "--mount",
        "type=bind,src=<TEST_CA>,dst=/mock/server.crt,readonly",
        "--mount",
        "type=bind,src=<TEST_TLS_KEY>,dst=/mock/server.key,readonly",
        "--mount",
        "type=bind,src=<MOCK_RESPONSE>,dst=/mock/response.json,readonly",
        "--mount",
        "type=bind,src=<MOCK_OUTPUT>,dst=/output",
        "<BASE_IMAGE>",
        "/mock/server.py",
    ],
    ["docker", "start", "<MOCK_NAME>"],
]


def _topology_command_diffs_verified(records: list[dict[str, Any]]) -> bool:
    if any(
        not isinstance(record, dict)
        or set(record)
        != {"component", "original_commands", "transformed_commands"}
        for record in records
    ):
        return False
    by_component = {record["component"]: record for record in records}
    if set(by_component) != {
        "provider_endpoint",
        "proxy_egress_network",
        "proxy_trust_store",
    } or len(by_component) != len(records):
        return False

    network = by_component["proxy_egress_network"]
    originals = network["original_commands"]
    transformed = network["transformed_commands"]
    if (
        not isinstance(originals, list)
        or len(originals) != 1
        or not isinstance(originals[0], list)
        or originals[0][:3] != ["docker", "network", "create"]
        or originals[0][-1:] != ["<EGRESS_NETWORK>"]
        or "--internal" in originals[0]
        or transformed
        != [[*originals[0][:-1], "--internal", originals[0][-1]]]
    ):
        return False

    trust = by_component["proxy_trust_store"]
    originals = trust["original_commands"]
    transformed = trust["transformed_commands"]
    if (
        not isinstance(originals, list)
        or len(originals) != 1
        or not isinstance(originals[0], list)
        or not originals[0]
        or not isinstance(transformed, list)
        or len(transformed) != 1
    ):
        return False
    expected_proxy = [
        *originals[0][:-1],
        "--mount",
        "type=bind,src=<TEST_CA>,dst=/etc/ssl/certs/ca-certificates.crt,readonly",
        originals[0][-1],
    ]
    if transformed[0] != expected_proxy:
        return False

    provider = by_component["provider_endpoint"]
    return (
        provider["original_commands"] == []
        and provider["transformed_commands"]
        == _EXPECTED_MOCK_PROVIDER_COMMANDS
    )
_MOCK_SERVER_SOURCE = r'''from __future__ import annotations

import json
import os
import ssl
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

OUTPUT = Path("/output")
RESPONSE = Path("/mock/response.json").read_bytes()


def atomic_json(path: Path, value: dict) -> None:
    raw = (json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, raw)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if 0 < length <= 1_048_576 else b""
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        authorization_present = bool(self.headers.get("Authorization"))
        valid = (
            self.path == "/v1/responses"
            and self.headers.get_content_type() == "application/json"
            and authorization_present
            and isinstance(payload, dict)
            and set(payload) == {"model", "stream", "store", "tools", "input", "text"}
            and payload.get("model") == "gpt-5.6-terra"
            and payload.get("stream") is False
            and payload.get("store") is False
            and payload.get("tools") == []
        )
        status = 200 if valid else 400
        response = RESPONSE if valid else b'{"error":"mock_contract_rejected"}'
        atomic_json(
            OUTPUT / "mock-receipt.json",
            {
                "attempt_count": 1,
                "authorization_present": authorization_present,
                "path": self.path,
                "request_contract_valid": valid,
                "status": status,
            },
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)


server = HTTPServer(("0.0.0.0", 443), Handler, bind_and_activate=False)
server.server_bind()
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/mock/server.crt", "/mock/server.key")
server.socket = context.wrap_socket(server.socket, server_side=True)
server.server_activate()
atomic_json(OUTPUT / "mock-ready.json", {"listener_ready": True})
server.handle_request()
server.server_close()
'''


class LocalMockError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _run(
    command: Sequence[str],
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalMockError("local_command_failed") from exc


def _require_success(result: subprocess.CompletedProcess[str], code: str) -> None:
    if result.returncode != 0:
        raise LocalMockError(code)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {}
    value = json.loads(path.read_bytes())
    return value if isinstance(value, dict) else {}


def _optional_sha256(path: Path) -> str | None:
    return _sha256(path) if path.is_file() and not path.is_symlink() else None


def _frozen_manifest_matches(observed: dict[str, str]) -> bool:
    return observed == _HISTORICAL_FROZEN_SHA256


def _historical_manifest(repo_root: Path) -> dict[str, str]:
    live_root = repo_root / "reports/phase2_provider_canary/live_canary"
    live_prefix = "reports/phase2_provider_canary/live_canary/"
    expected_live = {
        relative
        for relative in _HISTORICAL_FROZEN_SHA256
        if relative.startswith(live_prefix)
    }
    if not live_root.is_dir() or live_root.is_symlink():
        raise LocalMockError("historical_evidence_missing")
    actual_live = {
        str(path.relative_to(repo_root))
        for path in live_root.rglob("*")
        if path.is_file()
    }
    if actual_live != expected_live:
        raise LocalMockError("historical_evidence_path_set_mutated")
    observed: dict[str, str] = {}
    for relative, expected_sha256 in _HISTORICAL_FROZEN_SHA256.items():
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise LocalMockError("historical_evidence_missing")
        actual_sha256 = _sha256(path)
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise LocalMockError("historical_evidence_hash_mutated")
        observed[relative] = actual_sha256
    if not _frozen_manifest_matches(observed):
        raise LocalMockError("historical_evidence_manifest_mutated")
    return observed


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise LocalMockError("evidence_path_invalid")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise LocalMockError("evidence_write_failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _provider_response(projection: dict[str, Any]) -> dict[str, Any]:
    candidate = json.loads(FakeClaimsWorker().run(projection))
    return {
        "completed_at": 1,
        "created_at": 0,
        "error": None,
        "id": "resp_phase2_local_mock",
        "incomplete_details": None,
        "model": "gpt-5.6-terra",
        "object": "response",
        "output": [
            {"id": "rs_local_mock", "summary": [], "type": "reasoning"},
            {
                "content": [
                    {
                        "annotations": [],
                        "logprobs": [],
                        "text": json.dumps(
                            candidate,
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                        ),
                        "type": "output_text",
                    }
                ],
                "id": "msg_local_mock",
                "role": "assistant",
                "status": "completed",
                "type": "message",
            },
        ],
        "status": "completed",
        "tools": [],
    }


def _generate_certificate(root: Path) -> tuple[Path, Path]:
    certificate = root / "server.crt"
    private_key = root / "server.key"
    result = _run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=api.openai.com",
            "-addext",
            "subjectAltName=DNS:api.openai.com",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign",
            "-addext",
            "extendedKeyUsage=serverAuth",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        timeout=20,
    )
    _require_success(result, "mock_certificate_generation_failed")
    private_key.chmod(0o600)
    certificate.chmod(0o444)
    return certificate, private_key


class LocalMockTopologyExecutor:
    """Inject an internal TLS mock while preserving Proxy/Relay commands."""

    def __init__(
        self,
        *,
        request_id: str,
        root: Path,
        certificate: Path,
        private_key: Path,
        response_path: Path,
        server_path: Path,
    ) -> None:
        self.request_id = request_id
        self.root = root
        self.certificate = certificate
        self.private_key = private_key
        self.response_path = response_path
        self.server_path = server_path
        self.mock_output = root / "mock-output"
        self.mock_output.mkdir(mode=0o700)
        self.mock_output.chmod(0o777)
        self.mock_name = f"phase2-local-mock-provider-{request_id[:12]}"
        self.egress_network = f"phase2-canary-egress-{request_id[:12]}"
        self.relay_network = f"phase2-canary-relay-{request_id[:12]}"
        self.mock_started = False
        self.egress_internal = False
        self.observed_topology_deltas: set[str] = set()
        self.topology_command_diffs: list[dict[str, Any]] = []

    @staticmethod
    def _mount(source: Path, destination: str, *, readonly: bool = True) -> str:
        suffix = ",readonly" if readonly else ""
        return f"type=bind,src={source},dst={destination}{suffix}"

    def _normalize_command(self, command: Sequence[str]) -> list[str]:
        replacements = (
            (str(self.server_path), "<MOCK_SERVER>"),
            (str(self.certificate), "<TEST_CA>"),
            (str(self.private_key), "<TEST_TLS_KEY>"),
            (str(self.response_path), "<MOCK_RESPONSE>"),
            (str(self.mock_output), "<MOCK_OUTPUT>"),
            (str(self.root), "<RUN_ROOT>"),
            (self.mock_name, "<MOCK_NAME>"),
            (self.egress_network, "<EGRESS_NETWORK>"),
            (self.relay_network, "<RELAY_NETWORK>"),
            (BASE_IMAGE_REFERENCE, "<BASE_IMAGE>"),
        )
        normalized: list[str] = []
        for raw in command:
            value = raw
            for original, replacement in replacements:
                value = value.replace(original, replacement)
            if value.startswith("phase2-openai-proxy-"):
                value = "<PROXY_NAME>"
            if value.startswith("sha256:") and command[:2] == ["docker", "create"]:
                value = "<PROXY_IMAGE>"
            normalized.append(value)
        return normalized

    def _record_topology_command_delta(
        self,
        *,
        component: str,
        original_commands: Sequence[Sequence[str]],
        transformed_commands: Sequence[Sequence[str]],
    ) -> None:
        if any(item["component"] == component for item in self.topology_command_diffs):
            raise LocalMockError("duplicate_topology_delta")
        self.topology_command_diffs.append(
            {
                "component": component,
                "original_commands": [
                    self._normalize_command(command)
                    for command in original_commands
                ],
                "transformed_commands": [
                    self._normalize_command(command)
                    for command in transformed_commands
                ],
            }
        )

    def _start_mock(self) -> None:
        create = [
            "docker",
            "create",
            "--name",
            self.mock_name,
            "--network",
            self.egress_network,
            "--network-alias",
            "api.openai.com",
            "--read-only",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "NET_BIND_SERVICE",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "16",
            "--memory",
            "64m",
            "--cpus",
            "0.25",
            "--ipc",
            "none",
            "--restart",
            "no",
            "--mount",
            self._mount(self.server_path, "/mock/server.py"),
            "--mount",
            self._mount(self.certificate, "/mock/server.crt"),
            "--mount",
            self._mount(self.private_key, "/mock/server.key"),
            "--mount",
            self._mount(self.response_path, "/mock/response.json"),
            "--mount",
            self._mount(self.mock_output, "/output", readonly=False),
            BASE_IMAGE_REFERENCE,
            "/mock/server.py",
        ]
        start = ["docker", "start", self.mock_name]
        self._record_topology_command_delta(
            component="provider_endpoint",
            original_commands=(),
            transformed_commands=(create, start),
        )
        _require_success(_run(create), "mock_container_create_failed")
        _require_success(
            _run(start),
            "mock_container_start_failed",
        )
        self.mock_started = True
        self.observed_topology_deltas.add("provider_endpoint")
        marker = self.mock_output / "mock-ready.json"
        deadline = time.monotonic() + 10.0
        while time.monotonic() <= deadline:
            if marker.is_file() and not marker.is_symlink():
                value = json.loads(marker.read_bytes())
                if value == {"listener_ready": True}:
                    return
                raise LocalMockError("mock_readiness_invalid")
            state = _run(
                ["docker", "inspect", "--format", "{{.State.Running}}", self.mock_name]
            )
            if state.returncode != 0 or state.stdout.strip() != "true":
                raise LocalMockError("mock_provider_exited_before_ready")
            time.sleep(0.02)
        raise LocalMockError("mock_readiness_timeout")

    def _remove_mock(self) -> None:
        if not self.mock_started:
            return
        result = _run(["docker", "rm", "--force", self.mock_name])
        if result.returncode not in {0, 1}:
            raise LocalMockError("mock_cleanup_failed")
        self.mock_started = False

    def __call__(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        values = list(command)
        if values[:3] == ["docker", "network", "create"] and values[-1] == self.egress_network:
            original = list(values)
            if "--internal" not in values:
                values.insert(-1, "--internal")
            self._record_topology_command_delta(
                component="proxy_egress_network",
                original_commands=(original,),
                transformed_commands=(values,),
            )
            result = _run(values, timeout)
            if result.returncode == 0:
                inspect = _run(
                    ["docker", "network", "inspect", "--format", "{{.Internal}}", self.egress_network]
                )
                self.egress_internal = inspect.returncode == 0 and inspect.stdout.strip() == "true"
                if not self.egress_internal:
                    raise LocalMockError("mock_egress_not_internal")
                self.observed_topology_deltas.add("proxy_egress_network")
                self._start_mock()
            return result
        if values[:2] == ["docker", "create"] and any(
            item.startswith("phase2-openai-proxy-") for item in values
        ):
            original = list(values)
            values[-1:-1] = [
                "--mount",
                self._mount(
                    self.certificate,
                    "/etc/ssl/certs/ca-certificates.crt",
                ),
            ]
            self.observed_topology_deltas.add("proxy_trust_store")
            self._record_topology_command_delta(
                component="proxy_trust_store",
                original_commands=(original,),
                transformed_commands=(values,),
            )
        if values[:3] == ["docker", "network", "rm"] and values[-1] == self.egress_network:
            self._remove_mock()
        return _run(values, timeout)

    def cleanup(self) -> None:
        with contextlib.suppress(LocalMockError):
            self._remove_mock()
        for name in (
            f"phase2-canary-relay-worker-{self.request_id[:12]}",
            f"phase2-openai-proxy-{self.request_id[:12]}",
        ):
            _run(["docker", "rm", "--force", name])
        for network in (self.relay_network, self.egress_network):
            _run(["docker", "network", "rm", network])

    def residue(self) -> tuple[int, int]:
        containers = (
            self.mock_name,
            f"phase2-canary-relay-worker-{self.request_id[:12]}",
            f"phase2-openai-proxy-{self.request_id[:12]}",
        )
        networks = (self.relay_network, self.egress_network)
        container_count = sum(
            _run(["docker", "container", "inspect", name]).returncode == 0
            for name in containers
        )
        network_count = sum(
            _run(["docker", "network", "inspect", name]).returncode == 0
            for name in networks
        )
        return container_count, network_count


def _approved_artifacts(candidate_raw: bytes, candidate: dict[str, Any]) -> ApprovedCanaryArtifacts:
    identity = candidate["artifact_identity"]
    return ApprovedCanaryArtifacts(
        approval_candidate_sha256=hashlib.sha256(candidate_raw).hexdigest(),
        facts_sha256=identity["facts_sha256"],
        projection_sha256=identity["projection_sha256"],
        proxy_image_id=identity["proxy_image_id"],
        relay_image_id=identity["relay_image_id"],
        timeout_contract_sha256=identity["timeout_contract_sha256"],
        readiness_contract_sha256=identity["readiness_contract_sha256"],
        orchestrator_source_sha256=identity["orchestrator_source_sha256"],
    )


def _load_mock_approved_artifacts(
    *,
    candidate_path: Path,
    expected_candidate_sha256: str,
    approval_root: Path,
) -> ApprovedCanaryArtifacts:
    """Use the production approval loader with an ephemeral non-installed approval."""
    if _HEX_64.fullmatch(expected_candidate_sha256) is None:
        raise LocalMockError("external_candidate_sha256_invalid")
    candidate_raw = candidate_path.read_bytes()
    actual = hashlib.sha256(candidate_raw).hexdigest()
    if not hmac.compare_digest(actual, expected_candidate_sha256):
        raise LocalMockError("external_candidate_sha256_mismatch")
    approval_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    approval_root.chmod(0o700)
    approval_path = approval_root / "runtime-approval.json"
    approval = {
        "approved_candidate_sha256": expected_candidate_sha256,
        "approval_scope": "single_symbol_openai_canary_runtime_v2",
        "maximum_provider_attempts": 1,
        "provider": "openai",
        "retry_count": 0,
        "runtime_approval_schema_version": 2,
        "symbol": "000403.SZ",
    }
    _atomic_write(approval_path, canonical_json_bytes(approval))
    try:
        return load_installed_runtime_approval(
            candidate_path=candidate_path,
            approval_path=approval_path,
        )
    except (OSError, OrchestratorError, ValueError) as exc:
        raise LocalMockError("mock_runtime_approval_rejected") from exc


def _run_pre_fix_replay(repo_root: Path) -> dict[str, Any]:
    """Replay the frozen Relay ECONNREFUSED classification without network I/O."""
    candidate_path = (
        repo_root
        / "reports/phase2_provider_canary/superseded"
        / f"{_PREF_FIX_CANDIDATE_SHA256}.json"
    )
    candidate_raw = candidate_path.read_bytes()
    if hashlib.sha256(candidate_raw).hexdigest() != _PREF_FIX_CANDIDATE_SHA256:
        raise LocalMockError("pre_fix_candidate_binding_invalid")
    candidate = json.loads(candidate_raw)
    if (
        candidate.get("runtime_candidate_schema_version") != 1
        or candidate.get("inputs", {}).get("relay_source_sha256")
        != _PREF_FIX_RELAY_SHA256
        or "readiness_contract_sha256" in candidate.get("inputs", {})
    ):
        raise LocalMockError("pre_fix_candidate_contract_invalid")
    source_result = _run(
        [
            "git",
            "-C",
            str(repo_root),
            "show",
            f"{_PREF_FIX_RELAY_COMMIT}:{_PREF_FIX_RELAY_PATH}",
        ]
    )
    _require_success(source_result, "pre_fix_source_missing")
    source = source_result.stdout.encode("utf-8")
    if hashlib.sha256(source).hexdigest() != _PREF_FIX_RELAY_SHA256:
        raise LocalMockError("pre_fix_source_hash_mismatch")

    namespace: dict[str, Any] = {
        "__file__": f"{_PREF_FIX_RELAY_COMMIT}:{_PREF_FIX_RELAY_PATH}",
        "__name__": "phase2_pre_fix_relay_replay",
    }
    exec(compile(source, namespace["__file__"], "exec"), namespace)

    class RefusedConnection:
        sock = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def request(self, *_args: object, **_kwargs: object) -> None:
            raise ConnectionRefusedError(111, "synthetic local refusal")

        def close(self) -> None:
            pass

    fake_http = types.SimpleNamespace(
        client=types.SimpleNamespace(
            HTTPConnection=RefusedConnection,
            HTTPException=namespace["http"].client.HTTPException,
        )
    )
    namespace["http"] = fake_http
    observed_category: str | None = None
    proxy_request_count: int | None = None
    try:
        namespace["_default_requester"](b"{}", 75)
    except namespace["RelayError"] as exc:
        observed_category = exc.category
        proxy_request_count = exc.proxy_request_count
    if observed_category != "RELAY_TIMEOUT" or proxy_request_count != 1:
        raise LocalMockError("pre_fix_failure_not_reproduced")

    historical_path = (
        repo_root
        / "reports/phase2_provider_canary/live_canary/evidence"
        / "3d3e6adbe5b74c98ad18a2efe0fd1d0c.json"
    )
    if not historical_path.is_file() or historical_path.is_symlink():
        raise LocalMockError("pre_fix_historical_evidence_missing")
    historical_relative = str(historical_path.relative_to(repo_root))
    historical_sha256 = _sha256(historical_path)
    if not hmac.compare_digest(
        historical_sha256,
        _HISTORICAL_FROZEN_SHA256[historical_relative],
    ):
        raise LocalMockError("pre_fix_historical_evidence_mutated")
    return {
        "pre_fix_replay_schema_version": 1,
        "status": "PRE_FIX_FAILURE_REPRODUCED",
        "replay_mode": "DETERMINISTIC_LOCAL_FAULT_REPLAY",
        "superseded_candidate_sha256": _PREF_FIX_CANDIDATE_SHA256,
        "superseded_candidate_schema_version": 1,
        "superseded_candidate_has_readiness_contract": False,
        "relay_source_commit": _PREF_FIX_RELAY_COMMIT,
        "relay_source_path": _PREF_FIX_RELAY_PATH,
        "relay_source_sha256": _PREF_FIX_RELAY_SHA256,
        "injected_failure": "ConnectionRefusedError",
        "injected_errno": 111,
        "observed_old_error_category": observed_category,
        "expected_fixed_error_category": "RELAY_PROXY_CONNECT_FAILED",
        "proxy_request_count": proxy_request_count,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "public_network_request_count": 0,
        "historical_request_id": "3d3e6adbe5b74c98ad18a2efe0fd1d0c",
        "historical_evidence_sha256": historical_sha256,
        "historical_evidence_frozen_baseline_verified": True,
    }


def _run_once(
    *,
    index: int,
    repo_root: Path,
    root: Path,
    candidate_path: Path,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    certificate: Path,
    private_key: Path,
    response_path: Path,
    server_path: Path,
) -> dict[str, Any]:
    run_root = root / f"run-{index}"
    run_root.mkdir(mode=0o700)
    state_root = run_root / "state"
    work_root = run_root / "work"
    output_root = run_root / "output"
    for path in (state_root, work_root, output_root):
        path.mkdir(mode=0o700)
    request_id = f"{index:032x}"
    executor = LocalMockTopologyExecutor(
        request_id=request_id,
        root=run_root,
        certificate=certificate,
        private_key=private_key,
        response_path=response_path,
        server_path=server_path,
    )
    config = CanaryOrchestratorConfig(
        repo_root=repo_root,
        state_root=state_root,
        work_root=work_root,
        canary_output_root=output_root,
        facts_path=repo_root / "reports/phase2_facts/000403SZ_facts.json",
    )
    try:
        result = run_single_symbol_canary(
            config=config,
            artifacts=artifacts,
            projection=projection,
            backend=DockerCanaryBackend(executor=executor),
            artifact_verifier=lambda approved: verify_runtime_artifact_candidate(
                repo_root,
                candidate_path,
                expected_candidate_sha256=approved.approval_candidate_sha256,
            ),
            secret_reader=lambda: "phase2-local-mock-only",
            request_id_factory=lambda: request_id,
            wall_clock=_timestamp,
        )
        mock_receipt_path = executor.mock_output / "mock-receipt.json"
        mock_receipt = _optional_json(mock_receipt_path)
        archive = output_root / "receipts" / request_id
        proxy_receipt = _optional_json(archive / "proxy-receipt.json")
        relay_receipt = _optional_json(archive / "relay-receipt.json")
        document_path = output_root / "inbox" / f"{request_id}.json"
        markdown_path = output_root / "inbox" / f"{request_id}.md"
        provider_stages_complete = all(
            isinstance(proxy_receipt.get(event), dict)
            and proxy_receipt[event].get("occurred") is True
            for event in _PROVIDER_EVENTS
        )
        required_proxy_events = (
            isinstance(proxy_receipt.get("proxy_ready"), dict)
            and proxy_receipt["proxy_ready"].get("occurred") is True
            and isinstance(proxy_receipt.get("relay_request_received"), dict)
            and proxy_receipt["relay_request_received"].get("occurred") is True
        )
        topology_deltas_verified = _topology_deltas_verified(
            executor.observed_topology_deltas
        )
        topology_command_diffs_verified = _topology_command_diffs_verified(
            executor.topology_command_diffs
        )
        result_value = asdict(result)
        result_value["attempt_ledger_state"] = (
            result.attempt_ledger_state.value if result.attempt_ledger_state else None
        )
        return {
            "run": index,
            "request_id": request_id,
            "result": result_value,
            "mock_provider_attempt_count": mock_receipt.get("attempt_count"),
            "mock_request_contract_valid": mock_receipt.get("request_contract_valid"),
            "egress_network_internal": executor.egress_internal,
            "observed_topology_delta_components": sorted(
                executor.observed_topology_deltas
            ),
            "topology_deltas_verified": topology_deltas_verified,
            "topology_command_diffs": executor.topology_command_diffs,
            "topology_command_diffs_verified": (
                topology_command_diffs_verified
            ),
            "proxy_ready": required_proxy_events,
            "provider_stages_complete": provider_stages_complete,
            "proxy_terminal_status": proxy_receipt.get("terminal_status"),
            "proxy_provider_attempt_count": proxy_receipt.get(
                "provider_attempt_count"
            ),
            "proxy_retry_count": proxy_receipt.get("retry_count"),
            "relay_terminal_status": relay_receipt.get("terminal_status"),
            "relay_error_category": relay_receipt.get("error_category"),
            "relay_retry_count": relay_receipt.get("retry_count"),
            "proxy_receipt_durable": (archive / "proxy-receipt.json").is_file(),
            "relay_receipt_durable": (archive / "relay-receipt.json").is_file(),
            "candidate_valid": result.host_validation_status == "VALID",
            "claims_valid": result.host_validation_status == "VALID",
            "document_sha256": _optional_sha256(document_path),
            "renderer_sha256": _optional_sha256(markdown_path),
            "passed": (
                result.terminal_state == "SUCCEEDED"
                and result.route == "inbox"
                and result.host_validation_status == "VALID"
                and result.provider_http_status == 200
                and result.provider_attempt_count == 1
                and result.retry_count == 0
                and result.container_residue_count == 0
                and result.network_residue_count == 0
                and result.secret_file_residue_count == 0
                and result.temporary_file_residue_count == 0
                and result.receipt_archive_status == "ARCHIVED"
                and mock_receipt.get("attempt_count") == 1
                and mock_receipt.get("request_contract_valid") is True
                and mock_receipt.get("status") == 200
                and executor.egress_internal
                and topology_deltas_verified
                and topology_command_diffs_verified
                and required_proxy_events
                and provider_stages_complete
                and proxy_receipt.get("terminal_status") == "FORWARDED"
                and proxy_receipt.get("provider_attempt_count") == 1
                and proxy_receipt.get("retry_count") == 0
                and relay_receipt.get("terminal_status") == "RELAY_COMPLETED"
                and relay_receipt.get("error_category") is None
                and relay_receipt.get("retry_count") == 0
                and (archive / "proxy-receipt.json").is_file()
                and (archive / "relay-receipt.json").is_file()
                and document_path.is_file()
                and markdown_path.is_file()
            ),
        }
    finally:
        executor.cleanup()
        containers, networks = executor.residue()
        if containers or networks:
            raise LocalMockError("local_mock_cleanup_residue")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-candidate-sha256", required=True)
    args = parser.parse_args(argv)
    if _HEX_64.fullmatch(args.expected_candidate_sha256) is None:
        raise LocalMockError("external_candidate_sha256_invalid")
    repo_root = Path(__file__).resolve().parents[2]
    candidate_path = repo_root / "reports/phase2_provider_canary/runtime_contract_candidate.json"
    candidate_raw = candidate_path.read_bytes()
    candidate = json.loads(candidate_raw)
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    if not hmac.compare_digest(candidate_sha256, args.expected_candidate_sha256):
        raise LocalMockError("external_candidate_sha256_mismatch")
    verify_runtime_artifact_candidate(
        repo_root,
        candidate_path,
        expected_candidate_sha256=args.expected_candidate_sha256,
    )
    old_candidate_path = (
        repo_root
        / "reports/phase2_provider_canary/superseded"
        / "45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127.json"
    )
    if _sha256(old_candidate_path) != old_candidate_path.stem:
        raise LocalMockError("old_approval_not_preserved")
    facts_path = repo_root / "reports/phase2_facts/000403SZ_facts.json"
    facts_raw = facts_path.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_raw),
        hashlib.sha256(facts_raw).hexdigest(),
        facts_bytes=facts_raw,
    )
    if (
        projection["projection_sha256"]
        != candidate["artifact_identity"]["projection_sha256"]
    ):
        raise LocalMockError("projection_binding_invalid")
    historical_before = _historical_manifest(repo_root)
    pre_fix_replay = _run_pre_fix_replay(repo_root)
    pre_fix_output = (
        repo_root
        / "reports/phase2_provider_canary/local_path_pre_fix_replay.json"
    )
    _atomic_write(pre_fix_output, canonical_json_bytes(pre_fix_replay))

    with tempfile.TemporaryDirectory(prefix="phase2-local-path-mock-e2e-") as temporary:
        root = Path(temporary).resolve(strict=True)
        root.chmod(0o700)
        server_path = root / "mock_provider.py"
        response_path = root / "response.json"
        server_path.write_text(_MOCK_SERVER_SOURCE, encoding="utf-8")
        server_path.chmod(0o444)
        response_path.write_bytes(canonical_json_bytes(_provider_response(projection)))
        response_path.chmod(0o444)
        certificate, private_key = _generate_certificate(root)
        runs = []
        for index in range(1, 4):
            artifacts = _load_mock_approved_artifacts(
                candidate_path=candidate_path,
                expected_candidate_sha256=args.expected_candidate_sha256,
                approval_root=root / f"approval-{index}",
            )
            run = _run_once(
                index=index,
                repo_root=repo_root,
                root=root,
                candidate_path=candidate_path,
                artifacts=artifacts,
                projection=projection,
                certificate=certificate,
                private_key=private_key,
                response_path=response_path,
                server_path=server_path,
            )
            run["approval_loader"] = "APPROVED"
            run["external_candidate_sha256_bound"] = True
            runs.append(run)
            if not run["passed"]:
                break

    historical_after = _historical_manifest(repo_root)
    renderer_hashes = {run["renderer_sha256"] for run in runs}
    document_hashes = {run["document_sha256"] for run in runs}
    passed = (
        all(run["passed"] for run in runs)
        and all(run["approval_loader"] == "APPROVED" for run in runs)
        and len(renderer_hashes) == 1
        and len(document_hashes) == 1
        and historical_before == historical_after
    )
    evidence = {
        "local_path_mock_e2e_schema_version": 1,
        "status": "PASSED" if passed else "FAILED",
        "symbol": "000403.SZ",
        "trade_date": "2026-07-31",
        "approval_candidate_sha256": candidate_sha256,
        "expected_candidate_sha256": args.expected_candidate_sha256,
        "external_candidate_sha256_bound": True,
        "approval_loader": "APPROVED",
        "old_approval_candidate_sha256": old_candidate_path.stem,
        "old_approval_status": "INVALID_FOR_RUNTIME",
        "old_approval_preserved": True,
        "new_approval_installed": False,
        "mock_topology_deltas": list(_MOCK_TOPOLOGY_DELTAS),
        "mock_topology_delta_count": len(_MOCK_TOPOLOGY_DELTAS),
        "preserved_real_topology_identities": list(
            _PRESERVED_REAL_TOPOLOGY_IDENTITIES
        ),
        "pre_fix_replay_path": str(pre_fix_output.relative_to(repo_root)),
        "pre_fix_replay_sha256": _sha256(pre_fix_output),
        "pre_fix_failure_reproduced": (
            pre_fix_replay["status"] == "PRE_FIX_FAILURE_REPRODUCED"
        ),
        "requested_run_count": 3,
        "completed_run_count": len(runs),
        "runs": runs,
        "renderer_deterministic": len(renderer_hashes) == 1,
        "claims_document_deterministic": len(document_hashes) == 1,
        "historical_evidence_before": historical_before,
        "historical_evidence_after": historical_after,
        "historical_evidence_unchanged": historical_before == historical_after,
        "real_provider_attempt_count": 0,
        "real_ai_call_count": 0,
        "real_public_network_success_count": 0,
        "mock_provider_attempt_count": sum(
            int(run["mock_provider_attempt_count"] or 0) for run in runs
        ),
        "retry_count": 0,
        "container_residue_count": 0,
        "network_residue_count": 0,
        "temporary_residue_count": 0,
    }
    output = repo_root / "reports/phase2_provider_canary/local_path_mock_e2e.json"
    _atomic_write(output, canonical_json_bytes(evidence))
    print(json.dumps({"output": str(output), "status": evidence["status"]}))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
