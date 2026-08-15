from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / "docker/phase2-ark-proxy-tls-probe/contracts"
CONTRACT_PATHS = {
    "probe": CONTRACT_ROOT / "probe-contract.json",
    "runtime": CONTRACT_ROOT / "runtime-contract.json",
    "readiness": CONTRACT_ROOT / "readiness-contract.json",
    "internal_network": CONTRACT_ROOT / "internal-network-contract.json",
    "egress_network": CONTRACT_ROOT / "egress-network-contract.json",
    "attachment": CONTRACT_ROOT / "proxy-network-attachment-contract.json",
    "egress_policy": CONTRACT_ROOT / "egress-policy.json",
    "dns": CONTRACT_ROOT / "dns-configuration-contract.json",
    "receipt": CONTRACT_ROOT / "receipt.schema.json",
}
RUNNER_PATH = REPO_ROOT / "docker/phase2-ark-proxy-tls-probe/probe.py"
LAUNCHER_PATH = REPO_ROOT / "backend/scripts/run_phase2_ark_proxy_tls_probe.py"
BUILDER_PATH = (
    REPO_ROOT / "backend/scripts/build_phase2_ark_proxy_tls_probe_offline.py"
)
HARNESS_PATH = (
    REPO_ROOT / "backend/scripts/run_phase2_ark_proxy_tls_probe_mock_e2e.py"
)
MOCK_SERVER_PATH = REPO_ROOT / "docker/phase2-ark-proxy-tls-mock/server.py"


def _read_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"required contract is missing: {path}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required implementation is missing: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def contracts() -> dict[str, dict[str, Any]]:
    return {name: _read_json(path) for name, path in CONTRACT_PATHS.items()}


def test_all_production_proxy_tls_probe_contracts_are_source_controlled() -> None:
    assert all(path.is_file() for path in CONTRACT_PATHS.values())


def test_probe_contract_is_tls_only_and_fixed(
    contracts: dict[str, dict[str, Any]],
) -> None:
    contract = contracts["probe"]
    assert contract["probe_contract_version"] == 1
    assert contract["scope_type"] == "production_proxy_path_tls_only_probe"
    assert contract["target_host"] == "ark.cn-beijing.volces.com"
    assert contract["target_port"] == 443
    assert contract["sni_hostname"] == "ark.cn-beijing.volces.com"
    assert contract["hostname_verification_target"] == (
        "ark.cn-beijing.volces.com"
    )
    assert contract["ca_source"] == "/etc/ssl/certs/ca-certificates.crt"
    assert contract["tls_minimum"] == "TLSv1_2"
    assert contract["verify_mode"] == "CERT_REQUIRED"
    assert contract["check_hostname"] is True
    assert contract["retry_count"] == 0
    assert contract["maximum_probe_attempts"] == 1
    assert contract["allowed_operations"] == [
        "dns_resolution",
        "tcp_connect",
        "tls_handshake",
        "certificate_verification",
        "hostname_verification",
        "clean_tls_close",
    ]
    assert contract["forbidden_operations"] == [
        "secret_read",
        "authorization",
        "http_get",
        "http_head",
        "http_post",
        "ark_responses_api",
        "provider_request",
        "ai_call",
    ]


def test_runtime_contract_preserves_timeout_order_and_zero_retry(
    contracts: dict[str, dict[str, Any]],
) -> None:
    runtime = contracts["runtime"]
    assert runtime == {
        "cleanup_timeout_seconds": 15,
        "host_orchestrator_timeout_seconds": 210,
        "maximum_probe_attempts": 1,
        "provider_connect_timeout_seconds": 10,
        "provider_read_timeout_seconds": 180,
        "provider_total_timeout_seconds": 180,
        "relay_candidate_wait_timeout_seconds": 195,
        "retry_count": 0,
        "runtime_contract_schema_version": 1,
    }
    assert 180 < 195 < 210


def test_double_network_contract_matches_production_attachment(
    contracts: dict[str, dict[str, Any]],
) -> None:
    internal = contracts["internal_network"]
    egress = contracts["egress_network"]
    attachment = contracts["attachment"]
    policy = contracts["egress_policy"]
    dns = contracts["dns"]

    assert internal == {
        "driver": "bridge",
        "internal": True,
        "network_contract_schema_version": 1,
        "role": "relay_internal",
    }
    assert egress["driver"] == "bridge"
    assert egress["internal"] is False
    assert egress["mock_internal_override"] is True
    assert egress["role"] == "provider_egress"
    assert attachment["create_attached_role"] == "relay_internal"
    assert attachment["connect_before_start_role"] == "provider_egress"
    assert attachment["required_network_count"] == 2
    assert attachment["host_network_allowed"] is False
    assert policy["egress_owner"] == "proxy_container_only"
    assert policy["fixed_target"] == "ark.cn-beijing.volces.com:443"
    assert policy["relay_network_has_public_route"] is False
    assert dns["docker_embedded_dns"] is True
    assert dns["custom_dns_servers"] == []
    assert dns["proxy_environment_allowed"] is False


def test_readiness_and_receipt_contracts_are_closed_and_zero_activity(
    contracts: dict[str, dict[str, Any]],
) -> None:
    readiness = contracts["readiness"]
    receipt = contracts["receipt"]
    assert readiness["receipt_path"] == "/output/receipt.json"
    assert readiness["approval_required_for_live"] is True
    assert readiness["scope_historical_attempts"] == 0
    assert readiness["scope_availability"] == "AVAILABLE"
    assert readiness["approval_installed_during_preparation"] is False

    assert receipt["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert receipt["additionalProperties"] is False
    assert receipt["required"] == sorted(receipt["properties"])
    properties = receipt["properties"]
    assert properties["provider_attempt_count"] == {"const": 0}
    assert properties["ai_call_count"] == {"const": 0}
    assert properties["secret_content_read"] == {"const": False}
    assert properties["authorization_constructed"] == {"const": False}
    assert properties["http_request_sent"] == {"const": False}
    assert properties["real_public_network_success_count"] == {"const": 0}
    assert properties["receipt_schema_version"] == {"const": 2}
    assert properties["failure_phase"]["type"] == ["string", "null"]
    assert "TLS_CLOSE_FAILED" in properties["terminal_status"]["enum"]
    assert "TLS_FAILED" not in properties["terminal_status"]["enum"]


def test_runner_uses_only_production_tls_connect_and_closes() -> None:
    runner = _load_module(RUNNER_PATH, "phase2_proxy_tls_probe_runner_test")
    calls: list[str] = []

    class RawSocket:
        def close(self) -> None:
            calls.append("raw_close")

    class TlsSocket:
        def settimeout(self, timeout: float) -> None:
            assert timeout == 10
            calls.append("settimeout")

        def unwrap(self) -> RawSocket:
            calls.append("unwrap")
            return RawSocket()

    class Connection:
        sock: TlsSocket | None = TlsSocket()

        def close(self) -> None:
            calls.append("connection_close")

    class ProxyError(RuntimeError):
        pass

    class ProxyModule:
        @staticmethod
        def create_tls_context() -> object:
            calls.append("create_tls_context")
            return object()

        @staticmethod
        def connect_verified_tls(
            host: str,
            port: int,
            timeout: float,
            context: object,
        ) -> tuple[Connection, dict[str, str]]:
            assert host == "ark.cn-beijing.volces.com"
            assert port == 443
            assert timeout == 10
            assert context is not None
            calls.append("connect_verified_tls")
            return Connection(), {
                "tls_version": "TLSv1.3",
                "cipher_name": "TLS_AES_256_GCM_SHA384",
                "sni_hostname": host,
                "hostname_verification_target": host,
            }

        pass

    ProxyModule.ProxyError = ProxyError

    receipt = runner.execute_probe(
        "a" * 32,
        proxy_module=ProxyModule,
        resolver=lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("127.0.0.1", 443))
        ],
        now=lambda: "2026-08-15T12:00:00Z",
    )

    assert calls == [
        "create_tls_context",
        "connect_verified_tls",
        "settimeout",
        "unwrap",
        "raw_close",
        "connection_close",
    ]
    assert receipt["terminal_status"] == "PROBE_PASSED"
    assert receipt["failure_phase"] is None
    assert all(receipt["stages"].values())
    assert receipt["provider_attempt_count"] == 0
    assert receipt["ai_call_count"] == 0
    assert receipt["secret_content_read"] is False
    assert receipt["authorization_constructed"] is False
    assert receipt["http_request_sent"] is False


