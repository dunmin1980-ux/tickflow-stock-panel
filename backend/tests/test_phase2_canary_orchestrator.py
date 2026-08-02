from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from app.services.phase2_canary_orchestrator import (
    AttemptLedgerStore,
    CanaryRunIdentity,
    ExclusiveCanaryLock,
    LedgerState,
    OrchestratorError,
    recover_attempt_state,
)

FIXED_TIME = "2026-08-02T12:00:00Z"


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    return root


@pytest.fixture
def identity() -> CanaryRunIdentity:
    return CanaryRunIdentity(
        request_id="a" * 32,
        symbol="000403.SZ",
        trade_date="2026-07-31",
        provider="openai",
        endpoint_alias="openai_responses_v1",
        facts_sha256="1" * 64,
        projection_sha256="2" * 64,
        approval_candidate_sha256="3" * 64,
        proxy_image_id="sha256:" + "4" * 64,
        relay_image_id="sha256:" + "5" * 64,
        timeout_contract_sha256="6" * 64,
        orchestrator_source_sha256="7" * 64,
    )


def test_attempt_ledger_is_mode_0600_canonical_and_identity_bound(
    state_root: Path,
    identity: CanaryRunIdentity,
) -> None:
    store = AttemptLedgerStore(state_root)

    ledger = store.prepare(identity, timestamp=FIXED_TIME)

    path = state_root / "attempt-ledger.json"
    assert ledger.state is LedgerState.PREPARED
    assert ledger.provider_attempt_count == 0
    assert ledger.retry_count == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_bytes()) == ledger.model_dump(mode="json")
    assert path.read_bytes().endswith(b"\n")
    serialized = path.read_text(encoding="utf-8")
    for forbidden in ("Authorization", "Bearer ", "api_key", "secret"):
        assert forbidden not in serialized.lower()


def test_dispatch_transition_durably_consumes_the_only_attempt(
    state_root: Path,
    identity: CanaryRunIdentity,
) -> None:
    store = AttemptLedgerStore(state_root)
    store.prepare(identity, timestamp=FIXED_TIME)

    ledger = store.transition(
        LedgerState.NETWORK_DISPATCH_STARTED,
        timestamp="2026-08-02T12:00:01Z",
    )

    assert ledger.state is LedgerState.NETWORK_DISPATCH_STARTED
    assert ledger.provider_attempt_count == 1
    assert ledger.retry_count == 0
    assert ledger.dispatch_started_at == "2026-08-02T12:00:01Z"
    assert store.load() == ledger


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (LedgerState.PREPARED, LedgerState.CANDIDATE_COLLECTED),
        (LedgerState.FAILED_BEFORE_DISPATCH, LedgerState.NETWORK_DISPATCH_STARTED),
        (LedgerState.NETWORK_DISPATCH_STARTED, LedgerState.PREPARED),
        (LedgerState.CLEANUP_COMPLETED, LedgerState.FAILED_AFTER_DISPATCH),
    ],
)
def test_illegal_ledger_transitions_fail_closed(
    current: LedgerState,
    target: LedgerState,
    state_root: Path,
    identity: CanaryRunIdentity,
) -> None:
    store = AttemptLedgerStore(state_root)
    store.prepare(identity, timestamp=FIXED_TIME)
    if current is not LedgerState.PREPARED:
        path = [
            LedgerState.NETWORK_DISPATCH_STARTED,
            LedgerState.PROVIDER_RESPONSE_RECEIVED,
            LedgerState.CANDIDATE_COLLECTED,
            LedgerState.HOST_VALIDATION_COMPLETED,
            LedgerState.CLEANUP_COMPLETED,
        ]
        if current is LedgerState.FAILED_BEFORE_DISPATCH:
            store.transition(current, timestamp="2026-08-02T12:00:01Z")
        else:
            for state in path:
                store.transition(state, timestamp="2026-08-02T12:00:01Z")
                if state is current:
                    break

    before = (state_root / "attempt-ledger.json").read_bytes()
    with pytest.raises(OrchestratorError, match="ledger_transition_invalid"):
        store.transition(target, timestamp="2026-08-02T12:00:02Z")
    assert (state_root / "attempt-ledger.json").read_bytes() == before


