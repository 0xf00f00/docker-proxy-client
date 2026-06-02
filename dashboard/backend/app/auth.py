"""Optional shared-password authentication.

Auth is disabled by default. Set ``DASHBOARD_PASSWORD`` in the environment to
enable it: every protected endpoint then requires a valid session cookie,
issued by ``POST /api/v1/auth/login`` after the user submits the password.

Sessions are stored in-process and live only as long as the dashboard does;
a restart logs everyone out, which is fine for a household-LAN tool.
"""

import secrets
import threading
import time

from fastapi import Depends, HTTPException, Request, Response, status

from app.config import settings

SESSION_COOKIE = "dashboard_session"
SESSION_TTL_SEC = 30 * 24 * 3600

_sessions: dict[str, float] = {}
_lock = threading.Lock()


def auth_enabled() -> bool:
    return bool(settings.password)


def _purge_expired(now: float) -> None:
    for token in [t for t, exp in _sessions.items() if exp <= now]:
        _sessions.pop(token, None)


def issue_session(response: Response) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _purge_expired(now)
        _sessions[token] = now + SESSION_TTL_SEC
    # Secure flag intentionally omitted — dashboard is plain HTTP by design (LAN, IP-only).
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL_SEC,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return token


def revoke_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with _lock:
            _sessions.pop(token, None)
    response.delete_cookie(SESSION_COOKIE, path="/")


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    with _lock:
        expiry = _sessions.get(token)
        if expiry is None:
            return False
        if expiry <= time.time():
            _sessions.pop(token, None)
            return False
    return True


def require_auth(request: Request) -> None:
    """FastAPI dependency. No-op when auth is disabled."""
    if not auth_enabled():
        return
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def verify_password(submitted: str) -> bool:
    expected = settings.password
    if not expected:
        return False
    return secrets.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8"))


RequireAuth = Depends(require_auth)
