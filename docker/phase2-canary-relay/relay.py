"""One-shot, fail-closed Relay for the Phase 2B Provider Canary."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROXY_HOST = "phase2-egress-proxy"
PROXY_PORT = 8080
PROXY_PATH = "/v1/typed-claims"
REQUEST_PATH = Path("/input/request.json")
PROJECTION_PATH = Path("/input/projection.json")
OUTPUT_DIR = Path("/output")
RUNTIME_CONTRACT_PATH = Path("/relay/runtime-contract.json")
MAXIMUM_BYTES = 1_048_576
MAXIMUM_CONTRACT_BYTES = 16_384
FIXED_SYMBOL = "000403.SZ"
FIXED_TRADE_DATE = "2026-07-31"
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
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_STAGE_EVENT_FIELDS = {
    "event",
    "occurred",
    "wall_time",
    "monotonic_ns",
    "http_status",
    "byte_count",
}
_RELAY_EVENTS = (
    "relay_started",
    "proxy_connect_started",
    "proxy_connect_completed",
    "request_submitted",
    "response_wait_started",
    "response_received",
    "candidate_write_started",
    "candidate_write_completed",
    "candidate_ready_published",
)
_RECEIPT_FIELDS = {
    "receipt_schema_version",
    "request_id",
    "component",
    "status",
    "exit_code",
    "terminal_status",
    "terminal_reason_source",
    "error_category",
    "proxy_http_status",
    "proxy_request_count",
    "retry_count",
    "response_size",
    "response_sha256",
    "started_at",
    "completed_at",
    *_RELAY_EVENTS,
}
_PROTOCOL_ERROR_CATEGORIES = {
    "EMPTY_RESPONSE",
    "HTTP_REJECTED",
    "REDIRECT_REJECTED",
    "RELAY_PROTOCOL_REJECTED",
    "RESPONSE_TOO_LARGE",
    "STRICT_JSON_REJECTED",
}


class RelayError(Exception):
    """Stable fail-closed category safe for receipts and logs."""

    def __init__(
        self,
        category: str,
        *,
        proxy_request_count: int = 0,
        http_status: int | None = None,
        response_size: int | None = None,
        response_sha256: str | None = None,
    ) -> None:
        self.category = category
        self.proxy_request_count = proxy_request_count
        self.http_status = http_status
        self.response_size = response_size
        self.response_sha256 = response_sha256
        super().__init__(category)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RelayStageRecorder:
    """Record only sanitized Relay-local progress events."""

    def __init__(
        self,
        *,
        wall_clock: Callable[[], str] = _timestamp,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._wall_clock = wall_clock
        self._monotonic_ns = monotonic_ns
        self._events = {
            name: self._empty_event(name) for name in _RELAY_EVENTS
        }
        self.record("relay_started")

    @staticmethod
    def _empty_event(name: str) -> dict[str, Any]:
        return {
            "event": name,
            "occurred": False,
            "wall_time": None,
            "monotonic_ns": None,
            "http_status": None,
            "byte_count": None,
        }

    def record(
        self,
        name: str,
        *,
        http_status: int | None = None,
        byte_count: int | None = None,
    ) -> None:
        if name not in self._events or self._events[name]["occurred"] is True:
            raise RelayError("INTERNAL_ERROR")
        wall_time = self._wall_clock()
        monotonic_ns = self._monotonic_ns()
        if (
            not isinstance(wall_time, str)
            or not wall_time
            or isinstance(monotonic_ns, bool)
            or not isinstance(monotonic_ns, int)
            or monotonic_ns < 0
            or (
                http_status is not None
                and (
                    isinstance(http_status, bool)
                    or not isinstance(http_status, int)
                    or not 100 <= http_status <= 599
                )
            )
            or (
                byte_count is not None
                and (
                    isinstance(byte_count, bool)
                    or not isinstance(byte_count, int)
                    or byte_count < 0
                )
            )
        ):
            raise RelayError("INTERNAL_ERROR")
        self._events[name] = {
            "event": name,
            "occurred": True,
            "wall_time": wall_time,
            "monotonic_ns": monotonic_ns,
            "http_status": http_status,
            "byte_count": byte_count,
        }

    def events(self) -> dict[str, dict[str, Any]]:
        return {name: dict(value) for name, value in self._events.items()}


Requester = Callable[
    [bytes, int, RelayStageRecorder],
    tuple[int, bytes],
]


def _canonical_bytes(value: object) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise RelayError("JSON_ENCODING_FAILED") from exc
    return (rendered + "\n").encode("utf-8")


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


def _read_regular(path: Path, category: str, maximum_bytes: int) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RelayError(category) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or not 0 < before.st_size <= maximum_bytes
    ):
        raise RelayError(category)
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise RelayError(category) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise RelayError(category)
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.lstat(path)
        if (
            len(raw) != opened.st_size
            or len(raw) > maximum_bytes
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise RelayError(category)
        return raw
    except RelayError:
        raise
    except OSError as exc:
        raise RelayError(category) from exc
    finally:
        os.close(descriptor)


def load_runtime_contract(path: str) -> dict[str, Any]:
    raw = _read_regular(
        Path(path),
        "RUNTIME_CONTRACT_INVALID",
        MAXIMUM_CONTRACT_BYTES,
    )
    value = _strict_object(raw, "RUNTIME_CONTRACT_INVALID")
    expected = {
        "canary_runtime_contract_version": 1,
        "cleanup_timeout_seconds": 15,
        "host_orchestrator_timeout_seconds": 90,
        "maximum_provider_attempts": 1,
        "provider_connect_timeout_seconds": 10,
        "provider_read_timeout_seconds": 60,
        "provider_total_timeout_seconds": 60,
        "relay_candidate_wait_timeout_seconds": 75,
        "retry_count": 0,
    }
    if value != expected or raw != _canonical_bytes(expected):
        raise RelayError("RUNTIME_CONTRACT_INVALID")
    return value


def generation_controls(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "temperature": 0,
        "response_format": "typed_claims_json",
        "tool_use": False,
        "web_browsing": False,
        "file_tools": False,
        "function_calling": False,
        "streaming": False,
        "retry_count": contract["retry_count"],
        "maximum_output_bytes": MAXIMUM_BYTES,
        "timeout_seconds": contract["relay_candidate_wait_timeout_seconds"],
    }


def _projection_hash(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("projection_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _load_projection(path: Path) -> dict[str, Any]:
    value = _strict_object(
        _read_regular(path, "PROJECTION_INVALID", MAXIMUM_BYTES),
        "PROJECTION_INVALID",
    )
    predicates = value.get("allowed_predicates")
    if (
        set(value) != PROJECTION_FIELDS
        or value.get("projection_schema_version") != 1
        or value.get("symbol") != FIXED_SYMBOL
        or value.get("trade_date") != FIXED_TRADE_DATE
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


def _load_request(path: Path, projection: dict[str, Any]) -> dict[str, Any]:
    value = _strict_object(
        _read_regular(path, "REQUEST_INVALID", MAXIMUM_BYTES),
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


def _provider_body(
    request: dict[str, Any],
    projection: dict[str, Any],
    contract: dict[str, Any],
) -> bytes:
    return _canonical_bytes(
        {
            "request": request,
            "projection": projection,
            "generation": generation_controls(contract),
        }
    )


def _validate_output_dir(path: Path) -> int:
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RelayError("OUTPUT_DIRECTORY_INVALID")
        descriptor = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    except RelayError:
        raise
    except OSError as exc:
        raise RelayError("OUTPUT_DIRECTORY_INVALID") from exc
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        os.close(descriptor)
        raise RelayError("OUTPUT_DIRECTORY_INVALID")
    return descriptor


def _write_atomic(path: Path, raw: bytes, category: str) -> None:
    directory_descriptor = _validate_output_dir(path.parent)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.partial"
    descriptor: int | None = None
    try:
        if path.exists() or path.is_symlink():
            raise RelayError("CANDIDATE_ALREADY_EXISTS")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
        )
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise RelayError(category)
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if path.exists() or path.is_symlink():
            raise RelayError("CANDIDATE_ALREADY_EXISTS")
        os.replace(temporary, path)
        os.fsync(directory_descriptor)
    except RelayError:
        raise
    except OSError as exc:
        raise RelayError(category) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        os.close(directory_descriptor)


def _remove_published(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        directory = _validate_output_dir(path.parent)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except (OSError, RelayError):
        pass


def publish_candidate(
    relay_response: dict[str, Any],
    request: dict[str, Any],
    projection: dict[str, Any],
    output_dir: str,
    stage_recorder: RelayStageRecorder | None = None,
) -> dict[str, Any]:
    candidate = relay_response.get("claims_candidate")
    if (
        set(relay_response) != RESPONSE_FIELDS
        or relay_response.get("protocol_version") != 1
        or relay_response.get("request_id") != request.get("request_id")
        or relay_response.get("symbol") != request.get("symbol")
        or relay_response.get("projection_sha256")
        != projection.get("projection_sha256")
        or not isinstance(candidate, dict)
    ):
        raise RelayError("CANDIDATE_BINDING_INVALID", proxy_request_count=1)

    output = Path(output_dir)
    candidate_path = output / "candidate.json"
    marker_path = output / "candidate.ready.json"
    if (
        candidate_path.exists()
        or candidate_path.is_symlink()
        or marker_path.exists()
        or marker_path.is_symlink()
    ):
        raise RelayError("CANDIDATE_ALREADY_EXISTS")

    raw = _canonical_bytes(relay_response)
    marker = {
        "candidate_sha256": hashlib.sha256(raw).hexdigest(),
        "projection_sha256": projection["projection_sha256"],
        "request_id": request["request_id"],
    }
    try:
        if stage_recorder is not None:
            stage_recorder.record("candidate_write_started")
        _write_atomic(candidate_path, raw, "CANDIDATE_PUBLISH_FAILED")
        if stage_recorder is not None:
            stage_recorder.record(
                "candidate_write_completed",
                byte_count=len(raw),
            )
        _write_atomic(
            marker_path,
            _canonical_bytes(marker),
            "READY_MARKER_PUBLISH_FAILED",
        )
        if stage_recorder is not None:
            stage_recorder.record("candidate_ready_published")
    except RelayError as exc:
        _remove_published(marker_path)
        _remove_published(candidate_path)
        if exc.category == "CANDIDATE_ALREADY_EXISTS":
            raise
        raise RelayError(
            exc.category,
            proxy_request_count=1,
        ) from exc
    return marker


def _default_requester(
    body: bytes,
    timeout_seconds: int,
    stage_recorder: RelayStageRecorder,
) -> tuple[int, bytes]:
    deadline = time.monotonic() + timeout_seconds
    connection = http.client.HTTPConnection(
        PROXY_HOST,
        PROXY_PORT,
        timeout=max(0.001, deadline - time.monotonic()),
    )
    try:
        stage_recorder.record("proxy_connect_started")
        try:
            connection.connect()
        except TimeoutError as exc:
            raise RelayError("RELAY_TIMEOUT", proxy_request_count=1) from exc
        except OSError as exc:
            raise RelayError(
                "RELAY_PROXY_CONNECT_FAILED",
                proxy_request_count=1,
            ) from exc
        stage_recorder.record("proxy_connect_completed")
        connection.request(
            "POST",
            PROXY_PATH,
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        stage_recorder.record("request_submitted", byte_count=len(body))
        if connection.sock is not None:
            connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
        stage_recorder.record("response_wait_started")
        response = connection.getresponse()
        status = response.status
        if 300 <= status < 400:
            raise RelayError(
                "REDIRECT_REJECTED",
                proxy_request_count=1,
                http_status=status,
            )
        if not 200 <= status < 300:
            raise RelayError(
                "HTTP_REJECTED",
                proxy_request_count=1,
                http_status=status,
            )
        declared = response.getheader("Content-Length")
        if declared is not None:
            try:
                if int(declared) > MAXIMUM_BYTES:
                    raise RelayError(
                        "RESPONSE_TOO_LARGE",
                        proxy_request_count=1,
                        http_status=status,
                    )
            except ValueError as exc:
                raise RelayError(
                    "HTTP_REJECTED",
                    proxy_request_count=1,
                    http_status=status,
                ) from exc
        if connection.sock is not None:
            connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
        raw = response.read(MAXIMUM_BYTES + 1)
        if time.monotonic() > deadline:
            raise RelayError("RELAY_TIMEOUT", proxy_request_count=1)
        if not raw:
            raise RelayError(
                "EMPTY_RESPONSE",
                proxy_request_count=1,
                http_status=status,
                response_size=0,
                response_sha256=hashlib.sha256(raw).hexdigest(),
            )
        if len(raw) > MAXIMUM_BYTES:
            raise RelayError(
                "RESPONSE_TOO_LARGE",
                proxy_request_count=1,
                http_status=status,
            )
        stage_recorder.record(
            "response_received",
            http_status=status,
            byte_count=len(raw),
        )
        return status, raw
    except RelayError:
        raise
    except TimeoutError as exc:
        raise RelayError("RELAY_TIMEOUT", proxy_request_count=1) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise RelayError("RELAY_PROTOCOL_REJECTED", proxy_request_count=1) from exc
    finally:
        connection.close()


def _receipt(
    *,
    status: str,
    started_at: str,
    request_id: str | None,
    stage_recorder: RelayStageRecorder,
    error: RelayError | None,
    response_size: int | None = None,
    response_sha256: str | None = None,
) -> dict[str, Any]:
    if error is None:
        terminal_status = "RELAY_COMPLETED"
    elif error.category in {"RELAY_TIMEOUT", "RELAY_PROXY_CONNECT_FAILED"}:
        terminal_status = error.category
    elif error.category in _PROTOCOL_ERROR_CATEGORIES:
        terminal_status = "RELAY_PROTOCOL_REJECTED"
    else:
        terminal_status = "RELAY_PROCESS_ERROR"
    value = {
        "receipt_schema_version": 2,
        "request_id": request_id,
        "component": "relay",
        "status": status,
        "exit_code": 0 if error is None else 2,
        "terminal_status": terminal_status,
        "terminal_reason_source": "relay_self",
        "error_category": error.category if error else None,
        "proxy_http_status": error.http_status if error else 200,
        "proxy_request_count": error.proxy_request_count if error else 1,
        "retry_count": 0,
        "response_size": error.response_size if error else response_size,
        "response_sha256": error.response_sha256 if error else response_sha256,
        "started_at": started_at,
        "completed_at": _timestamp(),
        **stage_recorder.events(),
    }
    if (
        set(value) != _RECEIPT_FIELDS
        or (
            request_id is not None
            and _HEX_32.fullmatch(request_id) is None
        )
        or any(
            not isinstance(value.get(name), dict)
            or set(value[name]) != _STAGE_EVENT_FIELDS
            or value[name].get("event") != name
            for name in _RELAY_EVENTS
        )
    ):
        raise RelayError("INTERNAL_ERROR")
    return value


def _write_receipt(output: Path, receipt: dict[str, Any]) -> None:
    path = output / "relay-receipt.json"
    if path.exists() or path.is_symlink():
        _remove_published(path)
    _write_atomic(path, _canonical_bytes(receipt), "RECEIPT_PUBLISH_FAILED")


def run_relay(
    *,
    request_path: str,
    projection_path: str,
    output_dir: str,
    runtime_contract_path: str,
    requester: Requester | None = None,
) -> dict[str, Any]:
    started_at = _timestamp()
    stage_recorder = RelayStageRecorder()
    request_id: str | None = None
    output = Path(output_dir)
    try:
        if (output / "candidate.json").exists() or (
            output / "candidate.ready.json"
        ).exists():
            raise RelayError("CANDIDATE_ALREADY_EXISTS")
        contract = load_runtime_contract(runtime_contract_path)
        projection = _load_projection(Path(projection_path))
        request = _load_request(Path(request_path), projection)
        request_id = request["request_id"]
        body = _provider_body(request, projection, contract)
        dispatch = requester or _default_requester
        status_code, raw = dispatch(
            body,
            contract["relay_candidate_wait_timeout_seconds"],
            stage_recorder,
        )
        if not 200 <= status_code < 300:
            raise RelayError(
                "HTTP_REJECTED",
                proxy_request_count=1,
                http_status=status_code,
            )
        response_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            response = _strict_object(raw, "STRICT_JSON_REJECTED")
        except RelayError as exc:
            raise RelayError(
                exc.category,
                proxy_request_count=1,
                http_status=status_code,
                response_size=len(raw),
                response_sha256=response_sha256,
            ) from exc
        publish_candidate(
            response,
            request,
            projection,
            output_dir,
            stage_recorder,
        )
        receipt = _receipt(
            status="SUCCEEDED",
            started_at=started_at,
            request_id=request_id,
            stage_recorder=stage_recorder,
            error=None,
            response_size=len(raw),
            response_sha256=response_sha256,
        )
        _write_receipt(output, receipt)
        return receipt
    except RelayError as exc:
        receipt = _receipt(
            status="REJECTED",
            started_at=started_at,
            request_id=request_id,
            stage_recorder=stage_recorder,
            error=exc,
        )
        with suppress(RelayError):
            _write_receipt(output, receipt)
        return receipt
    except Exception:
        error = RelayError("INTERNAL_ERROR")
        receipt = _receipt(
            status="REJECTED",
            started_at=started_at,
            request_id=request_id,
            stage_recorder=stage_recorder,
            error=error,
        )
        with suppress(RelayError):
            _write_receipt(output, receipt)
        return receipt


def main() -> int:
    receipt = run_relay(
        request_path=str(REQUEST_PATH),
        projection_path=str(PROJECTION_PATH),
        output_dir=str(OUTPUT_DIR),
        runtime_contract_path=str(RUNTIME_CONTRACT_PATH),
    )
    return 0 if receipt["status"] == "SUCCEEDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
