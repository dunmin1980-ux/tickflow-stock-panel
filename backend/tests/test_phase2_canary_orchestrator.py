from __future__ import annotations

import hashlib
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
    ApprovalScopedAttemptLedger,
    ApprovedCanaryArtifacts,
    AttemptLedgerStore,
    BackendError,
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
HISTORICAL_REQUEST_ID = "d59766101b63450e8148541d589a90bf"
HISTORICAL_HASHES = {
    "reports/phase2_provider_canary/live_canary/evidence/"
    f"{HISTORICAL_REQUEST_ID}.json": (
        "0ef11071083053ac7db825b39492e7e5d24ce27d32cc40c6daa90f43138603e4"
    ),
    "reports/phase2_provider_canary/live_canary/rejected/"
    f"{HISTORICAL_REQUEST_ID}.json": (
        "ad7bada723a39a4fd0b266a3740e59f3e040d4ce81e79e6e594b6a2b3785e2f4"
    ),
    "reports/phase2_provider_canary/consumed_unknown_diagnosis.json": (
        "be69267d764634ce39ae8311ddbf2dcba11b1709baa5c999aad11bcd17e8d529"
    ),
    "reports/tickflow_phase2b_consumed_unknown_diagnosis.md": (
        "601eb1b07607a09ff662b10793d67bc912b5958df69b28a391826fdcc8628d93"
    ),
    "reports/tickflow_phase2b_single_symbol_canary_eval.md": (
        "a1ec5092f1c7db423d9b27f946c93bb904969b13bb67fb810888ad83ef7e5bd5"
    ),
}


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
    store = AttemptLedgerStore(state_root, fixture_writes_enabled=True)

    ledger = store.prepare(identity, timestamp=FIXED_TIME)

    path = state_root / "attempt-ledger.json"
    assert ledger.state is LedgerState.PREPARED
    assert ledger.provider_attempt_count == 0
    assert ledger.retry_count == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_bytes()) == ledger.model_dump(mode="json")
    assert path.read_bytes().endswith(b"\n")
    serialized = path.read_text(encoding="utf-8")
    assert "readiness_contract_sha256" not in serialized
    for forbidden in ("Authorization", "Bearer ", "api_key", "secret"):
        assert forbidden not in serialized.lower()


def test_legacy_attempt_ledger_store_is_read_only_by_default(
    state_root: Path,
    identity: CanaryRunIdentity,
) -> None:
    store = AttemptLedgerStore(state_root)

    with pytest.raises(OrchestratorError, match="legacy_ledger_read_only"):
        store.prepare(identity, timestamp=FIXED_TIME)


def test_fixture_writer_cannot_target_frozen_history(
    state_root: Path,
    identity: CanaryRunIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_module,
        "_frozen_legacy_state_roots",
        lambda: (state_root,),
    )
    store = AttemptLedgerStore(state_root, fixture_writes_enabled=True)

    with pytest.raises(OrchestratorError, match="historical_ledger_immutable"):
        store.prepare(identity, timestamp=FIXED_TIME)


def test_dispatch_transition_durably_consumes_the_only_attempt(
    state_root: Path,
    identity: CanaryRunIdentity,
) -> None:
    store = AttemptLedgerStore(state_root, fixture_writes_enabled=True)
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
    store = AttemptLedgerStore(state_root, fixture_writes_enabled=True)
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
    store = AttemptLedgerStore(state_root, fixture_writes_enabled=True)
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


def test_publish_private_file_does_not_overwrite_race_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir(mode=0o700)
    target = parent / "evidence.json"
    original_link = os.link

    def insert_race_winner(*args: object, **kwargs: object) -> None:
        target.write_bytes(b"race-winner\n")
        target.chmod(0o600)
        original_link(*args, **kwargs)

    monkeypatch.setattr(orchestrator_module.os, "link", insert_race_winner)

    with pytest.raises(OrchestratorError, match="artifact_already_exists"):
        orchestrator_module._publish_private_file(target, b"new-evidence\n")

    assert target.read_bytes() == b"race-winner\n"
    assert not list(parent.glob("*.partial"))


def test_recovery_marks_pre_dispatch_and_unknown_dispatch_differently(
    tmp_path: Path,
    identity: CanaryRunIdentity,
) -> None:
    before_root = tmp_path / "before"
    before_root.mkdir(mode=0o700)
    before = AttemptLedgerStore(before_root, fixture_writes_enabled=True)
    before.prepare(identity, timestamp=FIXED_TIME)

    before_recovered = recover_attempt_state(
        before,
        timestamp="2026-08-02T12:05:00Z",
    )

    assert before_recovered.state is LedgerState.FAILED_BEFORE_DISPATCH
    assert before_recovered.provider_attempt_count == 0

    after_root = tmp_path / "after"
    after_root.mkdir(mode=0o700)
    after = AttemptLedgerStore(after_root, fixture_writes_enabled=True)
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


