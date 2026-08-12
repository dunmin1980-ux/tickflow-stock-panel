"""Provider-neutral boundary for Phase 2 typed Claims generation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.phase2_claims import WorkerClaimsCandidate


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProviderRequest(_StrictFrozenModel):
    provider_id: str = Field(min_length=1, max_length=64)
    exact_model_id: str = Field(min_length=1, max_length=128)
    endpoint_alias: str = Field(min_length=1, max_length=128)
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    symbol: str = Field(pattern=r"^\d{6}\.(?:SZ|SH|BJ)$")
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims_schema: dict[str, Any]
    minimal_projection: dict[str, Any]

    @model_validator(mode="after")
    def require_projection_binding(self) -> ProviderRequest:
        projection = self.minimal_projection
        if (
            projection.get("symbol") != self.symbol
            or projection.get("projection_sha256") != self.projection_sha256
            or projection.get("facts_sha256") != self.facts_sha256
        ):
            raise ValueError("provider_projection_binding_invalid")
        return self


class ProviderResult(_StrictFrozenModel):
    provider_http_status: int = Field(ge=100, le=599)
    provider_request_id: str = Field(min_length=1, max_length=128)
    raw_envelope_metadata: dict[str, Any]
    claims_candidate: WorkerClaimsCandidate
    usage_metadata: dict[str, Any]
    stage_metadata: dict[str, Any]
