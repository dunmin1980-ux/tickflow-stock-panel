"""Offline capability evidence gate for the exact Ark model."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ArkCapabilityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["PASSED"]
    exact_model_id: Literal["doubao-seed-2-1-turbo-260628"]
    responses_api_supported: Literal[True]
    structured_output_supported: Literal[True]
    approved_mode: Literal["json_schema"]
    fallback_modes: tuple[()] = ()
    model_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    responses_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def validate_ark_capability_evidence_bytes(
    model_raw: bytes,
    responses_raw: bytes,
) -> ArkCapabilityEvidence:
    model_text = model_raw.decode("utf-8")
    responses_text = responses_raw.decode("utf-8")
    required_model = (
        "doubao-seed-2-1-turbo-260628",
        "Responses API\uff1aSUPPORTED",
        "Structured Output\uff1aSUPPORTED",
        "推荐模式\uff1ajson_schema",
    )
    required_responses = (
        "text.format.type",
        "json_schema",
        "text.format.schema",
        "text.format.strict",
    )
    if not all(item in model_text for item in required_model) or not all(
        item in responses_text for item in required_responses
    ):
        raise ValueError("ark_structured_output_capability_not_proven")
    return ArkCapabilityEvidence(
        status="PASSED",
        exact_model_id="doubao-seed-2-1-turbo-260628",
        responses_api_supported=True,
        structured_output_supported=True,
        approved_mode="json_schema",
        model_snapshot_sha256=hashlib.sha256(model_raw).hexdigest(),
        responses_snapshot_sha256=hashlib.sha256(responses_raw).hexdigest(),
    )


def validate_ark_capability_evidence(repo_root: Path) -> ArkCapabilityEvidence:
    root = repo_root.resolve(strict=True)
    model_path = root / "reports/phase2_provider_ark/official_model_capability_snapshot.txt"
    responses_path = (
        root / "reports/phase2_provider_ark/official_responses_json_schema_snapshot.txt"
    )
    return validate_ark_capability_evidence_bytes(
        model_path.read_bytes(),
        responses_path.read_bytes(),
    )
