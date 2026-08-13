from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import ssl
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.providers.ark_contract import (
    validate_ark_approval_candidate,
    validate_ark_history_baseline,
    validate_ark_timeout_mock_evidence,
)
from app.services import phase2_canary_orchestrator as orchestrator_module
from app.services.phase2_ai_worker_protocol import FakeClaimsWorker, build_worker_projection
from app.services.phase2_canary_orchestrator import (
    ApprovalScopedLedgerNamespace,
    ApprovalScopeIdentity,
    ApprovedCanaryArtifacts,
    ArkMockCanaryBackend,
    CanaryOrchestratorConfig,
    MockCanaryBackend,
    run_single_symbol_canary,
)
from app.services.phase2_claims_service import canonical_json_bytes
from scripts.prepare_phase2_ark_relay_source import derived_source
from scripts.run_phase2_ark_single_symbol_canary import (
    normalize_committed_runtime_modes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OLD_CANDIDATE_SHA256 = (
    "0fee5a1c509c77ae9b03107dfddf8caefa453673106cced13db4a4d6ac523ffc"
)
OLD_SCOPE_ID = "898079e765f188ba48b2de143ff5fce81e1191ec7bfc6cb773352b2bed90982d"
REQUEST_ID = "2f17745f58534063bdd7eda1eb0d16f1"
EXPECTED_ARK_CONTRACT = {
    "canary_runtime_contract_version": 1,
    "cleanup_timeout_seconds": 15,
    "host_orchestrator_timeout_seconds": 210,
    "maximum_provider_attempts": 1,
    "provider_connect_timeout_seconds": 10,
    "provider_read_timeout_seconds": 180,
    "provider_total_timeout_seconds": 180,
    "relay_candidate_wait_timeout_seconds": 195,
    "retry_count": 0,
}
HISTORICAL_HASHES = {
    "reports/phase2_provider_ark/live_canary/evidence/"
    f"{REQUEST_ID}.json": "35f957462c493331fe89c39f43420b3699a52346df4b8d3f6ff5beeebab1466e",
    "reports/phase2_provider_ark/live_canary/receipts/"
    f"{REQUEST_ID}/archive-status.json": "a540da4a95be4081225750dffa93c643cafee09c613ec90f21e23f649ae29f2e",
    "reports/phase2_provider_ark/live_canary/receipts/"
    f"{REQUEST_ID}/child-metadata.json": "e598dff711ff759e1fdd84576ce55af30b897891bd8225771077a0fab0e7f2db",
    "reports/phase2_provider_ark/live_canary/receipts/"
    f"{REQUEST_ID}/proxy-receipt.json": "46c05c526067bed736262354bd492e809ca0c8294d7523376d806f050e8be9e4",
    "reports/phase2_provider_ark/live_canary/receipts/"
    f"{REQUEST_ID}/relay-receipt.json": "e64a6de0dfa81e51e6ff50ea11fa516db6e20dbdce0f7d3ceb4c3e717579e9d9",
    "reports/phase2_provider_ark/live_canary/rejected/"
    f"{REQUEST_ID}.json": "31aedcefc27797eebccbc18ad9665738559aa31484fe46f2ec511508305e2853",
    "reports/phase2_provider_canary/attempts/"
    f"{OLD_SCOPE_ID}/{REQUEST_ID}/ledger.json": "5eeba9e7068c9225a1963883f7b814b87fe6198c53202502595a177f671bc8ed",
}


def test_ark_dockerfiles_bind_their_own_source_identity() -> None:
    proxy = (REPO_ROOT / "docker/phase2-ark-egress-proxy/Dockerfile").read_text()
    relay = (REPO_ROOT / "docker/phase2-ark-canary-relay/Dockerfile").read_text()

    assert "ARG PROXY_DOCKERFILE_SHA256" in proxy
    assert (
        'org.tickflow.phase2.proxy-dockerfile-sha256="${PROXY_DOCKERFILE_SHA256}"'
        in proxy
    )
    assert "ARG RELAY_DOCKERFILE_SHA256" in relay
    assert (
        'org.tickflow.phase2.relay-dockerfile-sha256="${RELAY_DOCKERFILE_SHA256}"'
        in relay
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_committed_runtime_mode_normalizer_restores_git_checkout_modes(
    tmp_path: Path,
) -> None:
    attempts = tmp_path / "reports/phase2_provider_canary/attempts"
    scope = attempts / ("a" * 64)
    request = scope / ("b" * 32)
    request.mkdir(parents=True, mode=0o755)
    ledger = request / "ledger.json"
    ledger.write_text("{}\n", encoding="utf-8")
    ledger.chmod(0o644)
    receipts = (
        tmp_path
        / "reports/phase2_provider_ark/live_canary/receipts"
        / ("b" * 32)
    )
    receipts.mkdir(parents=True, mode=0o755)
    receipt = receipts / "proxy-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    receipt.chmod(0o644)

    before = {path: path.read_bytes() for path in (ledger, receipt)}
    normalize_committed_runtime_modes(tmp_path)

    assert stat.S_IMODE(os.lstat(attempts).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(scope).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(request).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(ledger).st_mode) == 0o600
    assert stat.S_IMODE(os.lstat(receipts).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(receipt).st_mode) == 0o600
    assert {path: path.read_bytes() for path in before} == before


def test_committed_runtime_mode_normalizer_rejects_symlinked_ledger(
    tmp_path: Path,
) -> None:
    attempts = tmp_path / "reports/phase2_provider_canary/attempts"
    request = attempts / ("a" * 64) / ("b" * 32)
    request.mkdir(parents=True, mode=0o755)
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    (request / "ledger.json").symlink_to(outside)

    with pytest.raises(RuntimeError, match="committed_runtime_mode_invalid"):
        normalize_committed_runtime_modes(tmp_path)


@pytest.fixture(scope="module")
def ark_timeout_module() -> ModuleType:
    from app.services import phase2_ark_timeout_contract

    return phase2_ark_timeout_contract


@pytest.fixture(scope="module")
def ark_proxy_module() -> ModuleType:
    path = REPO_ROOT / "docker/phase2-ark-egress-proxy/proxy.py"
    spec = importlib.util.spec_from_file_location("phase2_ark_timeout_proxy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@pytest.fixture(scope="module")
def ark_relay_module() -> ModuleType:
    path = REPO_ROOT / "docker/phase2-ark-canary-relay/relay.py"
    spec = importlib.util.spec_from_file_location("phase2_ark_timeout_relay", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class _Socket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)


class _Response:
    status = 200

    def __init__(self, clock: list[float], completed_at: float) -> None:
        self.clock = clock
        self.completed_at = completed_at

    def getheader(self, name: str) -> str | None:
        return {
            "Content-Type": "application/json",
            "Content-Length": "2",
        }.get(name)

    def read(self, _maximum: int) -> bytes:
        self.clock[0] = self.completed_at
        return b"{}"


class _Connection:
    def __init__(self, response: _Response) -> None:
        self.sock = _Socket()
        self.response = response
        self.request_count = 0
        self.connect_count = 0

    def connect(self) -> None:
        self.connect_count += 1

    def request(self, *_args: Any, **_kwargs: Any) -> None:
        self.request_count += 1

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        return None


@pytest.mark.parametrize("elapsed", [1.0, 60.0, 120.0, 179.0])
def test_ark_provider_accepts_completion_strictly_before_180_seconds(
    elapsed: float,
    ark_proxy_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    connection = _Connection(_Response(clock, elapsed))
    monkeypatch.setattr(
        ark_proxy_module,
        "create_tls_context",
        lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )

    status, raw = ark_proxy_module.perform_provider_request(
        b"{}",
        "synthetic-test-value",
        connection_factory=lambda *_args: connection,
        runtime_contract=EXPECTED_ARK_CONTRACT,
        monotonic=lambda: clock[0],
    )

    assert (status, raw) == (200, b"{}")
    assert connection.connect_count == 1
    assert connection.request_count == 1


def test_ark_provider_rejects_completion_at_180_second_deadline_without_retry(
    ark_proxy_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    connection = _Connection(_Response(clock, 180.0))
    monkeypatch.setattr(
        ark_proxy_module,
        "create_tls_context",
        lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )

    with pytest.raises(ark_proxy_module.ProxyError) as caught:
        ark_proxy_module.perform_provider_request(
            b"{}",
            "synthetic-test-value",
            connection_factory=lambda *_args: connection,
            runtime_contract=EXPECTED_ARK_CONTRACT,
            monotonic=lambda: clock[0],
        )

    assert caught.value.category == "TIMEOUT"
    assert caught.value.provider_attempt_count == 1
    assert connection.request_count == 1


def test_ark_timeout_contract_is_exact_ordered_and_not_environment_overridable(
    ark_timeout_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_TOTAL_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("RELAY_CANDIDATE_WAIT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("HOST_ORCHESTRATOR_TIMEOUT_SECONDS", "1")

    observed = ark_timeout_module.load_ark_runtime_contract().model_dump(mode="json")

    assert observed == EXPECTED_ARK_CONTRACT
    assert observed["provider_total_timeout_seconds"] < observed[
        "relay_candidate_wait_timeout_seconds"
    ] < observed["host_orchestrator_timeout_seconds"]
    assert observed["retry_count"] == 0
    assert observed["maximum_provider_attempts"] == 1
    assert ark_timeout_module.ark_runtime_contract_sha256() != (
        "34aa9235d25ecc3f2b658551382f2a8ade031eb84f46cc2a1d79bcbc86bdcf68"
    )


def test_ark_timeout_contract_copies_are_byte_identical(
    ark_timeout_module: ModuleType,
) -> None:
    expected = ark_timeout_module.canonical_ark_runtime_contract_bytes()
    assert (
        REPO_ROOT / "docker/phase2-ark-egress-proxy/runtime-contract.json"
    ).read_bytes() == expected
    assert (
        REPO_ROOT / "docker/phase2-ark-canary-relay/runtime-contract.json"
    ).read_bytes() == expected
    assert ark_timeout_module.check_ark_runtime_contract_copies(REPO_ROOT) is True


def test_ark_relay_uses_exact_195_second_candidate_wait(
    ark_relay_module: ModuleType,
) -> None:
    contract = ark_relay_module.load_runtime_contract(
        str(REPO_ROOT / "docker/phase2-ark-canary-relay/runtime-contract.json")
    )

    assert contract == EXPECTED_ARK_CONTRACT
    assert ark_relay_module.generation_controls(contract)["timeout_seconds"] == 195


def test_ark_relay_timeout_source_is_reproducibly_derived() -> None:
    assert derived_source() == (
        REPO_ROOT / "docker/phase2-ark-canary-relay/relay.py"
    ).read_bytes()


def test_ark_orchestrator_uses_exact_210_second_host_deadline(
    tmp_path: Path,
    ark_timeout_module: ModuleType,
) -> None:
    facts_path = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"
    facts_raw = facts_path.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_raw),
        hashlib.sha256(facts_raw).hexdigest(),
        facts_bytes=facts_raw,
    )
    request_id = "e" * 32
    backend = MockCanaryBackend(
        scenario="provider_1s",
        relay_response={
            "protocol_version": 1,
            "request_id": request_id,
            "symbol": "000403.SZ",
            "projection_sha256": projection["projection_sha256"],
            "claims_candidate": json.loads(FakeClaimsWorker().run(projection)),
        },
    )
    observed: list[float] = []
    original_prepare = backend.prepare

    def record_prepare(context, *, deadline: float) -> None:
        observed.append(deadline)
        original_prepare(context, deadline=deadline)

    backend.prepare = record_prepare  # type: ignore[method-assign]
    paths = {
        name: tmp_path / name
        for name in ("state", "attempts", "work", "output", "history", "candidates")
    }
    for path in paths.values():
        path.mkdir(mode=0o700)
    artifacts = ApprovedCanaryArtifacts(
        approval_candidate_sha256="d" * 64,
        facts_sha256=projection["facts_sha256"],
        projection_sha256=projection["projection_sha256"],
        proxy_image_id="sha256:" + "1" * 64,
        relay_image_id="sha256:" + "2" * 64,
        timeout_contract_sha256=ark_timeout_module.ark_runtime_contract_sha256(),
        readiness_contract_sha256="3" * 64,
        orchestrator_source_sha256="4" * 64,
    )

    result = run_single_symbol_canary(
        config=CanaryOrchestratorConfig(
            repo_root=REPO_ROOT,
            state_root=paths["state"],
            attempts_root=paths["attempts"],
            work_root=paths["work"],
            canary_output_root=paths["output"],
            historical_evidence_root=paths["history"],
            candidate_roots=(paths["candidates"],),
            facts_path=facts_path,
        ),
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _value: None,
        secret_reader=lambda: "synthetic-test-value",
        request_id_factory=lambda: request_id,
        wall_clock=lambda: "2026-08-13T00:00:00Z",
        monotonic=backend.monotonic,
        provider_id="volcengine_ark",
        exact_model_id="doubao-seed-2-1-turbo-260628",
        endpoint_alias="ark_responses_cn_beijing_v1",
    )

    assert result.terminal_state == "SUCCEEDED"
    assert observed == [210.0]
    assert result.retry_count == 0


def test_openai_timeout_contract_remains_byte_identical() -> None:
    path = REPO_ROOT / "backend/app/services/phase2_canary_runtime_contract.json"
    assert _sha256(path) == (
        "34aa9235d25ecc3f2b658551382f2a8ade031eb84f46cc2a1d79bcbc86bdcf68"
    )


def test_historical_consumed_ark_request_and_evidence_are_unchanged() -> None:
    for relative, expected in HISTORICAL_HASHES.items():
        assert _sha256(REPO_ROOT / relative) == expected
    ledger = json.loads(
        (
            REPO_ROOT
            / "reports/phase2_provider_canary/attempts"
            / OLD_SCOPE_ID
            / REQUEST_ID
            / "ledger.json"
        ).read_text(encoding="utf-8")
    )
    attempt = ledger["attempt"]
    assert attempt["state"] == "FAILED_AFTER_DISPATCH"
    assert attempt["provider_attempt_count"] == 1
    assert attempt["retry_count"] == 0
    assert attempt["identity"]["request_id"] == REQUEST_ID
    assert attempt["identity"]["approval_candidate_sha256"] == OLD_CANDIDATE_SHA256


def test_historical_receipt_fixes_failure_stage_at_waiting_for_headers() -> None:
    receipt = json.loads(
        (
            REPO_ROOT
            / "reports/phase2_provider_ark/live_canary/receipts"
            / REQUEST_ID
            / "proxy-receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["provider_connect_started"]["occurred"] is True
    assert receipt["provider_connect_completed"]["occurred"] is True
    assert receipt["tls_completed"]["occurred"] is True
    assert receipt["request_write_started"]["occurred"] is True
    assert receipt["request_write_completed"]["occurred"] is True
    assert receipt["response_headers_received"]["occurred"] is False
    assert receipt["response_body_completed"]["occurred"] is False
    assert receipt["terminal_status"] == "RESPONSE_HEADERS_NOT_RECEIVED"


def test_response_header_bridge_is_restricted_to_pinned_consumed_ark_request() -> None:
    proxy = {
        "response_category": "RESPONSE_HEADERS_NOT_RECEIVED",
        "terminal_status": "RESPONSE_HEADERS_NOT_RECEIVED",
        "provider_http_status": None,
        "response_headers_received": {"occurred": False, "http_status": None},
    }
    relay = {
        "proxy_http_status": 502,
        "response_received": {"occurred": False, "http_status": None},
    }

    assert orchestrator_module._is_pinned_ark_response_header_bridge(
        request_id=REQUEST_ID,
        provider_id="volcengine_ark",
        pinned_receipt_bundle=True,
        proxy_receipt=proxy,
        relay_receipt=relay,
    ) is True
    assert orchestrator_module._is_pinned_ark_response_header_bridge(
        request_id="f" * 32,
        provider_id="volcengine_ark",
        pinned_receipt_bundle=True,
        proxy_receipt=proxy,
        relay_receipt=relay,
    ) is False
    assert orchestrator_module._is_pinned_ark_response_header_bridge(
        request_id=REQUEST_ID,
        provider_id="volcengine_ark",
        pinned_receipt_bundle=False,
        proxy_receipt=proxy,
        relay_receipt=relay,
    ) is False


def test_historical_consumed_scope_passes_narrow_response_header_bridge(
    tmp_path: Path,
) -> None:
    source_hashes = {
        relative: _sha256(REPO_ROOT / relative)
        for relative in HISTORICAL_HASHES
    }
    attempts_root = tmp_path / "reports/phase2_provider_canary/attempts"
    ark_history_root = tmp_path / "reports/phase2_provider_ark/live_canary"
    shutil.copytree(
        REPO_ROOT
        / "reports/phase2_provider_canary/attempts"
        / OLD_SCOPE_ID
        / REQUEST_ID,
        attempts_root / OLD_SCOPE_ID / REQUEST_ID,
    )
    shutil.copytree(
        REPO_ROOT / "reports/phase2_provider_ark/live_canary",
        ark_history_root,
    )
    normalize_committed_runtime_modes(tmp_path)
    legacy_state_root = tmp_path / "legacy-state"
    historical_evidence_root = tmp_path / "empty-historical-evidence"
    legacy_state_root.mkdir(mode=0o700)
    historical_evidence_root.mkdir(mode=0o700)
    scope = ApprovalScopeIdentity(
        approval_candidate_sha256="f" * 64,
        symbol="000403.SZ",
        provider_id="volcengine_ark",
        exact_model_id="doubao-seed-2-1-turbo-260628",
        endpoint_alias="ark_responses_cn_beijing_v1",
    )
    preflight = ApprovalScopedLedgerNamespace(
        repo_root=REPO_ROOT,
        attempts_root=attempts_root,
        legacy_state_root=legacy_state_root,
        historical_evidence_root=historical_evidence_root,
        candidate_roots=(
            REPO_ROOT / "reports/phase2_provider_canary/superseded",
            REPO_ROOT / "reports/phase2_provider_canary",
            REPO_ROOT / "reports/phase2_provider_ark",
        ),
        additional_historical_evidence_roots=(
            ark_history_root,
        ),
    ).preflight(scope)

    assert preflight.status == "READY", preflight.errors
    assert REQUEST_ID in preflight.historical_requests
    assert preflight.current_scope_provider_attempts == 0
    assert preflight.current_scope_attempt_availability == "AVAILABLE"
    assert {
        relative: _sha256(REPO_ROOT / relative)
        for relative in HISTORICAL_HASHES
    } == source_hashes


def test_old_60_75_90_candidate_is_superseded_preserved_and_not_current() -> None:
    path = (
        REPO_ROOT
        / "reports/phase2_provider_ark/superseded"
        / f"{OLD_CANDIDATE_SHA256}.json"
    )
    assert _sha256(path) == OLD_CANDIDATE_SHA256
    old = json.loads(path.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="ark_approval_candidate_invalid"):
        validate_ark_approval_candidate(REPO_ROOT, old)


def test_new_candidate_binds_180_195_210_and_new_scope_has_zero_attempts(
    ark_timeout_module: ModuleType,
) -> None:
    candidate_path = REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
    candidate_raw = candidate_path.read_bytes()
    candidate = json.loads(candidate_raw)
    scope = json.loads(
        (
            REPO_ROOT / "reports/phase2_provider_ark/approval_scope_preflight.json"
        ).read_text(encoding="utf-8")
    )
    assert hashlib.sha256(candidate_raw).hexdigest() != OLD_CANDIDATE_SHA256
    assert candidate["status"] == "PHASE2B_ARK_TIMEOUT_CONTRACT_READY_FOR_REAPPROVAL"
    assert candidate["artifact_hashes"]["runtime_contract_sha256"] == (
        ark_timeout_module.ark_runtime_contract_sha256()
    )
    assert scope["approval_candidate_sha256"] == hashlib.sha256(candidate_raw).hexdigest()
    assert scope["approval_scope_id"] != OLD_SCOPE_ID
    assert scope["historical_attempts"] == 0
    assert scope["attempt_availability"] == "AVAILABLE"
    assert scope["approval_installed"] is False
    assert scope["ledger_preflight_status"] == "READY"
    assert canonical_json_bytes(scope) == (
        REPO_ROOT / "reports/phase2_provider_ark/approval_scope_preflight.json"
    ).read_bytes()


def test_timeout_mock_evidence_covers_all_boundaries_without_public_network() -> None:
    value = json.loads(
        (
            REPO_ROOT / "reports/phase2_provider_ark/timeout_mock_e2e.json"
        ).read_text(encoding="utf-8")
    )

    assert value["status"] == "PASSED"
    assert value["provider_timeout_seconds"] == 180
    assert value["relay_timeout_seconds"] == 195
    assert value["host_timeout_seconds"] == 210
    assert value["retry_count"] == 0
    assert value["maximum_provider_attempts"] == 1
    assert value["real_provider_attempt_count"] == 0
    assert value["real_ai_call_count"] == 0
    assert value["public_network_used"] is False
    assert [run["virtual_elapsed_seconds"] for run in value["runs"]] == [
        1.0,
        60.0,
        179.0,
        180.0,
    ]
    assert all(run["passed"] for run in value["runs"])
    assert all(run["retry_count"] == 0 for run in value["runs"])
    assert all(
        run["proxy_receipt_identity"]
        == {
            "endpoint_alias": "ark_responses_cn_beijing_v1",
            "exact_model_id": "doubao-seed-2-1-turbo-260628",
            "provider_id": "volcengine_ark",
        }
        for run in value["runs"]
    )
    assert all(run["candidate_valid"] for run in value["runs"][:3])
    assert all(run["claims_valid"] for run in value["runs"][:3])
    assert all(run["renderer_deterministic"] for run in value["runs"][:3])
    assert value["runs"][3]["failure_stage"] == (
        "WAITING_FOR_PROVIDER_RESPONSE_HEADERS"
    )
    validate_ark_timeout_mock_evidence(value)


def test_timeout_mock_evidence_rejects_non_ark_receipt_identity() -> None:
    path = REPO_ROOT / "reports/phase2_provider_ark/timeout_mock_e2e.json"
    value = json.loads(path.read_bytes())
    value["runs"][0]["proxy_receipt_identity"] = {}

    with pytest.raises(ValueError, match="ark_timeout_mock_evidence_invalid"):
        validate_ark_timeout_mock_evidence(value)


@pytest.mark.parametrize(
    ("run_index", "field", "value"),
    [
        (0, "scenario", "wrong"),
        (0, "terminal_state", "PROVIDER_TIMEOUT"),
        (0, "provider_attempt_count", 999),
        (0, "provider_http", "REAL"),
        (0, "real_provider_attempt_count", 1),
        (0, "candidate_valid", False),
        (0, "container_residue_count", 1),
        (3, "terminal_state", "SUCCEEDED"),
        (3, "provider_attempt_count", 0),
        (3, "failure_stage", None),
    ],
)
def test_timeout_mock_evidence_rejects_material_run_semantic_tampering(
    run_index: int,
    field: str,
    value: object,
) -> None:
    path = REPO_ROOT / "reports/phase2_provider_ark/timeout_mock_e2e.json"
    observed = json.loads(path.read_bytes())
    observed["runs"][run_index][field] = value

    with pytest.raises(ValueError, match="ark_timeout_mock_evidence_invalid"):
        validate_ark_timeout_mock_evidence(observed)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("runs", 0, "result", "approval_scope_id"), "z" * 64),
        (("runs", 0, "provider_attempt_count"), True),
        (("runs", 0, "retry_count"), False),
        (("runs", 0, "result", "container_residue_count"), False),
        (("maximum_provider_attempts",), True),
        (("retry_count",), False),
        (("ark_timeout_mock_e2e_schema_version",), True),
        (("runs", 0, "virtual_elapsed_seconds"), True),
    ],
)
def test_timeout_mock_evidence_rejects_wrong_scope_or_boolean_counters(
    path: tuple[object, ...],
    value: object,
) -> None:
    evidence = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/timeout_mock_e2e.json").read_bytes()
    )
    target = evidence
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError, match="ark_timeout_mock_evidence_invalid"):
        validate_ark_timeout_mock_evidence(evidence)


def test_historical_baseline_is_independent_canonical_and_complete() -> None:
    path = (
        REPO_ROOT
        / "reports/phase2_provider_ark/timeout_contract_history_baseline.json"
    )
    raw = path.read_bytes()
    value = json.loads(raw)

    assert canonical_json_bytes(value) == raw
    assert value["baseline_schema_version"] == 1
    assert value["request_id"] == REQUEST_ID
    assert value["terminal_state"] == "FAILED_AFTER_DISPATCH"
    assert value["attempt_status"] == "CONSUMED"
    assert value["failure_stage"] == "WAITING_FOR_PROVIDER_RESPONSE_HEADERS"
    assert value["historical_hashes"] == HISTORICAL_HASHES
    assert value["old_approval_candidate_sha256"] == OLD_CANDIDATE_SHA256
    assert validate_ark_history_baseline(REPO_ROOT) == value


def test_historical_baseline_rejects_unlisted_ark_history_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    source = REPO_ROOT / "reports"
    destination = root / "reports"
    destination.mkdir(parents=True)
    shutil.copytree(source / "phase2_provider_ark", destination / "phase2_provider_ark")
    shutil.copytree(
        source / "phase2_provider_canary",
        destination / "phase2_provider_canary",
    )
    extra = destination / "phase2_provider_ark/live_canary/evidence" / ("f" * 32 + ".json")
    extra.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ark_history_baseline_invalid"):
        validate_ark_history_baseline(root)


@pytest.mark.parametrize("target_kind", ["baseline", "history_root", "attempt_root"])
def test_historical_baseline_rejects_symlinked_file_or_ancestor(
    tmp_path: Path,
    target_kind: str,
) -> None:
    root = tmp_path / "repo"
    destination = root / "reports"
    destination.mkdir(parents=True)
    shutil.copytree(
        REPO_ROOT / "reports/phase2_provider_ark",
        destination / "phase2_provider_ark",
    )
    shutil.copytree(
        REPO_ROOT / "reports/phase2_provider_canary",
        destination / "phase2_provider_canary",
    )
    targets = {
        "baseline": destination
        / "phase2_provider_ark/timeout_contract_history_baseline.json",
        "history_root": destination / "phase2_provider_ark/live_canary",
        "attempt_root": destination
        / "phase2_provider_canary/attempts"
        / OLD_SCOPE_ID,
    }
    target = targets[target_kind]
    outside = tmp_path / f"outside-{target_kind}"
    target.rename(outside)
    target.symlink_to(outside, target_is_directory=outside.is_dir())

    with pytest.raises(ValueError, match="ark_history_baseline_invalid"):
        validate_ark_history_baseline(root)


@pytest.mark.parametrize("mutation", ["missing", "bytes"])
def test_historical_baseline_rejects_missing_or_mutated_history(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / "repo"
    destination = root / "reports"
    destination.mkdir(parents=True)
    shutil.copytree(
        REPO_ROOT / "reports/phase2_provider_ark",
        destination / "phase2_provider_ark",
    )
    shutil.copytree(
        REPO_ROOT / "reports/phase2_provider_canary",
        destination / "phase2_provider_canary",
    )
    target = (
        destination
        / "phase2_provider_ark/live_canary/evidence"
        / f"{REQUEST_ID}.json"
    )
    if mutation == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(ValueError, match="ark_history_baseline_invalid"):
        validate_ark_history_baseline(root)


class _RelayBoundaryResponse:
    status = 200

    def __init__(self, clock: list[float]) -> None:
        self.clock = clock

    def getheader(self, name: str) -> str | None:
        return "2" if name == "Content-Length" else None

    def read(self, _maximum: int) -> bytes:
        self.clock[0] = 195.0
        return b"{}"


class _RelayBoundaryConnection:
    def __init__(self, clock: list[float]) -> None:
        self.sock = _Socket()
        self.response = _RelayBoundaryResponse(clock)

    def connect(self) -> None:
        return None

    def request(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def getresponse(self) -> _RelayBoundaryResponse:
        return self.response

    def close(self) -> None:
        return None


def test_ark_relay_rejects_completion_at_exact_195_second_deadline(
    ark_relay_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    connection = _RelayBoundaryConnection(clock)
    monkeypatch.setattr(ark_relay_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        ark_relay_module.http.client,
        "HTTPConnection",
        lambda *_args, **_kwargs: connection,
    )

    with pytest.raises(ark_relay_module.RelayError, match="RELAY_TIMEOUT"):
        ark_relay_module._default_requester(
            b"{}",
            195,
            ark_relay_module.RelayStageRecorder(),
        )


def test_ark_host_rejects_completion_at_exact_210_second_deadline(
    tmp_path: Path,
    ark_timeout_module: ModuleType,
) -> None:
    facts_path = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"
    facts_raw = facts_path.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_raw),
        hashlib.sha256(facts_raw).hexdigest(),
        facts_bytes=facts_raw,
    )
    request_id = "c" * 32
    backend = ArkMockCanaryBackend(
        scenario="provider_1s",
        relay_response={
            "protocol_version": 1,
            "request_id": request_id,
            "symbol": "000403.SZ",
            "projection_sha256": projection["projection_sha256"],
            "claims_candidate": json.loads(FakeClaimsWorker().run(projection)),
        },
    )
    original_dispatch = backend.dispatch

    def complete_at_deadline(context, *, deadline: float):
        result = original_dispatch(context, deadline=deadline)
        backend._now = deadline
        return result

    backend.dispatch = complete_at_deadline  # type: ignore[method-assign]
    paths = {
        name: tmp_path / name
        for name in ("state", "attempts", "work", "output", "history", "candidates")
    }
    for path in paths.values():
        path.mkdir(mode=0o700)
    artifacts = ApprovedCanaryArtifacts(
        approval_candidate_sha256="a" * 64,
        facts_sha256=projection["facts_sha256"],
        projection_sha256=projection["projection_sha256"],
        proxy_image_id="sha256:" + "1" * 64,
        relay_image_id="sha256:" + "2" * 64,
        timeout_contract_sha256=ark_timeout_module.ark_runtime_contract_sha256(),
        readiness_contract_sha256="3" * 64,
        orchestrator_source_sha256="4" * 64,
    )

    result = run_single_symbol_canary(
        config=CanaryOrchestratorConfig(
            repo_root=REPO_ROOT,
            state_root=paths["state"],
            attempts_root=paths["attempts"],
            work_root=paths["work"],
            canary_output_root=paths["output"],
            historical_evidence_root=paths["history"],
            candidate_roots=(paths["candidates"],),
            facts_path=facts_path,
        ),
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda _value: None,
        secret_reader=lambda: "synthetic-test-value",
        request_id_factory=lambda: request_id,
        wall_clock=lambda: "2026-08-13T00:00:00Z",
        monotonic=backend.monotonic,
        provider_id="volcengine_ark",
        exact_model_id="doubao-seed-2-1-turbo-260628",
        endpoint_alias="ark_responses_cn_beijing_v1",
    )

    assert result.terminal_state == "ORCHESTRATOR_TIMEOUT"
    assert result.provider_attempt_count == 1
    assert result.retry_count == 0


def test_ark_host_deadline_samples_monotonic_once_per_gate(
) -> None:
    values = iter([0.0, 210.0, 211.0])

    assert orchestrator_module._host_deadline_expired(
        monotonic=lambda: next(values),
        deadline=0.0,
        provider_id="openai",
    ) is False
    assert orchestrator_module._host_deadline_expired(
        monotonic=lambda: next(values),
        deadline=210.0,
        provider_id="volcengine_ark",
    ) is True
    assert next(values) == 211.0
