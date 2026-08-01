from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.phase2_claims import ALLOWED_CLAIM_TYPES, WorkerClaimsCandidate
from app.schemas.phase2_provider_relay import GenerationControls
from app.services.phase2_ai_worker_protocol import (
    FakeClaimsWorker,
    build_worker_projection,
    compute_projection_sha256,
)
from app.services.phase2_claims_service import PREDICATE_RULES
from app.services.phase2_openai_proxy_contract import (
    OpenAIProxyPolicy,
    build_proxy_contract,
    canonical_contract_payload,
    canonical_proxy_contract_bytes,
    main,
    proxy_policy_sha256,
    strict_worker_schema,
    validate_proxy_contract,
)
from app.services.phase2_provider_relay_protocol import (
    build_provider_envelope,
    build_relay_request,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY_PATH = REPO_ROOT / "docker/phase2-openai-egress-proxy/proxy.py"
CONTRACT_PATH = (
    REPO_ROOT / "docker/phase2-openai-egress-proxy/responses-contract.json"
)
FACTS_PATH = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"
FIXED_REQUEST_ID = "a" * 32

EXPECTED_POLICY = {
    "provider_id": "openai",
    "model_id": "gpt-5.6-terra",
    "endpoint_host": "api.openai.com",
    "endpoint_port": 443,
    "endpoint_path": "/v1/responses",
    "http_method": "POST",
    "tls_verification": True,
    "minimum_tls_version": "TLSv1_2",
    "follow_redirects": False,
    "stream": False,
    "tools": [],
    "retry_count": 0,
    "maximum_attempts": 1,
    "timeout_seconds": 60,
    "maximum_bytes": 1_048_576,
    "approved_symbol": "000403.SZ",
}


def test_proxy_policy_is_closed_and_exact() -> None:
    assert OpenAIProxyPolicy().model_dump(mode="json") == EXPECTED_POLICY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "other"),
        ("model_id", "latest"),
        ("endpoint_host", "example.com"),
        ("endpoint_port", 80),
        ("endpoint_path", "/v1/chat/completions"),
        ("http_method", "GET"),
        ("tls_verification", False),
        ("minimum_tls_version", "TLSv1_1"),
        ("follow_redirects", True),
        ("stream", True),
        ("tools", ["web_search"]),
        ("retry_count", 1),
        ("maximum_attempts", 2),
        ("timeout_seconds", 30),
        ("maximum_bytes", 2_048_576),
        ("approved_symbol", "600489.SH"),
    ],
)
def test_proxy_policy_rejects_every_mutation(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        OpenAIProxyPolicy.model_validate({**EXPECTED_POLICY, field: value})


def test_proxy_policy_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        OpenAIProxyPolicy.model_validate({**EXPECTED_POLICY, "url": "https://example.com"})


def test_contract_bytes_are_deterministic_and_schema_bound() -> None:
    first = canonical_proxy_contract_bytes()
    second = canonical_proxy_contract_bytes()
    contract = json.loads(first)

    assert first == second
    assert first.endswith(b"\n")
    assert contract["response_format"] == {
        "type": "json_schema",
        "name": "tickflow_phase2_claims_candidate",
        "strict": True,
        "schema": strict_worker_schema(),
    }
    recorded = contract.pop("contract_sha256")
    assert hashlib.sha256(canonical_contract_payload(contract)).hexdigest() == recorded
    assert contract["relay_contract"] == {
        "protocol_version": 1,
        "claims_schema_version": 1,
        "response_format": "typed_claims_json",
        "approved_symbol": "000403.SZ",
        "approved_trade_date": "2026-07-31",
        "allowed_claim_types": sorted(ALLOWED_CLAIM_TYPES),
        "allowed_predicates": sorted(PREDICATE_RULES),
        "generation": GenerationControls().model_dump(mode="json"),
    }


def test_contract_validation_rejects_self_hash_drift() -> None:
    value = build_proxy_contract()
    value["contract_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="proxy_contract_sha256_mismatch"):
        validate_proxy_contract(value)


def test_policy_hash_is_hash_of_canonical_policy() -> None:
    expected = hashlib.sha256(canonical_contract_payload(EXPECTED_POLICY)).hexdigest()
    assert proxy_policy_sha256() == expected


def test_contract_cli_writes_checks_and_rejects_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    target = repository / "docker/phase2-openai-egress-proxy/responses-contract.json"
    target.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "app.services.phase2_openai_proxy_contract.repo_root",
        lambda: repository,
    )

    assert main([]) == 2
    assert main(["--write"]) == 0
    assert target.read_bytes() == canonical_proxy_contract_bytes()
    assert main(["--check"]) == 0

    target.unlink()
    target.symlink_to(tmp_path / "outside.json")
    assert main(["--write"]) == 2
    assert not (tmp_path / "outside.json").exists()


def test_contract_cli_rejects_mutually_exclusive_modes() -> None:
    with pytest.raises(SystemExit):
        main(["--write", "--check"])


@pytest.fixture(scope="module")
def proxy_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase2_openai_proxy_under_test",
        PROXY_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def contract(proxy_module: ModuleType) -> dict[str, Any]:
    return proxy_module.load_contract(str(CONTRACT_PATH))


@pytest.fixture(scope="module")
def projection() -> dict[str, Any]:
    facts_bytes = FACTS_PATH.read_bytes()
    facts = json.loads(facts_bytes)
    return build_worker_projection(
        facts,
        hashlib.sha256(facts_bytes).hexdigest(),
        facts_bytes=facts_bytes,
    )


@pytest.fixture(scope="module")
def relay_envelope(projection: dict[str, Any]) -> dict[str, Any]:
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    return build_provider_envelope(request, projection).model_dump(mode="json")


@pytest.fixture(scope="module")
def valid_candidate(projection: dict[str, Any]) -> dict[str, Any]:
    return json.loads(FakeClaimsWorker().run(projection))


def _responses_fixture(
    candidate: dict[str, Any],
    *,
    include_reasoning: bool = True,
) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    if include_reasoning:
        output.append({"type": "reasoning", "id": "rs_test", "summary": []})
    output.append(
        {
            "type": "message",
            "id": "msg_test",
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": json.dumps(
                        candidate,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                    ),
                    "annotations": [],
                    "logprobs": [],
                }
            ],
        }
    )
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "completed_at": 1,
        "error": None,
        "incomplete_details": None,
        "model": "gpt-5.6-terra",
        "output": output,
        "tools": [],
    }


