"""Dedicated, fail-closed OpenAI adapter for the Phase 2B single-symbol Canary."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import math
import os
import re
import ssl
import stat
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

ALLOWED_METHOD = "POST"
ALLOWED_PATH = "/v1/typed-claims"
UPSTREAM_HOST = "api.openai.com"
UPSTREAM_PORT = 443
UPSTREAM_PATH = "/v1/responses"
MODEL_ID = "gpt-5.6-terra"
FOLLOW_REDIRECTS = False
RETRY_COUNT = 0
MAXIMUM_ATTEMPTS = 1
MAXIMUM_BYTES = 1_048_576
TIMEOUT_SECONDS = 60
CONTRACT_PATH = Path("/proxy/responses-contract.json")
AUTH_PATH = Path("/run/phase2/provider-auth")
RECEIPT_PATH = Path("/output/receipt.json")
AUTH_MAXIMUM_BYTES = 16_384

_EXPECTED_CONTRACT_SHA256 = (
    "da840ca12a4df5278cd4f1b01fef395b553186d6b1fe5df2d895b0c151ea6427"
)
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_JSON_CONTENT_TYPE = re.compile(
    r'^[ \t]*application/json[ \t]*(?:;[ \t]*charset[ \t]*=[ \t]*(?:utf-8|"utf-8")[ \t]*)?$',
    re.IGNORECASE,
)
_NOFOLLOW = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
_CONTRACT_FIELDS = {
    "contract_schema_version",
    "contract_sha256",
    "fixed_instructions",
    "policy",
    "relay_contract",
    "response_format",
}
_POLICY = {
    "provider_id": "openai",
    "model_id": MODEL_ID,
    "endpoint_host": UPSTREAM_HOST,
    "endpoint_port": UPSTREAM_PORT,
    "endpoint_path": UPSTREAM_PATH,
    "http_method": ALLOWED_METHOD,
    "tls_verification": True,
    "minimum_tls_version": "TLSv1_2",
    "follow_redirects": FOLLOW_REDIRECTS,
    "stream": False,
    "store": False,
    "tools": [],
    "retry_count": RETRY_COUNT,
    "maximum_attempts": MAXIMUM_ATTEMPTS,
    "timeout_seconds": TIMEOUT_SECONDS,
    "maximum_bytes": MAXIMUM_BYTES,
    "approved_symbol": "000403.SZ",
}
_RELAY_FIELDS = {
    "protocol_version",
    "claims_schema_version",
    "response_format",
    "approved_symbol",
    "approved_trade_date",
    "allowed_claim_types",
    "allowed_predicates",
    "generation",
}
_REQUEST_FIELDS = {
    "protocol_version",
    "request_id",
    "symbol",
    "projection_sha256",
    "claims_schema_version",
    "allowed_claim_types",
    "allowed_predicates",
    "response_format",
}
_PROJECTION_FIELDS = {
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
_GENERATION_FIELDS = {
    "temperature",
    "response_format",
    "tool_use",
    "web_browsing",
    "file_tools",
    "function_calling",
    "streaming",
    "retry_count",
    "maximum_output_bytes",
    "timeout_seconds",
}
_SAFE_FACT_FIELDS = {
    "daily",
    "indicators",
    "minute_1m",
    "minute_30m",
    "adjustment_factors",
    "data_freshness",
    "scope",
    "vendor_pending",
}
_INDICATORS = {
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
}
_EXPECTED_SAFE_FIELDS = {
    "daily": {"open", "close", "contract_status"},
    "indicators": _INDICATORS,
    "minute_1m": {
        "bar_count",
        "status",
        "duplicate_timestamp_count",
        "lunch_break_bar_count",
        "material_ohlc_anomaly_count",
        "material_negative_amount_count",
    },
    "minute_30m": {
        "bar_count",
        "status",
        "first_bucket_mechanically_explained",
        "duplicate_timestamp_count",
        "lunch_break_bar_count",
        "ohlc_mismatch_count",
        "non_first_bucket_volume_amount_mismatch_count",
        "material_ohlc_anomaly_count",
        "material_negative_amount_count",
    },
    "adjustment_factors": {
        "status",
        "repeat_stable",
        "second_adjustment_detected",
    },
    "scope": {
        "market_scope",
        "financial_scope",
        "news_scope",
        "industry_scope",
    },
}
_SCHEMA_KEYWORDS = {
    "$defs",
    "$ref",
    "type",
    "const",
    "enum",
    "anyOf",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "title",
}
_JSON_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
_RECEIPT_FIELDS = {
    "receipt_schema_version",
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
}
_LOCAL_STATUS = {
    "METHOD_BLOCKED": 405,
    "PATH_BLOCKED": 404,
    "REQUEST_SIZE_BLOCKED": 413,
    "REQUEST_SCHEMA_BLOCKED": 400,
    "AUTH_BLOCKED": 503,
    "TIMEOUT": 504,
    "RATE_LIMIT_REJECTED": 429,
    "TLS_BLOCKED": 502,
    "REDIRECT_REJECTED": 502,
    "UPSTREAM_REJECTED": 502,
    "RESPONSE_SIZE_BLOCKED": 502,
    "RESPONSE_JSON_BLOCKED": 502,
    "RESPONSE_SCHEMA_BLOCKED": 502,
    "MODEL_IDENTITY_BLOCKED": 502,
    "OUTPUT_EXTRACTION_BLOCKED": 502,
}


class ProxyError(Exception):
    """Stable, sanitized failure raised at the dedicated Proxy boundary."""

    def __init__(
        self,
        category: str,
        *,
        provider_http_status: int | None = None,
        provider_attempt_count: int = 0,
        response_size: int | None = None,
    ) -> None:
        self.category = category
        self.provider_http_status = provider_http_status
        self.provider_attempt_count = provider_attempt_count
        self.response_size = response_size
        super().__init__(category)


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _compact_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _finite_tree(value: Any) -> bool:
    if value is None or isinstance(value, (bool, str)):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _finite_tree(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_finite_tree(item) for item in value)
    return False


def strict_object(raw: bytes, category: str) -> dict[str, Any]:
    """Decode one bounded UTF-8 JSON object without trailing data."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAXIMUM_BYTES:
        raise ProxyError(category)

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite")

    try:
        text = raw.decode("utf-8")
        value, end = json.JSONDecoder(parse_constant=reject_constant).raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProxyError(category) from exc
    if text[end:].strip() or not isinstance(value, dict) or not _finite_tree(value):
        raise ProxyError(category)
    return value


