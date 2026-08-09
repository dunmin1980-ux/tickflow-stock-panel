from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import socket
import ssl
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.phase2_claims import ALLOWED_CLAIM_TYPES, WorkerClaimsCandidate
from app.services.phase2_ai_worker_protocol import (
    FakeClaimsWorker,
    build_worker_projection,
    compute_projection_sha256,
)
from app.services.phase2_canary_runtime_contract import (
    load_runtime_contract,
    runtime_contract_sha256,
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
RUNTIME_CONTRACT_PATH = (
    REPO_ROOT / "docker/phase2-openai-egress-proxy/runtime-contract.json"
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
    "store": False,
    "tools": [],
    "retry_count": 0,
    "maximum_attempts": 1,
    "connect_timeout_seconds": 10,
    "read_timeout_seconds": 60,
    "total_timeout_seconds": 60,
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
        ("store", True),
        ("tools", ["web_search"]),
        ("retry_count", 1),
        ("maximum_attempts", 2),
        ("connect_timeout_seconds", 30),
        ("read_timeout_seconds", 30),
        ("total_timeout_seconds", 30),
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
        "generation": {
            "temperature": 0,
            "response_format": "typed_claims_json",
            "tool_use": False,
            "web_browsing": False,
            "file_tools": False,
            "function_calling": False,
            "streaming": False,
            "retry_count": 0,
            "maximum_output_bytes": 1_048_576,
            "timeout_seconds": 75,
        },
    }
    assert contract["runtime_contract_sha256"] == runtime_contract_sha256()


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
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    module.RUNTIME_CONTRACT_PATH = RUNTIME_CONTRACT_PATH
    return module


@pytest.fixture(scope="module")
def contract(proxy_module: ModuleType) -> dict[str, Any]:
    return proxy_module.load_contract(
        str(CONTRACT_PATH),
        str(RUNTIME_CONTRACT_PATH),
    )


@pytest.fixture(scope="module")
def runtime_contract() -> dict[str, Any]:
    return load_runtime_contract(RUNTIME_CONTRACT_PATH).model_dump(mode="json")


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
def relay_envelope(
    projection: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    request = build_relay_request(projection, request_id=FIXED_REQUEST_ID)
    envelope = build_provider_envelope(request, projection).model_dump(mode="json")
    envelope["generation"] = contract["relay_contract"]["generation"]
    return envelope


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

    assert set(payload) == {
        "model",
        "stream",
        "store",
        "tools",
        "input",
        "text",
    }
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["stream"] is False
    assert payload["store"] is False
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
        value["generation"]["timeout_seconds"] = 2
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


class _FakeHTTPSResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
        content_length: str | None = None,
        on_read: Callable[[], None] | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.read_limits: list[int] = []
        self.on_read = on_read

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name)

    def read(self, amount: int) -> bytes:
        self.read_limits.append(amount)
        if self.on_read is not None:
            self.on_read()
        return self.body[:amount]


class _FakeSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)


class _FakeHTTPSConnection:
    def __init__(
        self,
        response: _FakeHTTPSResponse,
        *,
        request_error: BaseException | None = None,
        drop_socket_on_response: bool = False,
    ) -> None:
        self.response = response
        self.request_error = request_error
        self.drop_socket_on_response = drop_socket_on_response
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.getresponse_count = 0
        self.close_count = 0
        self.connect_count = 0
        self.sock = _FakeSocket()

    def connect(self) -> None:
        self.connect_count += 1

    def request(
        self,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        self.requests.append((method, path, body, headers))
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self) -> _FakeHTTPSResponse:
        self.getresponse_count += 1
        if self.drop_socket_on_response:
            self.sock = None
        return self.response

    def close(self) -> None:
        self.close_count += 1


class _FakeConnectionFactory:
    def __init__(self, connection: _FakeHTTPSConnection) -> None:
        self.connection = connection
        self.created: list[tuple[str, int, float]] = []
        self.contexts: list[ssl.SSLContext] = []

    def __call__(
        self,
        host: str,
        port: int,
        timeout: float,
        context: ssl.SSLContext,
    ) -> _FakeHTTPSConnection:
        self.created.append((host, port, timeout))
        self.contexts.append(context)
        return self.connection


