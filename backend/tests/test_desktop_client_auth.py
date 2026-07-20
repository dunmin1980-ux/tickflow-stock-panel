from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.desktop_client import api as client_api
from app.desktop_client.config import (
    ClientConfig,
    load_client_config,
    save_client_config,
    validate_remote_url,
)
from app.desktop_client.keychain import KeychainSessionStore
from app.desktop_client.remote import RemoteClient, RemotePathError


def test_client_config_rejects_public_http(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        save_client_config(tmp_path, {"remote_base_url": "http://example.com"})


@pytest.mark.parametrize(
    "value",
    [
        "https://user:password@example.com",
        "https://example.com/private",
        "https://example.com?token=secret",
        "https://example.com#fragment",
    ],
)
def test_client_config_rejects_credentials_path_query_and_fragment(value: str) -> None:
    with pytest.raises(ValueError):
        validate_remote_url(value)


def test_client_config_allows_loopback_http_and_writes_only_public_fields(
    tmp_path: Path,
) -> None:
    config = save_client_config(
        tmp_path,
        {
            "remote_base_url": "http://127.0.0.1:3019/",
            "preferred_mode": "cloud",
        },
    )

    path = tmp_path / "desktop_client.json"
    assert config == ClientConfig("http://127.0.0.1:3019", "cloud")
    assert load_client_config(tmp_path) == config
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "preferred_mode": "cloud",
        "remote_base_url": "http://127.0.0.1:3019",
    }
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_client_config_rejects_unknown_or_invalid_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_client_config(
            tmp_path,
            {
                "remote_base_url": "https://vm.tail.ts.net:8443",
                "preferred_mode": "local",
            },
        )
    with pytest.raises(ValueError):
        save_client_config(
            tmp_path,
            {
                "remote_base_url": "https://vm.tail.ts.net:8443",
                "preferred_mode": "hybrid",
                "token": "must-not-persist",
            },
        )


def test_keychain_never_puts_token_in_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("app.desktop_client.keychain.sys.platform", "darwin")
    monkeypatch.setattr(subprocess, "run", fake_run)

    KeychainSessionStore().save("https://vm.tail.ts.net:8443", "secret-token")

    args, kwargs = calls[0]
    assert args[0] == "/usr/bin/security"
    assert "secret-token" not in args
    assert kwargs["input"] == "secret-token\nsecret-token\n"
    assert kwargs["shell"] is False


def test_keychain_account_scopes_lookup_and_delete_by_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        stdout = "stored-token\n" if "find-generic-password" in args else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr("app.desktop_client.keychain.sys.platform", "darwin")
    monkeypatch.setattr(subprocess, "run", fake_run)
    store = KeychainSessionStore()

    assert store.load("https://one.tail.ts.net:8443") == "stored-token"
    store.delete("https://one.tail.ts.net:8443")
    store.load("https://two.tail.ts.net:8443")

    first_account = calls[0][calls[0].index("-a") + 1]
    delete_account = calls[1][calls[1].index("-a") + 1]
    second_account = calls[2][calls[2].index("-a") + 1]
    assert first_account == delete_account
    assert first_account != second_account
    assert all("com.tickflow.stockpanel.remote-session" in call for call in calls)


