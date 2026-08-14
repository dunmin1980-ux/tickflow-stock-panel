from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import ssl
import stat
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "docker/phase2-ark-tls-connectivity-probe/probe.py"
RUNNER_PATH = REPO_ROOT / "backend/scripts/run_phase2_ark_tls_connectivity_probe.py"
PROBE_ID = "a" * 32


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required implementation is missing: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def probe_module() -> ModuleType:
    if not PROBE_PATH.is_file():
        pytest.skip("probe implementation has not been added yet")
    return _load_module(PROBE_PATH, "phase2_ark_tls_connectivity_probe")


@pytest.fixture
def runner_module() -> ModuleType:
    if not RUNNER_PATH.is_file():
        pytest.skip("host runner has not been added yet")
    return _load_module(RUNNER_PATH, "run_phase2_ark_tls_connectivity_probe")


def test_probe_source_exists() -> None:
    assert PROBE_PATH.is_file(), "probe implementation must be source controlled"


def test_host_runner_source_exists() -> None:
    assert RUNNER_PATH.is_file(), "one-shot host runner must be source controlled"


class _Clock:
    def __init__(self) -> None:
        self.value = 0

    def monotonic_ns(self) -> int:
        self.value += 1
        return self.value

    def wall_time(self) -> str:
        self.value += 1
        return f"2026-08-14T00:00:{self.value:02d}Z"


class _RawSocket:
    def __init__(self, *, connect_error: BaseException | None = None) -> None:
        self.connect_error = connect_error
        self.connected_to: Any = None
        self.timeouts: list[float] = []
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def connect(self, address: Any) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected_to = address

    def close(self) -> None:
        self.closed = True


class _TLSSocket:
    def __init__(
        self,
        raw_socket: _RawSocket,
        *,
        handshake_error: BaseException | None = None,
        unwrap_error: BaseException | None = None,
    ) -> None:
        self.raw_socket = raw_socket
        self.handshake_error = handshake_error
        self.unwrap_error = unwrap_error
        self.handshake_count = 0
        self.unwrap_count = 0
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def do_handshake(self) -> None:
        self.handshake_count += 1
        if self.handshake_error is not None:
            raise self.handshake_error

    def getpeercert(self) -> dict[str, Any]:
        return {
            "notBefore": "Aug  1 00:00:00 2026 GMT",
            "notAfter": "Sep  1 00:00:00 2026 GMT",
            "subjectAltName": (
                ("DNS", "ark.cn-beijing.volces.com"),
                ("DNS", "example.invalid"),
            ),
        }

    def version(self) -> str:
        return "TLSv1.3"

    def cipher(self) -> tuple[str, str, int]:
        return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)

    def unwrap(self) -> _RawSocket:
        self.unwrap_count += 1
        if self.unwrap_error is not None:
            raise self.unwrap_error
        return self.raw_socket

    def close(self) -> None:
        self.raw_socket.close()


class _TLSContext:
    def __init__(self, tls_socket: _TLSSocket) -> None:
        self.tls_socket = tls_socket
        self.calls: list[tuple[_RawSocket, str, bool]] = []

    def wrap_socket(
        self,
        raw_socket: _RawSocket,
        *,
        server_hostname: str,
        do_handshake_on_connect: bool,
    ) -> _TLSSocket:
        self.calls.append((raw_socket, server_hostname, do_handshake_on_connect))
        return self.tls_socket


def _policy(probe_module: ModuleType) -> Any:
    return probe_module.ProbePolicy(
        tcp_connect_timeout_seconds=10.0,
        tls_handshake_timeout_seconds=180.0,
        tls_close_timeout_seconds=5.0,
    )


def _resolver(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("192.0.2.1", 443),
        )
    ]


