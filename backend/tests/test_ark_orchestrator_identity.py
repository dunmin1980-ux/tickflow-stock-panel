from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.providers.ark_contract import (
    ApprovedArkCandidateSnapshot,
    load_installed_ark_approval,
)
from app.services import phase2_canary_orchestrator as orchestrator
from app.services.phase2_canary_orchestrator import (
    ArkDockerCanaryBackend,
    BackendDispatchResult,
    BackendError,
    ExclusiveCanaryLock,
    read_ark_keychain_secret_once,
)
from app.services.phase2_claims_service import canonical_json_bytes
from scripts import run_phase2_ark_single_symbol_canary as ark_launcher

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ark_global_lock_payload_keeps_provider_identity(tmp_path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    lock = ExclusiveCanaryLock(
        state,
        approval_candidate_sha256="a" * 64,
        approval_scope_id="b" * 64,
        provider_id="volcengine_ark",
        endpoint_alias="ark_responses_cn_beijing_v1",
        pid=123,
        started_at="2026-08-12T00:00:00Z",
    )
    assert lock._payload() == {
        "lock_schema_version": 2,
        "pid": 123,
        "started_at": "2026-08-12T00:00:00Z",
        "symbol": "000403.SZ",
        "provider": "volcengine_ark",
        "endpoint_alias": "ark_responses_cn_beijing_v1",
        "approval_candidate_sha256": "a" * 64,
        "approval_scope_id": "b" * 64,
    }


def test_ark_backend_rejects_openai_receipt_after_generic_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ArkDockerCanaryBackend()
    backend._validated_receipts["proxy"] = b'{"component":"proxy"}\n'
    monkeypatch.setattr(
        "app.services.phase2_canary_orchestrator.DockerCanaryBackend.dispatch",
        lambda *_args, **_kwargs: BackendDispatchResult(200, True),
    )
    with pytest.raises(BackendError, match="PROXY_RECEIPT_INVALID"):
        backend.dispatch(SimpleNamespace(request_id="a" * 32), deadline=1.0)


def test_generic_receipt_parser_rejects_tampered_ark_identity() -> None:
    value = {field: None for field in orchestrator._ARK_PROXY_RECEIPT_FIELDS}
    value.update(
        {
            "receipt_schema_version": 2,
            "request_id": "a" * 32,
            "component": "proxy",
            "retry_count": 0,
            "auth_present": True,
            "method_allowed": True,
            "path_allowed": True,
            "redirect_followed": False,
            "tls_verification": True,
            "provider_attempt_count": 0,
            "provider_http_status": None,
            "response_bytes": None,
            "response_size": None,
            "response_category": "PROXY_READY",
            "terminal_status": "PROXY_READY",
            "provider_id": "openai",
            "endpoint_alias": "ark_responses_cn_beijing_v1",
            "exact_model_id": "doubao-seed-2-1-turbo-260628",
        }
    )
    for name in orchestrator._PROXY_EVENTS:
        value[name] = {
            "event": name,
            "occurred": False,
            "wall_time": None,
            "monotonic_ns": None,
            "http_status": None,
            "byte_count": None,
        }
    value["process_started"].update(
        occurred=True,
        wall_time="2026-08-13T00:00:00Z",
        monotonic_ns=1,
    )
    value["proxy_ready"].update(
        occurred=True,
        wall_time="2026-08-13T00:00:01Z",
        monotonic_ns=2,
    )
    with pytest.raises(orchestrator.OrchestratorError, match="RECEIPT_MALFORMED"):
        orchestrator._parse_stage_receipt_bytes(
            canonical_json_bytes(value),
            component="proxy",
            request_id="a" * 32,
        )


def test_openai_receipt_mode_rejects_valid_ark_identity() -> None:
    value = {field: None for field in orchestrator._ARK_PROXY_RECEIPT_FIELDS}
    value.update(
        {
            "receipt_schema_version": 2,
            "request_id": "a" * 32,
            "component": "proxy",
            "retry_count": 0,
            "auth_present": True,
            "method_allowed": True,
            "path_allowed": True,
            "redirect_followed": False,
            "tls_verification": True,
            "provider_attempt_count": 0,
            "provider_http_status": None,
            "response_bytes": None,
            "response_size": None,
            "response_category": "PROXY_READY",
            "terminal_status": "PROXY_READY",
            **orchestrator._ARK_PROXY_RECEIPT_IDENTITY,
        }
    )
    for name in orchestrator._PROXY_EVENTS:
        value[name] = {
            "event": name,
            "occurred": False,
            "wall_time": None,
            "monotonic_ns": None,
            "http_status": None,
            "byte_count": None,
        }
    value["process_started"].update(
        occurred=True,
        wall_time="2026-08-13T00:00:00Z",
        monotonic_ns=1,
    )
    value["proxy_ready"].update(
        occurred=True,
        wall_time="2026-08-13T00:00:01Z",
        monotonic_ns=2,
    )
    with pytest.raises(orchestrator.OrchestratorError, match="RECEIPT_MALFORMED"):
        orchestrator._parse_stage_receipt_bytes(
            canonical_json_bytes(value),
            component="proxy",
            request_id="a" * 32,
            expected_proxy_identity=orchestrator._OPENAI_PROXY_RECEIPT_IDENTITY,
        )


def test_ark_archive_rejects_proxy_receipt_without_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ArkDockerCanaryBackend()
    observed: dict[str, object] = {}

    def fake_archive(_context, _children, _receipts, *, expected_proxy_identity=None):
        observed["identity"] = expected_proxy_identity
        return SimpleNamespace(status="RECEIPT_MALFORMED")

    monkeypatch.setattr(orchestrator, "_archive_stage_evidence", fake_archive)
    result = backend.archive_evidence(
        SimpleNamespace(request_id="a" * 32),
        deadline=1.0,
    )
    assert result.status == "RECEIPT_MALFORMED"
    assert observed["identity"] == orchestrator._ARK_PROXY_RECEIPT_IDENTITY


def test_ark_keychain_reader_uses_only_ark_service() -> None:
    observed: list[list[str]] = []

    def executor(command: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        observed.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="synthetic\n", stderr="")

    assert read_ark_keychain_secret_once(executor=executor) == "synthetic"
    assert observed == [
        [
            "security",
            "find-generic-password",
            "-w",
            "-s",
            "tickflow-phase2-canary-volcengine-ark",
        ]
    ]


def test_ark_approval_loader_fails_before_any_secret_read(tmp_path: Path) -> None:
    candidate = REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
    with pytest.raises(ValueError, match="ark_approval_not_installed"):
        load_installed_ark_approval(candidate, tmp_path / "missing/approval.json")


def test_ark_approval_loader_returns_the_approved_single_snapshot(tmp_path: Path) -> None:
    candidate_path = REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
    candidate_raw = candidate_path.read_bytes()
    candidate = json.loads(candidate_raw)
    approval_root = tmp_path / "approval"
    approval_root.mkdir(mode=0o700)
    approval_root.chmod(0o700)
    approval_path = approval_root / "runtime-approval.json"
    approval_path.write_bytes(
        canonical_json_bytes(
            {
                "ark_runtime_approval_schema_version": 1,
                "approved_candidate_sha256": hashlib.sha256(candidate_raw).hexdigest(),
                "approval_scope": "single_symbol_ark_canary_runtime_v1",
                "provider_id": "volcengine_ark",
                "exact_model_id": "doubao-seed-2-1-turbo-260628",
                "endpoint_alias": "ark_responses_cn_beijing_v1",
                "symbol": "000403.SZ",
                "maximum_provider_attempts": 1,
                "retry_count": 0,
            }
        )
    )
    approval_path.chmod(0o600)

    snapshot = load_installed_ark_approval(candidate_path, approval_path)

    assert snapshot.candidate == candidate
    assert snapshot.candidate_raw == candidate_raw
    assert snapshot.candidate_sha256 == hashlib.sha256(candidate_raw).hexdigest()


def test_ark_launcher_stops_before_secret_or_network_without_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched = {"secret": False, "network": False}

    monkeypatch.setattr(
        ark_launcher,
        "load_installed_ark_approval",
        lambda *_args: (_ for _ in ()).throw(ValueError("ark_approval_not_installed")),
    )
    monkeypatch.setattr(
        ark_launcher,
        "read_ark_keychain_secret_once",
        lambda: touched.__setitem__("secret", True),
    )
    monkeypatch.setattr(
        ark_launcher,
        "run_single_symbol_canary",
        lambda **_kwargs: touched.__setitem__("network", True),
    )

    assert ark_launcher.main([]) == 2
    assert touched == {"secret": False, "network": False}


def test_ark_launcher_wires_the_only_approved_provider_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate_path = REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
    candidate = __import__("json").loads(candidate_path.read_text(encoding="utf-8"))
    observed: dict[str, object] = {}

    candidate_raw = candidate_path.read_bytes()
    approved = ApprovedArkCandidateSnapshot(
        candidate=candidate,
        candidate_raw=candidate_raw,
        candidate_sha256=__import__("hashlib").sha256(candidate_raw).hexdigest(),
    )
    monkeypatch.setattr(ark_launcher, "load_installed_ark_approval", lambda *_args: approved)
    monkeypatch.setattr(ark_launcher, "validate_ark_approval_candidate", lambda *_args: None)

    def private_directory(path: Path) -> Path:
        target = tmp_path / path.name
        target.mkdir(mode=0o700, exist_ok=True)
        return target

    monkeypatch.setattr(ark_launcher, "_private_directory", private_directory)

    def fake_run_single_symbol_canary(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(terminal_state="SUCCEEDED")

    monkeypatch.setattr(ark_launcher, "run_single_symbol_canary", fake_run_single_symbol_canary)
    monkeypatch.setattr(
        ark_launcher, "asdict", lambda result: {"terminal_state": result.terminal_state}
    )

    assert ark_launcher.main([]) == 0
    assert observed["provider_id"] == "volcengine_ark"
    assert observed["exact_model_id"] == "doubao-seed-2-1-turbo-260628"
    assert observed["endpoint_alias"] == "ark_responses_cn_beijing_v1"
    assert observed["secret_reader"] is read_ark_keychain_secret_once
    assert isinstance(observed["backend"], ArkDockerCanaryBackend)