def test_consumed_historical_attempt_remains_dispatch_ledger_only() -> None:
    for relative, expected in HISTORICAL_HASHES.items():
        actual = hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
        assert actual == expected
    diagnosis = json.loads(
        (
            REPO_ROOT
            / "reports/phase2_provider_canary/consumed_unknown_diagnosis.json"
        ).read_bytes()
    )
    historical_evidence = json.loads(
        (
            REPO_ROOT
            / "reports/phase2_provider_canary/live_canary/evidence"
            / f"{HISTORICAL_REQUEST_ID}.json"
        ).read_bytes()
    )
    ledger_path = (
        Path.home()
        / "Library/Application Support/TickFlowPhase2Canary/runtime-v1"
        / "history"
        / HISTORICAL_REQUEST_ID
        / "attempt-ledger.json"
    )

    assert diagnosis["classification"] == "DISPATCH_LEDGER_ONLY"
    assert diagnosis["root_cause"] == (
        "ROOT_CAUSE_NOT_PROVABLE_WITH_CURRENT_EVIDENCE"
    )
    assert historical_evidence["attempt_ledger_state"] == (
        "ATTEMPT_CONSUMED_UNKNOWN"
    )
    assert not any(
        event in historical_evidence for event in orchestrator_module._PROXY_EVENTS
    )
    if ledger_path.exists():
        assert hashlib.sha256(ledger_path.read_bytes()).hexdigest() == (
            "ded0f39813dd70657cedd6a8c92669d3508480e97efc6ed44b1a45f9470b5772"
        )


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
        AttemptLedgerStore(real, fixture_writes_enabled=True).prepare(
            identity,
            timestamp=FIXED_TIME,
        )


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

    store = AttemptLedgerStore(state_root, fixture_writes_enabled=True)
    store.prepare(identity, timestamp=FIXED_TIME)
    store.transition(
        LedgerState.FAILED_BEFORE_DISPATCH,
        timestamp="2026-08-02T12:00:01Z",
    )
    lock_path.write_bytes(orchestrator_module._canonical_bytes(stale))
    lock_path.chmod(0o600)

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
        readiness_contract_sha256="6" * 64,
        orchestrator_source_sha256="7" * 64,
    )


