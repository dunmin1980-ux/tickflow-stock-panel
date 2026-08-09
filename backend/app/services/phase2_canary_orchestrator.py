"""Fail-closed state primitives for the one-shot Phase 2B Canary."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.schemas.phase2_claims import ClaimsDocument, WorkerClaimsCandidate
from app.services.phase2_ai_worker_protocol import (
    compute_projection_sha256,
)
from app.services.phase2_canary_observability import (
    ChildExecutionTiming,
    ChildProcessEvidence,
    ChildState,
    classify_child_exception,
    classify_completed_child,
    classify_running_child,
)
from app.services.phase2_canary_runtime_contract import (
    load_runtime_contract,
    runtime_contract_sha256,
)
from app.services.phase2_canary_runtime_artifact import RuntimeArtifactCandidate
from app.services.phase2_claims_renderer import (
    render_claims_document,
    validate_rendered_document,
)
from app.services.phase2_claims_service import (
    CLAIMS_VALID,
    canonical_json_bytes,
    validate_claims_document,
)
from app.services.phase2_isolation_runtime import validate_isolated_candidate
from app.services.phase2_provider_relay_protocol import build_relay_request

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_MAXIMUM_LEDGER_BYTES = 65_536
_LEDGER_NAME = "attempt-ledger.json"
_LOCK_NAME = ".runtime.lock"
_EVENT_FIELDS = {
    "event",
    "occurred",
    "wall_time",
    "monotonic_ns",
    "http_status",
    "byte_count",
}
_PROXY_EVENTS = (
    "process_started",
    "proxy_ready",
    "relay_request_received",
    "provider_connect_started",
    "provider_connect_completed",
    "tls_completed",
    "request_write_started",
    "request_write_completed",
    "response_headers_received",
    "response_body_completed",
)
_RELAY_EVENTS = (
    "relay_started",
    "proxy_connect_started",
    "proxy_connect_completed",
    "request_submitted",
    "response_wait_started",
    "response_received",
    "candidate_write_started",
    "candidate_write_completed",
    "candidate_ready_published",
)
_STAGE_TIE_ORDER = (
    "process_started",
    "proxy_ready",
    "relay_started",
    "proxy_connect_started",
    "proxy_connect_completed",
    "request_submitted",
    "relay_request_received",
    "provider_connect_started",
    "provider_connect_completed",
    "tls_completed",
    "request_write_started",
    "request_write_completed",
    "response_wait_started",
    "response_headers_received",
    "response_body_completed",
    "response_received",
    "candidate_write_started",
    "candidate_write_completed",
    "candidate_ready_published",
)
_PROXY_RECEIPT_FIELDS = {
    "receipt_schema_version",
    "request_id",
    "component",
    "method_allowed",
    "path_allowed",
    "auth_present",
    "tls_verification",
    "redirect_followed",
    "provider_attempt_count",
    "retry_count",
    "provider_http_status",
    "response_category",
    "response_size",
    "response_bytes",
    "terminal_status",
    *_PROXY_EVENTS,
}
_RELAY_RECEIPT_FIELDS = {
    "receipt_schema_version",
    "request_id",
    "component",
    "status",
    "exit_code",
    "terminal_status",
    "terminal_reason_source",
    "error_category",
    "proxy_http_status",
    "proxy_request_count",
    "retry_count",
    "response_size",
    "response_sha256",
    "started_at",
    "completed_at",
    *_RELAY_EVENTS,
}
_RELAY_TERMINAL_STATUSES = {
    "RELAY_COMPLETED",
    "RELAY_TIMEOUT",
    "RELAY_NONZERO_EXIT",
    "RELAY_PROCESS_ERROR",
    "RELAY_SIGNALLED",
    "RELAY_START_FAILED",
    "RELAY_PROXY_CONNECT_FAILED",
    "RELAY_PROTOCOL_REJECTED",
}
_TERMINAL_REASON_SOURCES = {
    "relay_self",
    "host_child_process",
    "host_timeout",
}
_PROXY_READY_FIELDS = {
    "request_id",
    "proxy_ready",
    "wall_time",
    "monotonic_ns",
    "listener_ready",
}
_SENSITIVE_EVIDENCE = re.compile(
    r"Authorization\s*:|Bearer\s+[^\s]+|"
    r"(?:api[_-]?key|openai_api_key|secret|token|password|credential)"
    r"\s*[:=]\s*[^\s]+|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}",
    re.IGNORECASE,
)


def _wall_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class OrchestratorError(RuntimeError):
    """Stable fail-closed error category for Host orchestration."""


class LedgerState(StrEnum):
    PREPARED = "PREPARED"
    NETWORK_DISPATCH_STARTED = "NETWORK_DISPATCH_STARTED"
    PROVIDER_RESPONSE_RECEIVED = "PROVIDER_RESPONSE_RECEIVED"
    CANDIDATE_COLLECTED = "CANDIDATE_COLLECTED"
    HOST_VALIDATION_COMPLETED = "HOST_VALIDATION_COMPLETED"
    CLEANUP_COMPLETED = "CLEANUP_COMPLETED"
    FAILED_BEFORE_DISPATCH = "FAILED_BEFORE_DISPATCH"
    FAILED_AFTER_DISPATCH = "FAILED_AFTER_DISPATCH"
    ATTEMPT_CONSUMED_UNKNOWN = "ATTEMPT_CONSUMED_UNKNOWN"


_POST_DISPATCH_STATES = {
    LedgerState.NETWORK_DISPATCH_STARTED,
    LedgerState.PROVIDER_RESPONSE_RECEIVED,
    LedgerState.CANDIDATE_COLLECTED,
    LedgerState.HOST_VALIDATION_COMPLETED,
    LedgerState.CLEANUP_COMPLETED,
    LedgerState.FAILED_AFTER_DISPATCH,
    LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
}
_TERMINAL_STATES = {
    LedgerState.CLEANUP_COMPLETED,
    LedgerState.FAILED_BEFORE_DISPATCH,
    LedgerState.FAILED_AFTER_DISPATCH,
    LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
}
_TRANSITIONS = {
    LedgerState.PREPARED: {
        LedgerState.NETWORK_DISPATCH_STARTED,
        LedgerState.FAILED_BEFORE_DISPATCH,
    },
    LedgerState.NETWORK_DISPATCH_STARTED: {
        LedgerState.PROVIDER_RESPONSE_RECEIVED,
        LedgerState.FAILED_AFTER_DISPATCH,
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
    },
    LedgerState.PROVIDER_RESPONSE_RECEIVED: {
        LedgerState.CANDIDATE_COLLECTED,
        LedgerState.FAILED_AFTER_DISPATCH,
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
    },
    LedgerState.CANDIDATE_COLLECTED: {
        LedgerState.HOST_VALIDATION_COMPLETED,
        LedgerState.FAILED_AFTER_DISPATCH,
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
    },
    LedgerState.HOST_VALIDATION_COMPLETED: {
        LedgerState.CLEANUP_COMPLETED,
        LedgerState.FAILED_AFTER_DISPATCH,
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
    },
}


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CanaryRunIdentity(_StrictFrozenModel):
    request_id: str
    symbol: Literal["000403.SZ"]
    trade_date: Literal["2026-07-31"]
    provider: Literal["openai"]
    endpoint_alias: Literal["openai_responses_v1"]
    facts_sha256: str
    projection_sha256: str
    approval_candidate_sha256: str
    proxy_image_id: str
    relay_image_id: str
    timeout_contract_sha256: str
    orchestrator_source_sha256: str

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        if _HEX_32.fullmatch(value) is None:
            raise ValueError("request_id_invalid")
        return value

    @field_validator(
        "facts_sha256",
        "projection_sha256",
        "approval_candidate_sha256",
        "timeout_contract_sha256",
        "orchestrator_source_sha256",
    )
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("sha256_invalid")
        return value

    @field_validator("proxy_image_id", "relay_image_id")
    @classmethod
    def validate_image_id(cls, value: str) -> str:
        if _IMAGE_ID.fullmatch(value) is None:
            raise ValueError("image_id_invalid")
        return value


class AttemptLedger(_StrictFrozenModel):
    ledger_schema_version: Literal[1] = 1
    identity: CanaryRunIdentity
    state: LedgerState
    provider_attempt_count: Literal[0, 1]
    retry_count: Literal[0] = 0
    prepared_at: str
    dispatch_started_at: str | None = None
    response_received_at: str | None = None
    candidate_ready_at: str | None = None
    host_validation_completed_at: str | None = None
    cleanup_completed_at: str | None = None
    failed_at: str | None = None


def _canonical_bytes(value: object) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise OrchestratorError("ledger_encoding_failed") from exc
    return (rendered + "\n").encode("utf-8")


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
            raise OrchestratorError("state_root_symlink_forbidden")


def _validate_state_root(root: Path) -> Path:
    path = Path(os.path.abspath(os.fspath(root)))
    _assert_no_symlink_components(path)
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError("state_root_missing") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise OrchestratorError("state_root_symlink_forbidden")
    if not stat.S_ISDIR(metadata.st_mode):
        raise OrchestratorError("state_root_not_directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise OrchestratorError("state_root_mode_invalid")
    return path


def _read_regular(path: Path, category: str) -> bytes:
    try:
        before = os.lstat(path)
    except FileNotFoundError as exc:
        raise OrchestratorError("ledger_missing") from exc
    except OSError as exc:
        raise OrchestratorError(category) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 0 < before.st_size <= _MAXIMUM_LEDGER_BYTES
    ):
        raise OrchestratorError(category)
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise OrchestratorError(category)
        raw = os.read(descriptor, _MAXIMUM_LEDGER_BYTES + 1)
        after = os.lstat(path)
        if (
            len(raw) != opened.st_size
            or len(raw) > _MAXIMUM_LEDGER_BYTES
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OrchestratorError(category)
        return raw
    except OSError as exc:
        raise OrchestratorError(category) from exc
    finally:
        os.close(descriptor)


def _decode_ledger(raw: bytes) -> AttemptLedger:
    try:
        ledger = AttemptLedger.model_validate_json(raw)
    except ValueError as exc:
        raise OrchestratorError("ledger_invalid") from exc
    if raw != _canonical_bytes(ledger.model_dump(mode="json")):
        raise OrchestratorError("ledger_not_canonical")
    expected_attempts = 1 if ledger.state in _POST_DISPATCH_STATES else 0
    if ledger.provider_attempt_count != expected_attempts:
        raise OrchestratorError("ledger_attempt_count_invalid")
    return ledger


def _atomic_write(path: Path, raw: bytes) -> None:
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)
    ):
        raise OrchestratorError("ledger_target_invalid")
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
                raise OrchestratorError("ledger_write_failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OrchestratorError:
        raise
    except OSError as exc:
        raise OrchestratorError("ledger_publish_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


class AttemptLedgerStore:
    """Persistent fsync-backed ledger outside the ephemeral run directory."""

    def __init__(self, state_root: Path) -> None:
        self.root = _validate_state_root(state_root)
        self.path = self.root / _LEDGER_NAME

    def prepare(self, identity: CanaryRunIdentity, *, timestamp: str) -> AttemptLedger:
        if not isinstance(identity, CanaryRunIdentity):
            raise OrchestratorError("identity_invalid")
        try:
            existing = os.lstat(self.path)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
                raise OrchestratorError("ledger_target_invalid")
            raise OrchestratorError("ledger_already_exists")
        ledger = AttemptLedger(
            identity=identity,
            state=LedgerState.PREPARED,
            provider_attempt_count=0,
            prepared_at=timestamp,
        )
        _atomic_write(self.path, _canonical_bytes(ledger.model_dump(mode="json")))
        return ledger

    def load(self) -> AttemptLedger:
        return _decode_ledger(_read_regular(self.path, "ledger_target_invalid"))

    def transition(
        self,
        state: LedgerState,
        *,
        timestamp: str,
    ) -> AttemptLedger:
        current = self.load()
        if state not in _TRANSITIONS.get(current.state, set()):
            raise OrchestratorError("ledger_transition_invalid")
        updates: dict[str, Any] = {
            "state": state,
            "provider_attempt_count": 1 if state in _POST_DISPATCH_STATES else 0,
        }
        timestamp_field = {
            LedgerState.NETWORK_DISPATCH_STARTED: "dispatch_started_at",
            LedgerState.PROVIDER_RESPONSE_RECEIVED: "response_received_at",
            LedgerState.CANDIDATE_COLLECTED: "candidate_ready_at",
            LedgerState.HOST_VALIDATION_COMPLETED: "host_validation_completed_at",
            LedgerState.CLEANUP_COMPLETED: "cleanup_completed_at",
            LedgerState.FAILED_BEFORE_DISPATCH: "failed_at",
            LedgerState.FAILED_AFTER_DISPATCH: "failed_at",
            LedgerState.ATTEMPT_CONSUMED_UNKNOWN: "failed_at",
        }[state]
        updates[timestamp_field] = timestamp
        ledger = current.model_copy(update=updates)
        ledger = AttemptLedger.model_validate_json(
            _canonical_bytes(ledger.model_dump(mode="json"))
        )
        _atomic_write(self.path, _canonical_bytes(ledger.model_dump(mode="json")))
        return ledger


def recover_attempt_state(
    store: AttemptLedgerStore,
    *,
    timestamp: str,
) -> AttemptLedger:
    """Close an interrupted state without ever reopening the attempt."""
    current = store.load()
    if current.state in _TERMINAL_STATES:
        return current
    if current.state is LedgerState.PREPARED:
        return store.transition(
            LedgerState.FAILED_BEFORE_DISPATCH,
            timestamp=timestamp,
        )
    return store.transition(
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
        timestamp=timestamp,
    )


class ExclusiveCanaryLock:
    """A nonblocking process lock acquired before request-id generation."""

    def __init__(
        self,
        state_root: Path,
        *,
        approval_candidate_sha256: str,
        pid: int,
        started_at: str,
    ) -> None:
        self.root = _validate_state_root(state_root)
        if _HEX_64.fullmatch(approval_candidate_sha256) is None:
            raise OrchestratorError("approval_candidate_sha256_invalid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise OrchestratorError("lock_pid_invalid")
        self.approval_candidate_sha256 = approval_candidate_sha256
        self.pid = pid
        self.started_at = started_at
        self.path = self.root / _LOCK_NAME
        self._descriptor: int | None = None

    def _read_locked(self, descriptor: int) -> bytes:
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, _MAXIMUM_LEDGER_BYTES + 1)
        if len(raw) > _MAXIMUM_LEDGER_BYTES:
            raise OrchestratorError("stale_lock_state_unknown")
        return raw

    def _stale_lock_is_recoverable(self, raw: bytes) -> bool:
        if not raw.strip():
            return True
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        expected_fields = {
            "lock_schema_version",
            "pid",
            "started_at",
            "symbol",
            "provider",
            "endpoint_alias",
            "approval_candidate_sha256",
        }
        if (
            not isinstance(value, dict)
            or set(value) != expected_fields
            or value.get("lock_schema_version") != 1
            or value.get("symbol") != "000403.SZ"
            or value.get("provider") != "openai"
            or value.get("endpoint_alias") != "openai_responses_v1"
            or value.get("approval_candidate_sha256")
            != self.approval_candidate_sha256
        ):
            return False
        try:
            ledger = AttemptLedgerStore(self.root).load()
        except OrchestratorError:
            return False
        return (
            ledger.state is LedgerState.FAILED_BEFORE_DISPATCH
            and ledger.identity.approval_candidate_sha256
            == self.approval_candidate_sha256
        )

    def acquire(self) -> None:
        if self._descriptor is not None:
            raise OrchestratorError("canary_lock_already_acquired")
        try:
            descriptor = os.open(
                self.path,
                os.O_RDWR | os.O_CREAT | _NOFOLLOW,
                0o600,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise OrchestratorError("canary_lock_target_invalid")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise OrchestratorError("canary_lock_held") from exc
                raise
            raw = self._read_locked(descriptor)
            if not self._stale_lock_is_recoverable(raw):
                raise OrchestratorError("stale_lock_state_unknown")
            payload = {
                "lock_schema_version": 1,
                "pid": self.pid,
                "started_at": self.started_at,
                "symbol": "000403.SZ",
                "provider": "openai",
                "endpoint_alias": "openai_responses_v1",
                "approval_candidate_sha256": self.approval_candidate_sha256,
            }
            rendered = _canonical_bytes(payload)
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, rendered)
            os.fsync(descriptor)
            self._descriptor = descriptor
        except OrchestratorError:
            if "descriptor" in locals():
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            raise
        except OSError as exc:
            if "descriptor" in locals():
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            raise OrchestratorError("canary_lock_failed") from exc

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            self._descriptor = None

    def __enter__(self) -> ExclusiveCanaryLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class ApprovedCanaryArtifacts(_StrictFrozenModel):
    """Exact immutable identities approved before a future live run."""

    approval_candidate_sha256: str
    facts_sha256: str
    projection_sha256: str
    proxy_image_id: str
    relay_image_id: str
    timeout_contract_sha256: str
    readiness_contract_sha256: str
    orchestrator_source_sha256: str

    @field_validator(
        "approval_candidate_sha256",
        "facts_sha256",
        "projection_sha256",
        "timeout_contract_sha256",
        "readiness_contract_sha256",
        "orchestrator_source_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("artifact_sha256_invalid")
        return value

    @field_validator("proxy_image_id", "relay_image_id")
    @classmethod
    def validate_artifact_image_id(cls, value: str) -> str:
        if _IMAGE_ID.fullmatch(value) is None:
            raise ValueError("artifact_image_id_invalid")
        return value


@dataclass(frozen=True)
class CanaryOrchestratorConfig:
    repo_root: Path
    state_root: Path
    work_root: Path
    canary_output_root: Path
    facts_path: Path

    def __post_init__(self) -> None:
        repo = self.repo_root.resolve(strict=True)
        if not repo.is_dir():
            raise OrchestratorError("repo_root_invalid")
        object.__setattr__(self, "repo_root", repo)
        for field in ("state_root", "work_root", "canary_output_root"):
            path = _validate_state_root(getattr(self, field))
            object.__setattr__(self, field, path)
        facts = Path(os.path.abspath(os.fspath(self.facts_path)))
        _assert_no_symlink_components(facts)
        try:
            metadata = os.lstat(facts)
        except OSError as exc:
            raise OrchestratorError("facts_file_invalid") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OrchestratorError("facts_file_invalid")
        object.__setattr__(self, "facts_path", facts)


@dataclass(frozen=True)
class CanaryRuntimeContext:
    request_id: str
    request_path: Path
    projection_path: Path
    output_dir: Path
    proxy_output_dir: Path
    receipt_archive_dir: Path
    secret_path: Path
    proxy_image_id: str
    relay_image_id: str
    runtime_contract: Mapping[str, Any]


@dataclass(frozen=True)
class BackendDispatchResult:
    provider_http_status: int | None
    response_received: bool


@dataclass(frozen=True)
class CleanupResult:
    completed: bool
    timed_out: bool = False
    container_residue_count: int = 0
    network_residue_count: int = 0


@dataclass(frozen=True)
class ArchiveResult:
    completed: bool
    status: str
    archive_dir: Path | None = None
    last_proven_stage: str | None = None
    child_terminal_reason: str | None = None


class CanaryBackend(Protocol):
    def prepare(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> None: ...

    def dispatch(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> BackendDispatchResult: ...

    def archive_evidence(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> ArchiveResult: ...

    def cleanup(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> CleanupResult: ...


class BackendError(OrchestratorError):
    def __init__(
        self,
        category: str,
        *,
        unknown_dispatch_state: bool = False,
    ) -> None:
        self.category = category
        self.unknown_dispatch_state = unknown_dispatch_state
        super().__init__(category)


@dataclass(frozen=True)
class CanaryRunResult:
    terminal_state: str
    request_id: str | None
    attempt_ledger_state: LedgerState | None
    provider_attempt_count: int
    retry_count: int
    provider_http_status: int | None
    route: str
    host_validation_status: str
    container_residue_count: int
    network_residue_count: int
    secret_file_residue_count: int
    temporary_file_residue_count: int
    receipt_archive_status: str
    last_proven_stage: str | None
    child_terminal_reason: str | None
    error_category: str | None


def _write_private_file(path: Path, raw: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise OrchestratorError("private_file_parent_invalid")
    if path.exists() or path.is_symlink():
        raise OrchestratorError("private_file_already_exists")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
        )
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OrchestratorError("private_file_write_failed")
            offset += written
        os.fsync(descriptor)
    except OrchestratorError:
        raise
    except OSError as exc:
        raise OrchestratorError("private_file_write_failed") from exc
    finally:
        if "descriptor" in locals():
            os.close(descriptor)


def _publish_private_file(path: Path, raw: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise OrchestratorError("artifact_parent_invalid")
    if path.exists() or path.is_symlink():
        raise OrchestratorError("artifact_already_exists")
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
                raise OrchestratorError("artifact_write_failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OrchestratorError:
        raise
    except OSError as exc:
        raise OrchestratorError("artifact_publish_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _strict_json_bytes(raw: bytes, category: str) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite")

    try:
        text = raw.decode("utf-8")
        value, end = json.JSONDecoder(parse_constant=reject_constant).raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise OrchestratorError(category) from exc
    if text[end:].strip() or not isinstance(value, dict):
        raise OrchestratorError(category)
    return value


def _strict_json_object(path: Path, category: str) -> dict[str, Any]:
    return _strict_json_bytes(
        _read_regular_unrestricted(path, category),
        category,
    )


def _read_regular_unrestricted(path: Path, category: str) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or not 0 < before.st_size <= _MAXIMUM_LEDGER_BYTES * 16
    ):
        raise OrchestratorError(category)
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, _MAXIMUM_LEDGER_BYTES * 16 + 1)
        after = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or len(raw) != opened.st_size
        or len(raw) > _MAXIMUM_LEDGER_BYTES * 16
        or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise OrchestratorError(category)
    return raw


def _read_trusted_runtime_approval(path: Path) -> bytes:
    category = "runtime_approval_invalid"
    try:
        directory = os.lstat(path.parent)
        before = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    current_uid = os.geteuid()
    if (
        stat.S_ISLNK(directory.st_mode)
        or not stat.S_ISDIR(directory.st_mode)
        or stat.S_IMODE(directory.st_mode) != 0o700
        or directory.st_uid != current_uid
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_uid != current_uid
    ):
        raise OrchestratorError(category)
    raw = _read_regular_unrestricted(path, category)
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    if (
        (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or stat.S_IMODE(after.st_mode) != 0o600
        or after.st_uid != current_uid
    ):
        raise OrchestratorError(category)
    return raw


def _valid_stage_event(value: object, expected_name: str) -> bool:
    if not isinstance(value, dict) or set(value) != _EVENT_FIELDS:
        return False
    occurred = value.get("occurred")
    wall_time = value.get("wall_time")
    monotonic_ns = value.get("monotonic_ns")
    http_status = value.get("http_status")
    byte_count = value.get("byte_count")
    if value.get("event") != expected_name or not isinstance(occurred, bool):
        return False
    if not occurred:
        return all(
            item is None
            for item in (wall_time, monotonic_ns, http_status, byte_count)
        )
    if (
        not isinstance(wall_time, str)
        or not wall_time
        or isinstance(monotonic_ns, bool)
        or not isinstance(monotonic_ns, int)
        or monotonic_ns < 0
    ):
        return False
    if http_status is not None and (
        isinstance(http_status, bool)
        or not isinstance(http_status, int)
        or not 100 <= http_status <= 599
    ):
        return False
    return not (
        byte_count is not None
        and (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
        )
    )


def _validate_stage_receipt(
    value: dict[str, Any],
    *,
    component: Literal["relay", "proxy"],
    request_id: str,
) -> tuple[str, ...]:
    events = _RELAY_EVENTS if component == "relay" else _PROXY_EVENTS
    fields = (
        _RELAY_RECEIPT_FIELDS
        if component == "relay"
        else _PROXY_RECEIPT_FIELDS
    )
    relay_contract_invalid = component == "relay" and (
        value.get("terminal_reason_source") not in _TERMINAL_REASON_SOURCES
        or value.get("terminal_status") not in _RELAY_TERMINAL_STATUSES
        or (
            value.get("status") == "SUCCEEDED"
            and (
                value.get("exit_code") != 0
                or value.get("terminal_status") != "RELAY_COMPLETED"
                or value.get("error_category") is not None
            )
        )
        or (
            value.get("status") == "REJECTED"
            and (
                value.get("exit_code") != 2
                or value.get("terminal_status") == "RELAY_COMPLETED"
                or not isinstance(value.get("error_category"), str)
            )
        )
        or value.get("status") not in {"SUCCEEDED", "REJECTED"}
    )
    if (
        set(value) != fields
        or value.get("receipt_schema_version") != 2
        or value.get("request_id") != request_id
        or value.get("component") != component
        or value.get("retry_count") != 0
        or any(
            not _valid_stage_event(value.get(name), name) for name in events
        )
        or relay_contract_invalid
    ):
        raise OrchestratorError("RECEIPT_MALFORMED")
    serialized = canonical_json_bytes(value).decode("utf-8")
    if _SENSITIVE_EVIDENCE.search(serialized):
        raise OrchestratorError("RECEIPT_MALFORMED")
    return tuple(
        name
        for name in events
        if isinstance(value.get(name), dict)
        and value[name].get("occurred") is True
    )


def _load_stage_receipt(
    path: Path,
    *,
    component: Literal["relay", "proxy"],
    request_id: str,
) -> tuple[bytes | None, dict[str, Any] | None, str, tuple[str, ...]]:
    if not path.exists() or path.is_symlink():
        return None, None, "RECEIPT_MISSING", ()
    try:
        raw = _read_regular_unrestricted(path, "RECEIPT_MALFORMED")
        value, events = _parse_stage_receipt_bytes(
            raw,
            component=component,
            request_id=request_id,
        )
    except OrchestratorError:
        return None, None, "RECEIPT_MALFORMED", ()
    return raw, value, "VALID", events


def _parse_stage_receipt_bytes(
    raw: bytes,
    *,
    component: Literal["relay", "proxy"],
    request_id: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    value = _strict_json_bytes(raw, "RECEIPT_MALFORMED")
    events = _validate_stage_receipt(
        value,
        component=component,
        request_id=request_id,
    )
    if raw != canonical_json_bytes(value):
        raise OrchestratorError("RECEIPT_MALFORMED")
    return value, events


def _abnormal_child_reason(
    child_evidence: Mapping[str, ChildProcessEvidence],
) -> str | None:
    for component in ("relay", "proxy"):
        evidence = child_evidence.get(component)
        if evidence is not None and evidence.child_state not in {
            ChildState.RUNNING,
            ChildState.EXITED_ZERO,
        }:
            return evidence.terminal_reason
    return None


def _select_last_proven_stage(
    receipt_values: Mapping[str, Mapping[str, Any]],
) -> str | None:
    rank = {name: index for index, name in enumerate(_STAGE_TIE_ORDER)}
    candidates: list[tuple[int, str, int, str]] = []
    for component, events in (("proxy", _PROXY_EVENTS), ("relay", _RELAY_EVENTS)):
        receipt = receipt_values.get(component)
        if receipt is None:
            continue
        for name in events:
            event = receipt.get(name)
            if not isinstance(event, Mapping) or event.get("occurred") is not True:
                continue
            monotonic_ns = event.get("monotonic_ns")
            wall_time = event.get("wall_time")
            if not isinstance(monotonic_ns, int) or not isinstance(wall_time, str):
                continue
            candidates.append((monotonic_ns, wall_time, rank[name], name))
    return max(candidates)[-1] if candidates else None


def _archive_stage_evidence(
    context: CanaryRuntimeContext,
    child_evidence: Mapping[str, ChildProcessEvidence],
    validated_receipts: Mapping[str, bytes] | None = None,
) -> ArchiveResult:
    sources = {
        "relay": context.output_dir / "relay-receipt.json",
        "proxy": context.proxy_output_dir / "proxy-receipt.json",
    }
    loaded: dict[str, bytes] = {}
    receipt_values: dict[str, dict[str, Any]] = {}
    statuses: dict[str, str] = {}
    proven_events: dict[str, tuple[str, ...]] = {}
    for component in ("relay", "proxy"):
        _value: dict[str, Any] | None = None
        cached = (validated_receipts or {}).get(component)
        if cached is None:
            raw, _value, status, events = _load_stage_receipt(
                sources[component],
                component=component,
                request_id=context.request_id,
            )
        else:
            try:
                _value, events = _parse_stage_receipt_bytes(
                    cached,
                    component=component,
                    request_id=context.request_id,
                )
            except OrchestratorError:
                raw, status, events = None, "RECEIPT_MALFORMED", ()
            else:
                raw, status = cached, "VALID"
        statuses[component] = status
        proven_events[component] = events
        if _value is not None:
            receipt_values[component] = _value
        if raw is not None:
            loaded[component] = raw

    proxy_receipt = receipt_values.get("proxy")
    pre_dispatch_archive = (
        statuses.get("proxy") == "VALID"
        and statuses.get("relay") == "RECEIPT_MISSING"
        and proxy_receipt is not None
        and proxy_receipt.get("provider_attempt_count") == 0
        and proxy_receipt.get("response_category") == "PROXY_READY"
        and proxy_receipt.get("terminal_status") == "PROXY_READY"
        and proven_events.get("proxy") == ("process_started", "proxy_ready")
        and "relay" not in child_evidence
    )
    if "RECEIPT_MALFORMED" in statuses.values():
        archive_status = "RECEIPT_MALFORMED"
    elif pre_dispatch_archive:
        archive_status = "PRE_DISPATCH_ARCHIVED"
    elif "RECEIPT_MISSING" in statuses.values():
        archive_status = "RECEIPT_MISSING"
    elif set(child_evidence) != {"relay", "proxy"}:
        archive_status = "CHILD_EVIDENCE_MISSING"
    else:
        archive_status = "ARCHIVED"

    last_proven_stage = _select_last_proven_stage(receipt_values)
    host_child_terminal_reason = _abnormal_child_reason(child_evidence)
    child_terminal_reason = host_child_terminal_reason
    terminal_reason_source = (
        "host_timeout"
        if host_child_terminal_reason == "ORCHESTRATOR_TIMEOUT"
        else "host_child_process"
        if host_child_terminal_reason is not None
        else None
    )
    relay_receipt = receipt_values.get("relay")
    if (
        host_child_terminal_reason == "RELAY_NONZERO_EXIT"
        and relay_receipt is not None
        and relay_receipt.get("status") == "REJECTED"
        and relay_receipt.get("terminal_reason_source") == "relay_self"
        and relay_receipt.get("terminal_status") in _RELAY_TERMINAL_STATUSES
    ):
        child_terminal_reason = relay_receipt["terminal_status"]
        terminal_reason_source = "relay_self"
    child_payload = {
        "child_metadata_schema_version": 1,
        "request_id": context.request_id,
        "relay": (
            asdict(child_evidence["relay"])
            if "relay" in child_evidence
            else None
        ),
        "proxy": (
            asdict(child_evidence["proxy"])
            if "proxy" in child_evidence
            else None
        ),
    }
    status_payload = {
        "archive_status_schema_version": 1,
        "request_id": context.request_id,
        "archive_status": archive_status,
        "relay_receipt_status": statuses["relay"],
        "proxy_receipt_status": statuses["proxy"],
        "child_metadata_status": (
            "VALID"
            if set(child_evidence) == {"relay", "proxy"}
            else (
                "PARTIAL_PRE_DISPATCH"
                if pre_dispatch_archive and set(child_evidence) == {"proxy"}
                else "MISSING"
            )
        ),
        "last_proven_stage": last_proven_stage,
        "child_terminal_reason": child_terminal_reason,
        "host_child_terminal_reason": host_child_terminal_reason,
        "terminal_reason_source": terminal_reason_source,
        "retry_count": 0,
    }
    for value in (child_payload, status_payload):
        if _SENSITIVE_EVIDENCE.search(
            canonical_json_bytes(value).decode("utf-8")
        ):
            raise OrchestratorError("RECEIPT_SECURITY_BLOCKED")

    target = context.receipt_archive_dir
    parent = target.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (
        parent.is_symlink()
        or not parent.is_dir()
        or stat.S_IMODE(parent.stat().st_mode) != 0o700
        or target.exists()
        or target.is_symlink()
    ):
        raise OrchestratorError("RECEIPT_ARCHIVE_FAILED")
    staging = parent / f".{target.name}.{uuid.uuid4().hex}.partial"
    try:
        staging.mkdir(mode=0o700)
        for component, raw in loaded.items():
            _write_private_file(staging / f"{component}-receipt.json", raw)
        _write_private_file(
            staging / "child-metadata.json",
            canonical_json_bytes(child_payload),
        )
        _write_private_file(
            staging / "archive-status.json",
            canonical_json_bytes(status_payload),
        )
        staging_descriptor = os.open(
            staging,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
        )
        try:
            os.fsync(staging_descriptor)
        finally:
            os.close(staging_descriptor)
        os.replace(staging, target)
        parent_descriptor = os.open(
            parent,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except (OSError, OrchestratorError) as exc:
        raise OrchestratorError("RECEIPT_ARCHIVE_FAILED") from exc
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(staging)
    return ArchiveResult(
        completed=True,
        status=archive_status,
        archive_dir=target,
        last_proven_stage=last_proven_stage,
        child_terminal_reason=child_terminal_reason,
    )


def _collect_candidate(
    context: CanaryRuntimeContext,
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    entries = list(context.output_dir.iterdir())
    if any(path.name.endswith(".partial") for path in entries):
        raise OrchestratorError("CANDIDATE_PARTIAL_WRITE")
    candidate_files = [
        path
        for path in entries
        if path.name.startswith("candidate")
        and path.name.endswith(".json")
        and path.name != "candidate.ready.json"
    ]
    if len(candidate_files) > 1:
        raise OrchestratorError("CANDIDATE_MULTIPLE")
    candidate_path = context.output_dir / "candidate.json"
    marker_path = context.output_dir / "candidate.ready.json"
    if len(candidate_files) != 1 or not candidate_path.is_file():
        raise OrchestratorError("CANDIDATE_NOT_PRODUCED")
    if not marker_path.is_file():
        raise OrchestratorError("CANDIDATE_NOT_PRODUCED")
    candidate_raw = _read_regular_unrestricted(
        candidate_path,
        "CANDIDATE_INVALID",
    )
    marker = _strict_json_object(marker_path, "CANDIDATE_INVALID")
    if (
        set(marker)
        != {"candidate_sha256", "projection_sha256", "request_id"}
        or marker.get("candidate_sha256")
        != hashlib.sha256(candidate_raw).hexdigest()
        or marker.get("projection_sha256") != projection.get("projection_sha256")
        or marker.get("request_id") != context.request_id
    ):
        raise OrchestratorError("CANDIDATE_INVALID")
    try:
        value = json.loads(candidate_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestratorError("CANDIDATE_INVALID") from exc
    if not isinstance(value, dict):
        raise OrchestratorError("CANDIDATE_INVALID")
    if (
        value.get("request_id") != context.request_id
        or value.get("symbol") != "000403.SZ"
        or value.get("projection_sha256") != projection.get("projection_sha256")
        or not isinstance(value.get("claims_candidate"), dict)
    ):
        raise OrchestratorError("CANDIDATE_INVALID")
    return value


def _host_validate_and_render(
    repo_root: Path,
    candidate: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    evidence = validate_isolated_candidate(
        repo_root,
        candidate,
        projection,
    )
    if (
        evidence.errors
        or evidence.claims_status != CLAIMS_VALID
        or evidence.renderer_status != "RENDERED_VALID"
        or evidence.trading_claim_count != 0
        or evidence.sensitive_hit_count != 0
    ):
        raise OrchestratorError("CANDIDATE_INVALID")
    try:
        parsed = WorkerClaimsCandidate.model_validate(candidate)
        scope = projection["safe_facts"]["scope"]
        document = ClaimsDocument.model_validate(
            {
                "claims_schema_version": 1,
                "source_system": "tickflow-stock-panel",
                "symbol": parsed.symbol,
                "name": parsed.name,
                "trade_date": parsed.trade_date,
                "timezone": parsed.timezone,
                "facts_binding": {
                    "facts_file": "reports/phase2_facts/000403SZ_facts.json",
                    "facts_sha256": projection["facts_sha256"],
                    "facts_schema_version": 1,
                },
                "scope": {
                    "market_scope": scope["market_scope"],
                    "financial_scope": scope["financial_scope"],
                    "news_scope": scope["news_scope"],
                    "industry_scope": "unavailable",
                },
                "claims": [
                    item.model_dump(mode="json") for item in parsed.claims
                ],
                "vendor_pending": projection["safe_facts"]["vendor_pending"],
                "trading_advice": False,
            }
        )
        validation = validate_claims_document(repo_root, document)
        if validation.status != CLAIMS_VALID:
            raise OrchestratorError("CANDIDATE_INVALID")
        rendered = render_claims_document(document, validation)
        if validate_rendered_document(document, validation, rendered):
            raise OrchestratorError("CANDIDATE_INVALID")
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        if isinstance(exc, OrchestratorError):
            raise
        raise OrchestratorError("CANDIDATE_INVALID") from exc
    return document.model_dump(mode="json"), rendered


def _publish_route(
    root: Path,
    request_id: str,
    document: Mapping[str, Any],
    rendered: str,
) -> tuple[Path, Path]:
    inbox = root / "inbox"
    inbox.mkdir(mode=0o700, exist_ok=True)
    if inbox.is_symlink() or not inbox.is_dir():
        raise OrchestratorError("inbox_invalid")
    json_path = inbox / f"{request_id}.json"
    markdown_path = inbox / f"{request_id}.md"
    _publish_private_file(json_path, canonical_json_bytes(document))
    try:
        _publish_private_file(markdown_path, rendered.encode("utf-8"))
    except Exception:
        json_path.unlink(missing_ok=True)
        raise
    return json_path, markdown_path


def _remove_route(paths: tuple[Path, Path] | None) -> None:
    if paths is None:
        return
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def _publish_rejection(root: Path, request_id: str, category: str) -> None:
    rejected = root / "rejected"
    rejected.mkdir(mode=0o700, exist_ok=True)
    path = rejected / f"{request_id}.json"
    if path.exists():
        return
    _publish_private_file(
        path,
        canonical_json_bytes(
            {
                "can_publish": False,
                "error_category": category,
                "request_id": request_id,
                "retry_count": 0,
                "route": "rejected",
            }
        ),
    )


def _result(
    *,
    terminal_state: str,
    request_id: str | None,
    ledger: AttemptLedger | None,
    provider_http_status: int | None = None,
    route: str = "none",
    host_validation_status: str = "NOT_RUN",
    cleanup: CleanupResult | None = None,
    secret_file_residue_count: int = 0,
    temporary_file_residue_count: int = 0,
    archive: ArchiveResult | None = None,
    error_category: str | None = None,
) -> CanaryRunResult:
    cleanup = cleanup or CleanupResult(completed=True)
    archive = archive or ArchiveResult(completed=False, status="NOT_RUN")
    return CanaryRunResult(
        terminal_state=terminal_state,
        request_id=request_id,
        attempt_ledger_state=ledger.state if ledger else None,
        provider_attempt_count=ledger.provider_attempt_count if ledger else 0,
        retry_count=0,
        provider_http_status=provider_http_status,
        route=route,
        host_validation_status=host_validation_status,
        container_residue_count=cleanup.container_residue_count,
        network_residue_count=cleanup.network_residue_count,
        secret_file_residue_count=secret_file_residue_count,
        temporary_file_residue_count=temporary_file_residue_count,
        receipt_archive_status=archive.status,
        last_proven_stage=archive.last_proven_stage,
        child_terminal_reason=archive.child_terminal_reason,
        error_category=error_category,
    )


def _terminal_category(error: BaseException) -> str:
    value = str(error).strip()
    if not value:
        return "INTERNAL_ERROR"
    return value.upper()


def _publish_runtime_evidence(
    root: Path,
    artifacts: ApprovedCanaryArtifacts,
    result: CanaryRunResult,
    ledger: AttemptLedger,
) -> None:
    evidence_root = root / "evidence"
    evidence_root.mkdir(mode=0o700, exist_ok=True)
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise OrchestratorError("runtime_evidence_root_invalid")
    value = {
        "runtime_evidence_schema_version": 1,
        "runtime_contract_version": 1,
        "request_id": ledger.identity.request_id,
        "symbol": ledger.identity.symbol,
        "approval_candidate_sha256": artifacts.approval_candidate_sha256,
        "proxy_image_id": artifacts.proxy_image_id,
        "relay_image_id": artifacts.relay_image_id,
        "orchestrator_source_sha256": artifacts.orchestrator_source_sha256,
        "timeout_contract_sha256": artifacts.timeout_contract_sha256,
        "attempt_ledger_state": ledger.state,
        "dispatch_started_at": ledger.dispatch_started_at,
        "response_received_at": ledger.response_received_at,
        "candidate_ready_at": ledger.candidate_ready_at,
        "host_validation_completed_at": ledger.host_validation_completed_at,
        "cleanup_completed_at": ledger.cleanup_completed_at,
        "provider_attempt_count": result.provider_attempt_count,
        "retry_count": result.retry_count,
        "provider_http_status": result.provider_http_status,
        "terminal_state": result.terminal_state,
        "route": result.route,
        "can_publish": False,
        "manual_review": "PENDING",
        "container_residue_count": result.container_residue_count,
        "network_residue_count": result.network_residue_count,
        "secret_file_residue_count": result.secret_file_residue_count,
        "temporary_file_residue_count": result.temporary_file_residue_count,
        "receipt_archive_status": result.receipt_archive_status,
        "last_proven_stage": result.last_proven_stage,
        "child_terminal_reason": result.child_terminal_reason,
    }
    _publish_private_file(
        evidence_root / f"{ledger.identity.request_id}.json",
        canonical_json_bytes(value),
    )


def _with_runtime_evidence(
    root: Path,
    artifacts: ApprovedCanaryArtifacts,
    result: CanaryRunResult,
    ledger: AttemptLedger | None,
    *,
    success_route_paths: tuple[Path, Path] | None = None,
) -> CanaryRunResult:
    if ledger is None:
        return result
    try:
        _publish_runtime_evidence(root, artifacts, result, ledger)
    except OrchestratorError:
        _remove_route(success_route_paths)
        with contextlib.suppress(OrchestratorError):
            _publish_rejection(
                root,
                ledger.identity.request_id,
                "RUNTIME_EVIDENCE_PUBLISH_FAILED",
            )
        return replace(
            result,
            terminal_state="RUNTIME_EVIDENCE_PUBLISH_FAILED",
            error_category="RUNTIME_EVIDENCE_PUBLISH_FAILED",
            route="rejected",
        )
    return result


def run_single_symbol_canary(
    *,
    config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: Mapping[str, Any],
    backend: CanaryBackend,
    artifact_verifier: Callable[[ApprovedCanaryArtifacts], None],
    secret_reader: Callable[[], str],
    request_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    wall_clock: Callable[[], str],
    monotonic: Callable[[], float] = time.monotonic,
) -> CanaryRunResult:
    """Execute one complete attempt; no code path retries a dispatch."""
    request_id: str | None = None
    ledger: AttemptLedger | None = None
    store: AttemptLedgerStore | None = None
    context: CanaryRuntimeContext | None = None
    cleanup = CleanupResult(completed=True)
    archive = ArchiveResult(completed=False, status="NOT_RUN")
    route_paths: tuple[Path, Path] | None = None
    provider_http_status: int | None = None
    host_validation_status = "NOT_RUN"
    prepared = False
    workdir: Path | None = None

    def archive_runtime_evidence() -> ArchiveResult:
        nonlocal archive
        if archive.status != "NOT_RUN":
            return archive
        if (
            context is None
            or ledger is None
        ):
            return archive
        try:
            archive = backend.archive_evidence(
                context,
                deadline=(
                    monotonic()
                    + context.runtime_contract["cleanup_timeout_seconds"]
                ),
            )
        except Exception:
            archive = ArchiveResult(
                completed=False,
                status="RECEIPT_ARCHIVE_FAILED",
            )
        return archive

    def finalize_runtime() -> tuple[CleanupResult, int, int]:
        nonlocal cleanup, prepared, workdir
        if prepared and context is not None:
            try:
                cleanup = backend.cleanup(
                    context,
                    deadline=(
                        monotonic()
                        + context.runtime_contract["cleanup_timeout_seconds"]
                    ),
                )
            except Exception:
                cleanup = CleanupResult(completed=False, timed_out=True)
            prepared = False
        secret_residue = 0
        if context is not None:
            with contextlib.suppress(OSError):
                context.secret_path.unlink(missing_ok=True)
            secret_residue = int(context.secret_path.exists())
        temporary_residue = 0
        if workdir is not None:
            try:
                shutil.rmtree(workdir)
            except OSError:
                temporary_residue = int(workdir.exists())
            else:
                workdir = None
        return cleanup, secret_residue, temporary_residue
    lock = ExclusiveCanaryLock(
        config.state_root,
        approval_candidate_sha256=artifacts.approval_candidate_sha256,
        pid=os.getpid(),
        started_at=wall_clock(),
    )
    try:
        lock.acquire()
    except OrchestratorError as exc:
        category = _terminal_category(exc)
        return _result(
            terminal_state=category,
            request_id=None,
            ledger=None,
            error_category=category,
        )

    try:
        artifact_verifier(artifacts)
        facts_raw = _read_regular_unrestricted(
            config.facts_path,
            "FACTS_FILE_INVALID",
        )
        if (
            hashlib.sha256(facts_raw).hexdigest() != artifacts.facts_sha256
            or projection.get("facts_sha256") != artifacts.facts_sha256
            or projection.get("projection_sha256")
            != artifacts.projection_sha256
            or compute_projection_sha256(projection)
            != artifacts.projection_sha256
        ):
            raise OrchestratorError("PROJECTION_BINDING_INVALID")
        runtime = load_runtime_contract().model_dump(mode="json")
        if runtime_contract_sha256() != artifacts.timeout_contract_sha256:
            raise OrchestratorError("TIMEOUT_CONTRACT_MISMATCH")

        store = AttemptLedgerStore(config.state_root)
        if store.path.exists() or store.path.is_symlink():
            existing = recover_attempt_state(store, timestamp=wall_clock())
            category = (
                "ATTEMPT_CONSUMED_UNKNOWN"
                if existing.provider_attempt_count
                else "EXISTING_ATTEMPT_LEDGER"
            )
            return _result(
                terminal_state=category,
                request_id=existing.identity.request_id,
                ledger=existing,
                error_category=category,
            )

        request_id = request_id_factory()
        identity = CanaryRunIdentity(
            request_id=request_id,
            symbol="000403.SZ",
            trade_date="2026-07-31",
            provider="openai",
            endpoint_alias="openai_responses_v1",
            **artifacts.model_dump(
                mode="python",
                exclude={"readiness_contract_sha256"},
            ),
        )
        ledger = store.prepare(identity, timestamp=wall_clock())
        workdir = Path(tempfile.mkdtemp(prefix="phase2-canary-", dir=config.work_root))
        workdir.chmod(0o700)
        input_dir = workdir / "input"
        output_dir = workdir / "output"
        proxy_output_dir = workdir / "proxy-output"
        input_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o733)
        proxy_output_dir.mkdir(mode=0o733)
        receipts_root = config.canary_output_root / "receipts"
        receipts_root.mkdir(mode=0o700, exist_ok=True)
        if (
            receipts_root.is_symlink()
            or not receipts_root.is_dir()
            or stat.S_IMODE(receipts_root.stat().st_mode) != 0o700
        ):
            raise OrchestratorError("RECEIPT_ARCHIVE_ROOT_INVALID")
        projection_path = input_dir / "projection.json"
        request_path = input_dir / "request.json"
        secret_path = workdir / "provider-auth"
        _write_private_file(projection_path, canonical_json_bytes(projection))
        projection_path.chmod(0o444)
        request = build_relay_request(
            projection,
            request_id=request_id,
        ).model_dump(mode="json")
        _write_private_file(request_path, canonical_json_bytes(request))
        request_path.chmod(0o444)
        secret = secret_reader()
        if (
            not isinstance(secret, str)
            or not secret
            or secret != secret.strip()
            or any(character in secret for character in ("\x00", "\r", "\n"))
        ):
            raise OrchestratorError("SECRET_INVALID")
        _write_private_file(secret_path, secret.encode("utf-8"))
        del secret
        context = CanaryRuntimeContext(
            request_id=request_id,
            request_path=request_path,
            projection_path=projection_path,
            output_dir=output_dir,
            proxy_output_dir=proxy_output_dir,
            receipt_archive_dir=receipts_root / request_id,
            secret_path=secret_path,
            proxy_image_id=artifacts.proxy_image_id,
            relay_image_id=artifacts.relay_image_id,
            runtime_contract=runtime,
        )
        host_deadline = (
            monotonic() + runtime["host_orchestrator_timeout_seconds"]
        )
        prepared = True
        backend.prepare(context, deadline=host_deadline)
        ledger = store.transition(
            LedgerState.NETWORK_DISPATCH_STARTED,
            timestamp=wall_clock(),
        )
        dispatch = backend.dispatch(context, deadline=host_deadline)
        if monotonic() > host_deadline:
            raise BackendError("ORCHESTRATOR_TIMEOUT")
        provider_http_status = dispatch.provider_http_status
        if dispatch.response_received:
            ledger = store.transition(
                LedgerState.PROVIDER_RESPONSE_RECEIVED,
                timestamp=wall_clock(),
            )
        relay = _collect_candidate(context, projection)
        if monotonic() > host_deadline:
            raise BackendError("ORCHESTRATOR_TIMEOUT")
        ledger = store.transition(
            LedgerState.CANDIDATE_COLLECTED,
            timestamp=wall_clock(),
        )
        document, rendered = _host_validate_and_render(
            config.repo_root,
            relay["claims_candidate"],
            projection,
        )
        if monotonic() > host_deadline:
            raise BackendError("ORCHESTRATOR_TIMEOUT")
        host_validation_status = "VALID"
        route_paths = _publish_route(
            config.canary_output_root,
            request_id,
            document,
            rendered,
        )
        ledger = store.transition(
            LedgerState.HOST_VALIDATION_COMPLETED,
            timestamp=wall_clock(),
        )

        archive = archive_runtime_evidence()
        if not archive.completed or archive.status != "ARCHIVED":
            raise BackendError(archive.status)
        cleanup, secret_residue, temporary_residue = finalize_runtime()
        if cleanup.timed_out or not cleanup.completed:
            raise OrchestratorError("CLEANUP_TIMEOUT")
        if cleanup.container_residue_count or cleanup.network_residue_count:
            raise OrchestratorError("CLEANUP_RESIDUE")
        if secret_residue:
            raise OrchestratorError("SECRET_FILE_RESIDUE")
        if temporary_residue:
            raise OrchestratorError("TEMPORARY_FILE_RESIDUE")
        ledger = store.transition(
            LedgerState.CLEANUP_COMPLETED,
            timestamp=wall_clock(),
        )
        return _with_runtime_evidence(
            config.canary_output_root,
            artifacts,
            _result(
                terminal_state="SUCCEEDED",
                request_id=request_id,
                ledger=ledger,
                provider_http_status=provider_http_status,
                route="inbox",
                host_validation_status=host_validation_status,
                cleanup=cleanup,
                secret_file_residue_count=secret_residue,
                temporary_file_residue_count=temporary_residue,
                archive=archive,
            ),
            ledger,
            success_route_paths=route_paths,
        )
    except BackendError as exc:
        archive = archive_runtime_evidence()
        category = (
            "ATTEMPT_CONSUMED_UNKNOWN"
            if exc.unknown_dispatch_state
            else exc.category
        )
        unknown_dispatch_state = exc.unknown_dispatch_state
        if archive.child_terminal_reason is not None:
            category = archive.child_terminal_reason
            unknown_dispatch_state = category.endswith("_EXIT_UNKNOWN")
        if (
            ledger is not None
            and ledger.provider_attempt_count > 0
            and archive.status
            not in {
                "NOT_RUN",
                "ARCHIVED",
                "PRE_DISPATCH_ARCHIVED",
            }
        ):
            category = archive.status
            unknown_dispatch_state = False
        if store is not None and ledger is not None:
            target = (
                LedgerState.ATTEMPT_CONSUMED_UNKNOWN
                if unknown_dispatch_state
                else (
                    LedgerState.FAILED_AFTER_DISPATCH
                    if ledger.provider_attempt_count
                    else LedgerState.FAILED_BEFORE_DISPATCH
                )
            )
            if ledger.state not in _TERMINAL_STATES:
                ledger = store.transition(target, timestamp=wall_clock())
        _remove_route(route_paths)
        cleanup, secret_residue, temporary_residue = finalize_runtime()
        if not unknown_dispatch_state:
            if cleanup.timed_out or not cleanup.completed:
                category = "CLEANUP_TIMEOUT"
            elif cleanup.container_residue_count or cleanup.network_residue_count:
                category = "CLEANUP_RESIDUE"
            elif secret_residue:
                category = "SECRET_FILE_RESIDUE"
            elif temporary_residue:
                category = "TEMPORARY_FILE_RESIDUE"
        if request_id is not None:
            _publish_rejection(config.canary_output_root, request_id, category)
        return _with_runtime_evidence(
            config.canary_output_root,
            artifacts,
            _result(
                terminal_state=category,
                request_id=request_id,
                ledger=ledger,
                provider_http_status=provider_http_status,
                route="rejected" if request_id else "none",
                host_validation_status=host_validation_status,
                cleanup=cleanup,
                secret_file_residue_count=secret_residue,
                temporary_file_residue_count=temporary_residue,
                archive=archive,
                error_category=category,
            ),
            ledger,
        )
    except Exception as exc:
        archive = archive_runtime_evidence()
        category = _terminal_category(exc)
        if archive.child_terminal_reason is not None:
            category = archive.child_terminal_reason
        if (
            ledger is not None
            and ledger.provider_attempt_count > 0
            and archive.status
            not in {
                "NOT_RUN",
                "ARCHIVED",
                "PRE_DISPATCH_ARCHIVED",
            }
        ):
            category = archive.status
        if store is not None and ledger is not None and ledger.state not in _TERMINAL_STATES:
            target = (
                LedgerState.FAILED_AFTER_DISPATCH
                if ledger.provider_attempt_count
                else LedgerState.FAILED_BEFORE_DISPATCH
            )
            ledger = store.transition(target, timestamp=wall_clock())
        _remove_route(route_paths)
        cleanup, secret_residue, temporary_residue = finalize_runtime()
        if cleanup.timed_out or not cleanup.completed:
            category = "CLEANUP_TIMEOUT"
        elif cleanup.container_residue_count or cleanup.network_residue_count:
            category = "CLEANUP_RESIDUE"
        elif secret_residue:
            category = "SECRET_FILE_RESIDUE"
        elif temporary_residue:
            category = "TEMPORARY_FILE_RESIDUE"
        if request_id is not None:
            with contextlib.suppress(OrchestratorError):
                _publish_rejection(config.canary_output_root, request_id, category)
        return _with_runtime_evidence(
            config.canary_output_root,
            artifacts,
            _result(
                terminal_state=category,
                request_id=request_id,
                ledger=ledger,
                provider_http_status=provider_http_status,
                route="rejected" if request_id else "none",
                host_validation_status=host_validation_status,
                cleanup=cleanup,
                secret_file_residue_count=secret_residue,
                temporary_file_residue_count=temporary_residue,
                archive=archive,
                error_category=category,
            ),
            ledger,
        )
    finally:
        if prepared or workdir is not None:
            finalize_runtime()
        lock.release()


def _mock_stage_event(
    name: str,
    *,
    occurred: bool,
    index: int,
    http_status: int | None = None,
    byte_count: int | None = None,
) -> dict[str, Any]:
    return {
        "event": name,
        "occurred": occurred,
        "wall_time": (
            f"2026-08-08T08:23:{index:02d}.000000Z" if occurred else None
        ),
        "monotonic_ns": index * 1_000_000_000 if occurred else None,
        "http_status": http_status if occurred else None,
        "byte_count": byte_count if occurred else None,
    }


class MockCanaryBackend:
    """Deterministic local fault injector; it never opens a socket."""

    FAULT_CATALOG = (
        "provider_1s",
        "provider_59s",
        "provider_60s",
        "provider_over_60s",
        "relay_exit_before_candidate",
        "proxy_exit_after_request",
        "host_crash_before_dispatch",
        "host_crash_after_dispatch",
        "host_crash_after_response",
        "candidate_partial_write",
        "candidate_rename_failure",
        "candidate_missing",
        "candidate_multiple",
        "nonzero_container_exit",
        "cleanup_timeout",
        "duplicate_request_id",
        "concurrent_orchestrator",
        "stale_ledger",
        "stale_lock",
        "runtime_residue",
    )

    def __init__(self, *, scenario: str, relay_response: Mapping[str, Any]) -> None:
        if scenario not in self.FAULT_CATALOG:
            raise OrchestratorError("mock_scenario_invalid")
        self.scenario = scenario
        self.relay_response = dict(relay_response)
        self.prepare_count = 0
        self.dispatch_count = 0
        self.archive_count = 0
        self.cleanup_count = 0
        self.lifecycle: list[str] = []
        self._child_evidence: dict[str, ChildProcessEvidence] = {}
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    def prepare(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> None:
        del deadline
        self.lifecycle.append("prepare")
        self.prepare_count += 1
        if self.scenario == "host_crash_before_dispatch":
            proxy_events = {
                name: _mock_stage_event(
                    name,
                    occurred=name in {"process_started", "proxy_ready"},
                    index=index,
                )
                for index, name in enumerate(_PROXY_EVENTS, start=1)
            }
            _write_private_file(
                context.proxy_output_dir / "proxy-receipt.json",
                canonical_json_bytes(
                    {
                        "receipt_schema_version": 2,
                        "request_id": context.request_id,
                        "component": "proxy",
                        "method_allowed": False,
                        "path_allowed": False,
                        "auth_present": False,
                        "tls_verification": True,
                        "redirect_followed": False,
                        "provider_attempt_count": 0,
                        "retry_count": 0,
                        "provider_http_status": None,
                        "response_category": "PROXY_READY",
                        "response_size": None,
                        "response_bytes": None,
                        "terminal_status": "PROXY_READY",
                        **proxy_events,
                    }
                ),
            )
            timing = ChildExecutionTiming(
                started_at="2026-08-08T08:23:20.000000Z",
                exited_at="2026-08-08T08:23:20.000000Z",
                started_monotonic_ns=20_000_000_000,
                exited_monotonic_ns=20_000_000_000,
            )
            self._child_evidence["proxy"] = classify_running_child(
                "proxy",
                timing,
            )
            raise BackendError("HOST_CRASH_BEFORE_DISPATCH")

    def _publish(self, context: CanaryRuntimeContext, value: Mapping[str, Any]) -> None:
        raw = canonical_json_bytes(value)
        candidate = context.output_dir / "candidate.json"
        marker = context.output_dir / "candidate.ready.json"
        _publish_private_file(candidate, raw)
        _publish_private_file(
            marker,
            canonical_json_bytes(
                {
                    "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                    "projection_sha256": value["projection_sha256"],
                    "request_id": value["request_id"],
                }
            ),
        )

    def _write_receipts(self, context: CanaryRuntimeContext) -> None:
        relay_failed = self.scenario in {
            "provider_over_60s",
            "relay_exit_before_candidate",
            "nonzero_container_exit",
            "proxy_exit_after_request",
            "host_crash_after_dispatch",
            "host_crash_after_response",
        }
        candidate_published = self.scenario not in {
            "provider_over_60s",
            "relay_exit_before_candidate",
            "nonzero_container_exit",
            "proxy_exit_after_request",
            "host_crash_after_dispatch",
            "host_crash_after_response",
            "candidate_partial_write",
            "candidate_rename_failure",
            "candidate_missing",
        }
        proxy_events: dict[str, dict[str, Any]] = {}
        for index, name in enumerate(_PROXY_EVENTS, start=1):
            occurred = not (
                self.scenario == "proxy_exit_after_request"
                and name
                in {
                    "request_write_completed",
                    "response_headers_received",
                    "response_body_completed",
                }
            )
            proxy_events[name] = _mock_stage_event(
                name,
                occurred=occurred,
                index=index,
                http_status=(
                    200
                    if name
                    in {"response_headers_received", "response_body_completed"}
                    and occurred
                    else None
                ),
                byte_count=(
                    1
                    if name in {"request_write_completed", "response_body_completed"}
                    and occurred
                    else None
                ),
            )
        proxy_category = (
            "REQUEST_WRITE_FAILED"
            if self.scenario == "proxy_exit_after_request"
            else "FORWARDED"
        )
        proxy_receipt = {
            "receipt_schema_version": 2,
            "request_id": context.request_id,
            "component": "proxy",
            "method_allowed": True,
            "path_allowed": True,
            "auth_present": True,
            "tls_verification": True,
            "redirect_followed": False,
            "provider_attempt_count": 1,
            "retry_count": 0,
            "provider_http_status": (
                None if self.scenario == "proxy_exit_after_request" else 200
            ),
            "response_category": proxy_category,
            "response_size": (
                None if self.scenario == "proxy_exit_after_request" else 1
            ),
            "response_bytes": (
                None if self.scenario == "proxy_exit_after_request" else 1
            ),
            "terminal_status": proxy_category,
            **proxy_events,
        }
        relay_events: dict[str, dict[str, Any]] = {}
        for index, name in enumerate(_RELAY_EVENTS, start=20):
            occurred = name not in {
                "candidate_write_started",
                "candidate_write_completed",
                "candidate_ready_published",
            } or candidate_published
            relay_events[name] = _mock_stage_event(
                name,
                occurred=occurred,
                index=index,
                http_status=(200 if name == "response_received" and occurred else None),
                byte_count=(1 if name in {"request_submitted", "response_received"} and occurred else None),
            )
        relay_terminal = (
            "RELAY_NONZERO_EXIT"
            if self.scenario in {"relay_exit_before_candidate", "nonzero_container_exit"}
            else "RELAY_TIMEOUT"
            if self.scenario == "provider_over_60s"
            else "RELAY_PROCESS_ERROR"
            if relay_failed
            else "RELAY_COMPLETED"
        )
        relay_terminal_source = (
            "relay_self"
            if self.scenario in {"provider_over_60s"} or not relay_failed
            else "host_child_process"
        )
        relay_receipt = {
            "receipt_schema_version": 2,
            "request_id": context.request_id,
            "component": "relay",
            "status": "REJECTED" if relay_failed else "SUCCEEDED",
            "exit_code": 2 if relay_failed else 0,
            "terminal_status": relay_terminal,
            "terminal_reason_source": relay_terminal_source,
            "error_category": relay_terminal if relay_failed else None,
            "proxy_http_status": 200,
            "proxy_request_count": 1,
            "retry_count": 0,
            "response_size": 1,
            "response_sha256": "0" * 64,
            "started_at": "2026-08-08T08:23:20.000000Z",
            "completed_at": "2026-08-08T08:23:40.000000Z",
            **relay_events,
        }
        _write_private_file(
            context.proxy_output_dir / "proxy-receipt.json",
            canonical_json_bytes(proxy_receipt),
        )
        _write_private_file(
            context.output_dir / "relay-receipt.json",
            canonical_json_bytes(relay_receipt),
        )

    def _record_child_evidence(self) -> None:
        timing = ChildExecutionTiming(
            started_at="2026-08-08T08:23:20.000000Z",
            exited_at="2026-08-08T08:23:40.000000Z",
            started_monotonic_ns=20_000_000_000,
            exited_monotonic_ns=40_000_000_000,
        )
        relay_code = (
            1
            if self.scenario in {"relay_exit_before_candidate", "nonzero_container_exit"}
            else 0
        )
        self._child_evidence["relay"] = classify_completed_child(
            "relay",
            subprocess.CompletedProcess(
                ["mock-relay"],
                relay_code,
                stdout="",
                stderr="mock relay failure" if relay_code else "",
            ),
            timing,
        )
        if self.scenario == "proxy_exit_after_request":
            self._child_evidence["proxy"] = classify_completed_child(
                "proxy",
                subprocess.CompletedProcess(
                    ["mock-proxy"],
                    1,
                    stdout="",
                    stderr="mock proxy failure",
                ),
                timing,
            )
        else:
            self._child_evidence["proxy"] = classify_running_child(
                "proxy",
                timing,
            )

    def dispatch(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> BackendDispatchResult:
        del deadline
        self.lifecycle.append("dispatch")
        self.dispatch_count += 1
        if self.dispatch_count > 1:
            raise AssertionError("mock provider dispatched more than once")
        self._write_receipts(context)
        self._record_child_evidence()
        delays = {
            "provider_1s": 1.0,
            "provider_59s": 59.0,
            "provider_60s": 60.0,
            "provider_over_60s": 60.000_001,
        }
        self._now += delays.get(self.scenario, 1.0)
        if self.scenario == "provider_over_60s":
            raise BackendError("PROVIDER_TIMEOUT")
        if self.scenario in {"proxy_exit_after_request", "host_crash_after_dispatch"}:
            raise BackendError(
                "PROXY_EXITED",
                unknown_dispatch_state=True,
            )
        if self.scenario == "host_crash_after_response":
            raise BackendError(
                "HOST_CRASH_AFTER_RESPONSE",
                unknown_dispatch_state=True,
            )
        if self.scenario == "relay_exit_before_candidate":
            raise BackendError("CANDIDATE_NOT_PRODUCED")
        if self.scenario == "nonzero_container_exit":
            raise BackendError("CONTAINER_EXIT_NONZERO")
        if self.scenario == "candidate_partial_write":
            (context.output_dir / ".candidate.json.synthetic.partial").write_bytes(
                b"{\n"
            )
            return BackendDispatchResult(200, True)
        if self.scenario in {"candidate_rename_failure", "candidate_missing"}:
            return BackendDispatchResult(200, True)
        value = dict(self.relay_response)
        if self.scenario == "duplicate_request_id":
            value["request_id"] = "b" * 32
        self._publish(context, value)
        if self.scenario == "candidate_multiple":
            _publish_private_file(
                context.output_dir / "candidate.extra.json",
                canonical_json_bytes(value),
            )
        return BackendDispatchResult(200, True)

    def archive_evidence(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> ArchiveResult:
        del deadline
        self.lifecycle.append("archive")
        self.archive_count += 1
        return _archive_stage_evidence(context, self._child_evidence)

    def cleanup(
        self,
        _context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> CleanupResult:
        del deadline
        self.lifecycle.append("cleanup")
        self.cleanup_count += 1
        if self.scenario == "cleanup_timeout":
            return CleanupResult(completed=False, timed_out=True)
        if self.scenario == "runtime_residue":
            return CleanupResult(
                completed=True,
                container_residue_count=1,
                network_residue_count=1,
            )
        return CleanupResult(completed=True)


CommandExecutor = Callable[
    [list[str], float],
    subprocess.CompletedProcess[str],
]


def _default_command_executor(
    command: list[str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=timeout,
        env={"PATH": os.environ.get("PATH", "")},
    )


def _mount(source: Path, destination: str, *, readonly: bool) -> str:
    suffix = ",readonly" if readonly else ""
    return f"type=bind,src={source},dst={destination}{suffix}"


def _hardened_create_prefix(name: str, network: str) -> list[str]:
    return [
        "docker",
        "create",
        "--name",
        name,
        "--network",
        network,
        "--read-only",
        "--user",
        "65532:65532",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "16",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--ipc",
        "none",
        "--restart",
        "no",
    ]


class DockerCanaryBackend:
    """Production one-shot Docker lifecycle; construction performs no I/O."""

    def __init__(
        self,
        *,
        executor: CommandExecutor = _default_command_executor,
        monotonic: Callable[[], float] = time.monotonic,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_clock: Callable[[], str] = _wall_timestamp,
        waiter: Callable[[float], None] = time.sleep,
    ) -> None:
        self.executor = executor
        self.monotonic = monotonic
        self.monotonic_ns = monotonic_ns
        self.wall_clock = wall_clock
        self.waiter = waiter
        self._names: dict[str, str] = {}
        self._child_evidence: dict[str, ChildProcessEvidence] = {}
        self._validated_receipts: dict[str, bytes] = {}
        self._proxy_started_at: tuple[str, int] | None = None

    def _remaining(self, deadline: float, category: str) -> float:
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise BackendError(category)
        return remaining

    def _run(
        self,
        command: list[str],
        *,
        deadline: float,
        category: str,
        maximum_timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        remaining = self._remaining(deadline, category)
        timeout = min(remaining, maximum_timeout or remaining)
        try:
            result = self.executor(command, timeout)
        except subprocess.TimeoutExpired as exc:
            raise BackendError(category) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackendError(category) from exc
        if result.returncode != 0:
            raise BackendError(category)
        return result

    def _run_child(
        self,
        command: list[str],
        *,
        component: Literal["relay", "proxy"],
        deadline: float,
        maximum_timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        started_at = self.wall_clock()
        started_monotonic_ns = self.monotonic_ns()
        remaining = self._remaining(deadline, f"{component.upper()}_TIMEOUT")
        timeout = min(remaining, maximum_timeout or remaining)
        host_timeout_is_binding = (
            maximum_timeout is not None and remaining < maximum_timeout
        )
        try:
            result = self.executor(command, timeout)
        except Exception as exc:
            timing = ChildExecutionTiming(
                started_at=started_at,
                exited_at=self.wall_clock(),
                started_monotonic_ns=started_monotonic_ns,
                exited_monotonic_ns=max(
                    started_monotonic_ns,
                    self.monotonic_ns(),
                ),
            )
            evidence = classify_child_exception(component, exc, timing)
            if isinstance(exc, subprocess.TimeoutExpired) and host_timeout_is_binding:
                evidence = replace(
                    evidence,
                    terminal_reason="ORCHESTRATOR_TIMEOUT",
                )
            self._child_evidence[component] = evidence
            raise BackendError(
                evidence.terminal_reason,
                unknown_dispatch_state=(
                    evidence.child_state is ChildState.EXIT_UNKNOWN
                ),
            ) from exc
        timing = ChildExecutionTiming(
            started_at=started_at,
            exited_at=self.wall_clock(),
            started_monotonic_ns=started_monotonic_ns,
            exited_monotonic_ns=max(started_monotonic_ns, self.monotonic_ns()),
        )
        evidence = classify_completed_child(
            component,
            result,
            timing,
            interpret_positive_signal=True,
        )
        self._child_evidence[component] = evidence
        if evidence.child_state is not ChildState.EXITED_ZERO:
            raise BackendError(
                evidence.terminal_reason,
                unknown_dispatch_state=(
                    evidence.child_state is ChildState.EXIT_UNKNOWN
                ),
            )
        return result

    def _capture_proxy_evidence(self, *, deadline: float) -> None:
        if "proxy" in self._child_evidence:
            return
        if self._proxy_started_at is None or not self._names:
            return
        started_at, started_monotonic_ns = self._proxy_started_at
        try:
            remaining = self._remaining(deadline, "PROXY_TIMEOUT")
            result = self.executor(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .State}}",
                    self._names["proxy"],
                ],
                remaining,
            )
            if result.returncode != 0:
                raise subprocess.SubprocessError("proxy inspect failed")
            state = json.loads(result.stdout)
            if not isinstance(state, dict) or not isinstance(
                state.get("Running"),
                bool,
            ):
                raise RuntimeError("proxy state unknown")
            timing = ChildExecutionTiming(
                started_at=started_at,
                exited_at=self.wall_clock(),
                started_monotonic_ns=started_monotonic_ns,
                exited_monotonic_ns=max(
                    started_monotonic_ns,
                    self.monotonic_ns(),
                ),
            )
            if state["Running"]:
                evidence = classify_running_child("proxy", timing)
            else:
                exit_code = state.get("ExitCode")
                evidence = classify_completed_child(
                    "proxy",
                    subprocess.CompletedProcess(
                        ["docker", "inspect", self._names["proxy"]],
                        exit_code,
                        stdout="",
                        stderr=(
                            state.get("Error")
                            if isinstance(state.get("Error"), str)
                            else ""
                        ),
                    ),
                    timing,
                    interpret_positive_signal=True,
                )
        except Exception as exc:
            timing = ChildExecutionTiming(
                started_at=started_at,
                exited_at=self.wall_clock(),
                started_monotonic_ns=started_monotonic_ns,
                exited_monotonic_ns=max(
                    started_monotonic_ns,
                    self.monotonic_ns(),
                ),
            )
            evidence = classify_child_exception("proxy", exc, timing)
        self._child_evidence["proxy"] = evidence

    def _runtime_names(self, request_id: str) -> dict[str, str]:
        suffix = request_id[:12]
        return {
            "relay_network": f"phase2-canary-relay-{suffix}",
            "egress_network": f"phase2-canary-egress-{suffix}",
            "proxy": f"phase2-openai-proxy-{suffix}",
            "relay": f"phase2-canary-relay-worker-{suffix}",
        }

    def _wait_for_proxy_ready(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> None:
        ready_path = context.proxy_output_dir / "proxy-ready.json"
        readiness_deadline = min(
            deadline,
            self.monotonic()
            + float(context.runtime_contract["provider_connect_timeout_seconds"]),
        )
        while self.monotonic() <= readiness_deadline:
            if ready_path.exists() or ready_path.is_symlink():
                try:
                    raw = _read_regular_unrestricted(
                        ready_path,
                        "PROXY_READINESS_INVALID",
                    )
                    value = _strict_json_bytes(raw, "PROXY_READINESS_INVALID")
                except OrchestratorError as exc:
                    raise BackendError("PROXY_READINESS_INVALID") from exc
                if (
                    set(value) != _PROXY_READY_FIELDS
                    or value.get("request_id") != context.request_id
                    or value.get("proxy_ready") is not True
                    or value.get("listener_ready") is not True
                    or not isinstance(value.get("wall_time"), str)
                    or not value["wall_time"]
                    or isinstance(value.get("monotonic_ns"), bool)
                    or not isinstance(value.get("monotonic_ns"), int)
                    or value["monotonic_ns"] < 0
                    or raw != canonical_json_bytes(value)
                    or _SENSITIVE_EVIDENCE.search(raw.decode("utf-8"))
                ):
                    raise BackendError("PROXY_READINESS_INVALID")
                return
            remaining = readiness_deadline - self.monotonic()
            if remaining <= 0:
                break
            self.waiter(min(0.05, remaining))
        raise BackendError("PROXY_READINESS_TIMEOUT")

    def prepare(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> None:
        self._names = self._runtime_names(context.request_id)
        self._child_evidence = {}
        self._validated_receipts = {}
        self._proxy_started_at = None
        names = self._names
        commands = [
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--internal",
                names["relay_network"],
            ],
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                names["egress_network"],
            ],
            [
                *_hardened_create_prefix(
                    names["proxy"],
                    names["relay_network"],
                ),
                "--network-alias",
                "phase2-egress-proxy",
                "--mount",
                _mount(
                    context.secret_path,
                    "/run/phase2/provider-auth",
                    readonly=True,
                ),
                "--mount",
                _mount(
                    context.proxy_output_dir,
                    "/output",
                    readonly=False,
                ),
                "--mount",
                _mount(
                    context.request_path,
                    "/input/request.json",
                    readonly=True,
                ),
                context.proxy_image_id,
            ],
            [
                "docker",
                "network",
                "connect",
                names["egress_network"],
                names["proxy"],
            ],
            [
                *_hardened_create_prefix(
                    names["relay"],
                    names["relay_network"],
                ),
                "--mount",
                _mount(
                    context.request_path,
                    "/input/request.json",
                    readonly=True,
                ),
                "--mount",
                _mount(
                    context.projection_path,
                    "/input/projection.json",
                    readonly=True,
                ),
                "--mount",
                _mount(context.output_dir, "/output", readonly=False),
                context.relay_image_id,
            ],
            ["docker", "start", names["proxy"]],
        ]
        for command in commands:
            if command == ["docker", "start", names["proxy"]]:
                self._proxy_started_at = (
                    self.wall_clock(),
                    self.monotonic_ns(),
                )
            self._run(
                command,
                deadline=deadline,
                category="RUNTIME_PREPARE_FAILED",
            )
        self._wait_for_proxy_ready(context, deadline=deadline)

    def dispatch(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> BackendDispatchResult:
        if not self._names:
            raise BackendError("RUNTIME_NOT_PREPARED")
        self._run_child(
            ["docker", "start", "--attach", self._names["relay"]],
            component="relay",
            deadline=deadline,
            maximum_timeout=float(
                context.runtime_contract["relay_candidate_wait_timeout_seconds"]
            ),
        )
        self._capture_proxy_evidence(deadline=deadline)
        proxy_evidence = self._child_evidence.get("proxy")
        if proxy_evidence is not None and proxy_evidence.child_state not in {
            ChildState.RUNNING,
            ChildState.EXITED_ZERO,
        }:
            raise BackendError(
                proxy_evidence.terminal_reason,
                unknown_dispatch_state=(
                    proxy_evidence.child_state is ChildState.EXIT_UNKNOWN
                ),
            )
        raw, receipt, receipt_status, _events = _load_stage_receipt(
            context.proxy_output_dir / "proxy-receipt.json",
            component="proxy",
            request_id=context.request_id,
        )
        if receipt_status != "VALID" or raw is None or receipt is None:
            raise BackendError("PROXY_RECEIPT_INVALID")
        attempts = receipt.get("provider_attempt_count")
        retry = receipt.get("retry_count")
        status = receipt.get("provider_http_status")
        category = receipt.get("response_category")
        if (
            attempts != 1
            or retry != 0
            or isinstance(status, bool)
            or not isinstance(status, int)
            or category != "FORWARDED"
        ):
            raise BackendError("PROXY_RECEIPT_INVALID")
        self._validated_receipts["proxy"] = raw
        return BackendDispatchResult(
            provider_http_status=status,
            response_received=True,
        )

    def archive_evidence(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> ArchiveResult:
        self._capture_proxy_evidence(deadline=deadline)
        return _archive_stage_evidence(
            context,
            self._child_evidence,
            self._validated_receipts,
        )

    def _cleanup_command(
        self,
        command: list[str],
        *,
        resource_kind: Literal["container", "network"],
        resource_name: str,
        deadline: float,
    ) -> bool:
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            return False
        try:
            result = self.executor(command, remaining)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 or self._docker_absence_proven(
            result,
            resource_kind=resource_kind,
            resource_name=resource_name,
        )

    @staticmethod
    def _docker_absence_proven(
        result: subprocess.CompletedProcess[str],
        *,
        resource_kind: Literal["container", "network"],
        resource_name: str,
    ) -> bool:
        if (
            result.returncode != 1
            or not isinstance(result.stdout, str)
            or result.stdout.strip() not in {"", "[]"}
            or not isinstance(result.stderr, str)
        ):
            return False
        expected = {
            "container": {
                f"Error response from daemon: No such container: {resource_name}",
                f"Error: No such container: {resource_name}",
                f"Error: No such object: {resource_name}",
            },
            "network": {
                f"Error response from daemon: network {resource_name} not found",
                f"Error: No such network: {resource_name}",
                f"Error: No such object: {resource_name}",
            },
        }[resource_kind]
        return result.stderr.strip() in expected

    def cleanup(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> CleanupResult:
        names = self._names or self._runtime_names(context.request_id)
        completed = True
        for role in ("relay", "proxy"):
            completed = self._cleanup_command(
                ["docker", "rm", "--force", names[role]],
                resource_kind="container",
                resource_name=names[role],
                deadline=deadline,
            ) and completed
        for role in ("relay_network", "egress_network"):
            completed = self._cleanup_command(
                ["docker", "network", "rm", names[role]],
                resource_kind="network",
                resource_name=names[role],
                deadline=deadline,
            ) and completed
        container_residue = 0
        network_residue = 0
        for role in ("relay", "proxy"):
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return CleanupResult(
                    completed=False,
                    timed_out=True,
                    container_residue_count=container_residue,
                    network_residue_count=network_residue,
                )
            try:
                result = self.executor(
                    ["docker", "container", "inspect", names[role]],
                    remaining,
                )
            except (OSError, subprocess.SubprocessError):
                result = None
            if (
                result is None
                or result.returncode == 0
                or not self._docker_absence_proven(
                    result,
                    resource_kind="container",
                    resource_name=names[role],
                )
            ):
                container_residue += 1
        for role in ("relay_network", "egress_network"):
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return CleanupResult(
                    completed=False,
                    timed_out=True,
                    container_residue_count=container_residue,
                    network_residue_count=network_residue,
                )
            try:
                result = self.executor(
                    ["docker", "network", "inspect", names[role]],
                    remaining,
                )
            except (OSError, subprocess.SubprocessError):
                result = None
            if (
                result is None
                or result.returncode == 0
                or not self._docker_absence_proven(
                    result,
                    resource_kind="network",
                    resource_name=names[role],
                )
            ):
                network_residue += 1
        return CleanupResult(
            completed=completed and not container_residue and not network_residue,
            container_residue_count=container_residue,
            network_residue_count=network_residue,
        )


def read_keychain_secret_once(
    *,
    executor: CommandExecutor = _default_command_executor,
) -> str:
    """Read the fixed Canary credential once without exposing metadata."""
    command = [
        "security",
        "find-generic-password",
        "-w",
        "-s",
        "tickflow-phase2-canary-openai",
    ]
    try:
        result = executor(command, 10.0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OrchestratorError("keychain_secret_unavailable") from exc
    if result.returncode != 0:
        raise OrchestratorError("keychain_secret_unavailable")
    value = result.stdout.removesuffix("\n")
    if (
        not value
        or value != value.strip()
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise OrchestratorError("keychain_secret_invalid")
    return value


def load_installed_runtime_approval(
    *,
    candidate_path: Path,
    approval_path: Path,
) -> ApprovedCanaryArtifacts:
    """Bind one repository Candidate to a separate local approval record."""
    try:
        candidate_raw = _read_regular_unrestricted(
            candidate_path,
            "runtime_candidate_invalid",
        )
        approval_raw = _read_trusted_runtime_approval(approval_path)
        candidate = _strict_json_bytes(
            candidate_raw,
            "runtime_candidate_invalid",
        )
        approval = _strict_json_bytes(
            approval_raw,
            "runtime_approval_invalid",
        )
    except OrchestratorError as exc:
        if str(exc) == "runtime_candidate_invalid":
            raise
        raise OrchestratorError("runtime_approval_invalid") from exc
    if approval_raw != canonical_json_bytes(approval):
        raise OrchestratorError("runtime_approval_invalid")
    try:
        candidate_model = RuntimeArtifactCandidate.model_validate_json(
            candidate_raw
        )
    except ValidationError as exc:
        raise OrchestratorError("runtime_candidate_invalid") from exc
    if candidate_raw != canonical_json_bytes(
        candidate_model.model_dump(mode="json")
    ):
        raise OrchestratorError("runtime_candidate_invalid")
    expected_approval_fields = {
        "runtime_approval_schema_version",
        "approved_candidate_sha256",
        "approval_scope",
        "symbol",
        "provider",
        "maximum_provider_attempts",
        "retry_count",
    }
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    if (
        set(approval) != expected_approval_fields
        or approval.get("runtime_approval_schema_version") != 2
        or approval.get("approval_scope")
        != "single_symbol_openai_canary_runtime_v2"
        or approval.get("symbol") != "000403.SZ"
        or approval.get("provider") != "openai"
        or approval.get("maximum_provider_attempts") != 1
        or approval.get("retry_count") != 0
        or not isinstance(approval.get("approved_candidate_sha256"), str)
        or not hmac.compare_digest(
            approval["approved_candidate_sha256"],
            candidate_sha256,
        )
    ):
        raise OrchestratorError("runtime_approval_invalid")
    identity = candidate_model.artifact_identity.model_dump(mode="json")
    try:
        return ApprovedCanaryArtifacts.model_validate(
            {
                **identity,
                "approval_candidate_sha256": candidate_sha256,
            }
        )
    except ValidationError as exc:
        raise OrchestratorError("runtime_candidate_invalid") from exc
