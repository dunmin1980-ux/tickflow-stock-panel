from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.phase2_claims import ClaimsDocument
from app.schemas.phase2_option_c import ResearchDecision
from app.services.phase2_claims_service import (
    ClaimsValidationResult,
    canonical_json_bytes,
)
from app.services.phase2_option_c_fixture import load_reference_fixture
from app.services.phase2_research_decision import (
    ResearchDecisionError,
    build_research_decision,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION_AT = datetime.fromisoformat("2026-07-31T21:15:00+08:00")
EXPECTED_REASON_REFS = [
    "000403SZ-20260731-001-market-scope",
    "000403SZ-20260731-014-rsi6",
    "000403SZ-20260731-040-macd-dif-vs-dea",
    "000403SZ-20260731-041-ma-value-order",
]


def test_reference_fixture_maps_to_mixed_observation_and_hold() -> None:
    decision = build_research_decision(
        load_reference_fixture(REPO_ROOT),
        DECISION_AT,
    )

    assert decision.interpretation.code == "MIXED_TECHNICAL_STRUCTURE"
    assert decision.interpretation.reason_refs == EXPECTED_REASON_REFS
    assert decision.signal.code == "MIXED_OBSERVATION"
    assert decision.signal.reason_refs == EXPECTED_REASON_REFS
    assert decision.action.side == "HOLD"
    assert decision.action.quantity == 0
    assert decision.action.reason_refs == EXPECTED_REASON_REFS
    assert decision.decision_at == DECISION_AT
    assert decision.decision_trade_date.isoformat() == "2026-07-31"
    assert decision.simulation_only == "SIMULATION ONLY"
    assert decision.can_publish is False
    assert decision.trading_advice is False


def test_reference_decision_is_deterministic() -> None:
    bundle = load_reference_fixture(REPO_ROOT)

    first = build_research_decision(bundle, DECISION_AT)
    second = build_research_decision(bundle, DECISION_AT)

    assert first == second
    assert first.decision_id == second.decision_id
    assert first.interpretation.interpretation_id == second.interpretation.interpretation_id
    assert first.signal.signal_id == second.signal.signal_id
    assert first.action.action_id == second.action.action_id


def test_decision_rejects_evidence_before_it_is_available() -> None:
    bundle = load_reference_fixture(REPO_ROOT)

    with pytest.raises(ResearchDecisionError, match="evidence_not_available"):
        build_research_decision(
            bundle,
            datetime.fromisoformat("2026-07-31T21:11:32+08:00"),
        )


def test_decision_rejects_non_shanghai_timestamp() -> None:
    bundle = load_reference_fixture(REPO_ROOT)

    with pytest.raises(ResearchDecisionError, match="asia_shanghai"):
        build_research_decision(
            bundle,
            datetime.fromisoformat("2026-07-31T13:15:00+00:00"),
        )


def test_decision_rejects_invalid_claims_validation() -> None:
    bundle = load_reference_fixture(REPO_ROOT)
    invalid = ClaimsValidationResult(
        status="CLAIMS_INVALID",
        errors=["injected_invalid"],
        normalized_sha256=None,
        claim_count=0,
        facts_pointer_binding_count=0,
        free_text_field_count=0,
        unsourced_claim_count=0,
        trading_claim_count=0,
        raw_qfq_mismatch_count=0,
        sensitive_hit_count=0,
    )

    with pytest.raises(ResearchDecisionError, match="claims_invalid"):
        build_research_decision(replace(bundle, validation=invalid), DECISION_AT)


def test_decision_rejects_in_memory_claims_tampering_with_frozen_bytes() -> None:
    bundle = load_reference_fixture(REPO_ROOT)
    payload = bundle.claims.model_dump(mode="json")
    payload["claims"][0]["claim_id"] = (
        "000403SZ-20260731-001-market-scope-tampered"
    )
    altered = ClaimsDocument.model_validate(payload)

    with pytest.raises(ResearchDecisionError, match="fixture_identity_mismatch"):
        build_research_decision(replace(bundle, claims=altered), DECISION_AT)


def test_decision_rejects_claim_after_decision_date() -> None:
    bundle = load_reference_fixture(REPO_ROOT)
    payload = bundle.claims.model_dump(mode="json")
    payload["claims"][0]["scope"]["as_of"] = "2026-08-01"
    altered = ClaimsDocument.model_validate(payload)
    altered_raw = canonical_json_bytes(altered.model_dump(mode="json"))
    altered_sha256 = hashlib.sha256(altered_raw).hexdigest()
    internally_consistent = replace(
        bundle,
        claims=altered,
        canonical_claims=altered_raw,
        manifest=bundle.manifest.model_copy(
            update={"claims_sha256": altered_sha256}
        ),
        validation=replace(
            bundle.validation,
            normalized_sha256=altered_sha256,
        ),
    )

    with pytest.raises(ResearchDecisionError, match="claim_as_of_after_decision"):
        build_research_decision(internally_consistent, DECISION_AT)


def test_decision_rejects_fixture_manifest_identity_mismatch() -> None:
    bundle = load_reference_fixture(REPO_ROOT)
    altered_manifest = bundle.manifest.model_copy(
        update={"claims_sha256": "0" * 64}
    )

    with pytest.raises(ResearchDecisionError, match="fixture_identity_mismatch"):
        build_research_decision(
            replace(bundle, manifest=altered_manifest),
            DECISION_AT,
        )


def test_decision_rejects_projection_content_with_stale_self_hash() -> None:
    bundle = load_reference_fixture(REPO_ROOT)
    altered_projection = deepcopy(bundle.projection)
    altered_projection["safe_facts"]["daily"]["close"] = "999.99"

    with pytest.raises(ResearchDecisionError, match="fixture_identity_mismatch"):
        build_research_decision(
            replace(bundle, projection=altered_projection),
            DECISION_AT,
        )


def test_research_decision_schema_forbids_free_text_and_action_override() -> None:
    decision = build_research_decision(load_reference_fixture(REPO_ROOT), DECISION_AT)
    payload = decision.model_dump()
    payload["recommendation"] = "buy now"

    with pytest.raises(ValidationError):
        ResearchDecision.model_validate(payload)
