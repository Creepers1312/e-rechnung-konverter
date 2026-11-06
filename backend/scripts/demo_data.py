"""Fill an EMPTY database with invented demo data (screenshots, trying the app out).

    DATABASE_URL=sqlite:///./demo.db python scripts/demo_data.py
    DATABASE_URL=sqlite:///./demo.db python scripts/demo_data.py --touch   # worker "läuft" (screenshots)

Creates the users "admin" and "anna" (password: demo-passwort-2026), about a
year of archived e-invoices, a few review items and an SAP customer master.
All companies, people and numbers are made up. Refuses to run on a database
that already contains documents, so it can never mix into real data.
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import customer_import, review  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.company import load_company  # noqa: E402
from app.db import Document, User, get_engine, get_setting, put_setting  # noqa: E402
from app.export.pdf_render import render_invoice_pdf  # noqa: E402
from app.model import Address, Invoice, LineItem, Party, compute_totals  # noqa: E402
from app.settings import assign_ids  # noqa: E402
from app.validation import validate  # noqa: E402
from app.worker import IMPORT_STATUS_KEY, STATUS_KEY  # noqa: E402

PASSWORD = "demo-passwort-2026"
BUYERS = [
    ("710001", "Beispiel Chemie AG", "Werkstraße 7", "84508", "Burgkirchen", "eingang@beispiel-chemie.example"),
    ("710002", "Muster Metall GmbH", "Industriestraße 1", "70173", "Stuttgart", "rechnung@muster-metall.example"),
    ("710003", "Stadtwerke Musterstadt", "Am Markt 1", "12345", "Musterstadt", "rechnung@sw-musterstadt.example"),
    ("710004", "Nordlicht Logistik KG", "Hafenweg 12", "20457", "Hamburg", "ap@nordlicht.example"),
    ("710005", "Alpen Kunststoff GmbH", "Seestraße 3", "83022", "Rosenheim", ""),
    ("710006", "Rheinblick Bau GmbH", "Uferstraße 44", "50667", "Köln", "invoice@rheinblick.example"),
    ("710007", "Fichtel Papier AG", "Mühlweg 9", "95632", "Wunsiedel", ""),
    ("710008", "Havel Energie GmbH", "Kraftwerkstraße 2", "14467", "Potsdam", "eingang@havel-energie.example"),
]
WEIGHTS = [8, 6, 3, 4, 2, 3, 1, 2]
PRODUCTS = [
    ("Granulat PE-HD", "KGM", "2.85"),
    ("Kohlefaser-Rohr 40 mm", "MTR", "18.40"),
    ("Servicepauschale", "C62", "120.00"),
    ("Transport", "C62", "85.00"),
    ("Spezialharz", "KGM", "11.20"),
]


def _invoice(seller: Party, n: int, buyer: tuple, day: date, rng: random.Random) -> Invoice:
    erp, name, street, plz, city, mail = buyer
    lines = []
    for i in range(rng.randint(1, 4)):
        prod, unit, price = rng.choice(PRODUCTS)
        qty = Decimal(rng.choice([1, 2, 5, 10, 25, 40, 120, 250]))
        lines.append(LineItem(id=str(i + 1), name=prod, quantity=qty, unit_code=unit, unit_price=Decimal(price)))
    return Invoice(
        number=f"RE-{day.year}-{n:05d}",
        issue_date=day,
        delivery_date=day - timedelta(days=2),
        order_reference=f"PO-{rng.randint(1000, 9999)}",
        seller=seller.model_copy(deep=True),
        buyer=Party(
            name=name, erp_id=erp, electronic_address=mail, address=Address(line1=street, postcode=plz, city=city)
        ),
        lines=lines,
    )


def touch(db) -> None:
    """--touch: refresh the worker heartbeat, so the start page shows the automation as running."""
    status = get_setting(db, STATUS_KEY) or {}
    status["last_round_at"] = datetime.now(UTC).isoformat()
    put_setting(db, STATUS_KEY, status, "worker")
    db.commit()


def main() -> None:
    db = sessionmaker(get_engine(), expire_on_commit=False)()
    if "--touch" in sys.argv:
        return touch(db)
    if db.scalar(select(func.count()).select_from(Document)):
        sys.exit("Die Datenbank enthält bereits Rechnungen – Demo-Daten nur in eine leere Datenbank.")
    rng = random.Random(42)
    company = load_company()
    seller = company.seller
    # a group of three own companies (invented); the first one issues the demo invoices
    others = [
        ("Beispiel Carbon GmbH", "DE222222222", "DE02120300000000202051", ["Werk Süd"]),
        ("Beispiel Logistik GmbH", "DE333333333", "DE02500105170137075030", []),
    ]
    companies = [company.model_copy(update={"id": "firma-1"}, deep=True)]
    for name, vat, iban, keywords in others:
        c = company.model_copy(deep=True)
        c.id, c.keywords = "", keywords
        c.seller.name, c.seller.vat_id, c.payment.iban, c.payment.account_holder = name, vat, iban, name
        companies.append(c)
    put_setting(db, "companies", {"items": [c.model_dump(mode="json") for c in assign_ids(companies)]}, "admin")

    for username, role in (("admin", "admin"), ("anna", "user")):
        if not db.scalar(select(User).where(User.username == username)):
            pw = hash_password(PASSWORD)
            db.add(User(username=username, password_hash=pw, role=role, must_change_password=False))

    csv = "KUNNR;NAME1;STRAS;PSTLZ;ORT01;LAND1;SMTP_ADDR\n" + "".join(
        f"0000{b[0]};{b[1]};{b[2]};{b[3]};{b[4]};DE;{b[5]}\n" for b in BUYERS
    )
    report = customer_import.import_customers(db, csv.encode(), "kundenstamm_demo.csv")
    put_setting(db, IMPORT_STATUS_KEY, report.model_dump(mode="json"), "worker")

    now = datetime.now(UTC)
    n = 0
    for back in range(365, -1, -1):
        day = (now - timedelta(days=back)).date()
        if day.weekday() >= 5:
            continue
        growth = 1 + (365 - back) / 220  # business grows over the year
        for _ in range(rng.choices([0, 1, 2, 3, 4], [2, 3, 3, 2, 1])[0] + int(growth)):
            n += 1
            inv = _invoice(seller, n, rng.choices(BUYERS, WEIGHTS)[0], day, rng)
            auto = rng.random() < min(0.45 + (365 - back) / 700, 0.9)  # automation improves
            source = "idoc" if auto and rng.random() < 0.7 else "pdf" if auto else rng.choice(["pdf", "manual"])
            created = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=rng.uniform(7, 17))
            db.add(
                Document(
                    created_at=min(created, now),
                    source=source,
                    source_name=f"{inv.number}.{'xml' if source == 'idoc' else 'pdf'}",
                    profile="xrechnung" if inv.buyer.name.startswith("Stadtwerke") else "zugferd",
                    invoice_number=inv.number,
                    buyer_name=inv.buyer.name,
                    grand_total=compute_totals(inv).grand_total,
                    currency="EUR",
                    invoice=inv.model_dump(mode="json"),
                    filename=f"{inv.number}.pdf",
                    media_type="application/pdf",
                    content=b"%PDF-1.4 demo",
                    created_by="worker" if auto else rng.choice(["anna", "admin"]),
                )
            )

    # open review items: one with missing buyer address (scan), one with a missing e-invoice address
    for i, buyer in enumerate([BUYERS[4], BUYERS[6], BUYERS[1]]):
        n += 1
        inv = _invoice(seller, n, buyer, now.date(), rng)
        if i == 0:
            inv.buyer.address = Address()
        if i == 2:
            inv.buyer.name = ""
        issues = validate(inv, "zugferd")
        review.create(
            db,
            inv,
            source="pdf",
            source_name=f"scan_{now:%Y%m%d}_{i + 1:03d}.pdf",
            profile="zugferd",
            issues=issues,
            pdf=render_invoice_pdf(inv),
            warnings=["Gescanntes PDF – Text per Texterkennung (OCR) gelesen."],
        )

    # finished reviews of the last weeks (for the "time to release" figure)
    for k in range(14):
        n += 1
        created = now - timedelta(days=rng.uniform(1, 28))
        inv = _invoice(seller, n, rng.choice(BUYERS), created.date(), rng)
        item = review.create(db, inv, source="pdf", source_name=f"scan_{k:03d}.pdf", profile="zugferd", issues=[])
        item.created_at = created
        item.status = "erledigt" if k % 5 else "verworfen"
        item.resolved_by = "anna"
        db.flush()
        item.updated_at = created + timedelta(hours=rng.uniform(0.3, 9))

    put_setting(
        db,
        STATUS_KEY,
        {
            "last_round_at": now.isoformat(),
            "enabled": True,
            "pdf_enabled": True,
            "profile": "zugferd",
            "interval_seconds": 10,
            "converted_total": n,
            "failed_total": 2,
            "review_total": 41,
            "last_converted_at": now.isoformat(),
        },
        "worker",
    )
    db.commit()
    print(f"Demo-Daten angelegt: {n} Rechnungen. Anmeldung: admin / anna, Passwort {PASSWORD}")


if __name__ == "__main__":
    main()
