"""HTTP-free client for the internal Ark Probe/Proxy TLS parity harness."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

TARGET_HOST = "ark.cn-beijing.volces.com"
TARGET_PORT = 443
CA_PATH = Path("/etc/ssl/certs/ca-certificates.crt")


def _load(name: str, path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module_load_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _base_result(implementation: str) -> dict[str, Any]:
    return {
        "implementation": implementation,
        "target_host": TARGET_HOST,
        "target_port": TARGET_PORT,
        "ca_bundle_sha256": hashlib.sha256(CA_PATH.read_bytes()).hexdigest(),
        "category": "PROCESS_ERROR",
        "tls_version": None,
        "cipher_name": None,
        "sni_hostname": TARGET_HOST,
        "hostname_verification_target": TARGET_HOST,
        "exception_class": None,
        "verify_code": None,
        "verify_message": None,
        "errno": None,
        "secret_content_read": False,
        "authorization_constructed": False,
        "http_request_sent": False,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "real_public_network_success_count": 0,
    }


def run_proxy(timeout: float, host: str) -> dict[str, Any]:
    proxy = _load("ark_proxy_tls_parity_client", "/work/proxy.py")
    result = _base_result("ark_proxy")
    result["target_host"] = host
    result["sni_hostname"] = host
    result["hostname_verification_target"] = host
    context = proxy.create_tls_context()
    connection = None
    try:
        connection, metadata = proxy.connect_verified_tls(
            host,
            TARGET_PORT,
            timeout,
            context,
        )
        result.update(metadata)
        result["category"] = "PASSED"
    except proxy.ProxyError as error:
        result.update(
            category=error.category,
            exception_class=error.exception_class,
            verify_code=error.verify_code,
            verify_message=error.verify_message,
            errno=error.errno,
        )
    finally:
        if connection is not None:
            connection.close()
    return result


def run_probe(timeout: float) -> dict[str, Any]:
    probe = _load("ark_tls_probe_parity_client", "/work/probe.py")
    policy = probe.ProbePolicy(timeout, timeout, timeout)
    receipt = probe.execute_probe("f" * 32, policy=policy)
    result = _base_result("tls_probe")
    result.update(
        category=receipt["terminal_status"],
        tls_version=receipt["tls_version"],
        cipher_name=receipt["cipher_name"],
        verify_code=receipt["verify_code"],
        verify_message=receipt["verify_message"],
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--implementation", choices=("proxy", "probe"), required=True)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--host", default=TARGET_HOST)
    args = parser.parse_args()
    result = (
        run_proxy(args.timeout, args.host)
        if args.implementation == "proxy"
        else run_probe(args.timeout)
    )
    print(json.dumps(result, allow_nan=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
