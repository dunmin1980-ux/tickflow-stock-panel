from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.services.phase2_claims_service import build_claims_document
from app.services.phase2_isolation_runtime import CandidateHostEvidence
from app.services.phase2_minimal_ark_canary import (
    EXPECTED_FACTS_SHA256,
    EXPECTED_PROJECTION_SHA256,
    ArkHostTransport,
    MinimalArkCanaryError,
    build_minimal_execution_inputs,
    execute_minimal_ark_canary,
    finalize_minimal_attempt,
    load_minimal_request_contract,
    mark_minimal_dispatch,
    read_minimal_ark_keychain_secret_once,
    reserve_minimal_attempt,
    validate_minimal_execution_inputs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SENTINEL_SECRET = "PHASE2_TEST_SECRET_NEVER_PERSIST"


def _candidate() -> dict[str, object]:
    document = build_claims_document(REPO_ROOT, "000403.SZ")
    return {
        "candidate_schema_version": 1,
        "projection_sha256": EXPECTED_PROJECTION_SHA256,
        "symbol": document.symbol,
        "name": document.name,
        "trade_date": document.trade_date,
        "timezone": document.timezone,
        "claims": [claim.model_dump(mode="json") for claim in document.claims],
        "trading_advice": False,
    }


def _envelope(candidate: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": "ark_mock_response",
        "object": "response",
        "status": "completed",
        "model": "doubao-seed-2-1-turbo-260628",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            candidate if candidate is not None else _candidate(),
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _run_with_handler(
    tmp_path: Path,
    handler: httpx.MockTransport,
):
    times = iter(
        (
            "2026-08-16T00:00:00Z",
            "2026-08-16T00:00:01Z",
            "2026-08-16T00:00:02Z",
        )
    )
    return execute_minimal_ark_canary(
        repo_root=REPO_ROOT,
        attempts_root=tmp_path / "attempts",
        scope_id="b" * 64,
        candidate_sha256="c" * 64,
        request_id="a" * 32,
        transport=ArkHostTransport(mock_transport=handler),
        secret_reader=lambda: SENTINEL_SECRET,
        clock=lambda: next(times),
    )


def test_execution_inputs_bind_frozen_facts_and_strict_request() -> None:
    inputs = build_minimal_execution_inputs(REPO_ROOT, "a" * 32)

    assert inputs.provider_id == "volcengine_ark"
    assert inputs.exact_model_id == "doubao-seed-2-1-turbo-260628"
    assert inputs.endpoint == "https://ark.cn-beijing.volces.com/api/v3/responses"
    assert inputs.symbol == "000403.SZ"
    assert inputs.trade_date == "2026-07-31"
    assert inputs.facts_sha256 == EXPECTED_FACTS_SHA256
    assert inputs.facts_sha256 == (
        "adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956"
    )
    assert inputs.projection_sha256 == EXPECTED_PROJECTION_SHA256
    assert inputs.projection_sha256 == (
        "0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f"
    )
    assert inputs.request_body["model"] == inputs.exact_model_id
    assert inputs.request_body["store"] is False
    assert inputs.request_body["stream"] is False
    assert inputs.request_body["tools"] == []
    assert inputs.request_body["text"]["format"]["type"] == "json_schema"
    assert inputs.request_body["text"]["format"]["strict"] is True
    assert inputs.contract.retry_count == 0
    assert inputs.contract.maximum_attempts == 1
    assert inputs.contract.can_publish is False


def test_prompt_identity_excludes_dynamic_request_id() -> None:
    first = build_minimal_execution_inputs(REPO_ROOT, "a" * 32)
    second = build_minimal_execution_inputs(REPO_ROOT, "b" * 32)

    assert first.prompt_sha256 == second.prompt_sha256
    assert first.request_body != second.request_body


def test_contract_rejects_non_strict_or_retrying_variants(tmp_path: Path) -> None:
    contract = json.loads(
        (
            REPO_ROOT
            / "backend/app/services/phase2_minimal_ark_request_contract.json"
        ).read_text(encoding="utf-8")
    )

    for field, invalid in (
        ("provider_id", "openai"),
        ("exact_model_id", "another-model"),
        ("strict_json_schema", False),
        ("retry_count", 1),
        ("maximum_attempts", 2),
        ("can_publish", True),
        ("tls_verification", False),
        ("hostname_verification", False),
        ("follow_redirects", True),
        ("trust_env", True),
    ):
        mutated = dict(contract)
        mutated[field] = invalid
        path = tmp_path / f"{field}.json"
        path.write_text(json.dumps(mutated), encoding="utf-8")
        with pytest.raises(MinimalArkCanaryError, match="request_contract_invalid"):
            load_minimal_request_contract(path)


def test_changed_facts_bytes_are_blocked_before_request_build(tmp_path: Path) -> None:
    facts_dir = tmp_path / "reports/phase2_facts"
    facts_dir.mkdir(parents=True)
    source = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"
    target = facts_dir / source.name
    shutil.copyfile(source, target)
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(MinimalArkCanaryError, match="facts_identity_mismatch"):
        build_minimal_execution_inputs(tmp_path, "a" * 32)


def test_projection_identity_mutation_is_blocked() -> None:
    inputs = build_minimal_execution_inputs(REPO_ROOT, "a" * 32)
    mutated = replace(inputs, projection_sha256="0" * 64)

    with pytest.raises(MinimalArkCanaryError, match="projection_identity_mismatch"):
        validate_minimal_execution_inputs(mutated)


def test_request_id_mutation_cannot_diverge_from_request_body() -> None:
    inputs = build_minimal_execution_inputs(REPO_ROOT, "a" * 32)
    mutated = replace(inputs, request_id="b" * 32)

    with pytest.raises(MinimalArkCanaryError, match="request_contract_invalid"):
        validate_minimal_execution_inputs(mutated)


def test_attempt_ledger_is_private_atomic_and_single_dispatch(tmp_path: Path) -> None:
    inputs = build_minimal_execution_inputs(REPO_ROOT, "a" * 32)
    ledger_path = reserve_minimal_attempt(
        tmp_path / "attempts",
        scope_id="b" * 64,
        candidate_sha256="c" * 64,
        inputs=inputs,
        now="2026-08-16T00:00:00Z",
    )
    prepared = json.loads(ledger_path.read_text(encoding="utf-8"))

    assert prepared["state"] == "PREPARED"
    assert prepared["attempt_count"] == 0
    assert prepared["dispatch_started"] is False
    assert stat.S_IMODE(os.lstat(ledger_path).st_mode) == 0o600
    assert stat.S_IMODE(os.lstat(ledger_path.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(ledger_path.parent.parent).st_mode) == 0o700

    dispatched = mark_minimal_dispatch(
        ledger_path,
        now="2026-08-16T00:00:01Z",
    )
    assert dispatched["state"] == "DISPATCH_STARTED"
    assert dispatched["attempt_count"] == 1
    assert dispatched["dispatch_started"] is True

    with pytest.raises(MinimalArkCanaryError, match="attempt_already_consumed"):
        mark_minimal_dispatch(ledger_path, now="2026-08-16T00:00:02Z")


def test_terminal_ledger_is_consumed_without_secret_material(tmp_path: Path) -> None:
    sentinel = "PHASE2_TEST_SECRET_NEVER_PERSIST"
    inputs = build_minimal_execution_inputs(REPO_ROOT, "a" * 32)
    ledger_path = reserve_minimal_attempt(
        tmp_path / "attempts",
        scope_id="b" * 64,
        candidate_sha256="c" * 64,
        inputs=inputs,
        now="2026-08-16T00:00:00Z",
    )
    mark_minimal_dispatch(ledger_path, now="2026-08-16T00:00:01Z")
    terminal = finalize_minimal_attempt(
        ledger_path,
        terminal_state="HTTP_ERROR",
        provider_http_status=503,
        candidate_validation_state="NOT_RUN",
        claims_validation_state="NOT_RUN",
        now="2026-08-16T00:00:02Z",
    )

    assert terminal["attempt_count"] == 1
    assert terminal["dispatch_started"] is True
    assert terminal["terminal_state"] == "HTTP_ERROR"
    assert sentinel not in ledger_path.read_text(encoding="utf-8")


def test_scope_with_any_existing_attempt_cannot_reserve_again(tmp_path: Path) -> None:
    inputs = build_minimal_execution_inputs(REPO_ROOT, "a" * 32)
    another_request = build_minimal_execution_inputs(REPO_ROOT, "d" * 32)
    attempts = tmp_path / "attempts"
    reserve_minimal_attempt(
        attempts,
        scope_id="b" * 64,
        candidate_sha256="c" * 64,
        inputs=inputs,
        now="2026-08-16T00:00:00Z",
    )

    with pytest.raises(MinimalArkCanaryError, match="scope_already_consumed"):
        reserve_minimal_attempt(
            attempts,
            scope_id="b" * 64,
            candidate_sha256="c" * 64,
            inputs=another_request,
            now="2026-08-16T00:01:00Z",
        )


def test_keychain_reader_uses_fixed_service_and_returns_memory_value() -> None:
    seen: list[tuple[list[str], float]] = []

    def executor(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        seen.append((command, timeout))
        return subprocess.CompletedProcess(command, 0, "secret-in-memory\n", "")

    value = read_minimal_ark_keychain_secret_once(executor=executor)

    assert value == "secret-in-memory"
    assert seen == [
        (
            [
                "security",
                "find-generic-password",
                "-w",
                "-s",
                "tickflow-phase2-canary-volcengine-ark",
            ],
            10.0,
        )
    ]


def test_host_transport_sends_exact_request_once_without_persisting_secret() -> None:
    inputs = build_minimal_execution_inputs(REPO_ROOT, "a" * 32)
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "authorization_present": bool(request.headers.get("Authorization")),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"object": "response"},
        )

    transport = ArkHostTransport(mock_transport=httpx.MockTransport(handler))
    response = transport.send(inputs, "PHASE2_TEST_SECRET_NEVER_PERSIST")

    assert response.status_code == 200
    assert response.content_type == "application/json"
    assert calls == [
        {
            "method": "POST",
            "url": "https://ark.cn-beijing.volces.com/api/v3/responses",
            "authorization_present": True,
            "body": inputs.request_body,
        }
    ]
    assert "PHASE2_TEST_SECRET_NEVER_PERSIST" not in repr(response)


def test_execution_pipeline_validates_claims_and_renderer_once(tmp_path: Path) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_envelope())

    result = _run_with_handler(tmp_path, httpx.MockTransport(handler))
    ledger = json.loads(result.ledger_path.read_text(encoding="utf-8"))

    assert result.status == "SUCCEEDED"
    assert result.candidate_validation_state == "VALID"
    assert result.claims_validation_state == "VALID"
    assert result.renderer_validation_state == "VALID"
    assert result.claim_count == 42
    assert result.can_publish is False
    assert calls == 1
    assert ledger["attempt_count"] == 1
    assert ledger["terminal_state"] == "SUCCEEDED"
    assert SENTINEL_SECRET not in result.ledger_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("status_code", "content_type", "body", "terminal_state"),
    (
        (503, "application/json", b"{}", "HTTP_ERROR"),
        (307, "application/json", b"{}", "HTTP_ERROR"),
        (200, "text/plain", b"not-json", "RESPONSE_REJECTED"),
        (200, "application/json", b"{", "RESPONSE_REJECTED"),
    ),
)
def test_post_dispatch_http_and_envelope_failures_are_consumed_without_retry(
    tmp_path: Path,
    status_code: int,
    content_type: str,
    body: bytes,
    terminal_state: str,
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status_code,
            headers={"Content-Type": content_type},
            content=body,
        )

    result = _run_with_handler(tmp_path, httpx.MockTransport(handler))
    ledger = json.loads(result.ledger_path.read_text(encoding="utf-8"))

    assert result.status == terminal_state
    assert result.can_publish is False
    assert calls == 1
    assert ledger["attempt_count"] == 1
    assert ledger["terminal_state"] == terminal_state


