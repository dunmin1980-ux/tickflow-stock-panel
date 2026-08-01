from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from app.schemas.phase2_claims import ALLOWED_CLAIM_TYPES
from app.schemas.phase2_provider_relay import (
    GenerationControls,
    RelayExecutionReceipt,
    RelayRequest,
    RelayResponse,
)
from app.services.phase2_ai_worker_protocol import (
    FakeClaimsWorker,
    build_worker_projection,
)
from app.services.phase2_claims_service import PREDICATE_RULES, canonical_json_bytes
from app.services.phase2_provider_relay_protocol import (
    Phase2ProviderRelayError,
    build_provider_envelope,
    build_relay_request,
    parse_single_json_object,
    validate_relay_response,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED_REQUEST_ID = "a" * 32
RELAY_PATH = REPO_ROOT / "docker/phase2-provider-relay/relay.py"
RELAY_DOCKERFILE = REPO_ROOT / "docker/phase2-provider-relay/Dockerfile"
PINNED_DISTROLESS = (
    "gcr.io/distroless/python3-debian12@sha256:"
    "7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)


def _projection() -> dict:
    facts_path = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"
    facts_bytes = facts_path.read_bytes()
    return build_worker_projection(
        json.loads(facts_bytes),
        hashlib.sha256(facts_bytes).hexdigest(),
        facts_bytes=facts_bytes,
    )


def _candidate(projection: dict | None = None) -> dict:
    bound_projection = projection or _projection()
    return json.loads(FakeClaimsWorker().run(bound_projection))


def _relay_response_payload(
    projection: dict | None = None,
    *,
    request_id: str = FIXED_REQUEST_ID,
) -> dict:
    bound_projection = projection or _projection()
    return {
        "protocol_version": 1,
        "request_id": request_id,
        "symbol": "000403.SZ",
        "projection_sha256": bound_projection["projection_sha256"],
        "claims_candidate": _candidate(bound_projection),
    }


def test_relay_request_is_exact_and_bound_to_projection() -> None:
    projection = _projection()

    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)

    assert request.model_dump(mode="json") == {
        "protocol_version": 1,
        "request_id": FIXED_REQUEST_ID,
        "symbol": "000403.SZ",
        "projection_sha256": projection["projection_sha256"],
        "claims_schema_version": 1,
        "allowed_claim_types": sorted(ALLOWED_CLAIM_TYPES),
        "allowed_predicates": sorted(PREDICATE_RULES),
        "response_format": "typed_claims_json",
    }


def test_generation_controls_are_closed_constants() -> None:
    controls = GenerationControls()

    assert controls.model_dump(mode="json") == {
        "temperature": 0,
        "response_format": "typed_claims_json",
        "tool_use": False,
        "web_browsing": False,
        "file_tools": False,
        "function_calling": False,
        "streaming": False,
        "retry_count": 0,
        "maximum_output_bytes": 1_048_576,
        "timeout_seconds": 2,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", 0.1),
        ("response_format", "markdown"),
        ("tool_use", True),
        ("web_browsing", True),
        ("file_tools", True),
        ("function_calling", True),
        ("streaming", True),
        ("retry_count", 1),
        ("maximum_output_bytes", 1_048_577),
        ("timeout_seconds", 3),
    ],
)
def test_generation_controls_reject_every_relaxed_setting(
    field: str,
    value: object,
) -> None:
    payload = GenerationControls().model_dump(mode="python")
    payload[field] = value

    with pytest.raises(ValidationError):
        GenerationControls.model_validate(payload)


def test_provider_envelope_contains_only_minimal_projection_and_closed_controls() -> None:
    projection = _projection()
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)

    envelope = build_provider_envelope(request, projection)

    assert envelope.model_dump(mode="json") == {
        "request": request.model_dump(mode="json"),
        "projection": projection,
        "generation": GenerationControls().model_dump(mode="json"),
    }
    serialized = json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False)
    assert str(REPO_ROOT) not in serialized
    assert str(Path.home()) not in serialized
    assert "reports/phase2_facts" not in serialized


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocol_version", 2),
        ("request_id", "A" * 32),
        ("request_id", "a" * 31),
        ("symbol", "600489.SH"),
        ("projection_sha256", "g" * 64),
        ("claims_schema_version", 2),
        ("allowed_claim_types", ["NUMERIC_OBSERVATION"]),
        ("allowed_predicates", ["daily_close"]),
        ("response_format", "markdown"),
    ],
)
def test_relay_request_schema_rejects_contract_mutations(
    field: str,
    value: object,
) -> None:
    projection = _projection()
    payload = build_relay_request(
        projection,
        request_id=FIXED_REQUEST_ID,
    ).model_dump(mode="python")
    payload[field] = value

    with pytest.raises(ValidationError):
        RelayRequest.model_validate(payload)


