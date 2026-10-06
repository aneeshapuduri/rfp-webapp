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
import threading
import time
from urllib.parse import quote

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


def safe_next_path(candidate: str | None, default: str = "/home") -> str:
    """Validates a post-login redirect target. Only a plain same-site absolute path is allowed:
    it must start with exactly one "/" — "//host/path" and "/\\host" are protocol-relative URLs
    that browsers treat as an off-site link, so a bare startswith("/") check (what login used to
    do) let an attacker craft /login?next=//evil.example and bounce a freshly authenticated user
    to their site. Control characters / backslashes anywhere in the value are rejected too."""
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return default
    if "\\" in candidate or any(ord(ch) < 32 for ch in candidate):
        return default
    return candidate


# Dummy hash verified when the username doesn't exist, so a login attempt for an unknown
# account takes the same ~PBKDF2 time as one for a real account (otherwise response time alone
# reveals which usernames exist).
_DUMMY_HASH = None


def burn_password_check(password: str):
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("not-a-real-password")
    verify_password(password, _DUMMY_HASH)


# Minimal in-process brute-force throttle for the login form: after MAX_FAILURES failed attempts
# for the same username (or the same client IP) inside WINDOW_SECONDS, further attempts are
# refused until the window passes. In-memory by design (single-process app, no extra
# dependency) — it resets on restart, which is acceptable for a speed bump against guessing.
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_FAILURES = 8
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def _prune(key: str, now: float) -> list[float]:
    recent = [t for t in _failures.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)
    return recent


def login_throttled(username: str, client_ip: str) -> bool:
    now = time.time()
    with _failures_lock:
        return (len(_prune(f"u:{username.strip().lower()}", now)) >= _LOGIN_MAX_FAILURES
                or len(_prune(f"ip:{client_ip}", now)) >= _LOGIN_MAX_FAILURES * 3)


def record_login_failure(username: str, client_ip: str):
    now = time.time()
    with _failures_lock:
        _failures.setdefault(f"u:{username.strip().lower()}", []).append(now)
        _failures.setdefault(f"ip:{client_ip}", []).append(now)


def clear_login_failures(username: str):
    with _failures_lock:
        _failures.pop(f"u:{username.strip().lower()}", None)


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
                # Keep the query string too (e.g. /audit?tab=system) and URL-encode the whole
                # target so characters like "&" or "#" can't corrupt the login URL.
                target = path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
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
