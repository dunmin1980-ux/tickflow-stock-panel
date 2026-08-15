"""Minimal one-shot Host path for the approved Ark typed-Claims Canary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.providers.ark_provider import (
    ARK_ENDPOINT_ALIAS,
    ARK_EXACT_MODEL_ID,
    ARK_PROVIDER_ID,
    build_ark_responses_request,
)
from app.providers.base import ProviderRequest
from app.schemas.phase2_claims import WorkerClaimsCandidate
from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_claims_service import canonical_json_bytes

EXPECTED_FACTS_SHA256 = (
    "adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956"
)
EXPECTED_PROJECTION_SHA256 = (
    "0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f"
)
EXPECTED_SYMBOL = "000403.SZ"
EXPECTED_TRADE_DATE = "2026-07-31"
REQUEST_CONTRACT_PATH = Path(__file__).with_name(
    "phase2_minimal_ark_request_contract.json"
)
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class MinimalArkCanaryError(RuntimeError):
    """Fail-closed error for the minimal Ark Canary path."""


class MinimalArkRequestContract(BaseModel):
    """Closed runtime contract for the direct Host HTTPS request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract_version: Literal[1]
    provider_id: Literal["volcengine_ark"]
    exact_model_id: Literal["doubao-seed-2-1-turbo-260628"]
    scheme: Literal["https"]
    host: Literal["ark.cn-beijing.volces.com"]
    port: Literal[443]
    path: Literal["/api/v3/responses"]
    method: Literal["POST"]
    tls_verification: Literal[True]
    hostname_verification: Literal[True]
    tls_minimum: Literal["TLSv1_2"]
    follow_redirects: Literal[False]
    trust_env: Literal[False]
    connect_timeout_seconds: Literal[10]
    read_timeout_seconds: Literal[180]
    write_timeout_seconds: Literal[180]
    maximum_response_bytes: Literal[1_048_576]
    stream: Literal[False]
    store: Literal[False]
    tools: tuple[()]
    strict_json_schema: Literal[True]
    retry_count: Literal[0]
    maximum_attempts: Literal[1]
    can_publish: Literal[False]
    trade_date: Literal["2026-07-31"]

    @field_validator("tools", mode="before")
    @classmethod
    def require_empty_tools(cls, value: Any) -> tuple[()]:
        if value not in ([], ()):
            raise ValueError("tools_forbidden")
        return ()

    @property
    def endpoint(self) -> str:
        return f"{self.scheme}://{self.host}{self.path}"


@dataclass(frozen=True)
class MinimalExecutionInputs:
    provider_id: str
    exact_model_id: str
    endpoint_alias: str
    endpoint: str
    request_id: str
    symbol: str
    trade_date: str
    facts_sha256: str
    projection_sha256: str
    prompt_sha256: str
    projection: dict[str, Any]
    claims_schema: dict[str, Any]
    request_body: dict[str, Any]
    contract: MinimalArkRequestContract


def _read_regular_bytes(path: Path, error_code: str) -> bytes:
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise MinimalArkCanaryError(error_code)
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
        try:
            before = os.fstat(descriptor)
            raw = b""
            while len(raw) <= 8 * 1024 * 1024:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                raw += chunk
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise MinimalArkCanaryError(error_code) from exc
    if (
        len(raw) > 8 * 1024 * 1024
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(raw) != after.st_size
    ):
        raise MinimalArkCanaryError(error_code)
    return raw


def load_minimal_request_contract(
    path: Path = REQUEST_CONTRACT_PATH,
) -> MinimalArkRequestContract:
    try:
        value = json.loads(_read_regular_bytes(path, "request_contract_invalid"))
        contract = MinimalArkRequestContract.model_validate(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        MinimalArkCanaryError,
    ) as exc:
        raise MinimalArkCanaryError("request_contract_invalid") from exc
    if list(contract.tools):
        raise MinimalArkCanaryError("request_contract_invalid")
    return contract


def _provider_request(
    *,
    request_id: str,
    projection: dict[str, Any],
    claims_schema: dict[str, Any],
) -> ProviderRequest:
    return ProviderRequest(
        provider_id=ARK_PROVIDER_ID,
        exact_model_id=ARK_EXACT_MODEL_ID,
        endpoint_alias=ARK_ENDPOINT_ALIAS,
        request_id=request_id,
        symbol=EXPECTED_SYMBOL,
        projection_sha256=projection["projection_sha256"],
        facts_sha256=projection["facts_sha256"],
        claims_schema=claims_schema,
        minimal_projection=projection,
    )