def test_relay_request_and_response_reject_unknown_fields() -> None:
    projection = _projection()
    request_payload = build_relay_request(
        projection,
        request_id=FIXED_REQUEST_ID,
    ).model_dump(mode="python")
    request_payload["endpoint"] = "https://unapproved.invalid"
    response_payload = _relay_response_payload(projection)
    response_payload["analysis"] = "free text"

    with pytest.raises(ValidationError):
        RelayRequest.model_validate(request_payload)
    with pytest.raises(ValidationError):
        RelayResponse.model_validate(response_payload)


def test_relay_models_are_frozen() -> None:
    projection = _projection()
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    response = RelayResponse.model_validate(_relay_response_payload(projection))

    with pytest.raises(ValidationError):
        request.symbol = "600489.SH"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        response.symbol = "600489.SH"  # type: ignore[misc]


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}{}",
        b"[]",
        b"NaN",
        b"{bad}",
        b" {}",
        b'"prose"',
        b"null",
        b"true",
        b'{"value": Infinity}',
        b'{"value": -Infinity}',
    ],
)
def test_single_json_parser_rejects_non_contract_input(raw: bytes) -> None:
    with pytest.raises(Phase2ProviderRelayError):
        parse_single_json_object(raw, maximum_bytes=1_048_576)


def test_single_json_parser_accepts_one_object_and_trailing_whitespace() -> None:
    assert parse_single_json_object(
        b'{"status":"ok"}\n\t',
        maximum_bytes=1_048_576,
    ) == {"status": "ok"}


def test_single_json_parser_enforces_byte_limit_before_decode() -> None:
    prefix = b'{"pad":"'
    suffix = b'"}'
    exact = prefix + (b"x" * (1_048_576 - len(prefix) - len(suffix))) + suffix

    assert len(exact) == 1_048_576
    assert len(parse_single_json_object(exact, maximum_bytes=1_048_576)["pad"]) > 0
    with pytest.raises(Phase2ProviderRelayError, match="response_too_large"):
        parse_single_json_object(exact + b" ", maximum_bytes=1_048_576)


def test_valid_relay_response_returns_unchanged_typed_candidate() -> None:
    projection = _projection()
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    payload = _relay_response_payload(projection)

    candidate = validate_relay_response(payload, request)

    assert candidate.model_dump(mode="json") == payload["claims_candidate"]
    assert len(candidate.claims) == 42


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("request_id", "b" * 32, "relay_response_request_id_mismatch"),
        ("symbol", "600489.SH", "relay_response_schema_invalid"),
        ("projection_sha256", "0" * 64, "relay_response_projection_sha256_mismatch"),
    ],
)
def test_relay_response_rejects_outer_binding_mismatch(
    field: str,
    value: str,
    error_code: str,
) -> None:
    projection = _projection()
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    payload = _relay_response_payload(projection)
    payload[field] = value

    with pytest.raises(Phase2ProviderRelayError, match=error_code):
        validate_relay_response(payload, request)


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("symbol", "600489.SH", "relay_candidate_symbol_mismatch"),
        (
            "projection_sha256",
            "0" * 64,
            "relay_candidate_projection_sha256_mismatch",
        ),
    ],
)
def test_relay_response_rejects_candidate_binding_mismatch(
    field: str,
    value: str,
    error_code: str,
) -> None:
    projection = _projection()
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    payload = _relay_response_payload(projection)
    payload["claims_candidate"][field] = value

    with pytest.raises(Phase2ProviderRelayError, match=error_code):
        validate_relay_response(payload, request)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("analysis", "free text"),
        ("markdown", "# report"),
        ("html", "<p>report</p>"),
        ("xml", "<report />"),
        ("reasoning", "hidden reasoning"),
        ("code_block", "```json"),
        ("link", "https://example.invalid"),
        ("tool_call", {"name": "search"}),
        ("file", "/tmp/output"),
        ("claims_candidates", []),
    ],
)
def test_relay_response_rejects_free_form_or_multiple_candidate_fields(
    field: str,
    value: object,
) -> None:
    projection = _projection()
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    payload = _relay_response_payload(projection)
    payload[field] = value

    with pytest.raises(Phase2ProviderRelayError, match="relay_response_schema_invalid"):
        validate_relay_response(payload, request)