def test_runner_rejects_failed_orderly_tls_shutdown() -> None:
    runner = _load_module(RUNNER_PATH, "phase2_proxy_tls_probe_close_failure")

    class Classification:
        category = "TLS_CERT_VERIFY_FAILED"
        exception_class = "SSLEOFError"
        verify_code = None
        verify_message = None
        errno = None

    class TlsSocket:
        def settimeout(self, _timeout: float) -> None:
            pass

        def unwrap(self) -> None:
            raise EOFError("missing close notify")

    class Connection:
        sock: TlsSocket | None = TlsSocket()

        def close(self) -> None:
            self.sock = None

    class ProxyError(RuntimeError):
        pass

    class ProxyModule:
        @staticmethod
        def create_tls_context() -> object:
            return object()

        @staticmethod
        def connect_verified_tls(*_args: Any) -> tuple[Connection, dict[str, str]]:
            return Connection(), {
                "tls_version": "TLSv1.3",
                "cipher_name": "TLS_AES_256_GCM_SHA384",
                "sni_hostname": "ark.cn-beijing.volces.com",
                "hostname_verification_target": "ark.cn-beijing.volces.com",
            }

        @staticmethod
        def classify_tls_error(_error: BaseException, *, phase: str) -> Classification:
            assert phase == "tls"
            return Classification()

    ProxyModule.ProxyError = ProxyError

    receipt = runner.execute_probe(
        "c" * 32,
        proxy_module=ProxyModule,
        resolver=lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
        now=lambda: "2026-08-15T12:00:00Z",
    )

    assert receipt["terminal_status"] == "TLS_CLOSE_FAILED"
    assert receipt["failure_phase"] == "tls_close"
    assert receipt["stages"] == {
        "certificate_verified": True,
        "clean_tls_close": False,
        "dns_completed": True,
        "hostname_verified": True,
        "tcp_connected": True,
        "tls_completed": True,
    }


def test_runner_propagates_precise_tls_failure_without_activity() -> None:
    runner = _load_module(RUNNER_PATH, "phase2_proxy_tls_probe_runner_failure_test")

    class ProxyError(RuntimeError):
        def __init__(self) -> None:
            self.category = "TLS_CERT_VERIFY_FAILED"
            self.exception_class = "SSLCertVerificationError"
            self.verify_code = 20
            self.verify_message = "unable to get local issuer certificate"
            self.errno = 1

    class ProxyModule:
        @staticmethod
        def create_tls_context() -> object:
            return object()

        @staticmethod
        def connect_verified_tls(*_args: Any) -> None:
            raise ProxyError()

        pass

    ProxyModule.ProxyError = ProxyError

    receipt = runner.execute_probe(
        "b" * 32,
        proxy_module=ProxyModule,
        resolver=lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("127.0.0.1", 443))
        ],
        now=lambda: "2026-08-15T12:00:00Z",
    )

    assert receipt["terminal_status"] == "TLS_CERT_VERIFY_FAILED"
    assert receipt["failure_phase"] == "tls_handshake"
    assert receipt["exception_class"] == "SSLCertVerificationError"
    assert receipt["verify_code"] == 20
    assert receipt["provider_attempt_count"] == 0
    assert receipt["http_request_sent"] is False


def test_runner_source_has_no_secret_or_http_call_path() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert "create_tls_context" in source
    assert "connect_verified_tls" in source
    assert "read_auth_file" not in source
    assert "perform_provider_request" not in source
    assert "Authorization" not in source
    assert "http.client" not in source


def test_launcher_builds_exact_production_double_network_commands() -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_launcher_test")
    names = launcher.runtime_names("c" * 32)
    commands = launcher.network_create_commands(names, mock_mode=False)
    assert commands == [
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
            names["egress_network"],
        ],
    ]
    mock_commands = launcher.network_create_commands(names, mock_mode=True)
    assert "--internal" in mock_commands[0]
    assert "--internal" in mock_commands[1]


def test_launcher_proxy_command_has_no_secret_or_business_input(
    tmp_path: Path,
) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_command_test")
    names = launcher.runtime_names("d" * 32)
    command = launcher.proxy_create_command(
        names=names,
        image_id="sha256:" + "1" * 64,
        output_dir=tmp_path,
        probe_id="d" * 32,
    )
    flattened = " ".join(command)
    assert command[:3] == ["docker", "container", "create"]
    assert command[command.index("--network") + 1] == names["relay_network"]
    assert "/run/phase2/provider-auth" not in flattened
    assert "/input/request.json" not in flattened
    assert "/input/projection.json" not in flattened
    assert "Authorization" not in flattened
    assert "--entrypoint /usr/bin/python3" in flattened
    assert str(RUNNER_PATH) not in flattened
    assert "src=" + str(RUNNER_PATH) not in flattened
    assert "/probe/probe.py" in command


def test_launcher_attachment_verifier_requires_exact_two_networks() -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_network_test")
    names = launcher.runtime_names("e" * 32)
    valid = [
        {
            "NetworkSettings": {
                "Networks": {
                    names["relay_network"]: {"NetworkID": "relay-id"},
                    names["egress_network"]: {"NetworkID": "egress-id"},
                }
            }
        }
    ]
    launcher.validate_proxy_attachments(valid, names)
    valid[0]["NetworkSettings"]["Networks"].pop(names["egress_network"])
    with pytest.raises(ValueError, match="proxy_network_attachment_invalid"):
        launcher.validate_proxy_attachments(valid, names)


def test_launcher_never_auto_retries_failed_docker_command() -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_retry_test")
    calls: list[list[str]] = []

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "failed")

    with pytest.raises(RuntimeError, match="docker_command_failed"):
        launcher.run_once(["docker", "version"], run=run)
    assert calls == [["docker", "version"]]


def test_live_launcher_stops_before_docker_without_local_approval(
    tmp_path: Path,
) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_no_approval")
    calls: list[list[str]] = []

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises((OSError, ValueError)):
        launcher.run_live_probe(
            candidate_path=tmp_path / "candidate.json",
            scope_path=tmp_path / "scope.json",
            approval_path=tmp_path / "approval.json",
            attempts_root=tmp_path / "attempts",
            runner_path=RUNNER_PATH,
            run=run,
        )
    assert calls == []


