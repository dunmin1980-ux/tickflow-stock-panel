from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import app


def test_login_cookie_is_secure_when_configured(monkeypatch):
    monkeypatch.setattr(auth_api.auth, "is_configured", lambda: True)
    monkeypatch.setattr(auth_api.auth, "verify_and_create_session", lambda _: "session-token")
    monkeypatch.setattr(auth_api.settings, "auth_cookie_secure", True, raising=False)

    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"password": "secret"})

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie
    assert "session-token" not in response.text
