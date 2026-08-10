#!/usr/bin/env python3
"""Validate the approval-scoped Canary ledger namespace without external I/O."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.services.phase2_canary_orchestrator import (
    ApprovalScopedLedgerNamespace,
    ApprovalScopeIdentity,
    AttemptLedger,
    DockerCanaryBackend,
    LedgerNamespacePreflight,
    LedgerState,
    compute_approval_scope_id,
)
from app.services.phase2_canary_runtime_artifact import (
    RuntimeArtifactCandidate,
    verify_runtime_artifact_candidate,
)
from app.services.phase2_claims_service import canonical_json_bytes
from scripts.run_phase2_canary_local_path_mock_e2e import _historical_manifest

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_1 = "d59766101b63450e8148541d589a90bf"
_REQUEST_2 = "3d3e6adbe5b74c98ad18a2efe0fd1d0c"
_EXPECTED_LEDGER_SHA256 = {
    _REQUEST_1: "ded0f39813dd70657cedd6a8c92669d3508480e97efc6ed44b1a45f9470b5772",
    _REQUEST_2: "4adcce4ffff6364c0a2552b59be17f6cdd646cb2232d5c02eb16f19716ebf64b",
}
_EXPECTED_LEDGER_STATE = {
    _REQUEST_1: LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
    _REQUEST_2: LedgerState.FAILED_AFTER_DISPATCH,
}
_OLD_APPROVAL_SHA256 = (
    "769f4d762594ac496bbaf14b90b8dd3cb7eae572ac9bf05f0484abfe3b86ffaf"
)
_OLD_LOCAL_APPROVAL_SHA256 = (
    "86d570e37c9a28434963132a5c96c326679f3b762d74b66e67316f4c6614c585"
)
_OLD_LOCAL_APPROVAL_NAME = (
    "runtime-contract-approval.superseded-769f4d762594.json"
)
_CURRENT_LOCAL_APPROVAL_NAME = "runtime-contract-approval.json"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class HistoricalRequestEvidence(_StrictFrozenModel):
    request_id: str
    ledger_state: Literal[
        "FAILED_AFTER_DISPATCH",
        "ATTEMPT_CONSUMED_UNKNOWN",
        "CLEANUP_COMPLETED",
    ]
    disposition: Literal["CONSUMED_PRESERVED"]
    ledger_sha256: str

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        if _HEX_32.fullmatch(value) is None:
            raise ValueError("request_id_invalid")
        return value

    @field_validator("ledger_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("ledger_sha256_invalid")
        return value


class LedgerNamespaceEvidence(_StrictFrozenModel):
    ledger_namespace_preflight_schema_version: Literal[1] = 1
    status: Literal[
        "PHASE2B_CANARY_LEDGER_NAMESPACE_READY_FOR_REAPPROVAL",
        "PHASE2B_CANARY_LEDGER_NAMESPACE_BLOCKED",
    ]
    approval_candidate_sha256: str
    ledger_namespace_version: Literal[1] = 1
    current_approval_scope_id: str
    current_scope_historical_attempts: int
    current_scope_attempt_availability: Literal["AVAILABLE", "BLOCKED"]
    historical_requests: tuple[HistoricalRequestEvidence, ...]
    historical_ledger_bytes_unchanged: bool
    historical_evidence_unchanged: bool
    global_lock_state: Literal["NONE", "HELD", "UNKNOWN"]
    global_request_id_uniqueness: bool
    old_active_unknown_ledger_safety: bool
    container_residue_count: int
    network_residue_count: int
    old_769f_approval: Literal["SUPERSEDED_AND_PRESERVED", "INVALID"]
    new_approval_installed: bool
    secret_file_residue_count: int
    temporary_residue_count: int
    real_provider_attempt_count: Literal[0]
    ai_call_count: Literal[0]
    provider_http: Literal["NOT_RUN"]
    secret_content_read: Literal[False]
    errors: tuple[str, ...]

    @field_validator(
        "ledger_namespace_preflight_schema_version",
        "ledger_namespace_version",
        "current_scope_historical_attempts",
        "container_residue_count",
        "network_residue_count",
        "secret_file_residue_count",
        "temporary_residue_count",
        "real_provider_attempt_count",
        "ai_call_count",
        mode="before",
    )
    @classmethod
    def require_exact_integer_fields(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("namespace_evidence_type_invalid")
        return value

    @field_validator("secret_content_read", mode="before")
    @classmethod
    def require_exact_false(cls, value: object) -> object:
        if value is not False:
            raise ValueError("namespace_evidence_type_invalid")
        return value

    @field_validator("approval_candidate_sha256", "current_approval_scope_id")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("sha256_invalid")
        return value


def build_ledger_namespace_evidence(
    *,
    candidate_sha256: str,
    preflight: LedgerNamespacePreflight,
    historical_requests: tuple[HistoricalRequestEvidence, ...],
    historical_ledger_bytes_unchanged: bool,
    historical_evidence_unchanged: bool,
    global_lock_state: Literal["NONE", "HELD", "UNKNOWN"],
    global_request_id_uniqueness: bool,
    old_active_unknown_ledger_safety: bool,
    container_residue_count: int,
    network_residue_count: int,
    old_769f_approval_preserved: bool,
    current_approval_present: bool,
    secret_file_residue_count: int,
    temporary_residue_count: int,
) -> LedgerNamespaceEvidence:
    errors = list(preflight.errors)
    history_complete = (
        len(historical_requests) == 2
        and {item.request_id for item in historical_requests}
        == {_REQUEST_1, _REQUEST_2}
        and all(
            item.disposition == "CONSUMED_PRESERVED"
            for item in historical_requests
        )
    )
    gates = {
        "namespace_preflight": preflight.status == "READY",
        "current_scope_attempts": preflight.current_scope_provider_attempts == 0,
        "current_scope_available": (
            preflight.current_scope_attempt_availability == "AVAILABLE"
        ),
        "historical_requests": history_complete,
        "historical_ledger_bytes": historical_ledger_bytes_unchanged,
        "historical_evidence": historical_evidence_unchanged,
        "global_lock": global_lock_state == "NONE",
        "global_request_id_uniqueness": global_request_id_uniqueness,
        "old_active_unknown_ledger_safety": old_active_unknown_ledger_safety,
        "container_residue": container_residue_count == 0,
        "network_residue": network_residue_count == 0,
        "old_769f_approval": old_769f_approval_preserved,
        "current_approval_absent": not current_approval_present,
        "secret_file_residue": secret_file_residue_count == 0,
        "temporary_residue": temporary_residue_count == 0,
    }
    errors.extend(name for name, passed in gates.items() if not passed)
    status = (
        "PHASE2B_CANARY_LEDGER_NAMESPACE_READY_FOR_REAPPROVAL"
        if not errors
        else "PHASE2B_CANARY_LEDGER_NAMESPACE_BLOCKED"
    )
    return LedgerNamespaceEvidence(
        status=status,
        approval_candidate_sha256=candidate_sha256,
        current_approval_scope_id=preflight.current_scope_id,
        current_scope_historical_attempts=(
            preflight.current_scope_provider_attempts
        ),
        current_scope_attempt_availability=(
            preflight.current_scope_attempt_availability
        ),
        historical_requests=historical_requests,
        historical_ledger_bytes_unchanged=historical_ledger_bytes_unchanged,
        historical_evidence_unchanged=historical_evidence_unchanged,
        global_lock_state=global_lock_state,
        global_request_id_uniqueness=global_request_id_uniqueness,
        old_active_unknown_ledger_safety=old_active_unknown_ledger_safety,
        container_residue_count=container_residue_count,
        network_residue_count=network_residue_count,
        old_769f_approval=(
            "SUPERSEDED_AND_PRESERVED"
            if old_769f_approval_preserved
            else "INVALID"
        ),
        new_approval_installed=current_approval_present,
        secret_file_residue_count=secret_file_residue_count,
        temporary_residue_count=temporary_residue_count,
        real_provider_attempt_count=0,
        ai_call_count=0,
        provider_http="NOT_RUN",
        secret_content_read=False,
        errors=tuple(errors),
    )


def _read_regular(path: Path, *, maximum_bytes: int = 1_048_576) -> bytes:
    metadata = os.lstat(path)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or not 0 < metadata.st_size <= maximum_bytes
    ):
        raise RuntimeError("evidence_file_invalid")
    descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, maximum_bytes + 1)
        if (
            not stat.S_ISREG(opened.st_mode)
            or len(raw) != opened.st_size
            or len(raw) > maximum_bytes
            or (opened.st_dev, opened.st_ino)
            != (metadata.st_dev, metadata.st_ino)
        ):
            raise RuntimeError("evidence_file_invalid")
        return raw
    finally:
        os.close(descriptor)


def _historical_ledgers(
    state_root: Path,
) -> tuple[tuple[HistoricalRequestEvidence, ...], dict[str, str]]:
    paths = {
        _REQUEST_1: state_root / "history" / _REQUEST_1 / "attempt-ledger.json",
        _REQUEST_2: state_root / "attempt-ledger.json",
    }
    observed: list[HistoricalRequestEvidence] = []
    hashes: dict[str, str] = {}
    for request_id, path in paths.items():
        raw = _read_regular(path)
        digest = hashlib.sha256(raw).hexdigest()
        ledger = AttemptLedger.model_validate_json(raw)
        if (
            raw != canonical_json_bytes(ledger.model_dump(mode="json"))
            or digest != _EXPECTED_LEDGER_SHA256[request_id]
            or ledger.identity.request_id != request_id
            or ledger.state is not _EXPECTED_LEDGER_STATE[request_id]
            or ledger.provider_attempt_count != 1
        ):
            raise RuntimeError("historical_ledger_invalid")
        hashes[request_id] = digest
        observed.append(
            HistoricalRequestEvidence(
                request_id=request_id,
                ledger_state=ledger.state.value,
                disposition="CONSUMED_PRESERVED",
                ledger_sha256=digest,
            )
        )
    return tuple(observed), hashes


def _ensure_private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError("private_directory_invalid")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RuntimeError("private_directory_invalid")
    return path


def _inspect_private_directory(path: Path) -> Path:
    metadata = os.lstat(path)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise RuntimeError("private_directory_invalid")
    return path


def _validate_old_approval_preserved(
    *,
    repo_root: Path,
    app_support: Path,
) -> bool:
    archived_candidate = (
        repo_root
        / "reports/phase2_provider_canary/superseded"
        / f"{_OLD_APPROVAL_SHA256}.json"
    )
    local_approval = app_support / _OLD_LOCAL_APPROVAL_NAME
    try:
        candidate_raw = _read_regular(archived_candidate)
        approval_metadata = os.lstat(local_approval)
        approval_raw = _read_regular(local_approval)
        approval = json.loads(approval_raw)
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    expected_approval = {
        "approval_scope": "single_symbol_openai_canary_runtime_v2",
        "approved_candidate_sha256": _OLD_APPROVAL_SHA256,
        "maximum_provider_attempts": 1,
        "provider": "openai",
        "retry_count": 0,
        "runtime_approval_schema_version": 2,
        "symbol": "000403.SZ",
    }
    return (
        hashlib.sha256(candidate_raw).hexdigest() == _OLD_APPROVAL_SHA256
        and hashlib.sha256(approval_raw).hexdigest()
        == _OLD_LOCAL_APPROVAL_SHA256
        and stat.S_ISREG(approval_metadata.st_mode)
        and not stat.S_ISLNK(approval_metadata.st_mode)
        and stat.S_IMODE(approval_metadata.st_mode) == 0o600
        and approval_metadata.st_uid == os.getuid()
        and approval_raw == canonical_json_bytes(approval)
        and approval == expected_approval
    )


def _scan_work_residue(work_root: Path) -> tuple[int, int]:
    _inspect_private_directory(work_root)
    secret_count = 0
    temporary_count = 0
    pending = [work_root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                temporary_count += 1
                lowered = entry.name.lower()
                if any(
                    marker in lowered
                    for marker in ("provider-auth", "secret", "authorization")
                ):
                    secret_count += 1
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(Path(entry.path))
    return secret_count, temporary_count


def _inspect_global_lock(path: Path) -> Literal["NONE", "HELD", "UNKNOWN"]:
    metadata = os.lstat(path)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        return "UNKNOWN"
    descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "HELD"
        raw = os.read(descriptor, 262_145)
        return "NONE" if not raw.strip() else "UNKNOWN"
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    parent_metadata = os.lstat(path.parent)
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or stat.S_IMODE(parent_metadata.st_mode) & 0o022
    ):
        raise RuntimeError("output_directory_invalid")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".partial",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise RuntimeError("evidence_write_failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def validate_local_namespace(
    *,
    repo_root: Path,
    expected_candidate_sha256: str,
) -> LedgerNamespaceEvidence:
    if _HEX_64.fullmatch(expected_candidate_sha256) is None:
        raise RuntimeError("candidate_sha256_invalid")
    candidate_path = (
        repo_root
        / "reports/phase2_provider_canary/runtime_contract_candidate.json"
    )
    candidate_raw = _read_regular(candidate_path)
    if hashlib.sha256(candidate_raw).hexdigest() != expected_candidate_sha256:
        raise RuntimeError("candidate_sha256_mismatch")
    candidate = RuntimeArtifactCandidate.model_validate_json(candidate_raw)
    verify_runtime_artifact_candidate(
        repo_root,
        candidate_path,
        expected_candidate_sha256=expected_candidate_sha256,
    )

    app_support = Path.home() / "Library/Application Support/TickFlowPhase2Canary"
    state_root = app_support / "runtime-v1"
    attempts_root = _inspect_private_directory(
        repo_root / "reports/phase2_provider_canary/attempts"
    )
    historical_root = repo_root / "reports/phase2_provider_canary/live_canary"
    candidate_roots = (
        repo_root / "reports/phase2_provider_canary/superseded",
    )
    historical_requests, ledgers_before = _historical_ledgers(state_root)
    historical_before = _historical_manifest(repo_root)
    old_approval_preserved = _validate_old_approval_preserved(
        repo_root=repo_root,
        app_support=app_support,
    )
    current_approval = app_support / _CURRENT_LOCAL_APPROVAL_NAME
    current_approval_present = (
        current_approval.exists() or current_approval.is_symlink()
    )
    secret_residue_count, temporary_residue_count = _scan_work_residue(
        app_support / "work-v1"
    )
    scope = ApprovalScopeIdentity(
        approval_candidate_sha256=expected_candidate_sha256,
        symbol=candidate.symbol,
        provider_id=candidate.provider,
        exact_model_id=candidate.exact_model,
        endpoint_alias=candidate.endpoint_alias,
    )
    namespace = ApprovalScopedLedgerNamespace(
        repo_root=repo_root,
        attempts_root=attempts_root,
        legacy_state_root=state_root,
        historical_evidence_root=historical_root,
        candidate_roots=candidate_roots,
    )
    preflight = namespace.preflight(scope)
    expected_scope_id = compute_approval_scope_id(scope)
    if preflight.current_scope_id != expected_scope_id:
        raise RuntimeError("approval_scope_id_mismatch")
    lock_state = _inspect_global_lock(state_root / ".runtime.lock")
    residue = DockerCanaryBackend().inspect_global_residue(
        deadline=time.monotonic() + 15.0
    )
    historical_after = _historical_manifest(repo_root)
    _, ledgers_after = _historical_ledgers(state_root)
    return build_ledger_namespace_evidence(
        candidate_sha256=expected_candidate_sha256,
        preflight=preflight,
        historical_requests=historical_requests,
        historical_ledger_bytes_unchanged=ledgers_before == ledgers_after,
        historical_evidence_unchanged=historical_before == historical_after,
        global_lock_state=lock_state,
        global_request_id_uniqueness=(
            len(preflight.global_request_ids)
            == len(set(preflight.global_request_ids))
        ),
        old_active_unknown_ledger_safety=(
            preflight.status != "GLOBAL_LEDGER_SAFETY_BLOCKED"
        ),
        container_residue_count=residue.container_residue_count,
        network_residue_count=residue.network_residue_count,
        old_769f_approval_preserved=old_approval_preserved,
        current_approval_present=current_approval_present,
        secret_file_residue_count=secret_residue_count,
        temporary_residue_count=temporary_residue_count,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-candidate-sha256", required=True)
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    evidence = validate_local_namespace(
        repo_root=repo_root,
        expected_candidate_sha256=args.expected_candidate_sha256,
    )
    output = (
        repo_root
        / "reports/phase2_provider_canary/ledger_namespace_preflight.json"
    )
    _atomic_write(output, canonical_json_bytes(evidence.model_dump(mode="json")))
    print(
        json.dumps(
            {"output": str(output), "status": evidence.status},
            sort_keys=True,
        )
    )
    return 0 if evidence.status.endswith("READY_FOR_REAPPROVAL") else 2


if __name__ == "__main__":
    raise SystemExit(main())
