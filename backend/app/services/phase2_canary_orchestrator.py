"""Fail-closed state primitives for the one-shot Phase 2B Canary."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import re
import stat
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_MAXIMUM_LEDGER_BYTES = 65_536
_LEDGER_NAME = "attempt-ledger.json"
_LOCK_NAME = ".runtime.lock"


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
