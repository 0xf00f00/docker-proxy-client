"""Optional shared-password authentication.

Auth is disabled by default. Set ``DASHBOARD_PASSWORD`` in the environment to
enable it: every protected endpoint then requires a valid session cookie,
issued by ``POST /api/v1/auth/login`` after the user submits the password.
"""

import hashlib
import hmac
import time

from fastapi import Depends, HTTPException, Request, Response, status

from app.config import settings
from app.services import store

SESSION_COOKIE = "dashboard_session"
SESSION_TTL_SEC = 30 * 24 * 3600

_signing_key: bytes | None = None


def auth_enabled() -> bool:
    return bool(settings.password)


def _key() -> bytes:
    """Per-process signing key: persistent secret bound to the current password."""
    global _signing_key
    if _signing_key is None:
        pw_hash = hashlib.sha256(settings.password.encode("utf-8")).digest()
        _signing_key = hmac.new(store.get_or_create_session_secret(), pw_hash, hashlib.sha256).digest()
    return _signing_key


def _sign(expiry: int) -> str:
    return hmac.new(_key(), str(expiry).encode("utf-8"), hashlib.sha256).hexdigest()


def issue_session(response: Response) -> str:
    expiry = int(time.time()) + SESSION_TTL_SEC
    token = f"{expiry}.{_sign(expiry)}"
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
    response.delete_cookie(SESSION_COOKIE, path="/")


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or "." not in token:
        return False
    expiry_str, sig = token.rsplit(".", 1)
    try:
        expiry = int(expiry_str)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(expiry)):
        return False
    return expiry > time.time()


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
    return hmac.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8"))


RequireAuth = Depends(require_auth)
