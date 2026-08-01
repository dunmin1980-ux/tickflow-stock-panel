from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.phase2_claims import (
    ALLOWED_CLAIM_TYPES,
    FORBIDDEN_CLAIM_TYPES,
    ClaimsDocument,
    claims_json_schema,
)
from app.services.phase2_claims_service import (
    CALCULATION_REGISTRY,
    build_claims_document,
    build_claims_fixtures,
    execute_calculation,
    publish_claims_fixtures,
    validate_claims_directory,
    validate_claims_document,
)

FACTS_SHA256 = "a" * 64
REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _numeric_claim() -> dict:
    return {
        "claim_id": "000403SZ-20260731-010-daily-close",
        "claim_type": "NUMERIC_OBSERVATION",
        "subject": {
            "entity_type": "stock",
            "symbol": "000403.SZ",
        },
        "predicate": "daily_close",
        "object": {
            "value_type": "number",
            "value": 10.43,
            "unit": "raw_price",
        },
        "provenance": {
            "facts_sha256": FACTS_SHA256,
            "fact_refs": ["/daily/close"],
            "calculation_ref": None,
            "price_basis": "raw",
        },
        "rendering": {
            "template_id": "daily_close_v1",
            "display_precision": 2,
        },
        "scope": {
            "timeframe": "1d",
            "as_of": "2026-07-31",
        },
        "validation_status": "VALIDATED",
    }


def _document() -> dict:
    return {
        "claims_schema_version": 1,
        "source_system": "tickflow-stock-panel",
        "symbol": "000403.SZ",
        "name": "派林生物",
        "trade_date": "2026-07-31",
        "timezone": "Asia/Shanghai",
        "facts_binding": {
            "facts_file": "reports/phase2_facts/000403SZ_facts.json",
            "facts_sha256": FACTS_SHA256,
            "facts_schema_version": 1,
        },
        "scope": {
            "market_scope": "incomplete",
            "financial_scope": "unavailable",
            "news_scope": "unavailable",
            "industry_scope": "unavailable",
        },
        "claims": [_numeric_claim()],
        "vendor_pending": [
            "intraday_batch_entitlement",
            "first_30m_bucket_includes_09_30",
            "volume_unit",
            "amount_unit",
        ],
        "trading_advice": False,
    }


def test_minimal_numeric_claim_document_is_strictly_valid() -> None:
    document = ClaimsDocument.model_validate(_document())

    assert document.claims_schema_version == 1
    assert document.claims[0].claim_type == "NUMERIC_OBSERVATION"
    assert document.claims[0].object.value == 10.43


@pytest.mark.parametrize(
    "freeform_field",
    [
        "conclusion",
        "analysis",
        "reason",
        "recommendation",
        "outlook",
        "human_note",
        "debug_note",
        "validator_message",
        "human_review_note",
    ],
)
def test_publishable_claim_rejects_every_freeform_field(
    freeform_field: str,
) -> None:
    payload = _document()
    payload["claims"][0][freeform_field] = "arbitrary prose"

    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)


@pytest.mark.parametrize(
    "forbidden_type",
    [
        "RECOMMENDATION",
        "TRADE_ACTION",
        "POSITION_SIZING",
        "BUY_SELL_SIGNAL",
        "TARGET_PRICE",
        "STOP_LOSS",
        "FORECAST",
        "PREDICTION",
        "CATALYST",
        "NEWS_INTERPRETATION",
        "FINANCIAL_INTERPRETATION",
        "INDUSTRY_RANKING",
        "SUPPORT_LEVEL",
        "RESISTANCE_LEVEL",
    ],
)
def test_forbidden_claim_types_are_not_parseable(forbidden_type: str) -> None:
    payload = _document()
    payload["claims"][0]["claim_type"] = forbidden_type

    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_numeric_claim_rejects_non_finite_values(value: float) -> None:
    payload = _document()
    payload["claims"][0]["object"]["value"] = value

    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)


