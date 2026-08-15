"""Offline approval artifacts for the minimal direct Ark Canary path."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.services.phase2_claims_service import (
    build_claims_document,
    canonical_json_bytes,
)
from app.services.phase2_minimal_ark_canary import (
    EXPECTED_FACTS_SHA256,
    EXPECTED_PROJECTION_SHA256,
    ArkHostTransport,
    build_minimal_execution_inputs,
    execute_minimal_ark_canary,
)

_SHA256 = "0123456789abcdef"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_REQUEST_ID_BYTES = re.compile(rb"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
_SOURCE_PATHS = {
    "ark_adapter": "backend/app/providers/ark_provider.py",
    "claims_renderer": "backend/app/services/phase2_claims_renderer.py",
    "claims_schema": "backend/app/schemas/phase2_claims.py",
    "claims_validator": "backend/app/services/phase2_claims_service.py",
    "minimal_approval": "backend/app/services/phase2_minimal_ark_approval.py",
    "minimal_builder": "backend/scripts/build_phase2_minimal_ark_canary_offline.py",
    "minimal_launcher": "backend/scripts/run_phase2_minimal_ark_canary.py",
    "minimal_service": "backend/app/services/phase2_minimal_ark_canary.py",
    "request_contract": (
        "backend/app/services/phase2_minimal_ark_request_contract.json"
    ),
    "worker_protocol": "backend/app/services/phase2_ai_worker_protocol.py",
}
_HISTORICAL_ROOTS = (
    "reports/phase2_provider_ark/proxy_tls_differential",
    "reports/phase2_provider_ark/proxy_tls_probe",
    "reports/phase2_provider_ark/superseded",
    "reports/phase2_provider_ark/tls_connectivity_probe",
    "reports/phase2_provider_ark/tls_probe_v2",
    "reports/phase2_provider_ark/tls_receipt_transport",
    "reports/phase2_provider_canary",
    "reports/phase2_provider_relay",
)
_HISTORICAL_FILES = (
    "reports/phase2_provider_ark/approval_candidate.json",
    "reports/phase2_provider_ark/approval_scope_preflight.json",
    "reports/phase2_provider_ark/ark_build_provenance.json",
    "reports/phase2_provider_ark/artifact_generation.json",
    "reports/phase2_provider_ark/new_canary_history_binding.json",
    "reports/phase2_provider_ark/timeout_contract_history_baseline.json",
    "reports/tickflow_phase2b_ark_candidate_source_bindings_eval.md",
    "reports/tickflow_phase2b_ark_proxy_tls_differential_eval.md",
    "reports/tickflow_phase2b_ark_timeout_contract_eval.md",
)


class MinimalArkApprovalError(RuntimeError):
    """Fail-closed error for offline Candidate and Scope preparation."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MinimalExecutionContract(_StrictFrozenModel):
    can_publish: Literal[False]
    maximum_attempts: Literal[1]
    retry_count: Literal[0]
    store: Literal[False]
    stream: Literal[False]
    strict_json_schema: Literal[True]
    tools: tuple[()]

    @field_validator("tools", mode="before")
    @classmethod
    def require_empty_tools(cls, value: Any) -> tuple[()]:
        if value not in ([], ()):
            raise ValueError("tools_forbidden")
        return ()


class MinimalArtifactHashes(_StrictFrozenModel):
    claims_schema_sha256: str
    facts_sha256: str
    historical_evidence_manifest_sha256: str
    mock_e2e_sha256: str
    projection_sha256: str
    prompt_sha256: str
    request_contract_sha256: str
    request_history_registry_sha256: str

    @field_validator("*")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in _SHA256 for character in value):
            raise ValueError("sha256_invalid")
        return value


