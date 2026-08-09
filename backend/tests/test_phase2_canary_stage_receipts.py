from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import ssl
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.services.phase2_ai_worker_protocol import (
    FakeClaimsWorker,
    build_worker_projection,
)
from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_provider_relay_protocol import build_relay_request

REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY_PATH = REPO_ROOT / "docker/phase2-openai-egress-proxy/proxy.py"
RELAY_PATH = REPO_ROOT / "docker/phase2-canary-relay/relay.py"
FACTS_PATH = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"
REQUEST_ID = "a" * 32
PROVIDER_EVENTS = (
    "provider_connect_started",
    "provider_connect_completed",
    "tls_completed",
    "request_write_started",
    "request_write_completed",
    "response_headers_received",
    "response_body_completed",
)
PROXY_LOCAL_EVENTS = (
    "process_started",
    "proxy_ready",
    "relay_request_received",
)
RELAY_EVENTS = (
    "relay_started",
    "proxy_connect_started",
    "proxy_connect_completed",
    "request_submitted",
    "response_wait_started",
    "response_received",
    "candidate_write_started",
    "candidate_write_completed",
    "candidate_ready_published",
)
EVENT_FIELDS = {
    "event",
    "occurred",
    "wall_time",
    "monotonic_ns",
    "http_status",
    "byte_count",
}
RUNTIME_CONTRACT = {
    "canary_runtime_contract_version": 1,
    "cleanup_timeout_seconds": 15,
    "host_orchestrator_timeout_seconds": 90,
    "maximum_provider_attempts": 1,
    "provider_connect_timeout_seconds": 10,
    "provider_read_timeout_seconds": 60,
    "provider_total_timeout_seconds": 60,
    "relay_candidate_wait_timeout_seconds": 75,
    "retry_count": 0,
}


@pytest.fixture(scope="module")
def proxy_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase2_observability_proxy_under_test",
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
def relay_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase2_observability_relay_under_test",
        RELAY_PATH,
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


class _Clock:
    def __init__(self) -> None:
        self.index = 0

    def wall(self) -> str:
        self.index += 1
        return f"2026-08-08T08:23:{self.index:02d}.000000Z"

    def monotonic_ns(self) -> int:
        return self.index * 1_000_000_000


class _Socket:
    def settimeout(self, _value: float) -> None:
        return None


class _Response:
    def __init__(
        self,
        *,
        body: bytes = b"{}",
        status: int = 200,
        read_error: BaseException | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.read_error = read_error

    def getheader(self, name: str) -> str | None:
        if name == "Content-Type":
            return "application/json"
        if name == "Content-Length":
            return str(len(self.body))
        return None

    def read(self, _maximum: int) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        return self.body


class _Connection:
    def __init__(
        self,
        *,
        connect_error: BaseException | None = None,
        request_error: BaseException | None = None,
        headers_error: BaseException | None = None,
        response: _Response | None = None,
    ) -> None:
        self.connect_error = connect_error
        self.request_error = request_error
        self.headers_error = headers_error
        self.response = response or _Response()
        self.sock: _Socket | None = None
        self.closed = False

    def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.sock = _Socket()

    def request(self, *_args: Any, **_kwargs: Any) -> None:
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self) -> _Response:
        if self.headers_error is not None:
            raise self.headers_error
        return self.response

    def close(self) -> None:
        self.closed = True


def _recorder(proxy_module: ModuleType) -> Any:
    clock = _Clock()
    return proxy_module.ProviderStageRecorder(
        wall_clock=clock.wall,
        monotonic_ns=clock.monotonic_ns,
    )


def _factory(connection: _Connection) -> Any:
    def create(
        _host: str,
        _port: int,
        _timeout: float,
        _context: ssl.SSLContext,
    ) -> _Connection:
        return connection

    return create


def _events(recorder: Any) -> dict[str, dict[str, Any]]:
    return recorder.events()


def _relay_inputs(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    facts_bytes = FACTS_PATH.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_bytes),
        hashlib.sha256(facts_bytes).hexdigest(),
        facts_bytes=facts_bytes,
    )
    request = build_relay_request(
        projection,
        request_id=REQUEST_ID,
    ).model_dump(mode="json")
    response = {
        "protocol_version": 1,
        "request_id": REQUEST_ID,
        "symbol": "000403.SZ",
        "projection_sha256": projection["projection_sha256"],
        "claims_candidate": json.loads(FakeClaimsWorker().run(projection)),
    }
    request_path = tmp_path / "request.json"
    projection_path = tmp_path / "projection.json"
    output = tmp_path / "output"
    request_path.write_bytes(canonical_json_bytes(request))
    projection_path.write_bytes(canonical_json_bytes(projection))
    request_path.chmod(0o444)
    projection_path.chmod(0o444)
    output.mkdir(mode=0o700)
    return request_path, projection_path, output, response


