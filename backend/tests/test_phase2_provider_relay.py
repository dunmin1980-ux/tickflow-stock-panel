from __future__ import annotations

import ast
import hashlib
import http.client
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
    WORKER_CANDIDATE_VALID,
    FakeClaimsWorker,
    build_worker_projection,
    validate_worker_candidate,
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
PROXY_PATH = REPO_ROOT / "docker/phase2-egress-proxy/proxy.py"
PROXY_DOCKERFILE = REPO_ROOT / "docker/phase2-egress-proxy/Dockerfile"
MOCK_PROVIDER_PATH = REPO_ROOT / "docker/phase2-mock-provider/mock_provider.py"
MOCK_PROVIDER_DOCKERFILE = REPO_ROOT / "docker/phase2-mock-provider/Dockerfile"
WORKER_PATH = REPO_ROOT / "docker/phase2-ai-worker/worker.py"
PINNED_DISTROLESS = (
    "gcr.io/distroless/python3-debian12@sha256:"
    "7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)
MOCK_SCENARIOS = {
    "valid_typed_candidate",
    "markdown_instead_of_json",
    "extra_free_text_field",
    "unknown_claim_type",
    "unknown_predicate",
    "wrong_symbol",
    "wrong_projection_sha",
    "raw_qfq_mismatch",
    "unsupported_fact_pointer",
    "trading_claim",
    "oversized_response",
    "timeout",
    "http_429",
    "http_500",
    "invalid_json",
    "multiple_json_documents",
    "empty_response",
    "wrong_request_id",
    "wrong_facts_sha",
    "sensitive_shape",
    "redirect_response",
    "external_url_response",
}


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


@contextmanager
def _running_component_server(server: HTTPServer):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _http_exchange(
    port: int,
    *,
    method: str = "POST",
    path: str = "/v1/typed-claims",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 2,
) -> tuple[int, bytes, dict[str, str]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def _component_files(tmp_path: Path) -> tuple[Path, Path, str]:
    auth_value = uuid.uuid4().hex + uuid.uuid4().hex
    auth_path = tmp_path / "provider-auth"
    receipt_path = tmp_path / "receipt.json"
    auth_path.write_text(auth_value + "\n", encoding="utf-8")
    receipt_path.write_bytes(b"\n")
    auth_path.chmod(0o400)
    receipt_path.chmod(0o666)
    return auth_path, receipt_path, auth_value


def test_proxy_policy_constants_are_fixed() -> None:
    proxy = _load_python_file(PROXY_PATH, "phase2_proxy")

    assert proxy.ALLOWED_METHOD == "POST"
    assert proxy.ALLOWED_PATH == "/v1/typed-claims"
    assert proxy.UPSTREAM_HOST == "phase2-mock-provider"
    assert proxy.UPSTREAM_PORT == 8081
    assert proxy.UPSTREAM_PATH == "/v1/typed-claims"
    assert proxy.RETRY_COUNT == 0
    assert proxy.FOLLOW_REDIRECTS is False
    assert proxy.MAXIMUM_BYTES == 1_048_576
    assert proxy.TIMEOUT_SECONDS == 2


def test_proxy_forwards_one_exact_request_and_injects_auth_only_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    proxy = _load_python_file(PROXY_PATH, "phase2_proxy")
    auth_path, receipt_path, auth_value = _component_files(tmp_path)
    upstream_body = canonical_json_bytes(_relay_response_payload())
    with _http_server(body=upstream_body) as upstream:
        monkeypatch.setattr(proxy, "UPSTREAM_HOST", "127.0.0.1")
        monkeypatch.setattr(proxy, "UPSTREAM_PORT", upstream.server_port)
        monkeypatch.setattr(proxy, "AUTH_PATH", auth_path)
        monkeypatch.setattr(proxy, "RECEIPT_PATH", receipt_path)
        server = proxy.create_server(("127.0.0.1", 0))
        with _running_component_server(server):
            request_body = canonical_json_bytes(
                build_provider_envelope(
                    build_relay_request(_projection(), request_id=FIXED_REQUEST_ID),
                    _projection(),
                ).model_dump(mode="json")
            )
            status, body, headers = _http_exchange(
                server.server_port,
                body=request_body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

    assert status == 200
    assert body == upstream_body
    assert headers["Content-Type"] == "application/json"
    assert upstream.request_count == 1
    assert upstream.requests[0]["method"] == "POST"
    assert upstream.requests[0]["path"] == "/v1/typed-claims"
    assert upstream.requests[0]["body"] == request_body
    assert upstream.requests[0]["headers"]["Authorization"] == f"Bearer {auth_value}"
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    assert auth_value.encode() not in receipt_bytes
    assert receipt == {
        "receipt_schema_version": 1,
        "method_allowed": True,
        "path_allowed": True,
        "upstream_attempt_count": 1,
        "response_category": "FORWARDED",
        "response_size": len(upstream_body),
        "auth_present": True,
    }
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("method", "path", "expected_status"),
    [
        ("GET", "/v1/typed-claims", 405),
        ("POST", "/blocked", 404),
        ("PUT", "/blocked", 405),
    ],
)
def test_proxy_rejects_wrong_method_or_path_before_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    expected_status: int,
) -> None:
    proxy = _load_python_file(PROXY_PATH, "phase2_proxy")
    auth_path, receipt_path, _ = _component_files(tmp_path)
    with _http_server() as upstream:
        monkeypatch.setattr(proxy, "UPSTREAM_HOST", "127.0.0.1")
        monkeypatch.setattr(proxy, "UPSTREAM_PORT", upstream.server_port)
        monkeypatch.setattr(proxy, "AUTH_PATH", auth_path)
        monkeypatch.setattr(proxy, "RECEIPT_PATH", receipt_path)
        server = proxy.create_server(("127.0.0.1", 0))
        with _running_component_server(server):
            status, body, _ = _http_exchange(
                server.server_port,
                method=method,
                path=path,
                body=b"{}",
            )

    assert status == expected_status
    assert body == b""
    assert upstream.request_count == 0
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["upstream_attempt_count"] == 0


@pytest.mark.parametrize(
    ("upstream_status", "headers", "expected_status", "category"),
    [
        (302, {"Location": "http://redirect.invalid/"}, 502, "REDIRECT_REJECTED"),
        (429, {}, 429, "UPSTREAM_REJECTED"),
        (500, {}, 500, "UPSTREAM_REJECTED"),
    ],
)
def test_proxy_never_follows_redirect_and_passes_rejected_status_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    upstream_status: int,
    headers: dict[str, str],
    expected_status: int,
    category: str,
) -> None:
    proxy = _load_python_file(PROXY_PATH, "phase2_proxy")
    auth_path, receipt_path, _ = _component_files(tmp_path)
    with _http_server(
        status=upstream_status,
        body=b'{"ignored":true}',
        headers=headers,
    ) as upstream:
        monkeypatch.setattr(proxy, "UPSTREAM_HOST", "127.0.0.1")
        monkeypatch.setattr(proxy, "UPSTREAM_PORT", upstream.server_port)
        monkeypatch.setattr(proxy, "AUTH_PATH", auth_path)
        monkeypatch.setattr(proxy, "RECEIPT_PATH", receipt_path)
        server = proxy.create_server(("127.0.0.1", 0))
        with _running_component_server(server):
            status, body, response_headers = _http_exchange(
                server.server_port,
                body=b"{}",
            )

    assert status == expected_status
    assert body == b""
    assert "Location" not in response_headers
    assert upstream.request_count == 1
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["response_category"] == category
    assert receipt["upstream_attempt_count"] == 1


