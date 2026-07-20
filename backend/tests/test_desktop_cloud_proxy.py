from __future__ import annotations

import gzip
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.desktop_client import api as client_api
from app.desktop_client.proxy import (
    DesktopCloudProxyMiddleware,
    install_desktop_proxy,
    route_target,
)
from app.main import app as main_app


def _concrete_path(path: str) -> str:
    replacements = {
        "name": "watchlist",
        "symbol": "000403.SZ",
        "report_id": "report-1",
        "strategy_id": "strategy-1",
        "table": "income",
        "config_id": "config-1",
        "rule_id": "rule-1",
        "job_id": "job-1",
        "full_path": "index.html",
        "ts": "1",
        "run_id": "run-1",
    }
    return re.sub(
        r"\{([^}:]+)(?::[^}]+)?\}",
        lambda match: replacements.get(match.group(1), "value"),
        path,
    )


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/backtest/strategy/run", "local"),
        ("GET", "/api/backtest/strategy/stream", "local"),
        ("POST", "/api/backtest/strategy/stream", "cloud"),
        ("POST", "/api/screener/run", "local"),
        ("GET", "/api/client/status", "local"),
        ("POST", "/api/client/auth/login", "local"),
        ("GET", "/api/workspace/bootstrap/", "local"),
        ("GET", "/api/workspace/resources/watchlist", "local"),
        ("POST", "/api/workspace/resources/watchlist/commands", "local"),
        ("GET", "/api/kline/daily", "cloud"),
        ("POST", "/api/stock-analysis/analyze", "cloud"),
        ("GET", "/api/settings/preferences", "cloud"),
        ("POST", "/api/strategy", "cloud"),
        ("GET", "/api/signals", "cloud"),
        ("GET", "/health", "deny"),
    ],
)
def test_route_target_uses_normalized_method_and_exact_registered_path(
    method: str,
    path: str,
    expected: str,
) -> None:
    assert route_target(method, path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/api/kline/daily",
        "//evil.example/api/kline/daily",
        "/api/%2e%2e/settings/preferences",
        "/api/%252e%252e/settings/preferences",
        "/api/workspace/../settings/preferences",
        "/api/workspace\\..\\settings/preferences",
        "/api/kline/daily?token=secret",
        "/api/kline/daily\r\nX-Evil: yes",
    ],
)
def test_route_target_rejects_absolute_injected_and_traversal_paths(path: str) -> None:
    assert route_target("GET", path) == "deny"


@pytest.mark.parametrize(
    "path",
    [
        "/api/settings/tickflow-key",
        "/api/settings/tushare-token",
        "/api/settings/ai-key",
        "/api/settings/ai",
        "/api/settings/preferences/feishu-webhook",
        "/api/settings/preferences/wecom-webhook",
        "/api/settings/preferences/wecom-bot",
    ],
)
def test_sensitive_desktop_settings_routes_are_denied(path: str) -> None:
    assert route_target("GET", path) == "deny"
    assert route_target("POST", path) == "deny"
    assert route_target("DELETE", path) == "deny"


def test_every_registered_api_route_has_a_fail_closed_desktop_contract() -> None:
    routes = list(main_app.routes) + list(client_api.router.routes)
    seen: set[tuple[str, str]] = set()

    for route in routes:
        template = getattr(route, "path", "")
        if not template.startswith("/api/"):
            continue
        path = _concrete_path(template)
        for method in getattr(route, "methods", set()) or set():
            contract = (method, template)
            if contract in seen:
                continue
            seen.add(contract)
            target = route_target(method, path)
            expected_local = (
                template.startswith("/api/client/")
                or template.startswith("/api/workspace/")
                or (method, template)
                in {
                    ("POST", "/api/backtest/strategy/run"),
                    ("GET", "/api/backtest/strategy/stream"),
                    ("POST", "/api/screener/run"),
                }
            )
            if expected_local:
                assert target == "local", contract
            else:
                assert target in {"cloud", "deny"}, contract

    assert ("GET", "/api/workspace/bootstrap") in seen
    assert ("POST", "/api/client/auth/login") in seen
    assert ("GET", "/api/backtest/strategy/stream") in seen


class RemoteStub:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.calls.append((method, path, kwargs))
        return self.response