def test_document_rejects_true_trading_advice() -> None:
    payload = _document()
    payload["trading_advice"] = True

    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)


@pytest.mark.parametrize(
    "claim_id",
    [
        "000403.SZ-20260731-close",
        "000403SZ-2026-07-31-close",
        "000403SZ-20260731-daily-close",
        "../../etc/passwd",
    ],
)
def test_claim_id_has_fixed_symbol_date_sequence_shape(claim_id: str) -> None:
    payload = _document()
    payload["claims"][0]["claim_id"] = claim_id

    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)


def test_schema_has_eight_allowed_discriminated_claim_types() -> None:
    schema = claims_json_schema()
    serialized = str(schema)

    assert {
        "NUMERIC_OBSERVATION",
        "BOOLEAN_STATE",
        "ENUM_STATE",
        "SAME_BASIS_COMPARISON",
        "ORDERED_RELATION",
        "SCOPE_NOTICE",
        "VENDOR_PENDING_NOTICE",
        "DATA_QUALITY_STATE",
    } == ALLOWED_CLAIM_TYPES
    assert len(ALLOWED_CLAIM_TYPES) == 8
    assert len(FORBIDDEN_CLAIM_TYPES) == 14
    assert "discriminator" in serialized
    assert not (FORBIDDEN_CLAIM_TYPES & ALLOWED_CLAIM_TYPES)


def test_nested_models_reject_unknown_fields() -> None:
    payload = deepcopy(_document())
    payload["claims"][0]["object"]["markdown"] = "[link](https://example.com)"

    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)


def _built_payload(symbol: str = "000403.SZ") -> dict:
    return build_claims_document(REPO_ROOT, symbol).model_dump(mode="json")


def _claim(payload: dict, predicate: str) -> dict:
    return next(item for item in payload["claims"] if item["predicate"] == predicate)


def test_calculation_registry_is_closed_and_deterministic() -> None:
    assert set(CALCULATION_REGISTRY) == {
        "compare_numbers_v1",
        "ordered_relation_v1",
        "difference_v1",
        "percentage_difference_v1",
        "threshold_compare_v1",
        "normalize_scope_v1",
    }
    assert execute_calculation(
        "compare_numbers_v1",
        [10.0, 9.0],
        price_bases=["qfq", "qfq"],
        units=["qfq_price", "qfq_price"],
    ) == "ABOVE"
    assert execute_calculation(
        "difference_v1",
        [10.0, 9.0],
        price_bases=["raw", "raw"],
        units=["raw_price", "raw_price"],
    ) == 1.0
    assert execute_calculation(
        "percentage_difference_v1",
        [10.36, 10.43],
        price_bases=["raw", "raw"],
        units=["raw_price", "raw_price"],
    ) == pytest.approx(0.6756756756756757, abs=1e-14)
    assert execute_calculation(
        "threshold_compare_v1",
        [50.0, 50.0],
        price_bases=["none", "none"],
        units=["dimensionless", "dimensionless"],
    ) == "EQUAL_WITHIN_EPSILON"
    assert execute_calculation(
        "normalize_scope_v1",
        ["manual_verification_required"],
        price_bases=["none"],
        units=["none"],
    ) == "unavailable"


def test_unknown_calculation_and_cross_basis_execution_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown calculation"):
        execute_calculation(
            "__import__('os').system('id')",
            [1, 2],
            price_bases=["none", "none"],
            units=["count", "count"],
        )
    with pytest.raises(ValueError, match="CLAIM_REJECTED_RAW_QFQ_MISMATCH"):
        execute_calculation(
            "compare_numbers_v1",
            [10.43, 10.136],
            price_bases=["raw", "qfq"],
            units=["raw_price", "qfq_price"],
        )


