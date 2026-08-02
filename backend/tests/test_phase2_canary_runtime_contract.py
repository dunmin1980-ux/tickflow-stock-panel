from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.phase2_canary_runtime_contract import (
    CanaryRuntimeContract,
    RuntimeContractError,
    canonical_runtime_contract_bytes,
    check_runtime_contract_copies,
    load_runtime_contract,
    runtime_contract_sha256,
    write_runtime_contract_copies,
)

EXPECTED_CONTRACT = {
    "canary_runtime_contract_version": 1,
    "cleanup_timeout_seconds": 15,
    "host_orchestrator_timeout_seconds": 90,
    "maximum_provider_attempts": 1,
    "provider_connect_timeout_seconds": 10,
    "provider_read_timeout_seconds": 60,
    "provider_total_timeout_seconds": 60,
    "relay_candidate_wait_timeout_seconds": 75,
    "retry_count": 0,
}


def test_committed_runtime_contract_enforces_the_approved_ordering() -> None:
    contract = load_runtime_contract()

    assert contract.model_dump(mode="json") == EXPECTED_CONTRACT
    assert (
        contract.provider_total_timeout_seconds
        < contract.relay_candidate_wait_timeout_seconds
        < contract.host_orchestrator_timeout_seconds
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_connect_timeout_seconds", 61),
        ("provider_read_timeout_seconds", 61),
        ("relay_candidate_wait_timeout_seconds", 60),
        ("host_orchestrator_timeout_seconds", 75),
        ("retry_count", 1),
        ("maximum_provider_attempts", 2),
    ],
)
def test_runtime_contract_rejects_unsafe_timeout_or_attempt_mutations(
    field: str,
    value: int,
) -> None:
    mutated = dict(EXPECTED_CONTRACT)
    mutated[field] = value

    with pytest.raises(ValidationError):
        CanaryRuntimeContract.model_validate(mutated)


def test_runtime_contract_rejects_missing_and_unknown_fields() -> None:
    missing = dict(EXPECTED_CONTRACT)
    missing.pop("cleanup_timeout_seconds")
    extra = {**EXPECTED_CONTRACT, "environment_override": True}

    with pytest.raises(ValidationError):
        CanaryRuntimeContract.model_validate(missing)
    with pytest.raises(ValidationError):
        CanaryRuntimeContract.model_validate(extra)


def test_runtime_contract_is_not_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_TOTAL_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("RELAY_CANDIDATE_WAIT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("HOST_ORCHESTRATOR_TIMEOUT_SECONDS", "1")

    assert load_runtime_contract().model_dump(mode="json") == EXPECTED_CONTRACT


def test_canonical_bytes_and_hash_are_stable_literals() -> None:
    expected = (
        json.dumps(
            EXPECTED_CONTRACT,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    assert canonical_runtime_contract_bytes() == expected
    assert runtime_contract_sha256() == "34aa9235d25ecc3f2b658551382f2a8ade031eb84f46cc2a1d79bcbc86bdcf68"


def test_generated_proxy_and_relay_contracts_are_byte_identical(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "backend/app/services/phase2_canary_runtime_contract.json"
    proxy = tmp_path / "docker/phase2-openai-egress-proxy/runtime-contract.json"
    relay = tmp_path / "docker/phase2-canary-relay/runtime-contract.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(canonical_runtime_contract_bytes())

    written = write_runtime_contract_copies(tmp_path)

    assert written == (proxy, relay)
    assert proxy.read_bytes() == canonical.read_bytes()
    assert relay.read_bytes() == canonical.read_bytes()
    assert check_runtime_contract_copies(tmp_path) is True


def test_runtime_contract_copy_check_fails_closed_on_drift(tmp_path: Path) -> None:
    canonical = tmp_path / "backend/app/services/phase2_canary_runtime_contract.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(canonical_runtime_contract_bytes())
    write_runtime_contract_copies(tmp_path)
    relay = tmp_path / "docker/phase2-canary-relay/runtime-contract.json"
    relay.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeContractError, match="runtime_contract_copy_mismatch"):
        check_runtime_contract_copies(tmp_path)