def test_relay_response_rejects_non_finite_candidate_numbers() -> None:
    projection = _projection()
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    payload = _relay_response_payload(projection)
    numeric_claim = next(
        claim
        for claim in payload["claims_candidate"]["claims"]
        if claim["claim_type"] == "NUMERIC_OBSERVATION"
    )
    numeric_claim["object"]["value"] = float("nan")

    with pytest.raises(Phase2ProviderRelayError, match="relay_response_schema_invalid"):
        validate_relay_response(payload, request)


def test_builders_reject_tampered_projection_without_mutating_it() -> None:
    projection = _projection()
    original = deepcopy(projection)
    tampered = deepcopy(projection)
    tampered["safe_facts"]["daily"]["close"] += 0.01

    with pytest.raises(Phase2ProviderRelayError, match="projection_contract_invalid"):
        build_relay_request(tampered, request_id=FIXED_REQUEST_ID)
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    with pytest.raises(Phase2ProviderRelayError, match="projection_contract_invalid"):
        build_provider_envelope(request, tampered)
    assert projection == original


def test_relay_execution_receipt_is_sanitized_and_exact() -> None:
    receipt = RelayExecutionReceipt.model_validate(
        {
            "receipt_schema_version": 1,
            "status": "SUCCEEDED",
            "error_category": None,
            "provider_http_status": 200,
            "provider_attempt_count": 1,
            "retry_count": 0,
            "response_size": 1024,
            "response_sha256": "f" * 64,
            "started_at": "2026-08-01T08:00:00Z",
            "completed_at": "2026-08-01T08:00:01Z",
        }
    )

    assert set(receipt.model_dump(mode="json")) == {
        "receipt_schema_version",
        "status",
        "error_category",
        "provider_http_status",
        "provider_attempt_count",
        "retry_count",
        "response_size",
        "response_sha256",
        "started_at",
        "completed_at",
    }


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "body",
        "headers",
        "endpoint",
        "environment",
        "container_id",
        "host_path",
        "authorization",
        "secret",
    ],
)
def test_relay_execution_receipt_rejects_sensitive_or_runtime_fields(
    forbidden_field: str,
) -> None:
    payload = {
        "receipt_schema_version": 1,
        "status": "REJECTED",
        "error_category": "HTTP_REJECTED",
        "provider_http_status": 500,
        "provider_attempt_count": 1,
        "retry_count": 0,
        "response_size": None,
        "response_sha256": None,
        "started_at": "2026-08-01T08:00:00Z",
        "completed_at": "2026-08-01T08:00:01Z",
        forbidden_field: "forbidden",
    }

    with pytest.raises(ValidationError):
        RelayExecutionReceipt.model_validate(payload)


