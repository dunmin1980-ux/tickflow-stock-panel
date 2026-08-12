from __future__ import annotations

import json

import pytest

from app.providers.ark_provider import (
    ARK_EXACT_MODEL_ID,
    ArkProviderContractError,
    build_ark_responses_request,
)
from tests.phase2_ark_helpers import provider_request


def test_ark_request_contract_is_exact_json_schema_only() -> None:
    body = build_ark_responses_request(provider_request())
    assert body["model"] == ARK_EXACT_MODEL_ID
    assert body["store"] is False
    assert body["stream"] is False
    assert body["tools"] == []
    assert body["temperature"] == 0
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"] != provider_request().claims_schema
    assert body["text"]["format"]["schema"]["additionalProperties"] is False
    assert set(body) == {
        "model",
        "store",
        "stream",
        "tools",
        "temperature",
        "input",
        "text",
    }
    serialized = json.dumps(body, ensure_ascii=False)
    assert "json_object" not in serialized
    assert "chat/completions" not in serialized
    assert "600489.SH" not in serialized
    assert "300059.SZ" not in serialized


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "openai"),
        ("exact_model_id", "deepseek-v4-flash-ga-260731"),
        ("endpoint_alias", "ark_chat_completions"),
    ],
)
def test_ark_request_rejects_identity_fallback(field: str, value: str) -> None:
    request = provider_request().model_copy(update={field: value})
    with pytest.raises(ArkProviderContractError):
        build_ark_responses_request(request)