@pytest.mark.parametrize("symbol", ["000403.SZ", "600489.SH", "300059.SZ"])
def test_deterministic_fixture_is_fully_bound_and_valid(symbol: str) -> None:
    document = build_claims_document(REPO_ROOT, symbol)
    result = validate_claims_document(REPO_ROOT, document)

    assert result.status == "CLAIMS_VALID"
    assert result.errors == []
    assert result.claim_count == len(document.claims)
    assert result.facts_pointer_binding_count >= result.claim_count
    assert result.free_text_field_count == 0
    assert result.unsourced_claim_count == 0
    assert result.trading_claim_count == 0
    assert result.raw_qfq_mismatch_count == 0
    assert result.sensitive_hit_count == 0
    assert result.can_publish is False


def test_fixture_uses_literal_facts_and_hand_checked_percentage() -> None:
    payload = _built_payload()

    assert _claim(payload, "daily_close")["object"] == {
        "value_type": "number",
        "value": 10.43,
        "unit": "raw_price",
    }
    assert _claim(payload, "daily_open_to_close_percent")["object"][
        "value"
    ] == pytest.approx(0.6756756756756757, abs=1e-14)


def test_fixture_covers_required_claim_families_without_key_levels() -> None:
    payload = _built_payload()
    predicates = {item["predicate"] for item in payload["claims"]}

    assert {
        "daily_close",
        "daily_open_to_close_percent",
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "macd_dif",
        "macd_dea",
        "macd_hist",
        "rsi6",
        "rsi14",
        "boll_upper",
        "boll_middle",
        "boll_lower",
        "atr14",
        "minute_1m_contract_status",
        "minute_30m_contract_status",
        "minute_1m_material_ohlc_anomaly_count",
        "adjustment_factor_status",
        "macd_dif_vs_dea",
        "ma_value_order",
        "vendor_pending",
    } <= predicates
    assert not any("support" in item or "resistance" in item for item in predicates)


def test_every_claim_binds_the_document_facts_sha_and_existing_pointer() -> None:
    document = build_claims_document(REPO_ROOT, "600489.SH")
    result = validate_claims_document(REPO_ROOT, document)

    assert result.status == "CLAIMS_VALID"
    assert all(
        claim.provenance.facts_sha256 == document.facts_binding.facts_sha256
        for claim in document.claims
    )
    assert result.facts_pointer_binding_count == sum(
        len(claim.provenance.fact_refs) for claim in document.claims
    )


def test_facts_hash_tampering_invalidates_the_whole_document() -> None:
    payload = _built_payload()
    payload["facts_binding"]["facts_sha256"] = "0" * 64

    result = validate_claims_document(REPO_ROOT, payload)

    assert result.status == "CLAIMS_INVALID"
    assert "facts_sha256_mismatch" in result.errors


def test_unresolved_pointer_and_wrong_value_are_rejected() -> None:
    payload = _built_payload()
    close = _claim(payload, "daily_close")
    close["provenance"]["fact_refs"] = ["/daily/missing"]
    close["object"]["value"] = 99.99

    result = validate_claims_document(REPO_ROOT, payload)

    assert result.status == "CLAIMS_INVALID"
    assert any(error.startswith("facts_pointer_invalid:") for error in result.errors)
    assert "claim_value_mismatch:daily_close" in result.errors


def test_unit_and_template_must_match_the_predicate_rule() -> None:
    payload = _built_payload()
    close = _claim(payload, "daily_close")
    close["object"]["unit"] = "qfq_price"
    close["rendering"]["template_id"] = "indicator_numeric_v1"

    result = validate_claims_document(REPO_ROOT, payload)

    assert result.status == "CLAIMS_INVALID"
    assert "claim_unit_mismatch:daily_close" in result.errors
    assert "claim_template_mismatch:daily_close" in result.errors


