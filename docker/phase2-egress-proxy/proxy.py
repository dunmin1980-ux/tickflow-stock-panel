"""Fixed reverse proxy for the local-only Phase 2B Mock Provider."""

from __future__ import annotations

import http.client
import json
import os
import stat
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

ALLOWED_METHOD = "POST"
ALLOWED_PATH = "/v1/typed-claims"
UPSTREAM_HOST = "phase2-mock-provider"
UPSTREAM_PORT = 8081
UPSTREAM_PATH = "/v1/typed-claims"
RETRY_COUNT = 0
FOLLOW_REDIRECTS = False
MAXIMUM_BYTES = 1_048_576
TIMEOUT_SECONDS = 2
AUTH_PATH = Path("/run/phase2/provider-auth")
RECEIPT_PATH = Path("/output/receipt.json")
_NOFOLLOW = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0


class ProxyError(Exception):
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


def _read_auth() -> str:
    try:
        descriptor = os.open(AUTH_PATH, os.O_RDONLY | _NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= 4096:
                raise ProxyError("auth_file_invalid")
            raw = os.read(descriptor, 4097)
            if len(raw) != metadata.st_size:
                raise ProxyError("auth_file_invalid")
        finally:
            os.close(descriptor)
        value = raw.decode("utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ProxyError("auth_file_invalid") from exc
    if not value or "\n" in value or "\r" in value:
        raise ProxyError("auth_file_invalid")
    return value


def _write_receipt_value(value: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_TRUNC | _NOFOLLOW
    try:
        descriptor = os.open(RECEIPT_PATH, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ProxyError("receipt_file_invalid")
            raw = _canonical_bytes(value)
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise ProxyError("receipt_write_failed")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ProxyError("receipt_write_failed") from exc


def _write_receipt(
    *,
    method_allowed: bool,
    path_allowed: bool,
    upstream_attempt_count: int,
    response_category: str,
    response_size: int | None,
    auth_present: bool,
) -> None:
    _write_receipt_value(
        {
            "receipt_schema_version": 1,
            "method_allowed": method_allowed,
            "path_allowed": path_allowed,
            "upstream_attempt_count": upstream_attempt_count,
            "response_category": response_category,
            "response_size": response_size,
            "auth_present": auth_present,
        }
    )


def _write_ready() -> None:
    _write_receipt_value(
        {
            "receipt_schema_version": 1,
            "status": "READY",
            "auth_present": True,
        }
    )


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "Phase2Proxy/1"
    sys_version = ""

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _send(self, status_code: int, body: bytes = b"") -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _reject_method(self) -> None:
        _write_receipt(
            method_allowed=False,
            path_allowed=self.path == ALLOWED_PATH,
            upstream_attempt_count=0,
            response_category="METHOD_BLOCKED",
            response_size=0,
            auth_present=False,
        )
        self._send(405)

    do_GET = _reject_method  # noqa: N815
    do_PUT = _reject_method  # noqa: N815
    do_DELETE = _reject_method  # noqa: N815
    do_PATCH = _reject_method  # noqa: N815
    do_HEAD = _reject_method  # noqa: N815
    do_OPTIONS = _reject_method  # noqa: N815

    def do_POST(self) -> None:
        if self.path != ALLOWED_PATH:
            _write_receipt(
                method_allowed=True,
                path_allowed=False,
                upstream_attempt_count=0,
                response_category="PATH_BLOCKED",
                response_size=0,
                auth_present=False,
            )
            self._send(404)
            return
        try:
            declared_size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            declared_size = -1
        if not 0 < declared_size <= MAXIMUM_BYTES:
            _write_receipt(
                method_allowed=True,
                path_allowed=True,
                upstream_attempt_count=0,
                response_category="REQUEST_SIZE_BLOCKED",
                response_size=0,
                auth_present=False,
            )
            self._send(413)
            return
        body = self.rfile.read(declared_size)
        if len(body) != declared_size:
            _write_receipt(
                method_allowed=True,
                path_allowed=True,
                upstream_attempt_count=0,
                response_category="REQUEST_SIZE_BLOCKED",
                response_size=0,
                auth_present=False,
            )
            self._send(400)
            return
        try:
            auth_value = _read_auth()
        except ProxyError:
            _write_receipt(
                method_allowed=True,
                path_allowed=True,
                upstream_attempt_count=0,
                response_category="AUTH_BLOCKED",
                response_size=0,
                auth_present=False,
            )
            self._send(503)
            return
        connection = http.client.HTTPConnection(
            UPSTREAM_HOST,
            UPSTREAM_PORT,
            timeout=TIMEOUT_SECONDS,
        )
        try:
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
                _write_receipt(
                    method_allowed=True,
                    path_allowed=True,
                    upstream_attempt_count=1,
                    response_category="REDIRECT_REJECTED",
                    response_size=0,
                    auth_present=True,
                )
                self._send(502)
                return
            if not 200 <= status_code < 300:
                _write_receipt(
                    method_allowed=True,
                    path_allowed=True,
                    upstream_attempt_count=1,
                    response_category="UPSTREAM_REJECTED",
                    response_size=0,
                    auth_present=True,
                )
                self._send(status_code)
                return
            declared_response_size = response.getheader("Content-Length")
            if declared_response_size is not None:
                try:
                    if int(declared_response_size) > MAXIMUM_BYTES:
                        raise ProxyError("upstream_response_too_large")
                except ValueError as exc:
                    raise ProxyError("upstream_response_size_invalid") from exc
            response_body = response.read(MAXIMUM_BYTES + 1)
            if len(response_body) > MAXIMUM_BYTES:
                raise ProxyError("upstream_response_too_large")
            _write_receipt(
                method_allowed=True,
                path_allowed=True,
                upstream_attempt_count=1,
                response_category="FORWARDED",
                response_size=len(response_body),
                auth_present=True,
            )
            self._send(200, response_body)
        except TimeoutError:
            _write_receipt(
                method_allowed=True,
                path_allowed=True,
                upstream_attempt_count=1,
                response_category="TIMEOUT",
                response_size=None,
                auth_present=True,
            )
            self._send(504)
        except (OSError, http.client.HTTPException, ProxyError):
            _write_receipt(
                method_allowed=True,
                path_allowed=True,
                upstream_attempt_count=1,
                response_category="UPSTREAM_BLOCKED",
                response_size=None,
                auth_present=True,
            )
            self._send(502)
        finally:
            connection.close()


def create_server(address: tuple[str, int] = ("0.0.0.0", 8080)) -> HTTPServer:
    return HTTPServer(address, ProxyHandler)


def main() -> int:
    try:
        _read_auth()
    except ProxyError:
        return 2
    server = create_server()
    try:
        _write_ready()
        server.serve_forever()
    except ProxyError:
        return 2
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
