from __future__ import annotations

import json

import pytest

from app.providers.ark_provider import (
    ARK_EXACT_MODEL_ID,
    ArkProviderContractError,
    adapt_ark_responses_envelope,
)
from tests.phase2_ark_helpers import candidate, provider_request


def _envelope() -> dict:
    return {
        "id": "resp_ark_mock",
        "object": "response",
        "status": "completed",
        "model": ARK_EXACT_MODEL_ID,
        "output": [
            {
                "id": "msg_ark_mock",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(candidate(), ensure_ascii=False),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def test_adapter_extracts_one_typed_candidate() -> None:
    result = adapt_ark_responses_envelope(
        provider_request(),
        provider_http_status=200,
        content_type="application/json",
        envelope=_envelope(),
        stage_metadata={"response_body_completed": True},
    )
    assert result.claims_candidate.symbol == "000403.SZ"
    assert result.provider_request_id == "resp_ark_mock"
    assert result.raw_envelope_metadata == {
        "model": ARK_EXACT_MODEL_ID,
        "object": "response",
        "status": "completed",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(status="incomplete"),
        lambda value: value.update(model="deepseek-v4-flash-ga-260731"),
        lambda value: value.update(output=[]),
        lambda value: value["output"][0].update(type="function_call"),
        lambda value: value["output"][0]["content"].append({"type": "output_text", "text": "{}"}),
        lambda value: value["output"][0]["content"][0].update(text="not-json"),
    ],
)
def test_adapter_rejects_invalid_or_fallback_envelopes(mutation) -> None:
    value = _envelope()
    mutation(value)
    with pytest.raises(ArkProviderContractError):
        adapt_ark_responses_envelope(
            provider_request(),
            provider_http_status=200,
            content_type="application/json",
            envelope=value,
            stage_metadata={},
        )


def test_adapter_rejects_redirect_and_non_json() -> None:
    for status, content_type in ((302, "application/json"), (200, "text/plain")):
        with pytest.raises(ArkProviderContractError):
            adapt_ark_responses_envelope(
                provider_request(),
                provider_http_status=status,
                content_type=content_type,
                envelope=_envelope(),
                stage_metadata={},
            )
