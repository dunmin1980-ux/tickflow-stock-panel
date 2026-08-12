from __future__ import annotations

from pydantic import ValidationError

from app.providers.base import ProviderResult
from tests.phase2_ark_helpers import candidate, provider_request


def test_provider_request_is_closed_and_immutable() -> None:
    request = provider_request()
    assert request.provider_id == "volcengine_ark"
    try:
        request.symbol = "600489.SH"
    except ValidationError:
        pass
    else:
        raise AssertionError("provider request must be frozen")


def test_provider_result_exposes_only_approved_fields() -> None:
    result = ProviderResult(
        provider_http_status=200,
        provider_request_id="resp_ark_mock",
        raw_envelope_metadata={"object": "response", "status": "completed"},
        claims_candidate=candidate(),
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
        stage_metadata={"response_body_completed": True},
    )
    assert set(result.model_dump()) == {
        "provider_http_status",
        "provider_request_id",
        "raw_envelope_metadata",
        "claims_candidate",
        "usage_metadata",
        "stage_metadata",
    }


def test_provider_result_rejects_adapter_side_effect_fields() -> None:
    payload = {
        "provider_http_status": 200,
        "provider_request_id": "resp_ark_mock",
        "raw_envelope_metadata": {},
        "claims_candidate": candidate(),
        "usage_metadata": {},
        "stage_metadata": {},
        "markdown": "forbidden",
    }
    try:
        ProviderResult.model_validate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("provider result accepted an adapter side effect")
