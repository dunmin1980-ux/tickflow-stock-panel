"""Deterministic local Mock Provider for Phase 2B relay validation."""

from __future__ import annotations

import copy
import hashlib
import hmac
import importlib.util
import json
import os
import re
import stat
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

ALLOWED_METHOD = "POST"
ALLOWED_PATH = "/v1/typed-claims"
MAXIMUM_BYTES = 1_048_576
AUTH_PATH = Path("/run/phase2/provider-auth")
SCENARIO_PATH = Path("/input/scenario.json")
RECEIPT_PATH = Path("/output/receipt.json")
FACTORY_PATH = Path("/app/candidate_factory.py")
TIMEOUT_DELAY_SECONDS = 2.25
ALLOWED_SCENARIOS = {
    "valid_typed_candidate",
    "markdown_instead_of_json",
    "extra_free_text_field",
    "unknown_claim_type",
    "unknown_predicate",
    "wrong_symbol",
    "wrong_projection_sha",
    "raw_qfq_mismatch",
    "unsupported_fact_pointer",
    "trading_claim",
    "oversized_response",
    "timeout",
    "http_429",
    "http_500",
    "invalid_json",
    "multiple_json_documents",
    "empty_response",
    "wrong_request_id",
    "wrong_facts_sha",
    "sensitive_shape",
    "redirect_response",
    "external_url_response",
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
_GENERATION = {
    "temperature": 0,
    "response_format": "typed_claims_json",
    "tool_use": False,
    "web_browsing": False,
    "file_tools": False,
    "function_calling": False,
    "streaming": False,
    "retry_count": 0,
    "maximum_output_bytes": 1_048_576,
    "timeout_seconds": 2,
}
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_NOFOLLOW = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0


class MockProviderError(Exception):
    pass


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


def _read_regular(path: Path, maximum_bytes: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not 0 < metadata.st_size <= maximum_bytes
            ):
                raise MockProviderError("input_file_invalid")
            raw = os.read(descriptor, maximum_bytes + 1)
            if len(raw) != metadata.st_size:
                raise MockProviderError("input_file_invalid")
            return raw
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise MockProviderError("input_file_invalid") from exc


def _strict_object(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAXIMUM_BYTES:
        raise MockProviderError("json_invalid")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite")

    try:
        text = raw.decode("utf-8")
        value, end = json.JSONDecoder(parse_constant=reject_constant).raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MockProviderError("json_invalid") from exc
    if text[end:].strip() or not isinstance(value, dict):
        raise MockProviderError("json_invalid")
    return value


def _projection_hash(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("projection_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _validate_envelope(value: dict[str, Any]) -> None:
    if set(value) != {"request", "projection", "generation"}:
        raise MockProviderError("envelope_fields_invalid")
    request = value.get("request")
    projection = value.get("projection")
    generation = value.get("generation")
    if (
        not isinstance(request, dict)
        or not isinstance(projection, dict)
        or generation != _GENERATION
        or set(request) != _REQUEST_FIELDS
        or set(projection) != _PROJECTION_FIELDS
        or request.get("protocol_version") != 1
        or request.get("claims_schema_version") != 1
        or request.get("response_format") != "typed_claims_json"
        or request.get("symbol") != "000403.SZ"
        or projection.get("symbol") != "000403.SZ"
        or not _HEX_32.fullmatch(str(request.get("request_id", "")))
        or not _HEX_64.fullmatch(str(request.get("projection_sha256", "")))
        or request.get("projection_sha256") != projection.get("projection_sha256")
        or projection.get("projection_sha256") != _projection_hash(projection)
        or request.get("allowed_predicates") != projection.get("allowed_predicates")
        or not isinstance(request.get("allowed_claim_types"), list)
        or not isinstance(projection.get("safe_facts"), dict)
    ):
        raise MockProviderError("envelope_binding_invalid")


def _response_envelope(
    envelope: dict[str, Any],
    candidate_factory: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    request = envelope["request"]
    candidate = candidate_factory(envelope["projection"])
    return {
        "protocol_version": 1,
        "request_id": request["request_id"],
        "symbol": request["symbol"],
        "projection_sha256": request["projection_sha256"],
        "claims_candidate": candidate,
    }


def _claim_for(response: dict[str, Any], predicate: str) -> dict[str, Any]:
    return next(
        claim
        for claim in response["claims_candidate"]["claims"]
        if claim["predicate"] == predicate
    )


def build_scenario_response(
    envelope: dict[str, Any],
    scenario: str,
    candidate_factory: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Return one deterministic wire response for an approved test scenario."""
    _validate_envelope(envelope)
    if scenario not in ALLOWED_SCENARIOS:
        raise MockProviderError("scenario_invalid")
    response = copy.deepcopy(_response_envelope(envelope, candidate_factory))
    status = 200
    headers: dict[str, str] = {"Content-Type": "application/json"}
    delay_seconds = 0.0

    if scenario == "markdown_instead_of_json":
        body = b"# Generated review\n"
    elif scenario == "oversized_response":
        body = b"{" + (b"x" * MAXIMUM_BYTES) + b"}"
    elif scenario == "invalid_json":
        body = b"{invalid"
    elif scenario == "multiple_json_documents":
        body = b"{}{}"
    elif scenario == "empty_response":
        body = b""
    elif scenario == "http_429":
        status = 429
        body = b""
    elif scenario == "http_500":
        status = 500
        body = b""
    elif scenario == "redirect_response":
        status = 302
        headers = {"Location": "http://redirect.invalid/"}
        body = b""
    else:
        if scenario == "extra_free_text_field":
            response["claims_candidate"]["analysis"] = "free text"
        elif scenario == "unknown_claim_type":
            response["claims_candidate"]["claims"][0]["claim_type"] = "FORECAST"
        elif scenario == "unknown_predicate":
            response["claims_candidate"]["claims"][0]["predicate"] = (
                "unapproved_predicate"
            )
        elif scenario == "wrong_symbol":
            response["symbol"] = "600489.SH"
        elif scenario == "wrong_projection_sha":
            response["projection_sha256"] = "0" * 64
        elif scenario == "raw_qfq_mismatch":
            claim = _claim_for(response, "macd_dif_vs_dea")
            claim["object"]["right"]["price_basis"] = "raw"
        elif scenario == "unsupported_fact_pointer":
            response["claims_candidate"]["claims"][0]["provenance"][
                "fact_refs"
            ][0] = "/unsupported/value"
        elif scenario == "trading_claim":
            response["claims_candidate"]["trading_advice"] = True
        elif scenario == "timeout":
            delay_seconds = TIMEOUT_DELAY_SECONDS
        elif scenario == "wrong_request_id":
            response["request_id"] = "b" * 32
        elif scenario == "wrong_facts_sha":
            response["claims_candidate"]["claims"][0]["provenance"][
                "facts_sha256"
            ] = "0" * 64
        elif scenario == "sensitive_shape":
            response["claims_candidate"]["api_key"] = "redacted-shape"
        elif scenario == "external_url_response":
            response["external_url"] = "https://example.invalid/result"
        body = _canonical_bytes(response)
    return {
        "status": status,
        "body": body,
        "headers": headers,
        "delay_seconds": delay_seconds,
    }


def _read_auth() -> str:
    try:
        value = _read_regular(AUTH_PATH, 4096).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise MockProviderError("auth_invalid") from exc
    if not value or "\n" in value or "\r" in value:
        raise MockProviderError("auth_invalid")
    return value


def _read_scenario() -> str:
    value = _strict_object(_read_regular(SCENARIO_PATH, 4096))
    if set(value) != {"scenario"} or value.get("scenario") not in ALLOWED_SCENARIOS:
        raise MockProviderError("scenario_invalid")
    return str(value["scenario"])


def _load_factory() -> Callable[[dict[str, Any]], dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("candidate_factory", FACTORY_PATH)
    if spec is None or spec.loader is None:
        raise MockProviderError("factory_invalid")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(module, ModuleType) or not callable(
        getattr(module, "generate_candidate", None)
    ):
        raise MockProviderError("factory_invalid")
    return module.generate_candidate


def _write_receipt(value: dict[str, Any]) -> None:
    raw = _canonical_bytes(value)
    try:
        descriptor = os.open(RECEIPT_PATH, os.O_WRONLY | os.O_TRUNC | _NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise MockProviderError("receipt_invalid")
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise MockProviderError("receipt_invalid")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise MockProviderError("receipt_invalid") from exc


def _write_ready() -> None:
    _write_receipt(
        {
            "receipt_schema_version": 1,
            "status": "READY",
            "auth_present": True,
        }
    )


class MockProviderHandler(BaseHTTPRequestHandler):
    server_version = "Phase2MockProvider/1"
    sys_version = ""

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _send(
        self,
        status_code: int,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        try:
            self.send_response(status_code)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _reject_method(self) -> None:
        _write_receipt(
            {
                "receipt_schema_version": 1,
                "request_count": 1,
                "method_allowed": False,
                "path_allowed": self.path == ALLOWED_PATH,
                "auth_valid": False,
                "envelope_valid": False,
                "scenario": None,
                "response_category": "METHOD_BLOCKED",
                "http_status": 405,
                "response_size": 0,
            }
        )
        self._send(405)

    do_GET = _reject_method  # noqa: N815
    do_PUT = _reject_method  # noqa: N815
    do_DELETE = _reject_method  # noqa: N815

    def do_POST(self) -> None:
        if self.path != ALLOWED_PATH:
            _write_receipt(
                {
                    "receipt_schema_version": 1,
                    "request_count": 1,
                    "method_allowed": True,
                    "path_allowed": False,
                    "auth_valid": False,
                    "envelope_valid": False,
                    "scenario": None,
                    "response_category": "PATH_BLOCKED",
                    "http_status": 404,
                    "response_size": 0,
                }
            )
            self._send(404)
            return
        try:
            expected_auth = _read_auth()
        except MockProviderError:
            self._send(503)
            return
        supplied_auth = self.headers.get("Authorization", "")
        auth_valid = hmac.compare_digest(
            supplied_auth,
            f"Bearer {expected_auth}",
        )
        if not auth_valid:
            _write_receipt(
                {
                    "receipt_schema_version": 1,
                    "request_count": 1,
                    "method_allowed": True,
                    "path_allowed": True,
                    "auth_valid": False,
                    "envelope_valid": False,
                    "scenario": None,
                    "response_category": "AUTH_BLOCKED",
                    "http_status": 401,
                    "response_size": 0,
                }
            )
            self._send(401)
            return
        try:
            declared_size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            declared_size = -1
        if not 0 < declared_size <= MAXIMUM_BYTES:
            self._send(413)
            return
        raw = self.rfile.read(declared_size)
        try:
            envelope = _strict_object(raw)
            _validate_envelope(envelope)
            scenario = _read_scenario()
            factory = _load_factory()
            result = build_scenario_response(envelope, scenario, factory)
        except MockProviderError:
            _write_receipt(
                {
                    "receipt_schema_version": 1,
                    "request_count": 1,
                    "method_allowed": True,
                    "path_allowed": True,
                    "auth_valid": True,
                    "envelope_valid": False,
                    "scenario": None,
                    "response_category": "ENVELOPE_BLOCKED",
                    "http_status": 400,
                    "response_size": 0,
                }
            )
            self._send(400)
            return
        if result["delay_seconds"]:
            time.sleep(result["delay_seconds"])
        _write_receipt(
            {
                "receipt_schema_version": 1,
                "request_count": 1,
                "method_allowed": True,
                "path_allowed": True,
                "auth_valid": True,
                "envelope_valid": True,
                "scenario": scenario,
                "response_category": "SCENARIO_RESPONSE",
                "http_status": result["status"],
                "response_size": len(result["body"]),
            }
        )
        self._send(result["status"], result["body"], result["headers"])


def create_server(address: tuple[str, int] = ("0.0.0.0", 8081)) -> HTTPServer:
    return HTTPServer(address, MockProviderHandler)


def main() -> int:
    try:
        _read_auth()
        _read_scenario()
        _load_factory()
    except MockProviderError:
        return 2
    server = create_server()
    try:
        _write_ready()
        server.handle_request()
    except MockProviderError:
        return 2
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