def test_proxy_timeout_is_terminal_and_has_no_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = _load_python_file(PROXY_PATH, "phase2_proxy")
    auth_path, receipt_path, _ = _component_files(tmp_path)
    with _http_server(delay=0.15) as upstream:
        monkeypatch.setattr(proxy, "UPSTREAM_HOST", "127.0.0.1")
        monkeypatch.setattr(proxy, "UPSTREAM_PORT", upstream.server_port)
        monkeypatch.setattr(proxy, "AUTH_PATH", auth_path)
        monkeypatch.setattr(proxy, "RECEIPT_PATH", receipt_path)
        monkeypatch.setattr(proxy, "TIMEOUT_SECONDS", 0.03)
        server = proxy.create_server(("127.0.0.1", 0))
        with _running_component_server(server):
            status, body, _ = _http_exchange(server.server_port, body=b"{}")

    assert status == 504
    assert body == b""
    assert upstream.request_count == 1
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["response_category"] == "TIMEOUT"
    assert receipt["upstream_attempt_count"] == 1


@pytest.mark.parametrize("declared_size", [0, 1_048_577])
def test_proxy_rejects_invalid_input_size_before_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declared_size: int,
) -> None:
    proxy = _load_python_file(PROXY_PATH, "phase2_proxy")
    auth_path, receipt_path, _ = _component_files(tmp_path)
    with _http_server() as upstream:
        monkeypatch.setattr(proxy, "UPSTREAM_HOST", "127.0.0.1")
        monkeypatch.setattr(proxy, "UPSTREAM_PORT", upstream.server_port)
        monkeypatch.setattr(proxy, "AUTH_PATH", auth_path)
        monkeypatch.setattr(proxy, "RECEIPT_PATH", receipt_path)
        server = proxy.create_server(("127.0.0.1", 0))
        with _running_component_server(server):
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=2,
            )
            connection.putrequest("POST", "/v1/typed-claims")
            connection.putheader("Content-Length", str(declared_size))
            connection.endheaders()
            response = connection.getresponse()
            status = response.status
            response.read()
            connection.close()

    assert status == 413
    assert upstream.request_count == 0


