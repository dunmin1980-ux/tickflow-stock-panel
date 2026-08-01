"""Closed schemas for the Phase 2B isolated Provider Relay boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.phase2_claims import ALLOWED_CLAIM_TYPES, WorkerClaimsCandidate

_RFC3339 = (
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_SHA256 = r"^[0-9a-f]{64}$"

RelayErrorCategory = Literal[
    "REQUEST_INVALID",
    "PROJECTION_INVALID",
    "HTTP_REJECTED",
    "REDIRECT_REJECTED",
    "TIMEOUT",
    "EMPTY_RESPONSE",
    "RESPONSE_TOO_LARGE",
    "STRICT_JSON_REJECTED",
    "BINDING_REJECTED",
    "OUTPUT_WRITE_FAILED",
    "PROBE_FAILED",
    "INTERNAL_ERROR",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GenerationControls(_StrictFrozenModel):
    temperature: Literal[0] = 0
    response_format: Literal["typed_claims_json"] = "typed_claims_json"
    tool_use: Literal[False] = False
    web_browsing: Literal[False] = False
    file_tools: Literal[False] = False
    function_calling: Literal[False] = False
    streaming: Literal[False] = False
    retry_count: Literal[0] = 0
    maximum_output_bytes: Literal[1_048_576] = 1_048_576
    timeout_seconds: Literal[2] = 2


class RelayRequest(_StrictFrozenModel):
    protocol_version: Literal[1]
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    symbol: Literal["000403.SZ"]
    projection_sha256: str = Field(pattern=_SHA256)
    claims_schema_version: Literal[1]
    allowed_claim_types: tuple[str, ...]
    allowed_predicates: tuple[str, ...]
    response_format: Literal["typed_claims_json"]

    @field_validator("allowed_claim_types")
    @classmethod
    def validate_claim_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(ALLOWED_CLAIM_TYPES)):
            raise ValueError("claim type allowlist must be canonical")
        return value

    @field_validator("allowed_predicates")
    @classmethod
    def validate_predicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        from app.services.phase2_claims_service import PREDICATE_RULES

        if value != tuple(sorted(PREDICATE_RULES)):
            raise ValueError("predicate allowlist must be canonical")
        return value


class ProviderEnvelope(_StrictFrozenModel):
    request: RelayRequest
    projection: dict[str, Any]
    generation: GenerationControls


class RelayResponse(_StrictFrozenModel):
    protocol_version: Literal[1]
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    symbol: Literal["000403.SZ"]
    projection_sha256: str = Field(pattern=_SHA256)
    claims_candidate: WorkerClaimsCandidate


class RelayExecutionReceipt(_StrictFrozenModel):
    receipt_schema_version: Literal[1]
    status: Literal["SUCCEEDED", "REJECTED"]
    error_category: RelayErrorCategory | None
    provider_http_status: int | None = Field(default=None, ge=100, le=599)
    provider_attempt_count: Literal[0, 1]
    retry_count: Literal[0]
    response_size: int | None = Field(default=None, ge=0, le=1_048_576)
    response_sha256: str | None = Field(default=None, pattern=_SHA256)
    started_at: str = Field(pattern=_RFC3339)
    completed_at: str = Field(pattern=_RFC3339)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> RelayExecutionReceipt:
        started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(self.completed_at.replace("Z", "+00:00"))
        if completed < started:
            raise ValueError("receipt completion precedes start")
        if self.status == "SUCCEEDED":
            if (
                self.error_category is not None
                or self.provider_http_status is None
                or not 200 <= self.provider_http_status < 300
                or self.provider_attempt_count != 1
                or self.response_size is None
                or self.response_sha256 is None
            ):
                raise ValueError("successful receipt is incomplete")
        elif self.error_category is None:
            raise ValueError("rejected receipt requires an error category")
        return self
