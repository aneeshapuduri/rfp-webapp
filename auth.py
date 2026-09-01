"""
Authentication, sessions, roles, and CSRF protection.

Design goals (this used to be a completely open, no-login internal tool — see the review
that flagged that as a critical gap):
  - Per-user accounts with two roles: 'admin' (can manage users) and 'member' (everything else).
  - Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib only, no extra dependency) with a
    per-user random salt and a high iteration count — never stored or logged in plaintext.
  - Sessions are signed cookies (Starlette's SessionMiddleware, itsdangerous under the hood) —
    no server-side session table needed for a tool this size.
  - CSRF tokens are per-session and checked on every state-changing (POST) route via the
    `verify_csrf` dependency — added explicitly to each POST route rather than as blanket
    middleware, so it can't silently misfire on the request-body stream.
  - The auth gate itself IS a blanket middleware (deny-by-default) specifically so a route
    added later can't accidentally ship without a login check the way every route did before.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

import db

PBKDF2_ITERATIONS = 260_000

PUBLIC_PATHS = {"/login", "/health"}
PUBLIC_PREFIXES = ("/static/",)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iterations, salt, hex_digest = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), hex_digest)
    except (ValueError, AttributeError):
        return False


def get_session_secret() -> str:
    """
    A session secret signs the login cookie — if it changes, every session is invalidated
    (acceptable) but if it were guessable, sessions could be forged (not acceptable). Prefer
    an explicit SESSION_SECRET from the environment so sessions survive a restart; fall back to
    a random one generated at startup (with a loud warning) rather than a hardcoded default.
    """
    secret = os.environ.get("SESSION_SECRET")
    if secret:
        return secret
    generated = secrets.token_hex(32)
    print("[STARTUP WARNING] SESSION_SECRET is not set — using a randomly generated session "
          "secret for this process only. Every logged-in session will be invalidated on "
          "restart. Set SESSION_SECRET to a fixed, secret value in production.")
    return generated


class AuthGateMiddleware(BaseHTTPMiddleware):
    """Deny-by-default: every route requires a logged-in, active user unless explicitly
    public. Also gates /admin/* to the 'admin' role. Ensures a newly added route is locked
    down by default instead of needing someone to remember to add a login check."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        user_id = request.session.get("user_id")
        user = db.get_user(user_id) if user_id else None
        if not user or not user["is_active"]:
            request.session.clear()
            if request.method == "GET":
                return RedirectResponse(f"/login?next={path}", status_code=303)
            return RedirectResponse("/login", status_code=303)

        request.session.setdefault("csrf_token", secrets.token_hex(32))
        request.state.user = user

        if path.startswith("/admin") and user["role"] != "admin":
            return PlainTextResponse("Forbidden — admin access required.", status_code=403)

        return await call_next(request)


async def verify_csrf(request: Request):
    """Explicit per-route dependency (not middleware) so it reads the form body the same way
    FastAPI's own Form(...)/File(...) parameters do — Starlette caches the parsed form on the
    Request the first time it's read, so this and the route's own Form() params see the same
    parse rather than racing to read the body stream twice."""
    from fastapi import HTTPException
    form = await request.form()
    token = form.get("csrf_token", "")
    session_token = request.session.get("csrf_token")
    if not session_token or not secrets.compare_digest(str(token), str(session_token)):
        raise HTTPException(403, "Your session expired or the form was tampered with. Go back, refresh, and try again.")


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def ensure_bootstrap_admin():
    """
    On first run (no users at all), create the initial admin account so there's a way to log
    in at all. Prefers ADMIN_USERNAME/ADMIN_PASSWORD from the environment for a scripted/CI
    deploy; otherwise generates a random password and prints it once — never a hardcoded
    default credential that could be left in place unnoticed.
    """
    if db.count_users() > 0:
        return
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD")
    generated = False
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True
    db.create_user(username, hash_password(password), role="admin", created_by="system_bootstrap")
    db.log_action("bootstrap_admin_created", detail={"username": username}, user_identity="system")
    print("=" * 78)
    print(f"[FIRST RUN] Created initial admin account — username: {username}")
    if generated:
        print(f"[FIRST RUN] Generated password (shown once): {password}")
        print("[FIRST RUN] Log in and consider setting ADMIN_USERNAME/ADMIN_PASSWORD env vars,")
        print("[FIRST RUN] or add more accounts from the Admin > Users page, then rotate this one.")
    else:
        print("[FIRST RUN] Password set from ADMIN_PASSWORD environment variable.")
    print("=" * 78)