def test_proxy_success_records_all_seven_provider_events(
    proxy_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(response=_Response(body=b'{"ok":true}'))
    recorder = _recorder(proxy_module)
    monkeypatch.setattr(
        proxy_module,
        "create_tls_context",
        lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )

    status, body = proxy_module.perform_provider_request(
        b"{}",
        "synthetic-value",
        connection_factory=_factory(connection),
        runtime_contract=RUNTIME_CONTRACT,
        stage_recorder=recorder,
    )

    assert status == 200
    assert body == b'{"ok":true}'
    events = _events(recorder)
    assert tuple(events) == (*PROXY_LOCAL_EVENTS, *PROVIDER_EVENTS)
    assert events["process_started"]["occurred"] is True
    assert events["proxy_ready"]["occurred"] is False
    assert events["relay_request_received"]["occurred"] is False
    assert all(events[name]["occurred"] is True for name in PROVIDER_EVENTS)
    assert all(set(event) == EVENT_FIELDS for event in events.values())
    assert events["response_headers_received"]["http_status"] == 200
    assert events["response_body_completed"]["byte_count"] == len(body)
    assert all(
        isinstance(events[name]["monotonic_ns"], int) for name in PROVIDER_EVENTS
    )


@pytest.mark.parametrize(
    ("connection", "expected_true", "expected_false", "category"),
    [
        (
            _Connection(connect_error=OSError("connect failed")),
            ("provider_connect_started",),
            ("provider_connect_completed", "tls_completed"),
            "PROVIDER_CONNECT_FAILED",
        ),
        (
            _Connection(connect_error=ssl.SSLError("tls failed")),
            ("provider_connect_started",),
            ("provider_connect_completed", "tls_completed"),
            "TLS_FAILED",
        ),
        (
            _Connection(request_error=OSError("write failed")),
            (
                "provider_connect_started",
                "provider_connect_completed",
                "tls_completed",
                "request_write_started",
            ),
            ("request_write_completed", "response_headers_received"),
            "REQUEST_WRITE_FAILED",
        ),
        (
            _Connection(headers_error=TimeoutError("no headers")),
            (
                "provider_connect_started",
                "provider_connect_completed",
                "tls_completed",
                "request_write_started",
                "request_write_completed",
            ),
            ("response_headers_received", "response_body_completed"),
            "RESPONSE_HEADERS_NOT_RECEIVED",
        ),
        (
            _Connection(
                response=_Response(
                    read_error=http.client.IncompleteRead(b"partial", 10)
                )
            ),
            (
                "provider_connect_started",
                "provider_connect_completed",
                "tls_completed",
                "request_write_started",
                "request_write_completed",
                "response_headers_received",
            ),
            ("response_body_completed",),
            "RESPONSE_BODY_INCOMPLETE",
        ),
    ],
)
def test_proxy_failure_receipt_stops_at_last_proven_stage(
    proxy_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    connection: _Connection,
    expected_true: tuple[str, ...],
    expected_false: tuple[str, ...],
    category: str,
) -> None:
    recorder = _recorder(proxy_module)
    monkeypatch.setattr(
        proxy_module,
        "create_tls_context",
        lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )

    with pytest.raises(proxy_module.ProxyError) as raised:
        proxy_module.perform_provider_request(
            b"{}",
            "synthetic-value",
            connection_factory=_factory(connection),
            runtime_contract=RUNTIME_CONTRACT,
            stage_recorder=recorder,
        )

    assert raised.value.category == category
    events = _events(recorder)
    assert all(events[name]["occurred"] is True for name in expected_true)
    assert all(events[name]["occurred"] is False for name in expected_false)
    assert connection.closed is True


def test_proxy_receipt_is_atomic_strict_and_body_free(
    proxy_module: ModuleType,
    tmp_path: Path,
) -> None:
    recorder = _recorder(proxy_module)
    recorder.record("provider_connect_started")
    receipt = proxy_module.build_receipt(
        request_id=REQUEST_ID,
        response_category="UPSTREAM_REJECTED",
        terminal_status="PROVIDER_CONNECT_FAILED",
        provider_attempt_count=1,
        stage_recorder=recorder,
    )
    receipt_path = tmp_path / "proxy-receipt.json"

    proxy_module.write_receipt(receipt, str(receipt_path))

    persisted = json.loads(receipt_path.read_bytes())
    assert persisted == receipt
    assert persisted["receipt_schema_version"] == 2
    assert persisted["request_id"] == REQUEST_ID
    assert persisted["component"] == "proxy"
    assert all(name in persisted for name in (*PROXY_LOCAL_EVENTS, *PROVIDER_EVENTS))
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob("*.partial"))
    serialized = receipt_path.read_text(encoding="utf-8")
    assert "synthetic-value" not in serialized
    assert "Authorization" not in serialized
    assert "request body" not in serialized
    assert "response body" not in serialized