def test_success_uses_one_address_one_verified_handshake_and_no_http(
    probe_module: ModuleType,
) -> None:
    clock = _Clock()
    raw_socket = _RawSocket()
    tls_socket = _TLSSocket(raw_socket)
    context = _TLSContext(tls_socket)
    resolver_calls: list[tuple[Any, ...]] = []

    def resolver(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        resolver_calls.append((*args, kwargs))
        return _resolver()

    receipt = probe_module.execute_probe(
        PROBE_ID,
        policy=_policy(probe_module),
        resolver=resolver,
        socket_factory=lambda *_args: raw_socket,
        tls_context_factory=lambda: context,
        wall_clock=clock.wall_time,
        monotonic_ns=clock.monotonic_ns,
    )

    assert len(resolver_calls) == 1
    assert raw_socket.connected_to == ("192.0.2.1", 443)
    assert context.calls == [
        (raw_socket, "ark.cn-beijing.volces.com", False)
    ]
    assert tls_socket.handshake_count == 1
    assert tls_socket.unwrap_count == 1
    assert receipt["terminal_status"] == "PROBE_PASSED"
    assert receipt["tls_error_category"] == "NONE"
    assert receipt["tls_version"] == "TLSv1.3"
    assert receipt["cipher_name"] == "TLS_AES_256_GCM_SHA384"
    assert receipt["certificate_not_before"] == "Aug  1 00:00:00 2026 GMT"
    assert receipt["certificate_not_after"] == "Sep  1 00:00:00 2026 GMT"
    assert receipt["san_contains_hostname"] is True
    assert receipt["provider_attempt_count"] == 0
    assert receipt["ai_call_count"] == 0
    assert receipt["secret_content_read"] is False
    assert receipt["authorization_constructed"] is False
    assert receipt["http_request_sent"] is False
    assert receipt["business_body_sent"] is False
    assert receipt["probe_attempt_count"] == 1
    assert receipt["retry_count"] == 0
    assert all(
        receipt["events"][name]["occurred"] is True
        for name in probe_module.CHILD_SUCCESS_EVENTS
    )
    assert receipt["events"]["cleanup_completed"]["occurred"] is False
    serialized = json.dumps(receipt, sort_keys=True)
    assert "192.0.2.1" not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized
    assert "api_key" not in serialized.lower()
    assert "HTTP" not in serialized


@pytest.mark.parametrize(
    ("phase", "error", "expected"),
    [
        ("dns", socket.gaierror(-2, "name not known"), "DNS_RESOLUTION_FAILED"),
        ("tcp", TimeoutError("timed out"), "TCP_CONNECT_TIMEOUT"),
        ("tcp", ConnectionRefusedError("refused"), "TCP_CONNECT_FAILED"),
        ("tls", TimeoutError("timed out"), "TLS_HANDSHAKE_TIMEOUT"),
        ("tls", ConnectionResetError("reset"), "TLS_CONNECTION_RESET"),
        ("tls", ssl.SSLEOFError(8, "eof"), "TLS_EOF"),
        ("tls", ssl.SSLError(1, "WRONG_VERSION_NUMBER"), "TLS_PROTOCOL_FAILED"),
        ("tls", ssl.SSLError(1, "synthetic ssl"), "TLS_OTHER_SSL_ERROR"),
        ("tls", RuntimeError("synthetic process error"), "PROCESS_ERROR"),
    ],
)
def test_exception_classifier_preserves_phase_and_tls_subtype(
    probe_module: ModuleType,
    phase: str,
    error: BaseException,
    expected: str,
) -> None:
    classification = probe_module.classify_probe_error(error, phase=phase)
    assert classification.category == expected
    assert classification.verify_code is None
    assert classification.verify_message is None


def test_certificate_and_hostname_verification_errors_are_distinct(
    probe_module: ModuleType,
) -> None:
    certificate_error = ssl.SSLCertVerificationError(1, "certificate expired")
    certificate_error.verify_code = 10
    certificate_error.verify_message = "certificate has expired"
    hostname_error = ssl.SSLCertVerificationError(1, "hostname mismatch")
    hostname_error.verify_code = 62
    hostname_error.verify_message = (
        "Hostname mismatch, certificate is not valid for ark.cn-beijing.volces.com"
    )

    certificate = probe_module.classify_probe_error(certificate_error, phase="tls")
    hostname = probe_module.classify_probe_error(hostname_error, phase="tls")

    assert certificate.category == "TLS_CERT_VERIFY_FAILED"
    assert certificate.verify_code == 10
    assert certificate.verify_message == "certificate has expired"
    assert hostname.category == "TLS_HOSTNAME_VERIFY_FAILED"
    assert hostname.verify_code == 62
    assert hostname.verify_message.startswith("Hostname mismatch")


def test_failure_stops_at_exact_stage_and_remains_retry_free(
    probe_module: ModuleType,
) -> None:
    clock = _Clock()
    raw_socket = _RawSocket()
    verification_error = ssl.SSLCertVerificationError(1, "certificate expired")
    verification_error.verify_code = 10
    verification_error.verify_message = "certificate has expired"
    tls_socket = _TLSSocket(raw_socket, handshake_error=verification_error)

    receipt = probe_module.execute_probe(
        PROBE_ID,
        policy=_policy(probe_module),
        resolver=_resolver,
        socket_factory=lambda *_args: raw_socket,
        tls_context_factory=lambda: _TLSContext(tls_socket),
        wall_clock=clock.wall_time,
        monotonic_ns=clock.monotonic_ns,
    )

    assert receipt["terminal_status"] == "TLS_CERT_VERIFY_FAILED"
    assert receipt["probe_attempt_count"] == 1
    assert receipt["retry_count"] == 0
    assert receipt["events"]["dns_completed"]["occurred"] is True
    assert receipt["events"]["tcp_connect_completed"]["occurred"] is True
    assert receipt["events"]["tls_handshake_started"]["occurred"] is True
    assert receipt["events"]["tls_handshake_completed"]["occurred"] is False
    assert receipt["events"]["certificate_verified"]["occurred"] is False
    assert receipt["events"]["hostname_verified"]["occurred"] is False
    assert receipt["events"]["probe_closed"]["occurred"] is False


def test_receipt_validator_rejects_sensitive_or_http_fields(
    probe_module: ModuleType,
) -> None:
    clock = _Clock()
    raw_socket = _RawSocket()
    receipt = probe_module.execute_probe(
        PROBE_ID,
        policy=_policy(probe_module),
        resolver=_resolver,
        socket_factory=lambda *_args: raw_socket,
        tls_context_factory=lambda: _TLSContext(_TLSSocket(raw_socket)),
        wall_clock=clock.wall_time,
        monotonic_ns=clock.monotonic_ns,
    )
    probe_module.validate_probe_receipt(receipt, require_cleanup=False)

    for forbidden_field in ("authorization", "secret", "http_method", "body"):
        tampered = dict(receipt)
        tampered[forbidden_field] = "forbidden"
        with pytest.raises(ValueError, match="probe_receipt_invalid"):
            probe_module.validate_probe_receipt(tampered, require_cleanup=False)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("tls_version", 13),
        ("cipher_name", ["TLS_AES_256_GCM_SHA384"]),
        ("certificate_not_before", 1),
        ("certificate_not_after", False),
        ("san_contains_hostname", "true"),
    ],
)
def test_receipt_validator_matches_bound_scalar_types(
    probe_module: ModuleType,
    field: str,
    invalid: Any,
) -> None:
    clock = _Clock()
    receipt = probe_module.execute_probe(
        PROBE_ID,
        resolver=_resolver,
        socket_factory=lambda *_args: _RawSocket(),
        tls_context_factory=lambda: _TLSContext(),
        wall_clock=clock.wall_time,
        monotonic_ns=clock.monotonic_ns,
    )
    receipt[field] = invalid
    with pytest.raises(ValueError, match="probe_receipt_invalid"):
        probe_module.validate_probe_receipt(receipt, require_cleanup=False)


