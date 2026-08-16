"""Fail-closed deterministic research fixture for Phase 2B Option C."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.phase2_claims import (
    ClaimsDocument,
    WorkerClaimsCandidate,
    claims_json_schema,
)
from app.schemas.phase2_option_c import ReferenceFixtureManifest, canonical_option_c_bytes
from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_claims_renderer import render_claims_document
from app.services.phase2_claims_service import (
    CLAIMS_VALID,
    ClaimsValidationResult,
    canonical_json_bytes,
    validate_claims_document,
)

EXPECTED_FACTS_SHA256 = (
    "adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956"
)
EXPECTED_PROJECTION_SHA256 = (
    "0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f"
)
EXPECTED_CLAIMS_SHA256 = (
    "66f7ef471b293a4d5453ca53fd91668f217176915e6e88f2ce88f4f037559183"
)
EXPECTED_CLAIMS_DOCUMENT_SCHEMA_SHA256 = (
    "9a78c1ddb751f8854f58d6f2266cc18907ab4dbfb44ea104d724930ed0e45a83"
)
EXPECTED_WORKER_SCHEMA_SHA256 = (
    "9e80c214fc338790c17af28167f9e3916435f3e52fecbaf7925114e16cb6a19f"
)
EXPECTED_DAILY_SUMMARY_SHA256 = (
    "4cc5ea990df205deb17923826df6df49fadd68539784e0c71efee331031cbb60"
)
EXPECTED_RENDERER_SHA256 = (
    "63482087ca33cb7a0ea4767c9b4f41e9176b7abd0f5ac5516bb14b11fc9514e8"
)

FACTS_FILE = "reports/phase2_facts/000403SZ_facts.json"
CLAIMS_FILE = "reports/phase2_claims/fixtures/000403SZ_claims.json"
CLAIMS_SCHEMA_FILE = "reports/phase2_claims/schema/phase2_claims.schema.json"
DAILY_SUMMARY_FILE = "reports/phase1_observation/2026-07-31/daily_summary.json"


class ReferenceFixtureError(ValueError):
    """Raised when frozen Option C fixture evidence is not exact."""


@dataclass(frozen=True)
class ReferenceFixtureBundle:
    manifest: ReferenceFixtureManifest
    claims: ClaimsDocument
    validation: ClaimsValidationResult
    projection: dict[str, Any]
    rendered_claims: str
    canonical_claims: bytes


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_regular(repo_root: Path, relative: str) -> bytes:
    root = repo_root.resolve(strict=True)
    candidate = root / relative
    if candidate.is_symlink():
        raise ReferenceFixtureError(f"source_must_be_regular:{relative}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise ReferenceFixtureError(f"source_must_be_regular:{relative}") from exc
    if not resolved.is_file():
        raise ReferenceFixtureError(f"source_must_be_regular:{relative}")
    return resolved.read_bytes()


def _parse_object(raw: bytes, *, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non_finite:{value}")

    try:
        parsed = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReferenceFixtureError(f"{label}_json_invalid") from exc
    if not isinstance(parsed, dict):
        raise ReferenceFixtureError(f"{label}_json_invalid")
    return parsed


def _require_hash(raw: bytes, expected: str, *, label: str) -> str:
    actual = _sha256(raw)
    if actual != expected:
        raise ReferenceFixtureError(f"{label}_sha256_mismatch")
    return actual


def load_reference_fixture(repo_root: Path) -> ReferenceFixtureBundle:
    """Load and independently verify the frozen single-symbol fixture."""
    root = repo_root.resolve(strict=True)
    facts_raw = _read_regular(root, FACTS_FILE)
    claims_raw = _read_regular(root, CLAIMS_FILE)
    claims_schema_raw = _read_regular(root, CLAIMS_SCHEMA_FILE)
    daily_summary_raw = _read_regular(root, DAILY_SUMMARY_FILE)

    facts_sha256 = _require_hash(
        facts_raw,
        EXPECTED_FACTS_SHA256,
        label="facts",
    )
    claims_sha256 = _require_hash(
        claims_raw,
        EXPECTED_CLAIMS_SHA256,
        label="claims",
    )
    claims_schema_sha256 = _require_hash(
        claims_schema_raw,
        EXPECTED_CLAIMS_DOCUMENT_SCHEMA_SHA256,
        label="claims_document_schema",
    )
    daily_summary_sha256 = _require_hash(
        daily_summary_raw,
        EXPECTED_DAILY_SUMMARY_SHA256,
        label="daily_summary",
    )

    expected_claims_schema = canonical_json_bytes(claims_json_schema())
    if claims_schema_raw != expected_claims_schema:
        raise ReferenceFixtureError("claims_document_schema_content_mismatch")
    worker_schema_sha256 = _sha256(
        canonical_json_bytes(
            WorkerClaimsCandidate.model_json_schema(mode="validation")
        )
    )
    if worker_schema_sha256 != EXPECTED_WORKER_SCHEMA_SHA256:
        raise ReferenceFixtureError("worker_candidate_schema_sha256_mismatch")

    facts = _parse_object(facts_raw, label="facts")
    projection = build_worker_projection(
        facts,
        facts_sha256,
        facts_bytes=facts_raw,
    )
    if projection.get("projection_sha256") != EXPECTED_PROJECTION_SHA256:
        raise ReferenceFixtureError("projection_sha256_mismatch")

    try:
        claims = ClaimsDocument.model_validate_json(claims_raw)
    except ValidationError as exc:
        raise ReferenceFixtureError("claims_document_invalid") from exc
    validation = validate_claims_document(root, claims)
    if (
        validation.status != CLAIMS_VALID
        or validation.errors
        or validation.normalized_sha256 != claims_sha256
        or validation.free_text_field_count != 0
        or validation.unsourced_claim_count != 0
        or validation.trading_claim_count != 0
        or validation.raw_qfq_mismatch_count != 0
        or validation.sensitive_hit_count != 0
    ):
        raise ReferenceFixtureError("reference_claims_invalid")
    if (
        claims.symbol != "000403.SZ"
        or claims.name != "派林生物"
        or claims.trade_date != "2026-07-31"
        or claims.timezone != "Asia/Shanghai"
    ):
        raise ReferenceFixtureError("reference_claims_identity_mismatch")
    canonical_claims = canonical_json_bytes(claims.model_dump(mode="json"))
    if canonical_claims != claims_raw:
        raise ReferenceFixtureError("reference_claims_not_canonical")

    rendered_claims = render_claims_document(claims, validation)
    if rendered_claims != render_claims_document(claims, validation):
        raise ReferenceFixtureError("claims_renderer_not_deterministic")
    renderer_sha256 = _sha256(rendered_claims.encode("utf-8"))
    if renderer_sha256 != EXPECTED_RENDERER_SHA256:
        raise ReferenceFixtureError("claims_renderer_sha256_mismatch")

    daily_summary = _parse_object(daily_summary_raw, label="daily_summary")
    if daily_summary.get("observation_date") != "2026-07-31":
        raise ReferenceFixtureError("daily_summary_date_mismatch")
    try:
        evidence_available_at = datetime.fromisoformat(
            daily_summary["run_completed_at"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReferenceFixtureError("daily_summary_completion_invalid") from exc
    if (
        evidence_available_at.isoformat() != "2026-07-31T21:11:33+08:00"
        or evidence_available_at.date() != date(2026, 7, 31)
    ):
        raise ReferenceFixtureError("daily_summary_completion_mismatch")

    manifest = ReferenceFixtureManifest(
        fixture_manifest_version=1,
        source="DETERMINISTIC_REFERENCE_FIXTURE",
        symbol="000403.SZ",
        name="派林生物",
        trade_date=date(2026, 7, 31),
        timezone="Asia/Shanghai",
        evidence_available_at=evidence_available_at,
        facts_file=FACTS_FILE,
        facts_sha256=facts_sha256,
        projection_sha256=projection["projection_sha256"],
        claims_file=CLAIMS_FILE,
        claims_sha256=claims_sha256,
        claims_document_schema_file=CLAIMS_SCHEMA_FILE,
        claims_document_schema_sha256=claims_schema_sha256,
        worker_candidate_schema_sha256=worker_schema_sha256,
        source_daily_summary_file=DAILY_SUMMARY_FILE,
        source_daily_summary_sha256=daily_summary_sha256,
        claims_renderer_sha256=renderer_sha256,
        claim_count=validation.claim_count,
        facts_pointer_binding_count=validation.facts_pointer_binding_count,
        vendor_pending=list(claims.vendor_pending),
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    return ReferenceFixtureBundle(
        manifest=manifest,
        claims=claims,
        validation=validation,
        projection=projection,
        rendered_claims=rendered_claims,
        canonical_claims=canonical_claims,
    )


def build_reference_artifacts(bundle: ReferenceFixtureBundle) -> dict[str, bytes]:
    """Return the only two canonical reference-fixture artifacts."""
    return {
        "reference_fixture_manifest.json": canonical_option_c_bytes(bundle.manifest),
        "reference_typed_claims.json": bundle.canonical_claims,
    }
