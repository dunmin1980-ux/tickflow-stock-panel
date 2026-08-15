"""TLS-only runner that reuses the production Ark Proxy transport."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

TARGET_HOST = "ark.cn-beijing.volces.com"
TARGET_PORT = 443
CONNECT_TIMEOUT_SECONDS = 10.0
PROXY_SOURCE = Path("/proxy/proxy.py")
DEFAULT_OUTPUT = Path("/output/receipt.json")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def load_proxy_module(path: Path = PROXY_SOURCE) -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase2_ark_proxy_tls_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("proxy_module_load_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base_receipt(probe_id: str, started_at: str) -> dict[str, Any]:
    return {
        "ai_call_count": 0,
        "authorization_constructed": False,
        "cipher_name": None,
        "completed_at": started_at,
        "errno": None,
        "exception_class": None,
        "failure_phase": None,
        "hostname_verification_target": TARGET_HOST,
        "http_request_sent": False,
        "probe_id": probe_id,
        "provider_attempt_count": 0,
        "real_public_network_success_count": 0,
        "receipt_schema_version": 2,
        "secret_content_read": False,
        "sni_hostname": TARGET_HOST,
        "stages": {
            "certificate_verified": False,
            "clean_tls_close": False,
            "dns_completed": False,
            "hostname_verified": False,
            "tcp_connected": False,
            "tls_completed": False,
        },
        "started_at": started_at,
        "target_host": TARGET_HOST,
        "target_port": TARGET_PORT,
        "terminal_status": "DNS_RESOLUTION_FAILED",
        "tls_version": None,
        "verify_code": None,
        "verify_message": None,
    }


def execute_probe(
    probe_id: str,
    *,
    proxy_module: ModuleType | Any | None = None,
    resolver: Callable[..., Any] = socket.getaddrinfo,
    now: Callable[[], str] = _utc_now,
) -> dict[str, Any]:
    if len(probe_id) != 32 or any(character not in "0123456789abcdef" for character in probe_id):
        raise ValueError("probe_id_invalid")
    started_at = now()
    receipt = _base_receipt(probe_id, started_at)
    try:
        addresses = resolver(TARGET_HOST, TARGET_PORT, type=socket.SOCK_STREAM)
        if not addresses:
            raise OSError("empty_dns_result")
        receipt["stages"]["dns_completed"] = True
    except OSError as error:
        receipt["exception_class"] = type(error).__name__
        receipt["failure_phase"] = "dns"
        receipt["errno"] = getattr(error, "errno", None)
        receipt["completed_at"] = now()
        return receipt

    proxy = proxy_module or load_proxy_module()
    connection = None
    try:
        context = proxy.create_tls_context()
        connection, metadata = proxy.connect_verified_tls(
            TARGET_HOST,
            TARGET_PORT,
            CONNECT_TIMEOUT_SECONDS,
            context,
        )
        receipt.update(
            tls_version=metadata.get("tls_version"),
            cipher_name=metadata.get("cipher_name"),
            sni_hostname=metadata.get("sni_hostname"),
            hostname_verification_target=metadata.get(
                "hostname_verification_target"
            ),
        )
        receipt["stages"].update(
            tcp_connected=True,
            tls_completed=True,
            certificate_verified=True,
            hostname_verified=True,
        )
    except proxy.ProxyError as error:
        category = getattr(error, "category", "TLS_OTHER_SSL_ERROR")
        receipt.update(
            terminal_status=category,
            exception_class=getattr(error, "exception_class", type(error).__name__),
            verify_code=getattr(error, "verify_code", None),
            verify_message=getattr(error, "verify_message", None),
            errno=getattr(error, "errno", None),
        )
        if category.startswith("TLS_"):
            receipt["stages"]["tcp_connected"] = True
        receipt["failure_phase"] = (
            "tls_handshake" if category.startswith("TLS_") else "tcp"
        )
    if connection is not None:
        try:
            tls_socket = connection.sock
            if tls_socket is None or not hasattr(tls_socket, "unwrap"):
                raise RuntimeError("tls_socket_missing")
            tls_socket.settimeout(CONNECT_TIMEOUT_SECONDS)
            raw_socket = tls_socket.unwrap()
            connection.sock = None
            raw_socket.close()
            receipt["stages"]["clean_tls_close"] = True
            receipt["terminal_status"] = "PROBE_PASSED"
        except Exception as error:
            classification = proxy.classify_tls_error(error, phase="tls")
            receipt.update(
                terminal_status="TLS_CLOSE_FAILED",
                exception_class=classification.exception_class,
                failure_phase="tls_close",
                verify_code=None,
                verify_message=None,
                errno=classification.errno,
            )
        finally:
            connection.close()
    receipt["completed_at"] = now()
    return receipt


def atomic_write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".staging", dir=path.parent
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        raw = canonical_json_bytes(receipt)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-id", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    receipt = execute_probe(args.probe_id)
    atomic_write_receipt(args.output, receipt)
    print(receipt["terminal_status"])
    return 0 if receipt["terminal_status"] == "PROBE_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
