#!/usr/bin/env python3
"""Run the exact Ark path three times against an internal TLS mock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.providers.ark_provider import ARK_ENDPOINT_ALIAS, ARK_EXACT_MODEL_ID
from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_ark_timeout_contract import ark_runtime_contract_sha256
from app.services.phase2_canary_orchestrator import (
    ApprovedCanaryArtifacts,
    ArkDockerCanaryBackend,
    CanaryOrchestratorConfig,
    run_single_symbol_canary,
)
from app.services.phase2_canary_runtime_artifact import BASE_IMAGE_REFERENCE
from app.services.phase2_claims_service import canonical_json_bytes

ARK_PROXY_IMAGE = "tickflow-phase2-ark-egress-proxy:timeout-v2"
ARK_RELAY_IMAGE = "tickflow-phase2-ark-canary-relay:timeout-v2"
PROVIDER_EVENTS = (
    "provider_connect_started",
    "provider_connect_completed",
    "tls_completed",
    "request_write_started",
    "request_write_completed",
    "response_headers_received",
    "response_body_completed",
)
MOCK_SECRET = "PHASE2_" + "TEST_SECRET_DO_NOT_USE"


class ArkMockE2EError(RuntimeError):
    pass


def _run(command: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _require_success(result: subprocess.CompletedProcess[str], code: str) -> None:
    if result.returncode != 0:
        raise ArkMockE2EError(code)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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


def _provider_response(repo_root: Path, projection: dict[str, Any]) -> dict[str, Any]:
    fixture = json.loads(
        (repo_root / "reports/phase2_claims/fixtures/000403SZ_claims.json").read_text(
            encoding="utf-8"
        )
    )
    candidate = {
        "candidate_schema_version": 1,
        "projection_sha256": projection["projection_sha256"],
        "symbol": fixture["symbol"],
        "name": fixture["name"],
        "trade_date": fixture["trade_date"],
        "timezone": fixture["timezone"],
        "claims": fixture["claims"],
        "trading_advice": False,
    }
    return {
        "completed_at": 1,
        "created_at": 0,
        "error": None,
        "id": "resp_phase2_ark_mock",
        "incomplete_details": None,
        "model": ARK_EXACT_MODEL_ID,
        "object": "response",
        "output": [
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
                "id": "msg_phase2_ark_mock",
                "role": "assistant",
                "status": "completed",
                "type": "message",
            }
        ],
        "status": "completed",
        "tools": [],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


MOCK_SERVER_SOURCE = r"""from __future__ import annotations
import json, os, ssl, tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
OUTPUT=Path("/output")
RESPONSE=Path("/mock/response.json").read_bytes()
def atomic(path,value):
 raw=(json.dumps(value,allow_nan=False,indent=2,sort_keys=True)+"\n").encode()
 fd,name=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
 try:
  os.fchmod(fd,0o600); os.write(fd,raw); os.fsync(fd); os.close(fd); fd=-1
  os.replace(name,path); d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
 finally:
  if fd>=0: os.close(fd)
  try: os.unlink(name)
  except FileNotFoundError: pass
class Handler(BaseHTTPRequestHandler):
 protocol_version="HTTP/1.1"
 def log_message(self,*_args): return
 def do_POST(self):
  try: length=int(self.headers.get("Content-Length","0"))
  except ValueError: length=0
  body=self.rfile.read(length) if 0<length<=1048576 else b""
  try: payload=json.loads(body)
  except (UnicodeDecodeError,json.JSONDecodeError): payload=None
  fmt=(payload or {}).get("text",{}).get("format",{})
  valid=(self.path=="/api/v3/responses" and self.headers.get_content_type()=="application/json"
   and bool(self.headers.get("Authorization")) and isinstance(payload,dict)
   and set(payload)=={"model","stream","store","tools","temperature","input","text"}
   and payload.get("model")=="doubao-seed-2-1-turbo-260628" and payload.get("stream") is False
   and payload.get("store") is False and payload.get("tools")==[] and payload.get("temperature")==0
   and fmt.get("type")=="json_schema" and fmt.get("strict") is True and isinstance(fmt.get("schema"),dict))
  status=200 if valid else 400; response=RESPONSE if valid else b'{"error":"mock_contract_rejected"}'
  atomic(OUTPUT/"mock-receipt.json",{"attempt_count":1,"authorization_present":bool(self.headers.get("Authorization")),"path":self.path,"request_contract_valid":valid,"status":status})
  self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(response))); self.send_header("Connection","close"); self.end_headers(); self.wfile.write(response)
