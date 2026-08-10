from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from app.services import phase2_canary_orchestrator as orchestrator
from app.services.phase2_ai_worker_protocol import FakeClaimsWorker, build_worker_projection
from app.services.phase2_canary_orchestrator import (
    ApprovedCanaryArtifacts,
    AttemptLedgerStore,
    CanaryOrchestratorConfig,
    CanaryRunIdentity,
    CleanupResult,
    LedgerState,
    MockCanaryBackend,
    OrchestratorError,
    run_single_symbol_canary,
)
from app.services.phase2_canary_runtime_contract import runtime_contract_sha256
from app.services.phase2_claims_renderer import render_claims_document
from app.services.phase2_claims_service import canonical_json_bytes, validate_claims_document
from scripts.validate_phase2_canary_ledger_namespace import (
    HistoricalRequestEvidence,
    LedgerNamespaceEvidence,
    build_ledger_namespace_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OLD_APPROVAL_SHA256 = (
    "e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b"
)
CURRENT_APPROVAL_SHA256 = (
    "769f4d762594ac496bbaf14b90b8dd3cb7eae572ac9bf05f0484abfe3b86ffaf"
)
CURRENT_SCOPE_ID = (
    "829602089ff090ebc205ee43157476523296ce2019dcee9ae857199126ea6523"
)
FIXED_TIME = "2026-08-10T08:00:00Z"
FACTS_PATH = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"


def _scope(candidate_sha256: str = CURRENT_APPROVAL_SHA256):
    scope_type = orchestrator.ApprovalScopeIdentity
    return scope_type(
        ledger_namespace_version=1,
        approval_candidate_sha256=candidate_sha256,
        symbol="000403.SZ",
        provider_id="openai",
        exact_model_id="gpt-5.6-terra",
        endpoint_alias="openai_responses_v1",
    )


def _identity(candidate_sha256: str, request_id: str) -> CanaryRunIdentity:
    return CanaryRunIdentity(
        request_id=request_id,
        symbol="000403.SZ",
        trade_date="2026-07-31",
        provider="openai",
        endpoint_alias="openai_responses_v1",
        facts_sha256="1" * 64,
        projection_sha256="2" * 64,
        approval_candidate_sha256=candidate_sha256,
        proxy_image_id="sha256:" + "3" * 64,
        relay_image_id="sha256:" + "4" * 64,
        timeout_contract_sha256=runtime_contract_sha256(),
        orchestrator_source_sha256="5" * 64,
    )


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)
    return path


def _write_valid_inbox(root: Path, request_id: str) -> tuple[Path, Path]:
    inbox = _private_dir(root / "inbox")
    claims_raw = (
        REPO_ROOT
        / "reports/phase2_claims/fixtures/000403SZ_claims.json"
    ).read_bytes()
    document = orchestrator.ClaimsDocument.model_validate_json(claims_raw)
    validation = validate_claims_document(REPO_ROOT, document)
    rendered = render_claims_document(document, validation)
    json_path = inbox / f"{request_id}.json"
    markdown_path = inbox / f"{request_id}.md"
    json_path.write_bytes(claims_raw)
    markdown_path.write_text(rendered, encoding="utf-8")
    json_path.chmod(0o600)
    markdown_path.chmod(0o600)
    return json_path, markdown_path


def _write_runtime_evidence(
    root: Path,
    *,
    request_id: str,
    candidate_sha256: str,
    state: LedgerState,
    attempts: int,
    scoped: bool = False,
    dispatch_started_at: str | None = None,
) -> None:
    evidence = _private_dir(root / "evidence") / f"{request_id}.json"
    identity = _identity(candidate_sha256, request_id)
    succeeded = state is LedgerState.CLEANUP_COMPLETED
    value = {
        "approval_candidate_sha256": candidate_sha256,
        "attempt_ledger_state": state.value,
        "can_publish": False,
        "candidate_ready_at": FIXED_TIME if succeeded else None,
        "cleanup_completed_at": FIXED_TIME if succeeded else None,
        "container_residue_count": 0,
        "dispatch_started_at": (
            dispatch_started_at or FIXED_TIME if attempts else None
        ),
        "host_validation_completed_at": FIXED_TIME if succeeded else None,
        "manual_review": "PENDING",
        "network_residue_count": 0,
        "orchestrator_source_sha256": identity.orchestrator_source_sha256,
        "provider_attempt_count": attempts,
        "provider_http_status": None,
        "proxy_image_id": identity.proxy_image_id,
        "relay_image_id": identity.relay_image_id,
        "request_id": request_id,
        "response_received_at": FIXED_TIME if succeeded else None,
        "retry_count": 0,
        "route": "inbox" if succeeded else "rejected",
        "runtime_contract_version": 1,
        "runtime_evidence_schema_version": 1,
        "secret_file_residue_count": 0,
        "symbol": "000403.SZ",
        "temporary_file_residue_count": 0,
        "terminal_state": "SUCCEEDED" if succeeded else state.value,
        "timeout_contract_sha256": identity.timeout_contract_sha256,
    }
    if scoped:
        value["approval_scope_id"] = orchestrator.compute_approval_scope_id(
            _scope(candidate_sha256)
        )
        value["ledger_namespace_version"] = 1
        value["receipt_archive_status"] = "NOT_RUN"
        value["last_proven_stage"] = None
        value["child_terminal_reason"] = None
    evidence.write_bytes(
        canonical_json_bytes(value)
    )
    evidence.chmod(0o600)
    if not succeeded:
        rejected = root / "rejected"
        rejected.mkdir(mode=0o700, parents=True, exist_ok=True)
        rejected.chmod(0o700)
        rejection = rejected / f"{request_id}.json"
        rejection.write_bytes(
            canonical_json_bytes(
                {
                    "can_publish": False,
                    "error_category": state.value,
                    "request_id": request_id,
                    "retry_count": 0,
                    "route": "rejected",
                }
            )
        )
        rejection.chmod(0o600)


def _copy_candidate(root: Path, candidate_sha256: str) -> Path:
    candidates = _private_dir(root / "candidates")
    source = (
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{candidate_sha256}.json"
    )
    target = candidates / source.name
    shutil.copy2(source, target)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == candidate_sha256
    return candidates


def _namespace(
    tmp_path: Path,
    *,
    candidate_sha256: str = OLD_APPROVAL_SHA256,
):
    namespace_type = orchestrator.ApprovalScopedLedgerNamespace
    legacy = _private_dir(tmp_path / "legacy")
    attempts = _private_dir(tmp_path / "attempts")
    evidence = _private_dir(tmp_path / "historical-output")
    candidates = _copy_candidate(tmp_path, candidate_sha256)
    return (
        namespace_type(
            repo_root=REPO_ROOT,
            attempts_root=attempts,
            legacy_state_root=legacy,
            historical_evidence_root=evidence,
            candidate_roots=(candidates,),
        ),
        legacy,
        attempts,
        evidence,
    )


def _legacy_terminal(
    legacy: Path,
    evidence: Path,
    *,
    request_id: str,
    candidate_sha256: str = OLD_APPROVAL_SHA256,
    state: LedgerState = LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
) -> Path:
    history_root = _private_dir(legacy / "history")
    request_root = _private_dir(history_root / request_id)
    store = AttemptLedgerStore(request_root, fixture_writes_enabled=True)
    store.prepare(_identity(candidate_sha256, request_id), timestamp=FIXED_TIME)
    store.transition(
        LedgerState.NETWORK_DISPATCH_STARTED,
        timestamp="2026-08-10T08:00:01Z",
    )
    if state is LedgerState.CLEANUP_COMPLETED:
        for next_state in (
            LedgerState.PROVIDER_RESPONSE_RECEIVED,
            LedgerState.CANDIDATE_COLLECTED,
            LedgerState.HOST_VALIDATION_COMPLETED,
            LedgerState.CLEANUP_COMPLETED,
        ):
            store.transition(next_state, timestamp=FIXED_TIME)
    else:
        store.transition(state, timestamp="2026-08-10T08:00:02Z")
    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=candidate_sha256,
        state=state,
        attempts=1,
        dispatch_started_at="2026-08-10T08:00:01Z",
    )
    return store.path


def _scoped_store(attempts: Path, scope, request_id: str):
    store_type = orchestrator.ApprovalScopedAttemptLedgerStore
    return store_type(attempts, scope=scope, request_id=request_id)


def test_scope_material_and_sha256_are_exact_and_deterministic() -> None:
    scope = _scope()
    material = orchestrator.approval_scope_material(scope)

    assert material == (
        b"tickflow-canary-ledger-v1\n|\n"
        + CURRENT_APPROVAL_SHA256.encode("ascii")
        + b"\n|\n000403.SZ\n|\nopenai\n|\ngpt-5.6-terra\n|\n"
        + b"openai_responses_v1"
    )
    assert orchestrator.compute_approval_scope_id(scope) == (
        CURRENT_SCOPE_ID
    )
    assert orchestrator.compute_approval_scope_id(scope) == (
        orchestrator.compute_approval_scope_id(_scope())
    )