def test_raw_qfq_comparison_is_rejected_with_explicit_status() -> None:
    payload = _built_payload("300059.SZ")
    comparison = _claim(payload, "macd_dif_vs_dea")
    comparison["object"]["left"]["price_basis"] = "raw"

    result = validate_claims_document(REPO_ROOT, payload)

    assert result.status == "CLAIMS_INVALID"
    assert "CLAIM_REJECTED_RAW_QFQ_MISMATCH" in result.errors
    assert result.raw_qfq_mismatch_count >= 1


def test_duplicate_or_unsorted_claim_ids_fail_closed() -> None:
    duplicate = _built_payload()
    duplicate["claims"][1]["claim_id"] = duplicate["claims"][0]["claim_id"]
    duplicate_result = validate_claims_document(REPO_ROOT, duplicate)

    unsorted = _built_payload()
    unsorted["claims"][0], unsorted["claims"][1] = (
        unsorted["claims"][1],
        unsorted["claims"][0],
    )
    unsorted_result = validate_claims_document(REPO_ROOT, unsorted)

    assert "claim_id_duplicate" in duplicate_result.errors
    assert "claim_order_invalid" in unsorted_result.errors


def test_scope_and_vendor_pending_are_exact_and_complete() -> None:
    payload = _built_payload()
    payload["scope"]["industry_scope"] = "incomplete"
    payload["vendor_pending"] = payload["vendor_pending"][:-1]

    result = validate_claims_document(REPO_ROOT, payload)

    assert result.status == "CLAIMS_INVALID"
    assert "claims_schema_invalid" in result.errors


def test_repeated_build_and_validation_have_stable_normalized_hashes() -> None:
    first = build_claims_fixtures(REPO_ROOT)
    second = build_claims_fixtures(REPO_ROOT)

    assert list(first) == ["000403.SZ", "600489.SH", "300059.SZ"]
    for symbol in first:
        first_result = validate_claims_document(REPO_ROOT, first[symbol])
        second_result = validate_claims_document(REPO_ROOT, second[symbol])
        assert first[symbol] == second[symbol]
        assert first_result.normalized_sha256 == second_result.normalized_sha256


def test_fixture_publication_creates_exact_valid_artifact_set(tmp_path) -> None:
    manifest = publish_claims_fixtures(
        REPO_ROOT,
        tmp_path,
        _allow_test_output_root=True,
    )

    claims_root = tmp_path / "phase2_claims"
    assert manifest["status"] == "CLAIMS_VALID"
    assert manifest["new_tickflow_api_request_count"] == 0
    assert manifest["new_ai_call_count"] == 0
    assert manifest["new_provider_attempt_count"] == 0
    assert sorted(path.name for path in (claims_root / "fixtures").iterdir()) == [
        "000403SZ_claims.json",
        "300059SZ_claims.json",
        "600489SH_claims.json",
    ]
    assert (claims_root / "schema/phase2_claims.schema.json").is_file()
    assert (claims_root / "claims_index.json").is_file()
    assert validate_claims_directory(REPO_ROOT, claims_root)["status"] == (
        "CLAIMS_VALID"
    )


@pytest.mark.parametrize("tamper", ["fixture", "schema", "extra_file"])
def test_claims_directory_tampering_fails_closed(tmp_path, tamper: str) -> None:
    publish_claims_fixtures(
        REPO_ROOT,
        tmp_path,
        _allow_test_output_root=True,
    )
    claims_root = tmp_path / "phase2_claims"
    if tamper == "fixture":
        fixture = claims_root / "fixtures/000403SZ_claims.json"
        payload = __import__("json").loads(fixture.read_text())
        _claim(payload, "daily_close")["object"]["value"] = 99.99
        fixture.write_text(__import__("json").dumps(payload), encoding="utf-8")
    elif tamper == "schema":
        (claims_root / "schema/phase2_claims.schema.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
    else:
        (claims_root / "unexpected.json").write_text("{}\n", encoding="utf-8")

    result = validate_claims_directory(REPO_ROOT, claims_root)

    assert result["status"] == "CLAIMS_INVALID"
    assert result["errors"]
