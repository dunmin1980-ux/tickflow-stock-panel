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

from app.services.phase2_minimal_ark_canary import (
    EXPECTED_FACTS_SHA256,
    EXPECTED_PROJECTION_SHA256,
    ArkHostTransport,
    MinimalArkCanaryError,
    build_minimal_execution_inputs,
    finalize_minimal_attempt,
    load_minimal_request_contract,
    mark_minimal_dispatch,
    read_minimal_ark_keychain_secret_once,
    reserve_minimal_attempt,
    validate_minimal_execution_inputs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


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
