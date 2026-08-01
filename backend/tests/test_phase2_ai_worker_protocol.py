from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from app.services.phase2_ai_worker_protocol import (
    ISOLATION_DESIGN_READY,
    ISOLATION_RUNTIME_NOT_YET_VERIFIED,
    WORKER_CANDIDATE_INVALID,
    WORKER_CANDIDATE_VALID,
    FakeClaimsWorker,
    Phase2WorkerProtocolError,
    build_worker_projection,
    compute_projection_sha256,
    read_exact_projection,
    validate_worker_candidate,
)
from app.services.phase2_claims_service import build_claims_document

REPO_ROOT = Path(__file__).resolve().parents[2]


def _facts(symbol: str = "000403.SZ") -> tuple[dict, bytes, str]:
    path = REPO_ROOT / "reports/phase2_facts" / f'{symbol.replace(".", "")}_facts.json'
    raw = path.read_bytes()
    return json.loads(raw), raw, hashlib.sha256(raw).hexdigest()


def _projection(symbol: str = "000403.SZ") -> dict:
    facts, facts_bytes, facts_sha256 = _facts(symbol)
    return build_worker_projection(
        facts,
        facts_sha256,
        facts_bytes=facts_bytes,
    )


def _full_candidate(symbol: str = "000403.SZ") -> dict:
    document = build_claims_document(REPO_ROOT, symbol)
    projection = _projection(symbol)
    return {
        "candidate_schema_version": 1,
        "projection_sha256": projection["projection_sha256"],
        "symbol": document.symbol,
        "name": document.name,
        "trade_date": document.trade_date,
        "timezone": document.timezone,
        "claims": [claim.model_dump(mode="json") for claim in document.claims],
        "trading_advice": False,
    }


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value)) if value else set()
    return set()


def test_projection_is_minimal_hash_bound_and_contains_no_sensitive_paths() -> None:
    projection = _projection()
    serialized = json.dumps(projection, ensure_ascii=False, sort_keys=True)

    assert set(projection) == {
        "projection_schema_version",
        "projection_sha256",
        "facts_sha256",
        "symbol",
        "name",
        "trade_date",
        "timezone",
        "safe_facts",
        "allowed_predicates",
    }
    assert projection["projection_sha256"] == compute_projection_sha256(projection)
    assert set(projection["safe_facts"]) == {
        "daily",
        "indicators",
        "minute_1m",
        "minute_30m",
        "adjustment_factors",
        "data_freshness",
        "scope",
        "vendor_pending",
    }
    assert not {
        "source_evidence",
        "numeric_provenance",
        "generation",
        "key_levels",
        "secrets",
        "auth",
        "api_key",
    } & _all_keys(projection)
    assert str(REPO_ROOT) not in serialized
    assert str(Path.home()) not in serialized
    assert "reports/phase2_facts" not in serialized
    assert "/Users/" not in serialized


def test_projection_hash_changes_when_safe_value_changes() -> None:
    projection = _projection()
    changed = deepcopy(projection)
    changed["safe_facts"]["daily"]["close"] += 0.01

    assert compute_projection_sha256(changed) != projection["projection_sha256"]


def test_projection_builder_rejects_unproved_or_mismatched_facts_sha() -> None:
    facts, facts_bytes, _ = _facts()
    changed = deepcopy(facts)
    changed["daily"]["close"] += 0.01

    with pytest.raises(Phase2WorkerProtocolError, match="facts_sha256_mismatch"):
        build_worker_projection(
            facts,
            "0" * 64,
            facts_bytes=facts_bytes,
        )
    with pytest.raises(Phase2WorkerProtocolError, match="facts_bytes_mismatch"):
        build_worker_projection(
            changed,
            hashlib.sha256(facts_bytes).hexdigest(),
            facts_bytes=facts_bytes,
        )


def test_exact_projection_reader_uses_only_allowed_regular_file(tmp_path: Path) -> None:
    projection = _projection()
    allowed = tmp_path / "projection.json"
    allowed.write_text(json.dumps(projection), encoding="utf-8")

    assert read_exact_projection(allowed, allowed) == projection


