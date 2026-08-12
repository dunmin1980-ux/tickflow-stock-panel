from __future__ import annotations

from pathlib import Path

from app.providers.ark_capability_gate import validate_ark_capability_evidence

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_new_exact_model_passes_responses_json_schema_gate() -> None:
    evidence = validate_ark_capability_evidence(REPO_ROOT)
    assert evidence.status == "PASSED"
    assert evidence.exact_model_id == "doubao-seed-2-1-turbo-260628"
    assert evidence.responses_api_supported is True
    assert evidence.structured_output_supported is True
    assert evidence.approved_mode == "json_schema"
    assert evidence.fallback_modes == ()


def test_old_model_blocker_is_preserved() -> None:
    snapshot = (
        REPO_ROOT / "reports/phase2_provider_ark/official_model_capability_snapshot.txt"
    ).read_text(encoding="utf-8")
    assert "deepseek-v4-flash-ga-260731" in snapshot
    assert "PHASE2B_ARK_STRUCTURED_OUTPUT_BLOCKED" in snapshot
    assert "PRESERVED" in snapshot
