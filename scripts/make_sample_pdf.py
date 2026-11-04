"""Write samples/beispielrechnung.pdf (a plain PDF invoice) for trying out the app.

Run from the repository root:  backend/.venv/bin/python scripts/make_sample_pdf.py
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.company import load_company  # noqa: E402
from app.export.pdf_render import render_invoice_pdf  # noqa: E402
from app.model import Address, Invoice, LineItem, Party  # noqa: E402

company = load_company()
invoice = Invoice(
    number="RE-2026-0042",
    issue_date=date(2026, 11, 4),
    delivery_date=date(2026, 10, 31),
    order_reference="PO-778",
    seller=company.seller,
    buyer=Party(
        name="Kunde AG",
        address=Address(line1="Hauptstraße 5", line2="z. Hd. Einkauf", postcode="10115", city="Berlin"),
        erp_id="10042",
    ),
    lines=[
        LineItem(id="1", name="Beratung", quantity=Decimal("8"), unit_code="HUR", unit_price=Decimal("95")),
        LineItem(id="2", name="Lizenz Software", quantity=Decimal("2"), unit_price=Decimal("125")),
        LineItem(id="3", name="Fachbuch", quantity=Decimal("1"), unit_price=Decimal("39.90"), vat_rate=Decimal("7")),
    ],
    payment=company.payment.model_copy(update={"due_date": date(2026, 11, 18)}),
)
out = ROOT / "samples" / "beispielrechnung.pdf"
out.parent.mkdir(exist_ok=True)
out.write_bytes(render_invoice_pdf(invoice))
print(f"wrote {out}")
