"""Login, sessions and roles.

- Passwords: Argon2id (argon2-cffi).
- Sessions: random 256-bit token in an HttpOnly, SameSite=Strict cookie; the
  database only stores its SHA-256. Sessions end after SESSION_IDLE_MINUTES of
  inactivity and after SESSION_MAX_HOURS at the latest.
- CSRF: besides SameSite=Strict, every state-changing request must carry the
  header `X-Requested-With: erechnung` (a cross-site form cannot set it).
- Brute force: after MAX_FAILURES failed logins per username+IP, further
  attempts are refused for LOCKOUT_SECONDS.
- Roles: "user" may convert and use the archive; "admin" additionally manages
  users and settings (connectors, company data) and reads the audit log.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import LoginSession, User, audit, get_session

log = logging.getLogger("erechnung.auth")

Role = Literal["admin", "user"]
COOKIE_NAME = "erechnung_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "erechnung"
MIN_PASSWORD_LENGTH = 10
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300

_hasher = PasswordHasher()
# Verified against when the username does not exist, so both cases take equally long.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-password")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def idle_timeout() -> timedelta:
    return timedelta(minutes=_env_int("SESSION_IDLE_MINUTES", 60))


def max_lifetime() -> timedelta:
    return timedelta(hours=_env_int("SESSION_MAX_HOURS", 12))


def cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")


# --- passwords -------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def check_password_policy(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.")
    if username and password.lower() == username.lower():
        raise HTTPException(400, "Das Passwort darf nicht dem Benutzernamen entsprechen.")


# --- brute-force protection ------------------------------------------------


class LoginThrottle:
    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        return [t for t in self._failures.get(key, []) if now - t < LOCKOUT_SECONDS]

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key, time.monotonic())) >= MAX_FAILURES

    def fail(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._failures[key] = self._recent(key, now) + [now]

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


throttle = LoginThrottle()


# --- sessions --------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite drops tzinfo


def login(session: Session, request: Request, response: Response, username: str, password: str) -> User:
    client = request.client.host if request.client else "?"
    key = f"{username.lower()}|{client}"
    if throttle.blocked(key):
        raise HTTPException(429, "Zu viele Fehlversuche. Bitte in einigen Minuten erneut versuchen.")

    user = session.scalar(select(User).where(User.username == username))
    ok = verify_password(user.password_hash if user else _DUMMY_HASH, password)
    if not (user and ok and user.active):
        throttle.fail(key)
        audit(session, username, "login_failed", ip=client)
        session.commit()
        raise HTTPException(401, "Benutzername oder Passwort ist falsch.")

    throttle.reset(key)
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session.add(LoginSession(token_hash=_token_hash(token), user_id=user.id, created_at=now, last_seen_at=now))
    user.last_login_at = now
    audit(session, user.username, "login", ip=client)
    session.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        path="/api",
        max_age=int(max_lifetime().total_seconds()),
    )
    return user


def logout(session: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        session.execute(delete(LoginSession).where(LoginSession.token_hash == _token_hash(token)))
        session.commit()
    response.delete_cookie(COOKIE_NAME, path="/api")


def end_sessions_of(session: Session, user_id: int, keep_token_hash: str | None = None) -> None:
    stmt = delete(LoginSession).where(LoginSession.user_id == user_id)
    if keep_token_hash:
        stmt = stmt.where(LoginSession.token_hash != keep_token_hash)
    session.execute(stmt)


# --- FastAPI dependencies --------------------------------------------------


def _authenticate(request: Request, session: Session) -> tuple[User, LoginSession]:
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, "Anfrage abgelehnt (fehlender Schutz-Header).")
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Bitte anmelden.")
    row = session.get(LoginSession, _token_hash(token))
    now = datetime.now(UTC)
    if row is None or now - _utc(row.last_seen_at) > idle_timeout() or now - _utc(row.created_at) > max_lifetime():
        if row is not None:
            session.delete(row)
            session.commit()
        raise HTTPException(401, "Sitzung abgelaufen. Bitte erneut anmelden.")
    user = session.get(User, row.user_id)
    if user is None or not user.active:
        raise HTTPException(401, "Bitte anmelden.")
    if now - _utc(row.last_seen_at) > timedelta(minutes=1):  # avoid a write on every request
        row.last_seen_at = now
        session.commit()
    return user, row


def current_session(request: Request, session: Session = Depends(get_session)) -> tuple[User, LoginSession]:
    """Logged in, even if a password change is still pending (for /me, /password, /logout)."""
    return _authenticate(request, session)


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    """Logged in and allowed to work (initial password already changed)."""
    user, _ = _authenticate(request, session)
    if user.must_change_password:
        raise HTTPException(403, "Bitte zuerst das Passwort ändern.")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Nur für Administratoren.")
    return user


# --- first admin -----------------------------------------------------------


def bootstrap_admin(session: Session) -> None:
    """Create the first admin from ADMIN_USER / ADMIN_PASSWORD if no user exists yet.
    The password must be changed at first login."""
    if session.scalar(select(User.id).limit(1)) is not None:
        return
    username = os.environ.get("ADMIN_USER", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        log.warning(
            "Noch kein Benutzer vorhanden. ADMIN_USER und ADMIN_PASSWORD setzen oder "
            "'python -m app.manage create-admin <name>' ausführen."
        )
        return
    session.add(User(username=username, password_hash=hash_password(password), role="admin", must_change_password=True))
    audit(session, "system", "user_created", username=username, role="admin")
    session.commit()
    log.info("Erster Administrator '%s' angelegt.", username)