def build_minimal_execution_inputs(
    repo_root: Path,
    request_id: str,
) -> MinimalExecutionInputs:
    if _REQUEST_ID.fullmatch(request_id) is None:
        raise MinimalArkCanaryError("request_id_invalid")
    contract = load_minimal_request_contract()
    facts_path = repo_root / "reports/phase2_facts/000403SZ_facts.json"
    facts_bytes = _read_regular_bytes(facts_path, "facts_file_invalid")
    facts_sha256 = hashlib.sha256(facts_bytes).hexdigest()
    if facts_sha256 != EXPECTED_FACTS_SHA256:
        raise MinimalArkCanaryError("facts_identity_mismatch")
    try:
        facts = json.loads(facts_bytes)
        projection = build_worker_projection(
            facts,
            facts_sha256,
            facts_bytes=facts_bytes,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise MinimalArkCanaryError("projection_build_failed") from exc
    if (
        projection.get("projection_sha256") != EXPECTED_PROJECTION_SHA256
        or projection.get("facts_sha256") != EXPECTED_FACTS_SHA256
        or projection.get("symbol") != EXPECTED_SYMBOL
        or projection.get("trade_date") != EXPECTED_TRADE_DATE
    ):
        raise MinimalArkCanaryError("projection_identity_mismatch")
    claims_schema = WorkerClaimsCandidate.model_json_schema(mode="validation")
    request = _provider_request(
        request_id=request_id,
        projection=projection,
        claims_schema=claims_schema,
    )
    request_body = build_ark_responses_request(request)
    placeholder = _provider_request(
        request_id="0" * 32,
        projection=projection,
        claims_schema=claims_schema,
    )
    prompt_sha256 = hashlib.sha256(
        canonical_json_bytes(build_ark_responses_request(placeholder)["input"])
    ).hexdigest()
    inputs = MinimalExecutionInputs(
        provider_id=ARK_PROVIDER_ID,
        exact_model_id=ARK_EXACT_MODEL_ID,
        endpoint_alias=ARK_ENDPOINT_ALIAS,
        endpoint=contract.endpoint,
        request_id=request_id,
        symbol=EXPECTED_SYMBOL,
        trade_date=EXPECTED_TRADE_DATE,
        facts_sha256=facts_sha256,
        projection_sha256=projection["projection_sha256"],
        prompt_sha256=prompt_sha256,
        projection=projection,
        claims_schema=claims_schema,
        request_body=request_body,
        contract=contract,
    )
    validate_minimal_execution_inputs(inputs)
    return inputs


def validate_minimal_execution_inputs(inputs: MinimalExecutionInputs) -> None:
    fmt = inputs.request_body.get("text", {}).get("format", {})
    if (
        inputs.provider_id != ARK_PROVIDER_ID
        or inputs.exact_model_id != ARK_EXACT_MODEL_ID
        or inputs.endpoint_alias != ARK_ENDPOINT_ALIAS
        or inputs.endpoint != inputs.contract.endpoint
        or inputs.symbol != EXPECTED_SYMBOL
        or inputs.trade_date != EXPECTED_TRADE_DATE
        or inputs.facts_sha256 != EXPECTED_FACTS_SHA256
    ):
        raise MinimalArkCanaryError("execution_identity_mismatch")
    if inputs.projection_sha256 != EXPECTED_PROJECTION_SHA256:
        raise MinimalArkCanaryError("projection_identity_mismatch")
    if (
        inputs.projection.get("projection_sha256") != inputs.projection_sha256
        or inputs.projection.get("facts_sha256") != inputs.facts_sha256
        or inputs.request_body.get("model") != ARK_EXACT_MODEL_ID
        or inputs.request_body.get("stream") is not False
        or inputs.request_body.get("store") is not False
        or inputs.request_body.get("tools") != []
        or fmt.get("type") != "json_schema"
        or fmt.get("strict") is not True
        or inputs.contract.retry_count != 0
        or inputs.contract.maximum_attempts != 1
        or inputs.contract.can_publish is not False
    ):
        raise MinimalArkCanaryError("request_contract_invalid")
