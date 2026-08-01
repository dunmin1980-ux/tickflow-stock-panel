"""Standard-library-only Provider Relay for the Phase 2B mock boundary."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROXY_HOST = "phase2-egress-proxy"
PROXY_PORT = 8080
PROXY_PATH = "/v1/typed-claims"
DIRECT_PROVIDER_HOST = "phase2-mock-provider"
DIRECT_PROVIDER_PORT = 8081
ARBITRARY_HOST = "phase2-unapproved.invalid"
PUBLIC_TEST_IP = "192.0.2.1"
HOST_GATEWAY = "host.docker.internal"
DOCKER_CONTROL_HOST = "host.docker.internal"
DOCKER_CONTROL_PORT = 2375
DOCKER_CONTROL_PATH = Path("/var/run/docker.sock")
REQUEST_PATH = Path("/input/request.json")
PROJECTION_PATH = Path("/input/projection.json")
RESPONSE_PATH = Path("/output/response.json")
RECEIPT_PATH = Path("/output/receipt.json")
MAXIMUM_BYTES = 1_048_576
TIMEOUT_SECONDS = 2
ALLOWED_MODES = {"run", "probe"}
FIXED_SYMBOL = "000403.SZ"
CLAIM_TYPES = (
    "BOOLEAN_STATE",
    "DATA_QUALITY_STATE",
    "ENUM_STATE",
    "NUMERIC_OBSERVATION",
    "ORDERED_RELATION",
    "SAME_BASIS_COMPARISON",
    "SCOPE_NOTICE",
    "VENDOR_PENDING_NOTICE",
)
REQUEST_FIELDS = {
    "protocol_version",
    "request_id",
    "symbol",
    "projection_sha256",
    "claims_schema_version",
    "allowed_claim_types",
    "allowed_predicates",
    "response_format",
}
PROJECTION_FIELDS = {
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
RESPONSE_FIELDS = {
    "protocol_version",
    "request_id",
    "symbol",
    "projection_sha256",
    "claims_candidate",
}
CANDIDATE_FIELDS = {
    "candidate_schema_version",
    "projection_sha256",
    "symbol",
    "name",
    "trade_date",
    "timezone",
    "claims",
    "trading_advice",
}
RESULT_VALUES = {"ALLOWED", "BLOCKED", "NOT_REACHABLE", "REDIRECT_REJECTED"}
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_NOFOLLOW = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0


class RelayError(Exception):
    def __init__(
        self,
        category: str,
        *,
        http_status: int | None = None,
        attempt_count: int = 0,
        response_size: int | None = None,
        response_sha256: str | None = None,
    ) -> None:
        self.category = category
        self.http_status = http_status
        self.attempt_count = attempt_count
        self.response_size = response_size
        self.response_sha256 = response_sha256
        super().__init__(category)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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


def _strict_object(raw: bytes, category: str) -> dict[str, Any]:
    if not raw or len(raw) > MAXIMUM_BYTES:
        raise RelayError(category)

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite")

    try:
        text = raw.decode("utf-8")
        value, end = json.JSONDecoder(parse_constant=reject_constant).raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RelayError(category) from exc
    if text[end:].strip() or not isinstance(value, dict):
        raise RelayError(category)
    return value


def _read_regular(path: Path, category: str) -> bytes:
    flags = os.O_RDONLY | _NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAXIMUM_BYTES:
                raise RelayError(category)
            chunks: list[bytes] = []
            remaining = MAXIMUM_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) != metadata.st_size or len(raw) > MAXIMUM_BYTES:
                raise RelayError(category)
            return raw
        finally:
            os.close(descriptor)
    except RelayError:
        raise
    except OSError as exc:
        raise RelayError(category) from exc


def _write_existing(path: Path, value: bytes, category: str) -> None:
    flags = os.O_WRONLY | os.O_TRUNC | _NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RelayError(category)
            offset = 0
            while offset < len(value):
                written = os.write(descriptor, value[offset:])
                if written <= 0:
                    raise RelayError(category)
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except RelayError:
        raise
    except OSError as exc:
        raise RelayError(category) from exc


def _reset_response() -> None:
    _write_existing(RESPONSE_PATH, b"\n", "OUTPUT_WRITE_FAILED")


def _projection_hash(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("projection_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _load_projection() -> dict[str, Any]:
    value = _strict_object(
        _read_regular(PROJECTION_PATH, "PROJECTION_INVALID"),
        "PROJECTION_INVALID",
    )
    predicates = value.get("allowed_predicates")
    if (
        set(value) != PROJECTION_FIELDS
        or value.get("projection_schema_version") != 1
        or value.get("symbol") != FIXED_SYMBOL
        or value.get("timezone") != "Asia/Shanghai"
        or not isinstance(value.get("safe_facts"), dict)
        or not isinstance(predicates, list)
        or not predicates
        or predicates != sorted(set(predicates))
        or not _HEX_64.fullmatch(str(value.get("facts_sha256", "")))
        or not _HEX_64.fullmatch(str(value.get("projection_sha256", "")))
        or value.get("projection_sha256") != _projection_hash(value)
    ):
        raise RelayError("PROJECTION_INVALID")
    return value


def _load_request(projection: dict[str, Any]) -> dict[str, Any]:
    value = _strict_object(
        _read_regular(REQUEST_PATH, "REQUEST_INVALID"),
        "REQUEST_INVALID",
    )
    if (
        set(value) != REQUEST_FIELDS
        or value.get("protocol_version") != 1
        or value.get("claims_schema_version") != 1
        or value.get("symbol") != FIXED_SYMBOL
        or value.get("response_format") != "typed_claims_json"
        or not _HEX_32.fullmatch(str(value.get("request_id", "")))
        or not _HEX_64.fullmatch(str(value.get("projection_sha256", "")))
        or value.get("projection_sha256") != projection["projection_sha256"]
        or value.get("allowed_claim_types") != list(CLAIM_TYPES)
        or value.get("allowed_predicates") != projection["allowed_predicates"]
    ):
        raise RelayError("REQUEST_INVALID")
    return value


def _generation_controls() -> dict[str, Any]:
    return {
        "temperature": 0,
        "response_format": "typed_claims_json",
        "tool_use": False,
        "web_browsing": False,
        "file_tools": False,
        "function_calling": False,
        "streaming": False,
        "retry_count": 0,
        "maximum_output_bytes": MAXIMUM_BYTES,
        "timeout_seconds": 2,
    }


def _provider_body(request: dict[str, Any], projection: dict[str, Any]) -> bytes:
    return _canonical_bytes(
        {
            "request": request,
            "projection": projection,
            "generation": _generation_controls(),
        }
    )


def _validate_response(
    raw: bytes,
    request: dict[str, Any],
    projection: dict[str, Any],
) -> None:
    try:
        value = _strict_object(raw, "STRICT_JSON_REJECTED")
    except RelayError as exc:
        exc.attempt_count = 1
        exc.response_size = len(raw)
        exc.response_sha256 = hashlib.sha256(raw).hexdigest()
        raise
    candidate = value.get("claims_candidate")
    if set(value) != RESPONSE_FIELDS or not isinstance(candidate, dict):
        raise RelayError(
            "BINDING_REJECTED",
            attempt_count=1,
            response_size=len(raw),
            response_sha256=hashlib.sha256(raw).hexdigest(),
        )
    if (
        value.get("protocol_version") != 1
        or value.get("request_id") != request["request_id"]
        or value.get("symbol") != request["symbol"]
        or value.get("projection_sha256") != request["projection_sha256"]
        or set(candidate) != CANDIDATE_FIELDS
        or candidate.get("candidate_schema_version") != 1
        or candidate.get("symbol") != request["symbol"]
        or candidate.get("projection_sha256") != request["projection_sha256"]
        or candidate.get("name") != projection["name"]
        or candidate.get("trade_date") != projection["trade_date"]
        or candidate.get("timezone") != projection["timezone"]
        or not isinstance(candidate.get("claims"), list)
        or not candidate.get("claims")
        or candidate.get("trading_advice") is not False
    ):
        raise RelayError(
            "BINDING_REJECTED",
            attempt_count=1,
            response_size=len(raw),
            response_sha256=hashlib.sha256(raw).hexdigest(),
        )


def _provider_request(body: bytes) -> tuple[bytes, int]:
    connection = http.client.HTTPConnection(
        PROXY_HOST,
        PROXY_PORT,
        timeout=TIMEOUT_SECONDS,
    )
    try:
        connection.request(
            "POST",
            PROXY_PATH,
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        status_code = response.status
        if 300 <= status_code < 400:
            raise RelayError(
                "REDIRECT_REJECTED",
                http_status=status_code,
                attempt_count=1,
            )
        if not 200 <= status_code < 300:
            raise RelayError(
                "HTTP_REJECTED",
                http_status=status_code,
                attempt_count=1,
            )
        declared = response.getheader("Content-Length")
        if declared is not None:
            try:
                if int(declared) > MAXIMUM_BYTES:
                    raise RelayError(
                        "RESPONSE_TOO_LARGE",
                        http_status=status_code,
                        attempt_count=1,
                    )
            except ValueError as exc:
                raise RelayError(
                    "HTTP_REJECTED",
                    http_status=status_code,
                    attempt_count=1,
                ) from exc
        raw = response.read(MAXIMUM_BYTES + 1)
        if len(raw) > MAXIMUM_BYTES:
            raise RelayError(
                "RESPONSE_TOO_LARGE",
                http_status=status_code,
                attempt_count=1,
            )
        if not raw:
            raise RelayError(
                "EMPTY_RESPONSE",
                http_status=status_code,
                attempt_count=1,
                response_size=0,
                response_sha256=hashlib.sha256(raw).hexdigest(),
            )
        return raw, status_code
    except RelayError:
        raise
    except TimeoutError as exc:
        raise RelayError("TIMEOUT", attempt_count=1) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise RelayError("HTTP_REJECTED", attempt_count=1) from exc
    finally:
        connection.close()


def _receipt(
    *,
    status: str,
    started_at: str,
    completed_at: str,
    error_category: str | None,
    http_status: int | None,
    attempt_count: int,
    response_size: int | None,
    response_sha256: str | None,
) -> dict[str, Any]:
    return {
        "receipt_schema_version": 1,
        "status": status,
        "error_category": error_category,
        "provider_http_status": http_status,
        "provider_attempt_count": attempt_count,
        "retry_count": 0,
        "response_size": response_size,
        "response_sha256": response_sha256,
        "started_at": started_at,
        "completed_at": completed_at,
    }


def _write_receipt(value: dict[str, Any]) -> None:
    _write_existing(RECEIPT_PATH, _canonical_bytes(value), "OUTPUT_WRITE_FAILED")


def run() -> int:
    started_at = _timestamp()
    try:
        _reset_response()
        projection = _load_projection()
        request = _load_request(projection)
        raw, status_code = _provider_request(_provider_body(request, projection))
        _validate_response(raw, request, projection)
        _write_existing(RESPONSE_PATH, raw, "OUTPUT_WRITE_FAILED")
        _write_receipt(
            _receipt(
                status="SUCCEEDED",
                started_at=started_at,
                completed_at=_timestamp(),
                error_category=None,
                http_status=status_code,
                attempt_count=1,
                response_size=len(raw),
                response_sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
        return 0
    except RelayError as exc:
        try:
            _reset_response()
            _write_receipt(
                _receipt(
                    status="REJECTED",
                    started_at=started_at,
                    completed_at=_timestamp(),
                    error_category=exc.category,
                    http_status=exc.http_status,
                    attempt_count=exc.attempt_count,
                    response_size=exc.response_size,
                    response_sha256=exc.response_sha256,
                )
            )
        except RelayError:
            pass
        return 2
    except Exception:
        try:
            _reset_response()
            _write_receipt(
                _receipt(
                    status="REJECTED",
                    started_at=started_at,
                    completed_at=_timestamp(),
                    error_category="INTERNAL_ERROR",
                    http_status=None,
                    attempt_count=0,
                    response_size=None,
                    response_sha256=None,
                )
            )
        except RelayError:
            pass
        return 2


def _probe_http(
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes | None,
) -> str:
    connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT_SECONDS)
    try:
        connection.request(
            method,
            path,
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        response.read(min(MAXIMUM_BYTES + 1, 65_536))
        if 200 <= response.status < 300:
            return "ALLOWED"
        if 300 <= response.status < 400:
            return "REDIRECT_REJECTED"
        return "BLOCKED"
    except (OSError, TimeoutError, http.client.HTTPException):
        return "NOT_REACHABLE"
    finally:
        connection.close()


def _probe_control_file() -> str:
    try:
        descriptor = os.open(
            DOCKER_CONTROL_PATH,
            os.O_RDONLY | _NOFOLLOW,
        )
    except OSError:
        return "BLOCKED"
    else:
        os.close(descriptor)
        return "ALLOWED"


def probe() -> int:
    started_at = _timestamp()
    try:
        _reset_response()
        projection = _load_projection()
        request = _load_request(projection)
        body = _provider_body(request, projection)
        results = {
            "proxy_exact": _probe_http(
                PROXY_HOST,
                PROXY_PORT,
                "POST",
                PROXY_PATH,
                body,
            ),
            "direct_provider": _probe_http(
                DIRECT_PROVIDER_HOST,
                DIRECT_PROVIDER_PORT,
                "POST",
                PROXY_PATH,
                body,
            ),
            "arbitrary_hostname": _probe_http(
                ARBITRARY_HOST,
                443,
                "POST",
                PROXY_PATH,
                body,
            ),
            "public_test_ip": _probe_http(
                PUBLIC_TEST_IP,
                443,
                "POST",
                PROXY_PATH,
                body,
            ),
            "host_gateway": _probe_http(
                HOST_GATEWAY,
                80,
                "POST",
                PROXY_PATH,
                body,
            ),
            "docker_control_file": _probe_control_file(),
            "docker_control_tcp": _probe_http(
                DOCKER_CONTROL_HOST,
                DOCKER_CONTROL_PORT,
                "POST",
                "/version",
                None,
            ),
            "proxy_wrong_path": _probe_http(
                PROXY_HOST,
                PROXY_PORT,
                "POST",
                "/blocked",
                body,
            ),
            "proxy_wrong_method": _probe_http(
                PROXY_HOST,
                PROXY_PORT,
                "GET",
                PROXY_PATH,
                None,
            ),
        }
        if not set(results.values()) <= RESULT_VALUES:
            raise RelayError("PROBE_FAILED", attempt_count=1)
        output = _canonical_bytes(
            {"probe_schema_version": 1, "results": results}
        )
        _write_existing(RESPONSE_PATH, output, "OUTPUT_WRITE_FAILED")
        _write_receipt(
            _receipt(
                status="SUCCEEDED",
                started_at=started_at,
                completed_at=_timestamp(),
                error_category=None,
                http_status=200,
                attempt_count=1,
                response_size=len(output),
                response_sha256=hashlib.sha256(output).hexdigest(),
            )
        )
        return 0
    except RelayError as exc:
        try:
            _reset_response()
            _write_receipt(
                _receipt(
                    status="REJECTED",
                    started_at=started_at,
                    completed_at=_timestamp(),
                    error_category=exc.category,
                    http_status=exc.http_status,
                    attempt_count=exc.attempt_count,
                    response_size=exc.response_size,
                    response_sha256=exc.response_sha256,
                )
            )
        except RelayError:
            pass
        return 2


def main(arguments: list[str]) -> int:
    if len(arguments) != 1 or arguments[0] not in ALLOWED_MODES:
        return 2
    return run() if arguments[0] == "run" else probe()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