def test_remote_login_posts_password_once_and_stores_only_session_cookie() -> None:
    seen_requests: list[httpx.Request] = []
    stored: list[tuple[str, str]] = []

    class Sessions:
        def save(self, base_url: str, token: str) -> None:
            stored.append((base_url, token))

        def load(self, base_url: str) -> str | None:
            return None

        def delete(self, base_url: str) -> None:
            raise AssertionError("delete not expected")

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "authenticated": True},
            headers={"set-cookie": "tf_session=abc; HttpOnly; Secure; SameSite=Lax"},
        )

    remote = RemoteClient(
        "https://vm.tail.ts.net:8443",
        sessions=Sessions(),
        transport=httpx.MockTransport(handler),
    )

    assert remote.login("cloud-password") is True
    assert stored == [("https://vm.tail.ts.net:8443", "abc")]
    assert len(seen_requests) == 1
    request = seen_requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/auth/login"
    assert json.loads(request.content) == {"password": "cloud-password"}
    assert "cloud-password" not in str(request.headers)


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/api/data/status",
        "//evil.example/api/data/status",
        "/other/status",
        "/api/../settings",
        "/api/%2e%2e/settings",
        "/api/data\\..\\settings",
        "/api/data/status\r\nX-Evil: yes",
    ],
)
def test_remote_request_rejects_non_api_cross_origin_or_injected_paths(path: str) -> None:
    class Sessions:
        def load(self, base_url: str) -> str:
            return "session-token"

    remote = RemoteClient(
        "https://vm.tail.ts.net:8443",
        sessions=Sessions(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )

    with pytest.raises(RemotePathError):
        remote.request("GET", path)


@pytest.mark.parametrize(
    "headers",
    [
        {"Cookie": "attacker=1"},
        {"authorization": "Bearer attacker"},
        {"X-Test\r\nInjected": "yes"},
        {"X-Test": "yes\r\nInjected: true"},
    ],
)
def test_remote_request_rejects_credential_and_injected_headers(headers: dict[str, str]) -> None:
    class Sessions:
        def load(self, base_url: str) -> str:
            return "session-token"

    remote = RemoteClient(
        "https://vm.tail.ts.net:8443",
        sessions=Sessions(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )

    with pytest.raises(ValueError):
        remote.request("GET", "/api/data/status", headers=headers)


def test_status_probe_uses_only_auth_status_and_scoped_cookie() -> None:
    seen_requests: list[httpx.Request] = []

    class Sessions:
        def load(self, base_url: str) -> str:
            return "session-token"

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"configured": True, "authenticated": True})

    remote = RemoteClient(
        "https://vm.tail.ts.net:8443",
        sessions=Sessions(),
        transport=httpx.MockTransport(handler),
    )

    assert remote.auth_status() == {"configured": True, "authenticated": True}
    assert [request.url.path for request in seen_requests] == ["/api/auth/status"]
    assert seen_requests[0].headers["cookie"] == "tf_session=session-token"


def _client_api_app() -> FastAPI:
    app = FastAPI()
    app.include_router(client_api.router)
    return app


def test_client_api_login_response_never_contains_password_or_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured_passwords: list[str] = []

    class Remote:
        def login(self, password: str) -> bool:
            captured_passwords.append(password)
            return True

    monkeypatch.setattr(client_api.settings, "data_dir", tmp_path)
    save_client_config(tmp_path, {"remote_base_url": "https://vm.tail.ts.net:8443"})
    monkeypatch.setattr(client_api, "_remote_for", lambda config: Remote())

    with TestClient(_client_api_app()) as client:
        response = client.post("/api/client/auth/login", json={"password": "cloud-password"})

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    assert captured_passwords == ["cloud-password"]
    assert "cloud-password" not in response.text
    assert "tf_session" not in response.text


def test_client_api_status_maps_remote_result_without_leaking_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Remote:
        def auth_status(self) -> dict:
            return {
                "configured": True,
                "authenticated": True,
                "token": "must-not-leak",
            }

    monkeypatch.setattr(client_api.settings, "data_dir", tmp_path)
    save_client_config(
        tmp_path,
        {
            "remote_base_url": "https://vm.tail.ts.net:8443",
            "preferred_mode": "hybrid",
        },
    )
    monkeypatch.setattr(client_api, "_remote_for", lambda config: Remote())

    with TestClient(_client_api_app()) as client:
        response = client.get("/api/client/status")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "authenticated": True,
        "reachable": True,
        "mode": "hybrid",
        "error_code": None,
    }
    assert "must-not-leak" not in response.text


def test_client_api_logout_deletes_session_and_returns_only_auth_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []

    class Remote:
        def logout(self) -> None:
            events.append("logout")

    monkeypatch.setattr(client_api.settings, "data_dir", tmp_path)
    save_client_config(tmp_path, {"remote_base_url": "https://vm.tail.ts.net:8443"})
    monkeypatch.setattr(client_api, "_remote_for", lambda config: Remote())

    with TestClient(_client_api_app()) as client:
        response = client.post("/api/client/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"authenticated": False}
    assert events == ["logout"]
