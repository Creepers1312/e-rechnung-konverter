"""Address book, archive search and ZIP download."""

from __future__ import annotations

import io
import shutil
import zipfile
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import customers
from app.db import Customer, get_engine
from app.export.pdf_render import render_invoice_pdf
from app.model import Invoice, Party
from app.worker import Folders, process_once

SAMPLE_IDOC = Path(__file__).parent / "fixtures" / "invoic02_sample.xml"


def _db() -> Session:
    return sessionmaker(get_engine())()


def _export(client: TestClient, invoice: Invoice) -> int:
    r = client.post("/api/export/zugferd", data={"invoice": invoice.model_dump_json(), "source": "pdf"})
    assert r.status_code == 200, r.text
    return int(r.headers["X-Document-Id"])


# --- address book -------------------------------------------------------------


def test_address_book_fills_missing_buyer_data_without_overwriting(invoice: Invoice) -> None:
    invoice.buyer.name = "Adressbuch Test AG"
    invoice.buyer.erp_id = "000777"
    invoice.buyer.electronic_address = "rechnung@adressbuch.example"
    invoice.buyer_reference = "991-12345-67"
    with _db() as s:
        customers.remember(s, invoice, "tester")
        s.commit()

        # next invoice for the same customer number: e-address and Leitweg-ID are missing
        nxt = Invoice(buyer=Party(name="Adressbuch Test AG (neu)", erp_id="777"))
        filled = customers.fill(s, nxt)
    assert nxt.buyer.electronic_address == "rechnung@adressbuch.example"
    assert nxt.buyer_reference == "991-12345-67"
    assert nxt.buyer.address.city == invoice.buyer.address.city
    assert nxt.buyer.name == "Adressbuch Test AG (neu)"  # existing value kept
    assert "E-Rechnungs-Adresse" in filled and "Name" not in filled


def test_address_book_matches_by_name_case_insensitively(invoice: Invoice) -> None:
    invoice.buyer.name = "Name  Match GmbH"
    invoice.buyer.erp_id = ""
    invoice.buyer.vat_id = "DE111111111"
    with _db() as s:
        customers.remember(s, invoice, "tester")
        s.commit()
        nxt = Invoice(buyer=Party(name="name match gmbh"))
        customers.fill(s, nxt)
        assert nxt.buyer.vat_id == "DE111111111"
        assert customers.fill(s, Invoice(buyer=Party(name="Unbekannt KG"))) == []


def test_export_remembers_buyer_and_extract_uses_it(client: TestClient, invoice: Invoice) -> None:
    invoice.buyer.name = "Kunde AG"
    invoice.buyer.electronic_address = "eingang@kunde.example"
    _export(client, invoice)

    # The sample PDF has no e-invoice address for "Kunde AG"; the address book supplies it.
    r = client.post(
        "/api/extract",
        files={
            "file": (
                "r.pdf",
                render_invoice_pdf(
                    invoice.model_copy(update={"buyer": invoice.buyer.model_copy(update={"electronic_address": ""})})
                ),
                "application/pdf",
            )
        },
    )
    body = r.json()
    assert body["invoice"]["buyer"]["electronic_address"] == "eingang@kunde.example"
    assert any("Adressbuch" in n for n in body["notes"])

    hits = client.get("/api/customers", params={"q": "kunde"}).json()
    assert any(h["name"] == "Kunde AG" for h in hits)
    assert client.get("/api/customers", params={"q": "100%_"}).status_code == 200  # wildcards are escaped


def test_worker_fills_from_address_book(tmp_path: Path) -> None:
    # remember the IDoc's buyer with an e-invoice address, then let the worker produce XRechnung
    with _db() as s:
        s.query(Customer).filter(Customer.lookup_key == "erp:10042").delete()
        party = Party(name="Kunde AG Einkauf", erp_id="0000010042", electronic_address="sap@kunde.example")
        customers.remember(s, Invoice(buyer=party, buyer_reference="04011000-1-23"), "tester")
        s.commit()
        folders = Folders.under(tmp_path / "idoc")
        shutil.copy(SAMPLE_IDOC, folders.inbox / "x.xml")
        ok, failed = process_once(folders, "xrechnung", s, now=1e12)
    assert (ok, failed) == (1, 0), list(folders.error.iterdir())
    xml = (folders.outbox / "0090001234.xml").read_text(encoding="utf-8")
    assert "sap@kunde.example" in xml and "04011000-1-23" in xml


# --- archive search + zip -------------------------------------------------------


