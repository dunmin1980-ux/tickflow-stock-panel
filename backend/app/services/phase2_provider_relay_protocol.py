"""Pure, fail-closed contracts for the Phase 2B Provider Relay."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.schemas.phase2_claims import ALLOWED_CLAIM_TYPES, WorkerClaimsCandidate
from app.schemas.phase2_provider_relay import (
    GenerationControls,
    ProviderEnvelope,
    RelayRequest,
    RelayResponse,
)
from app.services.phase2_ai_worker_protocol import _projection_errors
from app.services.phase2_claims_service import PREDICATE_RULES, canonical_json_bytes

FIXED_SYMBOL = "000403.SZ"


class Phase2ProviderRelayError(ValueError):
    """Raised with a stable code when a Relay boundary fails closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require_projection(projection: Mapping[str, Any]) -> None:
    try:
        errors = _projection_errors(projection)
        canonical_json_bytes(projection)
    except (TypeError, ValueError) as exc:
        raise Phase2ProviderRelayError("projection_contract_invalid") from exc
    if errors or projection.get("symbol") != FIXED_SYMBOL:
        raise Phase2ProviderRelayError("projection_contract_invalid")


def build_relay_request(
    projection: Mapping[str, Any],
    *,
    request_id: str,
) -> RelayRequest:
    """Build the only Host-to-Relay request accepted by this phase."""
    _require_projection(projection)
    try:
        return RelayRequest(
            protocol_version=1,
            request_id=request_id,
            symbol=FIXED_SYMBOL,
            projection_sha256=str(projection["projection_sha256"]),
            claims_schema_version=1,
            allowed_claim_types=tuple(sorted(ALLOWED_CLAIM_TYPES)),
            allowed_predicates=tuple(sorted(PREDICATE_RULES)),
            response_format="typed_claims_json",
        )
    except (KeyError, ValidationError) as exc:
        raise Phase2ProviderRelayError("relay_request_invalid") from exc


def build_provider_envelope(
    request: RelayRequest,
    projection: Mapping[str, Any],
) -> ProviderEnvelope:
    """Bind one exact request to one minimal, validated Projection."""
    _require_projection(projection)
    if (
        request.symbol != projection.get("symbol")
        or request.projection_sha256 != projection.get("projection_sha256")
        or request.allowed_claim_types != tuple(sorted(ALLOWED_CLAIM_TYPES))
        or request.allowed_predicates != tuple(sorted(PREDICATE_RULES))
    ):
        raise Phase2ProviderRelayError("provider_envelope_binding_invalid")
    try:
        return ProviderEnvelope(
            request=request,
            projection=dict(projection),
            generation=GenerationControls(),
        )
    except ValidationError as exc:
        raise Phase2ProviderRelayError("provider_envelope_invalid") from exc


def parse_single_json_object(raw: bytes, *, maximum_bytes: int) -> dict[str, Any]:
    """Decode exactly one JSON object with no leading or trailing prose."""
    if not isinstance(raw, bytes) or maximum_bytes < 2:
        raise Phase2ProviderRelayError("strict_json_invalid")
    if len(raw) > maximum_bytes:
        raise Phase2ProviderRelayError("response_too_large")
    if not raw:
        raise Phase2ProviderRelayError("strict_json_invalid")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON constant")

    try:
        text = raw.decode("utf-8")
        decoder = json.JSONDecoder(parse_constant=reject_constant)
        value, end = decoder.raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Phase2ProviderRelayError("strict_json_invalid") from exc
    if text[end:].strip() or not isinstance(value, dict):
        raise Phase2ProviderRelayError("strict_json_invalid")
    return value


def validate_relay_response(
    value: Mapping[str, Any] | RelayResponse,
    request: RelayRequest,
) -> WorkerClaimsCandidate:
    """Validate response shape and request binding without changing Candidate."""
    try:
        response = (
            value
            if isinstance(value, RelayResponse)
            else RelayResponse.model_validate(value)
        )
    except ValidationError as exc:
        raise Phase2ProviderRelayError("relay_response_schema_invalid") from exc
    if response.request_id != request.request_id:
        raise Phase2ProviderRelayError("relay_response_request_id_mismatch")
    if response.symbol != request.symbol:
        raise Phase2ProviderRelayError("relay_response_symbol_mismatch")
    if response.projection_sha256 != request.projection_sha256:
        raise Phase2ProviderRelayError(
            "relay_response_projection_sha256_mismatch"
        )
    candidate = response.claims_candidate
    if candidate.symbol != request.symbol:
        raise Phase2ProviderRelayError("relay_candidate_symbol_mismatch")
    if candidate.projection_sha256 != request.projection_sha256:
        raise Phase2ProviderRelayError(
            "relay_candidate_projection_sha256_mismatch"
        )
    return candidate