def test_production_network_validator_requires_internal_relay_and_public_egress() -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_prod_network")
    names = launcher.runtime_names("f" * 32)
    valid = {
        names["relay_network"]: {"Driver": "bridge", "Internal": True},
        names["egress_network"]: {"Driver": "bridge", "Internal": False},
    }
    launcher.validate_production_networks(valid, names)
    valid[names["egress_network"]]["Internal"] = True
    with pytest.raises(ValueError, match="production_network_contract_invalid"):
        launcher.validate_production_networks(valid, names)


def _valid_probe_receipt(probe_id: str = "f" * 32) -> dict[str, Any]:
    return {
        "ai_call_count": 0,
        "authorization_constructed": False,
        "cipher_name": "TLS_AES_256_GCM_SHA384",
        "completed_at": "2026-08-15T12:00:01Z",
        "errno": None,
        "exception_class": None,
        "failure_phase": None,
        "hostname_verification_target": "ark.cn-beijing.volces.com",
        "http_request_sent": False,
        "probe_id": probe_id,
        "provider_attempt_count": 0,
        "real_public_network_success_count": 0,
        "receipt_schema_version": 2,
        "secret_content_read": False,
        "sni_hostname": "ark.cn-beijing.volces.com",
        "stages": {
            "certificate_verified": True,
            "clean_tls_close": True,
            "dns_completed": True,
            "hostname_verified": True,
            "tcp_connected": True,
            "tls_completed": True,
        },
        "started_at": "2026-08-15T12:00:00Z",
        "target_host": "ark.cn-beijing.volces.com",
        "target_port": 443,
        "terminal_status": "PROBE_PASSED",
        "tls_version": "TLSv1.3",
        "verify_code": None,
        "verify_message": None,
    }


def test_launcher_receipt_validator_rejects_any_external_activity() -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_receipt")
    receipt = _valid_probe_receipt()
    launcher.validate_probe_receipt(receipt, probe_id="f" * 32, container_exit_code=0)
    receipt["provider_attempt_count"] = 1
    with pytest.raises(ValueError, match="probe_receipt_invalid"):
        launcher.validate_probe_receipt(
            receipt,
            probe_id="f" * 32,
            container_exit_code=0,
        )


@pytest.mark.parametrize(
    ("mutation", "exit_code"),
    [
        ({"completed_at": "2026-08-15T11:59:59Z"}, 0),
        ({"exception_class": 7}, 0),
        ({"verify_code": True}, 0),
        ({"failure_phase": "tls_handshake"}, 0),
        ({"terminal_status": "PROBE_PASSED"}, 2),
    ],
)
def test_receipt_validator_rejects_bad_types_timestamps_and_exit_mismatch(
    mutation: dict[str, Any],
    exit_code: int,
) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_receipt_types")
    receipt = _valid_probe_receipt()
    receipt.update(mutation)
    with pytest.raises(ValueError, match="probe_receipt_invalid"):
        launcher.validate_probe_receipt(
            receipt,
            probe_id="f" * 32,
            container_exit_code=exit_code,
        )


def test_receipt_validator_enforces_failure_stage_semantics() -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_receipt_stages")
    receipt = _valid_probe_receipt()
    receipt.update(
        terminal_status="DNS_RESOLUTION_FAILED",
        failure_phase="dns",
        exception_class="gaierror",
        errno=-2,
        tls_version=None,
        cipher_name=None,
    )
    receipt["stages"] = dict.fromkeys(receipt["stages"], False)
    launcher.validate_probe_receipt(receipt, probe_id="f" * 32, container_exit_code=2)
    receipt["stages"]["tcp_connected"] = True
    with pytest.raises(ValueError, match="probe_receipt_invalid"):
        launcher.validate_probe_receipt(
            receipt,
            probe_id="f" * 32,
            container_exit_code=2,
        )

    certificate_failure = _valid_probe_receipt()
    certificate_failure.update(
        terminal_status="TLS_CERT_VERIFY_FAILED",
        failure_phase="tls_handshake",
        exception_class="SSLCertVerificationError",
        verify_code=20,
        verify_message="unable to get local issuer certificate",
        tls_version=None,
        cipher_name=None,
    )
    certificate_failure["stages"] = dict.fromkeys(
        certificate_failure["stages"], False
    )
    certificate_failure["stages"].update(dns_completed=True, tcp_connected=True)
    launcher.validate_probe_receipt(
        certificate_failure,
        probe_id="f" * 32,
        container_exit_code=2,
    )
    certificate_failure.update(
        exception_class="ConnectionResetError",
        verify_code=None,
        verify_message=None,
        errno=104,
    )
    with pytest.raises(ValueError, match="probe_receipt_invalid"):
        launcher.validate_probe_receipt(
            certificate_failure,
            probe_id="f" * 32,
            container_exit_code=2,
        )

    close_failure = _valid_probe_receipt()
    close_failure.update(
        terminal_status="TLS_CLOSE_FAILED",
        failure_phase="tls_close",
        exception_class="SSLEOFError",
    )
    close_failure["stages"]["clean_tls_close"] = False
    launcher.validate_probe_receipt(
        close_failure,
        probe_id="f" * 32,
        container_exit_code=2,
    )
    close_failure["terminal_status"] = "TLS_CERT_VERIFY_FAILED"
    with pytest.raises(ValueError, match="probe_receipt_invalid"):
        launcher.validate_probe_receipt(
            close_failure,
            probe_id="f" * 32,
            container_exit_code=2,
        )


@pytest.mark.parametrize(
    ("terminal_status", "failure_phase", "exception_class", "error_number"),
    [
        ("DNS_RESOLUTION_FAILED", "dns", "ConnectionRefusedError", 111),
        ("DNS_RESOLUTION_FAILED", "dns", "gaierror", 111),
        ("PROVIDER_CONNECT_FAILED", "tcp", "ConnectionRefusedError", 104),
        ("PROVIDER_CONNECT_FAILED", "tcp", "SSLError", None),
    ],
)
def test_receipt_validator_rejects_contradictory_dns_and_tcp_diagnostics(
    terminal_status: str,
    failure_phase: str,
    exception_class: str,
    error_number: int | None,
) -> None:
    launcher = _load_module(
        LAUNCHER_PATH,
        "phase2_proxy_tls_probe_receipt_transport_diagnostics",
    )
    receipt = _valid_probe_receipt()
    receipt.update(
        terminal_status=terminal_status,
        failure_phase=failure_phase,
        exception_class=exception_class,
        errno=error_number,
        tls_version=None,
        cipher_name=None,
    )
    receipt["stages"] = dict.fromkeys(receipt["stages"], False)
    if failure_phase == "tcp":
        receipt["stages"]["dns_completed"] = True

    with pytest.raises(ValueError, match="probe_receipt_invalid"):
        launcher.validate_probe_receipt(
            receipt,
            probe_id="f" * 32,
            container_exit_code=2,
        )