def _proxy_app(remote: RemoteStub) -> FastAPI:
    app = FastAPI()

    @app.post("/api/backtest/strategy/run")
    async def local_compute(request: Request) -> JSONResponse:
        return JSONResponse({"target": "local", "body": await request.json()})

    @app.get("/api/workspace/bootstrap")
    def local_workspace() -> dict[str, str]:
        return {"target": "local-workspace"}

    app.add_middleware(
        DesktopCloudProxyMiddleware,
        remote_factory=lambda: remote,
    )
    return app


def _remote_response(
    *,
    status_code: int = 200,
    content: bytes = b'{"target":"cloud"}',
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        headers=headers,
        request=httpx.Request("GET", "https://cloud.example/api/kline/daily"),
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/kline/daily"),
        ("POST", "/api/stock-analysis/analyze"),
        ("GET", "/api/settings/preferences"),
    ],
)
def test_cloud_routes_forward_method_path_query_body_and_safe_headers(
    method: str,
    path: str,
) -> None:
    remote = RemoteStub(
        _remote_response(
            status_code=207,
            headers={
                "content-type": "application/json",
                "x-cloud": "yes",
                "set-cookie": "tf_session=must-not-reach-browser",
                "connection": "x-private-hop",
                "x-private-hop": "remove-me",
                "keep-alive": "timeout=5",
            },
        )
    )

    with TestClient(_proxy_app(remote)) as client:
        response = client.request(
            method,
            path,
            params=[("symbol", "000403.SZ"), ("symbol", "600000.SH")],
            content=b'{"focus":"volume"}',
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "If-Match": '"' + "a" * 64 + '"',
                "Cookie": "browser-secret-cookie",
                "Authorization": "Bearer browser-secret-token",
                "X-Not-Forwarded": "private",
            },
        )

    assert response.status_code == 207
    assert response.json() == {"target": "cloud"}
    assert response.headers["x-cloud"] == "yes"
    assert "set-cookie" not in response.headers
    assert "connection" not in response.headers
    assert "x-private-hop" not in response.headers
    assert "keep-alive" not in response.headers
    assert len(remote.calls) == 1
    forwarded_method, forwarded_path, kwargs = remote.calls[0]
    assert (forwarded_method, forwarded_path) == (method, path)
    assert kwargs["params"] == [("symbol", "000403.SZ"), ("symbol", "600000.SH")]
    assert kwargs["content"] == b'{"focus":"volume"}'
    assert kwargs["headers"] == {
        "accept": "application/json",
        "content-type": "application/json",
        "if-match": '"' + "a" * 64 + '"',
    }


def test_only_explicit_local_routes_reach_loopback_handlers() -> None:
    remote = RemoteStub(_remote_response())

    with TestClient(_proxy_app(remote)) as client:
        compute = client.post("/api/backtest/strategy/run", json={"strategy": "momentum"})
        workspace = client.get("/api/workspace/bootstrap")

    assert compute.json() == {"target": "local", "body": {"strategy": "momentum"}}
    assert workspace.json() == {"target": "local-workspace"}
    assert remote.calls == []


def test_sensitive_route_is_denied_without_local_or_remote_access() -> None:
    remote = RemoteStub(_remote_response())

    with TestClient(_proxy_app(remote)) as client:
        response = client.put(
            "/api/settings/preferences/feishu-webhook",
            json={"webhook_url": "SENSITIVE_WEBHOOK_CANARY"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Desktop access to this route is denied",
        "code": "DESKTOP_ROUTE_DENIED",
    }
    assert remote.calls == []


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.iterated = 0

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.iterated += 1
            yield chunk


@pytest.mark.parametrize("content_type", ["text/event-stream", "application/x-ndjson"])
def test_streaming_cloud_responses_are_forwarded_chunk_by_chunk(content_type: str) -> None:
    stream = ChunkStream([b'{"one":1}\n', b'{"two":2}\n'])
    response = httpx.Response(
        200,
        stream=stream,
        headers={"content-type": content_type, "set-cookie": "must-not-forward=1"},
        request=httpx.Request("GET", "https://cloud.example/api/stock-analysis/analyze"),
    )
    remote = RemoteStub(response)

    with TestClient(_proxy_app(remote)) as client:
        proxied = client.post("/api/stock-analysis/analyze", json={"symbol": "000403.SZ"})

    assert proxied.content == b'{"one":1}\n{"two":2}\n'
    assert proxied.headers["content-type"].startswith(content_type)
    assert "set-cookie" not in proxied.headers
    assert stream.iterated == 2


def test_proxy_removes_gzip_encoding_after_buffered_httpx_decode() -> None:
    decoded = b'{"decoded":true}'
    remote = RemoteStub(
        httpx.Response(
            200,
            content=gzip.compress(decoded),
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
                "content-length": "999",
            },
            request=httpx.Request("GET", "https://cloud.example/api/kline/daily"),
        )
    )

    with TestClient(_proxy_app(remote)) as client:
        response = client.get("/api/kline/daily")

    assert response.content == decoded
    assert "content-encoding" not in response.headers


