"""Auth endpoints (/api/auth/*) and admin-only endpoints (/api/admin/*)."""

from __future__ import annotations

import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth
from .auth import Role, current_session, require_admin
from .company import Company
from .db import AuditEntry, LoginSession, User, audit, get_session, get_setting, put_setting
from .settings import IdocSettings, PdfInboxSettings, get_company, get_idoc_settings, get_pdf_inbox_settings
from .worker import STATUS_KEY

router = APIRouter(prefix="/api")

USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{2,100}$")


class UserInfo(BaseModel):
    id: int
    username: str
    role: Role
    active: bool
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class NewUser(BaseModel):
    username: str
    role: Role = "user"
    password: str = Field(max_length=200)  # initial password, must be changed at first login


class UserUpdate(BaseModel):
    role: Role | None = None
    active: bool | None = None
    password: str | None = Field(None, max_length=200)  # admin reset -> must change at next login


class Settings(BaseModel):
    company: Company
    idoc: IdocSettings
    pdf_inbox: PdfInboxSettings = Field(default_factory=PdfInboxSettings)


class AuditInfo(BaseModel):
    id: int
    at: datetime
    username: str
    action: str
    detail: dict

    model_config = {"from_attributes": True}


# --- auth -------------------------------------------------------------------


@router.post("/auth/login", response_model=UserInfo)
def login(body: LoginRequest, request: Request, response: Response, session: Session = Depends(get_session)) -> User:
    return auth.login(session, request, response, body.username.strip(), body.password)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, session: Session = Depends(get_session)) -> Response:
    auth.logout(session, request, response)
    response.status_code = 204
    return response


@router.get("/auth/me", response_model=UserInfo)
def me(current: tuple[User, LoginSession] = Depends(current_session)) -> User:
    return current[0]


@router.post("/auth/password", status_code=204)
def change_password(
    body: PasswordChange,
    current: tuple[User, LoginSession] = Depends(current_session),
    session: Session = Depends(get_session),
) -> Response:
    user, login_session = current
    if not auth.verify_password(user.password_hash, body.current_password):
        raise HTTPException(400, "Das aktuelle Passwort ist falsch.")
    if body.new_password == body.current_password:
        raise HTTPException(400, "Das neue Passwort muss sich vom alten unterscheiden.")
    auth.check_password_policy(body.new_password, user.username)
    user = session.merge(user)
    user.password_hash = auth.hash_password(body.new_password)
    user.must_change_password = False
    auth.end_sessions_of(session, user.id, keep_token_hash=login_session.token_hash)  # log out other devices
    audit(session, user.username, "password_changed")
    session.commit()
    return Response(status_code=204)


# --- users (admin) -----------------------------------------------------------


def _active_admins(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active)) or 0


@router.get("/admin/users", response_model=list[UserInfo])
def list_users(_: User = Depends(require_admin), session: Session = Depends(get_session)) -> list[User]:
    return list(session.scalars(select(User).order_by(User.username)))


@router.post("/admin/users", response_model=UserInfo, status_code=201)
def create_user(body: NewUser, admin: User = Depends(require_admin), session: Session = Depends(get_session)) -> User:
    username = body.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Benutzername: 2–100 Zeichen, nur Buchstaben, Ziffern und . _ @ -")
    if session.scalar(select(User.id).where(User.username == username)) is not None:
        raise HTTPException(409, "Diesen Benutzernamen gibt es schon.")
    auth.check_password_policy(body.password, username)
    user = User(
        username=username, password_hash=auth.hash_password(body.password), role=body.role, must_change_password=True
    )
    session.add(user)
    audit(session, admin.username, "user_created", username=username, role=body.role)
    session.commit()
    return user


@router.patch("/admin/users/{user_id}", response_model=UserInfo)
def update_user(
    user_id: int, body: UserUpdate, admin: User = Depends(require_admin), session: Session = Depends(get_session)
) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Benutzer nicht gefunden.")
    demotes_admin = user.role == "admin" and user.active and (body.role == "user" or body.active is False)
    if demotes_admin and _active_admins(session) <= 1:
        raise HTTPException(400, "Der letzte aktive Administrator kann nicht entfernt oder gesperrt werden.")

    changes: dict[str, object] = {}
    if body.role is not None and body.role != user.role:
        user.role = body.role
        changes["role"] = body.role
    if body.active is not None and body.active != user.active:
        user.active = body.active
        changes["active"] = body.active
        if not body.active:
            auth.end_sessions_of(session, user.id)
    if body.password is not None:
        auth.check_password_policy(body.password, user.username)
        user.password_hash = auth.hash_password(body.password)
        user.must_change_password = True
        auth.end_sessions_of(session, user.id)
        changes["password_reset"] = True
    audit(session, admin.username, "user_updated", username=user.username, **changes)
    session.commit()
    return user


# --- settings / connectors (admin) -------------------------------------------


@router.get("/admin/settings", response_model=Settings)
def read_settings(_: User = Depends(require_admin), session: Session = Depends(get_session)) -> Settings:
    return Settings(
        company=get_company(session), idoc=get_idoc_settings(session), pdf_inbox=get_pdf_inbox_settings(session)
    )