def test_scope_identity_has_no_secret_surface() -> None:
    scope = _scope()
    serialized = orchestrator.approval_scope_material(scope).decode()

    assert "secret" not in serialized.lower()
    assert "authorization" not in serialized.lower()
    assert set(scope.model_dump()) == {
        "approval_candidate_sha256",
        "endpoint_alias",
        "exact_model_id",
        "ledger_namespace_version",
        "provider_id",
        "symbol",
    }


def test_scope_identity_rejects_bool_namespace_version() -> None:
    payload = _scope().model_dump(mode="json")
    payload["ledger_namespace_version"] = True

    with pytest.raises(ValueError, match="ledger_namespace_version_invalid"):
        orchestrator.ApprovalScopeIdentity.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ledger_schema_version", True),
        ("provider_attempt_count", True),
        ("retry_count", False),
    ],
)
def test_attempt_ledger_rejects_bool_for_integer_contract_fields(
    field: str,
    value: bool,
) -> None:
    ledger = orchestrator.AttemptLedger(
        identity=_identity(CURRENT_APPROVAL_SHA256, "2" * 32),
        state=LedgerState.NETWORK_DISPATCH_STARTED,
        provider_attempt_count=1,
        prepared_at=FIXED_TIME,
        dispatch_started_at=FIXED_TIME,
    ).model_dump(mode="json")
    ledger[field] = value

    with pytest.raises(OrchestratorError, match="ledger_invalid"):
        orchestrator._decode_ledger(canonical_json_bytes(ledger))


def test_scoped_ledger_rejects_bool_namespace_version() -> None:
    scope = _scope()
    ledger = orchestrator.ApprovalScopedAttemptLedger(
        approval_scope_id=orchestrator.compute_approval_scope_id(scope),
        exact_model_id=scope.exact_model_id,
        attempt=orchestrator.AttemptLedger(
            identity=_identity(CURRENT_APPROVAL_SHA256, "2" * 32),
            state=LedgerState.PREPARED,
            provider_attempt_count=0,
            prepared_at=FIXED_TIME,
        ),
    ).model_dump(mode="json")
    ledger["ledger_namespace_version"] = True

    with pytest.raises(OrchestratorError, match="scoped_ledger_invalid"):
        orchestrator._decode_scoped_ledger(canonical_json_bytes(ledger))


def test_two_approvals_produce_different_scope_ids() -> None:
    compute = orchestrator.compute_approval_scope_id

    assert compute(_scope("8" * 64)) != compute(_scope("9" * 64))


def test_same_approval_identity_produces_the_same_scope_id() -> None:
    compute = orchestrator.compute_approval_scope_id

    assert compute(_scope("8" * 64)) == compute(_scope("8" * 64))


def test_old_consumed_scope_is_preserved_and_new_scope_is_available(
    tmp_path: Path,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    _legacy_terminal(legacy, evidence, request_id=request_id)

    result = namespace.preflight(_scope())

    assert result.status == "READY"
    assert result.current_scope_id == CURRENT_SCOPE_ID
    assert result.current_scope_provider_attempts == 0
    assert result.current_scope_attempt_availability == "AVAILABLE"
    assert result.historical_requests == (request_id,)


def test_historical_terminal_ledger_bytes_remain_unchanged(tmp_path: Path) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    ledger_path = _legacy_terminal(legacy, evidence, request_id="1" * 32)
    before = ledger_path.read_bytes()

    namespace.preflight(_scope())

    assert ledger_path.read_bytes() == before


def test_legacy_history_request_directory_rejects_extra_entries(
    tmp_path: Path,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    ledger_path = _legacy_terminal(
        legacy,
        evidence,
        request_id=request_id,
    )
    extra = ledger_path.parent / "moved-ledger.json"
    extra.write_bytes(ledger_path.read_bytes())
    extra.chmod(0o600)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_request_entries_invalid",)


def test_historical_scoped_terminal_requires_matching_runtime_evidence(
    tmp_path: Path,
) -> None:
    namespace, _legacy, attempts, evidence = _namespace(tmp_path)
    old_scope = _scope(OLD_APPROVAL_SHA256)
    request_id = "2" * 32
    store = _scoped_store(attempts, old_scope, request_id)
    store.prepare(_identity(OLD_APPROVAL_SHA256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    store.transition(LedgerState.ATTEMPT_CONSUMED_UNKNOWN, timestamp=FIXED_TIME)

    missing = namespace.preflight(_scope())

    assert missing.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert missing.errors == ("historical_runtime_evidence_missing",)

    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=OLD_APPROVAL_SHA256,
        state=LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
        attempts=1,
        scoped=True,
    )

    complete = namespace.preflight(_scope())

    assert complete.status == "READY"
    assert complete.historical_requests == (request_id,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unexpected_field", "must-fail-closed"),
        ("terminal_state", "CLEANUP_COMPLETED"),
    ],
)
def test_historical_runtime_evidence_rejects_extra_or_conflicting_fields(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    _legacy_terminal(legacy, evidence, request_id=request_id)
    evidence_path = evidence / "evidence" / f"{request_id}.json"
    payload = json.loads(evidence_path.read_bytes())
    payload[field] = value
    evidence_path.write_bytes(canonical_json_bytes(payload))
    evidence_path.chmod(0o600)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_runtime_evidence_invalid",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime_evidence_schema_version", True),
        ("runtime_contract_version", True),
        ("provider_attempt_count", True),
        ("retry_count", False),
        ("container_residue_count", False),
    ],
)
def test_historical_runtime_evidence_rejects_bool_for_integer_fields(
    tmp_path: Path,
    field: str,
    value: bool,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    _legacy_terminal(legacy, evidence, request_id=request_id)
    evidence_path = evidence / "evidence" / f"{request_id}.json"
    payload = json.loads(evidence_path.read_bytes())
    payload[field] = value
    evidence_path.write_bytes(canonical_json_bytes(payload))
    evidence_path.chmod(0o600)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_runtime_evidence_invalid",)


def test_completed_old_scope_without_receipts_blocks_new_scope(
    tmp_path: Path,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    _legacy_terminal(
        legacy,
        evidence,
        request_id=request_id,
        state=LedgerState.CLEANUP_COMPLETED,
    )
    claims_raw = _write_valid_inbox(evidence, request_id)[0].read_bytes()
    assert claims_raw == canonical_json_bytes(json.loads(claims_raw))

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_receipt_missing",)


def test_failed_historical_ledger_cannot_carry_success_inbox(tmp_path: Path) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    _legacy_terminal(legacy, evidence, request_id=request_id)
    _write_valid_inbox(evidence, request_id)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_route_artifact_conflict",)


def test_completed_historical_ledger_requires_success_inbox(tmp_path: Path) -> None:
    namespace, config, request_id = _completed_runtime_history(tmp_path)
    (config.canary_output_root / "inbox" / f"{request_id}.json").unlink()
    (config.canary_output_root / "inbox" / f"{request_id}.md").unlink()

    result = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_route_artifact_conflict",)


def test_completed_historical_inbox_rejects_tampered_markdown(
    tmp_path: Path,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    _legacy_terminal(
        legacy,
        evidence,
        request_id=request_id,
        state=LedgerState.CLEANUP_COMPLETED,
    )
    _json_path, markdown_path = _write_valid_inbox(evidence, request_id)
    markdown_path.write_text("# tampered but nonempty\n", encoding="utf-8")
    markdown_path.chmod(0o600)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_artifact_invalid",)


@pytest.mark.parametrize("entry_name", ["unexpected.json", "nested"])
def test_historical_receipt_directory_rejects_unexpected_entries(
    tmp_path: Path,
    entry_name: str,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    _legacy_terminal(legacy, evidence, request_id=request_id)
    receipt_root = _private_dir(evidence / "receipts")
    request_root = _private_dir(receipt_root / request_id)
    entry = request_root / entry_name
    if entry_name == "nested":
        _private_dir(entry)
    else:
        entry.write_bytes(canonical_json_bytes({"request_id": request_id}))
        entry.chmod(0o600)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_receipt_entry_invalid",)


def test_historical_receipt_request_id_must_match_directory(tmp_path: Path) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    _legacy_terminal(legacy, evidence, request_id=request_id)
    receipt_root = _private_dir(evidence / "receipts")
    request_root = _private_dir(receipt_root / request_id)
    receipt = request_root / "relay-receipt.json"
    receipt.write_bytes(canonical_json_bytes({"request_id": "2" * 32}))
    receipt.chmod(0o600)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_receipt_invalid",)


def test_scoped_runtime_evidence_rejects_scope_identity_mismatch(
    tmp_path: Path,
) -> None:
    namespace, _legacy, attempts, evidence = _namespace(tmp_path)
    old_scope = _scope(OLD_APPROVAL_SHA256)
    request_id = "2" * 32
    store = _scoped_store(attempts, old_scope, request_id)
    store.prepare(_identity(OLD_APPROVAL_SHA256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    store.transition(LedgerState.ATTEMPT_CONSUMED_UNKNOWN, timestamp=FIXED_TIME)
    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=OLD_APPROVAL_SHA256,
        state=LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
        attempts=1,
        scoped=True,
    )
    evidence_path = evidence / "evidence" / f"{request_id}.json"
    payload = json.loads(evidence_path.read_bytes())
    payload["approval_scope_id"] = "f" * 64
    evidence_path.write_bytes(canonical_json_bytes(payload))
    evidence_path.chmod(0o600)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_runtime_evidence_invalid",)


def test_scoped_runtime_evidence_rejects_bool_namespace_version(
    tmp_path: Path,
) -> None:
    namespace, _legacy, attempts, evidence = _namespace(tmp_path)
    old_scope = _scope(OLD_APPROVAL_SHA256)
    request_id = "2" * 32
    store = _scoped_store(attempts, old_scope, request_id)
    store.prepare(_identity(OLD_APPROVAL_SHA256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    store.transition(LedgerState.ATTEMPT_CONSUMED_UNKNOWN, timestamp=FIXED_TIME)
    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=OLD_APPROVAL_SHA256,
        state=LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
        attempts=1,
        scoped=True,
    )
    evidence_path = evidence / "evidence" / f"{request_id}.json"
    payload = json.loads(evidence_path.read_bytes())
    payload["ledger_namespace_version"] = True
    evidence_path.write_bytes(canonical_json_bytes(payload))
    evidence_path.chmod(0o600)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_runtime_evidence_invalid",)


def test_consumed_current_scope_is_blocked(tmp_path: Path) -> None:
    namespace, _legacy, attempts, _evidence = _namespace(tmp_path)
    scope = _scope()
    store = _scoped_store(attempts, scope, "2" * 32)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, "2" * 32), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    store.transition(LedgerState.FAILED_AFTER_DISPATCH, timestamp=FIXED_TIME)

    result = namespace.preflight(scope)

    assert result.status == "CURRENT_SCOPE_ATTEMPT_CONSUMED"
    assert result.current_scope_provider_attempts == 1
    assert result.current_scope_attempt_availability == "BLOCKED"


def test_unknown_current_scope_attempt_is_blocked(tmp_path: Path) -> None:
    namespace, _legacy, attempts, _evidence = _namespace(tmp_path)
    scope = _scope()
    store = _scoped_store(attempts, scope, "2" * 32)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, "2" * 32), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    store.transition(LedgerState.ATTEMPT_CONSUMED_UNKNOWN, timestamp=FIXED_TIME)

    result = namespace.preflight(scope)

    assert result.status == "CURRENT_SCOPE_ATTEMPT_CONSUMED"
    assert result.current_scope_attempt_availability == "BLOCKED"


def test_failed_before_dispatch_current_scope_remains_available(tmp_path: Path) -> None:
    namespace, _legacy, attempts, _evidence = _namespace(tmp_path)
    scope = _scope()
    store = _scoped_store(attempts, scope, "2" * 32)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, "2" * 32), timestamp=FIXED_TIME)
    store.transition(LedgerState.FAILED_BEFORE_DISPATCH, timestamp=FIXED_TIME)

    result = namespace.preflight(scope)

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_runtime_evidence_missing",)


def test_current_scope_failed_before_with_matching_evidence_is_available(
    tmp_path: Path,
) -> None:
    namespace, _legacy, attempts, evidence = _namespace(tmp_path)
    scope = _scope()
    request_id = "2" * 32
    store = _scoped_store(attempts, scope, request_id)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.FAILED_BEFORE_DISPATCH, timestamp=FIXED_TIME)
    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=CURRENT_APPROVAL_SHA256,
        state=LedgerState.FAILED_BEFORE_DISPATCH,
        attempts=0,
        scoped=True,
    )

    result = namespace.preflight(scope)

    assert result.status == "READY"
    assert result.current_scope_provider_attempts == 0
    assert result.current_scope_attempt_availability == "AVAILABLE"


