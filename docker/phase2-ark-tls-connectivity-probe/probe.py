"""Secret-free and HTTP-free TLS connectivity probe for the Ark endpoint."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import ssl
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

TARGET_HOST = "ark.cn-beijing.volces.com"
TARGET_PORT = 443
CA_FILE = "/etc/ssl/certs/ca-certificates.crt"
OUTPUT_PATH = Path("/output/child-receipt.json")
READY_PATH = Path("/output/receipt.ready")
PROBE_ID_PATH = Path("/run/tickflow/probe-id")
PROBE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
VERIFY_MESSAGE_MAXIMUM = 200
RETRY_COUNT = 0
MAXIMUM_PROBE_ATTEMPTS = 1

EVENT_NAMES = (
    "probe_started",
    "dns_started",
    "dns_completed",
    "tcp_connect_started",
    "tcp_connect_completed",
    "tls_handshake_started",
    "tls_handshake_completed",
    "certificate_verified",
    "hostname_verified",
    "probe_closed",
    "cleanup_completed",
)
CHILD_SUCCESS_EVENTS = EVENT_NAMES[:-1]
ERROR_CATEGORIES = {
    "DNS_RESOLUTION_FAILED",
    "TCP_CONNECT_FAILED",
    "TCP_CONNECT_TIMEOUT",
    "TLS_HANDSHAKE_TIMEOUT",
    "TLS_CERT_VERIFY_FAILED",
    "TLS_HOSTNAME_VERIFY_FAILED",
    "TLS_PROTOCOL_FAILED",
    "TLS_CONNECTION_RESET",
    "TLS_EOF",
    "TLS_OTHER_SSL_ERROR",
    "PROCESS_ERROR",
}
_PROTOCOL_MARKERS = (
    "ALERT_PROTOCOL_VERSION",
    "NO_SHARED_CIPHER",
    "PROTOCOL_VERSION",
    "UNSUPPORTED_PROTOCOL",
    "WRONG_VERSION_NUMBER",
)
_SAFE_VERIFY_MESSAGE = re.compile(r"[^A-Za-z0-9 .,:;_()\[\]/=-]+")


class ProbePolicy(NamedTuple):
    tcp_connect_timeout_seconds: float = 10.0
    tls_handshake_timeout_seconds: float = 180.0
    tls_close_timeout_seconds: float = 5.0


PRODUCTION_POLICY = ProbePolicy()


class ErrorClassification(NamedTuple):
    category: str
    verify_code: int | None = None
    verify_message: str | None = None


class ReceiptPublicationError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class ProbeEventRecorder:
    def __init__(
        self,
        *,
        wall_clock: Callable[[], str],
        monotonic_ns: Callable[[], int],
    ) -> None:
        self._wall_clock = wall_clock
        self._monotonic_ns = monotonic_ns
        self._events = {
            name: {
                "occurred": False,
                "wall_time": None,
                "monotonic_ns": None,
                "category": "NOT_REACHED",
            }
            for name in EVENT_NAMES
        }

    def record(self, name: str, *, category: str = "PASSED") -> None:
        if name not in self._events or self._events[name]["occurred"]:
            raise ValueError("probe_event_invalid")
        self._events[name] = {
            "occurred": True,
            "wall_time": self._wall_clock(),
            "monotonic_ns": self._monotonic_ns(),
            "category": category,
        }

    def values(self) -> dict[str, dict[str, Any]]:
        return {name: dict(value) for name, value in self._events.items()}


def _wall_time() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sanitize_verify_message(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    sanitized = _SAFE_VERIFY_MESSAGE.sub("?", value).strip()
    return sanitized[:VERIFY_MESSAGE_MAXIMUM] or None


def classify_probe_error(error: BaseException, *, phase: str) -> ErrorClassification:
    if isinstance(error, socket.gaierror) or phase == "dns":
        return ErrorClassification("DNS_RESOLUTION_FAILED")
    if isinstance(error, (TimeoutError, socket.timeout)):
        return ErrorClassification(
            "TCP_CONNECT_TIMEOUT" if phase == "tcp" else "TLS_HANDSHAKE_TIMEOUT"
        )
    if phase == "tcp":
        return ErrorClassification("TCP_CONNECT_FAILED")
    if isinstance(error, ssl.SSLCertVerificationError):
        verify_code = getattr(error, "verify_code", None)
        if isinstance(verify_code, bool) or not isinstance(verify_code, int):
            verify_code = None
        verify_message = _sanitize_verify_message(
            getattr(error, "verify_message", None)
        )
        lowered = (verify_message or str(error)).lower()
        category = (
            "TLS_HOSTNAME_VERIFY_FAILED"
            if verify_code == 62 or "hostname mismatch" in lowered
            else "TLS_CERT_VERIFY_FAILED"
        )
        return ErrorClassification(category, verify_code, verify_message)
    if isinstance(error, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return ErrorClassification("TLS_CONNECTION_RESET")
    if isinstance(error, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return ErrorClassification("TLS_EOF")
    if isinstance(error, ssl.SSLError):
        description = f"{getattr(error, 'reason', '')} {error}".upper()
        if any(marker in description for marker in _PROTOCOL_MARKERS):
            return ErrorClassification("TLS_PROTOCOL_FAILED")
        return ErrorClassification("TLS_OTHER_SSL_ERROR")
    if isinstance(error, OSError):
        return ErrorClassification("TLS_CONNECTION_RESET")
    return ErrorClassification("PROCESS_ERROR")


def create_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=CA_FILE)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.MAXIMUM_SUPPORTED
    return context


def _base_receipt(probe_id: str, recorder: ProbeEventRecorder) -> dict[str, Any]:
    return {
        "probe_receipt_schema_version": 1,
        "probe_id": probe_id,
        "target": {
            "host": TARGET_HOST,
            "port": TARGET_PORT,
            "sni": TARGET_HOST,
            "hostname_verification_target": TARGET_HOST,
        },
        "probe_attempt_count": MAXIMUM_PROBE_ATTEMPTS,
        "retry_count": RETRY_COUNT,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "secret_content_read": False,
        "authorization_constructed": False,
        "http_request_sent": False,
        "business_body_sent": False,
        "terminal_status": "PROCESS_ERROR",
        "tls_error_category": "PROCESS_ERROR",
        "verify_code": None,
        "verify_message": None,
        "tls_version": None,
        "cipher_name": None,
        "certificate_not_before": None,
        "certificate_not_after": None,
        "san_contains_hostname": None,
        "events": recorder.values(),
    }


def _certificate_metadata(certificate: Mapping[str, Any]) -> dict[str, Any]:
    sans = certificate.get("subjectAltName", ())
    san_contains_hostname = any(
        isinstance(item, (tuple, list))
        and len(item) == 2
        and item[0] == "DNS"
        and item[1] == TARGET_HOST
        for item in sans
    )
    not_before = certificate.get("notBefore")
    not_after = certificate.get("notAfter")
    return {
        "certificate_not_before": not_before if isinstance(not_before, str) else None,
        "certificate_not_after": not_after if isinstance(not_after, str) else None,
        "san_contains_hostname": san_contains_hostname,
    }


def execute_probe(
    probe_id: str,
    *,
    policy: ProbePolicy = PRODUCTION_POLICY,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    socket_factory: Callable[..., Any] = socket.socket,
    tls_context_factory: Callable[[], Any] = create_tls_context,
    wall_clock: Callable[[], str] = _wall_time,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> dict[str, Any]:
    if PROBE_ID_PATTERN.fullmatch(probe_id) is None or policy != ProbePolicy(
        policy.tcp_connect_timeout_seconds,
        policy.tls_handshake_timeout_seconds,
        policy.tls_close_timeout_seconds,
    ):
        raise ValueError("probe_input_invalid")
    recorder = ProbeEventRecorder(
        wall_clock=wall_clock,
        monotonic_ns=monotonic_ns,
    )
    recorder.record("probe_started")
    receipt = _base_receipt(probe_id, recorder)
    raw_socket: Any = None
    tls_socket: Any = None
    phase = "dns"
    try:
        recorder.record("dns_started")
        addresses = resolver(
            TARGET_HOST,
            TARGET_PORT,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
        if not addresses:
            raise socket.gaierror("no stream address")
        family, socktype, protocol, _canonical_name, address = addresses[0]
        recorder.record("dns_completed")

        phase = "tcp"
        recorder.record("tcp_connect_started")
        raw_socket = socket_factory(family, socktype, protocol)
        raw_socket.settimeout(policy.tcp_connect_timeout_seconds)
        raw_socket.connect(address)
        recorder.record("tcp_connect_completed")

        phase = "tls"
        recorder.record("tls_handshake_started")
        context = tls_context_factory()
        tls_socket = context.wrap_socket(
            raw_socket,
            server_hostname=TARGET_HOST,
            do_handshake_on_connect=False,
        )
        tls_socket.settimeout(policy.tls_handshake_timeout_seconds)
        tls_socket.do_handshake()
        recorder.record("tls_handshake_completed")
        certificate = tls_socket.getpeercert()
        recorder.record("certificate_verified")
        recorder.record("hostname_verified")

        tls_version = tls_socket.version()
        cipher = tls_socket.cipher()
        tls_socket.settimeout(policy.tls_close_timeout_seconds)
        raw_socket = tls_socket.unwrap()
        tls_socket = None
        raw_socket.close()
        raw_socket = None
        recorder.record("probe_closed")

        receipt.update(
            {
                "terminal_status": "PROBE_PASSED",
                "tls_error_category": "NONE",
                "tls_version": tls_version if isinstance(tls_version, str) else None,
                "cipher_name": (
                    cipher[0]
                    if isinstance(cipher, (tuple, list))
                    and cipher
                    and isinstance(cipher[0], str)
                    else None
                ),
                **_certificate_metadata(certificate),
            }
        )
    except BaseException as error:
        classification = classify_probe_error(error, phase=phase)
        receipt.update(
            {
                "terminal_status": classification.category,
                "tls_error_category": classification.category,
                "verify_code": classification.verify_code,
                "verify_message": classification.verify_message,
            }
        )
    finally:
        if tls_socket is not None:
            try:
                tls_socket.close()
            except OSError:
                pass
        elif raw_socket is not None:
            try:
                raw_socket.close()
            except OSError:
                pass
    receipt["events"] = recorder.values()
    validate_probe_receipt(receipt, require_cleanup=False)
    return receipt


_RECEIPT_FIELDS = {
    "ai_call_count",
    "authorization_constructed",
    "business_body_sent",
    "certificate_not_after",
    "certificate_not_before",
    "cipher_name",
    "events",
    "http_request_sent",
    "probe_attempt_count",
    "probe_id",
    "probe_receipt_schema_version",
    "provider_attempt_count",
    "retry_count",
    "san_contains_hostname",
    "secret_content_read",
    "target",
    "terminal_status",
    "tls_error_category",
    "tls_version",
    "verify_code",
    "verify_message",
}
_EVENT_FIELDS = {"occurred", "wall_time", "monotonic_ns", "category"}


def validate_probe_receipt(
    value: Mapping[str, Any],
    *,
    require_cleanup: bool,
) -> dict[str, Any]:
    events = value.get("events") if isinstance(value, Mapping) else None
    target = value.get("target") if isinstance(value, Mapping) else None
    terminal = value.get("terminal_status") if isinstance(value, Mapping) else None
    try:
        occurred_events = [
            name for name in EVENT_NAMES if events[name].get("occurred") is True
        ]
        child_occurrence = [events[name].get("occurred") for name in CHILD_SUCCESS_EVENTS]
        child_prefix_valid = child_occurrence == (
            [True] * sum(child_occurrence) + [False] * (len(child_occurrence) - sum(child_occurrence))
        )
        monotonic_values = [events[name]["monotonic_ns"] for name in occurred_events]
        valid = (
            set(value) == _RECEIPT_FIELDS
            and value.get("probe_receipt_schema_version") == 1
            and isinstance(value.get("probe_id"), str)
            and PROBE_ID_PATTERN.fullmatch(value["probe_id"]) is not None
            and target
            == {
                "host": TARGET_HOST,
                "port": TARGET_PORT,
                "sni": TARGET_HOST,
                "hostname_verification_target": TARGET_HOST,
            }
            and value.get("probe_attempt_count") == 1
            and value.get("retry_count") == 0
            and value.get("provider_attempt_count") == 0
            and value.get("ai_call_count") == 0
            and value.get("secret_content_read") is False
            and value.get("authorization_constructed") is False
            and value.get("http_request_sent") is False
            and value.get("business_body_sent") is False
            and terminal in ({"PROBE_PASSED"} | ERROR_CATEGORIES)
            and value.get("tls_error_category")
            == ("NONE" if terminal == "PROBE_PASSED" else terminal)
            and isinstance(events, Mapping)
            and set(events) == set(EVENT_NAMES)
            and all(
                isinstance(events[name], Mapping)
                and set(events[name]) == _EVENT_FIELDS
                and isinstance(events[name].get("occurred"), bool)
                and isinstance(events[name].get("category"), str)
                and bool(events[name]["category"])
                and (
                    (
                        isinstance(events[name].get("wall_time"), str)
                        and isinstance(events[name].get("monotonic_ns"), int)
                        and not isinstance(events[name].get("monotonic_ns"), bool)
                    )
                    if events[name]["occurred"]
                    else (
                        events[name].get("wall_time") is None
                        and events[name].get("monotonic_ns") is None
                    )
                )
                for name in EVENT_NAMES
            )
            and (
                not require_cleanup
                or events["cleanup_completed"]["occurred"] is True
            )
            and (
                terminal != "PROBE_PASSED"
                or all(events[name]["occurred"] is True for name in CHILD_SUCCESS_EVENTS)
            )
            and child_prefix_valid
            and all(
                left < right
                for left, right in zip(monotonic_values, monotonic_values[1:])
            )
            and (
                value.get("tls_version") is None
                or (
                    isinstance(value.get("tls_version"), str)
                    and bool(value["tls_version"])
                )
            )
            and (
                value.get("cipher_name") is None
                or (
                    isinstance(value.get("cipher_name"), str)
                    and bool(value["cipher_name"])
                )
            )
            and (
                value.get("certificate_not_before") is None
                or (
                    isinstance(value.get("certificate_not_before"), str)
                    and bool(value["certificate_not_before"])
                )
            )
            and (
                value.get("certificate_not_after") is None
                or (
                    isinstance(value.get("certificate_not_after"), str)
                    and bool(value["certificate_not_after"])
                )
            )
            and (
                value.get("san_contains_hostname") is None
                or isinstance(value.get("san_contains_hostname"), bool)
            )
            and (
                value.get("verify_code") is None
                or (
                    isinstance(value.get("verify_code"), int)
                    and not isinstance(value.get("verify_code"), bool)
                )
            )
            and (
                value.get("verify_message") is None
                or (
                    isinstance(value.get("verify_message"), str)
                    and 0 < len(value["verify_message"]) <= VERIFY_MESSAGE_MAXIMUM
                )
            )
        )
    except (AttributeError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError("probe_receipt_invalid")
    return dict(value)


def _durable_publish_json(path: Path, value: Mapping[str, Any]) -> str:
    parent = path.parent
    try:
        metadata = os.lstat(parent)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or os.path.lexists(path)
        ):
            raise OSError("publication target invalid")
    except OSError as error:
        raise ReceiptPublicationError("RECEIPT_OPEN_FAILED") from error
    raw = (
        json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    except OSError as error:
        raise ReceiptPublicationError("RECEIPT_OPEN_FAILED") from error
    temporary = Path(name)
    try:
        try:
            os.fchmod(descriptor, 0o600)
        except OSError as error:
            raise ReceiptPublicationError("RECEIPT_OPEN_FAILED") from error
        offset = 0
        try:
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise OSError("short receipt write")
                offset += written
        except OSError as error:
            raise ReceiptPublicationError("RECEIPT_WRITE_FAILED") from error
        try:
            os.fsync(descriptor)
        except OSError as error:
            raise ReceiptPublicationError("RECEIPT_FSYNC_FAILED") from error
        os.close(descriptor)
        descriptor = -1
        try:
            os.replace(temporary, path)
        except OSError as error:
            raise ReceiptPublicationError("RECEIPT_RENAME_FAILED") from error
        try:
            directory = os.open(
                parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            raise ReceiptPublicationError("RECEIPT_PARENT_FSYNC_FAILED") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return hashlib.sha256(raw).hexdigest()


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    _durable_publish_json(path, value)


def publish_receipt_bundle(
    receipt: Mapping[str, Any],
    *,
    output_dir: Path = OUTPUT_PATH.parent,
) -> dict[str, Any]:
    try:
        validated = validate_probe_receipt(receipt, require_cleanup=False)
    except ValueError as error:
        raise ReceiptPublicationError("RECEIPT_VALIDATION_FAILED") from error
    receipt_path = output_dir / OUTPUT_PATH.name
    ready_path = output_dir / READY_PATH.name
    receipt_sha256 = _durable_publish_json(receipt_path, validated)
    try:
        metadata = os.lstat(output_dir)
    except OSError as error:
        raise ReceiptPublicationError("RECEIPT_OPEN_FAILED") from error
    marker = {
        "publication_schema_version": 1,
        "publication_complete": True,
        "probe_id": validated["probe_id"],
        "receipt_sha256": receipt_sha256,
        "publisher_uid": os.getuid(),
        "publisher_gid": os.getgid(),
        "directory_uid": metadata.st_uid,
        "directory_gid": metadata.st_gid,
        "directory_mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }
    _durable_publish_json(ready_path, marker)
    return marker


def _read_probe_id(path: Path = PROBE_ID_PATH) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != 33:
            raise ValueError("probe_id_file_invalid")
        raw = os.read(descriptor, 34)
    finally:
        os.close(descriptor)
    try:
        probe_id = raw.decode("ascii").removesuffix("\n")
    except UnicodeDecodeError as error:
        raise ValueError("probe_id_file_invalid") from error
    if PROBE_ID_PATTERN.fullmatch(probe_id) is None:
        raise ValueError("probe_id_file_invalid")
    return probe_id


def main() -> int:
    probe_id = _read_probe_id()
    receipt = execute_probe(probe_id)
    try:
        publish_receipt_bundle(receipt)
    except ReceiptPublicationError as error:
        sys.stderr.write(
            json.dumps(
                {
                    "receipt_transport_schema_version": 1,
                    "category": error.category,
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 70
    return 0 if receipt["terminal_status"] == "PROBE_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