def _provider_response_bytes(candidate: dict[str, Any]) -> bytes:
    return json.dumps(
        _responses_fixture(candidate),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    ).encode("utf-8")


def _relay_body(envelope: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def test_transport_uses_one_verified_https_request(
    proxy_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_body = b'{"status":"synthetic"}'
    response = _FakeHTTPSResponse(response_body)
    connection = _FakeHTTPSConnection(response)
    factory = _FakeConnectionFactory(connection)
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(proxy_module, "create_tls_context", lambda: tls_context)
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("real socket forbidden")
        ),
    )
    valid_body = b'{"model":"gpt-5.6-terra"}'

    status, raw = proxy_module.perform_provider_request(
        valid_body,
        "synthetic-value",
        connection_factory=factory,
    )

    assert status == 200
    assert raw == response_body
    assert factory.created == [("api.openai.com", 443, 10)]
    assert factory.contexts == [tls_context]
    assert connection.requests == [
        (
            "POST",
            "/v1/responses",
            valid_body,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer synthetic-value",
            },
        )
    ]
    assert connection.getresponse_count == 1
    assert connection.connect_count == 1
    assert connection.close_count == 1
    assert response.read_limits == [proxy_module.MAXIMUM_BYTES + 1]
    assert connection.sock.timeouts
    assert all(0 < value <= 60 for value in connection.sock.timeouts)


