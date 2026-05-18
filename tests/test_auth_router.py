from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import auth


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(auth.router)
    return TestClient(app)


def test_google_login_sets_state_cookie(monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setattr(auth, "OAUTH_REDIRECT_BASE", "http://localhost:8080")

    client = _client()
    response = client.get("/api/auth/google/login", follow_redirects=False)

    assert response.status_code in {302, 307}
    location = response.headers["location"]
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    state = query.get("state", [""])[0]
    assert state

    set_cookie = response.headers.get("set-cookie", "")
    assert f"{auth.GOOGLE_STATE_COOKIE}={state}" in set_cookie


def test_google_callback_rejects_state_mismatch(monkeypatch):
    monkeypatch.setattr(auth, "OAUTH_REDIRECT_BASE", "http://localhost:8080")

    client = _client()
    client.cookies.set(auth.GOOGLE_STATE_COOKIE, "expected-state")
    response = client.get(
        "/api/auth/google/callback?code=dummy-code&state=wrong-state",
        follow_redirects=False,
    )

    assert response.status_code in {302, 307}
    assert response.headers["location"] == "http://localhost:8080/login#error=state_mismatch"


def test_github_callback_rejects_state_mismatch(monkeypatch):
    monkeypatch.setattr(auth, "OAUTH_REDIRECT_BASE", "http://localhost:8080")

    client = _client()
    client.cookies.set(auth.GITHUB_STATE_COOKIE, "expected-state")
    response = client.get(
        "/api/auth/github/callback?code=dummy-code&state=wrong-state",
        follow_redirects=False,
    )

    assert response.status_code in {302, 307}
    assert response.headers["location"] == "http://localhost:8080/login#error=state_mismatch"


def test_register_rejects_invalid_email():
    client = _client()
    response = client.post(
        "/api/auth/register",
        json={"email": "invalid-email", "password": "123456", "name": "User"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid email address."


def test_register_rejects_short_password():
    client = _client()
    response = client.post(
        "/api/auth/register",
        json={"email": "user@example.com", "password": "123", "name": "User"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Password must be at least 6 characters."


def test_register_rejects_duplicate_email(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_user_by_email",
        lambda _email: {"user_id": "email:user@example.com"},
    )

    client = _client()
    response = client.post(
        "/api/auth/register",
        json={"email": "user@example.com", "password": "123456", "name": "User"},
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


def test_login_requires_email_and_password():
    client = _client()
    response = client.post("/api/auth/login", json={"email": "", "password": ""})
    assert response.status_code == 400
    assert response.json()["detail"] == "Email and password are required."


def test_login_rejects_invalid_password(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_user_by_email",
        lambda _email: {"user_id": "email:user@example.com", "password_hash": "stored-hash"},
    )
    monkeypatch.setattr(auth.bcrypt, "checkpw", lambda _p, _h: False)

    client = _client()
    response = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_login_success_returns_token(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_user_by_email",
        lambda _email: {
            "user_id": "email:user@example.com",
            "email": "user@example.com",
            "password_hash": "stored-hash",
        },
    )
    monkeypatch.setattr(auth.bcrypt, "checkpw", lambda _p, _h: True)

    captured = {"updated_user_id": None}
    monkeypatch.setattr(
        auth,
        "update_last_login",
        lambda user_id: captured.update({"updated_user_id": user_id}),
    )
    monkeypatch.setattr(auth, "create_access_token", lambda _uid, email=None: f"token::{email}")

    client = _client()
    response = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "correct-password"},
    )
    assert response.status_code == 200
    assert response.json() == {"token": "token::user@example.com"}
    assert captured["updated_user_id"] == "email:user@example.com"