def test_forged_failed_before_with_dispatch_timestamp_blocks_globally(
    tmp_path: Path,
) -> None:
    namespace, _legacy, attempts, _evidence = _namespace(tmp_path)
    scope = _scope()
    request_id = "2" * 32
    store = _scoped_store(attempts, scope, request_id)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    raw = json.loads(store.path.read_bytes())
    raw["attempt"]["state"] = "FAILED_BEFORE_DISPATCH"
    raw["attempt"]["provider_attempt_count"] = 0
    raw["attempt"]["failed_at"] = FIXED_TIME
    store.path.write_bytes(canonical_json_bytes(raw))
    store.path.chmod(0o600)

    result = namespace.preflight(scope)

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("scoped_ledger_invalid",)


def test_current_scope_ledger_recomputes_its_embedded_scope_identity(
    tmp_path: Path,
) -> None:
    namespace, _legacy, attempts, _evidence = _namespace(tmp_path)
    scope = _scope()
    request_id = "2" * 32
    store = _scoped_store(attempts, scope, request_id)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, request_id), timestamp=FIXED_TIME)
    raw = json.loads(store.path.read_bytes())
    raw["attempt"]["identity"]["approval_candidate_sha256"] = (
        OLD_APPROVAL_SHA256
    )
    store.path.write_bytes(canonical_json_bytes(raw))
    store.path.chmod(0o600)

    result = namespace.preflight(scope)

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("scoped_ledger_invalid",)


def test_unknown_historical_nonterminal_ledger_blocks_globally(
    tmp_path: Path,
) -> None:
    namespace, legacy, _attempts, _evidence = _namespace(tmp_path)
    history_root = _private_dir(legacy / "history")
    request_root = _private_dir(history_root / ("1" * 32))
    AttemptLedgerStore(request_root, fixture_writes_enabled=True).prepare(
        _identity(OLD_APPROVAL_SHA256, "1" * 32),
        timestamp=FIXED_TIME,
    )

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_ledger_nonterminal",)