class MinimalArkCandidate(_StrictFrozenModel):
    candidate_version: Literal[1]
    candidate_type: Literal["phase2b_minimal_ark_ai_canary"]
    source_git_head: str
    provider_id: Literal["volcengine_ark"]
    exact_model_id: Literal["doubao-seed-2-1-turbo-260628"]
    endpoint_alias: Literal["ark_responses_cn_beijing_v1"]
    endpoint: Literal["https://ark.cn-beijing.volces.com/api/v3/responses"]
    symbol: Literal["000403.SZ"]
    trade_date: Literal["2026-07-31"]
    artifact_hashes: MinimalArtifactHashes
    source_bindings: dict[str, str]
    execution_contract: MinimalExecutionContract
    manual_review: Literal["PENDING"]
    three_symbol_batch: Literal["NOT_APPROVED"]

    @field_validator("source_git_head")
    @classmethod
    def require_git_head(cls, value: str) -> str:
        if len(value) != 40 or any(character not in _SHA256 for character in value):
            raise ValueError("git_head_invalid")
        return value

    @field_validator("source_bindings")
    @classmethod
    def require_source_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != set(_SOURCE_PATHS):
            raise ValueError("source_bindings_invalid")
        if any(
            len(item) != 64 or any(character not in _SHA256 for character in item)
            for item in value.values()
        ):
            raise ValueError("source_binding_sha_invalid")
        return value


