"""Scholar accounts API: /api/auth/register, /login, /logout, /me, /password.

The session is an HttpOnly, SameSite=Lax cookie. These routes are not under the search-access rule (they are for the
website, same origin), so mutating requests are additionally checked to come from this site (`require_same_site`).
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

import accounts
from config import settings
from db_pool import db

COOKIE = "osiss_session"
router = APIRouter(prefix="/api/auth", tags=["accounts"])


def same_site(request: Request) -> bool:
    """The request was made by a page served from this site (the browser sets these headers itself)."""
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site:
        return fetch_site in ("same-origin", "none")
    origin = request.headers.get("origin")
    if not origin:
        return True  # not a browser cross-site request (curl, tests); the cookie alone is the credential
    hosts = {request.headers.get("host", "").lower()}
    if settings.public_base_url:
        hosts.add(urlparse(settings.public_base_url).netloc.lower())
    return urlparse(origin).netloc.lower() in hosts


def require_same_site(request: Request) -> None:
    if request.method not in ("GET", "HEAD", "OPTIONS") and not same_site(request):
        raise HTTPException(403, "Cross-site requests are not allowed.")


def session_token(request: Request) -> Optional[str]:
    return request.cookies.get(COOKIE)


def current_user(request: Request) -> Dict[str, Any]:
    """Dependency for routes that need a signed-in scholar."""
    require_same_site(request)
    with db() as connection:
        user = accounts.user_for_token(connection, session_token(request))
    if user is None:
        raise HTTPException(401, "Sign in to use your workspace.")
    if user["must_change_password"] and not request.url.path.startswith("/api/auth/"):
        raise HTTPException(403, "Choose a new password first (your password was reset by the administrator).")
    return user


def set_cookie(request: Request, response: Response, token: str) -> None:
    secure = request.url.scheme == "https" or settings.public_base_url.startswith("https://")
    response.set_cookie(
        COOKIE, token, max_age=accounts.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure, path="/"
    )


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _fail(exc: accounts.AccountError):
    headers = {"Retry-After": "900"} if exc.status == 429 else None
    raise HTTPException(exc.status, str(exc), headers=headers) from exc


class Credentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=accounts.MAX_PASSWORD)
    name: Optional[str] = Field(default=None, max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=accounts.MAX_PASSWORD)
    new_password: str = Field(max_length=accounts.MAX_PASSWORD)


@router.get("/config")
def auth_config():
    return {"registration": settings.registration_mode == "open"}


@router.post("/register")
def register(payload: Credentials, request: Request, response: Response, _=Depends(require_same_site)):
    if settings.registration_mode != "open":
        raise HTTPException(403, "Registration is closed. Ask the administrator for an account.")
    try:
        with db() as connection:
            user = accounts.create_user(connection, payload.email, payload.password, payload.name)
            token = accounts.start_session(connection, user["id"])
            connection.commit()
    except accounts.AccountError as exc:
        _fail(exc)
    set_cookie(request, response, token)
    return {"user": user}


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response, _=Depends(require_same_site)):
    try:
        with db() as connection:
            try:
                user, token = accounts.login(connection, payload.email, payload.password, client_address(request))
            finally:
                connection.commit()  # keeps the failed-attempt record when the login is refused
    except accounts.AccountError as exc:
        _fail(exc)
    set_cookie(request, response, token)
    return {"user": user}


@router.post("/logout")
def logout(request: Request, response: Response, _=Depends(require_same_site)):
    with db() as connection:
        accounts.end_session(connection, session_token(request))
        connection.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: Dict[str, Any] = Depends(current_user)):
    return {"user": user}


@router.post("/password")
def change_password(payload: PasswordChange, request: Request, user: Dict[str, Any] = Depends(current_user)):
    try:
        with db() as connection:
            accounts.change_password(
                connection, user["id"], payload.current_password, payload.new_password, session_token(request)
            )
            connection.commit()
    except accounts.AccountError as exc:
        _fail(exc)
    return {"ok": True}