def test_archive_search_filters_and_pagination(client: TestClient, invoice: Invoice) -> None:
    ids = []
    for n in range(3):
        invoice.number = f"SUCHE-{n}"
        invoice.buyer.name = "Filter GmbH" if n < 2 else "Anderer Kunde"
        ids.append(_export(client, invoice))

    page = client.get("/api/documents", params={"q": "suche-", "limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert page["items"][0]["invoice_number"] == "SUCHE-2"  # newest first
    page2 = client.get("/api/documents", params={"q": "suche-", "limit": 2, "offset": 2}).json()
    assert [d["invoice_number"] for d in page2["items"]] == ["SUCHE-0"]

    by_buyer = client.get("/api/documents", params={"q": "filter gmbh"}).json()
    assert {d["invoice_number"] for d in by_buyer["items"]} >= {"SUCHE-0", "SUCHE-1"}
    assert client.get("/api/documents", params={"q": "suche-", "profile": "xrechnung"}).json()["total"] == 0
    assert client.get("/api/documents", params={"q": "suche-", "source": "pdf"}).json()["total"] == 3

    today = date.today().isoformat()
    assert client.get("/api/documents", params={"q": "suche-", "date_from": today}).json()["total"] == 3
    assert client.get("/api/documents", params={"q": "suche-", "date_to": "2000-01-01"}).json()["total"] == 0
    assert client.get("/api/documents", params={"profile": "pdf"}).status_code == 422


def test_zip_download(client: TestClient, invoice: Invoice) -> None:
    invoice.number = "ZIP-1"
    a = _export(client, invoice)
    b = _export(client, invoice)  # same filename twice
    r = client.post("/api/documents/zip", json={"ids": [a, b, 999999]})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert sorted(names) == ["ZIP-1.pdf", "ZIP-1_1.pdf"]
    assert client.post("/api/documents/zip", json={"ids": []}).status_code == 422


# --- SAP IDoc inbox status ------------------------------------------------------


def test_idoc_status_and_retry(admin_client: TestClient, client: TestClient, tmp_path: Path, monkeypatch) -> None:
    from app.worker import report_status

    monkeypatch.setenv("IDOC_DIR", str(tmp_path / "idoc"))
    folders = Folders.under(tmp_path / "idoc")
    shutil.copy(SAMPLE_IDOC, folders.inbox / "gut.xml")
    (folders.inbox / "kaputt.xml").write_text("<nicht-sap/>", encoding="utf-8")
    with _db() as s:
        ok, failed = process_once(folders, "zugferd", s, now=1e12)
        report_status(s, True, "zugferd", 10, ok, failed)

    st = admin_client.get("/api/admin/idoc/status").json()
    assert st["folder_available"] is True and st["worker_alive"] is True
    assert st["counts"] == {"inbox": 0, "outbox": 1, "processed": 1, "error": 1}
    assert st["worker"]["converted_total"] >= 1 and st["worker"]["failed_total"] >= 1
    [err] = st["errors"]
    assert err["name"] == "kaputt.xml" and "E1EDK01" in err["reason"]

    assert client.get("/api/admin/idoc/status").status_code == 403  # users may not
    assert admin_client.post("/api/admin/idoc/retry", json={"name": "../../etc/passwd"}).status_code == 400
    assert admin_client.post("/api/admin/idoc/retry", json={"name": "fehlt.xml"}).status_code == 404

    assert admin_client.post("/api/admin/idoc/retry", json={"name": "kaputt.xml"}).status_code == 204
    assert (folders.inbox / "kaputt.xml").exists()
    assert not (folders.error / "kaputt.xml.error.txt").exists()
    assert admin_client.get("/api/admin/idoc/status").json()["counts"]["inbox"] == 1


def test_idoc_status_without_folder(admin_client: TestClient, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("IDOC_DIR", str(tmp_path / "nicht-eingebunden"))
    st = admin_client.get("/api/admin/idoc/status").json()
    assert st["folder_available"] is False and st["errors"] == []


def test_address_book_search_and_name_match_ignore_case_for_erp_keyed_customers() -> None:
    """Regression: customers stored under their ERP number were not found by (differently cased) name.
    SQLite's LIKE is case-insensitive, PostgreSQL's is not – so compare lower-cased explicitly."""
    with _db() as s:
        party = Party(name="Stadtwerke Großstadt GmbH", erp_id="55501", electronic_address="ein@sw.example")
        customers.remember(s, Invoice(buyer=party), "tester")
        s.commit()
        assert any(c.name == "Stadtwerke Großstadt GmbH" for c in customers.search(s, "STADTWERKE"))
        nxt = Invoice(buyer=Party(name="stadtwerke großstadt gmbh"))  # PDF without customer number
        assert "E-Rechnungs-Adresse" in customers.fill(s, nxt)
        assert nxt.buyer.electronic_address == "ein@sw.example"
