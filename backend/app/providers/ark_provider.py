"""Fail-closed Volcengine Ark Responses contract and envelope adapter."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.providers.base import ProviderRequest, ProviderResult
from app.schemas.phase2_claims import WorkerClaimsCandidate
from app.services.phase2_isolation_runtime import validate_isolated_candidate

ARK_PROVIDER_ID = "volcengine_ark"
ARK_EXACT_MODEL_ID = "doubao-seed-2-1-turbo-260628"
ARK_ENDPOINT_ALIAS = "ark_responses_cn_beijing_v1"
ARK_RESPONSES_CONTRACT_VERSION = 1
_JSON_CONTENT_TYPE = re.compile(
    r'^application/json(?:\s*;\s*charset\s*=\s*"?utf-8"?)?$',
    re.IGNORECASE,
)


class ArkProviderContractError(ValueError):
    pass


def _canonical_compact(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _strict_provider_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_strict_provider_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"default", "discriminator"}:
            continue
        normalized["anyOf" if key == "oneOf" else key] = _strict_provider_schema(item)
    properties = normalized.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise ArkProviderContractError("ark_claims_schema_invalid")
        normalized["additionalProperties"] = False
        normalized["required"] = list(properties)
    return normalized


def _strict_json_object(text: str) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_key")
            value[key] = item
        return value

    try:
        value, end = json.JSONDecoder(
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        ).raw_decode(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ArkProviderContractError("ark_structured_output_invalid") from exc
    if text[end:].strip() or not isinstance(value, dict):
        raise ArkProviderContractError("ark_structured_output_invalid")
    return value


def _validate_identity(request: ProviderRequest) -> None:
    if (
        request.provider_id != ARK_PROVIDER_ID
        or request.exact_model_id != ARK_EXACT_MODEL_ID
        or request.endpoint_alias != ARK_ENDPOINT_ALIAS
        or request.symbol != "000403.SZ"
    ):
        raise ArkProviderContractError("ark_provider_identity_invalid")


def build_ark_responses_request(request: ProviderRequest) -> dict[str, Any]:
    _validate_identity(request)
    projection = request.minimal_projection
    allowed = {
        "projection_schema_version",
        "projection_sha256",
        "facts_sha256",
        "symbol",
        "name",
        "trade_date",
        "timezone",
        "safe_facts",
        "allowed_predicates",
    }
    if set(projection) != allowed:
        raise ArkProviderContractError("ark_minimal_projection_invalid")
    input_contract = {
        "request_id": request.request_id,
        "symbol": request.symbol,
        "name": projection["name"],
        "trade_date": projection["trade_date"],
        "projection_sha256": request.projection_sha256,
        "facts_sha256": request.facts_sha256,
        "minimal_projection": projection,
    }
    return {
        "model": ARK_EXACT_MODEL_ID,
        "store": False,
        "stream": False,
        "tools": [],
        "temperature": 0,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": _canonical_compact(input_contract),
                    }
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "tickflow_phase2_claims_candidate",
                "strict": True,
                "schema": _strict_provider_schema(request.claims_schema),
            }
        },
    }


def adapt_ark_responses_envelope(
    request: ProviderRequest,
    *,
    provider_http_status: int,
    content_type: str,
    envelope: Mapping[str, Any],
    stage_metadata: Mapping[str, Any],
    repo_root: str | None = None,
) -> ProviderResult:
    _validate_identity(request)
    if (
        provider_http_status != 200
        or not isinstance(content_type, str)
        or _JSON_CONTENT_TYPE.fullmatch(content_type.strip()) is None
        or not isinstance(envelope, Mapping)
        or envelope.get("object") != "response"
        or envelope.get("status") != "completed"
        or envelope.get("model") != ARK_EXACT_MODEL_ID
        or not isinstance(envelope.get("id"), str)
        or not envelope["id"]
    ):
        raise ArkProviderContractError("ark_responses_envelope_invalid")
    outputs = envelope.get("output")
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise ArkProviderContractError("ark_responses_envelope_invalid")
    message = outputs[0]
    if (
        not isinstance(message, Mapping)
        or message.get("type") != "message"
        or message.get("role") != "assistant"
        or message.get("status") != "completed"
    ):
        raise ArkProviderContractError("ark_responses_envelope_invalid")
    content = message.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise ArkProviderContractError("ark_structured_output_missing")
    output_text = content[0]
    if (
        not isinstance(output_text, Mapping)
        or output_text.get("type") != "output_text"
        or not isinstance(output_text.get("text"), str)
        or not output_text["text"]
    ):
        raise ArkProviderContractError("ark_structured_output_missing")
    candidate_value = _strict_json_object(output_text["text"])
    try:
        candidate = WorkerClaimsCandidate.model_validate(candidate_value)
    except ValidationError as exc:
        raise ArkProviderContractError("ark_candidate_schema_invalid") from exc
    if (
        candidate.symbol != request.symbol
        or candidate.projection_sha256 != request.projection_sha256
        or any(claim.provenance.facts_sha256 != request.facts_sha256 for claim in candidate.claims)
    ):
        raise ArkProviderContractError("ark_candidate_binding_invalid")
    if repo_root is not None:
        from pathlib import Path

        evidence = validate_isolated_candidate(
            Path(repo_root),
            candidate.model_dump(mode="json"),
            request.minimal_projection,
        )
        if evidence.errors or evidence.claims_status != "CLAIMS_VALID":
            raise ArkProviderContractError("ark_candidate_host_validation_invalid")
    usage = envelope.get("usage", {})
    if not isinstance(usage, Mapping):
        raise ArkProviderContractError("ark_usage_metadata_invalid")
    return ProviderResult(
        provider_http_status=provider_http_status,
        provider_request_id=envelope["id"],
        raw_envelope_metadata={
            "model": envelope["model"],
            "object": envelope["object"],
            "status": envelope["status"],
        },
        claims_candidate=candidate,
        usage_metadata=dict(usage),
        stage_metadata=dict(stage_metadata),
    )
