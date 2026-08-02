from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.services import phase2_canary_orchestrator as orchestrator_module
from app.services.phase2_ai_worker_protocol import (
    FakeClaimsWorker,
    build_worker_projection,
)
from app.services.phase2_canary_orchestrator import (
    ApprovedCanaryArtifacts,
    AttemptLedgerStore,
    CanaryOrchestratorConfig,
    CanaryRunIdentity,
    CanaryRuntimeContext,
    CleanupResult,
    DockerCanaryBackend,
    ExclusiveCanaryLock,
    LedgerState,
    MockCanaryBackend,
    OrchestratorError,
    load_installed_runtime_approval,
    read_keychain_secret_once,
    recover_attempt_state,
    run_single_symbol_canary,
)
from app.services.phase2_canary_runtime_contract import (
    load_runtime_contract,
    runtime_contract_sha256,
)
from app.services.phase2_claims_service import canonical_json_bytes

FIXED_TIME = "2026-08-02T12:00:00Z"
REPO_ROOT = Path(__file__).resolve().parents[2]
FACTS_PATH = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"


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
        timeout_contract_sha256=runtime_contract_sha256(),
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


@pytest.fixture
def projection() -> dict[str, Any]:
    facts_bytes = FACTS_PATH.read_bytes()
    facts = json.loads(facts_bytes)
    return build_worker_projection(
        facts,
        __import__("hashlib").sha256(facts_bytes).hexdigest(),
        facts_bytes=facts_bytes,
    )


@pytest.fixture
def relay_response(projection: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "request_id": "a" * 32,
        "symbol": "000403.SZ",
        "projection_sha256": projection["projection_sha256"],
        "claims_candidate": json.loads(FakeClaimsWorker().run(projection)),
    }


@pytest.fixture
def artifacts(projection: dict[str, Any]) -> ApprovedCanaryArtifacts:
    return ApprovedCanaryArtifacts(
        approval_candidate_sha256="3" * 64,
        facts_sha256=projection["facts_sha256"],
        projection_sha256=projection["projection_sha256"],
        proxy_image_id="sha256:" + "4" * 64,
        relay_image_id="sha256:" + "5" * 64,
        timeout_contract_sha256=runtime_contract_sha256(),
        orchestrator_source_sha256="7" * 64,
    )


@pytest.fixture
def orchestrator_config(tmp_path: Path) -> CanaryOrchestratorConfig:
    state = tmp_path / "state"
    work = tmp_path / "work"
    output = tmp_path / "canary-output"
    for path in (state, work, output):
        path.mkdir(mode=0o700)
    return CanaryOrchestratorConfig(
        repo_root=REPO_ROOT,
        state_root=state,
        work_root=work,
        canary_output_root=output,
        facts_path=FACTS_PATH,
    )


def _run_mock(
    *,
    scenario: str,
    config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
    secret_reads: list[str] | None = None,
) -> tuple[Any, MockCanaryBackend]:
    backend = MockCanaryBackend(
        scenario=scenario,
        relay_response=relay_response,
    )

    def secret_reader() -> str:
        if secret_reads is not None:
            secret_reads.append("read")
        return "placeholder-canary-secret"

    result = run_single_symbol_canary(
        config=config,
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _artifacts: None,
        secret_reader=secret_reader,
        request_id_factory=lambda: "a" * 32,
        wall_clock=lambda: FIXED_TIME,
        monotonic=backend.monotonic,
    )
    return result, backend


