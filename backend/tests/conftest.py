from __future__ import annotations

import os
import tempfile

# Tests use a throwaway SQLite database (set before the app is imported).
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='erechnung-test-')}/test.db"

from datetime import date
from decimal import Decimal

import pytest

from app.company import Company, load_company
from app.model import Address, Contact, Invoice, LineItem, Party


@pytest.fixture
def company() -> Company:
    return load_company()


@pytest.fixture
def invoice(company: Company) -> Invoice:
    return Invoice(
        number="RE-2026-0042",
        issue_date=date(2026, 11, 4),
        delivery_date=date(2026, 10, 31),
        buyer_reference="04011000-12345-34",
        order_reference="PO-778",
        seller=company.seller.model_copy(deep=True),
        buyer=Party(
            name="Kunde AG",
            address=Address(line1="Hauptstraße 5", postcode="10115", city="Berlin"),
            electronic_address="eingang@kunde.example",
            contact=Contact(),
            erp_id="10042",
        ),
        lines=[
            LineItem(id="1", name="Beratung", quantity=Decimal("8"), unit_code="HUR", unit_price=Decimal("95.00")),
            LineItem(id="2", name="Lizenz Software", quantity=Decimal("2"), unit_price=Decimal("125.00")),
            LineItem(id="3", name="Fachbuch", quantity=Decimal("1"), unit_price=Decimal("39.90"), vat_rate=Decimal("7")),
        ],
        payment=company.payment.model_copy(update={"due_date": date(2026, 11, 18)}),
    )


# --- logged-in API clients ---------------------------------------------------

import itertools  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.auth import hash_password, throttle  # noqa: E402
from app.db import User, get_engine  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "richtig-langes-passwort"
_ids = itertools.count(1)


def make_user(role: str = "user", must_change_password: bool = False, password: str = PASSWORD) -> str:
    username = f"{role}{next(_ids)}"
    with sessionmaker(get_engine())() as session:
        session.add(User(username=username, password_hash=hash_password(password), role=role,
                         must_change_password=must_change_password))
        session.commit()
    return username


def new_client() -> TestClient:
    c = TestClient(app)
    c.headers["X-Requested-With"] = "erechnung"
    return c


def login_as(username: str, password: str = PASSWORD) -> TestClient:
    c = new_client()
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(autouse=True)
def _reset_throttle() -> None:
    throttle._failures.clear()


@pytest.fixture
def client() -> TestClient:
    """Logged in with role "user"."""
    return login_as(make_user("user"))


@pytest.fixture
def admin_client() -> TestClient:
    return login_as(make_user("admin"))