def test_legacy_state_root_rejects_unknown_ledger_aliases(
    tmp_path: Path,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    ledger = _legacy_terminal(
        legacy,
        evidence,
        request_id="1" * 32,
    )
    alias = legacy / "attempt-ledger.backup.json"
    alias.write_bytes(ledger.read_bytes())
    alias.chmod(0o600)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_state_entries_invalid",)


def test_unresolvable_historical_approval_identity_blocks_globally(
    tmp_path: Path,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    _legacy_terminal(
        legacy,
        evidence,
        request_id="1" * 32,
        candidate_sha256="f" * 64,
    )

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_approval_identity_unresolved",)


def test_historical_candidate_rejects_duplicate_identity_keys(
    tmp_path: Path,
) -> None:
    namespace, _legacy, attempts, evidence = _namespace(tmp_path)
    candidate_root = namespace.candidate_roots[0]
    source = (
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{CURRENT_APPROVAL_SHA256}.json"
    ).read_bytes()
    malformed = b'{"symbol":"000403.SZ",' + source[1:]
    malformed_sha256 = hashlib.sha256(malformed).hexdigest()
    candidate_path = candidate_root / f"{malformed_sha256}.json"
    candidate_path.write_bytes(malformed)
    candidate_path.chmod(0o600)
    old_scope = _scope(malformed_sha256)
    request_id = "2" * 32
    store = _scoped_store(attempts, old_scope, request_id)
    store.prepare(_identity(malformed_sha256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    store.transition(LedgerState.ATTEMPT_CONSUMED_UNKNOWN, timestamp=FIXED_TIME)
    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=malformed_sha256,
        state=LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
        attempts=1,
        scoped=True,
    )

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_candidate_invalid",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime_candidate_schema_version", 99),
        ("old_approval_candidate_sha256", True),
    ],
)
def test_historical_candidate_rejects_invalid_versioned_schema(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    namespace, _legacy, attempts, evidence = _namespace(tmp_path)
    candidate_root = namespace.candidate_roots[0]
    source = (
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{CURRENT_APPROVAL_SHA256}.json"
    ).read_bytes()
    payload = json.loads(source)
    payload[field] = value
    malformed = canonical_json_bytes(payload)
    malformed_sha256 = hashlib.sha256(malformed).hexdigest()
    candidate_path = candidate_root / f"{malformed_sha256}.json"
    candidate_path.write_bytes(malformed)
    candidate_path.chmod(0o600)
    old_scope = _scope(malformed_sha256)
    request_id = "2" * 32
    store = _scoped_store(attempts, old_scope, request_id)
    store.prepare(_identity(malformed_sha256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    store.transition(LedgerState.ATTEMPT_CONSUMED_UNKNOWN, timestamp=FIXED_TIME)
    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=malformed_sha256,
        state=LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
        attempts=1,
        scoped=True,
    )

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_candidate_invalid",)


@pytest.mark.parametrize("mutation", ["inputs_missing", "source_binding"])
def test_historical_candidate_rejects_nested_schema_or_binding_corruption(
    tmp_path: Path,
    mutation: str,
) -> None:
    namespace, _legacy, attempts, evidence = _namespace(tmp_path)
    candidate_root = namespace.candidate_roots[0]
    source = (
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{CURRENT_APPROVAL_SHA256}.json"
    ).read_bytes()
    payload = json.loads(source)
    if mutation == "inputs_missing":
        payload["inputs"] = {}
    else:
        payload["proxy_contents"]["source_sha256"] = "f" * 64
    malformed = canonical_json_bytes(payload)
    malformed_sha256 = hashlib.sha256(malformed).hexdigest()
    candidate_path = candidate_root / f"{malformed_sha256}.json"
    candidate_path.write_bytes(malformed)
    candidate_path.chmod(0o600)
    old_scope = _scope(malformed_sha256)
    request_id = "2" * 32
    store = _scoped_store(attempts, old_scope, request_id)
    store.prepare(_identity(malformed_sha256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    store.transition(LedgerState.ATTEMPT_CONSUMED_UNKNOWN, timestamp=FIXED_TIME)
    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=malformed_sha256,
        state=LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
        attempts=1,
        scoped=True,
    )

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_candidate_invalid",)


def test_global_request_id_collision_with_legacy_history_is_blocked(
    tmp_path: Path,
) -> None:
    namespace, legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "1" * 32
    _legacy_terminal(legacy, evidence, request_id=request_id)
    preflight = namespace.preflight(_scope())

    with pytest.raises(OrchestratorError, match="REQUEST_ID_COLLISION_BLOCKED"):
        namespace.assert_request_id_available(request_id, preflight=preflight)


def test_orphan_historical_runtime_evidence_blocks_globally(tmp_path: Path) -> None:
    namespace, _legacy, _attempts, evidence = _namespace(tmp_path)
    _write_runtime_evidence(
        evidence,
        request_id="1" * 32,
        candidate_sha256=OLD_APPROVAL_SHA256,
        state=LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
        attempts=1,
    )

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("historical_runtime_evidence_orphan",)


def test_duplicate_request_id_across_scoped_namespaces_blocks_globally(
    tmp_path: Path,
) -> None:
    namespace, _legacy, attempts, _evidence = _namespace(tmp_path)
    request_id = "2" * 32
    first_scope = _scope("8" * 64)
    second_scope = _scope("9" * 64)
    first = _scoped_store(attempts, first_scope, request_id)
    second = _scoped_store(attempts, second_scope, request_id)
    first.prepare(_identity("8" * 64, request_id), timestamp=FIXED_TIME)
    second.prepare(_identity("9" * 64, request_id), timestamp=FIXED_TIME)

    result = namespace.preflight(_scope())

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("global_request_id_collision",)


def test_new_scoped_request_does_not_overwrite_historical_evidence(
    tmp_path: Path,
) -> None:
    _namespace_instance, legacy, attempts, evidence = _namespace(tmp_path)
    historical = _legacy_terminal(legacy, evidence, request_id="1" * 32)
    before = hashlib.sha256(historical.read_bytes()).hexdigest()
    scope = _scope()
    store = _scoped_store(attempts, scope, "2" * 32)

    store.prepare(_identity(CURRENT_APPROVAL_SHA256, "2" * 32), timestamp=FIXED_TIME)

    assert hashlib.sha256(historical.read_bytes()).hexdigest() == before
    assert store.path == attempts / CURRENT_SCOPE_ID / ("2" * 32) / "ledger.json"
    assert store.path.is_file()


def test_scoped_prepare_uses_atomic_no_clobber(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = _private_dir(tmp_path / "attempts")
    scope = _scope()
    request_id = "2" * 32
    calls: list[Path] = []

    def reject(path: Path, _raw: bytes) -> None:
        calls.append(path)
        raise OrchestratorError("scoped_ledger_already_exists")

    monkeypatch.setattr(orchestrator, "_atomic_create_no_clobber", reject)
    store = _scoped_store(attempts, scope, request_id)

    with pytest.raises(OrchestratorError, match="scoped_ledger_already_exists"):
        store.prepare(
            _identity(CURRENT_APPROVAL_SHA256, request_id),
            timestamp=FIXED_TIME,
        )

    assert calls == [store.path]


def test_atomic_replace_rejects_changed_expected_bytes_without_overwrite(
    tmp_path: Path,
) -> None:
    parent = _private_dir(tmp_path / "request")
    path = parent / "ledger.json"
    original = b'{"state":"original"}\n'
    changed = b'{"state":"changed"}\n'
    path.write_bytes(changed)
    path.chmod(0o600)

    with pytest.raises(OrchestratorError, match="scoped_ledger_changed"):
        orchestrator._atomic_replace_expected(
            path,
            b'{"state":"replacement"}\n',
            expected_raw=original,
        )

    assert path.read_bytes() == changed


def test_atomic_replace_does_not_overwrite_racing_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _private_dir(tmp_path / "request")
    path = parent / "ledger.json"
    original = b'{"state":"original"}\n'
    racing = b'{"state":"racing"}\n'
    path.write_bytes(original)
    path.chmod(0o600)
    real_rename = os.rename
    injected = False

    def inject_race(
        source: str,
        target: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal injected
        if not injected and source == path.name:
            injected = True
            competitor = parent / "competitor.json"
            competitor.write_bytes(racing)
            competitor.chmod(0o600)
            os.replace(competitor, path)
        real_rename(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(orchestrator.os, "rename", inject_race)

    with pytest.raises(OrchestratorError, match="scoped_ledger_changed"):
        orchestrator._atomic_replace_expected(
            path,
            b'{"state":"replacement"}\n',
            expected_raw=original,
        )

    assert path.read_bytes() == racing


def test_namespace_directory_creation_fsyncs_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _private_dir(tmp_path / "attempts")
    observed: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        observed.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(orchestrator.os, "fsync", recording_fsync)

    child = orchestrator._private_namespace_child(parent, "a" * 64)

    assert child.is_dir()
    assert observed


def test_scoped_request_directory_rejects_unexpected_entries(tmp_path: Path) -> None:
    namespace, _legacy, attempts, _evidence = _namespace(tmp_path)
    scope = _scope()
    request_id = "2" * 32
    store = _scoped_store(attempts, scope, request_id)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, request_id), timestamp=FIXED_TIME)
    extra = store.root / ".ledger.partial"
    extra.write_text("stale", encoding="utf-8")
    extra.chmod(0o600)

    result = namespace.preflight(scope)

    assert result.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert result.errors == ("scoped_request_entries_invalid",)


def test_current_scope_dispatch_then_repeat_preflight_is_blocked(
    tmp_path: Path,
) -> None:
    namespace, _legacy, attempts, _evidence = _namespace(tmp_path)
    scope = _scope()
    store = _scoped_store(attempts, scope, "2" * 32)

    assert namespace.preflight(scope).current_scope_attempt_availability == "AVAILABLE"

    store.prepare(_identity(CURRENT_APPROVAL_SHA256, "2" * 32), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)

    assert namespace.preflight(scope).current_scope_attempt_availability == "BLOCKED"


def test_scoped_ledger_rejects_symlinked_scope_directory(tmp_path: Path) -> None:
    attempts = _private_dir(tmp_path / "attempts")
    outside = _private_dir(tmp_path / "outside")
    scope = _scope()
    scope_id = orchestrator.compute_approval_scope_id(scope)
    (attempts / scope_id).symlink_to(outside, target_is_directory=True)

    store = _scoped_store(attempts, scope, "2" * 32)

    with pytest.raises(OrchestratorError, match="ledger_namespace_symlink_forbidden"):
        store.prepare(
            _identity(CURRENT_APPROVAL_SHA256, "2" * 32),
            timestamp=FIXED_TIME,
        )


def test_existing_global_lock_still_rejects_a_different_approval_scope(
    tmp_path: Path,
) -> None:
    state = _private_dir(tmp_path / "state")
    first = orchestrator.ExclusiveCanaryLock(
        state,
        approval_candidate_sha256="8" * 64,
        approval_scope_id=orchestrator.compute_approval_scope_id(
            _scope("8" * 64)
        ),
        pid=101,
        started_at=FIXED_TIME,
    )
    second = orchestrator.ExclusiveCanaryLock(
        state,
        approval_candidate_sha256="9" * 64,
        approval_scope_id=orchestrator.compute_approval_scope_id(
            _scope("9" * 64)
        ),
        pid=202,
        started_at=FIXED_TIME,
    )

    first.acquire()
    try:
        with pytest.raises(OrchestratorError, match="canary_lock_held"):
            second.acquire()
    finally:
        first.release()


def _write_stale_scoped_lock(
    state: Path,
    *,
    candidate_sha256: str,
    scope_id: str,
    request_id: str | None = None,
) -> None:
    path = state / ".runtime.lock"
    payload = {
        "approval_candidate_sha256": candidate_sha256,
        "approval_scope_id": scope_id,
        "endpoint_alias": "openai_responses_v1",
        "lock_schema_version": 3 if request_id else 2,
        "pid": 999,
        "provider": "openai",
        "started_at": FIXED_TIME,
        "symbol": "000403.SZ",
    }
    if request_id:
        payload["request_id"] = request_id
    path.write_bytes(
        canonical_json_bytes(payload)
    )
    path.chmod(0o600)


def test_stale_same_scope_lock_recovers_only_from_proven_pre_dispatch_ledger(
    tmp_path: Path,
) -> None:
    namespace, state, attempts, evidence = _namespace(tmp_path)
    scope = _scope()
    scope_id = orchestrator.compute_approval_scope_id(scope)
    request_id = "2" * 32
    store = _scoped_store(attempts, scope, request_id)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.FAILED_BEFORE_DISPATCH, timestamp=FIXED_TIME)
    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=CURRENT_APPROVAL_SHA256,
        state=LedgerState.FAILED_BEFORE_DISPATCH,
        attempts=0,
        scoped=True,
    )
    _write_stale_scoped_lock(
        state,
        candidate_sha256=CURRENT_APPROVAL_SHA256,
        scope_id=scope_id,
        request_id=request_id,
    )
    lock = orchestrator.ExclusiveCanaryLock(
        state,
        attempts_root=attempts,
        stale_lock_validator=namespace.prove_failed_before_dispatch,
        approval_candidate_sha256=CURRENT_APPROVAL_SHA256,
        approval_scope_id=scope_id,
        pid=101,
        started_at=FIXED_TIME,
    )

    lock.acquire()
    lock.release()

    assert (state / ".runtime.lock").read_bytes() == b""


def test_stale_scoped_lock_with_dispatch_evidence_fails_closed(tmp_path: Path) -> None:
    namespace, state, attempts, _evidence = _namespace(tmp_path)
    scope = _scope()
    scope_id = orchestrator.compute_approval_scope_id(scope)
    store = _scoped_store(attempts, scope, "2" * 32)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, "2" * 32), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    _write_stale_scoped_lock(
        state,
        candidate_sha256=CURRENT_APPROVAL_SHA256,
        scope_id=scope_id,
        request_id="2" * 32,
    )
    lock = orchestrator.ExclusiveCanaryLock(
        state,
        attempts_root=attempts,
        stale_lock_validator=namespace.prove_failed_before_dispatch,
        approval_candidate_sha256=CURRENT_APPROVAL_SHA256,
        approval_scope_id=scope_id,
        pid=101,
        started_at=FIXED_TIME,
    )

    with pytest.raises(OrchestratorError, match="stale_lock_state_unknown"):
        lock.acquire()


def test_unbound_v2_stale_lock_never_borrows_an_old_failed_before_ledger(
    tmp_path: Path,
) -> None:
    namespace, state, attempts, evidence = _namespace(tmp_path)
    scope = _scope()
    scope_id = orchestrator.compute_approval_scope_id(scope)
    request_id = "2" * 32
    store = _scoped_store(attempts, scope, request_id)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, request_id), timestamp=FIXED_TIME)
    store.transition(LedgerState.FAILED_BEFORE_DISPATCH, timestamp=FIXED_TIME)
    _write_runtime_evidence(
        evidence,
        request_id=request_id,
        candidate_sha256=CURRENT_APPROVAL_SHA256,
        state=LedgerState.FAILED_BEFORE_DISPATCH,
        attempts=0,
        scoped=True,
    )
    _write_stale_scoped_lock(
        state,
        candidate_sha256=CURRENT_APPROVAL_SHA256,
        scope_id=scope_id,
    )
    lock = orchestrator.ExclusiveCanaryLock(
        state,
        attempts_root=attempts,
        stale_lock_validator=namespace.prove_failed_before_dispatch,
        approval_candidate_sha256=CURRENT_APPROVAL_SHA256,
        approval_scope_id=scope_id,
        pid=101,
        started_at=FIXED_TIME,
    )

    with pytest.raises(OrchestratorError, match="stale_lock_state_unknown"):
        lock.acquire()