def test_receipt_validator_rejects_non_monotonic_event_order(
    probe_module: ModuleType,
) -> None:
    clock = _Clock()
    receipt = probe_module.execute_probe(
        PROBE_ID,
        resolver=_resolver,
        socket_factory=lambda *_args: _RawSocket(),
        tls_context_factory=lambda: _TLSContext(),
        wall_clock=clock.wall_time,
        monotonic_ns=clock.monotonic_ns,
    )
    receipt["events"]["dns_completed"]["monotonic_ns"] = 1
    with pytest.raises(ValueError, match="probe_receipt_invalid"):
        probe_module.validate_probe_receipt(receipt, require_cleanup=False)


def test_receipt_validator_fails_closed_for_malformed_event_value(
    probe_module: ModuleType,
) -> None:
    clock = _Clock()
    receipt = probe_module.execute_probe(
        PROBE_ID,
        resolver=_resolver,
        socket_factory=lambda *_args: _RawSocket(),
        tls_context_factory=lambda: _TLSContext(),
        wall_clock=clock.wall_time,
        monotonic_ns=clock.monotonic_ns,
    )
    receipt["events"]["dns_started"] = []
    with pytest.raises(ValueError, match="probe_receipt_invalid"):
        probe_module.validate_probe_receipt(receipt, require_cleanup=False)


