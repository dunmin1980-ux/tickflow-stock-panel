from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.phase2_claims import ALLOWED_CLAIM_TYPES
from app.schemas.phase2_provider_relay import GenerationControls
from app.services.phase2_claims_service import PREDICATE_RULES
from app.services.phase2_openai_proxy_contract import (
    OpenAIProxyPolicy,
    build_proxy_contract,
    canonical_contract_payload,
    canonical_proxy_contract_bytes,
    main,
    proxy_policy_sha256,
    strict_worker_schema,
    validate_proxy_contract,
)

EXPECTED_POLICY = {
    "provider_id": "openai",
    "model_id": "gpt-5.6-terra",
    "endpoint_host": "api.openai.com",
    "endpoint_port": 443,
    "endpoint_path": "/v1/responses",
    "http_method": "POST",
    "tls_verification": True,
    "minimum_tls_version": "TLSv1_2",
    "follow_redirects": False,
    "stream": False,
    "tools": [],
    "retry_count": 0,
    "maximum_attempts": 1,
    "timeout_seconds": 60,
    "maximum_bytes": 1_048_576,
    "approved_symbol": "000403.SZ",
}


def test_proxy_policy_is_closed_and_exact() -> None:
    assert OpenAIProxyPolicy().model_dump(mode="json") == EXPECTED_POLICY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "other"),
        ("model_id", "latest"),
        ("endpoint_host", "example.com"),
        ("endpoint_port", 80),
        ("endpoint_path", "/v1/chat/completions"),
        ("http_method", "GET"),
        ("tls_verification", False),
        ("minimum_tls_version", "TLSv1_1"),
        ("follow_redirects", True),
        ("stream", True),
        ("tools", ["web_search"]),
        ("retry_count", 1),
        ("maximum_attempts", 2),
        ("timeout_seconds", 30),
        ("maximum_bytes", 2_048_576),
        ("approved_symbol", "600489.SH"),
    ],
)
def test_proxy_policy_rejects_every_mutation(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        OpenAIProxyPolicy.model_validate({**EXPECTED_POLICY, field: value})


def test_proxy_policy_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        OpenAIProxyPolicy.model_validate({**EXPECTED_POLICY, "url": "https://example.com"})


def test_contract_bytes_are_deterministic_and_schema_bound() -> None:
    first = canonical_proxy_contract_bytes()
    second = canonical_proxy_contract_bytes()
    contract = json.loads(first)

    assert first == second
    assert first.endswith(b"\n")
    assert contract["response_format"] == {
        "type": "json_schema",
        "name": "tickflow_phase2_claims_candidate",
        "strict": True,
        "schema": strict_worker_schema(),
    }
    recorded = contract.pop("contract_sha256")
    assert hashlib.sha256(canonical_contract_payload(contract)).hexdigest() == recorded
    assert contract["relay_contract"] == {
        "protocol_version": 1,
        "claims_schema_version": 1,
        "response_format": "typed_claims_json",
        "approved_symbol": "000403.SZ",
        "approved_trade_date": "2026-07-31",
        "allowed_claim_types": sorted(ALLOWED_CLAIM_TYPES),
        "allowed_predicates": sorted(PREDICATE_RULES),
        "generation": GenerationControls().model_dump(mode="json"),
    }


def test_contract_validation_rejects_self_hash_drift() -> None:
    value = build_proxy_contract()
    value["contract_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="proxy_contract_sha256_mismatch"):
        validate_proxy_contract(value)


def test_policy_hash_is_hash_of_canonical_policy() -> None:
    expected = hashlib.sha256(canonical_contract_payload(EXPECTED_POLICY)).hexdigest()
    assert proxy_policy_sha256() == expected


def test_contract_cli_writes_checks_and_rejects_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    target = repository / "docker/phase2-openai-egress-proxy/responses-contract.json"
    target.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "app.services.phase2_openai_proxy_contract.repo_root",
        lambda: repository,
    )

    assert main([]) == 2
    assert main(["--write"]) == 0
    assert target.read_bytes() == canonical_proxy_contract_bytes()
    assert main(["--check"]) == 0

    target.unlink()
    target.symlink_to(tmp_path / "outside.json")
    assert main(["--write"]) == 2
    assert not (tmp_path / "outside.json").exists()


def test_contract_cli_rejects_mutually_exclusive_modes() -> None:
    with pytest.raises(SystemExit):
        main(["--write", "--check"])