def test_active_lock_binds_request_id_after_scoped_ledger_prepare(
    tmp_path: Path,
) -> None:
    namespace, state, attempts, _evidence = _namespace(tmp_path)
    scope = _scope()
    scope_id = orchestrator.compute_approval_scope_id(scope)
    request_id = "2" * 32
    lock = orchestrator.ExclusiveCanaryLock(
        state,
        attempts_root=attempts,
        stale_lock_validator=namespace.prove_failed_before_dispatch,
        approval_candidate_sha256=CURRENT_APPROVAL_SHA256,
        approval_scope_id=scope_id,
        pid=101,
        started_at=FIXED_TIME,
    )

    lock.acquire()
    try:
        _scoped_store(attempts, scope, request_id).prepare(
            _identity(CURRENT_APPROVAL_SHA256, request_id),
            timestamp=FIXED_TIME,
        )
        lock.bind_request_id(request_id)
        payload = json.loads((state / ".runtime.lock").read_bytes())
        assert payload["lock_schema_version"] == 3
        assert payload["request_id"] == request_id
    finally:
        lock.release()


def _projection() -> dict:
    raw = FACTS_PATH.read_bytes()
    return build_worker_projection(
        json.loads(raw),
        hashlib.sha256(raw).hexdigest(),
        facts_bytes=raw,
    )


def _artifacts(projection: dict) -> ApprovedCanaryArtifacts:
    return ApprovedCanaryArtifacts(
        approval_candidate_sha256=CURRENT_APPROVAL_SHA256,
        facts_sha256=projection["facts_sha256"],
        projection_sha256=projection["projection_sha256"],
        proxy_image_id="sha256:" + "3" * 64,
        relay_image_id="sha256:" + "4" * 64,
        timeout_contract_sha256=runtime_contract_sha256(),
        readiness_contract_sha256="5" * 64,
        orchestrator_source_sha256="6" * 64,
    )


def _runtime_config(tmp_path: Path) -> tuple[CanaryOrchestratorConfig, Path, Path, Path]:
    state = _private_dir(tmp_path / "state")
    attempts = _private_dir(tmp_path / "attempts")
    work = _private_dir(tmp_path / "work")
    output = _private_dir(tmp_path / "output")
    historical_output = _private_dir(tmp_path / "historical-output")
    candidates = _private_dir(tmp_path / "candidates")
    config = CanaryOrchestratorConfig(
        repo_root=REPO_ROOT,
        state_root=state,
        attempts_root=attempts,
        work_root=work,
        canary_output_root=output,
        historical_evidence_root=historical_output,
        candidate_roots=(candidates,),
        facts_path=FACTS_PATH,
    )
    return config, attempts, historical_output, candidates


def _relay_response(projection: dict, request_id: str) -> dict:
    return {
        "protocol_version": 1,
        "request_id": request_id,
        "symbol": "000403.SZ",
        "projection_sha256": projection["projection_sha256"],
        "claims_candidate": json.loads(FakeClaimsWorker().run(projection)),
    }


def _run(
    *,
    config: CanaryOrchestratorConfig,
    backend: MockCanaryBackend,
    projection: dict,
    request_id: str,
    secret_reads: list[str],
):
    return run_single_symbol_canary(
        config=config,
        artifacts=_artifacts(projection),
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _approved: None,
        secret_reader=lambda: secret_reads.append("read") or "mock-only-secret",
        request_id_factory=lambda: request_id,
        wall_clock=lambda: FIXED_TIME,
        monotonic=backend.monotonic,
    )


def _completed_runtime_history(
    tmp_path: Path,
) -> tuple[
    orchestrator.ApprovalScopedLedgerNamespace,
    CanaryOrchestratorConfig,
    str,
]:
    config, attempts, _historical_output, candidates = _runtime_config(tmp_path)
    shutil.copy2(
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{CURRENT_APPROVAL_SHA256}.json",
        candidates / f"{CURRENT_APPROVAL_SHA256}.json",
    )
    projection = _projection()
    request_id = "2" * 32
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, request_id),
    )
    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id=request_id,
        secret_reads=[],
    )
    assert result.terminal_state == "SUCCEEDED"
    namespace = orchestrator.ApprovalScopedLedgerNamespace(
        repo_root=REPO_ROOT,
        attempts_root=attempts,
        legacy_state_root=config.state_root,
        historical_evidence_root=config.canary_output_root,
        candidate_roots=(candidates,),
    )
    return namespace, config, request_id