@pytest.mark.parametrize("candidate_kind", ["sibling", "parent", "etc", "home", "repo"])
def test_projection_reader_rejects_non_exact_paths_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate_kind: str,
) -> None:
    projection = _projection()
    allowed = tmp_path / "projection.json"
    allowed.write_text(json.dumps(projection), encoding="utf-8")
    sibling = tmp_path / "sibling.json"
    sibling.write_text("{}", encoding="utf-8")
    candidates = {
        "sibling": sibling,
        "parent": tmp_path,
        "etc": Path("/etc/hosts"),
        "home": Path.home(),
        "repo": REPO_ROOT / "pyproject.toml",
    }
    opened: list[str] = []
    original_open = os.open

    def tracking_open(path, flags, *args, **kwargs):
        opened.append(os.fspath(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("app.services.phase2_ai_worker_protocol.os.open", tracking_open)

    with pytest.raises(Phase2WorkerProtocolError, match="projection_path_not_allowed"):
        read_exact_projection(candidates[candidate_kind], allowed)
    assert opened == []


def test_projection_reader_rejects_file_and_parent_symlinks(tmp_path: Path) -> None:
    projection = _projection()
    real = tmp_path / "real.json"
    real.write_text(json.dumps(projection), encoding="utf-8")
    symlink = tmp_path / "projection.json"
    symlink.symlink_to(real)

    with pytest.raises(Phase2WorkerProtocolError, match="projection_symlink_forbidden"):
        read_exact_projection(symlink, symlink)

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    nested = real_parent / "projection.json"
    nested.write_text(json.dumps(projection), encoding="utf-8")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(Phase2WorkerProtocolError, match="projection_symlink_forbidden"):
        read_exact_projection(linked_parent / "projection.json", linked_parent / "projection.json")


def test_full_typed_candidate_validates_against_projection() -> None:
    result = validate_worker_candidate(_full_candidate(), _projection())

    assert result.status == WORKER_CANDIDATE_VALID
    assert result.errors == []
    assert result.claim_count == 42
    assert result.ai_call_count == 0
    assert result.provider_attempt_count == 0
    assert result.external_send_count == 0


def test_fake_worker_is_json_only_deterministic_and_has_zero_external_attempts() -> None:
    projection = _projection()
    worker = FakeClaimsWorker()

    first = worker.run(projection)
    second = worker.run(projection)
    parsed = json.loads(first)

    assert first == second
    assert "```" not in first
    assert parsed["projection_sha256"] == projection["projection_sha256"]
    assert len(parsed["claims"]) == 42
    assert validate_worker_candidate(first, projection).status == WORKER_CANDIDATE_VALID
    assert worker.ai_call_count == 0
    assert worker.provider_attempt_count == 0
    assert worker.external_send_count == 0
    assert worker.isolation_design_status == ISOLATION_DESIGN_READY
    assert worker.isolation_runtime_status == ISOLATION_RUNTIME_NOT_YET_VERIFIED


@pytest.mark.parametrize(
    "output",
    [
        "# Markdown\n{}",
        "```json\n{}\n```",
        "not-json",
        "[1, 2, 3]",
    ],
)
def test_worker_output_must_be_one_json_object(output: str) -> None:
    result = validate_worker_candidate(output, _projection())

    assert result.status == WORKER_CANDIDATE_INVALID
    assert "worker_output_not_json_object" in result.errors


def test_candidate_rejects_freeform_and_forbidden_claim_type() -> None:
    projection = _projection()
    freeform = _full_candidate()
    freeform["claims"][0]["analysis"] = "arbitrary prose"
    forbidden = _full_candidate()
    forbidden["claims"][0]["claim_type"] = "TRADE_ACTION"

    assert validate_worker_candidate(freeform, projection).status == WORKER_CANDIDATE_INVALID
    result = validate_worker_candidate(forbidden, projection)
    assert result.status == WORKER_CANDIDATE_INVALID
    assert "worker_candidate_schema_invalid" in result.errors


def test_candidate_rejects_projection_symbol_date_and_hash_mismatch() -> None:
    projection = _projection()
    for field, value in (
        ("symbol", "600489.SH"),
        ("trade_date", "2026-07-30"),
        ("projection_sha256", "0" * 64),
    ):
        candidate = _full_candidate()
        candidate[field] = value
        result = validate_worker_candidate(candidate, projection)
        assert result.status == WORKER_CANDIDATE_INVALID


def test_candidate_requires_complete_predicates_and_deterministic_claim_ids() -> None:
    projection = _projection()
    incomplete = _full_candidate()
    incomplete["claims"] = incomplete["claims"][:-1]
    arbitrary_id = _full_candidate()
    arbitrary_id["claims"][0]["claim_id"] = (
        "000403SZ-20260731-001-arbitrary-channel"
    )

    incomplete_result = validate_worker_candidate(incomplete, projection)
    id_result = validate_worker_candidate(arbitrary_id, projection)

    assert "worker_candidate_predicate_set_invalid" in incomplete_result.errors
    assert "worker_candidate_claim_ids_invalid" in id_result.errors


def test_candidate_rejects_unexpected_fact_pointer() -> None:
    candidate = _full_candidate()
    daily_close = next(
        item for item in candidate["claims"] if item["predicate"] == "daily_close"
    )
    daily_close["provenance"]["fact_refs"] = ["/daily/high"]

    result = validate_worker_candidate(candidate, _projection())

    assert result.status == WORKER_CANDIDATE_INVALID
    assert any("claim_fact_refs_mismatch" in item for item in result.errors)


def test_candidate_rejects_raw_qfq_comparison() -> None:
    candidate = _full_candidate()
    comparison = next(
        item
        for item in candidate["claims"]
        if item["predicate"] == "macd_dif_vs_dea"
    )
    comparison["object"]["left"].update(
        {
            "label_id": "daily_close",
            "fact_ref": "/daily/close",
            "value": 10.43,
            "unit": "raw_price",
            "price_basis": "raw",
        }
    )
    comparison["provenance"]["fact_refs"][0] = "/daily/close"

    result = validate_worker_candidate(candidate, _projection())

    assert result.status == WORKER_CANDIDATE_INVALID
    assert "CLAIM_REJECTED_RAW_QFQ_MISMATCH" in result.errors


def test_isolation_harness_cannot_execute_live_workflows() -> None:
    harness = (REPO_ROOT / "scripts/test_phase2_ai_isolation.sh").read_text(
        encoding="utf-8"
    )

    assert "pytest" in harness
    assert "curl " not in harness
    assert "wget " not in harness
    assert "probe_tickflow" not in harness
    assert "run_phase1_daily_validation" not in harness
    assert "render_phase2_claims.py" not in harness