def _provider_envelope() -> dict:
    projection = _projection()
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    return build_provider_envelope(request, projection).model_dump(mode="json")


def _scenario_accepted(raw_result: dict, projection: dict) -> bool:
    status = raw_result["status"]
    body = raw_result["body"]
    if (
        not 200 <= status < 300
        or raw_result["delay_seconds"] > 2
        or not body
        or len(body) > 1_048_576
    ):
        return False
    try:
        parsed = parse_single_json_object(body, maximum_bytes=1_048_576)
        candidate = validate_relay_response(
            parsed,
            build_relay_request(projection, request_id=FIXED_REQUEST_ID),
        )
    except (Phase2ProviderRelayError, ValidationError):
        return False
    return (
        validate_worker_candidate(
            candidate.model_dump(mode="json"),
            projection,
        ).status
        == WORKER_CANDIDATE_VALID
    )


def test_mock_provider_constants_and_scenario_set_are_fixed() -> None:
    provider = _load_python_file(MOCK_PROVIDER_PATH, "phase2_mock_provider")

    assert provider.ALLOWED_METHOD == "POST"
    assert provider.ALLOWED_PATH == "/v1/typed-claims"
    assert provider.MAXIMUM_BYTES == 1_048_576
    assert MOCK_SCENARIOS == provider.ALLOWED_SCENARIOS


@pytest.mark.parametrize("scenario", sorted(MOCK_SCENARIOS))
def test_mock_provider_matrix_accepts_only_valid_typed_candidate(
    scenario: str,
) -> None:
    provider = _load_python_file(MOCK_PROVIDER_PATH, "phase2_mock_provider")
    factory = _load_python_file(WORKER_PATH, "phase2_candidate_factory")
    envelope = _provider_envelope()
    projection = envelope["projection"]

    result = provider.build_scenario_response(
        envelope,
        scenario,
        factory.generate_candidate,
    )

    assert set(result) == {"status", "body", "headers", "delay_seconds"}
    assert _scenario_accepted(result, projection) is (
        scenario == "valid_typed_candidate"
    )
    if scenario == "valid_typed_candidate":
        parsed = json.loads(result["body"])
        assert len(parsed["claims_candidate"]["claims"]) == 42


def test_mock_provider_valid_response_is_byte_deterministic() -> None:
    provider = _load_python_file(MOCK_PROVIDER_PATH, "phase2_mock_provider")
    factory = _load_python_file(WORKER_PATH, "phase2_candidate_factory")
    envelope = _provider_envelope()

    first = provider.build_scenario_response(
        envelope,
        "valid_typed_candidate",
        factory.generate_candidate,
    )
    second = provider.build_scenario_response(
        deepcopy(envelope),
        "valid_typed_candidate",
        factory.generate_candidate,
    )

    assert first == second