def test_one_shot_uses_scoped_ledger_when_old_scope_is_consumed(
    tmp_path: Path,
) -> None:
    config, attempts, historical_output, candidates = _runtime_config(tmp_path)
    shutil.copy2(
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{OLD_APPROVAL_SHA256}.json",
        candidates / f"{OLD_APPROVAL_SHA256}.json",
    )
    _legacy_terminal(
        config.state_root,
        historical_output,
        request_id="1" * 32,
    )
    projection = _projection()
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, "2" * 32),
    )
    secret_reads: list[str] = []

    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id="2" * 32,
        secret_reads=secret_reads,
    )

    assert result.terminal_state == "SUCCEEDED"
    assert result.approval_scope_id == CURRENT_SCOPE_ID
    assert secret_reads == ["read"]
    ledger_path = attempts / CURRENT_SCOPE_ID / ("2" * 32) / "ledger.json"
    assert ledger_path.is_file()
    scoped = orchestrator.ApprovalScopedAttemptLedger.model_validate_json(
        ledger_path.read_bytes()
    )
    assert scoped.state is LedgerState.CLEANUP_COMPLETED
    assert (
        config.state_root
        / "history"
        / ("1" * 32)
        / "attempt-ledger.json"
    ).is_file()


def test_completed_scoped_run_with_production_receipts_is_valid_history(
    tmp_path: Path,
) -> None:
    config, attempts, _historical_output, candidates = _runtime_config(tmp_path)
    shutil.copy2(
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{CURRENT_APPROVAL_SHA256}.json",
        candidates / f"{CURRENT_APPROVAL_SHA256}.json",
    )
    projection = _projection()
    request_id = "2" * 32
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, request_id),
    )

    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id=request_id,
        secret_reads=[],
    )

    assert result.terminal_state == "SUCCEEDED"
    namespace = orchestrator.ApprovalScopedLedgerNamespace(
        repo_root=REPO_ROOT,
        attempts_root=attempts,
        legacy_state_root=config.state_root,
        historical_evidence_root=config.canary_output_root,
        candidate_roots=(candidates,),
    )

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "READY", preflight.errors
    assert preflight.historical_requests == (request_id,)


def test_completed_scoped_history_requires_declared_receipt_archive(
    tmp_path: Path,
) -> None:
    config, attempts, _historical_output, candidates = _runtime_config(tmp_path)
    shutil.copy2(
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{CURRENT_APPROVAL_SHA256}.json",
        candidates / f"{CURRENT_APPROVAL_SHA256}.json",
    )
    projection = _projection()
    request_id = "2" * 32
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, request_id),
    )
    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id=request_id,
        secret_reads=[],
    )
    assert result.receipt_archive_status == "ARCHIVED"
    shutil.rmtree(config.canary_output_root / "receipts" / request_id)
    namespace = orchestrator.ApprovalScopedLedgerNamespace(
        repo_root=REPO_ROOT,
        attempts_root=attempts,
        legacy_state_root=config.state_root,
        historical_evidence_root=config.canary_output_root,
        candidate_roots=(candidates,),
    )

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_missing",)


def test_completed_scoped_history_cannot_downgrade_receipts_to_not_run(
    tmp_path: Path,
) -> None:
    namespace, config, request_id = _completed_runtime_history(tmp_path)
    shutil.rmtree(config.canary_output_root / "receipts" / request_id)
    evidence_path = config.canary_output_root / "evidence" / f"{request_id}.json"
    evidence = json.loads(evidence_path.read_bytes())
    evidence.update(
        {
            "receipt_archive_status": "NOT_RUN",
            "last_proven_stage": None,
            "child_terminal_reason": None,
        }
    )
    evidence_path.write_bytes(canonical_json_bytes(evidence))
    evidence_path.chmod(0o600)

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_missing",)


def test_completed_scoped_history_binds_proxy_attempt_count_to_ledger(
    tmp_path: Path,
) -> None:
    namespace, config, request_id = _completed_runtime_history(tmp_path)
    proxy_receipt = (
        config.canary_output_root
        / "receipts"
        / request_id
        / "proxy-receipt.json"
    )
    payload = json.loads(proxy_receipt.read_bytes())
    payload["provider_attempt_count"] = 0
    proxy_receipt.write_bytes(canonical_json_bytes(payload))
    proxy_receipt.chmod(0o600)

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_conflict",)


def test_completed_scoped_history_binds_relay_receipt_to_child_metadata(
    tmp_path: Path,
) -> None:
    namespace, config, request_id = _completed_runtime_history(tmp_path)
    child_path = (
        config.canary_output_root
        / "receipts"
        / request_id
        / "child-metadata.json"
    )
    payload = json.loads(child_path.read_bytes())
    payload["relay"].update(
        {
            "child_state": "CHILD_NONZERO_EXIT",
            "exit_code": 2,
            "terminal_reason": "RELAY_NONZERO_EXIT",
        }
    )
    child_path.write_bytes(canonical_json_bytes(payload))
    child_path.chmod(0o600)

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_conflict",)


def test_completed_scoped_history_recomputes_last_proven_stage(
    tmp_path: Path,
) -> None:
    namespace, config, request_id = _completed_runtime_history(tmp_path)
    archive_path = (
        config.canary_output_root
        / "receipts"
        / request_id
        / "archive-status.json"
    )
    evidence_path = config.canary_output_root / "evidence" / f"{request_id}.json"
    archive = json.loads(archive_path.read_bytes())
    evidence = json.loads(evidence_path.read_bytes())
    archive["last_proven_stage"] = "proxy_ready"
    evidence["last_proven_stage"] = "proxy_ready"
    archive_path.write_bytes(canonical_json_bytes(archive))
    archive_path.chmod(0o600)
    evidence_path.write_bytes(canonical_json_bytes(evidence))
    evidence_path.chmod(0o600)

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_conflict",)


def test_completed_scoped_history_rejects_non_prefix_receipt_events(
    tmp_path: Path,
) -> None:
    namespace, config, request_id = _completed_runtime_history(tmp_path)
    proxy_path = (
        config.canary_output_root
        / "receipts"
        / request_id
        / "proxy-receipt.json"
    )
    proxy = json.loads(proxy_path.read_bytes())
    proxy["request_write_started"] = orchestrator._mock_stage_event(
        "request_write_started",
        occurred=False,
        index=7,
    )
    proxy_path.write_bytes(canonical_json_bytes(proxy))
    proxy_path.chmod(0o600)

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_invalid",)


def test_completed_scoped_history_binds_provider_http_status_to_receipt(
    tmp_path: Path,
) -> None:
    namespace, config, request_id = _completed_runtime_history(tmp_path)
    evidence_path = config.canary_output_root / "evidence" / f"{request_id}.json"
    evidence = json.loads(evidence_path.read_bytes())
    evidence["provider_http_status"] = 503
    evidence_path.write_bytes(canonical_json_bytes(evidence))
    evidence_path.chmod(0o600)

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_conflict",)


def test_completed_scoped_history_rejects_malformed_stage_receipt(
    tmp_path: Path,
) -> None:
    config, attempts, _historical_output, candidates = _runtime_config(tmp_path)
    shutil.copy2(
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{CURRENT_APPROVAL_SHA256}.json",
        candidates / f"{CURRENT_APPROVAL_SHA256}.json",
    )
    projection = _projection()
    request_id = "2" * 32
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, request_id),
    )
    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id=request_id,
        secret_reads=[],
    )
    assert result.terminal_state == "SUCCEEDED"
    relay_receipt = (
        config.canary_output_root
        / "receipts"
        / request_id
        / "relay-receipt.json"
    )
    relay_receipt.write_bytes(
        canonical_json_bytes(
            {"request_id": request_id, "totally": "invalid"}
        )
    )
    relay_receipt.chmod(0o600)
    namespace = orchestrator.ApprovalScopedLedgerNamespace(
        repo_root=REPO_ROOT,
        attempts_root=attempts,
        legacy_state_root=config.state_root,
        historical_evidence_root=config.canary_output_root,
        candidate_roots=(candidates,),
    )

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_invalid",)


def test_pinned_legacy_receipts_are_normalized_without_mutating_bytes(
    tmp_path: Path,
) -> None:
    namespace, _legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "3d3e6adbe5b74c98ad18a2efe0fd1d0c"
    source = (
        REPO_ROOT
        / "reports/phase2_provider_canary/live_canary/receipts"
        / request_id
    )
    target = _private_dir(evidence / "receipts") / request_id
    shutil.copytree(source, target)
    target.chmod(0o700)
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in target.iterdir()
    }

    receipts = namespace._validate_historical_receipt_directory(
        target,
        request_id,
    )

    assert receipts["relay-receipt.json"]["terminal_reason_source"] == (
        "host_child_process"
    )
    assert receipts["archive-status.json"]["host_child_terminal_reason"] == (
        "RELAY_NONZERO_EXIT"
    )
    assert receipts["archive-status.json"]["terminal_reason_source"] == (
        "host_child_process"
    )
    assert before == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in target.iterdir()
    }


