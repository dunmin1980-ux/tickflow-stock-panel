"""Exact outbound policy for the Volcengine Ark Responses adapter."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator


class ArkProxyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    policy_version: Literal[1] = 1
    provider_id: Literal["volcengine_ark"] = "volcengine_ark"
    exact_model_id: Literal["doubao-seed-2-1-turbo-260628"] = "doubao-seed-2-1-turbo-260628"
    endpoint_alias: Literal["ark_responses_cn_beijing_v1"] = "ark_responses_cn_beijing_v1"
    scheme: Literal["https"] = "https"
    host: Literal["ark.cn-beijing.volces.com"] = "ark.cn-beijing.volces.com"
    port: Literal[443] = 443
    path: Literal["/api/v3/responses"] = "/api/v3/responses"
    method: Literal["POST"] = "POST"
    tls_verification: Literal[True] = True
    minimum_tls_version: Literal["TLSv1_2"] = "TLSv1_2"
    follow_redirects: Literal[False] = False
    stream: Literal[False] = False
    store: Literal[False] = False
    tools: tuple[Any, ...] = ()
    retry_count: Literal[0] = 0
    maximum_provider_attempts: Literal[1] = 1
    maximum_response_bytes: Literal[1_048_576] = 1_048_576

    @field_validator("tools", mode="before")
    @classmethod
    def require_no_tools(cls, value: Any) -> tuple[()]:
        if value not in ((), []):
            raise ValueError("ark_tools_forbidden")
        return ()

    @field_serializer("tools")
    def serialize_tools(self, value: tuple[Any, ...]) -> list[Any]:
        if value:
            raise ValueError("ark_tools_forbidden")
        return []


def validate_ark_egress(
    policy: ArkProxyPolicy,
    *,
    scheme: str,
    host: str,
    port: int,
    path: str,
    method: str,
    tls_verification: bool,
    follow_redirects: bool,
) -> bool:
    return (
        isinstance(policy, ArkProxyPolicy)
        and scheme == policy.scheme
        and host == policy.host
        and type(port) is int
        and port == policy.port
        and path == policy.path
        and method == policy.method
        and tls_verification is policy.tls_verification
        and follow_redirects is policy.follow_redirects
    )
