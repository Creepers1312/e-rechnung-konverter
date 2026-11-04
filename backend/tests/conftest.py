from __future__ import annotations

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