def test_pinned_observability_v1_candidate_resolves_historical_identity(
    tmp_path: Path,
) -> None:
    namespace, _legacy, _attempts, _evidence = _namespace(tmp_path)
    candidate_sha256 = (
        "b30c156bee1f5f2a1c8d44004fcbd25934e56a1caf6739ab58ffe7ead0e881fb"
    )
    source = (
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{candidate_sha256}.json"
    )
    target = namespace.candidate_roots[0] / source.name
    shutil.copy2(source, target)

    identity = namespace._candidate_identity(candidate_sha256)

    assert identity.approval_candidate_sha256 == candidate_sha256
    assert identity.symbol == "000403.SZ"
    assert identity.provider_id == "openai"
    assert identity.exact_model_id == "gpt-5.6-terra"
    assert identity.endpoint_alias == "openai_responses_v1"


def test_mutated_legacy_receipt_does_not_use_the_pinned_compatibility_path(
    tmp_path: Path,
) -> None:
    namespace, _legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "3d3e6adbe5b74c98ad18a2efe0fd1d0c"
    source = (
        REPO_ROOT
        / "reports/phase2_provider_canary/live_canary/receipts"
        / request_id
    )
    target = _private_dir(evidence / "receipts") / request_id
    shutil.copytree(source, target)
    target.chmod(0o700)
    relay = target / "relay-receipt.json"
    value = json.loads(relay.read_bytes())
    value["terminal_status"] = "RELAY_PROCESS_ERROR"
    relay.write_bytes(canonical_json_bytes(value))
    relay.chmod(0o600)

    with pytest.raises(OrchestratorError, match="historical_receipt_invalid"):
        namespace._validate_historical_receipt_directory(target, request_id)


def test_pinned_legacy_receipt_hash_and_parse_use_the_same_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, _legacy, _attempts, evidence = _namespace(tmp_path)
    request_id = "3d3e6adbe5b74c98ad18a2efe0fd1d0c"
    source = (
        REPO_ROOT
        / "reports/phase2_provider_canary/live_canary/receipts"
        / request_id
    )
    target = _private_dir(evidence / "receipts") / request_id
    shutil.copytree(source, target)
    target.chmod(0o700)
    relay = target / "relay-receipt.json"
    original_read = orchestrator._read_report_regular
    relay_reads = 0

    def swap_after_hash(
        path: Path,
        category: str,
        *,
        maximum_bytes: int = 262_144,
    ) -> bytes:
        nonlocal relay_reads
        raw = original_read(
            path,
            category,
            maximum_bytes=maximum_bytes,
        )
        if path == relay:
            relay_reads += 1
            if relay_reads == 1:
                value = json.loads(raw)
                value["terminal_status"] = "RELAY_PROCESS_ERROR"
                value["error_category"] = "RELAY_PROCESS_ERROR"
                relay.write_bytes(canonical_json_bytes(value))
                relay.chmod(0o600)
        return raw

    monkeypatch.setattr(orchestrator, "_read_report_regular", swap_after_hash)

    receipts = namespace._validate_historical_receipt_directory(
        target,
        request_id,
    )

    assert receipts["relay-receipt.json"]["terminal_status"] == "RELAY_TIMEOUT"


def test_completed_scoped_history_rejects_bool_receipt_integer(
    tmp_path: Path,
) -> None:
    config, attempts, _historical_output, candidates = _runtime_config(tmp_path)
    shutil.copy2(
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{CURRENT_APPROVAL_SHA256}.json",
        candidates / f"{CURRENT_APPROVAL_SHA256}.json",
    )
    projection = _projection()
    request_id = "2" * 32
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, request_id),
    )
    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id=request_id,
        secret_reads=[],
    )
    assert result.terminal_state == "SUCCEEDED"
    relay_receipt = (
        config.canary_output_root
        / "receipts"
        / request_id
        / "relay-receipt.json"
    )
    payload = json.loads(relay_receipt.read_bytes())
    payload["retry_count"] = False
    relay_receipt.write_bytes(canonical_json_bytes(payload))
    relay_receipt.chmod(0o600)
    namespace = orchestrator.ApprovalScopedLedgerNamespace(
        repo_root=REPO_ROOT,
        attempts_root=attempts,
        legacy_state_root=config.state_root,
        historical_evidence_root=config.canary_output_root,
        candidate_roots=(candidates,),
    )

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_invalid",)


def test_completed_scoped_history_rejects_malformed_child_metadata(
    tmp_path: Path,
) -> None:
    config, attempts, _historical_output, candidates = _runtime_config(tmp_path)
    shutil.copy2(
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{CURRENT_APPROVAL_SHA256}.json",
        candidates / f"{CURRENT_APPROVAL_SHA256}.json",
    )
    projection = _projection()
    request_id = "2" * 32
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, request_id),
    )
    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id=request_id,
        secret_reads=[],
    )
    assert result.terminal_state == "SUCCEEDED"
    child_metadata = (
        config.canary_output_root
        / "receipts"
        / request_id
        / "child-metadata.json"
    )
    child_metadata.write_bytes(
        canonical_json_bytes(
            {"request_id": request_id, "totally": "invalid"}
        )
    )
    child_metadata.chmod(0o600)
    namespace = orchestrator.ApprovalScopedLedgerNamespace(
        repo_root=REPO_ROOT,
        attempts_root=attempts,
        legacy_state_root=config.state_root,
        historical_evidence_root=config.canary_output_root,
        candidate_roots=(candidates,),
    )

    preflight = namespace.preflight(_scope(OLD_APPROVAL_SHA256))

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_receipt_invalid",)


def test_failed_before_dispatch_with_production_receipts_keeps_scope_available(
    tmp_path: Path,
) -> None:
    config, attempts, _historical_output, candidates = _runtime_config(tmp_path)
    projection = _projection()
    request_id = "2" * 32
    backend = MockCanaryBackend(
        scenario="host_crash_before_dispatch",
        relay_response=_relay_response(projection, request_id),
    )

    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id=request_id,
        secret_reads=[],
    )

    assert result.attempt_ledger_state is LedgerState.FAILED_BEFORE_DISPATCH
    assert result.provider_attempt_count == 0
    assert result.receipt_archive_status == "PRE_DISPATCH_ARCHIVED"
    namespace = orchestrator.ApprovalScopedLedgerNamespace(
        repo_root=REPO_ROOT,
        attempts_root=attempts,
        legacy_state_root=config.state_root,
        historical_evidence_root=config.canary_output_root,
        candidate_roots=(candidates,),
    )

    preflight = namespace.preflight(_scope())

    assert preflight.status == "READY", preflight.errors
    assert preflight.current_scope_provider_attempts == 0
    assert preflight.current_scope_attempt_availability == "AVAILABLE"
    assert namespace.prove_failed_before_dispatch(
        CURRENT_APPROVAL_SHA256,
        CURRENT_SCOPE_ID,
        request_id,
    )


def test_failed_before_dispatch_requires_rejection_artifact(
    tmp_path: Path,
) -> None:
    config, attempts, _historical_output, candidates = _runtime_config(tmp_path)
    projection = _projection()
    request_id = "2" * 32
    backend = MockCanaryBackend(
        scenario="host_crash_before_dispatch",
        relay_response=_relay_response(projection, request_id),
    )
    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id=request_id,
        secret_reads=[],
    )
    assert result.attempt_ledger_state is LedgerState.FAILED_BEFORE_DISPATCH
    (config.canary_output_root / "rejected" / f"{request_id}.json").unlink()
    namespace = orchestrator.ApprovalScopedLedgerNamespace(
        repo_root=REPO_ROOT,
        attempts_root=attempts,
        legacy_state_root=config.state_root,
        historical_evidence_root=config.canary_output_root,
        candidate_roots=(candidates,),
    )

    preflight = namespace.preflight(_scope())

    assert preflight.status == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert preflight.errors == ("historical_route_artifact_conflict",)


def test_one_shot_consumed_current_scope_blocks_before_secret_or_backend(
    tmp_path: Path,
) -> None:
    config, attempts, _historical_output, _candidates = _runtime_config(tmp_path)
    projection = _projection()
    scope = _scope()
    store = _scoped_store(attempts, scope, "2" * 32)
    store.prepare(_identity(CURRENT_APPROVAL_SHA256, "2" * 32), timestamp=FIXED_TIME)
    store.transition(LedgerState.NETWORK_DISPATCH_STARTED, timestamp=FIXED_TIME)
    store.transition(LedgerState.FAILED_AFTER_DISPATCH, timestamp=FIXED_TIME)
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, "3" * 32),
    )
    secret_reads: list[str] = []

    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id="3" * 32,
        secret_reads=secret_reads,
    )

    assert result.terminal_state == "CURRENT_SCOPE_ATTEMPT_CONSUMED"
    assert secret_reads == []
    assert backend.prepare_count == 0
    assert backend.dispatch_count == 0