def test_proxy_receipt_atomic_replace_failure_preserves_existing_file(
    proxy_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "proxy-receipt.json"
    receipt_path.write_text('{"old":true}\n', encoding="utf-8")
    recorder = _recorder(proxy_module)
    receipt = proxy_module.build_receipt(
        request_id=REQUEST_ID,
        response_category="AUTH_BLOCKED",
        terminal_status="AUTH_BLOCKED",
        stage_recorder=recorder,
    )

    def fail_replace(_source: Any, _target: Any) -> None:
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(proxy_module.os, "replace", fail_replace)

    with pytest.raises(proxy_module.ProxyError):
        proxy_module.write_receipt(receipt, str(receipt_path))

    assert receipt_path.read_text(encoding="utf-8") == '{"old":true}\n'
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob("*.partial"))


def test_relay_success_records_all_local_events(
    relay_module: ModuleType,
    tmp_path: Path,
) -> None:
    request_path, projection_path, output, response = _relay_inputs(tmp_path)

    def requester(
        body: bytes,
        _timeout_seconds: int,
        recorder: Any,
    ) -> tuple[int, bytes]:
        raw = canonical_json_bytes(response)
        recorder.record("proxy_connect_started")
        recorder.record("proxy_connect_completed")
        recorder.record("request_submitted", byte_count=len(body))
        recorder.record("response_wait_started")
        recorder.record(
            "response_received",
            http_status=200,
            byte_count=len(raw),
        )
        return 200, raw

    receipt = relay_module.run_relay(
        request_path=str(request_path),
        projection_path=str(projection_path),
        output_dir=str(output),
        runtime_contract_path=str(
            REPO_ROOT / "docker/phase2-canary-relay/runtime-contract.json"
        ),
        requester=requester,
    )

    assert receipt["receipt_schema_version"] == 2
    assert receipt["request_id"] == REQUEST_ID
    assert receipt["component"] == "relay"
    assert receipt["exit_code"] == 0
    assert receipt["terminal_status"] == "RELAY_COMPLETED"
    assert receipt["terminal_reason_source"] == "relay_self"
    assert all(receipt[event]["occurred"] is True for event in RELAY_EVENTS)
    assert all(set(receipt[event]) == EVENT_FIELDS for event in RELAY_EVENTS)
    assert receipt == json.loads((output / "relay-receipt.json").read_bytes())


def test_relay_timeout_stops_at_last_proven_stage(
    relay_module: ModuleType,
    tmp_path: Path,
) -> None:
    request_path, projection_path, output, _response = _relay_inputs(tmp_path)

    def requester(
        body: bytes,
        _timeout_seconds: int,
        recorder: Any,
    ) -> tuple[int, bytes]:
        recorder.record("proxy_connect_started")
        recorder.record("proxy_connect_completed")
        recorder.record("request_submitted", byte_count=len(body))
        recorder.record("response_wait_started")
        raise relay_module.RelayError(
            "RELAY_TIMEOUT",
            proxy_request_count=1,
        )

    receipt = relay_module.run_relay(
        request_path=str(request_path),
        projection_path=str(projection_path),
        output_dir=str(output),
        runtime_contract_path=str(
            REPO_ROOT / "docker/phase2-canary-relay/runtime-contract.json"
        ),
        requester=requester,
    )

    assert receipt["exit_code"] == 2
    assert receipt["terminal_status"] == "RELAY_TIMEOUT"
    assert receipt["terminal_reason_source"] == "relay_self"
    assert receipt["response_wait_started"]["occurred"] is True
    assert receipt["response_received"]["occurred"] is False
    assert receipt["candidate_write_started"]["occurred"] is False
    assert receipt["candidate_ready_published"]["occurred"] is False
