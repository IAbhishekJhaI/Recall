"""Authentication dependencies.

The current user is resolved *only* from the server-side session referenced by
the session cookie. Every per-user store is opened from that user's id.
"""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, HTTPException, Request, Response

from .accounts import AccountStore, User
from .config import Settings
from .store import UserStore

COOKIE_NAME = "recall_session"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_accounts(request: Request) -> AccountStore:
    return request.app.state.accounts


def current_user(request: Request, accounts: AccountStore = Depends(get_accounts)) -> User:
    user = accounts.user_for_session(request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=404, detail="Not found")
    return user


def user_store(user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> Iterator[UserStore]:
    store = UserStore(settings.users_dir, user.id)
    try:
        yield store
    finally:
        store.close()


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=settings.session_days * 86400,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
