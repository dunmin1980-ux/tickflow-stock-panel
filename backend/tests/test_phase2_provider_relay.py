from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

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
from app.services.phase2_claims_service import PREDICATE_RULES
from app.services.phase2_provider_relay_protocol import (
    Phase2ProviderRelayError,
    build_provider_envelope,
    build_relay_request,
    parse_single_json_object,
    validate_relay_response,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED_REQUEST_ID = "a" * 32


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
