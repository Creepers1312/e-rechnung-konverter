from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.auth import COOKIE_NAME, MAX_FAILURES
from app.db import AuditEntry, LoginSession, User, get_engine

from .conftest import PASSWORD, login_as, make_user, new_client

PROTECTED = [
    ("get", "/api/company"),
    ("get", "/api/documents"),
    ("post", "/api/validate"),
    ("post", "/api/extract"),
    ("post", "/api/export/xrechnung"),
    ("get", "/api/admin/users"),
    ("get", "/api/admin/settings"),
]
ADMIN_ONLY = [
    ("get", "/api/admin/users"),
    ("post", "/api/admin/users"),
    ("patch", "/api/admin/users/1"),
    ("get", "/api/admin/settings"),
    ("put", "/api/admin/settings"),
    ("get", "/api/admin/audit"),
]


def _db():
    return sessionmaker(get_engine())()


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_everything_requires_login(method: str, path: str) -> None:
    r = getattr(new_client(), method)(path)
    assert r.status_code == 401


def test_health_is_public() -> None:
    assert new_client().get("/api/health").status_code == 200


@pytest.mark.parametrize(("method", "path"), ADMIN_ONLY)
def test_user_role_cannot_use_admin_endpoints(client: TestClient, method: str, path: str) -> None:
    kwargs = {"json": {}} if method in ("post", "put", "patch") else {}
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_login_sets_hardened_cookie_and_logout_ends_session() -> None:
    c = new_client()
    name = make_user()
    r = c.post("/api/auth/login", json={"username": name, "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    assert c.get("/api/auth/me").json()["username"] == name

    assert c.post("/api/auth/logout").status_code == 204
    c.cookies.clear()
    assert c.get("/api/documents").status_code == 401


def test_session_token_is_stored_hashed() -> None:
    c = login_as(make_user())
    token = c.cookies.get(COOKIE_NAME)
    with _db() as s:
        hashes = set(s.scalars(select(LoginSession.token_hash)))
    assert token not in hashes


def test_wrong_password_and_unknown_user_look_the_same() -> None:
    name = make_user()
    a = new_client().post("/api/auth/login", json={"username": name, "password": "falsch-falsch-falsch"})
    b = new_client().post("/api/auth/login", json={"username": "gibtsnicht", "password": "falsch-falsch-falsch"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_repeated_failures() -> None:
    name = make_user()
    c = new_client()
    for _ in range(MAX_FAILURES):
        assert c.post("/api/auth/login", json={"username": name, "password": "falsch-falsch"}).status_code == 401
    # even the right password is refused while locked
    assert c.post("/api/auth/login", json={"username": name, "password": PASSWORD}).status_code == 429


def test_state_changing_requests_need_csrf_header(client: TestClient) -> None:
    del client.headers["X-Requested-With"]
    assert client.post("/api/validate", json={}).status_code == 403
    assert client.get("/api/documents").status_code == 200  # reads are fine


def test_idle_session_expires(client: TestClient) -> None:
    token_hash_rows = None
    with _db() as s:
        token_hash_rows = list(s.scalars(select(LoginSession)))
        for row in token_hash_rows:
            row.last_seen_at = datetime.now(UTC) - timedelta(hours=5)
        s.commit()
    assert client.get("/api/documents").status_code == 401


def test_initial_password_must_be_changed_first() -> None:
    name = make_user(must_change_password=True)
    c = login_as(name)
    assert c.get("/api/auth/me").json()["must_change_password"] is True
    assert c.get("/api/documents").status_code == 403

    bad = c.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "kurz"})
    assert bad.status_code == 400
    ok = c.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "ein-neues-langes-pw"})
    assert ok.status_code == 204
    assert c.get("/api/documents").status_code == 200


def test_admin_manages_users(admin_client: TestClient) -> None:
    r = admin_client.post(
        "/api/admin/users", json={"username": "erika", "role": "user", "password": "start-passwort-123"}
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["must_change_password"] is True
    assert (
        admin_client.post("/api/admin/users", json={"username": "erika", "password": "start-passwort-123"}).status_code
        == 409
    )

    erika = login_as("erika", "start-passwort-123")
    assert erika.get("/api/auth/me").status_code == 200

    # deactivating ends her sessions immediately
    assert admin_client.patch(f"/api/admin/users/{uid}", json={"active": False}).json()["active"] is False
    assert erika.get("/api/auth/me").status_code == 401
    assert (
        new_client().post("/api/auth/login", json={"username": "erika", "password": "start-passwort-123"}).status_code
        == 401
    )

    # promote + reactivate + reset password
    r = admin_client.patch(
        f"/api/admin/users/{uid}", json={"active": True, "role": "admin", "password": "noch-ein-passwort"}
    )
    assert r.json()["role"] == "admin" and r.json()["must_change_password"] is True


def test_last_admin_cannot_lock_themselves_out() -> None:
    with _db() as s:  # make sure no other active admins exist for this test
        for u in s.scalars(select(User).where(User.role == "admin")):
            u.active = False
        s.commit()
    name = make_user("admin")
    c = login_as(name)
    me = c.get("/api/auth/me").json()
    assert c.patch(f"/api/admin/users/{me['id']}", json={"role": "user"}).status_code == 400
    assert c.patch(f"/api/admin/users/{me['id']}", json={"active": False}).status_code == 400


def test_settings_change_company_data_used_by_extraction(admin_client: TestClient, client: TestClient) -> None:
    settings = admin_client.get("/api/admin/settings").json()
    settings["companies"][0]["seller"]["name"] = "Neue Firma GmbH"
    settings["idoc"] = {"enabled": False, "profile": "xrechnung", "interval_seconds": 30}
    r = admin_client.put("/api/admin/settings", json=settings)
    assert r.status_code == 200, r.text
    assert client.get("/api/company").json()["seller"]["name"] == "Neue Firma GmbH"
    assert admin_client.get("/api/admin/settings").json()["idoc"]["enabled"] is False

    bad = dict(settings, idoc={"enabled": True, "profile": "pdf", "interval_seconds": 1})
    assert admin_client.put("/api/admin/settings", json=bad).status_code == 422


def test_audit_log_records_who_did_what(admin_client: TestClient, client: TestClient) -> None:
    client.post("/api/auth/login", json={"username": "nobody", "password": "x" * 12})
    entries = admin_client.get("/api/admin/audit", params={"action": "login_failed"}).json()
    assert any(e["username"] == "nobody" for e in entries)
    with _db() as s:
        assert s.scalar(select(AuditEntry).where(AuditEntry.action == "login")) is not None