def _read_regular(path: Path, maximum_bytes: int, category: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise ProxyError(category) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= maximum_bytes:
            raise ProxyError(category)
        raw = os.read(descriptor, maximum_bytes + 1)
        if len(raw) != metadata.st_size or len(raw) > maximum_bytes:
            raise ProxyError(category)
        return raw
    except OSError as exc:
        raise ProxyError(category) from exc
    finally:
        os.close(descriptor)


def _schema_ref_name(reference: Any, root: Mapping[str, Any]) -> str:
    if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    name = reference.removeprefix("#/$defs/")
    definitions = root.get("$defs")
    if (
        not name
        or "/" in name
        or not isinstance(definitions, Mapping)
        or name not in definitions
    ):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    return name


def _validate_schema_definition(
    schema: Any,
    root: Mapping[str, Any],
    ref_stack: tuple[str, ...] = (),
) -> None:
    if not isinstance(schema, Mapping) or not set(schema) <= _SCHEMA_KEYWORDS:
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    title = schema.get("title")
    if title is not None and not isinstance(title, str):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    reference = schema.get("$ref")
    if reference is not None:
        if set(schema) - {"$ref", "title"}:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        name = _schema_ref_name(reference, root)
        if name in ref_stack:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        definitions = root["$defs"]
        _validate_schema_definition(definitions[name], root, (*ref_stack, name))
        return
    definitions = schema.get("$defs")
    if definitions is not None:
        if not isinstance(definitions, Mapping) or not definitions:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        for name, definition in definitions.items():
            if not isinstance(name, str) or not name or "/" in name:
                raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
            _validate_schema_definition(definition, root, (name,))
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _JSON_TYPES:
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum or not _finite_tree(enum)):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    if "const" in schema and not _finite_tree(schema["const"]):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    variants = schema.get("anyOf")
    if variants is not None:
        if not isinstance(variants, list) or not variants:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        for variant in variants:
            _validate_schema_definition(variant, root, ref_stack)
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list)
        or not all(isinstance(item, str) for item in required)
        or len(required) != len(set(required))
    ):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        for key, subschema in properties.items():
            if not isinstance(key, str):
                raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
            _validate_schema_definition(subschema, root, ref_stack)
    if "additionalProperties" in schema and schema["additionalProperties"] is not False:
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    if "items" in schema:
        _validate_schema_definition(schema["items"], root, ref_stack)
    pattern = schema.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED") from exc
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        value = schema.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    for key in ("minimum", "maximum"):
        value = schema.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")