def _load_python_file(path: Path, prefix: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{prefix}_{uuid.uuid4().hex}",
        path,
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordedHTTPServer(HTTPServer):
    response_status = 200
    response_body = b"{}"
    response_headers: ClassVar[dict[str, str]] = {}
    response_delay = 0.0
    request_count = 0
    requests: ClassVar[list[dict[str, Any]]] = []


class _RecordingHandler(BaseHTTPRequestHandler):
    def _handle(self) -> None:
        server = self.server
        assert isinstance(server, _RecordedHTTPServer)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server.request_count += 1
        server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            }
        )
        if server.response_delay:
            time.sleep(server.response_delay)
        try:
            self.send_response(server.response_status)
            for name, value in server.response_headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(server.response_body)))
            self.end_headers()
            self.wfile.write(server.response_body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_POST = _handle  # noqa: N815
    do_GET = _handle  # noqa: N815

    def log_message(self, _format: str, *args: object) -> None:
        del args


class _ProxyPolicyHandler(_RecordingHandler):
    def _handle(self) -> None:
        server = self.server
        assert isinstance(server, _RecordedHTTPServer)
        if self.command != "POST":
            server.response_status = 405
            server.response_body = b""
        elif self.path != "/v1/typed-claims":
            server.response_status = 404
            server.response_body = b""
        else:
            server.response_status = 200
            server.response_body = canonical_json_bytes(_relay_response_payload())
        super()._handle()

    do_POST = _handle  # noqa: N815
    do_GET = _handle  # noqa: N815


@contextmanager
def _http_server(
    *,
    status: int = 200,
    body: bytes = b"{}",
    headers: dict[str, str] | None = None,
    delay: float = 0.0,
):
    server = _RecordedHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    server.response_status = status
    server.response_body = body
    server.response_headers = dict(headers or {})
    server.response_delay = delay
    server.request_count = 0
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _proxy_policy_server():
    server = _RecordedHTTPServer(("127.0.0.1", 0), _ProxyPolicyHandler)
    server.response_headers = {}
    server.response_delay = 0
    server.request_count = 0
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _configure_relay_files(
    relay: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    response_payload: bytes,
) -> tuple[dict, RelayRequest, Path, Path]:
    projection = _projection()
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    request_path = tmp_path / "request.json"
    projection_path = tmp_path / "projection.json"
    response_path = tmp_path / "response.json"
    receipt_path = tmp_path / "receipt.json"
    request_path.write_bytes(canonical_json_bytes(request.model_dump(mode="json")))
    projection_path.write_bytes(canonical_json_bytes(projection))
    response_path.write_bytes(response_payload)
    receipt_path.write_bytes(b"\n")
    for path in (request_path, projection_path):
        path.chmod(0o444)
    for path in (response_path, receipt_path):
        path.chmod(0o666)
    monkeypatch.setattr(relay, "REQUEST_PATH", request_path)
    monkeypatch.setattr(relay, "PROJECTION_PATH", projection_path)
    monkeypatch.setattr(relay, "RESPONSE_PATH", response_path)
    monkeypatch.setattr(relay, "RECEIPT_PATH", receipt_path)
    return projection, request, response_path, receipt_path


def test_relay_executable_constants_are_fixed() -> None:
    relay = _load_python_file(RELAY_PATH, "phase2_relay")

    assert relay.PROXY_HOST == "phase2-egress-proxy"
    assert relay.PROXY_PORT == 8080
    assert relay.PROXY_PATH == "/v1/typed-claims"
    assert relay.MAXIMUM_BYTES == 1_048_576
    assert relay.TIMEOUT_SECONDS == 2
    assert {"run", "probe"} == relay.ALLOWED_MODES
    assert Path("/input/request.json") == relay.REQUEST_PATH
    assert Path("/input/projection.json") == relay.PROJECTION_PATH
    assert Path("/output/response.json") == relay.RESPONSE_PATH
    assert Path("/output/receipt.json") == relay.RECEIPT_PATH


def test_relay_http_posts_one_canonical_envelope_and_writes_exact_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    relay = _load_python_file(RELAY_PATH, "phase2_relay")
    projection = _projection()
    provider_payload = canonical_json_bytes(_relay_response_payload(projection))
    with _http_server(body=provider_payload) as server:
        projection, request, response_path, receipt_path = _configure_relay_files(
            relay,
            monkeypatch,
            tmp_path,
            response_payload=b"stale partial response",
        )
        monkeypatch.setattr(relay, "PROXY_HOST", "127.0.0.1")
        monkeypatch.setattr(relay, "PROXY_PORT", server.server_port)

        exit_code = relay.run()

    assert exit_code == 0
    assert server.request_count == 1
    recorded = server.requests[0]
    expected = build_provider_envelope(request, projection).model_dump(mode="json")
    assert recorded["method"] == "POST"
    assert recorded["path"] == "/v1/typed-claims"
    assert recorded["body"] == canonical_json_bytes(expected)
    assert recorded["headers"]["Accept"] == "application/json"
    assert recorded["headers"]["Content-Type"] == "application/json"
    assert "Authorization" not in recorded["headers"]
    assert response_path.read_bytes() == provider_payload
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["provider_attempt_count"] == 1
    assert receipt["retry_count"] == 0
    assert receipt["response_size"] == len(provider_payload)
    assert receipt["response_sha256"] == hashlib.sha256(provider_payload).hexdigest()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("status", "body", "headers", "error_category"),
    [
        (302, b"", {"Location": "http://redirect.invalid/"}, "REDIRECT_REJECTED"),
        (429, b'{"error":"rate limited"}', {}, "HTTP_REJECTED"),
        (500, b'{"error":"server"}', {}, "HTTP_REJECTED"),
        (200, b"", {}, "EMPTY_RESPONSE"),
        (200, b"not-json", {}, "STRICT_JSON_REJECTED"),
        (200, b"{}{}", {}, "STRICT_JSON_REJECTED"),
    ],
)
def test_relay_http_rejects_non_contract_response_without_retry_or_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: bytes,
    headers: dict[str, str],
    error_category: str,
) -> None:
    relay = _load_python_file(RELAY_PATH, "phase2_relay")
    with _http_server(status=status, body=body, headers=headers) as server:
        _, _, response_path, receipt_path = _configure_relay_files(
            relay,
            monkeypatch,
            tmp_path,
            response_payload=b"stale partial response",
        )
        monkeypatch.setattr(relay, "PROXY_HOST", "127.0.0.1")
        monkeypatch.setattr(relay, "PROXY_PORT", server.server_port)

        exit_code = relay.run()

    receipt = json.loads(receipt_path.read_bytes())
    assert exit_code != 0
    assert server.request_count == 1
    assert response_path.read_bytes() == b"\n"
    assert receipt["status"] == "REJECTED"
    assert receipt["error_category"] == error_category
    assert receipt["provider_attempt_count"] == 1
    assert receipt["retry_count"] == 0


