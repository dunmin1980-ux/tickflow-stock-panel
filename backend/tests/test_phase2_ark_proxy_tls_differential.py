from __future__ import annotations

import errno
import importlib.util
import json
import ssl
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.services import phase2_canary_orchestrator as orchestrator
from app.services.phase2_claims_service import canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY_PATH = REPO_ROOT / "docker/phase2-ark-egress-proxy/proxy.py"
HARNESS_PATH = REPO_ROOT / "backend/scripts/run_phase2_ark_proxy_tls_parity.py"
CLIENT_PATH = REPO_ROOT / "docker/phase2-ark-proxy-tls-mock/client.py"
SERVER_PATH = REPO_ROOT / "docker/phase2-ark-proxy-tls-mock/server.py"
RUNTIME_CONTRACT = {
    "canary_runtime_contract_version": 1,
    "cleanup_timeout_seconds": 15,
    "host_orchestrator_timeout_seconds": 210,
    "maximum_provider_attempts": 1,
    "provider_connect_timeout_seconds": 10,
    "provider_read_timeout_seconds": 180,
    "provider_total_timeout_seconds": 180,
    "relay_candidate_wait_timeout_seconds": 195,
    "retry_count": 0,
}


@pytest.fixture(scope="module")
def ark_proxy() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase2_ark_proxy_tls_differential_under_test",
        PROXY_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@pytest.fixture(scope="module")
def parity_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase2_ark_proxy_tls_parity_under_test",
        HARNESS_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _cert_error(message: str, *, verify_code: int) -> ssl.SSLCertVerificationError:
    error = ssl.SSLCertVerificationError(1, message)
    error.verify_code = verify_code
    error.verify_message = message
    return error


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (_cert_error("certificate verify failed", verify_code=20), "TLS_CERT_VERIFY_FAILED"),
        (_cert_error("hostname mismatch", verify_code=62), "TLS_HOSTNAME_VERIFY_FAILED"),
        (ssl.SSLError(1, "WRONG_VERSION_NUMBER"), "TLS_PROTOCOL_FAILED"),
        (ConnectionResetError(errno.ECONNRESET, "reset"), "TLS_CONNECTION_RESET"),
        (ssl.SSLEOFError(8, "unexpected eof"), "TLS_EOF"),
        (ssl.SSLError(1, "synthetic ssl failure"), "TLS_OTHER_SSL_ERROR"),
        (ConnectionRefusedError(errno.ECONNREFUSED, "refused"), "PROVIDER_CONNECT_FAILED"),
    ],
)
def test_classify_tls_error_is_precise_and_sanitized(
    ark_proxy: ModuleType,
    error: BaseException,
    category: str,
) -> None:
    result = ark_proxy.classify_tls_error(error)

    assert result.category == category
    assert result.exception_class == type(error).__name__
    assert result.errno is None or isinstance(result.errno, int)
    assert result.verify_code is None or isinstance(result.verify_code, int)
    assert result.verify_message is None or len(result.verify_message) <= 160
    serialized = json.dumps(result.as_dict(), sort_keys=True)
    assert "Authorization" not in serialized
    assert "Bearer " not in serialized


def test_timeout_classification_uses_the_measured_connect_phase(
    ark_proxy: ModuleType,
) -> None:
    timeout = TimeoutError("timed out")

    assert ark_proxy.classify_tls_error(timeout, phase="tcp").category == (
        "PROVIDER_CONNECT_FAILED"
    )
    assert ark_proxy.classify_tls_error(timeout, phase="tls").category == (
        "TLS_HANDSHAKE_TIMEOUT"
    )


def test_connection_reset_classification_uses_the_measured_connect_phase(
    ark_proxy: ModuleType,
) -> None:
    reset = ConnectionResetError(errno.ECONNRESET, "reset")

    assert ark_proxy.classify_tls_error(reset, phase="tcp").category == (
        "PROVIDER_CONNECT_FAILED"
    )
    assert ark_proxy.classify_tls_error(reset, phase="tls").category == (
        "TLS_CONNECTION_RESET"
    )


class _Socket:
    def version(self) -> str:
        return "TLSv1.3"

    def cipher(self) -> tuple[str, str, int]:
        return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)


class _Connection:
    def __init__(self, *, connect_error: BaseException | None = None) -> None:
        self.connect_error = connect_error
        self.sock: _Socket | None = None
        self.closed = False

    def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.sock = _Socket()

    def close(self) -> None:
        self.closed = True


def test_connect_verified_tls_returns_transport_metadata_without_http(
    ark_proxy: ModuleType,
) -> None:
    connection = _Connection()
    observed: dict[str, Any] = {}

    def factory(host: str, port: int, timeout: float, context: ssl.SSLContext):
        observed.update(host=host, port=port, timeout=timeout, context=context)
        return connection

    result, metadata = ark_proxy.connect_verified_tls(
        "ark.internal.test",
        443,
        10.0,
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        connection_factory=factory,
    )

    assert result is connection
    assert observed["host"] == "ark.internal.test"
    assert observed["port"] == 443
    assert metadata == {
        "tls_version": "TLSv1.3",
        "cipher_name": "TLS_AES_256_GCM_SHA384",
        "sni_hostname": "ark.internal.test",
        "hostname_verification_target": "ark.internal.test",
    }


def test_connect_verified_tls_raises_precise_error_and_closes_connection(
    ark_proxy: ModuleType,
) -> None:
    failure = _cert_error("hostname mismatch", verify_code=62)
    connection = _Connection(connect_error=failure)

    with pytest.raises(ark_proxy.ProxyError) as raised:
        ark_proxy.connect_verified_tls(
            "ark.internal.test",
            443,
            10.0,
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            connection_factory=lambda *_args: connection,
        )

    assert raised.value.category == "TLS_HOSTNAME_VERIFY_FAILED"
    assert raised.value.exception_class == "SSLCertVerificationError"
    assert raised.value.verify_code == 62
    assert connection.closed is True


