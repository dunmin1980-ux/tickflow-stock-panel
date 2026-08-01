"""Closed contracts for the offline Phase 2B Provider Canary preflight."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.phase2_claims import WorkerClaimsCandidate


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CanaryProviderApproval(_StrictFrozenModel):
    config_version: Literal[1]
    provider_id: Literal["openai"]
    exact_model_id: Literal["gpt-5.6-terra"]
    approved_endpoint_alias: Literal["openai_responses_v1"]
    endpoint_host: Literal["api.openai.com"]
    endpoint_port: Literal[443]
    endpoint_path: Literal["/v1/responses"]
    http_method: Literal["POST"]
    tls_verification: Literal[True]
    follow_redirects: Literal[False]
    response_mode: Literal["json_schema_strict"]
    streaming: Literal[False]
    tools_enabled: Literal[False]
    web_browsing_enabled: Literal[False]
    file_tools_enabled: Literal[False]
    retry_count: Literal[0]
    max_provider_attempts: Literal[1]
    approved_symbol: Literal["000403.SZ"]
    secret_source: Literal["macos_keychain"]
    secret_service: Literal["tickflow-phase2-canary-openai"]
    can_publish: Literal[False]


class CanaryEgressPolicy(_StrictFrozenModel):
    scheme: Literal["https"] = "https"
    host: Literal["api.openai.com"] = "api.openai.com"
    port: Literal[443] = 443
    path: Literal["/v1/responses"] = "/v1/responses"
    method: Literal["POST"] = "POST"
    tls_verification: Literal[True] = True
    follow_redirects: Literal[False] = False
    allow_response_url_navigation: Literal[False] = False
    allow_arbitrary_public_ip: Literal[False] = False
    allow_host_access: Literal[False] = False
    allow_docker_control: Literal[False] = False

    @classmethod
    def from_approval(
        cls,
        approval: CanaryProviderApproval,
    ) -> CanaryEgressPolicy:
        return cls(
            host=approval.endpoint_host,
            port=approval.endpoint_port,
            path=approval.endpoint_path,
            method=approval.http_method,
            tls_verification=approval.tls_verification,
            follow_redirects=approval.follow_redirects,
        )


def _provider_strict_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_provider_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"default", "discriminator"}:
            continue
        target_key = "anyOf" if key == "oneOf" else key
        normalized[target_key] = _provider_strict_schema(item)
    if "properties" in normalized:
        properties = normalized["properties"]
        if not isinstance(properties, dict):
            raise ValueError("responses_schema_properties_invalid")
        normalized["additionalProperties"] = False
        normalized["required"] = list(properties)
    return normalized


def build_responses_request_contract(
    approval: CanaryProviderApproval,
) -> dict[str, Any]:
    """Build a network-free OpenAI Responses structured-output contract."""
    candidate_schema = WorkerClaimsCandidate.model_json_schema(mode="validation")
    strict_schema = _provider_strict_schema(candidate_schema)
    return {
        "model": approval.exact_model_id,
        "stream": approval.streaming,
        "tools": [],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "tickflow_phase2_claims_candidate",
                "strict": True,
                "schema": strict_schema,
            }
        },
    }


def validate_exact_egress(
    policy: CanaryEgressPolicy,
    *,
    scheme: str,
    host: str,
    port: int,
    path: str,
    method: str,
    tls_verification: bool,
    follow_redirects: bool,
) -> bool:
    """Return true only for the single approved transport tuple."""
    return (
        scheme == policy.scheme
        and host == policy.host
        and type(port) is int
        and port == policy.port
        and path == policy.path
        and method == policy.method
        and tls_verification is policy.tls_verification
        and follow_redirects is policy.follow_redirects
    )