def _validate_contract(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != _CONTRACT_FIELDS or value.get("contract_schema_version") != 1:
        raise ProxyError("REQUEST_SCHEMA_BLOCKED")
    digest = value.get("contract_sha256")
    if not isinstance(digest, str) or not hmac.compare_digest(
        digest,
        _EXPECTED_CONTRACT_SHA256,
    ):
        raise ProxyError("REQUEST_SCHEMA_BLOCKED")
    payload = dict(value)
    payload.pop("contract_sha256")
    actual = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if not hmac.compare_digest(digest, actual) or value.get("policy") != _POLICY:
        raise ProxyError("REQUEST_SCHEMA_BLOCKED")
    relay = value.get("relay_contract")
    instructions = value.get("fixed_instructions")
    response_format = value.get("response_format")
    if (
        not isinstance(relay, dict)
        or set(relay) != _RELAY_FIELDS
        or relay.get("protocol_version") != 1
        or relay.get("claims_schema_version") != 1
        or relay.get("response_format") != "typed_claims_json"
        or relay.get("approved_symbol") != _POLICY["approved_symbol"]
        or relay.get("approved_trade_date") != "2026-07-31"
        or not _canonical_string_list(relay.get("allowed_claim_types"))
        or not _canonical_string_list(relay.get("allowed_predicates"))
        or not isinstance(relay.get("generation"), dict)
        or set(relay["generation"]) != _GENERATION_FIELDS
        or not isinstance(instructions, list)
        or not instructions
        or not all(isinstance(item, str) and item for item in instructions)
        or not isinstance(response_format, dict)
        or set(response_format) != {"type", "name", "strict", "schema"}
        or response_format.get("type") != "json_schema"
        or response_format.get("name") != "tickflow_phase2_claims_candidate"
        or response_format.get("strict") is not True
    ):
        raise ProxyError("REQUEST_SCHEMA_BLOCKED")
    schema = response_format.get("schema")
    if not isinstance(schema, dict):
        raise ProxyError("REQUEST_SCHEMA_BLOCKED")
    try:
        _validate_schema_definition(schema, schema)
    except ProxyError as exc:
        raise ProxyError("REQUEST_SCHEMA_BLOCKED") from exc
    return value


def load_contract(path: str = "/proxy/responses-contract.json") -> dict[str, Any]:
    """Load the exact source-controlled request/response contract."""
    return _validate_contract(
        strict_object(
            _read_regular(Path(path), MAXIMUM_BYTES, "REQUEST_SCHEMA_BLOCKED"),
            "REQUEST_SCHEMA_BLOCKED",
        )
    )


def _canonical_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item for item in value)
        and value == sorted(set(value))
    )


def projection_sha256(projection: Mapping[str, Any]) -> str:
    payload = dict(projection)
    payload.pop("projection_sha256", None)
    try:
        raw = _canonical_bytes(payload)
    except (TypeError, ValueError) as exc:
        raise ProxyError("REQUEST_SCHEMA_BLOCKED") from exc
    return hashlib.sha256(raw).hexdigest()