def test_relay_http_rejects_oversized_response_after_one_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = _load_python_file(RELAY_PATH, "phase2_relay")
    oversized = b"{" + (b"x" * 1_048_576) + b"}"
    with _http_server(body=oversized) as server:
        _, _, response_path, receipt_path = _configure_relay_files(
            relay,
            monkeypatch,
            tmp_path,
            response_payload=b"stale partial response",
        )
        monkeypatch.setattr(relay, "PROXY_HOST", "127.0.0.1")
        monkeypatch.setattr(relay, "PROXY_PORT", server.server_port)

        exit_code = relay.run()

    receipt = json.loads(receipt_path.read_bytes())
    assert exit_code != 0
    assert server.request_count == 1
    assert response_path.read_bytes() == b"\n"
    assert receipt["error_category"] == "RESPONSE_TOO_LARGE"
    assert receipt["retry_count"] == 0


def test_relay_http_timeout_is_terminal_and_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = _load_python_file(RELAY_PATH, "phase2_relay")
    with _http_server(body=b"{}", delay=0.15) as server:
        _, _, response_path, receipt_path = _configure_relay_files(
            relay,
            monkeypatch,
            tmp_path,
            response_payload=b"stale partial response",
        )
        monkeypatch.setattr(relay, "PROXY_HOST", "127.0.0.1")
        monkeypatch.setattr(relay, "PROXY_PORT", server.server_port)
        monkeypatch.setattr(relay, "TIMEOUT_SECONDS", 0.03)

        exit_code = relay.run()

    receipt = json.loads(receipt_path.read_bytes())
    assert exit_code != 0
    assert server.request_count == 1
    assert response_path.read_bytes() == b"\n"
    assert receipt["error_category"] == "TIMEOUT"
    assert receipt["provider_attempt_count"] == 1
    assert receipt["retry_count"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "b" * 32),
        ("projection_sha256", "0" * 64),
        ("symbol", "600489.SH"),
    ],
)
def test_relay_http_rejects_wrong_response_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    relay = _load_python_file(RELAY_PATH, "phase2_relay")
    response = _relay_response_payload()
    response[field] = value
    with _http_server(body=canonical_json_bytes(response)) as server:
        _, _, response_path, receipt_path = _configure_relay_files(
            relay,
            monkeypatch,
            tmp_path,
            response_payload=b"stale partial response",
        )
        monkeypatch.setattr(relay, "PROXY_HOST", "127.0.0.1")
        monkeypatch.setattr(relay, "PROXY_PORT", server.server_port)

        exit_code = relay.run()

    receipt = json.loads(receipt_path.read_bytes())
    assert exit_code != 0
    assert server.request_count == 1
    assert response_path.read_bytes() == b"\n"
    assert receipt["error_category"] == "BINDING_REJECTED"


def test_relay_executable_rejects_input_symlink_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = _load_python_file(RELAY_PATH, "phase2_relay")
    _, _, response_path, receipt_path = _configure_relay_files(
        relay,
        monkeypatch,
        tmp_path,
        response_payload=b"stale partial response",
    )
    real_request = tmp_path / "request.json"
    linked_request = tmp_path / "linked-request.json"
    linked_request.symlink_to(real_request)
    monkeypatch.setattr(relay, "REQUEST_PATH", linked_request)
    monkeypatch.setattr(relay, "PROXY_HOST", "unreachable.invalid")

    exit_code = relay.run()

    receipt = json.loads(receipt_path.read_bytes())
    assert exit_code != 0
    assert response_path.read_bytes() == b"\n"
    assert receipt["error_category"] == "REQUEST_INVALID"
    assert receipt["provider_attempt_count"] == 0


