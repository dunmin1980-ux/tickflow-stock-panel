from __future__ import annotations

import json
from pathlib import Path

from app.providers.ark_mock import ARK_FAILURE_SCENARIOS
from app.providers.ark_provider import (
    ArkProviderContractError,
    adapt_ark_responses_envelope,
)
from tests.phase2_ark_helpers import candidate, provider_request

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ark_failure_matrix_is_complete_and_fail_closed() -> None:
    expected = {
        "valid_response",
        "401",
        "403",
        "429",
        "500",
        "timeout",
        "invalid_json",
        "invalid_envelope",
        "missing_structured_output",
        "schema_mismatch",
        "empty_candidate",
        "wrong_symbol",
        "wrong_projection_sha",
        "wrong_facts_sha",
        "unknown_claim_type",
        "unknown_predicate",
        "raw_qfq_mismatch",
        "trading_claim",
        "free_text_field",
        "oversized_response",
        "redirect",
    }
    assert set(ARK_FAILURE_SCENARIOS) == expected
    assert all(
        scenario == "valid_response" or result == "REJECTED"
        for scenario, result in ARK_FAILURE_SCENARIOS.items()
    )


def test_ark_failure_matrix_exercises_adapter_without_retry() -> None:
    base = {
        "id": "resp_ark_failure_matrix",
        "object": "response",
        "status": "completed",
        "model": "doubao-seed-2-1-turbo-260628",
        "output": [
            {
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(candidate())}],
            }
        ],
        "usage": {},
    }
    status_scenarios = {"401": 401, "403": 403, "429": 429, "500": 500, "redirect": 302}
    for scenario, status in status_scenarios.items():
        try:
            adapt_ark_responses_envelope(
                provider_request(),
                provider_http_status=status,
                content_type="application/json",
                envelope=base,
                stage_metadata={"retry_count": 0},
            )
        except ArkProviderContractError:
            continue
        raise AssertionError(f"failure scenario accepted: {scenario}")


def test_ark_failure_matrix_rejects_candidate_and_envelope_drift() -> None:
    for scenario in (
        "invalid_json",
        "invalid_envelope",
        "missing_structured_output",
        "schema_mismatch",
        "empty_candidate",
        "wrong_symbol",
        "wrong_projection_sha",
        "wrong_facts_sha",
        "unknown_claim_type",
        "unknown_predicate",
        "raw_qfq_mismatch",
        "trading_claim",
        "free_text_field",
    ):
        value = candidate()
        if scenario == "invalid_json":
            text = "not-json"
        else:
            if scenario == "wrong_symbol":
                value["symbol"] = "600489.SH"
            elif scenario == "wrong_projection_sha":
                value["projection_sha256"] = "f" * 64
            elif scenario == "wrong_facts_sha":
                value["claims"][0]["provenance"]["facts_sha256"] = "f" * 64
            elif scenario == "unknown_claim_type":
                value["claims"][0]["claim_type"] = "UNKNOWN"
            elif scenario == "unknown_predicate":
                value["claims"][0]["predicate"] = "unknown"
            elif scenario == "raw_qfq_mismatch":
                value["claims"][4]["provenance"]["price_basis"] = "qfq"
            elif scenario == "trading_claim":
                value["trading_advice"] = True
            elif scenario == "free_text_field":
                value["commentary"] = "forbidden"
            elif scenario in {"schema_mismatch", "empty_candidate"}:
                value = {}
            text = json.dumps(value)
        envelope = {
            "id": "resp_ark_failure_matrix",
            "object": "wrong" if scenario == "invalid_envelope" else "response",
            "status": "completed",
            "model": "doubao-seed-2-1-turbo-260628",
            "output": []
            if scenario == "missing_structured_output"
            else [
                {
                    "id": "msg",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
            "usage": {},
        }
        try:
            adapt_ark_responses_envelope(
                provider_request(),
                provider_http_status=200,
                content_type="application/json",
                envelope=envelope,
                stage_metadata={"retry_count": 0},
                repo_root=str(REPO_ROOT),
            )
        except ArkProviderContractError:
            continue
        raise AssertionError(f"failure scenario accepted: {scenario}")


def test_ark_mock_transport_limits_are_fail_closed() -> None:
    assert ARK_FAILURE_SCENARIOS["timeout"] == "REJECTED"
    assert ARK_FAILURE_SCENARIOS["oversized_response"] == "REJECTED"
    assert ARK_FAILURE_SCENARIOS["429"] == "REJECTED"


def test_committed_ark_mock_e2e_contains_three_independent_passes() -> None:
    value = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_text(encoding="utf-8")
    )
    assert value["status"] == "PASSED"
    assert value["requested_run_count"] == 3
    assert value["completed_run_count"] == 3
    assert value["distinct_approval_scope_count"] == 3
    assert value["real_public_network_success_count"] == 0
    assert all(run["passed"] for run in value["runs"])
    assert all(run["provider_attempt_count"] == 1 for run in value["runs"])
    assert all(run["retry_count"] == 0 for run in value["runs"])
    assert all(run["seven_stages_complete"] for run in value["runs"])
    assert all(run["container_residue_count"] == 0 for run in value["runs"])
    assert all(run["network_residue_count"] == 0 for run in value["runs"])
    assert all(
        run["proxy_receipt_identity"]
        == {
            "provider_id": "volcengine_ark",
            "endpoint_alias": "ark_responses_cn_beijing_v1",
            "exact_model_id": "doubao-seed-2-1-turbo-260628",
        }
        for run in value["runs"]
    )