def _mapping_fields(value: Any, expected: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == expected


def _validate_safe_facts(value: Any) -> bool:
    if not _mapping_fields(value, _SAFE_FACT_FIELDS):
        return False
    for field, expected in _EXPECTED_SAFE_FIELDS.items():
        if not _mapping_fields(value.get(field), expected):
            return False
    indicators = value["indicators"]
    if any(not _mapping_fields(indicators.get(name), {"value"}) for name in _INDICATORS):
        return False
    return _finite_tree(value)


def validate_relay_envelope(
    value: Any,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one Provider-neutral Relay envelope before any transport."""
    try:
        if not isinstance(contract, dict):
            raise ProxyError("REQUEST_SCHEMA_BLOCKED")
        _validate_contract(contract)
        if not _mapping_fields(value, {"request", "projection", "generation"}):
            raise ProxyError("REQUEST_SCHEMA_BLOCKED")
        request = value["request"]
        projection = value["projection"]
        generation = value["generation"]
        relay = contract["relay_contract"]
        if (
            not _finite_tree(value)
            or not _mapping_fields(request, _REQUEST_FIELDS)
            or not _mapping_fields(projection, _PROJECTION_FIELDS)
            or not _mapping_fields(generation, _GENERATION_FIELDS)
            or request.get("protocol_version") != relay["protocol_version"]
            or request.get("claims_schema_version") != relay["claims_schema_version"]
            or request.get("response_format") != relay["response_format"]
            or request.get("symbol") != relay["approved_symbol"]
            or not _HEX_32.fullmatch(str(request.get("request_id", "")))
            or not _HEX_64.fullmatch(str(request.get("projection_sha256", "")))
            or request.get("allowed_claim_types") != relay["allowed_claim_types"]
            or request.get("allowed_predicates") != relay["allowed_predicates"]
            or generation != relay["generation"]
            or projection.get("projection_schema_version") != 1
            or projection.get("symbol") != relay["approved_symbol"]
            or projection.get("trade_date") != relay["approved_trade_date"]
            or projection.get("timezone") != "Asia/Shanghai"
            or not isinstance(projection.get("name"), str)
            or not projection["name"]
            or len(projection["name"]) > 40
            or not _HEX_64.fullmatch(str(projection.get("facts_sha256", "")))
            or not _HEX_64.fullmatch(str(projection.get("projection_sha256", "")))
            or projection.get("allowed_predicates") != relay["allowed_predicates"]
            or request.get("projection_sha256") != projection.get("projection_sha256")
            or projection.get("projection_sha256") != projection_sha256(projection)
            or not _validate_safe_facts(projection.get("safe_facts"))
        ):
            raise ProxyError("REQUEST_SCHEMA_BLOCKED")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ProxyError):
            raise
        raise ProxyError("REQUEST_SCHEMA_BLOCKED") from exc
    return dict(value)


def build_provider_request(
    envelope: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Construct the only OpenAI Responses request approved for the Canary."""
    validated = validate_relay_envelope(envelope, contract)
    projection = validated["projection"]
    try:
        return {
            "model": MODEL_ID,
            "stream": False,
            "store": False,
            "tools": [],
            "input": [
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
                            "text": _compact_text(projection),
                        }
                    ],
                },
            ],
            "text": {"format": contract["response_format"]},
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ProxyError("REQUEST_SCHEMA_BLOCKED") from exc


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(left) and math.isfinite(right) and left == right
    return type(left) is type(right) and left == right