@pytest.mark.parametrize(
    ("terminal_status", "failure_phase", "exception_class", "error_number"),
    [
        ("DNS_RESOLUTION_FAILED", "dns", "gaierror", -2),
        ("DNS_RESOLUTION_FAILED", "dns", "OSError", None),
        ("PROVIDER_CONNECT_FAILED", "tcp", "ConnectionRefusedError", 111),
        ("PROVIDER_CONNECT_FAILED", "tcp", "TimeoutError", None),
        ("PROVIDER_CONNECT_FAILED", "tcp", "OSError", 101),
        ("PROVIDER_CONNECT_FAILED", "tcp", "MissingConnectedSocket", None),
    ],
)
def test_receipt_validator_accepts_bound_dns_and_tcp_diagnostics(
    terminal_status: str,
    failure_phase: str,
    exception_class: str,
    error_number: int | None,
) -> None:
    launcher = _load_module(
        LAUNCHER_PATH,
        "phase2_proxy_tls_probe_receipt_valid_transport_diagnostics",
    )
    receipt = _valid_probe_receipt()
    receipt.update(
        terminal_status=terminal_status,
        failure_phase=failure_phase,
        exception_class=exception_class,
        errno=error_number,
        tls_version=None,
        cipher_name=None,
    )
    receipt["stages"] = dict.fromkeys(receipt["stages"], False)
    if failure_phase == "tcp":
        receipt["stages"]["dns_completed"] = True

    launcher.validate_probe_receipt(
        receipt,
        probe_id="f" * 32,
        container_exit_code=2,
    )


