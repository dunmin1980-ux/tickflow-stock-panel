"""Deterministic contract artifact for the dedicated OpenAI Canary proxy."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    field_serializer,
    field_validator,
)

from app.schemas.phase2_canary_provider_config import (
    CanaryProviderApproval,
    build_responses_request_contract,
)
from app.schemas.phase2_claims import ALLOWED_CLAIM_TYPES
from app.schemas.phase2_provider_relay import GenerationControls
from app.services.phase2_claims_service import PREDICATE_RULES

_CONTRACT_PATH = Path(
    "docker/phase2-openai-egress-proxy/responses-contract.json"
)
_FIXED_INSTRUCTIONS = (
    "Use only supplied projection facts and pointers.",
    "Return exactly one schema-valid candidate object.",
    "Do not provide trading advice, predictions, news, or financial assertions.",
    "Do not emit markdown, prose, URLs, tool calls, or file references.",
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OpenAIProxyPolicy(_StrictFrozenModel):
    """Closed policy for the only Provider call a later Canary may make."""

    provider_id: Literal["openai"] = "openai"
    model_id: Literal["gpt-5.6-terra"] = "gpt-5.6-terra"
    endpoint_host: Literal["api.openai.com"] = "api.openai.com"
    endpoint_port: Literal[443] = 443
    endpoint_path: Literal["/v1/responses"] = "/v1/responses"
    http_method: Literal["POST"] = "POST"
    tls_verification: Literal[True] = True
    minimum_tls_version: Literal["TLSv1_2"] = "TLSv1_2"
    follow_redirects: Literal[False] = False
    stream: Literal[False] = False
    tools: tuple[Any, ...] = ()
    retry_count: Literal[0] = 0
    maximum_attempts: Literal[1] = 1
    timeout_seconds: Literal[60] = 60
    maximum_bytes: Literal[1_048_576] = 1_048_576
    approved_symbol: Literal["000403.SZ"] = "000403.SZ"

    @field_validator("tools", mode="before")
    @classmethod
    def require_empty_tools(cls, value: Any) -> tuple[()]:
        if value not in ((), []):
            raise ValueError("proxy tools must remain empty")
        return ()

    @field_serializer("tools")
    def serialize_tools(self, value: tuple[Any, ...]) -> list[Any]:
        if value:
            raise ValueError("proxy tools must remain empty")
        return []


def _provider_approval() -> CanaryProviderApproval:
    return CanaryProviderApproval(
        config_version=1,
        provider_id="openai",
        exact_model_id="gpt-5.6-terra",
        approved_endpoint_alias="openai_responses_v1",
        endpoint_host="api.openai.com",
        endpoint_port=443,
        endpoint_path="/v1/responses",
        http_method="POST",
        tls_verification=True,
        follow_redirects=False,
        response_mode="json_schema_strict",
        streaming=False,
        tools_enabled=False,
        web_browsing_enabled=False,
        file_tools_enabled=False,
        retry_count=0,
        max_provider_attempts=1,
        approved_symbol="000403.SZ",
        secret_source="macos_keychain",
        secret_service="tickflow-phase2-canary-openai",
        can_publish=False,
    )


def strict_worker_schema() -> dict[str, Any]:
    """Return the existing Host-generated strict Candidate schema."""
    request = build_responses_request_contract(_provider_approval())
    schema = request["text"]["format"]["schema"]
    if not isinstance(schema, dict):
        raise ValueError("proxy_contract_schema_invalid")
    return schema


def canonical_contract_payload(value: Mapping[str, Any]) -> bytes:
    """Serialize one contract value deterministically without non-finite data."""
    if not isinstance(value, Mapping):
        raise TypeError("proxy_contract_must_be_mapping")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _relay_contract() -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "claims_schema_version": 1,
        "response_format": "typed_claims_json",
        "approved_symbol": "000403.SZ",
        "approved_trade_date": "2026-07-31",
        "allowed_claim_types": sorted(ALLOWED_CLAIM_TYPES),
        "allowed_predicates": sorted(PREDICATE_RULES),
        "generation": GenerationControls().model_dump(mode="json"),
    }


def _contract_without_hash() -> dict[str, Any]:
    response_format = build_responses_request_contract(_provider_approval())[
        "text"
    ]["format"]
    return {
        "contract_schema_version": 1,
        "policy": OpenAIProxyPolicy().model_dump(mode="json"),
        "relay_contract": _relay_contract(),
        "fixed_instructions": list(_FIXED_INSTRUCTIONS),
        "response_format": response_format,
    }


def build_proxy_contract() -> dict[str, Any]:
    value = _contract_without_hash()
    value["contract_sha256"] = hashlib.sha256(
        canonical_contract_payload(value)
    ).hexdigest()
    return value


def canonical_proxy_contract_bytes() -> bytes:
    return canonical_contract_payload(build_proxy_contract())


def proxy_policy_sha256() -> str:
    payload = canonical_contract_payload(
        OpenAIProxyPolicy().model_dump(mode="json")
    )
    return hashlib.sha256(payload).hexdigest()


def validate_proxy_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("proxy_contract_not_object")
    if set(value) != {
        "contract_schema_version",
        "policy",
        "relay_contract",
        "fixed_instructions",
        "response_format",
        "contract_sha256",
    }:
        raise ValueError("proxy_contract_fields_invalid")
    expected = build_proxy_contract()
    digest = value.get("contract_sha256")
    if not isinstance(digest, str):
        raise ValueError("proxy_contract_sha256_invalid")
    without_hash = dict(value)
    without_hash.pop("contract_sha256")
    actual = hashlib.sha256(canonical_contract_payload(without_hash)).hexdigest()
    if not hmac.compare_digest(digest, actual):
        raise ValueError("proxy_contract_sha256_mismatch")
    if value != expected:
        raise ValueError("proxy_contract_content_mismatch")
    return dict(value)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _assert_no_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("proxy_contract_symlink_forbidden")


def _ensure_target_directory(path: Path) -> None:
    parent = path.parent
    _assert_no_symlink_components(parent.parent)
    if not parent.exists():
        os.mkdir(parent, 0o755)
    _assert_no_symlink_components(parent)
    if not parent.is_dir():
        raise ValueError("proxy_contract_parent_invalid")


def _atomic_write(path: Path, payload: bytes, *, mode: int) -> None:
    _ensure_target_directory(path)
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise ValueError("proxy_contract_target_invalid")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _read_regular(path: Path) -> bytes | None:
    _assert_no_symlink_components(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read(len(canonical_proxy_contract_bytes()) + 1)
    finally:
        os.close(descriptor)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate or check the dedicated OpenAI proxy contract."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--write", action="store_true")
    modes.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    target = repo_root() / _CONTRACT_PATH
    expected = canonical_proxy_contract_bytes()
    if args.write:
        try:
            _atomic_write(target, expected, mode=0o644)
        except (OSError, ValueError):
            return 2
        return 0
    try:
        current = _read_regular(target)
    except ValueError:
        return 2
    return 0 if current == expected else 2


if __name__ == "__main__":
    raise SystemExit(main())
