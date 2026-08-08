"""Sanitized child-process evidence for the Phase 2B Canary runtime."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

MAX_BOUNDED_STDERR_BYTES = 4096

_SENSITIVE_STDERR = re.compile(
    r"Authorization\s*:|"
    r"Bearer\s+[^\s]+|"
    r"(?:api[_-]?key|openai_api_key|secret|token|password|credential)"
    r"\s*[:=]\s*[^\s]+|"
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}",
    re.IGNORECASE,
)


class ChildState(StrEnum):
    RUNNING = "CHILD_RUNNING"
    EXITED_ZERO = "CHILD_EXITED_ZERO"
    TIMEOUT = "CHILD_TIMEOUT"
    NONZERO_EXIT = "CHILD_NONZERO_EXIT"
    PROCESS_ERROR = "CHILD_PROCESS_ERROR"
    SIGNALLED = "CHILD_SIGNALLED"
    START_FAILED = "CHILD_START_FAILED"
    EXIT_UNKNOWN = "CHILD_EXIT_UNKNOWN"


@dataclass(frozen=True)
class ChildExecutionTiming:
    started_at: str
    exited_at: str
    started_monotonic_ns: int
    exited_monotonic_ns: int

    def __post_init__(self) -> None:
        if (
            not self.started_at
            or not self.exited_at
            or isinstance(self.started_monotonic_ns, bool)
            or isinstance(self.exited_monotonic_ns, bool)
            or not isinstance(self.started_monotonic_ns, int)
            or not isinstance(self.exited_monotonic_ns, int)
            or self.started_monotonic_ns < 0
            or self.exited_monotonic_ns < self.started_monotonic_ns
        ):
            raise ValueError("child_timing_invalid")

    @property
    def elapsed_ms(self) -> int:
        return (self.exited_monotonic_ns - self.started_monotonic_ns) // 1_000_000


@dataclass(frozen=True)
class BoundedStderrEvidence:
    stderr_excerpt: str | None
    stderr_sha256: str | None
    stderr_byte_count: int
    stderr_truncated: bool
    stderr_redacted: bool
    sensitive_hit_count: int


@dataclass(frozen=True)
class ChildProcessEvidence:
    component: Literal["relay", "proxy"]
    child_state: ChildState
    terminal_reason: str
    started_at: str
    exited_at: str
    started_monotonic_ns: int
    exited_monotonic_ns: int
    elapsed_ms: int
    exit_code: int | None
    signal: int | None
    stderr: BoundedStderrEvidence


def _stderr_bytes(value: str | bytes | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError("stderr_value_invalid")


def sanitize_bounded_stderr(
    value: str | bytes | None,
    *,
    maximum_bytes: int = MAX_BOUNDED_STDERR_BYTES,
) -> BoundedStderrEvidence:
    """Return metadata only; child stderr text is never persisted."""
    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int):
        raise ValueError("stderr_limit_invalid")
    if maximum_bytes <= 0 or maximum_bytes > MAX_BOUNDED_STDERR_BYTES:
        raise ValueError("stderr_limit_invalid")
    raw = _stderr_bytes(value)
    if not raw:
        return BoundedStderrEvidence(
            stderr_excerpt=None,
            stderr_sha256=None,
            stderr_byte_count=0,
            stderr_truncated=False,
            stderr_redacted=False,
            sensitive_hit_count=0,
        )

    hits = len(_SENSITIVE_STDERR.findall(raw.decode("utf-8", errors="replace")))
    return BoundedStderrEvidence(
        stderr_excerpt=None,
        stderr_sha256=None,
        stderr_byte_count=0,
        stderr_truncated=len(raw) > maximum_bytes,
        stderr_redacted=True,
        sensitive_hit_count=hits,
    )


def _component(value: str) -> Literal["relay", "proxy"]:
    if value not in {"relay", "proxy"}:
        raise ValueError("child_component_invalid")
    return value


def _terminal_reason(component: str, state: ChildState) -> str:
    suffix = {
        ChildState.RUNNING: "RUNNING",
        ChildState.EXITED_ZERO: "EXITED_ZERO",
        ChildState.TIMEOUT: "TIMEOUT",
        ChildState.NONZERO_EXIT: "NONZERO_EXIT",
        ChildState.PROCESS_ERROR: "PROCESS_ERROR",
        ChildState.SIGNALLED: "SIGNALLED",
        ChildState.START_FAILED: "START_FAILED",
        ChildState.EXIT_UNKNOWN: "EXIT_UNKNOWN",
    }[state]
    return f"{component.upper()}_{suffix}"


def _evidence(
    component: str,
    state: ChildState,
    timing: ChildExecutionTiming,
    *,
    exit_code: int | None,
    signal: int | None,
    stderr: str | bytes | None,
) -> ChildProcessEvidence:
    checked_component = _component(component)
    return ChildProcessEvidence(
        component=checked_component,
        child_state=state,
        terminal_reason=_terminal_reason(checked_component, state),
        started_at=timing.started_at,
        exited_at=timing.exited_at,
        started_monotonic_ns=timing.started_monotonic_ns,
        exited_monotonic_ns=timing.exited_monotonic_ns,
        elapsed_ms=timing.elapsed_ms,
        exit_code=exit_code,
        signal=signal,
        stderr=sanitize_bounded_stderr(stderr),
    )


def classify_completed_child(
    component: str,
    result: subprocess.CompletedProcess[str],
    timing: ChildExecutionTiming,
    *,
    interpret_positive_signal: bool = False,
) -> ChildProcessEvidence:
    returncode = result.returncode
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return _evidence(
            component,
            ChildState.EXIT_UNKNOWN,
            timing,
            exit_code=None,
            signal=None,
            stderr=result.stderr,
        )
    if returncode < 0:
        return _evidence(
            component,
            ChildState.SIGNALLED,
            timing,
            exit_code=None,
            signal=-returncode,
            stderr=result.stderr,
        )
    if interpret_positive_signal and 128 < returncode <= 192:
        return _evidence(
            component,
            ChildState.SIGNALLED,
            timing,
            exit_code=returncode,
            signal=returncode - 128,
            stderr=result.stderr,
        )
    state = ChildState.EXITED_ZERO if returncode == 0 else ChildState.NONZERO_EXIT
    return _evidence(
        component,
        state,
        timing,
        exit_code=returncode,
        signal=None,
        stderr=result.stderr,
    )


def classify_running_child(
    component: str,
    timing: ChildExecutionTiming,
) -> ChildProcessEvidence:
    return _evidence(
        component,
        ChildState.RUNNING,
        timing,
        exit_code=None,
        signal=None,
        stderr=None,
    )


def classify_child_exception(
    component: str,
    error: BaseException,
    timing: ChildExecutionTiming,
) -> ChildProcessEvidence:
    stderr: str | bytes | None = None
    if isinstance(error, subprocess.TimeoutExpired):
        state = ChildState.TIMEOUT
        stderr = error.stderr
    elif isinstance(error, (FileNotFoundError, PermissionError)):
        state = ChildState.START_FAILED
    elif isinstance(error, (OSError, subprocess.SubprocessError)):
        state = ChildState.PROCESS_ERROR
    else:
        state = ChildState.EXIT_UNKNOWN
    return _evidence(
        component,
        state,
        timing,
        exit_code=None,
        signal=None,
        stderr=stderr,
    )
