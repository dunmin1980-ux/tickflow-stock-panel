"""Fail-closed desktop proxy for cloud-owned API routes."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Literal, Protocol
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.routing import compile_path

logger = logging.getLogger(__name__)

RouteTarget = Literal["local", "cloud", "deny"]

_LOCAL_ROUTE_TEMPLATES = frozenset(
    {
        ("POST", "/api/backtest/strategy/run"),
        ("GET", "/api/backtest/strategy/stream"),
        ("POST", "/api/screener/run"),
        ("GET", "/api/client/config"),
        ("PUT", "/api/client/config"),
        ("GET", "/api/client/status"),
        ("POST", "/api/client/auth/login"),
        ("POST", "/api/client/auth/logout"),
        ("GET", "/api/workspace/bootstrap"),
        ("GET", "/api/workspace/resources/{name}"),
        ("GET", "/api/workspace/revisions"),
        ("GET", "/api/workspace/events"),
        ("POST", "/api/workspace/resources/{name}/commands"),
        ("POST", "/api/workspace/compute-inputs/build"),
    }
)
_LOCAL_MATCHERS = tuple(
    (method, compile_path(template)[0])
    for method, template in sorted(_LOCAL_ROUTE_TEMPLATES)
)
_SENSITIVE_DESKTOP_DENY = (
    "/api/settings/tickflow-key",
    "/api/settings/tushare-token",
    "/api/settings/ai-key",
    "/api/settings/ai",
    "/api/settings/preferences/feishu-webhook",
    "/api/settings/preferences/wecom-webhook",
    "/api/settings/preferences/wecom-bot",
)
_REQUEST_HEADER_ALLOWLIST = {
    "accept",
    "content-type",
    "if-match",
    "if-none-match",
    "last-event-id",
    "range",
}
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_STREAMING_CONTENT_TYPES = ("text/event-stream", "application/x-ndjson")


class RemoteRequester(Protocol):
    def request(self, method: str, path: str, **kwargs) -> httpx.Response: ...


RemoteFactory = Callable[[], RemoteRequester]


def _normalized_path(value: str) -> str:
    if not isinstance(value, str) or not value or any(char in value for char in "\r\n\0"):
        raise ValueError("desktop proxy path is invalid")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("desktop proxy path must be same-origin")
    if not parsed.path.startswith("/"):
        raise ValueError("desktop proxy path must be absolute")

    decoded = parsed.path
    for _ in range(4):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if "\\" in decoded or "//" in decoded:
        raise ValueError("desktop proxy path is not canonical")
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        raise ValueError("desktop proxy traversal is not allowed")
    if any(char in decoded for char in "\r\n\0"):
        raise ValueError("desktop proxy path is invalid")
    if decoded != "/":
        decoded = decoded.rstrip("/")
    return decoded


def _route_target(method: str, path: str) -> RouteTarget:
    normalized_method = method.upper()
    if not normalized_method.isalpha():
        return "deny"
    if any(
        path == sensitive or path.startswith(f"{sensitive}/")
        for sensitive in _SENSITIVE_DESKTOP_DENY
    ):
        return "deny"
    if any(
        normalized_method == local_method and matcher.fullmatch(path)
        for local_method, matcher in _LOCAL_MATCHERS
    ):
        return "local"
    if path == "/api" or path.startswith("/api/"):
        return "cloud"
    return "deny"


def route_target(method: str, path: str) -> RouteTarget:
    try:
        normalized = _normalized_path(path)
    except ValueError:
        return "deny"
    return _route_target(method, normalized)


def _request_headers(request: Request) -> dict[str, str]:
    return {
        name.lower(): value
        for name, value in request.headers.items()
        if name.lower() in _REQUEST_HEADER_ALLOWLIST
    }


def _response_headers(response: httpx.Response) -> dict[str, str]:
    connection_tokens = {
        token.strip().lower()
        for token in response.headers.get("connection", "").split(",")
        if token.strip()
    }
    forbidden = _HOP_BY_HOP_HEADERS | connection_tokens | {
        "set-cookie",
        "content-length",
        "content-encoding",
    }
    return {
        name: value
        for name, value in response.headers.multi_items()
        if name.lower() not in forbidden
    }


def _stream(
    response: httpx.Response,
    stream_context: AbstractContextManager[httpx.Response] | None,
) -> Iterator[bytes]:
    try:
        yield from response.iter_bytes()
    finally:
        if stream_context is None:
            response.close()
        else:
            stream_context.__exit__(None, None, None)


def _denied_response() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "detail": "Desktop access to this route is denied",
            "code": "DESKTOP_ROUTE_DENIED",
        },
    )


class DesktopCloudProxyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, remote_factory: RemoteFactory) -> None:
        super().__init__(app)
        self.remote_factory = remote_factory

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        raw_path = request.scope.get("raw_path", request.url.path.encode("utf-8"))
        try:
            path = _normalized_path(raw_path.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return _denied_response()
        target = _route_target(request.method, path)
        if target == "local":
            return await call_next(request)
        if target == "deny":
            return _denied_response()

        body = await request.body()
        kwargs = {
            "params": list(request.query_params.multi_items()),
            "content": body,
            "headers": _request_headers(request),
        }
        stream_context: AbstractContextManager[httpx.Response] | None = None
        try:
            remote = self.remote_factory()
            stream_method = getattr(remote, "stream", None)
            if callable(stream_method):
                stream_context = stream_method(request.method, path, **kwargs)
                response = await run_in_threadpool(stream_context.__enter__)
            else:
                response = await run_in_threadpool(
                    remote.request,
                    request.method,
                    path,
                    **kwargs,
                )
        except (httpx.HTTPError, OSError, RuntimeError, ValueError):
            logger.warning("desktop cloud proxy unavailable method=%s path=%s", request.method, path)
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Cloud service is unavailable",
                    "code": "DESKTOP_CLOUD_UNAVAILABLE",
                },
            )

        logger.info(
            "desktop cloud proxy method=%s path=%s status=%d",
            request.method,
            path,
            response.status_code,
        )
        headers = _response_headers(response)
        content_type = response.headers.get("content-type", "").lower()
        if content_type.startswith(_STREAMING_CONTENT_TYPES):
            return StreamingResponse(
                _stream(response, stream_context),
                status_code=response.status_code,
                headers=headers,
            )
        try:
            content = await run_in_threadpool(response.read)
        finally:
            if stream_context is None:
                response.close()
            else:
                await run_in_threadpool(stream_context.__exit__, None, None, None)
        return Response(content=content, status_code=response.status_code, headers=headers)


def install_desktop_proxy(app: FastAPI, *, remote_factory: RemoteFactory) -> bool:
    if os.environ.get("TICKFLOW_DESKTOP_CLIENT") != "1":
        return False
    app.add_middleware(DesktopCloudProxyMiddleware, remote_factory=remote_factory)
    return True