def test_one_shot_global_request_id_collision_blocks_before_secret(
    tmp_path: Path,
) -> None:
    config, _attempts, historical_output, candidates = _runtime_config(tmp_path)
    shutil.copy2(
        REPO_ROOT
        / "reports/phase2_provider_canary/superseded"
        / f"{OLD_APPROVAL_SHA256}.json",
        candidates / f"{OLD_APPROVAL_SHA256}.json",
    )
    _legacy_terminal(
        config.state_root,
        historical_output,
        request_id="1" * 32,
    )
    projection = _projection()
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, "1" * 32),
    )
    secret_reads: list[str] = []

    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id="1" * 32,
        secret_reads=secret_reads,
    )

    assert result.terminal_state == "REQUEST_ID_COLLISION_BLOCKED"
    assert secret_reads == []
    assert backend.prepare_count == 0


class _ResidueBackend(MockCanaryBackend):
    def inspect_global_residue(self, *, deadline: float) -> CleanupResult:
        return CleanupResult(
            completed=False,
            container_residue_count=1,
        )


def test_one_shot_global_runtime_residue_blocks_before_secret(tmp_path: Path) -> None:
    config, _attempts, _historical_output, _candidates = _runtime_config(tmp_path)
    projection = _projection()
    backend = _ResidueBackend(
        scenario="provider_1s",
        relay_response=_relay_response(projection, "2" * 32),
    )
    secret_reads: list[str] = []

    result = _run(
        config=config,
        backend=backend,
        projection=projection,
        request_id="2" * 32,
        secret_reads=secret_reads,
    )

    assert result.terminal_state == "GLOBAL_LEDGER_SAFETY_BLOCKED"
    assert secret_reads == []
    assert backend.prepare_count == 0


def test_offline_namespace_evidence_is_ready_only_for_complete_zero_residue_gate() -> None:
    preflight_type = orchestrator.LedgerNamespacePreflight
    preflight = preflight_type(
        status="READY",
        current_scope_id=CURRENT_SCOPE_ID,
        current_scope_provider_attempts=0,
        current_scope_attempt_availability="AVAILABLE",
        historical_requests=(
            "3d3e6adbe5b74c98ad18a2efe0fd1d0c",
            "d59766101b63450e8148541d589a90bf",
        ),
        global_request_ids=(
            "3d3e6adbe5b74c98ad18a2efe0fd1d0c",
            "d59766101b63450e8148541d589a90bf",
        ),
    )
    history = (
        HistoricalRequestEvidence(
            request_id="d59766101b63450e8148541d589a90bf",
            ledger_state="ATTEMPT_CONSUMED_UNKNOWN",
            disposition="CONSUMED_PRESERVED",
            ledger_sha256="3" * 64,
        ),
        HistoricalRequestEvidence(
            request_id="3d3e6adbe5b74c98ad18a2efe0fd1d0c",
            ledger_state="FAILED_AFTER_DISPATCH",
            disposition="CONSUMED_PRESERVED",
            ledger_sha256="4" * 64,
        ),
    )

    evidence = build_ledger_namespace_evidence(
        candidate_sha256=CURRENT_APPROVAL_SHA256,
        preflight=preflight,
        historical_requests=history,
        historical_ledger_bytes_unchanged=True,
        historical_evidence_unchanged=True,
        global_lock_state="NONE",
        global_request_id_uniqueness=True,
        old_active_unknown_ledger_safety=True,
        container_residue_count=0,
        network_residue_count=0,
        old_769f_approval_preserved=True,
        current_approval_present=False,
        secret_file_residue_count=0,
        temporary_residue_count=0,
    )

    assert evidence.status == (
        "PHASE2B_CANARY_LEDGER_NAMESPACE_READY_FOR_REAPPROVAL"
    )
    assert evidence.real_provider_attempt_count == 0
    assert evidence.ai_call_count == 0
    assert evidence.provider_http == "NOT_RUN"
    assert evidence.secret_content_read is False
    assert b"Authorization" not in canonical_json_bytes(evidence.model_dump(mode="json"))


def test_offline_namespace_evidence_blocks_consumed_current_scope() -> None:
    preflight_type = orchestrator.LedgerNamespacePreflight
    preflight = preflight_type(
        status="CURRENT_SCOPE_ATTEMPT_CONSUMED",
        current_scope_id=CURRENT_SCOPE_ID,
        current_scope_provider_attempts=1,
        current_scope_attempt_availability="BLOCKED",
        historical_requests=("1" * 32, "2" * 32),
        global_request_ids=("1" * 32, "2" * 32),
    )

    evidence = build_ledger_namespace_evidence(
        candidate_sha256=CURRENT_APPROVAL_SHA256,
        preflight=preflight,
        historical_requests=(),
        historical_ledger_bytes_unchanged=True,
        historical_evidence_unchanged=True,
        global_lock_state="NONE",
        global_request_id_uniqueness=True,
        old_active_unknown_ledger_safety=True,
        container_residue_count=0,
        network_residue_count=0,
        old_769f_approval_preserved=True,
        current_approval_present=False,
        secret_file_residue_count=0,
        temporary_residue_count=0,
    )

    assert evidence.status == "PHASE2B_CANARY_LEDGER_NAMESPACE_BLOCKED"
    assert evidence.current_scope_attempt_availability == "BLOCKED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ledger_namespace_preflight_schema_version", True),
        ("ledger_namespace_version", True),
        ("real_provider_attempt_count", False),
        ("ai_call_count", False),
        ("secret_content_read", 0),
    ],
)
def test_namespace_preflight_evidence_rejects_bool_integer_aliases(
    field: str,
    value: object,
) -> None:
    path = (
        REPO_ROOT
        / "reports/phase2_provider_canary/ledger_namespace_preflight.json"
    )
    payload = json.loads(path.read_bytes())
    payload[field] = value

    with pytest.raises(ValueError, match="namespace_evidence_type_invalid"):
        LedgerNamespaceEvidence.model_validate_json(canonical_json_bytes(payload))


@pytest.mark.parametrize(
    ("override", "expected_error"),
    [
        ({"old_769f_approval_preserved": False}, "old_769f_approval"),
        ({"current_approval_present": True}, "current_approval_absent"),
        ({"secret_file_residue_count": 1}, "secret_file_residue"),
        ({"temporary_residue_count": 1}, "temporary_residue"),
    ],
)
def test_offline_namespace_evidence_blocks_unverified_local_state(
    override: dict[str, object],
    expected_error: str,
) -> None:
    preflight = orchestrator.LedgerNamespacePreflight(
        status="READY",
        current_scope_id=CURRENT_SCOPE_ID,
        current_scope_provider_attempts=0,
        current_scope_attempt_availability="AVAILABLE",
        historical_requests=(
            "3d3e6adbe5b74c98ad18a2efe0fd1d0c",
            "d59766101b63450e8148541d589a90bf",
        ),
        global_request_ids=(
            "3d3e6adbe5b74c98ad18a2efe0fd1d0c",
            "d59766101b63450e8148541d589a90bf",
        ),
    )
    history = (
        HistoricalRequestEvidence(
            request_id="d59766101b63450e8148541d589a90bf",
            ledger_state="ATTEMPT_CONSUMED_UNKNOWN",
            disposition="CONSUMED_PRESERVED",
            ledger_sha256="3" * 64,
        ),
        HistoricalRequestEvidence(
            request_id="3d3e6adbe5b74c98ad18a2efe0fd1d0c",
            ledger_state="FAILED_AFTER_DISPATCH",
            disposition="CONSUMED_PRESERVED",
            ledger_sha256="4" * 64,
        ),
    )
    kwargs: dict[str, object] = {
        "candidate_sha256": CURRENT_APPROVAL_SHA256,
        "preflight": preflight,
        "historical_requests": history,
        "historical_ledger_bytes_unchanged": True,
        "historical_evidence_unchanged": True,
        "global_lock_state": "NONE",
        "global_request_id_uniqueness": True,
        "old_active_unknown_ledger_safety": True,
        "container_residue_count": 0,
        "network_residue_count": 0,
        "old_769f_approval_preserved": True,
        "current_approval_present": False,
        "secret_file_residue_count": 0,
        "temporary_residue_count": 0,
    }
    kwargs.update(override)

    evidence = build_ledger_namespace_evidence(**kwargs)  # type: ignore[arg-type]

    assert evidence.status == "PHASE2B_CANARY_LEDGER_NAMESPACE_BLOCKED"
    assert expected_error in evidence.errors
