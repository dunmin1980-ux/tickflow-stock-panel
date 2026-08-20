"""Deterministic FACT to INTERPRETATION to SIGNAL to ACTION boundary."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from app.schemas.phase2_option_c import (
    ResearchDecision,
    ResearchInterpretation,
    ResearchSignal,
    SimulationConfig,
    canonical_option_c_bytes,
)
from app.services.phase2_claims_service import CLAIMS_VALID, canonical_json_bytes
from app.services.phase2_option_c_fixture import (
    ReferenceFixtureBundle,
    ReferenceFixtureError,
    verify_reference_projection,
)
from app.services.phase2_paper_trading import derive_action, new_account

SHANGHAI_OFFSET = timedelta(hours=8)
REFERENCE_REASON_PREDICATES = (
    "market_scope",
    "rsi6",
    "macd_dif_vs_dea",
    "ma_value_order",
)


class ResearchDecisionError(ValueError):
    """Raised when validated research evidence cannot produce a decision."""


def _identity(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_option_c_bytes(value)).hexdigest()


def _claim_payloads(bundle: ReferenceFixtureBundle) -> dict[str, dict[str, Any]]:
    payloads = {
        claim.predicate: claim.model_dump(mode="json")
        for claim in bundle.claims.claims
    }
    if any(predicate not in payloads for predicate in REFERENCE_REASON_PREDICATES):
        raise ResearchDecisionError("reference_claim_missing")
    return payloads


def _verify_bundle_identity(bundle: ReferenceFixtureBundle) -> str:
    if bundle.validation.status != CLAIMS_VALID or bundle.validation.errors:
        raise ResearchDecisionError("claims_invalid")
    manifest_sha256 = hashlib.sha256(
        canonical_option_c_bytes(bundle.manifest)
    ).hexdigest()
    claims_sha256 = hashlib.sha256(bundle.canonical_claims).hexdigest()
    claims_model_bytes = canonical_json_bytes(bundle.claims.model_dump(mode="json"))
    try:
        verify_reference_projection(bundle)
    except ReferenceFixtureError as exc:
        raise ResearchDecisionError("fixture_identity_mismatch") from exc
    if bundle.projection.get("trade_date") != bundle.manifest.trade_date.isoformat():
        raise ResearchDecisionError("projection_date_mismatch")
    if (
        claims_model_bytes != bundle.canonical_claims
        or claims_sha256 != bundle.manifest.claims_sha256
        or bundle.validation.normalized_sha256 != claims_sha256
        or bundle.claims.facts_binding.facts_sha256 != bundle.manifest.facts_sha256
        or bundle.projection.get("projection_sha256")
        != bundle.manifest.projection_sha256
        or bundle.claims.symbol != bundle.manifest.symbol
        or bundle.claims.name != bundle.manifest.name
        or bundle.claims.trade_date != bundle.manifest.trade_date.isoformat()
        or bundle.claims.timezone != bundle.manifest.timezone
    ):
        raise ResearchDecisionError("fixture_identity_mismatch")
    return manifest_sha256


def _validate_decision_time(
    bundle: ReferenceFixtureBundle,
    decision_at: datetime,
) -> None:
    if decision_at.tzinfo is None or decision_at.utcoffset() != SHANGHAI_OFFSET:
        raise ResearchDecisionError("decision_timestamp_not_asia_shanghai")
    if decision_at < bundle.manifest.evidence_available_at:
        raise ResearchDecisionError("evidence_not_available_at_decision")
    if decision_at.date() != bundle.manifest.trade_date:
        raise ResearchDecisionError("decision_trade_date_mismatch")


def _validate_claim_times(
    bundle: ReferenceFixtureBundle,
    decision_at: datetime,
) -> None:
    expected = bundle.manifest.trade_date.isoformat()
    for claim in bundle.claims.claims:
        if claim.scope.as_of != expected or claim.scope.as_of > decision_at.date().isoformat():
            raise ResearchDecisionError("claim_as_of_after_decision")


def _reference_reason_refs(
    bundle: ReferenceFixtureBundle,
) -> list[str]:
    claims = _claim_payloads(bundle)
    market_scope = claims["market_scope"]
    rsi6 = claims["rsi6"]
    macd = claims["macd_dif_vs_dea"]
    ma_order = claims["ma_value_order"]
    ma_labels = [
        operand.get("label_id")
        for operand in ma_order.get("object", {}).get("operands", [])
    ]
    if (
        market_scope.get("object", {}).get("value") != "incomplete"
        or rsi6.get("object", {}).get("value", 0) < 70
        or macd.get("object", {}).get("relation") != "ABOVE"
        or ma_order.get("object", {}).get("relation") != "DESCENDING"
        or ma_labels != ["ma60", "ma5", "ma10", "ma20"]
    ):
        raise ResearchDecisionError("reference_rule_not_satisfied")
    return [claims[predicate]["claim_id"] for predicate in REFERENCE_REASON_PREDICATES]


def build_research_decision(
    bundle: ReferenceFixtureBundle,
    decision_at: datetime,
) -> ResearchDecision:
    """Produce the only business decision approved for the frozen fixture."""
    manifest_sha256 = _verify_bundle_identity(bundle)
    _validate_decision_time(bundle, decision_at)
    _validate_claim_times(bundle, decision_at)
    reason_refs = _reference_reason_refs(bundle)
    common_identity = {
        "source_fixture": bundle.manifest.source,
        "symbol": bundle.manifest.symbol,
        "trade_date": bundle.manifest.trade_date.isoformat(),
        "facts_sha256": bundle.manifest.facts_sha256,
        "claims_sha256": bundle.manifest.claims_sha256,
        "fixture_identity": manifest_sha256,
        "reason_refs": reason_refs,
    }
    interpretation_id = _identity(
        {
            **common_identity,
            "identity_type": "research_interpretation_v1",
            "code": "MIXED_TECHNICAL_STRUCTURE",
        }
    )
    interpretation = ResearchInterpretation(
        interpretation_schema_version=1,
        interpretation_id=interpretation_id,
        source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
        symbol="000403.SZ",
        trade_date=bundle.manifest.trade_date,
        code="MIXED_TECHNICAL_STRUCTURE",
        reason_refs=reason_refs,
        facts_sha256=bundle.manifest.facts_sha256,
        claims_sha256=bundle.manifest.claims_sha256,
        fixture_identity=manifest_sha256,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    signal_id = _identity(
        {
            **common_identity,
            "identity_type": "research_signal_v1",
            "interpretation_id": interpretation_id,
            "code": "MIXED_OBSERVATION",
        }
    )
    signal = ResearchSignal(
        signal_schema_version=1,
        signal_id=signal_id,
        interpretation_id=interpretation_id,
        source_fixture="DETERMINISTIC_REFERENCE_FIXTURE",
        symbol="000403.SZ",
        trade_date=bundle.manifest.trade_date,
        code="MIXED_OBSERVATION",
        reason_refs=reason_refs,
        facts_sha256=bundle.manifest.facts_sha256,
        claims_sha256=bundle.manifest.claims_sha256,
        fixture_identity=manifest_sha256,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    config = SimulationConfig.default()
    action = derive_action(
        signal,
        new_account(config),
        config,
        decision_at=decision_at,
    )
    decision_id = action.decision_id
    return ResearchDecision(
        decision_schema_version=1,
        decision_id=decision_id,
        symbol="000403.SZ",
        name="派林生物",
        decision_trade_date=bundle.manifest.trade_date,
        decision_at=decision_at,
        timezone="Asia/Shanghai",
        fixture_manifest_sha256=manifest_sha256,
        facts_sha256=bundle.manifest.facts_sha256,
        projection_sha256=bundle.manifest.projection_sha256,
        claims_sha256=bundle.manifest.claims_sha256,
        interpretation=interpretation,
        signal=signal,
        action=action,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