def test_successful_one_shot_lifecycle_validates_renders_and_cleans(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    reads: list[str] = []

    result, backend = _run_mock(
        scenario="provider_1s",
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        relay_response=relay_response,
        secret_reads=reads,
    )

    assert result.terminal_state == "SUCCEEDED"
    assert result.attempt_ledger_state is LedgerState.CLEANUP_COMPLETED
    assert result.provider_attempt_count == 1
    assert result.retry_count == 0
    assert result.route == "inbox"
    assert result.host_validation_status == "VALID"
    assert reads == ["read"]
    assert backend.prepare_count == 1
    assert backend.dispatch_count == 1
    assert backend.cleanup_count == 1
    assert not list(orchestrator_config.work_root.iterdir())
    assert result.secret_file_residue_count == 0
    assert result.container_residue_count == 0
    assert result.network_residue_count == 0
    inbox = orchestrator_config.canary_output_root / "inbox"
    assert len(list(inbox.glob("*.json"))) == 1
    assert len(list(inbox.glob("*.md"))) == 1
    evidence_path = (
        orchestrator_config.canary_output_root
        / "evidence"
        / f"{'a' * 32}.json"
    )
    evidence = json.loads(evidence_path.read_bytes())
    assert evidence["runtime_contract_version"] == 1
    assert evidence["attempt_ledger_state"] == "CLEANUP_COMPLETED"
    assert evidence["provider_attempt_count"] == 1
    assert evidence["retry_count"] == 0
    assert evidence["can_publish"] is False
    assert evidence["manual_review"] == "PENDING"
    assert evidence["container_residue_count"] == 0
    assert evidence["network_residue_count"] == 0
    assert evidence["secret_file_residue_count"] == 0
    assert evidence["temporary_file_residue_count"] == 0
    serialized = "\n".join(
        path.read_text(encoding="utf-8") for path in inbox.iterdir()
    )
    serialized += evidence_path.read_text(encoding="utf-8")
    assert "placeholder-canary-secret" not in serialized
    assert "Authorization" not in serialized


def test_runtime_evidence_failure_removes_success_route(
    monkeypatch: pytest.MonkeyPatch,
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    def fail_evidence(*_args: object, **_kwargs: object) -> None:
        raise OrchestratorError("runtime_evidence_publish_failed")

    monkeypatch.setattr(
        orchestrator_module,
        "_publish_runtime_evidence",
        fail_evidence,
    )

    result, backend = _run_mock(
        scenario="provider_1s",
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        relay_response=relay_response,
    )

    assert result.terminal_state == "RUNTIME_EVIDENCE_PUBLISH_FAILED"
    assert result.route == "rejected"
    assert backend.dispatch_count == 1
    inbox = orchestrator_config.canary_output_root / "inbox"
    assert not inbox.exists() or not list(inbox.iterdir())


@pytest.mark.parametrize(
    ("scenario", "terminal", "ledger_state", "attempts", "route"),
    [
        ("provider_1s", "SUCCEEDED", LedgerState.CLEANUP_COMPLETED, 1, "inbox"),
        ("provider_59s", "SUCCEEDED", LedgerState.CLEANUP_COMPLETED, 1, "inbox"),
        ("provider_60s", "SUCCEEDED", LedgerState.CLEANUP_COMPLETED, 1, "inbox"),
        (
            "provider_over_60s",
            "PROVIDER_TIMEOUT",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "relay_exit_before_candidate",
            "CANDIDATE_NOT_PRODUCED",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "proxy_exit_after_request",
            "ATTEMPT_CONSUMED_UNKNOWN",
            LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
            1,
            "rejected",
        ),
        (
            "host_crash_before_dispatch",
            "HOST_CRASH_BEFORE_DISPATCH",
            LedgerState.FAILED_BEFORE_DISPATCH,
            0,
            "rejected",
        ),
        (
            "host_crash_after_dispatch",
            "ATTEMPT_CONSUMED_UNKNOWN",
            LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
            1,
            "rejected",
        ),
        (
            "host_crash_after_response",
            "ATTEMPT_CONSUMED_UNKNOWN",
            LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
            1,
            "rejected",
        ),
        (
            "candidate_partial_write",
            "CANDIDATE_PARTIAL_WRITE",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "candidate_rename_failure",
            "CANDIDATE_NOT_PRODUCED",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "candidate_missing",
            "CANDIDATE_NOT_PRODUCED",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "candidate_multiple",
            "CANDIDATE_MULTIPLE",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "nonzero_container_exit",
            "CONTAINER_EXIT_NONZERO",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "cleanup_timeout",
            "CLEANUP_TIMEOUT",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "duplicate_request_id",
            "CANDIDATE_INVALID",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "runtime_residue",
            "CLEANUP_RESIDUE",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
    ],
)
def test_mock_fault_matrix_never_retries_or_routes_failures_to_inbox(
    scenario: str,
    terminal: str,
    ledger_state: LedgerState,
    attempts: int,
    route: str,
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    result, backend = _run_mock(
        scenario=scenario,
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        relay_response=relay_response,
    )

    assert result.terminal_state == terminal
    assert result.attempt_ledger_state is ledger_state
    assert result.provider_attempt_count == attempts
    assert result.retry_count == 0
    assert result.route == route
    assert backend.dispatch_count <= 1
    assert backend.cleanup_count == 1
    assert result.secret_file_residue_count == 0
    assert result.temporary_file_residue_count == 0
    inbox = orchestrator_config.canary_output_root / "inbox"
    if terminal != "SUCCEEDED":
        assert not inbox.exists() or not list(inbox.iterdir())


def test_concurrent_orchestrator_is_rejected_before_secret_or_request_id(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    held = ExclusiveCanaryLock(
        orchestrator_config.state_root,
        approval_candidate_sha256=artifacts.approval_candidate_sha256,
        pid=101,
        started_at=FIXED_TIME,
    )
    request_ids: list[str] = []
    secret_reads: list[str] = []
    held.acquire()
    try:
        result = run_single_symbol_canary(
            config=orchestrator_config,
            artifacts=artifacts,
            projection=projection,
            backend=MockCanaryBackend(
                scenario="provider_1s",
                relay_response=relay_response,
            ),
            artifact_verifier=lambda _artifacts: None,
            secret_reader=lambda: secret_reads.append("read") or "placeholder",
            request_id_factory=lambda: request_ids.append("made") or "a" * 32,
            wall_clock=lambda: FIXED_TIME,
        )
    finally:
        held.release()

    assert result.terminal_state == "CANARY_LOCK_HELD"
    assert result.provider_attempt_count == 0
    assert request_ids == []
    assert secret_reads == []


def test_interrupted_ledger_blocks_new_attempt_without_secret_read(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    store = AttemptLedgerStore(orchestrator_config.state_root)
    interrupted = CanaryRunIdentity(
        request_id="b" * 32,
        symbol="000403.SZ",
        trade_date="2026-07-31",
        provider="openai",
        endpoint_alias="openai_responses_v1",
        **artifacts.model_dump(mode="python"),
    )
    store.prepare(interrupted, timestamp=FIXED_TIME)
    store.transition(
        LedgerState.NETWORK_DISPATCH_STARTED,
        timestamp=FIXED_TIME,
    )
    reads: list[str] = []

    result, backend = _run_mock(
        scenario="provider_1s",
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        relay_response=relay_response,
        secret_reads=reads,
    )

    assert result.terminal_state == "ATTEMPT_CONSUMED_UNKNOWN"
    assert result.provider_attempt_count == 1
    assert store.load().state is LedgerState.ATTEMPT_CONSUMED_UNKNOWN
    assert reads == []
    assert backend.dispatch_count == 0


def test_stale_lock_without_provable_pre_dispatch_state_fails_closed(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    lock_path = orchestrator_config.state_root / ".runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "lock_schema_version": 1,
                "pid": 999_999,
                "started_at": FIXED_TIME,
                "symbol": "000403.SZ",
                "provider": "openai",
                "endpoint_alias": "openai_responses_v1",
                "approval_candidate_sha256": artifacts.approval_candidate_sha256,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    lock_path.chmod(0o600)

    result, backend = _run_mock(
        scenario="provider_1s",
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        relay_response=relay_response,
    )

    assert result.terminal_state == "STALE_LOCK_STATE_UNKNOWN"
    assert result.provider_attempt_count == 0
    assert backend.dispatch_count == 0


def test_artifact_gate_runs_before_secret_and_backend(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    reads: list[str] = []
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=relay_response,
    )

    result = run_single_symbol_canary(
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _artifacts: (_ for _ in ()).throw(
            OrchestratorError("approved_artifact_mismatch")
        ),
        secret_reader=lambda: reads.append("read") or "placeholder",
        request_id_factory=lambda: "a" * 32,
        wall_clock=lambda: FIXED_TIME,
    )

    assert result.terminal_state == "APPROVED_ARTIFACT_MISMATCH"
    assert result.provider_attempt_count == 0
    assert reads == []
    assert backend.prepare_count == 0
    assert backend.dispatch_count == 0


def test_projection_binding_mismatch_fails_before_secret(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    reads: list[str] = []
    mismatched = artifacts.model_copy(update={"projection_sha256": "8" * 64})

    result, backend = _run_mock(
        scenario="provider_1s",
        config=orchestrator_config,
        artifacts=mismatched,
        projection=projection,
        relay_response=relay_response,
        secret_reads=reads,
    )

    assert result.terminal_state == "PROJECTION_BINDING_INVALID"
    assert result.provider_attempt_count == 0
    assert reads == []
    assert backend.dispatch_count == 0


def test_mock_fault_catalog_is_exactly_the_twenty_required_cases() -> None:
    assert MockCanaryBackend.FAULT_CATALOG == (
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


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], float]] = []

    def __call__(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, timeout))
        if command[:3] == ["docker", "container", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "not found")
        if command[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "not found")
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def runtime_context(
    tmp_path: Path,
    projection: dict[str, Any],
) -> CanaryRuntimeContext:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    output = root / "output"
    output.mkdir(mode=0o700)
    request = root / "request.json"
    projection_path = root / "projection.json"
    secret = root / "provider-auth"
    request.write_text("{}\n", encoding="utf-8")
    projection_path.write_text(json.dumps(projection) + "\n", encoding="utf-8")
    secret.write_text("placeholder-canary-secret", encoding="utf-8")
    for path in (request, projection_path, secret):
        path.chmod(0o600)
    return CanaryRuntimeContext(
        request_id="a" * 32,
        request_path=request,
        projection_path=projection_path,
        output_dir=output,
        secret_path=secret,
        proxy_image_id="sha256:" + "4" * 64,
        relay_image_id="sha256:" + "5" * 64,
        runtime_contract=load_runtime_contract().model_dump(mode="json"),
    )


def test_docker_backend_commands_have_exact_isolation_and_no_secret_surface(
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _RecordingExecutor()
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: 0.0)

    backend.prepare(runtime_context, deadline=90.0)

    commands = [command for command, _timeout in executor.calls]
    joined = "\n".join(" ".join(command) for command in commands)
    assert any("--internal" in command for command in commands)
    assert joined.count("docker network create") == 2
    assert "--network-alias phase2-egress-proxy" in joined
    assert "dst=/run/phase2/provider-auth,readonly" in joined
    assert "dst=/input/request.json,readonly" in joined
    assert "dst=/input/projection.json,readonly" in joined
    assert "dst=/output" in joined
    assert joined.count("dst=/run/phase2/provider-auth,readonly") == 1
    for forbidden in (
        "placeholder-canary-secret",
        "Authorization",
        "--env",
        "--env-file",
        "--publish",
        "-p ",
        "docker.sock",
        "--privileged",
    ):
        assert forbidden not in joined
    assert all(timeout <= 90 for _command, timeout in executor.calls)


def test_docker_backend_dispatches_relay_once_and_cleanup_is_bounded(
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _RecordingExecutor()
    now = [0.0]
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: now[0])
    backend.prepare(runtime_context, deadline=90.0)
    (runtime_context.output_dir / "proxy-receipt.json").write_text(
        json.dumps(
            {
                "provider_attempt_count": 1,
                "provider_http_status": 200,
                "retry_count": 0,
                "response_category": "FORWARDED",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    dispatch = backend.dispatch(runtime_context, deadline=90.0)
    cleanup = backend.cleanup(runtime_context, deadline=15.0)

    relay_starts = [
        command
        for command, _timeout in executor.calls
        if command[:3] == ["docker", "start", "--attach"]
    ]
    assert len(relay_starts) == 1
    assert dispatch.provider_http_status == 200
    assert dispatch.response_received is True
    assert cleanup == CleanupResult(completed=True)
    assert all(timeout <= 75 for command, timeout in executor.calls if "--attach" in command)


def test_keychain_reader_uses_fixed_service_once_without_shell_or_output_logging() -> None:
    calls: list[tuple[list[str], float]] = []

    def executor(
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, timeout))
        return subprocess.CompletedProcess(
            command,
            0,
            "placeholder-canary-secret\n",
            "",
        )

    assert read_keychain_secret_once(executor=executor) == (
        "placeholder-canary-secret"
    )
    assert calls == [
        (
            [
                "security",
                "find-generic-password",
                "-w",
                "-s",
                "tickflow-phase2-canary-openai",
            ],
            10.0,
        )
    ]


def test_runtime_candidate_requires_separate_mode_0600_local_approval(
    tmp_path: Path,
    artifacts: ApprovedCanaryArtifacts,
) -> None:
    candidate_path = tmp_path / "runtime_contract_candidate.json"
    approval_path = tmp_path / "runtime-contract-approval.json"
    artifact_identity = artifacts.model_dump(mode="json")
    artifact_identity.pop("approval_candidate_sha256")
    candidate_bytes = canonical_json_bytes(
        {
            "runtime_candidate_schema_version": 1,
            "status": "PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL",
            "artifact_identity": artifact_identity,
        }
    )
    candidate_path.write_bytes(candidate_bytes)
    candidate_path.chmod(0o644)
    candidate_sha = __import__("hashlib").sha256(candidate_bytes).hexdigest()

    with pytest.raises(OrchestratorError, match="runtime_approval_invalid"):
        load_installed_runtime_approval(
            candidate_path=candidate_path,
            approval_path=approval_path,
        )

    approval_path.write_bytes(
        canonical_json_bytes(
            {
                "runtime_approval_schema_version": 1,
                "approved_candidate_sha256": candidate_sha,
                "approval_scope": "single_symbol_openai_canary_runtime_v1",
                "symbol": "000403.SZ",
                "provider": "openai",
                "maximum_provider_attempts": 1,
                "retry_count": 0,
            }
        )
    )
    approval_path.chmod(0o600)

    approved = load_installed_runtime_approval(
        candidate_path=candidate_path,
        approval_path=approval_path,
    )

    assert approved.approval_candidate_sha256 == candidate_sha
    assert approved.model_dump(mode="json") == {
        **artifact_identity,
        "approval_candidate_sha256": candidate_sha,
    }


def test_runtime_approval_hash_or_file_shape_mismatch_fails_closed(
    tmp_path: Path,
    artifacts: ApprovedCanaryArtifacts,
) -> None:
    candidate = tmp_path / "candidate.json"
    approval = tmp_path / "approval.json"
    artifact_identity = artifacts.model_dump(mode="json")
    artifact_identity.pop("approval_candidate_sha256")
    candidate.write_bytes(
        canonical_json_bytes(
            {
                "runtime_candidate_schema_version": 1,
                "status": "PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL",
                "artifact_identity": artifact_identity,
            }
        )
    )
    candidate.chmod(0o644)
    approval.write_bytes(
        canonical_json_bytes(
            {
                "runtime_approval_schema_version": 1,
                "approved_candidate_sha256": "0" * 64,
                "approval_scope": "single_symbol_openai_canary_runtime_v1",
                "symbol": "000403.SZ",
                "provider": "openai",
                "maximum_provider_attempts": 1,
                "retry_count": 0,
            }
        )
    )
    approval.chmod(0o600)

    with pytest.raises(OrchestratorError, match="runtime_approval_invalid"):
        load_installed_runtime_approval(
            candidate_path=candidate,
            approval_path=approval,
        )
