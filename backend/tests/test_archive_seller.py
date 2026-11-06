"""Own company (issuer) in the archive: column, backfill of old entries, filter."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import Base, Document, _upgrade, archive, get_engine
from app.model import Invoice


def test_old_archive_entries_get_their_company(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:  # database of an older version: no seller_name column
        conn.execute(text("DROP INDEX ix_documents_seller_name"))
        conn.execute(text("ALTER TABLE documents DROP COLUMN seller_name"))
        conn.execute(
            text(
                "INSERT INTO documents (created_at, source, source_name, profile, invoice_number, buyer_name,"
                " grand_total, currency, invoice, filename, media_type, content, created_by)"
                " VALUES ('2026-11-05 10:00:00', 'pdf', 'a.pdf', 'zugferd', 'RE-1', 'Kunde', 10, 'EUR', :inv,"
                " 'a.pdf', 'application/pdf', x'00', 'anna')"
            ),
            {"inv": json.dumps({"seller": {"name": "Alte Firma GmbH"}})},
        )
    with engine.begin() as conn:
        _upgrade(conn)
        assert conn.execute(text("SELECT seller_name FROM documents")).scalar() == "Alte Firma GmbH"


def test_archive_shows_and_filters_by_company(client: TestClient, invoice: Invoice) -> None:
    with sessionmaker(get_engine(), expire_on_commit=False)() as s:
        for seller, number in (("Firma Nord GmbH", "NORD-1"), ("Firma Süd GmbH", "SUED-1")):
            inv = invoice.model_copy(deep=True)
            inv.seller.name, inv.number = seller, number
            archive(s, inv, profile="zugferd", source="pdf", source_name="x.pdf", filename="x.pdf",
                    media_type="application/pdf", content=b"%PDF")  # fmt: skip
        s.commit()
        assert s.query(Document).filter_by(invoice_number="NORD-1").one().seller_name == "Firma Nord GmbH"

    page = client.get("/api/documents", params={"seller": "Firma Süd GmbH"}).json()
    assert [d["invoice_number"] for d in page["items"]] == ["SUED-1"]
    assert page["items"][0]["seller_name"] == "Firma Süd GmbH"
    found = client.get("/api/documents", params={"q": "nord"}).json()["items"]
    assert any(d["invoice_number"] == "NORD-1" for d in found)