def test_relay_executable_probe_records_only_bounded_policy_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = _load_python_file(RELAY_PATH, "phase2_relay")
    with _proxy_policy_server() as server:
        _, _, response_path, receipt_path = _configure_relay_files(
            relay,
            monkeypatch,
            tmp_path,
            response_payload=b"stale partial response",
        )
        monkeypatch.setattr(relay, "PROXY_HOST", "127.0.0.1")
        monkeypatch.setattr(relay, "PROXY_PORT", server.server_port)
        monkeypatch.setattr(relay, "TIMEOUT_SECONDS", 0.03)
        monkeypatch.setattr(relay, "DIRECT_PROVIDER_HOST", "direct.invalid")
        monkeypatch.setattr(relay, "ARBITRARY_HOST", "arbitrary.invalid")
        monkeypatch.setattr(relay, "HOST_GATEWAY", "host.invalid")
        monkeypatch.setattr(relay, "DOCKER_CONTROL_HOST", "control.invalid")
        monkeypatch.setattr(relay, "DOCKER_CONTROL_PATH", tmp_path / "absent.sock")

        exit_code = relay.probe()

    output = json.loads(response_path.read_bytes())
    receipt = json.loads(receipt_path.read_bytes())
    assert exit_code == 0
    assert output["probe_schema_version"] == 1
    assert set(output["results"]) == {
        "proxy_exact",
        "direct_provider",
        "arbitrary_hostname",
        "public_test_ip",
        "host_gateway",
        "docker_control_file",
        "docker_control_tcp",
        "proxy_wrong_path",
        "proxy_wrong_method",
    }
    assert output["results"]["proxy_exact"] == "ALLOWED"
    assert output["results"]["proxy_wrong_path"] == "BLOCKED"
    assert output["results"]["proxy_wrong_method"] == "BLOCKED"
    assert output["results"]["docker_control_file"] == "BLOCKED"
    assert set(output["results"].values()) <= {
        "ALLOWED",
        "BLOCKED",
        "NOT_REACHABLE",
        "REDIRECT_REJECTED",
    }
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["retry_count"] == 0


def test_relay_executable_uses_only_stdlib_and_hardened_fixed_file_io() -> None:
    source = RELAY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.partition(".")[0])
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    function_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert roots <= sys.stdlib_module_names
    assert {"O_NOFOLLOW", "fstat", "fsync"} <= attributes
    assert "S_ISREG" in attributes
    assert "print" not in function_names
    assert "/input/request.json" in source
    assert "/input/projection.json" in source
    assert "/output/response.json" in source
    assert "/output/receipt.json" in source
    lowered = source.lower()
    assert all(
        forbidden not in lowered
        for forbidden in (
            "authorization",
            "api_key",
            "apikey",
            "credential",
            "password",
            "secret",
        )
    )


def test_relay_dockerfile_is_digest_pinned_non_root_and_shell_less() -> None:
    lines = RELAY_DOCKERFILE.read_text(encoding="utf-8").splitlines()
    source = "\n".join(lines)

    assert lines[0] == f"FROM {PINNED_DISTROLESS}"
    assert "USER 65532:65532" in lines
    assert 'ENTRYPOINT ["/usr/bin/python3", "/relay/relay.py"]' in lines
    assert "CMD [\"run\"]" in lines
    assert sum(line.startswith("COPY ") for line in lines) == 5
    assert all(
        expected in source
        for expected in (
            "relay.py /relay/relay.py",
            "request.placeholder.json /input/request.json",
            "projection.placeholder.json /input/projection.json",
            "response.placeholder.json /output/response.json",
            "receipt.placeholder.json /output/receipt.json",
        )
    )
    forbidden = (
        "RUN ",
        "ADD ",
        ":debug",
        "/bin/sh",
        "/bin/bash",
        " apt",
        "apk ",
        "pip ",
        "curl ",
        "wget ",
        "http://",
        "https://",
        "API_KEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
    )
    assert all(item not in source for item in forbidden)


@pytest.mark.parametrize(
    "name",
    [
        "request.placeholder.json",
        "projection.placeholder.json",
        "response.placeholder.json",
        "receipt.placeholder.json",
    ],
)
def test_relay_image_contains_all_single_file_mount_targets(name: str) -> None:
    path = RELAY_PATH.parent / name

    assert path.is_file()
    assert not path.is_symlink()