@router.put("/admin/settings", response_model=Settings)
def write_settings(
    body: Settings, admin: User = Depends(require_admin), session: Session = Depends(get_session)
) -> Settings:
    put_setting(session, "company", body.company.model_dump(mode="json"), admin.username)
    put_setting(session, "idoc", body.idoc.model_dump(mode="json"), admin.username)
    put_setting(session, "pdf_inbox", body.pdf_inbox.model_dump(mode="json"), admin.username)
    audit(
        session,
        admin.username,
        "settings_changed",
        idoc=body.idoc.model_dump(mode="json"),
        pdf_inbox=body.pdf_inbox.model_dump(mode="json"),
        company=body.company.seller.name,
    )
    session.commit()
    return body


# --- audit log (admin) -------------------------------------------------------


@router.get("/admin/audit", response_model=list[AuditInfo])
def read_audit(
    limit: int = Query(200, ge=1, le=1000),
    action: Literal[
        "login",
        "login_failed",
        "export",
        "download",
        "user_created",
        "user_updated",
        "password_changed",
        "password_reset",
        "settings_changed",
        "idoc_retry",
        "review_discarded",
    ]
    | None = None,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[AuditEntry]:
    stmt = select(AuditEntry).order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditEntry.action == action)
    return list(session.scalars(stmt))


# --- inbox status: SAP IDocs + PDFs (admin) -----------------------------------

InboxKind = Literal["idoc", "pdf"]
INBOXES: dict[str, tuple[str, str, str]] = {  # kind -> (label, env var, default dir)
    "idoc": ("SAP-IDocs", "IDOC_DIR", "./data/idoc"),
    "pdf": ("PDF-Rechnungen", "PDF_DIR", "./data/pdf"),
}


class ErrorFile(BaseModel):
    name: str
    size: int
    modified_at: datetime
    reason: str


class InboxInfo(BaseModel):
    kind: InboxKind
    label: str
    folder_available: bool  # is the folder mounted into the backend?
    counts: dict[str, int]  # files per folder: inbox, outbox, processed, error
    errors: list[ErrorFile]


class InboxStatus(BaseModel):
    worker_alive: bool
    worker: dict  # last report of the worker (see worker.report_status)
    inboxes: list[InboxInfo]


class RetryRequest(BaseModel):
    kind: InboxKind = "idoc"
    name: str = Field(max_length=255)


def _inbox_base(kind: str) -> Path:
    _, env, default = INBOXES[kind]
    return Path(os.environ.get(env, default))


def _count(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.iterdir() if p.is_file() and not p.name.endswith(".error.txt"))


def _errors(folder: Path) -> list[ErrorFile]:
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".error.txt")]
    result = []
    for p in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:100]:
        reason_file = p.with_name(p.name + ".error.txt")
        reason = reason_file.read_text(encoding="utf-8", errors="replace").strip() if reason_file.exists() else ""
        st = p.stat()
        result.append(
            ErrorFile(name=p.name, size=st.st_size, modified_at=datetime.fromtimestamp(st.st_mtime, UTC), reason=reason)
        )
    return result


@router.get("/admin/inbox/status", response_model=InboxStatus)
def inbox_status(_: User = Depends(require_admin), session: Session = Depends(get_session)) -> InboxStatus:
    worker = get_setting(session, STATUS_KEY) or {}
    alive = False
    if worker.get("last_round_at"):
        age = datetime.now(UTC) - datetime.fromisoformat(worker["last_round_at"])
        alive = age.total_seconds() < 3 * int(worker.get("interval_seconds", 10)) + 30
    inboxes = []
    for kind, (label, _, _) in INBOXES.items():
        base = _inbox_base(kind)
        inboxes.append(
            InboxInfo(
                kind=kind,  # type: ignore[arg-type]
                label=label,
                folder_available=(base / "inbox").is_dir(),
                counts={name: _count(base / name) for name in ("inbox", "outbox", "processed", "error")},
                errors=_errors(base / "error"),
            )
        )
    return InboxStatus(worker_alive=alive, worker=worker, inboxes=inboxes)


@router.post("/admin/inbox/retry", status_code=204)
def inbox_retry(
    body: RetryRequest, admin: User = Depends(require_admin), session: Session = Depends(get_session)
) -> Response:
    """Move a failed file back into its inbox (e.g. after fixing master data or a template)."""
    base = _inbox_base(body.kind)
    if Path(body.name).name != body.name or body.name.endswith(".error.txt"):
        raise HTTPException(400, "Ungültiger Dateiname.")
    src = base / "error" / body.name
    if not src.is_file():
        raise HTTPException(404, "Datei nicht im Fehlerordner gefunden.")
    target = base / "inbox" / body.name
    n = 1
    while target.exists():
        target = base / "inbox" / f"{src.stem}.retry{n}{src.suffix}"
        n += 1
    shutil.move(str(src), target)
    os.utime(target, (time.time() - 60, time.time() - 60))  # ready for the worker right away
    src.with_name(src.name + ".error.txt").unlink(missing_ok=True)
    audit(session, admin.username, "idoc_retry", file=body.name, inbox=body.kind)
    session.commit()
    return Response(status_code=204)