def test_tls_context_uses_fixed_ca_and_secure_verification(
    probe_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    calls: list[str] = []

    def fake_default_context(*, cafile: str | None = None) -> ssl.SSLContext:
        calls.append(str(cafile))
        return context

    monkeypatch.setattr(ssl, "create_default_context", fake_default_context)
    observed = probe_module.create_tls_context()

    assert calls == ["/etc/ssl/certs/ca-certificates.crt"]
    assert observed is context
    assert observed.check_hostname is True
    assert observed.verify_mode == ssl.CERT_REQUIRED
    assert observed.minimum_version == ssl.TLSVersion.TLSv1_2
    assert observed.maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED


def test_docker_command_is_restricted_and_mounts_no_secret_or_broad_path(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    probe_id_file = tmp_path / "probe-id"
    probe_id_file.write_text(PROBE_ID + "\n", encoding="ascii")
    command = runner_module.build_probe_container_command(
        probe_id=PROBE_ID,
        container_name="phase2-ark-tls-probe-aaaaaaaaaaaa",
        network_name="phase2-ark-tls-probe-net-aaaaaaaaaaaa",
        output_dir=output,
        probe_source=PROBE_PATH,
        probe_id_file=probe_id_file,
    )

    assert command[:2] == ["docker", "create"]
    assert "--read-only" in command
    assert command[command.index("--user") + 1] == "65532:65532"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert command[command.index("--restart") + 1] == "no"
    assert command[command.index("--network") + 1] == (
        "phase2-ark-tls-probe-net-aaaaaaaaaaaa"
    )
    assert runner_module.PROXY_IMAGE_ID in command
    mounts = [command[index + 1] for index, item in enumerate(command) if item == "--mount"]
    assert len(mounts) == 3
    assert any("dst=/probe/probe.py,readonly" in mount for mount in mounts)
    assert any(
        "dst=/run/tickflow/probe-id,readonly" in mount for mount in mounts
    )
    assert any("dst=/output" in mount and "readonly" not in mount for mount in mounts)
    serialized = " ".join(command).lower()
    assert "docker.sock" not in serialized
    assert "provider-auth" not in serialized
    assert "secret" not in serialized
    sanitized_mounts = serialized.replace(
        str(PROBE_PATH).lower(), "<single-probe-source>"
    )
    assert str(Path.home()).lower() not in sanitized_mounts
    assert str(REPO_ROOT).lower() not in sanitized_mounts
    assert "ark.cn-beijing.volces.com" not in serialized


def test_execution_ledger_is_mode_0600_and_refuses_second_probe(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "execution-ledger.json"
    ledger = runner_module.create_execution_ledger(
        ledger_path,
        probe_id=PROBE_ID,
        probe_source_sha256="b" * 64,
        wall_time="2026-08-14T01:00:00Z",
    )

    assert ledger["probe_id"] == PROBE_ID
    assert ledger["probe_attempt_count"] == 0
    assert ledger["retry_count"] == 0
    assert ledger["provider_attempt_count"] == 0
    assert ledger["ai_call_count"] == 0
    assert ledger["state"] == "PREPARED"
    assert stat.S_IMODE(os.stat(ledger_path).st_mode) == 0o600

    with pytest.raises(ValueError, match="probe_execution_already_exists"):
        runner_module.create_execution_ledger(
            ledger_path,
            probe_id="c" * 32,
            probe_source_sha256="d" * 64,
            wall_time="2026-08-14T01:00:01Z",
        )


def test_execution_ledger_creation_is_atomic_against_toctou(
    runner_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "execution-ledger.json"
    monkeypatch.setattr(runner_module.os.path, "lexists", lambda _path: False)

    runner_module.create_execution_ledger(
        ledger_path,
        probe_id=PROBE_ID,
        probe_source_sha256="b" * 64,
        wall_time="2026-08-14T01:00:00Z",
    )
    with pytest.raises(ValueError, match="probe_execution_already_exists"):
        runner_module.create_execution_ledger(
            ledger_path,
            probe_id="c" * 32,
            probe_source_sha256="d" * 64,
            wall_time="2026-08-14T01:00:01Z",
        )

    ledger = json.loads(ledger_path.read_bytes())
    assert ledger["probe_id"] == PROBE_ID


def test_mark_dispatch_consumes_attempt_before_container_start(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "execution-ledger.json"
    runner_module.create_execution_ledger(
        ledger_path,
        probe_id=PROBE_ID,
        probe_source_sha256="b" * 64,
        wall_time="2026-08-14T01:00:00Z",
    )

    ledger = runner_module.mark_probe_dispatched(
        ledger_path,
        wall_time="2026-08-14T01:00:02Z",
    )

    assert ledger["state"] == "NETWORK_DISPATCH_STARTED"
    assert ledger["probe_attempt_count"] == 1
    assert ledger["retry_count"] == 0
    assert ledger["provider_attempt_count"] == 0
    assert ledger["ai_call_count"] == 0


def test_historical_baseline_verifier_detects_mutation_and_symlink(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    evidence = repo / "evidence.json"
    evidence.write_text('{"state":"CONSUMED"}\n', encoding="utf-8")
    approval = tmp_path / "approval.json"
    approval.write_text('{"approved":true}\n', encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "frozen_roots": ["."],
                "historical_baseline_schema_version": 1,
                "files": [
                    {
                        "path_kind": "repo",
                        "path": "evidence.json",
                        "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    },
                    {
                        "path_kind": "approval",
                        "path": "runtime-approval.json",
                        "sha256": hashlib.sha256(approval.read_bytes()).hexdigest(),
                    },
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert runner_module.verify_historical_baseline(
        repo,
        baseline,
        approval,
    ) == 2

    evidence.write_text('{"state":"MUTATED"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="historical_evidence_mutated"):
        runner_module.verify_historical_baseline(repo, baseline, approval)

    evidence.unlink()
    evidence.symlink_to(approval)
    with pytest.raises(ValueError, match="historical_evidence_invalid"):
        runner_module.verify_historical_baseline(repo, baseline, approval)


def test_historical_baseline_verifier_rejects_unlisted_file(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    frozen = repo / "history"
    frozen.mkdir(parents=True)
    evidence = frozen / "receipt.json"
    evidence.write_text('{"state":"CONSUMED"}\n', encoding="utf-8")
    approval = tmp_path / "approval.json"
    approval.write_text('{"approved":true}\n', encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "frozen_roots": ["history"],
                "historical_baseline_schema_version": 1,
                "files": [
                    {
                        "path_kind": "repo",
                        "path": "history/receipt.json",
                        "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    assert runner_module.verify_historical_baseline(repo, baseline, approval) == 1

    (frozen / "unexpected.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="historical_evidence_set_changed"):
        runner_module.verify_historical_baseline(repo, baseline, approval)


def test_host_finalization_adds_cleanup_without_changing_child_evidence(
    probe_module: ModuleType,
    runner_module: ModuleType,
) -> None:
    clock = _Clock()
    raw_socket = _RawSocket()
    child = probe_module.execute_probe(
        PROBE_ID,
        policy=_policy(probe_module),
        resolver=_resolver,
        socket_factory=lambda *_args: raw_socket,
        tls_context_factory=lambda: _TLSContext(_TLSSocket(raw_socket)),
        wall_clock=clock.wall_time,
        monotonic_ns=clock.monotonic_ns,
    )
    original = json.loads(json.dumps(child))

    final = runner_module.finalize_probe_receipt(
        child,
        wall_time="2026-08-14T01:00:03Z",
        monotonic_ns=99,
        probe_module=probe_module,
    )

    assert child == original
    assert final["events"]["cleanup_completed"] == {
        "occurred": True,
        "wall_time": "2026-08-14T01:00:03Z",
        "monotonic_ns": 99,
        "category": "PASSED",
    }
    probe_module.validate_probe_receipt(final, require_cleanup=True)


def test_host_finalization_marks_cleanup_residue(
    probe_module: ModuleType,
    runner_module: ModuleType,
) -> None:
    receipt = runner_module._empty_process_receipt(PROBE_ID, probe_module)

    final = runner_module.finalize_probe_receipt(
        receipt,
        wall_time="2026-08-14T01:00:03Z",
        monotonic_ns=99,
        probe_module=probe_module,
        cleanup_succeeded=False,
    )

    assert final["events"]["cleanup_completed"]["occurred"] is True
    assert final["events"]["cleanup_completed"]["category"] == "RESIDUE_DETECTED"
    probe_module.validate_probe_receipt(final, require_cleanup=True)


def test_cleanup_is_fail_closed_and_attempts_both_resources(
    runner_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container_exists = True
    network_exists = True
    network_remove_attempted = False

    def fake_run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal container_exists, network_exists, network_remove_attempted
        if command[:3] == ["docker", "container", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0 if container_exists else 1,
                "",
                "" if container_exists else "Error: No such container: container",
            )
        if command[:4] == ["docker", "container", "rm", "--force"]:
            raise subprocess.TimeoutExpired(command, timeout)
        if command[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0 if network_exists else 1,
                "",
                "" if network_exists else "Error: network network not found",
            )
        if command[:3] == ["docker", "network", "rm"]:
            network_remove_attempted = True
            network_exists = False
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(runner_module, "_run", fake_run)

    assert runner_module._cleanup("container", "network") == (1, 0)
    assert network_remove_attempted is True


def test_resource_inspection_errors_are_not_treated_as_absent(
    runner_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            "Cannot connect to the Docker daemon",
        )

    monkeypatch.setattr(runner_module, "_run", unavailable)

    with pytest.raises(runner_module.ProbeRunnerError, match="container_inspect_failed"):
        runner_module._container_exists("container")
    with pytest.raises(runner_module.ProbeRunnerError, match="network_inspect_failed"):
        runner_module._network_exists("network")


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("NONE", "PHASE2B_ARK_TLS_CONNECTIVITY_PROBE_PASSED"),
        ("DNS_RESOLUTION_FAILED", "PHASE2B_ARK_TLS_PROBE_DNS_BLOCKED"),
        ("TCP_CONNECT_FAILED", "PHASE2B_ARK_TLS_PROBE_TCP_BLOCKED"),
        ("TCP_CONNECT_TIMEOUT", "PHASE2B_ARK_TLS_PROBE_TCP_BLOCKED"),
        ("TLS_CERT_VERIFY_FAILED", "PHASE2B_ARK_TLS_PROBE_CERT_BLOCKED"),
        ("TLS_HOSTNAME_VERIFY_FAILED", "PHASE2B_ARK_TLS_PROBE_HOSTNAME_BLOCKED"),
        ("TLS_HANDSHAKE_TIMEOUT", "PHASE2B_ARK_TLS_PROBE_HANDSHAKE_BLOCKED"),
        ("TLS_PROTOCOL_FAILED", "PHASE2B_ARK_TLS_PROBE_HANDSHAKE_BLOCKED"),
        ("PROCESS_ERROR", "PHASE2B_ARK_TLS_PROBE_UNKNOWN"),
    ],
)
def test_terminal_status_mapping_is_fail_closed(
    runner_module: ModuleType,
    category: str,
    expected: str,
) -> None:
    assert runner_module.final_status_for_category(category) == expected


def test_dispatched_timeout_is_consumed_published_and_cleaned(
    runner_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "reports"
    output_root.mkdir()
    attempts_root = output_root / "attempts"
    attempts_root.mkdir(mode=0o700)
    approval = tmp_path / "approval.json"
    approval.write_text('{"approved":true}\n', encoding="utf-8")
    baseline = output_root / "historical_baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "frozen_roots": [],
                "historical_baseline_schema_version": 1,
                "files": [
                    {
                        "path_kind": "approval",
                        "path": "runtime-approval.json",
                        "sha256": hashlib.sha256(approval.read_bytes()).hexdigest(),
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    container_exists = False
    network_exists = False

    def fake_run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal container_exists, network_exists
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, runner_module.PROXY_IMAGE_ID + "\n", "")
        if command[:3] == ["docker", "network", "create"]:
            network_exists = True
            return subprocess.CompletedProcess(command, 0, "network\n", "")
        if command[:2] == ["docker", "create"]:
            container_exists = True
            return subprocess.CompletedProcess(command, 0, "container\n", "")
        if command[:3] == ["docker", "start", "--attach"]:
            raise subprocess.TimeoutExpired(command, timeout)
        if command[:3] == ["docker", "container", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0 if container_exists else 1,
                "",
                "" if container_exists else f"Error: No such container: {command[-1]}",
            )
        if command[:4] == ["docker", "container", "rm", "--force"]:
            container_exists = False
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0 if network_exists else 1,
                "",
                "" if network_exists else f"Error: network {command[-1]} not found",
            )
        if command[:3] == ["docker", "network", "rm"]:
            network_exists = False
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(runner_module, "OUTPUT_ROOT", output_root)
    monkeypatch.setattr(runner_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner_module, "BASELINE_PATH", baseline)
    monkeypatch.setattr(runner_module, "LOCAL_APPROVAL_PATH", approval)
    monkeypatch.setattr(runner_module, "V2_ATTEMPTS_ROOT", attempts_root)
    monkeypatch.setattr(
        runner_module,
        "load_probe_v2_execution_identity",
        lambda *_args, **_kwargs: {
            "approval_candidate_sha256": "a" * 64,
            "approval_scope_id": "b" * 64,
            "source_git_head": "c" * 40,
            "historical_attempts": 0,
            "attempt_availability": "AVAILABLE",
            "probe_source_sha256": hashlib.sha256(
                runner_module.PROBE_SOURCE.read_bytes()
            ).hexdigest(),
            "host_orchestrator_sha256": "d" * 64,
        },
    )
    monkeypatch.setattr(
        runner_module.uuid,
        "uuid4",
        lambda: type("FixedUUID", (), {"hex": PROBE_ID})(),
    )
    monkeypatch.setattr(runner_module.tempfile, "mkdtemp", lambda **_kwargs: str(runtime_root))
    monkeypatch.setattr(runner_module, "_run", fake_run)

    result = runner_module.run_live_probe()

    assert result["final_status"] == "PHASE2B_ARK_TLS_PROBE_UNKNOWN"
    assert result["probe_attempt_count"] == 1
    assert result["retry_count"] == 0
    assert result["container_residue_count"] == 0
    assert result["network_residue_count"] == 0
    assert result["temporary_residue_count"] == 0
    assert not runtime_root.exists()
    attempt_dir = attempts_root / ("b" * 64) / PROBE_ID
    ledger = json.loads((attempt_dir / "ledger.json").read_bytes())
    assert ledger["state"] == "COMPLETED"
    assert ledger["probe_attempt_count"] == 1
    receipt = json.loads(
        (attempt_dir / "evidence" / "receipt.json").read_bytes()
    )
    precleanup = json.loads(
        (
            attempt_dir
            / "evidence"
            / "transport-evidence-precleanup.json"
        ).read_bytes()
    )
    postcleanup = json.loads(
        (attempt_dir / "evidence" / "transport-evidence.json").read_bytes()
    )
    assert receipt["tls_error_category"] == "PROCESS_ERROR"
    assert receipt["events"]["cleanup_completed"]["occurred"] is True
    assert precleanup["host_lifecycle"]["receipt_archive_completed"]["occurred"] is True
    assert precleanup["host_lifecycle"]["cleanup_started"]["occurred"] is False
    assert postcleanup["host_lifecycle"]["cleanup_completed"]["occurred"] is True
    assert postcleanup["temporary_residue_count"] == 0


def _write_probe_v2_identity(
    runner_module: ModuleType,
    root: Path,
    *,
    source_head: str = "1" * 40,
) -> dict[str, Path | str]:
    candidate_path = root / "approval_candidate.json"
    scope_path = root / "approval_scope.json"
    approval_dir = root / "local-approval"
    approval_path = approval_dir / "runtime-approval.json"
    attempts_root = root / "attempts"
    approval_dir.mkdir(mode=0o700)
    attempts_root.mkdir(mode=0o700)
    candidate = {
        "probe_approval_candidate_schema_version": 2,
        "candidate_status": "TLS_PROBE_CANDIDATE_READY_FOR_REVIEW",
        "approval_installed": False,
        "scope_type": "ark_tls_connectivity_probe_v2",
        "source_git_head": source_head,
        "probe_contract_version": 2,
        "target_host": "ark.cn-beijing.volces.com",
        "target_port": 443,
        "sni_hostname": "ark.cn-beijing.volces.com",
        "hostname_verification_target": "ark.cn-beijing.volces.com",
        "retry_count": 0,
        "maximum_probe_attempts": 1,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "authorization_constructed": False,
        "http_request_sent": False,
        "secret_content_read": False,
        "real_public_network_success_count": 0,
        "source_bindings": {
            "probe_runner_source": {
                "path": PROBE_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": hashlib.sha256(PROBE_PATH.read_bytes()).hexdigest(),
            },
            "probe_host_orchestrator": {
                "path": RUNNER_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest(),
            },
        },
        "artifact_hashes": {
            "container_image_id": runner_module.PROXY_IMAGE_ID,
            "probe_runner_source_sha256": hashlib.sha256(
                PROBE_PATH.read_bytes()
            ).hexdigest(),
            "probe_host_orchestrator_sha256": hashlib.sha256(
                RUNNER_PATH.read_bytes()
            ).hexdigest(),
        },
    }
    candidate_raw = (
        json.dumps(candidate, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    candidate_path.write_bytes(candidate_raw)
    candidate_sha = hashlib.sha256(candidate_raw).hexdigest()
    scope_identity = {
        "scope_type": "ark_tls_connectivity_probe_v2",
        "approval_candidate_sha256": candidate_sha,
        "target_host": "ark.cn-beijing.volces.com",
        "target_port": 443,
    }
    scope_id = hashlib.sha256(
        (
            json.dumps(scope_identity, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
    ).hexdigest()
    scope = {
        "probe_approval_scope_schema_version": 1,
        **scope_identity,
        "approval_scope_id": scope_id,
        "approval_installed": False,
        "historical_attempts": 0,
        "attempt_availability": "AVAILABLE",
        "retry_count": 0,
        "maximum_probe_attempts": 1,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "provider_scope_shared": False,
        "ai_canary_scope_shared": False,
        "openai_scope_shared": False,
    }
    scope_path.write_text(
        json.dumps(scope, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    approval = {
        "probe_v2_runtime_approval_schema_version": 1,
        "approval_status": "APPROVED",
        "approval_candidate_sha256": candidate_sha,
        "approval_scope_id": scope_id,
        "source_git_head": source_head,
        "scope_type": "ark_tls_connectivity_probe_v2",
    }
    approval_path.write_text(
        json.dumps(approval, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    approval_path.chmod(0o600)
    return {
        "candidate_path": candidate_path,
        "scope_path": scope_path,
        "approval_path": approval_path,
        "attempts_root": attempts_root,
        "candidate_sha": candidate_sha,
        "scope_id": scope_id,
    }


def test_probe_v2_identity_is_independent_and_available(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    identity = _write_probe_v2_identity(runner_module, tmp_path)

    loaded = runner_module.load_probe_v2_execution_identity(
        REPO_ROOT,
        candidate_path=identity["candidate_path"],
        scope_path=identity["scope_path"],
        approval_path=identity["approval_path"],
        attempts_root=identity["attempts_root"],
    )

    assert loaded["approval_candidate_sha256"] == identity["candidate_sha"]
    assert loaded["approval_scope_id"] == identity["scope_id"]
    assert loaded["historical_attempts"] == 0
    assert loaded["attempt_availability"] == "AVAILABLE"


def test_probe_v2_identity_rejects_noncanonical_candidate_file_bytes(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    identity = _write_probe_v2_identity(runner_module, tmp_path)
    candidate_path = Path(identity["candidate_path"])
    candidate = json.loads(candidate_path.read_bytes())
    candidate_path.write_text(
        json.dumps(candidate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(runner_module.ProbeRunnerError, match="probe_v2_identity_invalid"):
        runner_module.load_probe_v2_execution_identity(
            REPO_ROOT,
            candidate_path=candidate_path,
            scope_path=identity["scope_path"],
            approval_path=identity["approval_path"],
            attempts_root=identity["attempts_root"],
        )


def test_missing_probe_v2_approval_blocks_before_any_docker_action(
    runner_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    identity = _write_probe_v2_identity(runner_module, tmp_path)
    Path(identity["approval_path"]).unlink()
    calls: list[list[str]] = []

    monkeypatch.setattr(runner_module, "REPO_ROOT", REPO_ROOT)
    monkeypatch.setattr(runner_module, "V2_CANDIDATE_PATH", identity["candidate_path"])
    monkeypatch.setattr(runner_module, "V2_SCOPE_PATH", identity["scope_path"])
    monkeypatch.setattr(runner_module, "V2_LOCAL_APPROVAL_PATH", identity["approval_path"])
    monkeypatch.setattr(runner_module, "V2_ATTEMPTS_ROOT", identity["attempts_root"])
    monkeypatch.setattr(
        runner_module,
        "verify_historical_baseline",
        lambda *_args, **_kwargs: 7,
    )
    monkeypatch.setattr(
        runner_module,
        "_run",
        lambda command, **_kwargs: calls.append(command),
    )

    with pytest.raises(runner_module.ProbeRunnerError, match="probe_v2_approval_invalid"):
        runner_module.run_live_probe()
    assert calls == []


def test_probe_v2_scope_reservation_ignores_historical_singleton_ledger(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    identity = _write_probe_v2_identity(runner_module, tmp_path)
    historical_ledger = tmp_path / "historical-execution-ledger.json"
    historical_ledger.write_text('{"state":"COMPLETED"}\n', encoding="utf-8")
    historical_hash = hashlib.sha256(historical_ledger.read_bytes()).hexdigest()
    loaded = runner_module.load_probe_v2_execution_identity(
        REPO_ROOT,
        candidate_path=identity["candidate_path"],
        scope_path=identity["scope_path"],
        approval_path=identity["approval_path"],
        attempts_root=identity["attempts_root"],
    )

    reservation = runner_module.reserve_probe_v2_scope(
        loaded,
        probe_id=PROBE_ID,
        attempts_root=identity["attempts_root"],
    )

    assert reservation["scope_directory"].is_dir()
    assert reservation["attempt_directory"].is_dir()
    assert reservation["ledger_path"] == reservation["attempt_directory"] / "ledger.json"
    assert hashlib.sha256(historical_ledger.read_bytes()).hexdigest() == historical_hash
    with pytest.raises(ValueError, match="probe_scope_attempt_unavailable"):
        runner_module.reserve_probe_v2_scope(
            loaded,
            probe_id="b" * 32,
            attempts_root=identity["attempts_root"],
        )