def test_proxy_removes_gzip_encoding_after_streaming_httpx_decode() -> None:
    decoded = b"data: one\n\ndata: two\n\n"
    stream = ChunkStream([gzip.compress(decoded)])
    remote = RemoteStub(
        httpx.Response(
            200,
            stream=stream,
            headers={
                "content-type": "text/event-stream",
                "content-encoding": "gzip",
                "transfer-encoding": "chunked",
            },
            request=httpx.Request(
                "GET", "https://cloud.example/api/stock-analysis/analyze"
            ),
        )
    )

    with TestClient(_proxy_app(remote)) as client:
        response = client.post("/api/stock-analysis/analyze", json={"symbol": "000403.SZ"})

    assert response.content == decoded
    assert "content-encoding" not in response.headers
    assert "transfer-encoding" not in response.headers


def test_proxy_prefers_remote_stream_context_and_closes_it_after_delivery() -> None:
    stream = ChunkStream([b"data: one\n\n", b"data: two\n\n"])

    class StreamingRemote:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict]] = []
            self.closed = False

        def request(self, method: str, path: str, **kwargs) -> httpx.Response:
            raise AssertionError("buffered request path must not be used")

        @contextmanager
        def stream(self, method: str, path: str, **kwargs):
            self.calls.append((method, path, kwargs))
            try:
                yield httpx.Response(
                    200,
                    stream=stream,
                    headers={"content-type": "text/event-stream"},
                    request=httpx.Request("GET", f"https://cloud.example{path}"),
                )
            finally:
                self.closed = True

    remote = StreamingRemote()

    with TestClient(_proxy_app(remote)) as client:
        response = client.get("/api/stock-analysis/analyze", params={"symbol": "000403.SZ"})

    assert response.content == b"data: one\n\ndata: two\n\n"
    assert remote.closed is True
    assert remote.calls == [
        (
            "GET",
            "/api/stock-analysis/analyze",
            {
                "params": [("symbol", "000403.SZ")],
                "content": b"",
                "headers": {"accept": "*/*"},
            },
        )
    ]


def test_proxy_logs_and_forwarded_headers_exclude_body_cookie_and_authorization(
    caplog: pytest.LogCaptureFixture,
) -> None:
    remote = RemoteStub(_remote_response())
    caplog.set_level(logging.INFO, logger="app.desktop_client.proxy")

    with TestClient(_proxy_app(remote)) as client:
        response = client.post(
            "/api/stock-analysis/analyze",
            content=b'BODY_SECRET_CANARY',
            headers={
                "Cookie": "COOKIE_SECRET_CANARY",
                "Authorization": "Bearer AUTH_SECRET_CANARY",
                "Content-Type": "application/octet-stream",
            },
        )

    assert response.status_code == 200
    rendered_log = caplog.text
    for canary in ("BODY_SECRET_CANARY", "COOKIE_SECRET_CANARY", "AUTH_SECRET_CANARY"):
        assert canary not in rendered_log
    assert remote.calls[0][2]["content"] == b"BODY_SECRET_CANARY"
    for canary in ("COOKIE_SECRET_CANARY", "AUTH_SECRET_CANARY"):
        assert canary not in repr(remote.calls)


def test_proxy_installs_only_when_desktop_mode_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RemoteStub(_remote_response())
    cloud_app = FastAPI()
    monkeypatch.delenv("TICKFLOW_DESKTOP_CLIENT", raising=False)

    assert install_desktop_proxy(cloud_app, remote_factory=lambda: remote) is False
    assert not any(item.cls is DesktopCloudProxyMiddleware for item in cloud_app.user_middleware)

    desktop_app = FastAPI()
    monkeypatch.setenv("TICKFLOW_DESKTOP_CLIENT", "1")
    assert install_desktop_proxy(desktop_app, remote_factory=lambda: remote) is True
    assert any(item.cls is DesktopCloudProxyMiddleware for item in desktop_app.user_middleware)