class MinimalArkScope(_StrictFrozenModel):
    scope_version: Literal[1]
    scope_type: Literal["phase2b_minimal_ark_ai_canary"]
    scope_id: str
    approval_candidate_sha256: str
    provider_id: Literal["volcengine_ark"]
    exact_model_id: Literal["doubao-seed-2-1-turbo-260628"]
    symbol: Literal["000403.SZ"]
    trade_date: Literal["2026-07-31"]
    historical_attempts: int
    availability: Literal["AVAILABLE", "BLOCKED"]
    maximum_attempts: Literal[1]
    retry_count: Literal[0]

    @field_validator("scope_id", "approval_candidate_sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in _SHA256 for character in value):
            raise ValueError("sha256_invalid")
        return value


def _regular_bytes(path: Path, error_code: str) -> bytes:
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise MinimalArkApprovalError(error_code)
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
        try:
            before = os.fstat(descriptor)
            chunks: list[bytes] = []
            total = 0
            while total <= 16 * 1024 * 1024:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise MinimalArkApprovalError(error_code) from exc
    raw = b"".join(chunks)
    if (
        total > 16 * 1024 * 1024
        or metadata.st_dev != before.st_dev
        or metadata.st_ino != before.st_ino
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(raw) != after.st_size
    ):
        raise MinimalArkApprovalError(error_code)
    return raw


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _source_bindings(repo_root: Path) -> dict[str, str]:
    return {
        name: _sha256(
            _regular_bytes(repo_root / relative, "source_binding_file_invalid")
        )
        for name, relative in sorted(_SOURCE_PATHS.items())
    }


def build_historical_evidence_manifest(repo_root: Path) -> dict[str, Any]:
    files: set[Path] = set()
    for relative in _HISTORICAL_ROOTS:
        root = repo_root / relative
        if root.is_symlink() or not root.is_dir():
            raise MinimalArkApprovalError("historical_evidence_root_invalid")
        files.update(path for path in root.rglob("*") if path.is_file())
    files.update(repo_root / relative for relative in _HISTORICAL_FILES)
    entries: list[dict[str, Any]] = []
    for path in sorted(files):
        raw = _regular_bytes(path, "historical_evidence_file_invalid")
        entries.append(
            {
                "path": path.relative_to(repo_root).as_posix(),
                "sha256": _sha256(raw),
                "size_bytes": len(raw),
            }
        )
    if len(entries) < 40:
        raise MinimalArkApprovalError("historical_evidence_incomplete")
    return {
        "historical_evidence_manifest_version": 1,
        "status": "FROZEN_PRESERVED",
        "file_count": len(entries),
        "files": entries,
    }


def verify_historical_evidence_manifest(
    repo_root: Path,
    manifest: Any,
) -> list[str]:
    try:
        expected = build_historical_evidence_manifest(repo_root)
    except MinimalArkApprovalError:
        return ["historical_evidence_invalid"]
    if manifest != expected:
        return ["historical_evidence_mutated"]
    return []


def build_historical_request_id_registry(
    repo_root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    if verify_historical_evidence_manifest(repo_root, manifest):
        raise MinimalArkApprovalError("historical_evidence_invalid")
    request_ids: set[str] = set()
    for item in manifest["files"]:
        raw = _regular_bytes(
            repo_root / item["path"],
            "historical_evidence_file_invalid",
        )
        request_ids.update(
            match.group().decode("ascii")
            for match in _REQUEST_ID_BYTES.finditer(raw)
        )
    if not request_ids:
        raise MinimalArkApprovalError("historical_request_registry_empty")
    return {
        "request_history_registry_version": 1,
        "status": "FROZEN_PRESERVED",
        "source_manifest_sha256": _canonical_sha256(manifest),
        "request_id_count": len(request_ids),
        "request_ids": sorted(request_ids),
    }


def validate_historical_request_id_registry(
    repo_root: Path,
    registry: Any,
    manifest: dict[str, Any],
) -> list[str]:
    try:
        expected = build_historical_request_id_registry(repo_root, manifest)
    except MinimalArkApprovalError:
        return ["historical_request_registry_invalid"]
    if registry != expected:
        return ["historical_request_registry_mutated"]
    return []


def validate_request_id_available(
    request_id: str,
    registry: dict[str, Any],
    attempts_root: Path,
) -> None:
    if _REQUEST_ID.fullmatch(request_id) is None:
        raise MinimalArkApprovalError("request_id_invalid")
    request_ids = registry.get("request_ids")
    if (
        registry.get("request_history_registry_version") != 1
        or registry.get("status") != "FROZEN_PRESERVED"
        or not isinstance(request_ids, list)
        or request_ids != sorted(set(request_ids))
        or registry.get("request_id_count") != len(request_ids)
        or any(
            not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None
            for value in request_ids
        )
    ):
        raise MinimalArkApprovalError("historical_request_registry_invalid")
    if request_id in request_ids:
        raise MinimalArkApprovalError("request_id_reused")
    if attempts_root.is_symlink():
        raise MinimalArkApprovalError("minimal_attempt_history_invalid")
    if not attempts_root.exists():
        return
    if not attempts_root.is_dir():
        raise MinimalArkApprovalError("minimal_attempt_history_invalid")
    for ledger_path in attempts_root.rglob("ledger.json"):
        try:
            metadata = os.lstat(ledger_path)
            ledger = json.loads(
                _regular_bytes(ledger_path, "minimal_attempt_history_invalid")
            )
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
            raise MinimalArkApprovalError(
                "minimal_attempt_history_invalid"
            ) from exc
        if stat.S_IMODE(metadata.st_mode) != 0o600 or not isinstance(ledger, dict):
            raise MinimalArkApprovalError("minimal_attempt_history_invalid")
        if ledger.get("request_id") == request_id:
            raise MinimalArkApprovalError("request_id_reused")


def build_minimal_candidate(
    repo_root: Path,
    *,
    source_git_head: str,
    historical_evidence_manifest_sha256: str,
    mock_e2e_sha256: str,
    request_history_registry_sha256: str,
) -> dict[str, Any]:
    inputs = build_minimal_execution_inputs(repo_root, "0" * 32)
    claims_schema_sha256 = _canonical_sha256(inputs.claims_schema)
    request_contract_path = repo_root / _SOURCE_PATHS["request_contract"]
    value = {
        "candidate_version": 1,
        "candidate_type": "phase2b_minimal_ark_ai_canary",
        "source_git_head": source_git_head,
        "provider_id": inputs.provider_id,
        "exact_model_id": inputs.exact_model_id,
        "endpoint_alias": inputs.endpoint_alias,
        "endpoint": inputs.endpoint,
        "symbol": inputs.symbol,
        "trade_date": inputs.trade_date,
        "artifact_hashes": {
            "claims_schema_sha256": claims_schema_sha256,
            "facts_sha256": EXPECTED_FACTS_SHA256,
            "historical_evidence_manifest_sha256": (
                historical_evidence_manifest_sha256
            ),
            "mock_e2e_sha256": mock_e2e_sha256,
            "projection_sha256": EXPECTED_PROJECTION_SHA256,
            "prompt_sha256": inputs.prompt_sha256,
            "request_contract_sha256": _sha256(
                _regular_bytes(request_contract_path, "request_contract_invalid")
            ),
            "request_history_registry_sha256": (
                request_history_registry_sha256
            ),
        },
        "source_bindings": _source_bindings(repo_root),
        "execution_contract": {
            "can_publish": False,
            "maximum_attempts": 1,
            "retry_count": 0,
            "store": False,
            "stream": False,
            "strict_json_schema": True,
            "tools": [],
        },
        "manual_review": "PENDING",
        "three_symbol_batch": "NOT_APPROVED",
    }
    try:
        return MinimalArkCandidate.model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        raise MinimalArkApprovalError("candidate_schema_invalid") from exc


def validate_minimal_candidate(
    candidate: Any,
    repo_root: Path,
    *,
    expected_git_head: str,
    historical_evidence_manifest_sha256: str,
    mock_e2e_sha256: str,
    request_history_registry_sha256: str,
) -> list[str]:
    try:
        parsed = MinimalArkCandidate.model_validate(candidate)
        expected = build_minimal_candidate(
            repo_root,
            source_git_head=expected_git_head,
            historical_evidence_manifest_sha256=(
                historical_evidence_manifest_sha256
            ),
            mock_e2e_sha256=mock_e2e_sha256,
            request_history_registry_sha256=request_history_registry_sha256,
        )
    except (ValidationError, MinimalArkApprovalError):
        return ["candidate_schema_invalid"]
    if parsed.model_dump(mode="json") != expected:
        return ["candidate_identity_mismatch"]
    return []


def _scope_identity(candidate_sha256: str) -> dict[str, Any]:
    return {
        "approval_candidate_sha256": candidate_sha256,
        "exact_model_id": "doubao-seed-2-1-turbo-260628",
        "provider_id": "volcengine_ark",
        "scope_type": "phase2b_minimal_ark_ai_canary",
        "symbol": "000403.SZ",
        "trade_date": "2026-07-31",
    }


def build_minimal_scope(
    candidate_sha256: str,
    attempts_root: Path,
) -> dict[str, Any]:
    identity = _scope_identity(candidate_sha256)
    scope_id = _canonical_sha256(identity)
    scope_path = attempts_root / scope_id
    try:
        os.lstat(scope_path)
        historical_attempts = 1
    except FileNotFoundError:
        historical_attempts = 0
    except OSError as exc:
        raise MinimalArkApprovalError("scope_state_invalid") from exc
    value = {
        "scope_version": 1,
        "scope_type": identity["scope_type"],
        "scope_id": scope_id,
        "approval_candidate_sha256": candidate_sha256,
        "provider_id": identity["provider_id"],
        "exact_model_id": identity["exact_model_id"],
        "symbol": identity["symbol"],
        "trade_date": identity["trade_date"],
        "historical_attempts": historical_attempts,
        "availability": "AVAILABLE" if historical_attempts == 0 else "BLOCKED",
        "maximum_attempts": 1,
        "retry_count": 0,
    }
    try:
        return MinimalArkScope.model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        raise MinimalArkApprovalError("scope_schema_invalid") from exc


def validate_minimal_scope(
    scope: Any,
    candidate_sha256: str,
    attempts_root: Path,
) -> list[str]:
    try:
        parsed = MinimalArkScope.model_validate(scope)
        expected = build_minimal_scope(candidate_sha256, attempts_root)
    except (ValidationError, MinimalArkApprovalError):
        return ["scope_schema_invalid"]
    if parsed.model_dump(mode="json") != expected:
        return ["scope_identity_mismatch"]
    if parsed.availability != "AVAILABLE" or parsed.historical_attempts != 0:
        return ["scope_not_available"]
    return []


def _mock_candidate(repo_root: Path) -> dict[str, Any]:
    document = build_claims_document(repo_root, "000403.SZ")
    return {
        "candidate_schema_version": 1,
        "projection_sha256": EXPECTED_PROJECTION_SHA256,
        "symbol": document.symbol,
        "name": document.name,
        "trade_date": document.trade_date,
        "timezone": document.timezone,
        "claims": [claim.model_dump(mode="json") for claim in document.claims],
        "trading_advice": False,
    }


def _mock_envelope(candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    output = candidate if candidate is not None else {}
    return {
        "id": "ark_minimal_offline_mock",
        "object": "response",
        "status": "completed",
        "model": "doubao-seed-2-1-turbo-260628",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            output,
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _mock_run(
    repo_root: Path,
    runtime_root: Path,
    *,
    label: str,
    handler: Any,
) -> tuple[dict[str, Any], int]:
    calls = 0

    def counted(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return handler(request)

    request_id = hashlib.sha256(label.encode()).hexdigest()[:32]
    scope_id = _sha256(f"minimal-mock:{label}".encode())
    times = iter(
        (
            "2026-08-16T00:00:00Z",
            "2026-08-16T00:00:01Z",
            "2026-08-16T00:00:02Z",
        )
    )
    result = execute_minimal_ark_canary(
        repo_root=repo_root,
        attempts_root=runtime_root / "attempts",
        scope_id=scope_id,
        candidate_sha256="c" * 64,
        request_id=request_id,
        transport=ArkHostTransport(mock_transport=httpx.MockTransport(counted)),
        secret_reader=lambda: "PHASE2_OFFLINE_MOCK_SENTINEL",
        request_id_guard=lambda _request_id: None,
        clock=lambda: next(times),
    )
    return (
        {
            "case": label,
            "status": result.status,
            "provider_attempt_count": result.attempt_count,
            "provider_http_status": result.provider_http_status,
            "candidate_validation_state": result.candidate_validation_state,
            "claims_validation_state": result.claims_validation_state,
            "renderer_validation_state": result.renderer_validation_state,
            "claim_count": result.claim_count,
            "claims_candidate_sha256": result.claims_candidate_sha256,
            "rendered_sha256": result.rendered_sha256,
            "can_publish": result.can_publish,
        },
        calls,
    )


def run_minimal_mock_e2e(repo_root: Path, runtime_root: Path) -> dict[str, Any]:
    if runtime_root.exists() or runtime_root.is_symlink():
        raise MinimalArkApprovalError("mock_runtime_not_clean")
    happy: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    call_counts: list[int] = []
    candidate = _mock_candidate(repo_root)
    invalid_claims = json.loads(canonical_json_bytes(candidate))
    invalid_claims["claims"][0]["provenance"]["fact_refs"] = ["/not/approved"]

    def success(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mock_envelope(candidate))

    def http_error(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("offline mock timeout", request=request)

    def malformed(_request: httpx.Request) -> httpx.Response:
        envelope = _mock_envelope(candidate)
        envelope["output"][0]["content"][0]["text"] = "not-json"
        return httpx.Response(200, json=envelope)

    def claims_error(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mock_envelope(invalid_claims))

    try:
        for index in range(3):
            result, calls = _mock_run(
                repo_root,
                runtime_root,
                label=f"happy-{index + 1}",
                handler=success,
            )
            happy.append(result)
            call_counts.append(calls)
        for label, handler in (
            ("http-error", http_error),
            ("timeout", timeout),
            ("candidate-error", malformed),
            ("claims-error", claims_error),
        ):
            result, calls = _mock_run(
                repo_root,
                runtime_root,
                label=label,
                handler=handler,
            )
            failures.append(result)
            call_counts.append(calls)
        secret_hits = 0
        for path in runtime_root.rglob("*"):
            if path.is_file() and b"PHASE2_OFFLINE_MOCK_SENTINEL" in path.read_bytes():
                secret_hits += 1
    finally:
        shutil.rmtree(runtime_root, ignore_errors=True)
    rendered = {item["rendered_sha256"] for item in happy}
    status = (
        "MOCK_E2E_PASSED"
        if all(item["status"] == "SUCCEEDED" for item in happy)
        and [item["status"] for item in failures]
        == ["HTTP_ERROR", "TIMEOUT", "CANDIDATE_REJECTED", "CLAIMS_REJECTED"]
        and all(count == 1 for count in call_counts)
        and len(rendered) == 1
        and secret_hits == 0
        and not runtime_root.exists()
        else "MOCK_E2E_FAILED"
    )
    return {
        "mock_e2e_version": 1,
        "status": status,
        "happy_path_runs": len(happy),
        "happy_path": happy,
        "failure_cases": failures,
        "rendered_sha256_unique_count": len(rendered),
        "total_mock_attempts": sum(call_counts),
        "real_provider_attempts": 0,
        "real_ai_calls": 0,
        "keychain_secret_read": False,
        "secret_hits": secret_hits,
        "temporary_residue": 0 if not runtime_root.exists() else 1,
    }


def validate_minimal_mock_e2e(evidence: Any) -> list[str]:
    if not isinstance(evidence, dict):
        return ["mock_e2e_invalid"]
    happy = evidence.get("happy_path")
    failures = evidence.get("failure_cases")
    if (
        evidence.get("mock_e2e_version") != 1
        or evidence.get("status") != "MOCK_E2E_PASSED"
        or evidence.get("happy_path_runs") != 3
        or not isinstance(happy, list)
        or len(happy) != 3
        or any(item.get("status") != "SUCCEEDED" for item in happy)
        or evidence.get("rendered_sha256_unique_count") != 1
        or not isinstance(failures, list)
        or [item.get("status") for item in failures]
        != ["HTTP_ERROR", "TIMEOUT", "CANDIDATE_REJECTED", "CLAIMS_REJECTED"]
        or evidence.get("total_mock_attempts") != 7
        or evidence.get("real_provider_attempts") != 0
        or evidence.get("real_ai_calls") != 0
        or evidence.get("keychain_secret_read") is not False
        or evidence.get("secret_hits") != 0
        or evidence.get("temporary_residue") != 0
    ):
        return ["mock_e2e_invalid"]
    return []


def validate_installed_approval(
    approval_path: Path,
    candidate_path: Path,
    scope_path: Path,
) -> dict[str, Any]:
    try:
        approval_dir = os.lstat(approval_path.parent)
        approval_file = os.lstat(approval_path)
    except OSError as exc:
        raise MinimalArkApprovalError("approval_file_invalid") from exc
    if (
        stat.S_ISLNK(approval_dir.st_mode)
        or not stat.S_ISDIR(approval_dir.st_mode)
        or stat.S_IMODE(approval_dir.st_mode) != 0o700
        or stat.S_ISLNK(approval_file.st_mode)
        or not stat.S_ISREG(approval_file.st_mode)
        or stat.S_IMODE(approval_file.st_mode) != 0o600
        or approval_file.st_uid != os.getuid()
    ):
        raise MinimalArkApprovalError("approval_file_invalid")
    try:
        approval = json.loads(_regular_bytes(approval_path, "approval_file_invalid"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinimalArkApprovalError("approval_file_invalid") from exc
    expected_keys = {
        "approval_version",
        "approval_status",
        "approval_candidate_sha256",
        "approval_scope_sha256",
        "approval_scope_id",
    }
    if not isinstance(approval, dict) or set(approval) != expected_keys:
        raise MinimalArkApprovalError("approval_file_invalid")
    candidate_sha = _sha256(
        _regular_bytes(candidate_path, "approval_candidate_invalid")
    )
    scope_raw = _regular_bytes(scope_path, "approval_scope_invalid")
    scope_sha = _sha256(scope_raw)
    try:
        scope = MinimalArkScope.model_validate(json.loads(scope_raw))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise MinimalArkApprovalError("approval_scope_invalid") from exc
    if approval != {
        "approval_version": 1,
        "approval_status": "APPROVED",
        "approval_candidate_sha256": candidate_sha,
        "approval_scope_sha256": scope_sha,
        "approval_scope_id": scope.scope_id,
    }:
        raise MinimalArkApprovalError("approval_identity_mismatch")
    return approval


def load_minimal_artifact(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_regular_bytes(path, "runtime_artifact_invalid"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinimalArkApprovalError("runtime_artifact_invalid") from exc
    if not isinstance(value, dict):
        raise MinimalArkApprovalError("runtime_artifact_invalid")
    return value