def _leaf_differences(left: Any, right: Any, path: str = "") -> set[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        differences: set[str] = set()
        for key in set(left) | set(right):
            child = f"{path}/{key}"
            if key not in left or key not in right:
                differences.add(child)
            else:
                differences.update(_leaf_differences(left[key], right[key], child))
        return differences
    if isinstance(left, list) and isinstance(right, list):
        differences = set()
        if len(left) != len(right):
            differences.add(f"{path}/length")
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=False)):
            differences.update(
                _leaf_differences(left_item, right_item, f"{path}/{index}")
            )
        return differences
    return set() if left == right else {path}


@pytest.mark.parametrize(
    ("scenario", "expected_difference"),
    [
        ("extra_free_text_field", "/claims_candidate/analysis"),
        ("unknown_claim_type", "/claims_candidate/claims/0/claim_type"),
        ("unknown_predicate", "/claims_candidate/claims/0/predicate"),
        ("wrong_symbol", "/symbol"),
        ("wrong_projection_sha", "/projection_sha256"),
        (
            "raw_qfq_mismatch",
            "/claims_candidate/claims/39/object/right/price_basis",
        ),
        (
            "unsupported_fact_pointer",
            "/claims_candidate/claims/0/provenance/fact_refs/0",
        ),
        ("trading_claim", "/claims_candidate/trading_advice"),
        ("wrong_request_id", "/request_id"),
        (
            "wrong_facts_sha",
            "/claims_candidate/claims/0/provenance/facts_sha256",
        ),
        ("sensitive_shape", "/claims_candidate/api_key"),
        ("external_url_response", "/external_url"),
    ],
)
def test_mock_provider_structured_scenarios_mutate_only_one_leaf(
    scenario: str,
    expected_difference: str,
) -> None:
    provider = _load_python_file(MOCK_PROVIDER_PATH, "phase2_mock_provider")
    factory = _load_python_file(WORKER_PATH, "phase2_candidate_factory")
    envelope = _provider_envelope()
    valid = provider.build_scenario_response(
        envelope,
        "valid_typed_candidate",
        factory.generate_candidate,
    )
    mutated = provider.build_scenario_response(
        envelope,
        scenario,
        factory.generate_candidate,
    )

    assert _leaf_differences(
        json.loads(valid["body"]),
        json.loads(mutated["body"]),
    ) == {expected_difference}


def test_mock_provider_http_requires_matching_auth_and_exact_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = _load_python_file(MOCK_PROVIDER_PATH, "phase2_mock_provider")
    auth_path, receipt_path, auth_value = _component_files(tmp_path)
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_bytes(
        canonical_json_bytes({"scenario": "valid_typed_candidate"})
    )
    scenario_path.chmod(0o444)
    monkeypatch.setattr(provider, "AUTH_PATH", auth_path)
    monkeypatch.setattr(provider, "SCENARIO_PATH", scenario_path)
    monkeypatch.setattr(provider, "RECEIPT_PATH", receipt_path)
    monkeypatch.setattr(provider, "FACTORY_PATH", WORKER_PATH)
    server = provider.create_server(("127.0.0.1", 0))
    with _running_component_server(server):
        status, body, _ = _http_exchange(
            server.server_port,
            body=canonical_json_bytes(_provider_envelope()),
            headers={
                "Authorization": f"Bearer {auth_value}",
                "Content-Type": "application/json",
            },
        )

    assert status == 200
    assert len(json.loads(body)["claims_candidate"]["claims"]) == 42
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    assert auth_value.encode() not in receipt_bytes
    assert receipt["auth_valid"] is True
    assert receipt["request_count"] == 1
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("auth_header", [None, "Bearer wrong"])
def test_mock_provider_http_rejects_missing_or_wrong_auth_without_body_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    auth_header: str | None,
) -> None:
    provider = _load_python_file(MOCK_PROVIDER_PATH, "phase2_mock_provider")
    auth_path, receipt_path, _ = _component_files(tmp_path)
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_bytes(
        canonical_json_bytes({"scenario": "valid_typed_candidate"})
    )
    scenario_path.chmod(0o444)
    monkeypatch.setattr(provider, "AUTH_PATH", auth_path)
    monkeypatch.setattr(provider, "SCENARIO_PATH", scenario_path)
    monkeypatch.setattr(provider, "RECEIPT_PATH", receipt_path)
    monkeypatch.setattr(provider, "FACTORY_PATH", WORKER_PATH)
    server = provider.create_server(("127.0.0.1", 0))
    headers = {"Content-Type": "application/json"}
    if auth_header is not None:
        headers["Authorization"] = auth_header
    with _running_component_server(server):
        status, body, _ = _http_exchange(
            server.server_port,
            body=canonical_json_bytes(_provider_envelope()),
            headers=headers,
        )

    assert status == 401
    assert body == b""
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["auth_valid"] is False