@pytest.fixture
def orchestrator_config(tmp_path: Path) -> CanaryOrchestratorConfig:
    state = tmp_path / "state"
    attempts = tmp_path / "attempts"
    work = tmp_path / "work"
    output = tmp_path / "canary-output"
    historical_output = tmp_path / "historical-output"
    candidates = tmp_path / "candidates"
    for path in (state, attempts, work, output, historical_output, candidates):
        path.mkdir(mode=0o700)
    return CanaryOrchestratorConfig(
        repo_root=REPO_ROOT,
        state_root=state,
        attempts_root=attempts,
        work_root=work,
        canary_output_root=output,
        historical_evidence_root=historical_output,
        candidate_roots=(candidates,),
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


class _ReceiptFaultBackend(MockCanaryBackend):
    def __init__(
        self,
        *,
        receipt_fault: str,
        relay_response: dict[str, Any],
    ) -> None:
        super().__init__(scenario="provider_1s", relay_response=relay_response)
        self.receipt_fault = receipt_fault

    def dispatch(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> Any:
        result = super().dispatch(context, deadline=deadline)
        proxy_receipt = context.proxy_output_dir / "proxy-receipt.json"
        if self.receipt_fault == "missing":
            proxy_receipt.unlink()
        elif self.receipt_fault == "malformed":
            proxy_receipt.write_text('{"invalid":true}\n', encoding="utf-8")
        return result


class _LedgerObservingBackend(MockCanaryBackend):
    def __init__(
        self,
        *,
        attempts_root: Path,
        relay_response: dict[str, Any],
    ) -> None:
        super().__init__(scenario="provider_1s", relay_response=relay_response)
        self.attempts_root = attempts_root
        self.ledger_state_at_archive: LedgerState | None = None

    def archive_evidence(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> Any:
        ledgers = list(self.attempts_root.glob("*/*/ledger.json"))
        assert len(ledgers) == 1
        self.ledger_state_at_archive = ApprovalScopedAttemptLedger.model_validate_json(
            ledgers[0].read_bytes()
        ).state
        return super().archive_evidence(context, deadline=deadline)


class _SensitiveStderrBackend(MockCanaryBackend):
    def _record_child_evidence(self) -> None:
        timing = orchestrator_module.ChildExecutionTiming(
            started_at="2026-08-08T08:23:20.000000Z",
            exited_at="2026-08-08T08:23:40.000000Z",
            started_monotonic_ns=20_000_000_000,
            exited_monotonic_ns=40_000_000_000,
        )
        self._child_evidence["relay"] = orchestrator_module.classify_completed_child(
            "relay",
            subprocess.CompletedProcess(
                ["mock-relay"],
                1,
                stdout="",
                stderr="Authorization: Bearer [REDACTED_TEST_FIXTURE]",
            ),
            timing,
        )
        self._child_evidence["proxy"] = orchestrator_module.classify_running_child(
            "proxy",
            timing,
        )


class _ProxyReadinessFailureBackend(MockCanaryBackend):
    def prepare(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> None:
        super().prepare(context, deadline=deadline)
        raise BackendError("PROXY_READINESS_TIMEOUT")


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
    assert backend.archive_count == 1
    assert backend.cleanup_count == 1
    assert backend.lifecycle == ["prepare", "dispatch", "archive", "cleanup"]
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
    receipt_archive = (
        orchestrator_config.canary_output_root / "receipts" / ("a" * 32)
    )
    assert (receipt_archive / "relay-receipt.json").is_file()
    assert (receipt_archive / "proxy-receipt.json").is_file()
    assert (receipt_archive / "child-metadata.json").is_file()
    assert (receipt_archive / "archive-status.json").is_file()


def test_cleanup_failure_preserves_archived_receipts(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    result, backend = _run_mock(
        scenario="cleanup_timeout",
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        relay_response=relay_response,
    )

    archive = orchestrator_config.canary_output_root / "receipts" / ("a" * 32)
    assert result.terminal_state == "CLEANUP_TIMEOUT"
    assert backend.lifecycle == ["prepare", "dispatch", "archive", "cleanup"]
    assert (archive / "archive-status.json").is_file()
    assert (archive / "relay-receipt.json").is_file()
    assert (archive / "proxy-receipt.json").is_file()


@pytest.mark.parametrize(
    ("receipt_fault", "expected"),
    [
        ("missing", "RECEIPT_MISSING"),
        ("malformed", "RECEIPT_MALFORMED"),
    ],
)
def test_receipt_fault_is_archived_without_copying_untrusted_body(
    receipt_fault: str,
    expected: str,
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    backend = _ReceiptFaultBackend(
        receipt_fault=receipt_fault,
        relay_response=relay_response,
    )

    result = run_single_symbol_canary(
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _artifacts: None,
        secret_reader=lambda: "placeholder-canary-secret",
        request_id_factory=lambda: "a" * 32,
        wall_clock=lambda: FIXED_TIME,
        monotonic=backend.monotonic,
    )

    archive = orchestrator_config.canary_output_root / "receipts" / ("a" * 32)
    status = json.loads((archive / "archive-status.json").read_bytes())
    assert result.terminal_state == expected
    assert result.receipt_archive_status == expected
    assert status["archive_status"] == expected
    assert not (archive / "proxy-receipt.json").exists()
    assert (archive / "relay-receipt.json").is_file()
    assert backend.lifecycle == ["prepare", "dispatch", "archive", "cleanup"]
    serialized = "\n".join(
        path.read_text(encoding="utf-8") for path in archive.iterdir()
    )
    assert "placeholder-canary-secret" not in serialized
    assert "Authorization" not in serialized


def test_stage_receipt_is_parsed_from_single_controlled_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "proxy-receipt.json"
    events = {
        name: orchestrator_module._mock_stage_event(
            name,
            occurred=True,
            index=index,
        )
        for index, name in enumerate(
            orchestrator_module._PROXY_EVENTS,
            start=1,
        )
    }
    receipt_path.write_bytes(
        canonical_json_bytes(
            {
                "receipt_schema_version": 2,
                "request_id": "a" * 32,
                "component": "proxy",
                "method_allowed": True,
                "path_allowed": True,
                "auth_present": True,
                "tls_verification": True,
                "redirect_followed": False,
                "provider_attempt_count": 1,
                "provider_http_status": 200,
                "retry_count": 0,
                "response_category": "FORWARDED",
                "response_size": 2,
                "response_bytes": 2,
                "terminal_status": "FORWARDED",
                **events,
            }
        )
    )
    reads = 0
    original = orchestrator_module._read_regular_unrestricted

    def count_read(path: Path, category: str) -> bytes:
        nonlocal reads
        reads += 1
        return original(path, category)

    monkeypatch.setattr(
        orchestrator_module,
        "_read_regular_unrestricted",
        count_read,
    )

    raw, value, status, _events = orchestrator_module._load_stage_receipt(
        receipt_path,
        component="proxy",
        request_id="a" * 32,
    )

    assert raw is not None
    assert value is not None
    assert status == "VALID"
    assert reads == 1


def test_archive_precedes_terminal_ledger_transition(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    backend = _LedgerObservingBackend(
        attempts_root=orchestrator_config.attempts_root,
        relay_response=relay_response,
    )

    result = run_single_symbol_canary(
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _artifacts: None,
        secret_reader=lambda: "placeholder-canary-secret",
        request_id_factory=lambda: "a" * 32,
        wall_clock=lambda: FIXED_TIME,
        monotonic=backend.monotonic,
    )

    assert result.terminal_state == "SUCCEEDED"
    assert backend.ledger_state_at_archive is LedgerState.HOST_VALIDATION_COMPLETED
    assert result.attempt_ledger_state is LedgerState.CLEANUP_COMPLETED


def test_pre_dispatch_failure_archives_proxy_ready_receipt_without_consuming_attempt(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    result, backend = _run_mock(
        scenario="host_crash_before_dispatch",
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        relay_response=relay_response,
    )

    target = orchestrator_config.canary_output_root / "receipts" / ("a" * 32)
    assert result.terminal_state == "HOST_CRASH_BEFORE_DISPATCH"
    assert result.attempt_ledger_state is LedgerState.FAILED_BEFORE_DISPATCH
    assert result.provider_attempt_count == 0
    assert result.receipt_archive_status == "PRE_DISPATCH_ARCHIVED"
    assert backend.archive_count == 1
    assert backend.lifecycle == ["prepare", "archive", "cleanup"]
    assert (target / "proxy-receipt.json").is_file()
    assert not (target / "relay-receipt.json").exists()
    status = json.loads((target / "archive-status.json").read_bytes())
    assert status["archive_status"] == "PRE_DISPATCH_ARCHIVED"
    assert status["proxy_receipt_status"] == "VALID"
    assert status["relay_receipt_status"] == "RECEIPT_MISSING"
    assert status["last_proven_stage"] == "proxy_ready"


def test_proxy_readiness_timeout_is_failed_before_dispatch_without_attempt(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    backend = _ProxyReadinessFailureBackend(
        scenario="provider_1s",
        relay_response=relay_response,
    )

    result = run_single_symbol_canary(
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _artifacts: None,
        secret_reader=lambda: "placeholder-canary-secret",
        request_id_factory=lambda: "a" * 32,
        wall_clock=lambda: FIXED_TIME,
        monotonic=backend.monotonic,
    )

    assert result.terminal_state == "PROXY_READINESS_TIMEOUT"
    assert result.attempt_ledger_state is LedgerState.FAILED_BEFORE_DISPATCH
    assert result.provider_attempt_count == 0
    assert backend.dispatch_count == 0
    assert backend.cleanup_count == 1


def test_sensitive_child_stderr_is_redacted_before_archive(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    backend = _SensitiveStderrBackend(
        scenario="relay_exit_before_candidate",
        relay_response=relay_response,
    )

    result = run_single_symbol_canary(
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _artifacts: None,
        secret_reader=lambda: "placeholder-canary-secret",
        request_id_factory=lambda: "a" * 32,
        wall_clock=lambda: FIXED_TIME,
        monotonic=backend.monotonic,
    )

    archive = orchestrator_config.canary_output_root / "receipts" / ("a" * 32)
    metadata = json.loads((archive / "child-metadata.json").read_bytes())
    serialized = (archive / "child-metadata.json").read_text(encoding="utf-8")
    assert result.terminal_state == "RELAY_NONZERO_EXIT"
    assert metadata["relay"]["stderr"]["stderr_redacted"] is True
    assert metadata["relay"]["stderr"]["stderr_excerpt"] is None
    assert metadata["relay"]["stderr"]["stderr_sha256"] is None
    assert "synthetic-canary-secret-value" not in serialized
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


def test_host_validation_cannot_publish_after_orchestrator_deadline(
    monkeypatch: pytest.MonkeyPatch,
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=relay_response,
    )
    original = orchestrator_module._host_validate_and_render

    def delayed_validation(*args: object, **kwargs: object) -> tuple[dict[str, Any], str]:
        document, rendered = original(*args, **kwargs)
        backend._now = 90.000_001
        return document, rendered

    monkeypatch.setattr(
        orchestrator_module,
        "_host_validate_and_render",
        delayed_validation,
    )

    result = run_single_symbol_canary(
        config=orchestrator_config,
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _artifacts: None,
        secret_reader=lambda: "placeholder-canary-secret",
        request_id_factory=lambda: "a" * 32,
        wall_clock=lambda: FIXED_TIME,
        monotonic=backend.monotonic,
    )

    assert result.terminal_state == "ORCHESTRATOR_TIMEOUT"
    assert result.provider_attempt_count == 1
    assert result.retry_count == 0
    assert result.route == "rejected"
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
            "RELAY_NONZERO_EXIT",
            LedgerState.FAILED_AFTER_DISPATCH,
            1,
            "rejected",
        ),
        (
            "proxy_exit_after_request",
            "PROXY_NONZERO_EXIT",
            LedgerState.FAILED_AFTER_DISPATCH,
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
            "RELAY_NONZERO_EXIT",
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


def test_interrupted_legacy_ledger_blocks_globally_without_secret_read(
    orchestrator_config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: dict[str, Any],
    relay_response: dict[str, Any],
) -> None:
    store = AttemptLedgerStore(
        orchestrator_config.state_root,
        fixture_writes_enabled=True,
    )
    interrupted = CanaryRunIdentity(
        request_id="b" * 32,
        symbol="000403.SZ",
        trade_date="2026-07-31",
        provider="openai",
        endpoint_alias="openai_responses_v1",
        **artifacts.model_dump(
            mode="python",
            exclude={"readiness_contract_sha256"},
        ),
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

    assert result.terminal_state == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.provider_attempt_count == 0
    assert store.load().state is LedgerState.NETWORK_DISPATCH_STARTED
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
    def __init__(self, ready_path: Path | None = None) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.ready_path = ready_path

    def __call__(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, timeout))
        if (
            self.ready_path is not None
            and command[:2] == ["docker", "start"]
            and "proxy" in command[-1]
        ):
            self.ready_path.write_bytes(
                canonical_json_bytes(
                    {
                        "listener_ready": True,
                        "monotonic_ns": 1,
                        "proxy_ready": True,
                        "request_id": "a" * 32,
                        "wall_time": FIXED_TIME,
                    }
                )
            )
        if command[:2] == ["docker", "inspect"] and "--format" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"Running": True, "ExitCode": 0, "Error": ""}),
                "",
            )
        if command[:3] == ["docker", "container", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                1,
                "[]\n",
                f"Error response from daemon: No such container: {command[-1]}",
            )
        if command[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                1,
                "[]\n",
                f"Error response from daemon: network {command[-1]} not found",
            )
        return subprocess.CompletedProcess(command, 0, "", "")


class _GlobalResidueExecutor(_RecordingExecutor):
    def __call__(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, timeout))
        if command[:3] == ["docker", "container", "ls"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "\n".join(
                    (
                        "unrelated-container",
                        "phase2-openai-proxy-deadbeef0000",
                        "phase2-canary-relay-worker-deadbeef0000",
                    )
                ),
                "",
            )
        if command[:3] == ["docker", "network", "ls"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "\n".join(
                    (
                        "bridge",
                        "phase2-canary-relay-deadbeef0000",
                        "phase2-canary-egress-deadbeef0000",
                    )
                ),
                "",
            )
        raise AssertionError(f"unexpected command: {command}")


def test_docker_backend_global_residue_scan_is_prefix_exact_and_bounded() -> None:
    executor = _GlobalResidueExecutor()
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: 0.0)

    result = backend.inspect_global_residue(deadline=20.0)

    assert result.completed is False
    assert result.container_residue_count == 2
    assert result.network_residue_count == 2
    assert [command[:3] for command, _timeout in executor.calls] == [
        ["docker", "container", "ls"],
        ["docker", "network", "ls"],
    ]
    assert all(timeout <= 15.0 for _command, timeout in executor.calls)


class _ChildOutcomeExecutor(_RecordingExecutor):
    def __init__(self, outcome: str, ready_path: Path | None = None) -> None:
        super().__init__(ready_path)
        self.outcome = outcome

    def __call__(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, timeout))
        if (
            self.ready_path is not None
            and command[:2] == ["docker", "start"]
            and "proxy" in command[-1]
        ):
            self.ready_path.write_bytes(
                canonical_json_bytes(
                    {
                        "listener_ready": True,
                        "monotonic_ns": 1,
                        "proxy_ready": True,
                        "request_id": "a" * 32,
                        "wall_time": FIXED_TIME,
                    }
                )
            )
        if command[:3] == ["docker", "start", "--attach"]:
            if self.outcome == "relay_timeout":
                raise subprocess.TimeoutExpired(command, timeout, stderr="deadline")
            if self.outcome == "relay_process_error":
                raise subprocess.SubprocessError("relay process failed")
            if self.outcome == "relay_nonzero":
                return subprocess.CompletedProcess(command, 1, "", "relay failed")
            if self.outcome == "relay_signal":
                return subprocess.CompletedProcess(command, 137, "", "killed")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["docker", "inspect"] and "--format" in command:
            state = {
                "Running": self.outcome
                not in {"proxy_nonzero", "proxy_signal"},
                "ExitCode": (
                    137
                    if self.outcome == "proxy_signal"
                    else 7
                    if self.outcome == "proxy_nonzero"
                    else 0
                ),
                "Error": (
                    "killed"
                    if self.outcome == "proxy_signal"
                    else "proxy failed"
                    if self.outcome == "proxy_nonzero"
                    else ""
                ),
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(state), "")
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
    proxy_output = root / "proxy-output"
    proxy_output.mkdir(mode=0o700)
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
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
        proxy_output_dir=proxy_output,
        receipt_archive_dir=receipts / ("a" * 32),
        secret_path=secret,
        proxy_image_id="sha256:" + "4" * 64,
        relay_image_id="sha256:" + "5" * 64,
        runtime_contract=load_runtime_contract().model_dump(mode="json"),
    )


def test_docker_backend_commands_have_exact_isolation_and_no_secret_surface(
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _RecordingExecutor(
        runtime_context.proxy_output_dir / "proxy-ready.json"
    )
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: 0.0)

    backend.prepare(runtime_context, deadline=90.0)

    commands = [command for command, _timeout in executor.calls]
    joined = "\n".join(" ".join(command) for command in commands)
    assert any("--internal" in command for command in commands)
    assert joined.count("docker network create") == 2
    assert "--network-alias phase2-egress-proxy" in joined
    assert "dst=/run/phase2/provider-auth,readonly" in joined
    assert joined.count("dst=/input/request.json,readonly") == 2
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


def test_docker_backend_refuses_dispatch_until_proxy_application_is_ready(
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _RecordingExecutor()
    now = [0.0]

    def wait(seconds: float) -> None:
        now[0] += seconds

    backend = DockerCanaryBackend(
        executor=executor,
        monotonic=lambda: now[0],
        waiter=wait,
    )

    with pytest.raises(BackendError, match="PROXY_READINESS_TIMEOUT"):
        backend.prepare(runtime_context, deadline=90.0)

    commands = [command for command, _timeout in executor.calls]
    assert any(command[:2] == ["docker", "start"] for command in commands)
    assert not any(
        command[:3] == ["docker", "start", "--attach"]
        for command in commands
    )


def test_docker_backend_dispatches_relay_once_and_cleanup_is_bounded(
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _RecordingExecutor(
        runtime_context.proxy_output_dir / "proxy-ready.json"
    )
    now = [0.0]
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: now[0])
    backend.prepare(runtime_context, deadline=90.0)
    events = {
        name: orchestrator_module._mock_stage_event(
            name,
            occurred=True,
            index=index,
            http_status=(
                200
                if name in {"response_headers_received", "response_body_completed"}
                else None
            ),
            byte_count=(
                1
                if name in {"request_write_completed", "response_body_completed"}
                else None
            ),
        )
        for index, name in enumerate(orchestrator_module._PROXY_EVENTS, start=1)
    }
    (runtime_context.proxy_output_dir / "proxy-receipt.json").write_bytes(
        canonical_json_bytes(
            {
                "receipt_schema_version": 2,
                "request_id": runtime_context.request_id,
                "component": "proxy",
                "method_allowed": True,
                "path_allowed": True,
                "auth_present": True,
                "tls_verification": True,
                "redirect_followed": False,
                "provider_attempt_count": 1,
                "provider_http_status": 200,
                "retry_count": 0,
                "response_category": "FORWARDED",
                "response_size": 1,
                "response_bytes": 1,
                "terminal_status": "FORWARDED",
                **events,
            }
        )
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


@pytest.mark.parametrize(
    ("outcome", "category"),
    [
        ("relay_nonzero", "RELAY_NONZERO_EXIT"),
        ("relay_timeout", "RELAY_TIMEOUT"),
        ("relay_process_error", "RELAY_PROCESS_ERROR"),
        ("relay_signal", "RELAY_SIGNALLED"),
        ("proxy_nonzero", "PROXY_NONZERO_EXIT"),
        ("proxy_signal", "PROXY_SIGNALLED"),
    ],
)
def test_docker_child_outcomes_are_not_collapsed_to_timeout(
    outcome: str,
    category: str,
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _ChildOutcomeExecutor(
        outcome,
        runtime_context.proxy_output_dir / "proxy-ready.json",
    )
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: 0.0)
    backend.prepare(runtime_context, deadline=90.0)

    with pytest.raises(BackendError, match=category) as raised:
        backend.dispatch(runtime_context, deadline=90.0)

    assert raised.value.category == category
    assert raised.value.unknown_dispatch_state is False


def test_host_deadline_expiry_is_not_reported_as_relay_timeout(
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _ChildOutcomeExecutor(
        "relay_timeout",
        runtime_context.proxy_output_dir / "proxy-ready.json",
    )
    now = [0.0]
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: now[0])
    backend.prepare(runtime_context, deadline=90.0)
    now[0] = 20.0

    with pytest.raises(BackendError, match="ORCHESTRATOR_TIMEOUT") as raised:
        backend.dispatch(runtime_context, deadline=90.0)

    evidence = backend._child_evidence["relay"]
    assert raised.value.category == "ORCHESTRATOR_TIMEOUT"
    assert evidence.child_state is orchestrator_module.ChildState.TIMEOUT
    assert evidence.terminal_reason == "ORCHESTRATOR_TIMEOUT"
    relay_timeout = next(
        timeout
        for command, timeout in executor.calls
        if command[:3] == ["docker", "start", "--attach"]
    )
    assert relay_timeout == 70.0

    archive = backend.archive_evidence(runtime_context, deadline=90.0)
    status = json.loads(
        (runtime_context.receipt_archive_dir / "archive-status.json").read_bytes()
    )
    assert archive.child_terminal_reason == "ORCHESTRATOR_TIMEOUT"
    assert status["terminal_reason_source"] == "host_timeout"


def test_last_proven_stage_uses_persisted_monotonic_time_across_components(
    runtime_context: CanaryRuntimeContext,
) -> None:
    backend = MockCanaryBackend(scenario="provider_1s", relay_response={})
    backend._write_receipts(runtime_context)
    backend._record_child_evidence()
    for component, path, base, wall_time in (
        (
            "proxy",
            runtime_context.proxy_output_dir / "proxy-receipt.json",
            1,
            "2030-01-01T00:00:00Z",
        ),
        (
            "relay",
            runtime_context.output_dir / "relay-receipt.json",
            100,
            "2020-01-01T00:00:00Z",
        ),
    ):
        receipt = json.loads(path.read_bytes())
        events = (
            orchestrator_module._PROXY_EVENTS
            if component == "proxy"
            else orchestrator_module._RELAY_EVENTS
        )
        for index, name in enumerate(events, start=base):
            receipt[name]["wall_time"] = wall_time
            receipt[name]["monotonic_ns"] = index
        path.write_bytes(canonical_json_bytes(receipt))

    archive = orchestrator_module._archive_stage_evidence(
        runtime_context,
        backend._child_evidence,
    )

    status = json.loads(
        (runtime_context.receipt_archive_dir / "archive-status.json").read_bytes()
    )
    assert archive.last_proven_stage == "candidate_ready_published"
    assert status["last_proven_stage"] == "candidate_ready_published"


def test_receipt_directory_publication_does_not_replace_a_racing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "receipts"
    parent.mkdir(mode=0o700)
    staging = parent / ".request.partial"
    staging.mkdir(mode=0o700)
    source = staging / "archive-status.json"
    source.write_bytes(b"{}\n")
    source.chmod(0o600)
    target = parent / ("2" * 32)
    original_mkdir = os.mkdir
    racing_inode: list[int] = []

    def race_target(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if path == target.name and dir_fd is not None:
            original_mkdir(path, mode, dir_fd=dir_fd)
            racing_inode.append(os.stat(path, dir_fd=dir_fd).st_ino)
            raise FileExistsError(path)
        original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(orchestrator_module.os, "mkdir", race_target)

    with pytest.raises(OrchestratorError, match="artifact_directory_already_exists"):
        orchestrator_module._publish_private_directory_no_clobber(
            staging,
            target,
        )

    assert racing_inode
    assert target.is_dir()
    assert target.stat().st_ino == racing_inode[0]
    assert source.is_file()


def test_receipt_directory_publication_binds_created_target_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "receipts"
    parent.mkdir(mode=0o700)
    staging = parent / ".request.partial"
    staging.mkdir(mode=0o700)
    source = staging / "archive-status.json"
    source.write_bytes(b"{}\n")
    source.chmod(0o600)
    target = parent / ("2" * 32)
    displaced = parent / f"{target.name}.displaced"
    original_open = os.open
    raced = False

    def replace_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal raced
        if path == target.name and dir_fd is not None and not raced:
            raced = True
            os.rename(
                target.name,
                displaced.name,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
            os.mkdir(target.name, mode=0o700, dir_fd=dir_fd)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(orchestrator_module.os, "open", replace_before_open)

    with pytest.raises(OrchestratorError, match="artifact_directory_publish_failed"):
        orchestrator_module._publish_private_directory_no_clobber(
            staging,
            target,
        )

    assert raced
    assert displaced.is_dir()
    assert source.is_file()


def test_relay_self_reason_refines_host_nonzero_exit_without_losing_child_evidence(
    runtime_context: CanaryRuntimeContext,
) -> None:
    fixture_backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response={},
    )
    fixture_backend._write_receipts(runtime_context)
    relay_path = runtime_context.output_dir / "relay-receipt.json"
    relay_receipt = json.loads(relay_path.read_bytes())
    relay_receipt.update(
        {
            "status": "REJECTED",
            "exit_code": 2,
            "terminal_status": "RELAY_PROXY_CONNECT_FAILED",
            "terminal_reason_source": "relay_self",
            "error_category": "RELAY_PROXY_CONNECT_FAILED",
            "proxy_http_status": None,
            "response_size": None,
            "response_sha256": None,
        }
    )
    for name in (
        "proxy_connect_completed",
        "request_submitted",
        "response_wait_started",
        "response_received",
        "candidate_write_started",
        "candidate_write_completed",
        "candidate_ready_published",
    ):
        relay_receipt[name] = orchestrator_module._mock_stage_event(
            name,
            occurred=False,
            index=0,
        )
    relay_path.write_bytes(canonical_json_bytes(relay_receipt))
    timing = orchestrator_module.ChildExecutionTiming(
        started_at=FIXED_TIME,
        exited_at=FIXED_TIME,
        started_monotonic_ns=1,
        exited_monotonic_ns=2,
    )
    child_evidence = {
        "relay": orchestrator_module.classify_completed_child(
            "relay",
            subprocess.CompletedProcess(["mock-relay"], 2, "", ""),
            timing,
            interpret_positive_signal=True,
        ),
        "proxy": orchestrator_module.classify_running_child("proxy", timing),
    }

    archive = orchestrator_module._archive_stage_evidence(
        runtime_context,
        child_evidence,
    )

    status = json.loads(
        (runtime_context.receipt_archive_dir / "archive-status.json").read_bytes()
    )
    child = json.loads(
        (runtime_context.receipt_archive_dir / "child-metadata.json").read_bytes()
    )
    assert archive.child_terminal_reason == "RELAY_PROXY_CONNECT_FAILED"
    assert status["child_terminal_reason"] == "RELAY_PROXY_CONNECT_FAILED"
    assert status["host_child_terminal_reason"] == "RELAY_NONZERO_EXIT"
    assert status["terminal_reason_source"] == "relay_self"
    assert child["relay"]["terminal_reason"] == "RELAY_NONZERO_EXIT"


class _CleanupInspectionErrorExecutor(_RecordingExecutor):
    def __call__(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, timeout))
        if tuple(command[:3]) in {
            ("docker", "container", "inspect"),
            ("docker", "network", "inspect"),
        }:
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "permission denied while contacting daemon",
            )
        return subprocess.CompletedProcess(command, 0, "", "")


def test_cleanup_inspection_error_cannot_prove_zero_residue(
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _CleanupInspectionErrorExecutor()
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: 0.0)
    backend._names = backend._runtime_names(runtime_context.request_id)

    result = backend.cleanup(runtime_context, deadline=15.0)

    assert result.completed is False
    assert result.container_residue_count == 2
    assert result.network_residue_count == 2


class _CleanupRemovalErrorExecutor(_RecordingExecutor):
    def __call__(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        if command[:3] in (
            ["docker", "rm", "--force"],
            ["docker", "network", "rm"],
        ):
            self.calls.append((command, timeout))
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "permission denied while contacting daemon",
            )
        return super().__call__(command, timeout)


def test_cleanup_removal_error_is_not_reported_complete(
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _CleanupRemovalErrorExecutor()
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: 0.0)
    backend._names = backend._runtime_names(runtime_context.request_id)

    result = backend.cleanup(runtime_context, deadline=15.0)

    assert result.completed is False
    assert result.container_residue_count == 0
    assert result.network_residue_count == 0


def test_cleanup_without_cached_names_proves_deterministic_absence(
    runtime_context: CanaryRuntimeContext,
) -> None:
    executor = _RecordingExecutor()
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: 0.0)

    result = backend.cleanup(runtime_context, deadline=15.0)

    assert result.completed is True
    assert len(executor.calls) == 8
    assert any(
        command[:3] == ["docker", "container", "inspect"]
        for command, _timeout in executor.calls
    )
    assert any(
        command[:3] == ["docker", "network", "inspect"]
        for command, _timeout in executor.calls
    )


def test_archives_exact_proxy_receipt_validated_during_dispatch(
    runtime_context: CanaryRuntimeContext,
) -> None:
    fixture_backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response={},
    )
    fixture_backend._write_receipts(runtime_context)
    proxy_receipt_path = runtime_context.proxy_output_dir / "proxy-receipt.json"
    validated_bytes = proxy_receipt_path.read_bytes()
    executor = _RecordingExecutor(
        runtime_context.proxy_output_dir / "proxy-ready.json"
    )
    backend = DockerCanaryBackend(executor=executor, monotonic=lambda: 0.0)
    backend.prepare(runtime_context, deadline=90.0)

    backend.dispatch(runtime_context, deadline=90.0)
    proxy_receipt_path.write_text('{"replaced":true}\n', encoding="utf-8")
    archive = backend.archive_evidence(runtime_context, deadline=15.0)

    archived = runtime_context.receipt_archive_dir / "proxy-receipt.json"
    assert archive.status == "ARCHIVED"
    assert archived.read_bytes() == validated_bytes


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
) -> None:
    candidate_path = tmp_path / "runtime_contract_candidate.json"
    approval_path = tmp_path / "runtime-contract-approval.json"
    candidate_bytes = (
        REPO_ROOT
        / "reports/phase2_provider_canary/runtime_contract_candidate.json"
    ).read_bytes()
    candidate = json.loads(candidate_bytes)
    artifact_identity = candidate["artifact_identity"]
    candidate_path.write_bytes(candidate_bytes)
    candidate_path.chmod(0o644)
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()

    with pytest.raises(OrchestratorError, match="runtime_approval_invalid"):
        load_installed_runtime_approval(
            candidate_path=candidate_path,
            approval_path=approval_path,
        )

    approval_path.write_bytes(
        canonical_json_bytes(
            {
                "runtime_approval_schema_version": 2,
                "approved_candidate_sha256": candidate_sha,
                "approval_scope": "single_symbol_openai_canary_runtime_v2",
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


def test_runtime_approval_loader_rejects_incomplete_schema_v2_candidate(
    tmp_path: Path,
    artifacts: ApprovedCanaryArtifacts,
) -> None:
    candidate_path = tmp_path / "runtime_contract_candidate.json"
    approval_path = tmp_path / "runtime-contract-approval.json"
    artifact_identity = artifacts.model_dump(mode="json")
    artifact_identity.pop("approval_candidate_sha256")
    candidate_bytes = canonical_json_bytes(
        {
            "runtime_candidate_schema_version": 2,
            "status": "PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL",
            "artifact_identity": artifact_identity,
        }
    )
    candidate_path.write_bytes(candidate_bytes)
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    approval_path.write_bytes(
        canonical_json_bytes(
            {
                "runtime_approval_schema_version": 2,
                "approved_candidate_sha256": candidate_sha,
                "approval_scope": "single_symbol_openai_canary_runtime_v2",
                "symbol": "000403.SZ",
                "provider": "openai",
                "maximum_provider_attempts": 1,
                "retry_count": 0,
            }
        )
    )
    approval_path.chmod(0o600)

    with pytest.raises(OrchestratorError, match="runtime_candidate_invalid"):
        load_installed_runtime_approval(
            candidate_path=candidate_path,
            approval_path=approval_path,
        )


def test_runtime_schema_v2_loads_exact_readiness_identity_and_rejects_v1(
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "runtime_contract_candidate.json"
    approval_directory = tmp_path / "approval"
    approval_directory.mkdir(mode=0o700)
    approval_path = approval_directory / "runtime-contract-approval.json"
    candidate_value = json.loads(
        (
            REPO_ROOT
            / "reports/phase2_provider_canary/runtime_contract_candidate.json"
        ).read_bytes()
    )
    readiness_sha256 = candidate_value["artifact_identity"][
        "readiness_contract_sha256"
    ]

    def write_candidate(schema_version: int) -> str:
        value = dict(candidate_value)
        value["runtime_candidate_schema_version"] = schema_version
        raw = canonical_json_bytes(value)
        candidate_path.write_bytes(raw)
        candidate_path.chmod(0o644)
        return hashlib.sha256(raw).hexdigest()

    def write_approval(schema_version: int, candidate_sha256: str) -> None:
        approval_path.write_bytes(
            canonical_json_bytes(
                {
                    "runtime_approval_schema_version": schema_version,
                    "approved_candidate_sha256": candidate_sha256,
                    "approval_scope": "single_symbol_openai_canary_runtime_v2",
                    "symbol": "000403.SZ",
                    "provider": "openai",
                    "maximum_provider_attempts": 1,
                    "retry_count": 0,
                }
            )
        )
        approval_path.chmod(0o600)

    candidate_sha256 = write_candidate(2)
    write_approval(2, candidate_sha256)

    approved = load_installed_runtime_approval(
        candidate_path=candidate_path,
        approval_path=approval_path,
    )

    assert approved.readiness_contract_sha256 == readiness_sha256
    assert approved.approval_candidate_sha256 == candidate_sha256

    legacy_candidate_sha256 = write_candidate(1)
    write_approval(2, legacy_candidate_sha256)
    with pytest.raises(OrchestratorError, match="runtime_candidate_invalid"):
        load_installed_runtime_approval(
            candidate_path=candidate_path,
            approval_path=approval_path,
        )


def test_preserved_45c5_candidate_is_rejected_by_runtime_loader(
    tmp_path: Path,
) -> None:
    candidate_path = (
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / "45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127.json"
    )
    candidate_sha256 = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    assert candidate_sha256 == candidate_path.stem
    approval_directory = tmp_path / "approval"
    approval_directory.mkdir(mode=0o700)
    approval_path = approval_directory / "runtime-contract-approval.json"
    approval_path.write_bytes(
        canonical_json_bytes(
            {
                "runtime_approval_schema_version": 2,
                "approved_candidate_sha256": candidate_sha256,
                "approval_scope": "single_symbol_openai_canary_runtime_v2",
                "symbol": "000403.SZ",
                "provider": "openai",
                "maximum_provider_attempts": 1,
                "retry_count": 0,
            }
        )
    )
    approval_path.chmod(0o600)

    with pytest.raises(OrchestratorError, match="runtime_candidate_invalid"):
        load_installed_runtime_approval(
            candidate_path=candidate_path,
            approval_path=approval_path,
        )


def test_runtime_approval_rejects_untrusted_directory_and_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path = tmp_path / "runtime_contract_candidate.json"
    candidate_raw = canonical_json_bytes(
        {
            "runtime_candidate_schema_version": 2,
            "status": "PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL",
            "artifact_identity": {
                "facts_sha256": "1" * 64,
                "projection_sha256": "2" * 64,
                "proxy_image_id": "sha256:" + "3" * 64,
                "relay_image_id": "sha256:" + "4" * 64,
                "timeout_contract_sha256": "5" * 64,
                "readiness_contract_sha256": "6" * 64,
                "orchestrator_source_sha256": "7" * 64,
            },
        }
    )
    candidate_path.write_bytes(candidate_raw)
    candidate_path.chmod(0o644)
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()

    approval_directory = tmp_path / "approval"
    approval_directory.mkdir(mode=0o700)
    approval_path = approval_directory / "runtime-contract-approval.json"
    approval_path.write_bytes(
        canonical_json_bytes(
            {
                "runtime_approval_schema_version": 2,
                "approved_candidate_sha256": candidate_sha256,
                "approval_scope": "single_symbol_openai_canary_runtime_v2",
                "symbol": "000403.SZ",
                "provider": "openai",
                "maximum_provider_attempts": 1,
                "retry_count": 0,
            }
        )
    )
    approval_path.chmod(0o600)

    approval_directory.chmod(0o755)
    with pytest.raises(OrchestratorError, match="runtime_approval_invalid"):
        load_installed_runtime_approval(
            candidate_path=candidate_path,
            approval_path=approval_path,
        )

    approval_directory.chmod(0o700)
    current_uid = os.geteuid()
    monkeypatch.setattr(orchestrator_module.os, "geteuid", lambda: current_uid + 1)
    with pytest.raises(OrchestratorError, match="runtime_approval_invalid"):
        load_installed_runtime_approval(
            candidate_path=candidate_path,
            approval_path=approval_path,
        )


def test_runtime_approval_rejects_symlinked_parent(tmp_path: Path) -> None:
    candidate_path = tmp_path / "runtime_contract_candidate.json"
    candidate_raw = canonical_json_bytes(
        {
            "runtime_candidate_schema_version": 2,
            "status": "PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL",
            "artifact_identity": {
                "facts_sha256": "1" * 64,
                "projection_sha256": "2" * 64,
                "proxy_image_id": "sha256:" + "3" * 64,
                "relay_image_id": "sha256:" + "4" * 64,
                "timeout_contract_sha256": "5" * 64,
                "readiness_contract_sha256": "6" * 64,
                "orchestrator_source_sha256": "7" * 64,
            },
        }
    )
    candidate_path.write_bytes(candidate_raw)
    candidate_path.chmod(0o644)
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    real_directory = tmp_path / "real-approval"
    real_directory.mkdir(mode=0o700)
    approval_path = real_directory / "runtime-contract-approval.json"
    approval_path.write_bytes(
        canonical_json_bytes(
            {
                "runtime_approval_schema_version": 2,
                "approved_candidate_sha256": candidate_sha256,
                "approval_scope": "single_symbol_openai_canary_runtime_v2",
                "symbol": "000403.SZ",
                "provider": "openai",
                "maximum_provider_attempts": 1,
                "retry_count": 0,
            }
        )
    )
    approval_path.chmod(0o600)
    linked_directory = tmp_path / "linked-approval"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(OrchestratorError, match="runtime_approval_invalid"):
        load_installed_runtime_approval(
            candidate_path=candidate_path,
            approval_path=linked_directory / approval_path.name,
        )


def test_runtime_approval_hash_or_file_shape_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.json"
    approval = tmp_path / "approval.json"
    candidate.write_bytes(
        (
            REPO_ROOT
            / "reports/phase2_provider_canary/runtime_contract_candidate.json"
        ).read_bytes()
    )
    candidate.chmod(0o644)
    approval.write_bytes(
        canonical_json_bytes(
            {
                "runtime_approval_schema_version": 2,
                "approved_candidate_sha256": "0" * 64,
                "approval_scope": "single_symbol_openai_canary_runtime_v2",
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