def test_transport_reads_connection_close_response_after_socket_detaches(
    proxy_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_body = b'{"status":"synthetic"}'
    response = _FakeHTTPSResponse(response_body)
    connection = _FakeHTTPSConnection(
        response,
        drop_socket_on_response=True,
    )
    monkeypatch.setattr(
        proxy_module,
        "create_tls_context",
        lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )

    status, raw = proxy_module.perform_provider_request(
        b'{"model":"gpt-5.6-terra"}',
        "synthetic-value",
        connection_factory=_FakeConnectionFactory(connection),
    )

    assert status == 200
    assert raw == response_body
    assert response.read_limits == [proxy_module.MAXIMUM_BYTES + 1]
    assert connection.close_count == 1


@pytest.mark.parametrize(
    ("elapsed_seconds", "accepted"),
    [(1.0, True), (59.0, True), (60.0, True), (60.000_001, False)],
)
def test_provider_total_deadline_uses_monotonic_clock_without_retry(
    elapsed_seconds: float,
    accepted: bool,
    proxy_module: ModuleType,
    runtime_contract: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    response = _FakeHTTPSResponse(
        b"{}",
        on_read=lambda: now.__setitem__(0, 100.0 + elapsed_seconds),
    )
    connection = _FakeHTTPSConnection(response)
    factory = _FakeConnectionFactory(connection)
    monkeypatch.setattr(
        proxy_module,
        "create_tls_context",
        lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )

    if accepted:
        assert proxy_module.perform_provider_request(
            b"{}",
            "synthetic-value",
            connection_factory=factory,
            runtime_contract=runtime_contract,
            monotonic=lambda: now[0],
        ) == (200, b"{}")
    else:
        with pytest.raises(proxy_module.ProxyError) as raised:
            proxy_module.perform_provider_request(
                b"{}",
                "synthetic-value",
                connection_factory=factory,
                runtime_contract=runtime_contract,
                monotonic=lambda: now[0],
            )
        assert raised.value.category == "TIMEOUT"
        assert raised.value.provider_attempt_count == 1
    assert len(factory.created) == 1
    assert connection.connect_count == 1
    assert connection.close_count == 1


@pytest.mark.parametrize(
    ("content_type", "accepted"),
    [
        ("application/json", True),
        ("Application/JSON", True),
        ("application/json; charset=utf-8", True),
        (" application/json ; charset = UTF-8 ", True),
        ('application/json; charset="utf-8"', True),
        ("application/json;", False),
        ("application/json; boundary=x", False),
        ("application/json; charset=latin1", False),
        ("application/json; charset=utf-8; boundary=x", False),
        ("text/json", False),
    ],
)
def test_transport_content_type_allows_only_json_and_optional_utf8_charset(
    content_type: str,
    accepted: bool,
    proxy_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeHTTPSResponse(b"{}", content_type=content_type)
    connection = _FakeHTTPSConnection(response)
    factory = _FakeConnectionFactory(connection)
    monkeypatch.setattr(
        proxy_module,
        "create_tls_context",
        lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )

    if accepted:
        assert proxy_module.perform_provider_request(
            b"{}",
            "synthetic-value",
            connection_factory=factory,
        ) == (200, b"{}")
    else:
        with pytest.raises(proxy_module.ProxyError) as raised:
            proxy_module.perform_provider_request(
                b"{}",
                "synthetic-value",
                connection_factory=factory,
            )
        assert raised.value.category == "UPSTREAM_REJECTED"
    assert len(connection.requests) == 1
    assert connection.close_count == 1


def test_tls_context_requires_ca_hostname_and_tls12(
    proxy_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    calls: list[str | None] = []

    def fake_default_context(*, cafile: str | None = None) -> ssl.SSLContext:
        calls.append(cafile)
        return context

    monkeypatch.setattr(ssl, "create_default_context", fake_default_context)

    result = proxy_module.create_tls_context()

    assert result is context
    assert calls == ["/etc/ssl/certs/ca-certificates.crt"]
    assert result.check_hostname is True
    assert result.verify_mode == ssl.CERT_REQUIRED
    assert result.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_read_auth_file_accepts_only_regular_mode_0600(
    proxy_module: ModuleType,
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-auth"
    path.write_text("synthetic-value", encoding="utf-8")
    path.chmod(0o600)

    assert proxy_module.read_auth_file(str(path)) == "synthetic-value"


@pytest.mark.parametrize(
    ("case", "content"),
    [
        ("missing", None),
        ("empty", b""),
        ("nul", b"synthetic\x00value"),
        ("cr", b"synthetic\rvalue"),
        ("lf", b"synthetic\nvalue"),
        ("leading_space", b" synthetic"),
        ("trailing_space", b"synthetic "),
        ("oversized", b"x" * 16_385),
        ("wrong_mode", b"synthetic"),
        ("directory", b"directory"),
        ("symlink", b"synthetic"),
    ],
)
def test_read_auth_file_rejects_unsafe_inputs(
    case: str,
    content: bytes | None,
    proxy_module: ModuleType,
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-auth"
    if case == "directory":
        path.mkdir()
    elif case == "symlink":
        target = tmp_path / "target"
        target.write_bytes(content or b"")
        target.chmod(0o600)
        path.symlink_to(target)
    elif case != "missing":
        path.write_bytes(content or b"")
        path.chmod(0o644 if case == "wrong_mode" else 0o600)

    with pytest.raises(proxy_module.ProxyError) as raised:
        proxy_module.read_auth_file(str(path))
    assert raised.value.category == "AUTH_BLOCKED"
    assert raised.value.provider_attempt_count == 0


@pytest.mark.parametrize(
    ("case", "category", "status"),
    [
        ("timeout", "REQUEST_WRITE_FAILED", 200),
        ("tls", "REQUEST_WRITE_FAILED", 200),
        ("redirect_301", "REDIRECT_REJECTED", 301),
        ("redirect_307", "REDIRECT_REJECTED", 307),
        ("rate_limit", "RATE_LIMIT_REJECTED", 429),
        ("server_error", "UPSTREAM_REJECTED", 500),
        ("wrong_content_type", "UPSTREAM_REJECTED", 200),
        ("oversized", "RESPONSE_SIZE_BLOCKED", 200),
        ("invalid_content_length", "RESPONSE_SIZE_BLOCKED", 200),
    ],
)
def test_transport_failures_are_one_attempt_and_stable(
    case: str,
    category: str,
    status: int,
    proxy_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"{}"
    content_type = "application/json"
    content_length: str | None = None
    request_error: BaseException | None = None
    if case == "timeout":
        request_error = TimeoutError("synthetic timeout")
    elif case == "tls":
        request_error = ssl.SSLError("synthetic tls")
    elif case == "wrong_content_type":
        content_type = "text/plain"
    elif case == "oversized":
        body = b"x" * (proxy_module.MAXIMUM_BYTES + 1)
    elif case == "invalid_content_length":
        content_length = "not-an-integer"
    response = _FakeHTTPSResponse(
        body,
        status=status,
        content_type=content_type,
        content_length=content_length,
    )
    connection = _FakeHTTPSConnection(response, request_error=request_error)
    factory = _FakeConnectionFactory(connection)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(proxy_module, "create_tls_context", lambda: context)

    with pytest.raises(proxy_module.ProxyError) as raised:
        proxy_module.perform_provider_request(
            b"{}",
            "synthetic-value",
            connection_factory=factory,
        )

    assert raised.value.category == category
    assert raised.value.provider_attempt_count == 1
    assert len(factory.created) == 1
    assert len(connection.requests) == 1
    assert connection.getresponse_count in {0, 1}
    assert connection.close_count == 1
    if status in {301, 307, 429, 500}:
        assert response.read_limits == []


def test_process_local_request_success_returns_relay_response_and_sanitized_receipt(
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    valid_candidate: dict[str, Any],
    contract: dict[str, Any],
    tmp_path: Path,
) -> None:
    request_body = _relay_body(relay_envelope)
    provider_body = _provider_response_bytes(valid_candidate)
    auth_value = "synthetic-value"
    auth_path = tmp_path / "provider-auth"
    auth_path.write_text(auth_value, encoding="utf-8")
    auth_path.chmod(0o600)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}\n", encoding="utf-8")
    calls: list[tuple[bytes, str]] = []

    def provider_requester(
        body: bytes,
        auth: str,
        stage_recorder: Any,
    ) -> tuple[int, bytes]:
        calls.append((body, auth))
        stage_recorder.record("provider_connect_started")
        stage_recorder.record("provider_connect_completed")
        stage_recorder.record("tls_completed")
        stage_recorder.record("request_write_started")
        stage_recorder.record("request_write_completed", byte_count=len(body))
        stage_recorder.record("response_headers_received", http_status=200)
        stage_recorder.record(
            "response_body_completed",
            http_status=200,
            byte_count=len(provider_body),
        )
        return 200, provider_body

    status, response_body, receipt = proxy_module.process_local_request(
        method="POST",
        path="/v1/typed-claims",
        content_length=str(len(request_body)),
        body=request_body,
        contract=contract,
        auth_path=str(auth_path),
        receipt_path=str(receipt_path),
        provider_requester=provider_requester,
    )

    assert status == 200
    assert len(calls) == 1
    assert json.loads(calls[0][0]) == proxy_module.build_provider_request(
        relay_envelope,
        contract,
    )
    assert calls[0][1] == auth_value
    assert json.loads(response_body) == {
        "protocol_version": 1,
        "request_id": FIXED_REQUEST_ID,
        "symbol": "000403.SZ",
        "projection_sha256": relay_envelope["projection"]["projection_sha256"],
        "claims_candidate": valid_candidate,
    }
    assert set(receipt) == {
        "receipt_schema_version",
        "request_id",
        "component",
        "method_allowed",
        "path_allowed",
        "auth_present",
        "tls_verification",
        "redirect_followed",
        "provider_attempt_count",
        "retry_count",
        "provider_http_status",
        "response_category",
        "response_size",
        "response_bytes",
        "terminal_status",
        "process_started",
        "proxy_ready",
        "relay_request_received",
        "provider_connect_started",
        "provider_connect_completed",
        "tls_completed",
        "request_write_started",
        "request_write_completed",
        "response_headers_received",
        "response_body_completed",
    }
    assert receipt == json.loads(receipt_path.read_bytes())
    assert receipt["receipt_schema_version"] == 2
    assert receipt["request_id"] == FIXED_REQUEST_ID
    assert receipt["component"] == "proxy"
    assert all(
        receipt[event]["occurred"] is True
        for event in (
            "provider_connect_started",
            "provider_connect_completed",
            "tls_completed",
            "request_write_started",
            "request_write_completed",
            "response_headers_received",
            "response_body_completed",
        )
    )
    assert receipt["provider_attempt_count"] == 1
    assert receipt["retry_count"] == 0
    assert receipt["response_category"] == "FORWARDED"
    serialized_receipt = receipt_path.read_text(encoding="utf-8")
    assert auth_value not in serialized_receipt
    assert request_body.decode("utf-8") not in serialized_receipt
    assert provider_body.decode("utf-8") not in serialized_receipt


@pytest.mark.parametrize(
    ("method", "path", "content_length", "body", "status", "category"),
    [
        ("GET", "/v1/typed-claims", None, b"", 405, "METHOD_BLOCKED"),
        ("POST", "/blocked", None, b"", 404, "PATH_BLOCKED"),
        ("POST", "/v1/typed-claims", None, b"", 413, "REQUEST_SIZE_BLOCKED"),
        ("POST", "/v1/typed-claims", "invalid", b"", 413, "REQUEST_SIZE_BLOCKED"),
        ("POST", "/v1/typed-claims", "2", b"{}x", 413, "REQUEST_SIZE_BLOCKED"),
        ("POST", "/v1/typed-claims", "2", b"{}", 400, "REQUEST_SCHEMA_BLOCKED"),
    ],
)
def test_process_local_request_gates_before_auth_or_transport(
    method: str,
    path: str,
    content_length: str | None,
    body: bytes,
    status: int,
    category: str,
    proxy_module: ModuleType,
    contract: dict[str, Any],
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}\n", encoding="utf-8")
    calls = 0

    def forbidden_requester(
        _body: bytes,
        _auth: str,
        _stage_recorder: Any,
    ) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    actual_status, response_body, receipt = proxy_module.process_local_request(
        method=method,
        path=path,
        content_length=content_length,
        body=body,
        contract=contract,
        auth_path=str(tmp_path / "missing-auth"),
        receipt_path=str(receipt_path),
        provider_requester=forbidden_requester,
    )

    assert actual_status == status
    assert json.loads(response_body) == {"error": category}
    assert calls == 0
    assert receipt["provider_attempt_count"] == 0
    assert receipt["retry_count"] == 0
    assert receipt["response_category"] == category


def test_process_local_request_maps_auth_failure_to_503(
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
    tmp_path: Path,
) -> None:
    body = _relay_body(relay_envelope)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}\n", encoding="utf-8")

    status, response_body, receipt = proxy_module.process_local_request(
        method="POST",
        path="/v1/typed-claims",
        content_length=str(len(body)),
        body=body,
        contract=contract,
        auth_path=str(tmp_path / "missing-auth"),
        receipt_path=str(receipt_path),
    )

    assert status == 503
    assert json.loads(response_body) == {"error": "AUTH_BLOCKED"}
    assert receipt["auth_present"] is False
    assert receipt["provider_attempt_count"] == 0


@pytest.mark.parametrize(
    ("category", "local_status"),
    [
        ("TIMEOUT", 504),
        ("RATE_LIMIT_REJECTED", 429),
        ("TLS_BLOCKED", 502),
        ("REDIRECT_REJECTED", 502),
        ("UPSTREAM_REJECTED", 502),
        ("RESPONSE_SIZE_BLOCKED", 502),
    ],
)
def test_process_local_request_maps_transport_failures_without_retry(
    category: str,
    local_status: int,
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
    tmp_path: Path,
) -> None:
    body = _relay_body(relay_envelope)
    auth_value = "synthetic-value"
    auth_path = tmp_path / "provider-auth"
    auth_path.write_text(auth_value, encoding="utf-8")
    auth_path.chmod(0o600)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}\n", encoding="utf-8")
    calls = 0

    def failing_requester(
        _body: bytes,
        _auth: str,
        _stage_recorder: Any,
    ) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        raise proxy_module.ProxyError(
            category,
            provider_http_status=429 if category == "RATE_LIMIT_REJECTED" else None,
            provider_attempt_count=1,
        )

    status, response_body, receipt = proxy_module.process_local_request(
        method="POST",
        path="/v1/typed-claims",
        content_length=str(len(body)),
        body=body,
        contract=contract,
        auth_path=str(auth_path),
        receipt_path=str(receipt_path),
        provider_requester=failing_requester,
    )

    assert status == local_status
    assert json.loads(response_body) == {"error": category}
    assert calls == 1
    assert receipt["provider_attempt_count"] == 1
    assert receipt["retry_count"] == 0
    assert auth_value not in json.dumps(receipt, sort_keys=True)


@pytest.mark.parametrize(
    "reached_stages",
    [
        (),
        (
            "provider_connect_started",
            "provider_connect_completed",
            "tls_completed",
        ),
        (
            "provider_connect_started",
            "provider_connect_completed",
            "tls_completed",
            "request_write_started",
        ),
    ],
)
def test_unexpected_provider_exception_publishes_sanitized_terminal_receipt(
    reached_stages: tuple[str, ...],
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
    tmp_path: Path,
) -> None:
    body = _relay_body(relay_envelope)
    auth_path = tmp_path / "provider-auth"
    auth_path.write_text("synthetic-value", encoding="utf-8")
    auth_path.chmod(0o600)
    receipt_path = tmp_path / "receipt.json"
    sensitive_exception = "Authorization: Bearer must-not-be-persisted"

    def unexpected_requester(
        _body: bytes,
        _auth: str,
        stage_recorder: Any,
    ) -> tuple[int, bytes]:
        for stage in reached_stages:
            stage_recorder.record(stage)
        raise RuntimeError(sensitive_exception)

    status, response_body, receipt = proxy_module.process_local_request(
        method="POST",
        path="/v1/typed-claims",
        content_length=str(len(body)),
        body=body,
        contract=contract,
        auth_path=str(auth_path),
        receipt_path=str(receipt_path),
        provider_requester=unexpected_requester,
    )

    persisted = receipt_path.read_text(encoding="utf-8")
    assert status == 502
    assert json.loads(response_body) == {"error": "UPSTREAM_REJECTED"}
    assert receipt["terminal_status"] == "UPSTREAM_REJECTED"
    assert receipt["response_category"] == "UPSTREAM_REJECTED"
    assert receipt["provider_attempt_count"] == 1
    assert receipt["retry_count"] == 0
    assert all(receipt[name]["occurred"] is True for name in reached_stages)
    assert sensitive_exception not in persisted
    assert "Authorization" not in persisted
    assert "Bearer " not in persisted
    assert json.loads(persisted) == receipt


def test_unexpected_base_exception_is_not_swallowed(
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
    tmp_path: Path,
) -> None:
    body = _relay_body(relay_envelope)
    auth_path = tmp_path / "provider-auth"
    auth_path.write_text("synthetic-value", encoding="utf-8")
    auth_path.chmod(0o600)
    receipt_path = tmp_path / "receipt.json"

    def interrupted_requester(
        _body: bytes,
        _auth: str,
        _stage_recorder: Any,
    ) -> tuple[int, bytes]:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        proxy_module.process_local_request(
            method="POST",
            path="/v1/typed-claims",
            content_length=str(len(body)),
            body=body,
            contract=contract,
            auth_path=str(auth_path),
            receipt_path=str(receipt_path),
            provider_requester=interrupted_requester,
        )

    assert not receipt_path.exists()


@pytest.mark.parametrize(
    ("case", "provider_status", "category", "local_status"),
    [
        ("redirect", 307, "REDIRECT_REJECTED", 502),
        ("rate_limit", 429, "RATE_LIMIT_REJECTED", 429),
        ("server_error", 500, "UPSTREAM_REJECTED", 502),
        ("oversized", 200, "RESPONSE_SIZE_BLOCKED", 502),
    ],
)
def test_process_local_request_revalidates_provider_result_boundary(
    case: str,
    provider_status: int,
    category: str,
    local_status: int,
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    contract: dict[str, Any],
    tmp_path: Path,
) -> None:
    body = _relay_body(relay_envelope)
    auth_path = tmp_path / "provider-auth"
    auth_path.write_text("synthetic-value", encoding="utf-8")
    auth_path.chmod(0o600)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}\n", encoding="utf-8")
    provider_body = b"x" * 1_048_577 if case == "oversized" else b"{}"

    def provider_requester(
        _body: bytes,
        _auth: str,
        _stage_recorder: Any,
    ) -> tuple[int, bytes]:
        return provider_status, provider_body

    status, response_body, receipt = proxy_module.process_local_request(
        method="POST",
        path="/v1/typed-claims",
        content_length=str(len(body)),
        body=body,
        contract=contract,
        auth_path=str(auth_path),
        receipt_path=str(receipt_path),
        provider_requester=provider_requester,
    )

    assert status == local_status
    assert json.loads(response_body) == {"error": category}
    assert receipt["response_category"] == category
    assert receipt["provider_attempt_count"] == 1


@pytest.mark.parametrize(
    ("case", "category"),
    [
        ("malformed_json", "RESPONSE_JSON_BLOCKED"),
        ("wrong_model", "MODEL_IDENTITY_BLOCKED"),
        ("schema_invalid", "RESPONSE_SCHEMA_BLOCKED"),
    ],
)
def test_process_local_request_rejects_invalid_provider_output(
    case: str,
    category: str,
    proxy_module: ModuleType,
    relay_envelope: dict[str, Any],
    valid_candidate: dict[str, Any],
    contract: dict[str, Any],
    tmp_path: Path,
) -> None:
    body = _relay_body(relay_envelope)
    auth_path = tmp_path / "provider-auth"
    auth_path.write_text("synthetic-value", encoding="utf-8")
    auth_path.chmod(0o600)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}\n", encoding="utf-8")
    if case == "malformed_json":
        provider_body = b"not-json"
    else:
        response = _responses_fixture(valid_candidate)
        if case == "wrong_model":
            response["model"] = "wrong-model"
        else:
            response["output"][-1]["content"][0]["text"] = "{}"
        provider_body = json.dumps(response, ensure_ascii=False).encode("utf-8")

    def provider_requester(
        _body: bytes,
        _auth: str,
        _stage_recorder: Any,
    ) -> tuple[int, bytes]:
        return 200, provider_body

    status, response_body, receipt = proxy_module.process_local_request(
        method="POST",
        path="/v1/typed-claims",
        content_length=str(len(body)),
        body=body,
        contract=contract,
        auth_path=str(auth_path),
        receipt_path=str(receipt_path),
        provider_requester=provider_requester,
    )

    assert status == 502
    assert json.loads(response_body) == {"error": category}
    assert receipt["provider_attempt_count"] == 1
    assert provider_body.decode("utf-8") not in json.dumps(receipt, ensure_ascii=False)


def test_receipt_writer_rejects_symlink(
    proxy_module: ModuleType,
    tmp_path: Path,
) -> None:
    target = tmp_path / "outside.json"
    target.write_text("unchanged\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.symlink_to(target)

    with pytest.raises(proxy_module.ProxyError):
        proxy_module.write_receipt(
            proxy_module.build_receipt(response_category="AUTH_BLOCKED"),
            str(receipt),
        )
    assert target.read_text(encoding="utf-8") == "unchanged\n"


def test_handler_disables_access_logs(proxy_module: ModuleType) -> None:
    handler = object.__new__(proxy_module.ProxyHandler)
    assert handler.log_message("sensitive %s", "value") is None
    assert not issubclass(proxy_module.ProxyHandler, http.client.HTTPConnection)
