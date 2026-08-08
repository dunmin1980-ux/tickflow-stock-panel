from __future__ import annotations

import subprocess

import pytest

from app.services.phase2_canary_observability import (
    MAX_BOUNDED_STDERR_BYTES,
    ChildExecutionTiming,
    classify_child_exception,
    classify_completed_child,
    classify_running_child,
    sanitize_bounded_stderr,
)


@pytest.fixture
def timing() -> ChildExecutionTiming:
    return ChildExecutionTiming(
        started_at="2026-08-08T08:23:32.000000Z",
        exited_at="2026-08-08T08:23:33.500000Z",
        started_monotonic_ns=4_000_000_000,
        exited_monotonic_ns=5_500_000_000,
    )


def test_positive_return_code_is_nonzero_not_timeout(
    timing: ChildExecutionTiming,
) -> None:
    result = subprocess.CompletedProcess(
        ["docker", "start", "relay"],
        1,
        stdout="",
        stderr="relay failed locally",
    )

    evidence = classify_completed_child("relay", result, timing)

    assert evidence.child_state == "CHILD_NONZERO_EXIT"
    assert evidence.terminal_reason == "RELAY_NONZERO_EXIT"
    assert evidence.exit_code == 1
    assert evidence.signal is None
    assert evidence.elapsed_ms == 1500
    assert evidence.stderr.stderr_excerpt is None
    assert evidence.stderr.stderr_sha256 is None
    assert evidence.stderr.stderr_redacted is True


def test_negative_return_code_is_signal(timing: ChildExecutionTiming) -> None:
    result = subprocess.CompletedProcess(
        ["docker", "start", "relay"],
        -15,
        stdout="",
        stderr="terminated",
    )

    evidence = classify_completed_child("relay", result, timing)

    assert evidence.child_state == "CHILD_SIGNALLED"
    assert evidence.terminal_reason == "RELAY_SIGNALLED"
    assert evidence.exit_code is None
    assert evidence.signal == 15


def test_container_signal_exit_code_is_classified_explicitly(
    timing: ChildExecutionTiming,
) -> None:
    result = subprocess.CompletedProcess(
        ["docker", "start", "--attach", "relay"],
        137,
        stdout="",
        stderr="killed",
    )

    evidence = classify_completed_child(
        "relay",
        result,
        timing,
        interpret_positive_signal=True,
    )

    assert evidence.child_state == "CHILD_SIGNALLED"
    assert evidence.terminal_reason == "RELAY_SIGNALLED"
    assert evidence.exit_code == 137
    assert evidence.signal == 9


@pytest.mark.parametrize(
    ("error", "state", "reason"),
    [
        (
            subprocess.TimeoutExpired(
                ["docker", "start", "--attach", "relay"],
                75,
                stderr="deadline reached",
            ),
            "CHILD_TIMEOUT",
            "RELAY_TIMEOUT",
        ),
        (
            FileNotFoundError("docker unavailable"),
            "CHILD_START_FAILED",
            "RELAY_START_FAILED",
        ),
        (
            subprocess.SubprocessError("wait failed"),
            "CHILD_PROCESS_ERROR",
            "RELAY_PROCESS_ERROR",
        ),
        (
            OSError("pipe failed"),
            "CHILD_PROCESS_ERROR",
            "RELAY_PROCESS_ERROR",
        ),
        (
            RuntimeError("unclassified"),
            "CHILD_EXIT_UNKNOWN",
            "RELAY_EXIT_UNKNOWN",
        ),
    ],
)
def test_child_exception_categories_are_strict(
    timing: ChildExecutionTiming,
    error: BaseException,
    state: str,
    reason: str,
) -> None:
    evidence = classify_child_exception("relay", error, timing)

    assert evidence.child_state == state
    assert evidence.terminal_reason == reason


def test_proxy_uses_proxy_specific_nonzero_category(
    timing: ChildExecutionTiming,
) -> None:
    result = subprocess.CompletedProcess(
        ["docker", "inspect", "proxy"],
        7,
        stdout="",
        stderr="proxy exited",
    )

    evidence = classify_completed_child("proxy", result, timing)

    assert evidence.child_state == "CHILD_NONZERO_EXIT"
    assert evidence.terminal_reason == "PROXY_NONZERO_EXIT"
    assert evidence.component == "proxy"


def test_running_proxy_is_not_reported_as_exited_or_timed_out(
    timing: ChildExecutionTiming,
) -> None:
    evidence = classify_running_child("proxy", timing)

    assert evidence.child_state == "CHILD_RUNNING"
    assert evidence.terminal_reason == "PROXY_RUNNING"
    assert evidence.exit_code is None
    assert evidence.signal is None


def test_unknown_return_code_is_not_timeout(timing: ChildExecutionTiming) -> None:
    result = subprocess.CompletedProcess(
        ["docker", "start", "relay"],
        None,
        stdout="",
        stderr="",
    )

    evidence = classify_completed_child("relay", result, timing)

    assert evidence.child_state == "CHILD_EXIT_UNKNOWN"
    assert evidence.terminal_reason == "RELAY_EXIT_UNKNOWN"


def test_bounded_stderr_is_utf8_safe_and_limited() -> None:
    raw = ("x" * (MAX_BOUNDED_STDERR_BYTES - 1)) + "中文尾部"

    evidence = sanitize_bounded_stderr(raw)

    assert evidence.stderr_redacted is True
    assert evidence.stderr_truncated is True
    assert evidence.stderr_excerpt is None
    assert evidence.stderr_sha256 is None
    assert evidence.stderr_byte_count == 0


def test_unlabelled_sensitive_stderr_is_never_persisted() -> None:
    evidence = sanitize_bounded_stderr("opaque-credential-value-with-no-label")

    assert evidence.stderr_redacted is True
    assert evidence.stderr_excerpt is None
    assert evidence.stderr_sha256 is None
    assert evidence.stderr_byte_count == 0


@pytest.mark.parametrize(
    "stderr",
    [
        "Authorization: Bearer synthetic-test-secret-value",
        "api_key=synthetic-test-secret-value",
        "OPENAI_API_KEY: synthetic-test-secret-value",
        "token=synthetic-test-secret-value",
    ],
)
def test_sensitive_stderr_body_and_digest_are_not_persisted(stderr: str) -> None:
    evidence = sanitize_bounded_stderr(stderr)

    assert evidence.stderr_redacted is True
    assert evidence.stderr_excerpt is None
    assert evidence.stderr_sha256 is None
    assert evidence.stderr_byte_count == 0
    assert evidence.sensitive_hit_count >= 1


def test_empty_stderr_has_no_excerpt_or_digest() -> None:
    evidence = sanitize_bounded_stderr("")

    assert evidence.stderr_redacted is False
    assert evidence.stderr_truncated is False
    assert evidence.stderr_excerpt is None
    assert evidence.stderr_sha256 is None
    assert evidence.stderr_byte_count == 0
