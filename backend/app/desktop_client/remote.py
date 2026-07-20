"""Constrained HTTP client for a private remote TickFlow instance."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

import httpx

from app.desktop_client.config import validate_remote_url
from app.desktop_client.keychain import KeychainSessionStore

logger = logging.getLogger(__name__)


class SessionStore(Protocol):
    def save(self, remote_base_url: str, token: str) -> None: ...

    def load(self, remote_base_url: str) -> str | None: ...

    def delete(self, remote_base_url: str) -> None: ...


class RemoteClientError(RuntimeError):
    """Base error for sanitized desktop-to-cloud failures."""


class RemoteAuthError(RemoteClientError):
    pass


class RemoteAuthRequiredError(RemoteClientError):
    pass


class RemotePathError(RemoteClientError):
    pass


def _validated_api_path(value: str) -> str:
    if not isinstance(value, str) or not value or any(char in value for char in "\r\n\0"):
        raise RemotePathError("remote path is invalid")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise RemotePathError("remote path must be same-origin")
    if not (parsed.path == "/api" or parsed.path.startswith("/api/")):
        raise RemotePathError("remote path must be under /api")

    decoded = parsed.path
    for _ in range(4):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if "\\" in decoded or any(segment in {".", ".."} for segment in decoded.split("/")):
        raise RemotePathError("remote path traversal is not allowed")
    if any(char in decoded for char in "\r\n\0"):
        raise RemotePathError("remote path is invalid")
    return value


def _validated_headers(headers: dict[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("remote headers must be strings")
        if any(char in name or char in value for char in "\r\n\0"):
            raise ValueError("remote header injection is not allowed")
        if name.lower() in {"authorization", "cookie", "host"}:
            raise ValueError("caller-provided credential headers are not allowed")
        clean[name] = value
    return clean


class RemoteClient:
    def __init__(
        self,
        base_url: str,
        *,
        sessions: SessionStore | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_remote_url(base_url)
        self.sessions = sessions or KeychainSessionStore()
        self._transport = transport

    def _send(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        normalized_path = _validated_api_path(path)
        method = method.upper()
        if not method.isalpha():
            raise ValueError("remote HTTP method is invalid")
        started = time.monotonic()
        with httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            response = client.request(method, normalized_path, headers=headers, **kwargs)
        logger.info(
            "desktop cloud request method=%s path=%s status=%d elapsed_ms=%d",
            method,
            urlsplit(normalized_path).path,
            response.status_code,
            int((time.monotonic() - started) * 1000),
        )
        return response

    def login(self, password: str) -> bool:
        response = self._send(
            "POST",
            "/api/auth/login",
            timeout=15,
            json={"password": password},
        )
        response.raise_for_status()
        token = response.cookies.get("tf_session")
        if not token:
            raise RemoteAuthError("cloud did not return a session cookie")
        self.sessions.save(self.base_url, token)
        return True

    def auth_status(self) -> dict[str, Any]:
        headers: dict[str, str] = {}
        token = self.sessions.load(self.base_url)
        if token:
            headers["Cookie"] = f"tf_session={token}"
        response = self._send(
            "GET",
            "/api/auth/status",
            timeout=15,
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = self.sessions.load(self.base_url)
        if not token:
            raise RemoteAuthRequiredError("cloud login required")
        if any(key in kwargs for key in ("auth", "cookies", "follow_redirects")):
            raise ValueError("caller-provided remote credentials or redirects are not allowed")
        headers = _validated_headers(dict(kwargs.pop("headers", {})))
        headers["Cookie"] = f"tf_session={token}"
        response = self._send(
            method,
            path,
            timeout=30,
            headers=headers,
            **kwargs,
        )
        return response

    def logout(self) -> None:
        token = self.sessions.load(self.base_url)
        try:
            if token:
                self._send(
                    "POST",
                    "/api/auth/logout",
                    timeout=15,
                    headers={"Cookie": f"tf_session={token}"},
                )
        except httpx.HTTPError:
            # Local revocation is authoritative for the desktop client.
            pass
        finally:
            self.sessions.delete(self.base_url)
