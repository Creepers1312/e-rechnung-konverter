"""Auth endpoints (/api/auth/*) and admin-only endpoints (/api/admin/*)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth
from .auth import Role, current_session, current_user, require_admin
from .company import Company
from .db import AuditEntry, LoginSession, User, audit, get_session, put_setting
from .settings import IdocSettings, get_company, get_idoc_settings

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
    user = User(username=username, password_hash=auth.hash_password(body.password), role=body.role,
                must_change_password=True)
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
    return Settings(company=get_company(session), idoc=get_idoc_settings(session))


@router.put("/admin/settings", response_model=Settings)
def write_settings(body: Settings, admin: User = Depends(require_admin), session: Session = Depends(get_session)) -> Settings:
    put_setting(session, "company", body.company.model_dump(mode="json"), admin.username)
    put_setting(session, "idoc", body.idoc.model_dump(mode="json"), admin.username)
    audit(session, admin.username, "settings_changed", idoc=body.idoc.model_dump(mode="json"),
          company=body.company.seller.name)
    session.commit()
    return body


# --- audit log (admin) -------------------------------------------------------


@router.get("/admin/audit", response_model=list[AuditInfo])
def read_audit(
    limit: int = Query(200, ge=1, le=1000),
    action: Literal["login", "login_failed", "export", "download", "user_created", "user_updated",
                    "password_changed", "password_reset", "settings_changed"] | None = None,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[AuditEntry]:
    stmt = select(AuditEntry).order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditEntry.action == action)
    return list(session.scalars(stmt))