def _schema_type_matches(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False


def _validate_instance(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    ref_stack: tuple[str, ...] = (),
) -> None:
    reference = schema.get("$ref")
    if reference is not None:
        name = _schema_ref_name(reference, root)
        if name in ref_stack:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        _validate_instance(value, root["$defs"][name], root, (*ref_stack, name))
        return
    variants = schema.get("anyOf")
    if variants is not None:
        matches = 0
        for variant in variants:
            try:
                _validate_instance(value, variant, root, ref_stack)
            except ProxyError:
                continue
            matches += 1
        if matches == 0:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    schema_type = schema.get("type")
    if schema_type is not None and not _schema_type_matches(value, schema_type):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    if "const" in schema and not _json_equal(value, schema["const"]):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    if isinstance(value, dict):
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if any(key not in value for key in required):
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        if schema.get("additionalProperties") is False and not set(value) <= set(properties):
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        for key, item in value.items():
            if key in properties:
                _validate_instance(item, properties[key], root, ref_stack)
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        if "items" in schema:
            for item in value:
                _validate_instance(item, schema["items"], root, ref_stack)
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        if "minimum" in schema and value < schema["minimum"]:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        if "maximum" in schema and value > schema["maximum"]:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")


def validate_candidate(candidate: Any, schema: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a Candidate through the source-controlled closed JSON Schema."""
    try:
        _validate_schema_definition(schema, schema)
        _validate_instance(candidate, schema, schema)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ProxyError):
            raise
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED") from exc
    if not isinstance(candidate, dict) or not _finite_tree(candidate):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    return candidate


def _resolve_projection_pointer(safe_facts: Mapping[str, Any], pointer: Any) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/") or "~" in pointer:
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    current: Any = safe_facts
    for part in pointer[1:].split("/"):
        if not isinstance(current, Mapping) or part not in current:
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        current = current[part]
    return current


def _validate_candidate_bindings(
    candidate: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> None:
    projection = envelope["projection"]
    request = envelope["request"]
    if (
        candidate.get("projection_sha256") != projection["projection_sha256"]
        or candidate.get("symbol") != projection["symbol"]
        or candidate.get("name") != projection["name"]
        or candidate.get("trade_date") != projection["trade_date"]
        or candidate.get("timezone") != projection["timezone"]
        or candidate.get("trading_advice") is not False
    ):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    for claim in candidate.get("claims", []):
        provenance = claim["provenance"]
        if (
            claim.get("claim_type") not in request["allowed_claim_types"]
            or claim.get("predicate") not in request["allowed_predicates"]
            or claim["subject"].get("symbol") != projection["symbol"]
            or claim["scope"].get("as_of") != projection["trade_date"]
            or provenance.get("facts_sha256") != projection["facts_sha256"]
        ):
            raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
        for pointer in provenance.get("fact_refs", []):
            _resolve_projection_pointer(projection["safe_facts"], pointer)


def extract_candidate(
    response: Mapping[str, Any],
    contract: Mapping[str, Any],
    envelope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract exactly one completed structured Candidate from a Responses envelope."""
    if not isinstance(response, Mapping):
        raise ProxyError("OUTPUT_EXTRACTION_BLOCKED")
    if response.get("model") != MODEL_ID:
        raise ProxyError("MODEL_IDENTITY_BLOCKED")
    required = {
        "object",
        "status",
        "error",
        "incomplete_details",
        "model",
        "output",
        "tools",
    }
    if (
        not required <= set(response)
        or response.get("object") != "response"
        or response.get("status") != "completed"
        or response.get("error") is not None
        or response.get("incomplete_details") is not None
        or response.get("tools") != []
        or not isinstance(response.get("output"), list)
    ):
        raise ProxyError("OUTPUT_EXTRACTION_BLOCKED")
    messages: list[Mapping[str, Any]] = []
    message_seen = False
    for item in response["output"]:
        if not isinstance(item, Mapping):
            raise ProxyError("OUTPUT_EXTRACTION_BLOCKED")
        if item.get("type") == "reasoning" and not message_seen:
            if (
                set(item) != {"id", "type", "summary"}
                or not isinstance(item.get("id"), str)
                or not item["id"]
                or item.get("summary") != []
            ):
                raise ProxyError("OUTPUT_EXTRACTION_BLOCKED")
            continue
        if item.get("type") != "message" or message_seen:
            raise ProxyError("OUTPUT_EXTRACTION_BLOCKED")
        message_seen = True
        messages.append(item)
    if len(messages) != 1:
        raise ProxyError("OUTPUT_EXTRACTION_BLOCKED")
    message = messages[0]
    if (
        set(message) != {"type", "id", "status", "role", "content"}
        or not isinstance(message.get("id"), str)
        or not message["id"]
        or message.get("status") != "completed"
        or message.get("role") != "assistant"
        or not isinstance(message.get("content"), list)
        or len(message["content"]) != 1
    ):
        raise ProxyError("OUTPUT_EXTRACTION_BLOCKED")
    content = message["content"][0]
    if (
        not isinstance(content, Mapping)
        or not {"type", "text"} <= set(content)
        or not set(content) <= {"type", "text", "annotations", "logprobs"}
        or content.get("type") != "output_text"
        or content.get("annotations", []) != []
        or content.get("logprobs", []) != []
        or not isinstance(content.get("text"), str)
    ):
        raise ProxyError("OUTPUT_EXTRACTION_BLOCKED")
    candidate = strict_object(
        content["text"].encode("utf-8"),
        "RESPONSE_JSON_BLOCKED",
    )
    schema = contract.get("response_format", {}).get("schema")
    if not isinstance(schema, Mapping):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    validate_candidate(candidate, schema)
    relay = contract.get("relay_contract", {})
    if (
        candidate.get("symbol") != relay.get("approved_symbol")
        or candidate.get("trade_date") != relay.get("approved_trade_date")
    ):
        raise ProxyError("RESPONSE_SCHEMA_BLOCKED")
    if envelope is not None:
        validated_envelope = validate_relay_envelope(envelope, contract)
        _validate_candidate_bindings(candidate, validated_envelope)
    return candidate


ConnectionFactory = Callable[
    [str, int, int, ssl.SSLContext],
    http.client.HTTPSConnection,
]
ProviderRequester = Callable[[bytes, str], tuple[int, bytes]]


def create_tls_context() -> ssl.SSLContext:
    """Create the fixed verified TLS context used by the sole upstream."""
    context = ssl.create_default_context(
        cafile="/etc/ssl/certs/ca-certificates.crt"
    )
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def default_connection_factory(
    host: str,
    port: int,
    timeout: int,
    context: ssl.SSLContext,
) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(
        host,
        port,
        timeout=timeout,
        context=context,
    )


def read_auth_file(path: str = "/run/phase2/provider-auth") -> str:
    """Read one fixed, no-follow mode-0600 credential file without metadata output."""
    try:
        descriptor = os.open(Path(path), os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise ProxyError("AUTH_BLOCKED") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 0 < metadata.st_size <= AUTH_MAXIMUM_BYTES
        ):
            raise ProxyError("AUTH_BLOCKED")
        raw = os.read(descriptor, AUTH_MAXIMUM_BYTES + 1)
        if len(raw) != metadata.st_size or len(raw) > AUTH_MAXIMUM_BYTES:
            raise ProxyError("AUTH_BLOCKED")
    except OSError as exc:
        raise ProxyError("AUTH_BLOCKED") from exc
    finally:
        os.close(descriptor)
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProxyError("AUTH_BLOCKED") from exc
    if (
        not value
        or value != value.strip()
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        raise ProxyError("AUTH_BLOCKED")
    return value


def perform_provider_request(
    body: bytes,
    auth_value: str,
    *,
    connection_factory: ConnectionFactory = default_connection_factory,
) -> tuple[int, bytes]:
    """Perform exactly one verified HTTPS request with no redirect or retry."""
    if not isinstance(body, bytes) or not 0 < len(body) <= MAXIMUM_BYTES:
        raise ProxyError("REQUEST_SIZE_BLOCKED")
    if (
        not isinstance(auth_value, str)
        or not auth_value
        or auth_value != auth_value.strip()
        or any(character in auth_value for character in ("\x00", "\r", "\n"))
    ):
        raise ProxyError("AUTH_BLOCKED")
    connection: http.client.HTTPSConnection | None = None
    try:
        context = create_tls_context()
        connection = connection_factory(
            UPSTREAM_HOST,
            UPSTREAM_PORT,
            TIMEOUT_SECONDS,
            context,
        )
        connection.request(
            ALLOWED_METHOD,
            UPSTREAM_PATH,
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_value}",
            },
        )
        response = connection.getresponse()
        status_code = response.status
        if 300 <= status_code < 400:
            raise ProxyError(
                "REDIRECT_REJECTED",
                provider_http_status=status_code,
                provider_attempt_count=1,
            )
        if status_code == 429:
            raise ProxyError(
                "RATE_LIMIT_REJECTED",
                provider_http_status=status_code,
                provider_attempt_count=1,
            )
        if not 200 <= status_code < 300:
            raise ProxyError(
                "UPSTREAM_REJECTED",
                provider_http_status=status_code,
                provider_attempt_count=1,
            )
        content_type = response.getheader("Content-Type")
        if (
            not isinstance(content_type, str)
            or _JSON_CONTENT_TYPE.fullmatch(content_type) is None
        ):
            raise ProxyError(
                "UPSTREAM_REJECTED",
                provider_http_status=status_code,
                provider_attempt_count=1,
            )
        declared_size = response.getheader("Content-Length")
        if declared_size is not None:
            try:
                parsed_size = int(declared_size)
            except ValueError as exc:
                raise ProxyError(
                    "RESPONSE_SIZE_BLOCKED",
                    provider_http_status=status_code,
                    provider_attempt_count=1,
                ) from exc
            if parsed_size < 0 or parsed_size > MAXIMUM_BYTES:
                raise ProxyError(
                    "RESPONSE_SIZE_BLOCKED",
                    provider_http_status=status_code,
                    provider_attempt_count=1,
                )
        raw = response.read(MAXIMUM_BYTES + 1)
        if len(raw) > MAXIMUM_BYTES:
            raise ProxyError(
                "RESPONSE_SIZE_BLOCKED",
                provider_http_status=status_code,
                provider_attempt_count=1,
                response_size=len(raw),
            )
        return status_code, raw
    except ProxyError:
        raise
    except TimeoutError as exc:
        raise ProxyError("TIMEOUT", provider_attempt_count=1) from exc
    except (ssl.SSLError, ssl.CertificateError) as exc:
        raise ProxyError("TLS_BLOCKED", provider_attempt_count=1) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ProxyError("UPSTREAM_REJECTED", provider_attempt_count=1) from exc
    finally:
        if connection is not None:
            connection.close()


def build_receipt(
    *,
    response_category: str,
    method_allowed: bool = False,
    path_allowed: bool = False,
    auth_present: bool = False,
    provider_attempt_count: int = 0,
    provider_http_status: int | None = None,
    response_size: int | None = None,
) -> dict[str, Any]:
    """Build the only persisted proxy evidence shape."""
    value = {
        "receipt_schema_version": 1,
        "method_allowed": method_allowed,
        "path_allowed": path_allowed,
        "auth_present": auth_present,
        "tls_verification": True,
        "redirect_followed": False,
        "provider_attempt_count": provider_attempt_count,
        "retry_count": RETRY_COUNT,
        "provider_http_status": provider_http_status,
        "response_category": response_category,
        "response_size": response_size,
    }
    if (
        set(value) != _RECEIPT_FIELDS
        or not isinstance(response_category, str)
        or not response_category
        or provider_attempt_count not in {0, 1}
        or (
            provider_http_status is not None
            and not 100 <= provider_http_status <= 599
        )
        or (
            response_size is not None
            and not 0 <= response_size <= MAXIMUM_BYTES + 1
        )
    ):
        raise ProxyError("UPSTREAM_REJECTED")
    return value


def write_receipt(
    value: Mapping[str, Any],
    path: str = "/output/receipt.json",
) -> None:
    """Atomically replace the contents of one existing no-follow regular file."""
    if not isinstance(value, Mapping) or set(value) != _RECEIPT_FIELDS:
        raise ProxyError("UPSTREAM_REJECTED")
    try:
        raw = _canonical_bytes(value)
        descriptor = os.open(
            Path(path),
            os.O_WRONLY | os.O_TRUNC | _NOFOLLOW,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ProxyError("UPSTREAM_REJECTED") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ProxyError("UPSTREAM_REJECTED")
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise ProxyError("UPSTREAM_REJECTED")
            offset += written
        os.fsync(descriptor)
    except OSError as exc:
        raise ProxyError("UPSTREAM_REJECTED") from exc
    finally:
        os.close(descriptor)


def _error_body(category: str) -> bytes:
    return json.dumps(
        {"error": category},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _local_status(category: str) -> int:
    return _LOCAL_STATUS.get(category, 502)


def process_local_request(
    *,
    method: str,
    path: str,
    content_length: str | None,
    body: bytes,
    contract: Mapping[str, Any],
    auth_path: str = "/run/phase2/provider-auth",
    receipt_path: str = "/output/receipt.json",
    provider_requester: ProviderRequester = perform_provider_request,
) -> tuple[int, bytes, dict[str, Any]]:
    """Apply every local gate and produce one sanitized receipt."""
    method_allowed = method == ALLOWED_METHOD
    path_allowed = path == ALLOWED_PATH
    auth_present = False
    provider_attempted = False
    provider_status: int | None = None
    response_size: int | None = None
    try:
        if not method_allowed:
            raise ProxyError("METHOD_BLOCKED")
        if not path_allowed:
            raise ProxyError("PATH_BLOCKED")
        try:
            declared_size = int(content_length) if content_length is not None else -1
        except (TypeError, ValueError) as exc:
            raise ProxyError("REQUEST_SIZE_BLOCKED") from exc
        if (
            declared_size <= 0
            or declared_size > MAXIMUM_BYTES
            or not isinstance(body, bytes)
            or len(body) != declared_size
        ):
            raise ProxyError("REQUEST_SIZE_BLOCKED")
        envelope = strict_object(body, "REQUEST_SCHEMA_BLOCKED")
        provider_payload = build_provider_request(envelope, contract)
        provider_body = _canonical_bytes(provider_payload)
        if len(provider_body) > MAXIMUM_BYTES:
            raise ProxyError("REQUEST_SIZE_BLOCKED")
        auth_value = read_auth_file(auth_path)
        auth_present = True
        provider_attempted = True
        provider_status, raw_response = provider_requester(provider_body, auth_value)
        if (
            isinstance(provider_status, bool)
            or not isinstance(provider_status, int)
            or not 100 <= provider_status <= 599
        ):
            raise ProxyError("UPSTREAM_REJECTED", provider_attempt_count=1)
        if 300 <= provider_status < 400:
            raise ProxyError(
                "REDIRECT_REJECTED",
                provider_http_status=provider_status,
                provider_attempt_count=1,
            )
        if provider_status == 429:
            raise ProxyError(
                "RATE_LIMIT_REJECTED",
                provider_http_status=provider_status,
                provider_attempt_count=1,
            )
        if not 200 <= provider_status < 300:
            raise ProxyError(
                "UPSTREAM_REJECTED",
                provider_http_status=provider_status,
                provider_attempt_count=1,
            )
        if not isinstance(raw_response, bytes):
            raise ProxyError(
                "RESPONSE_JSON_BLOCKED",
                provider_http_status=provider_status,
                provider_attempt_count=1,
            )
        response_size = len(raw_response)
        if response_size > MAXIMUM_BYTES:
            raise ProxyError(
                "RESPONSE_SIZE_BLOCKED",
                provider_http_status=provider_status,
                provider_attempt_count=1,
                response_size=min(response_size, MAXIMUM_BYTES + 1),
            )
        response = strict_object(raw_response, "RESPONSE_JSON_BLOCKED")
        candidate = extract_candidate(response, contract, envelope)
        relay_response = {
            "protocol_version": envelope["request"]["protocol_version"],
            "request_id": envelope["request"]["request_id"],
            "symbol": envelope["request"]["symbol"],
            "projection_sha256": envelope["request"]["projection_sha256"],
            "claims_candidate": candidate,
        }
        response_body = _canonical_bytes(relay_response)
        receipt = build_receipt(
            method_allowed=True,
            path_allowed=True,
            auth_present=True,
            provider_attempt_count=1,
            provider_http_status=provider_status,
            response_category="FORWARDED",
            response_size=response_size,
        )
        write_receipt(receipt, receipt_path)
        return 200, response_body, receipt
    except ProxyError as exc:
        attempts = max(
            int(provider_attempted),
            exc.provider_attempt_count,
        )
        receipt = build_receipt(
            method_allowed=method_allowed,
            path_allowed=path_allowed,
            auth_present=auth_present,
            provider_attempt_count=attempts,
            provider_http_status=(
                exc.provider_http_status
                if exc.provider_http_status is not None
                else provider_status
            ),
            response_category=exc.category,
            response_size=(
                exc.response_size
                if exc.response_size is not None
                else response_size
            ),
        )
        write_receipt(receipt, receipt_path)
        return _local_status(exc.category), _error_body(exc.category), receipt


class ProxyHTTPServer(HTTPServer):
    proxy_contract: dict[str, Any]


class ProxyHandler(BaseHTTPRequestHandler):
    """No-log local-only adapter endpoint for the fixed Relay request."""

    server_version = "TickFlowOpenAIProxy/1"
    sys_version = ""

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _send(self, status_code: int, body: bytes) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _handle(self, method: str) -> None:
        content_length = self.headers.get("Content-Length")
        body = b""
        if method == ALLOWED_METHOD and self.path == ALLOWED_PATH:
            try:
                declared_size = int(content_length) if content_length is not None else -1
            except (TypeError, ValueError):
                declared_size = -1
            if 0 < declared_size <= MAXIMUM_BYTES:
                body = self.rfile.read(declared_size)
        try:
            status_code, response_body, _receipt = process_local_request(
                method=method,
                path=self.path,
                content_length=content_length,
                body=body,
                contract=self.server.proxy_contract,
            )
        except ProxyError:
            status_code = 502
            response_body = _error_body("UPSTREAM_REJECTED")
        self._send(status_code, response_body)

    def do_POST(self) -> None:
        self._handle("POST")

    def _reject_method(self) -> None:
        self._handle(self.command)

    do_GET = _reject_method  # noqa: N815
    do_PUT = _reject_method  # noqa: N815
    do_DELETE = _reject_method  # noqa: N815
    do_PATCH = _reject_method  # noqa: N815
    do_HEAD = _reject_method  # noqa: N815
    do_OPTIONS = _reject_method  # noqa: N815


def create_server(
    address: tuple[str, int] = ("0.0.0.0", 8080),
    *,
    contract: dict[str, Any] | None = None,
) -> ProxyHTTPServer:
    server = ProxyHTTPServer(address, ProxyHandler)
    server.proxy_contract = contract if contract is not None else load_contract()
    return server


def main() -> int:
    try:
        contract = load_contract()
        server = create_server(contract=contract)
    except ProxyError:
        return 2
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