def test_provider_request_propagates_precise_tls_category_without_retry(
    ark_proxy: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = _cert_error("certificate verify failed", verify_code=20)
    connection = _Connection(connect_error=failure)
    monkeypatch.setattr(
        ark_proxy,
        "create_tls_context",
        lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )

    with pytest.raises(ark_proxy.ProxyError) as raised:
        ark_proxy.perform_provider_request(
            b"{}",
            "synthetic-test-only",
            connection_factory=lambda *_args: connection,
            runtime_contract=RUNTIME_CONTRACT,
        )

    assert raised.value.category == "TLS_CERT_VERIFY_FAILED"
    assert raised.value.provider_attempt_count == 1
    assert raised.value.exception_class == "SSLCertVerificationError"
    assert connection.closed is True


def test_ark_v3_receipt_persists_sanitized_tls_diagnostics(
    ark_proxy: ModuleType,
) -> None:
    receipt = ark_proxy.build_receipt(
        request_id="a" * 32,
        response_category="TLS_CERT_VERIFY_FAILED",
        terminal_status="TLS_CERT_VERIFY_FAILED",
        provider_attempt_count=1,
        exception_class="SSLCertVerificationError",
        verify_code=20,
        verify_message="unable to get local issuer certificate",
        errno=1,
    )

    assert receipt["receipt_schema_version"] == 3
    assert receipt["exception_class"] == "SSLCertVerificationError"
    assert receipt["verify_code"] == 20
    assert receipt["verify_message"] == "unable to get local issuer certificate"
    assert receipt["errno"] == 1
    orchestrator._parse_stage_receipt_bytes(
        canonical_json_bytes(receipt),
        component="proxy",
        request_id="a" * 32,
        expected_proxy_identity=orchestrator._ARK_PROXY_RECEIPT_IDENTITY,
    )


def test_historical_ark_v2_receipt_remains_accepted_byte_for_byte() -> None:
    path = (
        REPO_ROOT
        / "reports/phase2_provider_ark/live_canary/receipts/"
        "d57b276fe9014d35bdae5a92950ca17b/proxy-receipt.json"
    )
    before = path.read_bytes()

    orchestrator._parse_stage_receipt_bytes(
        before,
        component="proxy",
        request_id="d57b276fe9014d35bdae5a92950ca17b",
        expected_proxy_identity=orchestrator._ARK_PROXY_RECEIPT_IDENTITY,
    )

    assert path.read_bytes() == before


def test_proxy_source_keeps_verified_tls_and_does_not_read_proxy_environment() -> None:
    source = PROXY_PATH.read_text(encoding="utf-8")

    assert "context.check_hostname = True" in source
    assert "context.verify_mode = ssl.CERT_REQUIRED" in source
    assert "ssl.CERT_NONE" not in source
    assert "check_hostname = False" not in source
    assert "urllib.request" not in source
    assert "getproxies" not in source


def test_parity_harness_network_is_internal_only(parity_harness: ModuleType) -> None:
    command = parity_harness.network_create_command("phase2-ark-tls-test")

    assert command == [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        "phase2-ark-tls-test",
    ]


def test_parity_harness_rejects_incomplete_case_results(
    parity_harness: ModuleType,
) -> None:
    with pytest.raises(ValueError, match="parity_result_invalid"):
        parity_harness.validate_case_results(
            {
                "trusted_proxy": {"category": "PASSED"},
            }
        )


def test_parity_harness_counts_runtime_residue(
    parity_harness: ModuleType,
) -> None:
    def runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        output = (
            "phase2-ark-tls-mock-leftover\nunrelated\n"
            if command[1:3] == ["container", "ls"]
            else "phase2-ark-tls-parity-leftover\nbridge\n"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    assert parity_harness.runtime_residue_counts(run=runner) == {
        "container_count": 1,
        "network_count": 1,
    }


def test_parity_harness_rejects_unmeasured_or_wrong_sni(
    parity_harness: ModuleType,
) -> None:
    report = json.loads(
        (
            REPO_ROOT
            / "reports/phase2_provider_ark/proxy_tls_differential/local_parity.json"
        ).read_text(encoding="utf-8")
    )
    cases = report["cases"]
    cases["trusted_proxy"]["server_observed_sni"] = None

    with pytest.raises(ValueError, match="parity_result_invalid"):
        parity_harness.validate_case_results(cases)


def test_cleanup_verifier_fails_closed_on_residue(
    parity_harness: ModuleType,
) -> None:
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1:3] == ["container", "ls"]:
            output = "phase2-ark-tls-mock-leftover\n"
        elif command[1:3] == ["network", "ls"]:
            output = "phase2-ark-tls-parity-leftover\n"
        else:
            output = ""
        return subprocess.CompletedProcess(command, 1 if "rm" in command else 0, output, "")

    with pytest.raises(RuntimeError, match="parity_runtime_residue"):
        parity_harness.cleanup_and_verify("mock", "network", run=runner)

    assert [command[1:3] for command in commands[:2]] == [
        ["rm", "--force"],
        ["network", "rm"],
    ]


def test_parity_harness_source_cannot_read_secrets_or_send_http() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (HARNESS_PATH, CLIENT_PATH, SERVER_PATH)
    )

    assert "perform_provider_request" not in sources
    assert "read_auth_file" not in sources
    assert "Authorization" not in sources
    assert "Bearer " not in sources
    assert "http_request_sent" in sources
    assert "real_public_network_success_count" in sources
    assert "--internal" in HARNESS_PATH.read_text(encoding="utf-8")