def test_mock_provider_timeout_client_disconnect_produces_no_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = _load_python_file(MOCK_PROVIDER_PATH, "phase2_mock_provider")
    auth_path, receipt_path, auth_value = _component_files(tmp_path)
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_bytes(canonical_json_bytes({"scenario": "timeout"}))
    scenario_path.chmod(0o444)
    monkeypatch.setattr(provider, "AUTH_PATH", auth_path)
    monkeypatch.setattr(provider, "SCENARIO_PATH", scenario_path)
    monkeypatch.setattr(provider, "RECEIPT_PATH", receipt_path)
    monkeypatch.setattr(provider, "FACTORY_PATH", WORKER_PATH)
    monkeypatch.setattr(provider, "TIMEOUT_DELAY_SECONDS", 0.15)
    server = provider.create_server(("127.0.0.1", 0))
    with _running_component_server(server):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=0.03,
        )
        connection.request(
            "POST",
            "/v1/typed-claims",
            body=canonical_json_bytes(_provider_envelope()),
            headers={"Authorization": f"Bearer {auth_value}"},
        )
        with pytest.raises(TimeoutError):
            connection.getresponse()
        connection.close()
        time.sleep(0.2)

    assert json.loads(receipt_path.read_bytes())["scenario"] == "timeout"
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("path", "copy_count", "entrypoint"),
    [
        (PROXY_DOCKERFILE, 3, "/proxy/proxy.py"),
        (MOCK_PROVIDER_DOCKERFILE, 5, "/app/mock_provider.py"),
    ],
)
def test_proxy_and_mock_dockerfiles_are_pinned_non_root_and_minimal(
    path: Path,
    copy_count: int,
    entrypoint: str,
) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    source = "\n".join(lines)

    assert lines[0] == f"FROM {PINNED_DISTROLESS}"
    assert "USER 65532:65532" in lines
    assert f'ENTRYPOINT ["/usr/bin/python3", "{entrypoint}"]' in lines
    assert sum(line.startswith("COPY ") for line in lines) == copy_count
    assert all(
        item not in source
        for item in (
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
        )
    )


@pytest.mark.parametrize("path", [PROXY_PATH, MOCK_PROVIDER_PATH])
def test_proxy_and_mock_use_only_stdlib_and_hardened_file_io(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
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


def test_mock_dockerfile_copies_only_approved_factory_and_component_files() -> None:
    source = MOCK_PROVIDER_DOCKERFILE.read_text(encoding="utf-8")
    copy_lines = [line for line in source.splitlines() if line.startswith("COPY ")]

    assert copy_lines == [
        "COPY --chown=65532:65532 phase2-ai-worker/worker.py /app/candidate_factory.py",
        "COPY --chown=65532:65532 phase2-mock-provider/mock_provider.py /app/mock_provider.py",
        "COPY --chown=65532:65532 phase2-mock-provider/secret.placeholder /run/phase2/provider-auth",
        "COPY --chown=65532:65532 phase2-mock-provider/scenario.placeholder.json /input/scenario.json",
        "COPY --chown=65532:65532 phase2-mock-provider/receipt.placeholder.json /output/receipt.json",
    ]