def test_runtime_bindings_reject_head_and_source_drift(tmp_path: Path) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_bindings")
    launcher_source = tmp_path / "launcher.py"
    runner_source = tmp_path / "probe.py"
    launcher_source.write_text("launcher-v1\n", encoding="utf-8")
    runner_source.write_text("runner-v1\n", encoding="utf-8")
    candidate = {
        "current_git_head": "1" * 40,
        "source_bindings": {
            "host_launcher_source": {
                "path": "launcher.py",
                "sha256": launcher.sha256_bytes(launcher_source.read_bytes()),
            },
            "container_runner_source": {
                "path": "probe.py",
                "sha256": launcher.sha256_bytes(runner_source.read_bytes()),
            },
        },
    }
    launcher.validate_runtime_source_bindings(
        candidate,
        repo_root=tmp_path,
        runner_path=runner_source,
        launcher_path=launcher_source,
        git_head_loader=lambda _root: "1" * 40,
    )
    runner_source.write_text("runner-v2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="runtime_source_binding_invalid"):
        launcher.validate_runtime_source_bindings(
            candidate,
            repo_root=tmp_path,
            runner_path=runner_source,
            launcher_path=launcher_source,
            git_head_loader=lambda _root: "1" * 40,
        )
    with pytest.raises(ValueError, match="runtime_source_binding_invalid"):
        launcher.validate_runtime_source_bindings(
            candidate,
            repo_root=tmp_path,
            runner_path=runner_source,
            launcher_path=launcher_source,
            git_head_loader=lambda _root: "2" * 40,
        )


def test_execution_identity_requires_exact_candidate_scope_and_local_approval(
    tmp_path: Path,
) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_identity_test")
    candidate = {
        "approval_candidate_schema_version": 1,
        "candidate_status": "PROXY_TLS_PROBE_CANDIDATE_READY_FOR_REVIEW",
        "scope_type": "production_proxy_path_tls_only_probe",
        "current_git_head": "1" * 40,
        "target_host": "ark.cn-beijing.volces.com",
        "target_port": 443,
        "retry_count": 0,
        "maximum_probe_attempts": 1,
        "source_bindings": {},
    }
    candidate_path = tmp_path / "approval_candidate.json"
    candidate_raw = launcher.canonical_json_bytes(candidate)
    candidate_path.write_bytes(candidate_raw)
    candidate_sha = launcher.sha256_bytes(candidate_raw)
    scope = {
        "approval_scope_schema_version": 1,
        "approval_candidate_sha256": candidate_sha,
        "approval_scope_id": launcher.scope_id_for_candidate(candidate_sha),
        "attempt_availability": "AVAILABLE",
        "historical_attempts": 0,
        "maximum_probe_attempts": 1,
        "provider_scope_shared": False,
        "retry_count": 0,
        "scope_type": "production_proxy_path_tls_only_probe",
    }
    scope_path = tmp_path / "approval_scope.json"
    scope_path.write_bytes(launcher.canonical_json_bytes(scope))
    approval = {
        "approval_candidate_sha256": candidate_sha,
        "approval_scope_id": scope["approval_scope_id"],
        "approval_status": "APPROVED",
        "runtime_approval_schema_version": 1,
    }
    approval_path = tmp_path / "runtime-approval.json"
    approval_path.write_bytes(launcher.canonical_json_bytes(approval))
    approval_path.chmod(0o600)

    identity = launcher.load_execution_identity(
        candidate_path=candidate_path,
        scope_path=scope_path,
        approval_path=approval_path,
        verify_runtime_bindings=lambda _candidate: None,
    )
    assert identity["approval_candidate_sha256"] == candidate_sha
    assert identity["approval_scope_id"] == scope["approval_scope_id"]
    assert identity["historical_attempts"] == 0
    assert identity["attempt_availability"] == "AVAILABLE"

    approval_path.chmod(0o644)
    with pytest.raises(ValueError, match="runtime_approval_invalid"):
        launcher.load_execution_identity(
            candidate_path=candidate_path,
            scope_path=scope_path,
            approval_path=approval_path,
            verify_runtime_bindings=lambda _candidate: None,
        )


def test_cleanup_attempts_all_resources_and_requires_verified_absence() -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_cleanup")
    names = launcher.runtime_names("9" * 32)
    calls: list[list[str]] = []

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["docker", "container", "ls"]:
            return subprocess.CompletedProcess(command, 0, names["proxy"] + "\n", "")
        if command[:3] == ["docker", "network", "ls"]:
            return subprocess.CompletedProcess(
                command,
                0,
                names["egress_network"] + "\n" + names["relay_network"] + "\n",
                "",
            )
        if command[:4] == ["docker", "container", "rm", "--force"]:
            return subprocess.CompletedProcess(command, 1, "", "remove failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = launcher._cleanup_runtime(names, run=run)
    assert result["status"] == "FAILED"
    assert result["container_residue"] == [names["proxy"]]
    assert any(command[:4] == ["docker", "container", "rm", "--force"] for command in calls)
    assert result["cleanup_errors"] == ["container_remove_nonzero"]


def test_cleanup_of_absent_resources_is_success_without_remove_commands() -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_cleanup_absent")
    names = launcher.runtime_names("8" * 32)
    calls: list[list[str]] = []

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    result = launcher._cleanup_runtime(names, run=run)
    assert result == {
        "cleanup_errors": [],
        "container_residue": [],
        "network_residue": [],
        "status": "PASSED",
    }
    assert not any("rm" in command for command in calls)


def test_cleanup_state_is_persisted_and_failure_rejects_terminal_success(
    tmp_path: Path,
) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_cleanup_ledger")
    reservation = launcher.reserve_scope(
        attempts_root=tmp_path,
        scope_id="a" * 64,
        probe_id="b" * 32,
    )
    launcher.mark_dispatch_started(
        reservation["ledger_path"],
        started_at="2026-08-15T12:00:00Z",
    )
    launcher._mark_terminal(
        reservation["ledger_path"],
        receipt=_valid_probe_receipt("b" * 32),
    )
    result = launcher._mark_cleanup(
        reservation["ledger_path"],
        cleanup={
            "cleanup_errors": ["remove_nonzero"],
            "status": "FAILED",
            "container_residue": ["leftover"],
            "network_residue": [],
        },
    )
    assert result["state"] == "TERMINAL_CLEANUP_FAILED"
    assert result["cleanup_status"] == "FAILED"
    assert result["cleanup_errors"] == ["remove_nonzero"]


def test_pre_dispatch_cleanup_failure_preserves_zero_attempt_ledger(
    tmp_path: Path,
) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_predispatch_cleanup")
    reservation = launcher.reserve_scope(
        attempts_root=tmp_path,
        scope_id="c" * 64,
        probe_id="d" * 32,
    )
    result = launcher._mark_cleanup(
        reservation["ledger_path"],
        cleanup={
            "cleanup_errors": ["network_verification_nonzero"],
            "status": "FAILED",
            "container_residue": [],
            "network_residue": ["leftover-network"],
        },
    )
    assert result["state"] == "PRE_DISPATCH_CLEANUP_FAILED"
    assert result["probe_attempt_count"] == 0
    assert reservation["ledger_path"].is_file()


@pytest.mark.parametrize(
    ("terminal_status", "expected_exit"),
    [("PROBE_PASSED", 0), ("TLS_CERT_VERIFY_FAILED", 2)],
)
def test_launcher_main_exit_code_matches_probe_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    terminal_status: str,
    expected_exit: int,
) -> None:
    launcher = _load_module(LAUNCHER_PATH, f"phase2_proxy_tls_probe_main_{expected_exit}")
    monkeypatch.setattr(launcher, "LOCK_PATH", tmp_path / "runtime.lock")
    monkeypatch.setattr(
        launcher,
        "run_live_probe",
        lambda: {"terminal_status": terminal_status},
    )
    monkeypatch.setattr(sys, "argv", ["launcher", "--live"])
    assert launcher.main() == expected_exit


def test_scope_reservation_and_dispatch_ledger_are_one_shot(tmp_path: Path) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_ledger_test")
    scope_id = "2" * 64
    probe_id = "3" * 32
    reservation = launcher.reserve_scope(
        attempts_root=tmp_path,
        scope_id=scope_id,
        probe_id=probe_id,
    )
    ledger_path = reservation["ledger_path"]
    ledger = launcher.read_canonical_json(ledger_path)
    assert ledger["state"] == "RESERVED_BEFORE_NETWORK"
    assert ledger["probe_attempt_count"] == 0
    assert ledger["provider_attempt_count"] == 0
    assert ledger["ai_call_count"] == 0

    dispatched = launcher.mark_dispatch_started(
        ledger_path,
        started_at="2026-08-15T12:00:00Z",
    )
    assert dispatched["state"] == "NETWORK_DISPATCH_STARTED"
    assert dispatched["probe_attempt_count"] == 1
    assert dispatched["retry_count"] == 0
    assert dispatched["provider_attempt_count"] == 0
    assert dispatched["ai_call_count"] == 0

    with pytest.raises(ValueError, match="probe_scope_attempt_unavailable"):
        launcher.reserve_scope(
            attempts_root=tmp_path,
            scope_id=scope_id,
            probe_id="4" * 32,
        )


def test_pre_dispatch_failure_rolls_back_without_consuming_scope(
    tmp_path: Path,
) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_rollback")
    scope_id = "5" * 64
    first = launcher.reserve_scope(
        attempts_root=tmp_path,
        scope_id=scope_id,
        probe_id="6" * 32,
    )
    launcher.rollback_reservation_before_dispatch(
        attempts_root=tmp_path,
        scope_id=scope_id,
        reservation=first,
    )
    assert not (tmp_path / scope_id).exists()
    second = launcher.reserve_scope(
        attempts_root=tmp_path,
        scope_id=scope_id,
        probe_id="7" * 32,
    )
    assert launcher.read_canonical_json(second["ledger_path"], mode=0o600)[
        "probe_attempt_count"
    ] == 0


def test_exclusive_lock_rejects_concurrent_entry(tmp_path: Path) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_lock")
    lock = tmp_path / "runtime.lock"
    with (
        launcher.exclusive_lock(lock),
        pytest.raises(ValueError, match="probe_runtime_locked"),
        launcher.exclusive_lock(lock),
    ):
        raise AssertionError("second lock holder entered")


def test_ledger_publication_is_regular_mode_0600(tmp_path: Path) -> None:
    launcher = _load_module(LAUNCHER_PATH, "phase2_proxy_tls_probe_mode_test")
    path = tmp_path / "ledger.json"
    launcher.atomic_write_json(path, {"state": "SAFE"})
    metadata = path.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert not path.is_symlink()
    assert launcher.read_canonical_json(path) == {"state": "SAFE"}


def test_offline_image_build_command_is_networkless_pull_free_and_uncached(
    tmp_path: Path,
) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_command")
    command = builder.strict_build_command(
        tag="tickflow:test",
        context=tmp_path,
        build_arguments={"B": "2", "A": "1"},
    )
    assert command[:9] == [
        "docker",
        "build",
        "--network",
        "none",
        "--pull=false",
        "--no-cache",
        "--tag",
        "tickflow:test",
        "--build-arg",
    ]
    assert command[-1] == str(tmp_path)
    assert command.count("--build-arg") == 2
    assert "A=1" in command
    assert "B=2" in command


def test_backend_failure_baseline_uses_exact_junit_node_ids(tmp_path: Path) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_junit")
    junit = tmp_path / "baseline.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="3" failures="2" errors="0" skipped="0">
  <testsuite name="pytest" tests="3" failures="2" errors="0" skipped="0">
    <testcase classname="tests.test_ark_contract_artifact" name="test_identity"><failure>fail</failure></testcase>
    <testcase classname="tests.test_gold_shadow_store" name="test_store"><failure>fail</failure></testcase>
    <testcase classname="tests.test_ok" name="test_pass" />
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
    baseline = builder.build_backend_failure_baseline(
        junit,
        source_git_head="1" * 40,
        expected_passed=1,
        expected_failed=2,
    )
    assert baseline["failed_node_ids"] == [
        "tests/test_ark_contract_artifact.py::test_identity",
        "tests/test_gold_shadow_store.py::test_store",
    ]
    assert baseline["failure_set_sha256"] == hashlib.sha256(
        builder.canonical_json_bytes(baseline["failed_node_ids"])
    ).hexdigest()
    assert baseline["classification_counts"] == {
        "expected_immutable_identity_fail_closed": 1,
        "pre_existing_gold_debt": 1,
        "pre_existing_launcher_debt": 0,
    }


def test_backend_postcheck_requires_exact_frozen_failure_set(tmp_path: Path) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_postcheck")
    baseline_path = tmp_path / "baseline.json"
    builder.atomic_write_json(
        baseline_path,
        {
            "classification_counts": {
                "expected_immutable_identity_fail_closed": 1,
                "pre_existing_gold_debt": 1,
                "pre_existing_launcher_debt": 0,
            },
            "failed": 2,
            "failed_node_ids": [
                "tests/test_ark_contract_artifact.py::test_identity",
                "tests/test_gold_shadow_store.py::test_store",
            ],
            "failure_set_sha256": hashlib.sha256(
                builder.canonical_json_bytes(
                    [
                        "tests/test_ark_contract_artifact.py::test_identity",
                        "tests/test_gold_shadow_store.py::test_store",
                    ]
                )
            ).hexdigest(),
            "passed": 1,
            "source_git_head": "1" * 40,
        },
    )
    post = tmp_path / "post.xml"
    post.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="4" failures="2" errors="0" skipped="0">
  <testsuite name="pytest" tests="4" failures="2" errors="0" skipped="0">
    <testcase classname="tests.test_gold_shadow_store" name="test_store"><failure>fail</failure></testcase>
    <testcase classname="tests.test_new" name="test_new_pass" />
    <testcase classname="tests.test_ark_contract_artifact" name="test_identity"><failure>fail</failure></testcase>
    <testcase classname="tests.test_ok" name="test_pass" />
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
    result = builder.build_backend_failure_postcheck(
        baseline_path,
        post,
        source_git_head="2" * 40,
    )
    assert result["status"] == "KNOWN_BACKEND_FAILURE_SET_UNCHANGED"
    assert result["passed"] == 2
    assert result["failed"] == 2
    assert result["failure_set_sha256"] == builder.read_canonical_json(
        baseline_path
    )["failure_set_sha256"]

    post.write_text(
        post.read_text(encoding="utf-8").replace(
            'classname="tests.test_new" name="test_new_pass" /',
            'classname="tests.test_new" name="test_new_pass"><failure>new</failure></testcase',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="backend_failure_set_changed"):
        builder.build_backend_failure_postcheck(
            baseline_path,
            post,
            source_git_head="2" * 40,
        )


def test_candidate_binds_complete_proxy_topology_and_historical_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_candidate")
    bindings = {
        key: {"path": f"bound/{key}", "sha256": "a" * 64}
        for key in builder.REQUIRED_SOURCE_BINDING_KEYS
    }
    monkeypatch.setattr(builder, "source_bindings", lambda _root: bindings)
    image_id = "sha256:" + "a" * 64
    provenance_sha = "b" * 64
    candidate = builder.build_candidate(
        REPO_ROOT,
        current_git_head="1" * 40,
        proxy_image_id=image_id,
        build_provenance_sha256=provenance_sha,
        ca_bundle_sha256="c" * 64,
    )
    required = {
        "current_git_head",
        "probe_contract_version",
        "proxy_source_sha256",
        "orchestrator_source_sha256",
        "proxy_dockerfile_sha256",
        "proxy_image_id",
        "base_image_digest",
        "build_provenance_sha256",
        "ca_bundle_sha256",
        "tls_contract_sha256",
        "readiness_contract_sha256",
        "internal_network_contract_sha256",
        "egress_network_contract_sha256",
        "proxy_network_attachment_contract_sha256",
        "egress_policy_sha256",
        "dns_configuration_identity",
        "historical_tls_probe_sha256",
        "proxy_tls_differential_evidence_sha256",
        "local_proxy_parity_evidence_sha256",
    }
    assert required <= set(candidate)
    assert candidate["proxy_image_id"] == image_id
    assert candidate["build_provenance_sha256"] == provenance_sha
    assert candidate["target_host"] == "ark.cn-beijing.volces.com"
    assert candidate["target_port"] == 443
    assert candidate["retry_count"] == 0
    assert candidate["maximum_probe_attempts"] == 1
    assert set(candidate["source_bindings"]) == set(
        builder.REQUIRED_SOURCE_BINDING_KEYS
    )
    assert {
        "mock_harness_source",
        "mock_server_source",
        "local_parity_harness_source",
    } <= set(candidate["source_bindings"])


def test_new_probe_scope_is_separate_available_and_zero_attempts(
    tmp_path: Path,
) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_scope")
    candidate_raw = builder.canonical_json_bytes({"candidate": "fixed"})
    scope = builder.build_scope(candidate_raw, attempts_root=tmp_path)
    assert scope["scope_type"] == "production_proxy_path_tls_only_probe"
    assert scope["historical_attempts"] == 0
    assert scope["attempt_availability"] == "AVAILABLE"
    assert scope["retry_count"] == 0
    assert scope["maximum_probe_attempts"] == 1
    assert scope["provider_scope_shared"] is False
    assert scope["ai_canary_scope_shared"] is False
    assert scope["generic_tls_probe_scope_shared"] is False
    assert scope["openai_scope_shared"] is False


def test_build_arguments_bind_exact_proxy_context(
    tmp_path: Path,
) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_args")
    files = {
        "Dockerfile": b"FROM fixed\n",
        "probe.py": b"print('probe')\n",
        "proxy.py": b"print('proxy')\n",
        "responses-contract.json": b"{}\n",
        "runtime-contract.json": b"{\"runtime\":1}\n",
        "readiness-contract.json": b"{\"ready\":1}\n",
        "secret.placeholder": b"placeholder",
        "receipt.placeholder.json": b"{}\n",
    }
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    hashes = builder.context_hashes(tmp_path)
    arguments = builder.build_arguments_for_context(
        hashes,
        proxy_policy_sha256="f" * 64,
    )
    assert arguments == {
        "BASE_IMAGE_DIGEST": builder.BASE_IMAGE_DIGEST,
        "PROXY_DOCKERFILE_SHA256": hashlib.sha256(files["Dockerfile"]).hexdigest(),
        "PROBE_RUNNER_SHA256": hashlib.sha256(files["probe.py"]).hexdigest(),
        "PROXY_POLICY_SHA256": "f" * 64,
        "PROXY_SOURCE_SHA256": hashlib.sha256(files["proxy.py"]).hexdigest(),
        "READINESS_CONTRACT_SHA256": hashlib.sha256(
            files["readiness-contract.json"]
        ).hexdigest(),
        "RESPONSES_CONTRACT_SHA256": hashlib.sha256(
            files["responses-contract.json"]
        ).hexdigest(),
        "RUNTIME_CONTRACT_SHA256": hashlib.sha256(
            files["runtime-contract.json"]
        ).hexdigest(),
    }


def test_image_identity_requires_exact_build_labels() -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_image")
    arguments = {
        "BASE_IMAGE_DIGEST": builder.BASE_IMAGE_DIGEST,
        "PROXY_DOCKERFILE_SHA256": "1" * 64,
        "PROBE_RUNNER_SHA256": "2" * 64,
        "PROXY_POLICY_SHA256": "3" * 64,
        "PROXY_SOURCE_SHA256": "4" * 64,
        "READINESS_CONTRACT_SHA256": "5" * 64,
        "RESPONSES_CONTRACT_SHA256": "6" * 64,
        "RUNTIME_CONTRACT_SHA256": "7" * 64,
    }
    labels = builder.expected_image_labels(arguments)
    payload = [
        {
            "Architecture": "amd64",
            "Config": {"Labels": labels, "User": "65532:65532"},
            "Id": "sha256:" + "a" * 64,
            "Os": "linux",
        }
    ]
    identity = builder.validate_image_inspect(payload, expected_labels=labels)
    assert identity["image_id"] == "sha256:" + "a" * 64
    assert identity["user"] == "65532:65532"
    payload[0]["Config"]["Labels"] = {**labels, "unexpected": "value"}
    with pytest.raises(ValueError, match="proxy_image_labels_mismatch"):
        builder.validate_image_inspect(payload, expected_labels=labels)


def test_build_provenance_records_offline_rebuild_and_runtime_identity() -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_provenance")
    provenance = builder.build_provenance(
        source_git_head="1" * 40,
        context_hashes={"Dockerfile": "2" * 64, "proxy.py": "3" * 64},
        build_arguments={"BASE_IMAGE_DIGEST": builder.BASE_IMAGE_DIGEST},
        image_identity={
            "architecture": "amd64",
            "image_id": "sha256:" + "4" * 64,
            "labels": {},
            "os": "linux",
            "user": "65532:65532",
        },
        ca_identity={
            "path": "/etc/ssl/certs/ca-certificates.crt",
            "sha256": "5" * 64,
            "size_bytes": 123,
        },
        runtime_identity={
            "openssl_version": "OpenSSL 3.0.19 27 Jan 2026",
            "python_version": "3.11.2",
            "uid": 65532,
            "gid": 65532,
        },
    )
    assert provenance["build_network"] == "none"
    assert provenance["pull"] is False
    assert provenance["no_cache"] is True
    assert provenance["real_public_network_success_count"] == 0
    assert provenance["provider_attempt_count"] == 0
    assert provenance["ai_call_count"] == 0
    assert provenance["secret_content_read"] is False
    assert provenance["image_identity"]["image_id"] == "sha256:" + "4" * 64


def test_base_image_inspection_requires_exact_local_digest() -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_base")
    payload = [
        {
            "Architecture": "amd64",
            "Id": builder.BASE_IMAGE_DIGEST,
            "Os": "linux",
        }
    ]

    def run_ok(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        assert command == ["docker", "image", "inspect", builder.BASE_IMAGE_REFERENCE]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    assert builder.inspect_local_base(run=run_ok)["Id"] == builder.BASE_IMAGE_DIGEST
    payload[0]["Id"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="offline_base_image_missing"):
        builder.inspect_local_base(run=run_ok)


def test_readonly_context_copy_rejects_symlink_and_preserves_hashes(
    tmp_path: Path,
) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_copy")
    source = tmp_path / "source"
    source.mkdir()
    (source / "proxy.py").write_text("print('ok')\n", encoding="utf-8")
    target = tmp_path / "target"
    hashes = builder.copy_readonly_context(source, target)
    assert hashes == builder.context_hashes(source) == builder.context_hashes(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o500
    assert stat.S_IMODE((target / "proxy.py").stat().st_mode) == 0o400

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "source.py").write_text("ok\n", encoding="utf-8")
    (bad / "link.py").symlink_to(bad / "source.py")
    with pytest.raises(ValueError, match="proxy_build_context_invalid"):
        builder.copy_readonly_context(bad, tmp_path / "bad-target")


def test_probe_image_dockerfile_packages_runner_without_runtime_bind_mount() -> None:
    dockerfile = REPO_ROOT / "docker/phase2-ark-proxy-tls-probe/Dockerfile"
    source = dockerfile.read_text(encoding="utf-8")
    assert "COPY --chown=65532:65532 probe.py /probe/probe.py" in source
    assert 'ENTRYPOINT ["/usr/bin/python3", "/probe/probe.py"]' in source


def test_offline_artifact_publication_is_atomic_mode_0600(tmp_path: Path) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_atomic")
    path = tmp_path / "artifact.json"
    builder.atomic_write_json(path, {"status": "READY"})
    assert builder.read_canonical_json(path) == {"status": "READY"}
    metadata = path.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert not list(tmp_path.glob("*.staging"))


def test_prepare_and_verify_artifacts_are_deterministic_and_zero_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_prepare")
    bindings = {
        key: {"path": f"bound/{key}", "sha256": "a" * 64}
        for key in builder.REQUIRED_SOURCE_BINDING_KEYS
    }
    monkeypatch.setattr(builder, "source_bindings", lambda _root: bindings)
    output = tmp_path / "output"
    output.mkdir()
    head = "1" * 40
    image_id = "sha256:" + "2" * 64
    build_provenance = {
        "ai_call_count": 0,
        "build_network": "none",
        "image_identity": {"image_id": image_id},
        "no_cache": True,
        "provider_attempt_count": 0,
        "pull": False,
        "real_public_network_success_count": 0,
        "secret_content_read": False,
        "source_git_head": head,
        "ca_bundle_identity": {"sha256": "3" * 64},
    }
    mock = {
        "ai_call_count": 0,
        "authorization_constructed": False,
        "double_network_topology": "VERIFIED",
        "happy_path_runs": 3,
        "happy_path_status": "PASSED",
        "http_request_sent": False,
        "mock_networks_internal": True,
        "production_topology_mock": "PASSED",
        "provider_attempt_count": 0,
        "proxy_image_id": image_id,
        "real_public_network_success_count": 0,
        "residue": {"container_count": 0, "network_count": 0},
        "secret_content_read": False,
        "status": "PRODUCTION_PROXY_TLS_PROBE_MOCK_PASSED",
    }
    baseline = {
        "failed": 57,
        "failed_node_ids": ["tests/test_known.py::test_failure"],
        "failure_set_sha256": "4" * 64,
        "passed": 2656,
    }
    for name, value in (
        ("build_provenance.json", build_provenance),
        ("mock_e2e.json", mock),
        ("backend_failure_baseline.json", baseline),
    ):
        builder.atomic_write_json(output / name, value)
    builder.atomic_write_json(
        output / "backend_failure_postcheck.json",
        {
            "baseline_sha256": builder.sha256_file(
                output / "backend_failure_baseline.json"
            ),
            "failed": 57,
            "failed_node_ids": baseline["failed_node_ids"],
            "failure_set_sha256": baseline["failure_set_sha256"],
            "passed": 2696,
            "source_git_head": head,
            "status": "KNOWN_BACKEND_FAILURE_SET_UNCHANGED",
        },
    )

    prepared = builder.prepare_artifacts(
        REPO_ROOT,
        output_root=output,
        attempts_root=output / "attempts",
        current_head=head,
        historical_evidence={"checked_file_count": 13, "status": "UNCHANGED"},
    )
    candidate_raw = (output / "approval_candidate.json").read_bytes()
    scope_raw = (output / "approval_scope.json").read_bytes()
    verified = builder.verify_artifacts(
        REPO_ROOT,
        output_root=output,
        attempts_root=output / "attempts",
        current_head=head,
        historical_evidence={"checked_file_count": 13, "status": "UNCHANGED"},
        publish=False,
    )
    assert prepared["approval_candidate_sha256"] == hashlib.sha256(
        candidate_raw
    ).hexdigest()
    assert prepared["approval_scope_id"] == json.loads(scope_raw)[
        "approval_scope_id"
    ]
    assert verified["status"] == "PROXY_TLS_PROBE_OFFLINE_VERIFIED"
    assert verified["external_activity"] == {
        "ai_call_count": 0,
        "authorization_constructed": False,
        "http_request_sent": False,
        "provider_attempt_count": 0,
        "real_proxy_tls_probe_attempts": 0,
        "real_public_network_success_count": 0,
        "secret_content_read": False,
    }
    assert builder.prepare_artifacts(
        REPO_ROOT,
        output_root=output,
        attempts_root=output / "attempts",
        current_head=head,
        historical_evidence={"checked_file_count": 13, "status": "UNCHANGED"},
    ) == prepared
    assert (output / "approval_candidate.json").read_bytes() == candidate_raw
    assert (output / "approval_scope.json").read_bytes() == scope_raw


def test_prepare_artifacts_rejects_failed_mock_or_existing_scope_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _load_module(BUILDER_PATH, "phase2_proxy_tls_probe_builder_reject")
    bindings = {
        key: {"path": f"bound/{key}", "sha256": "a" * 64}
        for key in builder.REQUIRED_SOURCE_BINDING_KEYS
    }
    monkeypatch.setattr(builder, "source_bindings", lambda _root: bindings)
    output = tmp_path / "output"
    output.mkdir()
    head = "1" * 40
    image_id = "sha256:" + "2" * 64
    builder.atomic_write_json(
        output / "build_provenance.json",
        {
            "ai_call_count": 0,
            "build_network": "none",
            "image_identity": {"image_id": image_id},
            "no_cache": True,
            "provider_attempt_count": 0,
            "pull": False,
            "real_public_network_success_count": 0,
            "secret_content_read": False,
            "source_git_head": head,
            "ca_bundle_identity": {"sha256": "3" * 64},
        },
    )
    builder.atomic_write_json(
        output / "backend_failure_baseline.json",
        {
            "failed": 57,
            "failed_node_ids": ["tests/test_known.py::test_failure"],
            "failure_set_sha256": "4" * 64,
            "passed": 2656,
        },
    )
    baseline = builder.read_canonical_json(output / "backend_failure_baseline.json")
    builder.atomic_write_json(
        output / "backend_failure_postcheck.json",
        {
            "baseline_sha256": builder.sha256_file(
                output / "backend_failure_baseline.json"
            ),
            "failed": 57,
            "failed_node_ids": baseline["failed_node_ids"],
            "failure_set_sha256": baseline["failure_set_sha256"],
            "passed": 2696,
            "source_git_head": head,
            "status": "KNOWN_BACKEND_FAILURE_SET_UNCHANGED",
        },
    )
    builder.atomic_write_json(
        output / "mock_e2e.json",
        {
            "status": "FAILED",
            "proxy_image_id": image_id,
        },
    )
    with pytest.raises(ValueError, match="mock_e2e_invalid"):
        builder.prepare_artifacts(
            REPO_ROOT,
            output_root=output,
            attempts_root=output / "attempts",
            current_head=head,
            historical_evidence={"checked_file_count": 13, "status": "UNCHANGED"},
        )


def test_builder_script_entrypoint_runs_after_all_function_definitions() -> None:
    source = BUILDER_PATH.read_text(encoding="utf-8")
    assert source.rindex('if __name__ == "__main__":') > source.rindex(
        "def build_scope("
    )


def test_mock_harness_creates_two_internal_networks() -> None:
    harness = _load_module(HARNESS_PATH, "phase2_proxy_tls_probe_harness_networks")
    names = harness.runtime_names("a" * 12)
    commands = harness.mock_network_create_commands(names)
    assert commands == [
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
    assert names["relay_network"] != names["egress_network"]


def test_mock_harness_requires_exact_proxy_double_attachment() -> None:
    harness = _load_module(HARNESS_PATH, "phase2_proxy_tls_probe_harness_topology")
    names = harness.runtime_names("b" * 12)
    network_inspect = {
        names["relay_network"]: {"Driver": "bridge", "Internal": True},
        names["egress_network"]: {"Driver": "bridge", "Internal": True},
    }
    container_inspect = [
        {
            "NetworkSettings": {
                "Networks": {
                    names["relay_network"]: {"NetworkID": "relay"},
                    names["egress_network"]: {"NetworkID": "egress"},
                }
            }
        }
    ]
    harness.validate_mock_topology(
        names=names,
        network_inspect=network_inspect,
        container_inspect=container_inspect,
    )
    container_inspect[0]["NetworkSettings"]["Networks"].pop(
        names["egress_network"]
    )
    with pytest.raises(ValueError, match="mock_topology_invalid"):
        harness.validate_mock_topology(
            names=names,
            network_inspect=network_inspect,
            container_inspect=container_inspect,
        )
    assert (
        harness.topology_status(
            names=names,
            network_inspect=network_inspect,
            container_inspect=container_inspect,
        )
        == "TOPOLOGY_BLOCKED"
    )


def test_mock_harness_defines_required_cases_and_three_happy_paths() -> None:
    harness = _load_module(HARNESS_PATH, "phase2_proxy_tls_probe_harness_cases")
    assert harness.EXPECTED_CASES == {
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


def test_mock_harness_uses_built_image_runner_and_no_sensitive_mount() -> None:
    source = HARNESS_PATH.read_text(encoding="utf-8")
    assert "build_provenance.json" in source
    assert "phase2-ark-proxy-tls-probe/probe.py" not in source
    assert '"/probe/probe.py"' in source
    assert "/run/phase2/provider-auth" not in source
    assert "/input/request.json" not in source
    assert "perform_provider_request" not in source
    assert "Authorization" not in source
    assert "--internal" in source


def test_mock_host_timeout_exceeds_tls_and_server_hold_budget() -> None:
    harness = _load_module(HARNESS_PATH, "phase2_proxy_tls_probe_mock_timeout")
    assert harness.PROBE_CONTAINER_TIMEOUT_SECONDS == 30.0
    assert harness.REFUSED_DNS_HOLD_SECONDS == 15.0
    assert harness.PROBE_CONTAINER_TIMEOUT_SECONDS > max(
        12.0,
        harness.REFUSED_DNS_HOLD_SECONDS,
    )


def test_mock_server_supports_dns_success_with_refused_port() -> None:
    source = MOCK_SERVER_PATH.read_text(encoding="utf-8")
    assert '"refused"' in source
    assert "if args.mode == \"refused\"" in source


def test_mock_case_artifacts_finalize_into_one_zero_activity_report(
    tmp_path: Path,
) -> None:
    harness = _load_module(HARNESS_PATH, "phase2_proxy_tls_probe_harness_chunked")
    image_id = "sha256:" + "a" * 64
    case_root = tmp_path / ".mock-cases.staging"
    case_root.mkdir()
    for case, terminal_status in harness.EXPECTED_CASES.items():
        result: dict[str, Any] = {
            "case": case,
            "terminal_status": terminal_status,
        }
        if terminal_status != "TOPOLOGY_BLOCKED":
            result.update(
                ai_call_count=0,
                authorization_constructed=False,
                double_network_topology="VERIFIED",
                http_request_sent=False,
                provider_attempt_count=0,
                real_public_network_success_count=0,
                secret_content_read=False,
            )
        harness._write_json(case_root / f"{case}.json", result)
    output = tmp_path / "mock_e2e.json"
    report = harness.finalize_case_results(
        case_root=case_root,
        output_path=output,
        image_id=image_id,
        residue={"container_count": 0, "network_count": 0},
    )
    assert report["status"] == "PRODUCTION_PROXY_TLS_PROBE_MOCK_PASSED"
    assert report["happy_path_runs"] == 3
    assert report["provider_attempt_count"] == 0
    assert report["ai_call_count"] == 0
    assert not case_root.exists()
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_mock_cli_supports_one_case_per_process_and_offline_finalize() -> None:
    source = HARNESS_PATH.read_text(encoding="utf-8")
    assert 'parser.add_argument("--case"' in source
    assert 'parser.add_argument("--finalize"' in source
    assert "run_case_harness(" in source
    assert "finalize_case_results(" in source