def test_atomic_ledger_update_failure_preserves_previous_bytes(
    state_root: Path,
    identity: CanaryRunIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AttemptLedgerStore(state_root)
    store.prepare(identity, timestamp=FIXED_TIME)
    ledger_path = state_root / "attempt-ledger.json"
    before = ledger_path.read_bytes()

    def fail_replace(_source: str | Path, _target: str | Path) -> None:
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(
        "app.services.phase2_canary_orchestrator.os.replace",
        fail_replace,
    )
    with pytest.raises(OrchestratorError, match="ledger_publish_failed"):
        store.transition(
            LedgerState.NETWORK_DISPATCH_STARTED,
            timestamp="2026-08-02T12:00:01Z",
        )

    assert ledger_path.read_bytes() == before
    assert not list(state_root.glob("*.partial"))


def test_recovery_marks_pre_dispatch_and_unknown_dispatch_differently(
    tmp_path: Path,
    identity: CanaryRunIdentity,
) -> None:
    before_root = tmp_path / "before"
    before_root.mkdir(mode=0o700)
    before = AttemptLedgerStore(before_root)
    before.prepare(identity, timestamp=FIXED_TIME)

    before_recovered = recover_attempt_state(
        before,
        timestamp="2026-08-02T12:05:00Z",
    )

    assert before_recovered.state is LedgerState.FAILED_BEFORE_DISPATCH
    assert before_recovered.provider_attempt_count == 0

    after_root = tmp_path / "after"
    after_root.mkdir(mode=0o700)
    after = AttemptLedgerStore(after_root)
    after.prepare(identity, timestamp=FIXED_TIME)
    after.transition(
        LedgerState.NETWORK_DISPATCH_STARTED,
        timestamp="2026-08-02T12:00:01Z",
    )

    after_recovered = recover_attempt_state(
        after,
        timestamp="2026-08-02T12:05:00Z",
    )

    assert after_recovered.state is LedgerState.ATTEMPT_CONSUMED_UNKNOWN
    assert after_recovered.provider_attempt_count == 1


def test_state_root_and_ledger_reject_symlinks_or_unsafe_modes(
    tmp_path: Path,
    identity: CanaryRunIdentity,
) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(OrchestratorError, match="state_root_mode_invalid"):
        AttemptLedgerStore(unsafe)

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(OrchestratorError, match="state_root_symlink_forbidden"):
        AttemptLedgerStore(linked)

    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    (real / "attempt-ledger.json").symlink_to(outside)
    with pytest.raises(OrchestratorError, match="ledger_target_invalid"):
        AttemptLedgerStore(real).prepare(identity, timestamp=FIXED_TIME)


def test_exclusive_lock_rejects_concurrent_owner_before_new_request_id(
    state_root: Path,
) -> None:
    first = ExclusiveCanaryLock(
        state_root,
        approval_candidate_sha256="3" * 64,
        pid=101,
        started_at=FIXED_TIME,
    )
    second = ExclusiveCanaryLock(
        state_root,
        approval_candidate_sha256="3" * 64,
        pid=202,
        started_at=FIXED_TIME,
    )

    first.acquire()
    try:
        with pytest.raises(OrchestratorError, match="canary_lock_held"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_unknown_stale_lock_fails_but_failed_before_dispatch_can_recover(
    state_root: Path,
    identity: CanaryRunIdentity,
) -> None:
    lock_path = state_root / ".runtime.lock"
    stale = {
        "lock_schema_version": 1,
        "pid": 999_999,
        "started_at": FIXED_TIME,
        "symbol": "000403.SZ",
        "provider": "openai",
        "endpoint_alias": "openai_responses_v1",
        "approval_candidate_sha256": "3" * 64,
    }
    lock_path.write_text(
        json.dumps(stale, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lock_path.chmod(0o600)
    lock = ExclusiveCanaryLock(
        state_root,
        approval_candidate_sha256="3" * 64,
        pid=os.getpid(),
        started_at="2026-08-02T12:10:00Z",
    )

    with pytest.raises(OrchestratorError, match="stale_lock_state_unknown"):
        lock.acquire()

    store = AttemptLedgerStore(state_root)
    store.prepare(identity, timestamp=FIXED_TIME)
    store.transition(
        LedgerState.FAILED_BEFORE_DISPATCH,
        timestamp="2026-08-02T12:00:01Z",
    )

    lock.acquire()
    lock.release()


def test_clean_lock_release_leaves_no_stale_identity(state_root: Path) -> None:
    lock = ExclusiveCanaryLock(
        state_root,
        approval_candidate_sha256="3" * 64,
        pid=os.getpid(),
        started_at=FIXED_TIME,
    )

    lock.acquire()
    lock.release()

    lock_path = state_root / ".runtime.lock"
    assert lock_path.is_file()
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert lock_path.read_bytes().strip() == b""