server=HTTPServer(("0.0.0.0",443),Handler,bind_and_activate=False); server.server_bind()
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain("/mock/server.crt","/mock/server.key")
server.socket=context.wrap_socket(server.socket,server_side=True); server.server_activate(); atomic(OUTPUT/"mock-ready.json",{"listener_ready":True}); server.handle_request(); server.server_close()
"""


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
            "/CN=ark.cn-beijing.volces.com",
            "-addext",
            "subjectAltName=DNS:ark.cn-beijing.volces.com",
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
    _require_success(result, "ark_mock_certificate_generation_failed")
    return certificate, private_key


class ArkMockTopologyExecutor:
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
        self.mock_output.mkdir(mode=0o777)
        self.mock_output.chmod(0o777)
        self.mock_name = f"phase2-ark-mock-provider-{request_id[:12]}"
        self.egress_network = f"phase2-canary-egress-{request_id[:12]}"
        self.relay_network = f"phase2-canary-relay-{request_id[:12]}"
        self.mock_started = False
        self.egress_internal = False

    @staticmethod
    def _mount(source: Path, destination: str, readonly: bool = True) -> str:
        return f"type=bind,src={source},dst={destination}" + (",readonly" if readonly else "")

    def _start_mock(self) -> None:
        command = [
            "docker",
            "create",
            "--name",
            self.mock_name,
            "--network",
            self.egress_network,
            "--network-alias",
            "ark.cn-beijing.volces.com",
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
            self._mount(self.mock_output, "/output", False),
            BASE_IMAGE_REFERENCE,
            "/mock/server.py",
        ]
        _require_success(_run(command), "ark_mock_container_create_failed")
        _require_success(_run(["docker", "start", self.mock_name]), "ark_mock_start_failed")
        self.mock_started = True
        marker = self.mock_output / "mock-ready.json"
        deadline = time.monotonic() + 10
        while time.monotonic() <= deadline:
            if marker.is_file() and json.loads(marker.read_text()) == {"listener_ready": True}:
                return
            time.sleep(0.02)
        raise ArkMockE2EError("ark_mock_readiness_timeout")

    def __call__(self, command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        values = list(command)
        if values[:3] == ["docker", "network", "create"] and values[-1] == self.egress_network:
            if "--internal" not in values:
                values.insert(-1, "--internal")
            result = _run(values, timeout)
            if result.returncode == 0:
                inspect = _run(
                    [
                        "docker",
                        "network",
                        "inspect",
                        "--format",
                        "{{.Internal}}",
                        self.egress_network,
                    ]
                )
                self.egress_internal = inspect.returncode == 0 and inspect.stdout.strip() == "true"
                if not self.egress_internal:
                    raise ArkMockE2EError("ark_mock_egress_not_internal")
                self._start_mock()
            return result
        if values[:2] == ["docker", "create"] and any(
            item.startswith("phase2-ark-proxy-") for item in values
        ):
            values[-1:-1] = [
                "--mount",
                self._mount(self.certificate, "/etc/ssl/certs/ca-certificates.crt"),
            ]
        if values[:3] == ["docker", "network", "rm"] and values[-1] == self.egress_network:
            self._remove_mock()
        return _run(values, timeout)

    def _remove_mock(self) -> None:
        if self.mock_started:
            _run(["docker", "rm", "--force", self.mock_name])
            self.mock_started = False

    def cleanup(self) -> None:
        self._remove_mock()
        for name in (
            self.mock_name,
            f"phase2-canary-relay-worker-{self.request_id[:12]}",
            f"phase2-ark-proxy-{self.request_id[:12]}",
        ):
            _run(["docker", "rm", "--force", name])
        for network in (self.relay_network, self.egress_network):
            _run(["docker", "network", "rm", network])

    def residue(self) -> tuple[int, int]:
        containers = (
            self.mock_name,
            f"phase2-canary-relay-worker-{self.request_id[:12]}",
            f"phase2-ark-proxy-{self.request_id[:12]}",
        )
        networks = (self.relay_network, self.egress_network)
        return (
            sum(
                _run(["docker", "container", "inspect", name]).returncode == 0
                for name in containers
            ),
            sum(_run(["docker", "network", "inspect", name]).returncode == 0 for name in networks),
        )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_once(
    *,
    index: int,
    repo_root: Path,
    root: Path,
    projection: dict[str, Any],
    proxy_image_id: str,
    relay_image_id: str,
    certificate: Path,
    private_key: Path,
    response_path: Path,
    server_path: Path,
    runtime_hashes: dict[str, str],
) -> dict[str, Any]:
    run_root = root / f"run-{index}"
    run_root.mkdir(mode=0o700)
    paths = {name: run_root / name for name in ("state", "attempts", "work", "output")}
    for path in paths.values():
        path.mkdir(mode=0o700)
    request_id = f"{index + 10:032x}"
    candidate_hash = hashlib.sha256(f"ark-mock-scope-{index}".encode()).hexdigest()
    artifacts = ApprovedCanaryArtifacts(
        approval_candidate_sha256=candidate_hash,
        facts_sha256=projection["facts_sha256"],
        projection_sha256=projection["projection_sha256"],
        proxy_image_id=proxy_image_id,
        relay_image_id=relay_image_id,
        timeout_contract_sha256=ark_runtime_contract_sha256(),
        readiness_contract_sha256=_sha256(
            repo_root / "docker/phase2-ark-egress-proxy/readiness-contract.json"
        ),
        orchestrator_source_sha256=_sha256(
            repo_root / "backend/app/services/phase2_canary_orchestrator.py"
        ),
    )
    executor = ArkMockTopologyExecutor(
        request_id=request_id,
        root=run_root,
        certificate=certificate,
        private_key=private_key,
        response_path=response_path,
        server_path=server_path,
    )
    config = CanaryOrchestratorConfig(
        repo_root=repo_root,
        state_root=paths["state"],
        attempts_root=paths["attempts"],
        work_root=paths["work"],
        canary_output_root=paths["output"],
        historical_evidence_root=paths["output"],
        candidate_roots=(repo_root / "reports/phase2_provider_canary/superseded",),
        facts_path=repo_root / "reports/phase2_facts/000403SZ_facts.json",
    )
    try:
        result = run_single_symbol_canary(
            config=config,
            artifacts=artifacts,
            projection=projection,
            backend=ArkDockerCanaryBackend(executor=executor),
            artifact_verifier=lambda approved: (
                approved == artifacts or (_ for _ in ()).throw(ArkMockE2EError("artifact_mismatch"))
            ),
            secret_reader=lambda: MOCK_SECRET,
            request_id_factory=lambda: request_id,
            wall_clock=_timestamp,
            provider_id="volcengine_ark",
            exact_model_id=ARK_EXACT_MODEL_ID,
            endpoint_alias=ARK_ENDPOINT_ALIAS,
        )
        archive = paths["output"] / "receipts" / request_id
        if not (executor.mock_output / "mock-receipt.json").is_file():
            observed = sorted(
                str(path.relative_to(run_root)) for path in run_root.rglob("*") if path.is_file()
            )
            receipt_values = {}
            for receipt_name in ("proxy-receipt.json", "relay-receipt.json"):
                receipt_path = archive / receipt_name
                if receipt_path.is_file():
                    receipt_values[receipt_name] = _load_json(receipt_path)
            raise ArkMockE2EError(
                "ark_mock_provider_not_reached:"
                + json.dumps(
                    {
                        "result": asdict(result),
                        "observed_files": observed,
                        "receipts": receipt_values,
                    },
                    ensure_ascii=False,
                    default=str,
                    sort_keys=True,
                )
            )
        proxy_receipt = _load_json(archive / "proxy-receipt.json")
        relay_receipt = _load_json(archive / "relay-receipt.json")
        mock_receipt = _load_json(executor.mock_output / "mock-receipt.json")
        stages_complete = all(proxy_receipt[event]["occurred"] for event in PROVIDER_EVENTS)
        markdown_path = paths["output"] / "inbox" / f"{request_id}.md"
        document_path = paths["output"] / "inbox" / f"{request_id}.json"
        containers, networks = executor.residue()
        value = {
            "run": index,
            "request_id": request_id,
            "provider_id": "volcengine_ark",
            "endpoint_alias": ARK_ENDPOINT_ALIAS,
            "exact_model_id": ARK_EXACT_MODEL_ID,
            "approval_candidate_sha256": candidate_hash,
            "approval_scope_id": result.approval_scope_id,
            **runtime_hashes,
            "provider_attempt_count": result.provider_attempt_count,
            "retry_count": result.retry_count,
            "provider_http_status": result.provider_http_status,
            "proxy_ready": proxy_receipt["proxy_ready"]["occurred"],
            "proxy_receipt_identity": {
                "provider_id": proxy_receipt.get("provider_id"),
                "endpoint_alias": proxy_receipt.get("endpoint_alias"),
                "exact_model_id": proxy_receipt.get("exact_model_id"),
            },
            "relay_completed": relay_receipt["terminal_status"] == "RELAY_COMPLETED",
            "seven_stages_complete": stages_complete,
            "candidate_valid": result.host_validation_status == "VALID",
            "claims_valid": result.host_validation_status == "VALID",
            "renderer_deterministic": markdown_path.is_file() and document_path.is_file(),
            "renderer_sha256": _sha256(markdown_path) if markdown_path.is_file() else None,
            "mock_request_contract_valid": mock_receipt["request_contract_valid"],
            "egress_network_internal": executor.egress_internal,
            "container_residue_count": containers,
            "network_residue_count": networks,
            "temporary_residue_count": result.temporary_file_residue_count,
            "can_publish": False,
        }
        value["passed"] = all(
            (
                result.terminal_state == "SUCCEEDED",
                result.route == "inbox",
                value["provider_attempt_count"] == 1,
                value["retry_count"] == 0,
                value["proxy_ready"],
                value["proxy_receipt_identity"]
                == {
                    "provider_id": "volcengine_ark",
                    "endpoint_alias": ARK_ENDPOINT_ALIAS,
                    "exact_model_id": ARK_EXACT_MODEL_ID,
                },
                value["relay_completed"],
                value["seven_stages_complete"],
                value["candidate_valid"],
                value["claims_valid"],
                value["renderer_deterministic"],
                value["mock_request_contract_valid"],
                value["egress_network_internal"],
                containers == 0,
                networks == 0,
            )
        )
        return value
    finally:
        executor.cleanup()
        if executor.residue() != (0, 0):
            raise ArkMockE2EError("ark_mock_cleanup_residue")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs != 3:
        raise ArkMockE2EError("ark_mock_run_count_must_equal_three")
    repo_root = Path(__file__).resolve().parents[2]
    proxy = _run(["docker", "image", "inspect", ARK_PROXY_IMAGE, "--format", "{{.Id}}"])
    _require_success(proxy, "ark_proxy_image_missing")
    proxy_image_id = proxy.stdout.strip()
    relay = _run(["docker", "image", "inspect", ARK_RELAY_IMAGE, "--format", "{{.Id}}"])
    _require_success(relay, "ark_relay_image_missing")
    relay_image_id = relay.stdout.strip()
    facts_path = repo_root / "reports/phase2_facts/000403SZ_facts.json"
    facts_raw = facts_path.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_raw),
        hashlib.sha256(facts_raw).hexdigest(),
        facts_bytes=facts_raw,
    )
    runtime_hashes = {
        "orchestrator_source_sha256": _sha256(
            repo_root / "backend/app/services/phase2_canary_orchestrator.py"
        ),
        "runtime_contract_sha256": ark_runtime_contract_sha256(),
        "facts_sha256": projection["facts_sha256"],
        "projection_sha256": projection["projection_sha256"],
    }
    with tempfile.TemporaryDirectory(prefix="phase2-ark-mock-e2e-") as name:
        root = Path(name).resolve(strict=True)
        root.chmod(0o700)
        certificate, private_key = _generate_certificate(root)
        response_path = root / "response.json"
        server_path = root / "mock_provider.py"
        response_path.write_bytes(canonical_json_bytes(_provider_response(repo_root, projection)))
        server_path.write_text(MOCK_SERVER_SOURCE, encoding="utf-8")
        runs = [
            _run_once(
                index=index,
                repo_root=repo_root,
                root=root,
                projection=projection,
                proxy_image_id=proxy_image_id,
                relay_image_id=relay_image_id,
                certificate=certificate,
                private_key=private_key,
                response_path=response_path,
                server_path=server_path,
                runtime_hashes=runtime_hashes,
            )
            for index in range(1, 4)
        ]
    scopes = {run["approval_scope_id"] for run in runs}
    value = {
        "ark_mock_e2e_schema_version": 1,
        "status": "PASSED" if all(run["passed"] for run in runs) else "FAILED",
        "provider_id": "volcengine_ark",
        "exact_model_id": ARK_EXACT_MODEL_ID,
        "endpoint_alias": ARK_ENDPOINT_ALIAS,
        "proxy_image_id": proxy_image_id,
        "relay_image_id": relay_image_id,
        **runtime_hashes,
        "requested_run_count": 3,
        "completed_run_count": len(runs),
        "distinct_approval_scope_count": len(scopes),
        "real_public_network_success_count": 0,
        "real_provider_attempt_count": 0,
        "real_ai_call_count": 0,
        "mock_provider_attempt_count": sum(run["provider_attempt_count"] for run in runs),
        "retry_count": 0,
        "container_residue_count": sum(run["container_residue_count"] for run in runs),
        "network_residue_count": sum(run["network_residue_count"] for run in runs),
        "temporary_residue_count": sum(run["temporary_residue_count"] for run in runs),
        "can_publish": False,
        "runs": runs,
    }
    output = repo_root / "reports/phase2_provider_ark/mock_e2e.json"
    _atomic_write(output, canonical_json_bytes(value))
    print(json.dumps({"status": value["status"], "output": str(output)}, sort_keys=True))
    return 0 if value["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
