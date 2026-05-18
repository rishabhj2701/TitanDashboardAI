import os
import re
import secrets
from urllib.parse import urlencode

import bcrypt
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import httpx

from dynamic_analyst.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET,
    OAUTH_REDIRECT_BASE,
)
from backend.auth.jwt_utils import create_access_token
from backend.auth.db import upsert_user, get_user_by_email, create_email_user, update_last_login
from backend.auth.dependencies import get_current_user

router = APIRouter()

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAIL_URL = "https://api.github.com/user/emails"
GOOGLE_STATE_COOKIE = "oauth_state_google"
GITHUB_STATE_COOKIE = "oauth_state_github"
OAUTH_STATE_MAX_AGE_SECONDS = 600
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
SECURE_COOKIES = APP_ENV in {"production", "prod"} or OAUTH_REDIRECT_BASE.startswith("https://")


def _set_oauth_state_cookie(response: RedirectResponse, cookie_name: str, state: str) -> None:
    response.set_cookie(
        key=cookie_name,
        value=state,
        max_age=OAUTH_STATE_MAX_AGE_SECONDS,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        path="/",
    )


def _clear_oauth_state_cookie(response: RedirectResponse, cookie_name: str) -> None:
    response.delete_cookie(key=cookie_name, path="/")


# ── Google ────────────────────────────────────────────────────────────


@router.get("/api/auth/google/login")
async def google_login():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    redirect_uri = f"{OAUTH_REDIRECT_BASE}/api/auth/google/callback"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": secrets.token_urlsafe(32),
    }
    response = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")
    _set_oauth_state_cookie(response, GOOGLE_STATE_COOKIE, params["state"])
    return response


@router.get("/api/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    if error or not code:
        msg = error or "missing_code"
        response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error={msg}")
        _clear_oauth_state_cookie(response, GOOGLE_STATE_COOKIE)
        return response
    expected_state = request.cookies.get(GOOGLE_STATE_COOKIE, "")
    if not state or not expected_state or state != expected_state:
        response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error=state_mismatch")
        _clear_oauth_state_cookie(response, GOOGLE_STATE_COOKIE)
        return response
    redirect_uri = f"{OAUTH_REDIRECT_BASE}/api/auth/google/callback"
    try:
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error=google_token_failed")
                _clear_oauth_state_cookie(response, GOOGLE_STATE_COOKIE)
                return response
            access_token = token_resp.json().get("access_token")

            user_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error=google_userinfo_failed")
                _clear_oauth_state_cookie(response, GOOGLE_STATE_COOKIE)
                return response
            info = user_resp.json()

        user = upsert_user(
            provider="google",
            provider_id=info["sub"],
            email=info.get("email"),
            name=info.get("name"),
            avatar_url=info.get("picture"),
        )
        jwt_token = create_access_token(user["user_id"], email=user.get("email"))
        response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/#token={jwt_token}")
        _clear_oauth_state_cookie(response, GOOGLE_STATE_COOKIE)
        return response
    except Exception:
        response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error=google_unexpected")
        _clear_oauth_state_cookie(response, GOOGLE_STATE_COOKIE)
        return response


# ── GitHub ────────────────────────────────────────────────────────────


@router.get("/api/auth/github/login")
async def github_login():
    if not GITHUB_CLIENT_ID:
        raise HTTPException(status_code=501, detail="GitHub OAuth not configured")
    redirect_uri = f"{OAUTH_REDIRECT_BASE}/api/auth/github/callback"
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
        "state": secrets.token_urlsafe(32),
    }
    response = RedirectResponse(f"https://github.com/login/oauth/authorize?{urlencode(params)}")
    _set_oauth_state_cookie(response, GITHUB_STATE_COOKIE, params["state"])
    return response


@router.get("/api/auth/github/callback")
async def github_callback(
    request: Request,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    if error or not code:
        msg = error or "missing_code"
        response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error={msg}")
        _clear_oauth_state_cookie(response, GITHUB_STATE_COOKIE)
        return response
    expected_state = request.cookies.get(GITHUB_STATE_COOKIE, "")
    if not state or not expected_state or state != expected_state:
        response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error=state_mismatch")
        _clear_oauth_state_cookie(response, GITHUB_STATE_COOKIE)
        return response
    redirect_uri = f"{OAUTH_REDIRECT_BASE}/api/auth/github/callback"
    try:
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "client_id": GITHUB_CLIENT_ID,
                    "client_secret": GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            if token_resp.status_code != 200:
                response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error=github_token_failed")
                _clear_oauth_state_cookie(response, GITHUB_STATE_COOKIE)
                return response
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error=github_denied")
                _clear_oauth_state_cookie(response, GITHUB_STATE_COOKIE)
                return response

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            }
            user_resp = await client.get(GITHUB_USER_URL, headers=headers)
            if user_resp.status_code != 200:
                response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error=github_user_failed")
                _clear_oauth_state_cookie(response, GITHUB_STATE_COOKIE)
                return response
            gh_user = user_resp.json()

            email = gh_user.get("email")
            if not email:
                email_resp = await client.get(GITHUB_EMAIL_URL, headers=headers)
                if email_resp.status_code == 200:
                    for entry in email_resp.json():
                        if entry.get("primary") and entry.get("verified"):
                            email = entry.get("email")
                            break

        user = upsert_user(
            provider="github",
            provider_id=str(gh_user["id"]),
            email=email,
            name=gh_user.get("name") or gh_user.get("login"),
            avatar_url=gh_user.get("avatar_url"),
        )
        jwt_token = create_access_token(user["user_id"], email=user.get("email"))
        response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/#token={jwt_token}")
        _clear_oauth_state_cookie(response, GITHUB_STATE_COOKIE)
        return response
    except Exception:
        response = RedirectResponse(f"{OAUTH_REDIRECT_BASE}/login#error=github_unexpected")
        _clear_oauth_state_cookie(response, GITHUB_STATE_COOKIE)
        return response


# ── Email/password ────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/api/auth/register")
async def register(body: RegisterRequest):
    email = body.email.strip().lower()
    name = body.name.strip()
    password = body.password

    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")

    existing = get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    user = create_email_user(email=email, name=name, password_hash=hashed.decode("utf-8"))

    token = create_access_token(user["user_id"], email=user.get("email"))
    return {"token": token}


@router.post("/api/auth/login")
async def login(body: LoginRequest):
    email = body.email.strip().lower()
    password = body.password

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")

    user = get_user_by_email(email)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    update_last_login(user["user_id"])
    token = create_access_token(user["user_id"], email=user.get("email"))
    return {"token": token}


# ── /auth/me ──────────────────────────────────────────────────────────


@router.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    created = current_user.get("created_at")
    return {
        "user_id": current_user["user_id"],
        "email": current_user.get("email"),
        "name": current_user.get("name"),
        "avatar_url": current_user.get("avatar_url"),
        "provider": current_user.get("provider"),
        "created_at": created.isoformat() if created else None,
    }