def test_timeout_is_terminal_and_not_retried(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("mock timeout", request=request)

    result = _run_with_handler(tmp_path, httpx.MockTransport(handler))

    assert result.status == "TIMEOUT"
    assert calls == 1
    assert result.attempt_count == 1


def test_invalid_structured_candidate_is_rejected_whole(tmp_path: Path) -> None:
    envelope = _envelope()
    envelope["output"][0]["content"][0]["text"] = "not-json"  # type: ignore[index]
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=envelope)

    result = _run_with_handler(tmp_path, httpx.MockTransport(handler))

    assert result.status == "CANDIDATE_REJECTED"
    assert result.candidate_validation_state == "REJECTED"
    assert result.claims_validation_state == "NOT_RUN"
    assert calls == 1


def test_invalid_claims_are_rejected_without_repair(tmp_path: Path) -> None:
    candidate = _candidate()
    candidate["claims"][0]["provenance"]["fact_refs"] = ["/not/approved"]  # type: ignore[index]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(candidate))

    result = _run_with_handler(tmp_path, httpx.MockTransport(handler))

    assert result.status == "CLAIMS_REJECTED"
    assert result.candidate_validation_state == "VALID"
    assert result.claims_validation_state == "REJECTED"
    assert result.claim_count == 0


def test_renderer_failure_rejects_entire_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = CandidateHostEvidence(
        worker_status="WORKER_CANDIDATE_VALID",
        claims_status="CLAIMS_VALID",
        renderer_status="RENDERED_BLOCKED",
        errors=["rendered_document_not_deterministic"],
        claim_count=42,
        facts_pointer_binding_count=42,
        candidate_sha256="d" * 64,
        rendered_sha256="",
        free_text_field_count=0,
        unsourced_claim_count=0,
        trading_claim_count=0,
        raw_qfq_mismatch_count=0,
        sensitive_hit_count=0,
    )
    monkeypatch.setattr(
        "app.services.phase2_minimal_ark_canary.validate_isolated_candidate",
        lambda *_args, **_kwargs: blocked,
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope())

    result = _run_with_handler(tmp_path, httpx.MockTransport(handler))

    assert result.status == "RENDERER_REJECTED"
    assert result.candidate_validation_state == "VALID"
    assert result.claims_validation_state == "VALID"
    assert result.renderer_validation_state == "REJECTED"


def test_unexpected_host_validation_failure_is_terminal_and_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_closed(*_args, **_kwargs):
        raise RuntimeError("offline validator failure")

    monkeypatch.setattr(
        "app.services.phase2_minimal_ark_canary.validate_isolated_candidate",
        fail_closed,
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_envelope())

    result = _run_with_handler(tmp_path, httpx.MockTransport(handler))
    ledger = json.loads(result.ledger_path.read_text(encoding="utf-8"))

    assert result.status == "INTERNAL_ERROR"
    assert result.attempt_count == 1
    assert calls == 1
    assert ledger["terminal_state"] == "INTERNAL_ERROR"