def test_provider_request_is_exact_and_projection_only(
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    payload = proxy_module.build_provider_request(relay_envelope, contract)

    assert set(payload) == {"model", "stream", "tools", "input", "text"}
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["stream"] is False
    assert payload["tools"] == []
    assert payload["text"]["format"] == contract["response_format"]
    assert payload["input"] == [
        {
            "role": "developer",
            "content": [
                {
                    "type": "input_text",
                    "text": "\n".join(contract["fixed_instructions"]),
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": json.dumps(
                        relay_envelope["projection"],
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ],
        },
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "000403.SZ" in serialized
    assert "reports/phase2_facts" not in serialized
    assert str(REPO_ROOT) not in serialized
    assert str(Path.home()) not in serialized
    assert "temperature" not in payload
    assert "store" not in payload


def test_projection_hash_uses_host_canonical_encoding(
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    validated = proxy_module.validate_relay_envelope(relay_envelope, contract)
    projection = validated["projection"]

    assert proxy_module.projection_sha256(projection) == compute_projection_sha256(
        projection
    )
    assert projection["projection_sha256"] == compute_projection_sha256(projection)


@pytest.mark.parametrize(
    "case",
    [
        "unknown_top_level",
        "wrong_symbol",
        "wrong_projection_sha",
        "wrong_facts_sha",
        "wrong_response_format",
        "altered_claim_types",
        "altered_predicates",
        "tool_enabled",
        "web_enabled",
        "file_enabled",
        "function_enabled",
        "streaming_enabled",
        "retry_enabled",
        "maximum_bytes_changed",
        "timeout_changed",
        "non_finite",
    ],
)
def test_relay_envelope_mutations_fail_closed_before_transport(
    case: str,
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    value = deepcopy(relay_envelope)
    if case == "unknown_top_level":
        value["provider_url"] = "https://example.invalid"
    elif case == "wrong_symbol":
        value["request"]["symbol"] = "600489.SH"
    elif case == "wrong_projection_sha":
        value["request"]["projection_sha256"] = "0" * 64
    elif case == "wrong_facts_sha":
        value["projection"]["facts_sha256"] = "0" * 64
    elif case == "wrong_response_format":
        value["request"]["response_format"] = "markdown"
    elif case == "altered_claim_types":
        value["request"]["allowed_claim_types"] = ["NUMERIC_OBSERVATION"]
    elif case == "altered_predicates":
        value["projection"]["allowed_predicates"] = ["daily_close"]
    elif case == "tool_enabled":
        value["generation"]["tool_use"] = True
    elif case == "web_enabled":
        value["generation"]["web_browsing"] = True
    elif case == "file_enabled":
        value["generation"]["file_tools"] = True
    elif case == "function_enabled":
        value["generation"]["function_calling"] = True
    elif case == "streaming_enabled":
        value["generation"]["streaming"] = True
    elif case == "retry_enabled":
        value["generation"]["retry_count"] = 1
    elif case == "maximum_bytes_changed":
        value["generation"]["maximum_output_bytes"] = 2_048_576
    elif case == "timeout_changed":
        value["generation"]["timeout_seconds"] = 60
    elif case == "non_finite":
        value["projection"]["safe_facts"]["daily"]["open"] = float("nan")
    else:  # pragma: no cover - parameter list is closed above
        raise AssertionError(case)

    with pytest.raises(proxy_module.ProxyError) as raised:
        proxy_module.validate_relay_envelope(value, contract)
    assert raised.value.category == "REQUEST_SCHEMA_BLOCKED"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"request": {}} trailing',
        b'{"request": {}}{}',
        b'{"request": NaN}',
        b"[]",
        b"",
    ],
)
def test_strict_object_rejects_non_object_or_non_strict_json(
    raw: bytes,
    proxy_module: ModuleType,
) -> None:
    with pytest.raises(proxy_module.ProxyError) as raised:
        proxy_module.strict_object(raw, "REQUEST_SCHEMA_BLOCKED")
    assert raised.value.category == "REQUEST_SCHEMA_BLOCKED"


def test_strict_object_rejects_oversized_input(proxy_module: ModuleType) -> None:
    with pytest.raises(proxy_module.ProxyError) as raised:
        proxy_module.strict_object(
            b"{" + b" " * proxy_module.MAXIMUM_BYTES + b"}",
            "REQUEST_SCHEMA_BLOCKED",
        )
    assert raised.value.category == "REQUEST_SCHEMA_BLOCKED"


@pytest.mark.parametrize("include_reasoning", [False, True])
def test_response_extracts_one_valid_candidate_unchanged(
    include_reasoning: bool,
    proxy_module: ModuleType,
    valid_candidate: dict[str, Any],
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    response = _responses_fixture(
        valid_candidate,
        include_reasoning=include_reasoning,
    )

    extracted = proxy_module.extract_candidate(response, contract, relay_envelope)

    assert extracted == valid_candidate
    assert WorkerClaimsCandidate.model_validate(extracted).model_dump(mode="json") == (
        valid_candidate
    )


@pytest.mark.parametrize(
    ("case", "category"),
    [
        ("wrong_object", "OUTPUT_EXTRACTION_BLOCKED"),
        ("wrong_status", "OUTPUT_EXTRACTION_BLOCKED"),
        ("wrong_model", "MODEL_IDENTITY_BLOCKED"),
        ("error", "OUTPUT_EXTRACTION_BLOCKED"),
        ("incomplete", "OUTPUT_EXTRACTION_BLOCKED"),
        ("missing_output", "OUTPUT_EXTRACTION_BLOCKED"),
        ("two_messages", "OUTPUT_EXTRACTION_BLOCKED"),
        ("tool_item", "OUTPUT_EXTRACTION_BLOCKED"),
        ("malformed_reasoning", "OUTPUT_EXTRACTION_BLOCKED"),
        ("two_content_items", "OUTPUT_EXTRACTION_BLOCKED"),
        ("refusal", "OUTPUT_EXTRACTION_BLOCKED"),
        ("non_output_text", "OUTPUT_EXTRACTION_BLOCKED"),
        ("annotations", "OUTPUT_EXTRACTION_BLOCKED"),
        ("logprobs", "OUTPUT_EXTRACTION_BLOCKED"),
        ("empty_text", "RESPONSE_JSON_BLOCKED"),
        ("markdown", "RESPONSE_JSON_BLOCKED"),
        ("trailing_json", "RESPONSE_JSON_BLOCKED"),
        ("list_output", "RESPONSE_JSON_BLOCKED"),
    ],
)
def test_response_envelope_mutations_fail_closed(
    case: str,
    category: str,
    proxy_module: ModuleType,
    valid_candidate: dict[str, Any],
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    response = _responses_fixture(valid_candidate)
    message = response["output"][-1]
    content = message["content"][0]
    if case == "wrong_object":
        response["object"] = "chat.completion"
    elif case == "wrong_status":
        response["status"] = "in_progress"
    elif case == "wrong_model":
        response["model"] = "other-model"
    elif case == "error":
        response["error"] = {"code": "test"}
    elif case == "incomplete":
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    elif case == "missing_output":
        response.pop("output")
    elif case == "two_messages":
        response["output"].append(deepcopy(message))
    elif case == "tool_item":
        response["output"].insert(0, {"type": "function_call", "id": "fc"})
    elif case == "malformed_reasoning":
        response["output"][0]["encrypted_content"] = "not-allowed"
    elif case == "two_content_items":
        message["content"].append(deepcopy(content))
    elif case == "refusal":
        message["content"] = [{"type": "refusal", "refusal": "no"}]
    elif case == "non_output_text":
        content["type"] = "input_text"
    elif case == "annotations":
        content["annotations"] = [{"type": "citation"}]
    elif case == "logprobs":
        content["logprobs"] = [{"token": "x"}]
    elif case == "empty_text":
        content["text"] = ""
    elif case == "markdown":
        content["text"] = "```json\n{}\n```"
    elif case == "trailing_json":
        content["text"] += "{}"
    elif case == "list_output":
        content["text"] = "[]"
    else:  # pragma: no cover - parameter list is closed above
        raise AssertionError(case)

    with pytest.raises(proxy_module.ProxyError) as raised:
        proxy_module.extract_candidate(response, contract, relay_envelope)
    assert raised.value.category == category


@pytest.mark.parametrize(
    "case",
    [
        "unknown_root_field",
        "wrong_symbol",
        "wrong_trade_date",
        "wrong_facts_sha",
        "wrong_projection_sha",
        "trading_advice",
        "unknown_claim_type",
        "unknown_predicate",
        "unknown_pointer",
        "unknown_unit",
        "unknown_price_basis",
        "non_finite_number",
        "nested_unknown_field",
    ],
)
def test_candidate_mutations_match_pydantic_or_stricter_binding(
    case: str,
    proxy_module: ModuleType,
    valid_candidate: dict[str, Any],
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    candidate = deepcopy(valid_candidate)
    claim = candidate["claims"][0]
    if case == "unknown_root_field":
        candidate["free_text"] = "not allowed"
    elif case == "wrong_symbol":
        candidate["symbol"] = "600489.SH"
    elif case == "wrong_trade_date":
        candidate["trade_date"] = "2026-07-30"
    elif case == "wrong_facts_sha":
        for item in candidate["claims"]:
            item["provenance"]["facts_sha256"] = "0" * 64
    elif case == "wrong_projection_sha":
        candidate["projection_sha256"] = "0" * 64
    elif case == "trading_advice":
        candidate["trading_advice"] = True
    elif case == "unknown_claim_type":
        claim["claim_type"] = "TRADE_ACTION"
    elif case == "unknown_predicate":
        claim["predicate"] = "buy_now"
    elif case == "unknown_pointer":
        claim["provenance"]["fact_refs"] = ["/not/a/projection/fact"]
    elif case == "unknown_unit":
        claim["object"]["unit"] = "shares"
    elif case == "unknown_price_basis":
        claim["provenance"]["price_basis"] = "mixed"
    elif case == "non_finite_number":
        numeric = next(
            item
            for item in candidate["claims"]
            if item["claim_type"] == "NUMERIC_OBSERVATION"
        )
        numeric["object"]["value"] = float("inf")
    elif case == "nested_unknown_field":
        claim["subject"]["account"] = "private"
    else:  # pragma: no cover - parameter list is closed above
        raise AssertionError(case)

    if case == "non_finite_number":
        with pytest.raises(proxy_module.ProxyError) as raised:
            proxy_module.validate_candidate(
                candidate,
                contract["response_format"]["schema"],
            )
        assert raised.value.category == "RESPONSE_SCHEMA_BLOCKED"
        return

    response = _responses_fixture(candidate)
    with pytest.raises(proxy_module.ProxyError) as raised:
        proxy_module.extract_candidate(response, contract, relay_envelope)
    assert raised.value.category == "RESPONSE_SCHEMA_BLOCKED"


def test_schema_validator_rejects_unsupported_and_remote_schema_keywords(
    proxy_module: ModuleType,
) -> None:
    for schema in (
        {"type": "string", "description": "not approved"},
        {"$ref": "https://example.invalid/schema.json"},
        {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"},
    ):
        with pytest.raises(proxy_module.ProxyError) as raised:
            proxy_module.validate_candidate("value", schema)
        assert raised.value.category == "RESPONSE_SCHEMA_BLOCKED"


def test_schema_validator_rejects_boolean_as_number(proxy_module: ModuleType) -> None:
    with pytest.raises(proxy_module.ProxyError) as raised:
        proxy_module.validate_candidate(True, {"type": "number"})
    assert raised.value.category == "RESPONSE_SCHEMA_BLOCKED"
