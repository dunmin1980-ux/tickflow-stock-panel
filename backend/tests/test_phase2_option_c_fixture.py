from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.services.phase2_option_c_fixture import (
    ReferenceFixtureError,
    build_reference_artifacts,
    load_reference_fixture,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FACTS_SHA256 = "adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956"
PROJECTION_SHA256 = "0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f"
CLAIMS_SHA256 = "66f7ef471b293a4d5453ca53fd91668f217176915e6e88f2ce88f4f037559183"
CLAIMS_DOCUMENT_SCHEMA_SHA256 = (
    "9a78c1ddb751f8854f58d6f2266cc18907ab4dbfb44ea104d724930ed0e45a83"
)
WORKER_SCHEMA_SHA256 = (
    "9e80c214fc338790c17af28167f9e3916435f3e52fecbaf7925114e16cb6a19f"
)
RENDERER_SHA256 = "63482087ca33cb7a0ea4767c9b4f41e9176b7abd0f5ac5516bb14b11fc9514e8"

SOURCE_PATHS = (
    "reports/phase2_facts/000403SZ_facts.json",
    "reports/phase2_claims/fixtures/000403SZ_claims.json",
    "reports/phase2_claims/schema/phase2_claims.schema.json",
    "reports/phase1_observation/2026-07-31/daily_summary.json",
)


def _copy_fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for relative in SOURCE_PATHS:
        source = REPO_ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return root


def test_reference_fixture_binds_all_frozen_identities() -> None:
    bundle = load_reference_fixture(REPO_ROOT)

    assert bundle.manifest.source == "DETERMINISTIC_REFERENCE_FIXTURE"
    assert bundle.manifest.symbol == "000403.SZ"
    assert bundle.manifest.name == "派林生物"
    assert bundle.manifest.trade_date.isoformat() == "2026-07-31"
    assert bundle.manifest.evidence_available_at.isoformat() == (
        "2026-07-31T21:11:33+08:00"
    )
    assert bundle.manifest.facts_sha256 == FACTS_SHA256
    assert bundle.manifest.projection_sha256 == PROJECTION_SHA256
    assert bundle.manifest.claims_sha256 == CLAIMS_SHA256
    assert (
        bundle.manifest.claims_document_schema_sha256
        == CLAIMS_DOCUMENT_SCHEMA_SHA256
    )
    assert bundle.manifest.worker_candidate_schema_sha256 == WORKER_SCHEMA_SHA256
    assert bundle.manifest.claims_renderer_sha256 == RENDERER_SHA256
    assert bundle.validation.status == "CLAIMS_VALID"
    assert bundle.validation.trading_claim_count == 0
    assert bundle.validation.free_text_field_count == 0
    assert bundle.validation.unsourced_claim_count == 0
    assert bundle.validation.raw_qfq_mismatch_count == 0
    assert bundle.validation.sensitive_hit_count == 0
    assert bundle.claims.symbol == "000403.SZ"
    assert bundle.claims.trade_date == "2026-07-31"
    assert bundle.projection["projection_sha256"] == PROJECTION_SHA256
    assert bundle.manifest.can_publish is False
    assert bundle.manifest.trading_advice is False
    assert bundle.manifest.simulation_only == "SIMULATION ONLY"


def test_reference_claims_artifact_remains_a_strict_claims_document() -> None:
    bundle = load_reference_fixture(REPO_ROOT)
    artifacts = build_reference_artifacts(bundle)

    assert sorted(artifacts) == [
        "reference_fixture_manifest.json",
        "reference_typed_claims.json",
    ]
    assert artifacts["reference_typed_claims.json"] == (
        REPO_ROOT / "reports/phase2_claims/fixtures/000403SZ_claims.json"
    ).read_bytes()
    manifest = json.loads(artifacts["reference_fixture_manifest.json"])
    claims = json.loads(artifacts["reference_typed_claims.json"])
    assert manifest["source"] == "DETERMINISTIC_REFERENCE_FIXTURE"
    assert "source" not in claims
    assert claims["source_system"] == "tickflow-stock-panel"


def test_reference_fixture_is_deterministic() -> None:
    first = load_reference_fixture(REPO_ROOT)
    second = load_reference_fixture(REPO_ROOT)

    assert build_reference_artifacts(first) == build_reference_artifacts(second)
    assert first.rendered_claims == second.rendered_claims


@pytest.mark.parametrize(
    ("relative", "mutation"),
    [
        (
            "reports/phase2_facts/000403SZ_facts.json",
            lambda raw: raw + b"\n",
        ),
        (
            "reports/phase2_claims/fixtures/000403SZ_claims.json",
            lambda raw: raw.replace(b'"000403.SZ"', b'"600489.SH"', 1),
        ),
        (
            "reports/phase2_claims/schema/phase2_claims.schema.json",
            lambda raw: raw + b"\n",
        ),
        (
            "reports/phase1_observation/2026-07-31/daily_summary.json",
            lambda raw: raw.replace(
                b"2026-07-31T21:11:33+08:00",
                b"2026-08-01T00:00:00+08:00",
            ),
        ),
    ],
)
def test_reference_fixture_rejects_mutated_source_bytes(
    tmp_path: Path,
    relative: str,
    mutation: object,
) -> None:
    root = _copy_fixture_repo(tmp_path)
    path = root / relative
    path.write_bytes(mutation(path.read_bytes()))  # type: ignore[operator]

    with pytest.raises(ReferenceFixtureError):
        load_reference_fixture(root)


def test_reference_fixture_rejects_symlinked_source(tmp_path: Path) -> None:
    root = _copy_fixture_repo(tmp_path)
    claims = root / "reports/phase2_claims/fixtures/000403SZ_claims.json"
    target = claims.with_suffix(".target.json")
    claims.rename(target)
    claims.symlink_to(target)

    with pytest.raises(ReferenceFixtureError, match="regular"):
        load_reference_fixture(root)
