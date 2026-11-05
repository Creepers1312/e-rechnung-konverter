"""PDF inbox, review queue ("Zu prüfen") and dashboard."""

from __future__ import annotations

import shutil
from pathlib import Path

import facturx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import ocr, review
from app.db import ReviewItem, get_engine
from app.export.pdf_render import render_invoice_pdf
from app.model import Invoice
from app.worker import Folders, process_idocs, process_pdfs

SAMPLE_IDOC = Path(__file__).parent / "fixtures" / "invoic02_sample.xml"


def _db() -> Session:
    return sessionmaker(get_engine(), expire_on_commit=False)()


def _items(session: Session, source_name: str) -> list[ReviewItem]:
    return [i for i in review.list_items(session, "alle") if i.source_name == source_name]


@pytest.fixture
def pdf_folders(tmp_path: Path) -> Folders:
    return Folders.under(tmp_path / "pdf")


def test_valid_pdf_is_converted_automatically(pdf_folders: Folders, invoice: Invoice) -> None:
    invoice.number = "AUTO-1"
    (pdf_folders.inbox / "auto-1.pdf").write_bytes(render_invoice_pdf(invoice))
    with _db() as s:
        r = process_pdfs(pdf_folders, "zugferd", s, now=1e12)
    assert (r.converted, r.review, r.failed) == (1, 0, 0)
    _, xml = facturx.get_xml_from_pdf((pdf_folders.outbox / "AUTO-1.pdf").read_bytes())
    assert b"AUTO-1" in xml
    assert (pdf_folders.processed / "auto-1.pdf").exists()


def test_pdf_with_errors_goes_to_review_queue(pdf_folders: Folders, invoice: Invoice) -> None:
    invoice.number = "REVIEW-1"
    invoice.buyer.name = ""  # buyer missing -> BR-07
    invoice.buyer.address.line1 = invoice.buyer.address.postcode = invoice.buyer.address.city = ""
    invoice.buyer.erp_id = ""  # otherwise the address book (earlier tests) would fill the buyer in
    data = render_invoice_pdf(invoice)
    (pdf_folders.inbox / "review-1.pdf").write_bytes(data)
    with _db() as s:
        r = process_pdfs(pdf_folders, "zugferd", s, now=1e12)
        assert (r.converted, r.review, r.failed) == (0, 1, 0)
        [item] = _items(s, "review-1.pdf")
        assert item.status == "offen" and item.invoice_number == "REVIEW-1"
        assert any(i["code"] == "BR-07" for i in item.issues)
        assert item.pdf == data  # the original is kept for the reviewer
    assert list(pdf_folders.outbox.iterdir()) == []
    assert (pdf_folders.processed / "review-1.pdf").exists()


def test_without_auto_export_every_pdf_is_reviewed(pdf_folders: Folders, invoice: Invoice) -> None:
    invoice.number = "MANUAL-1"
    (pdf_folders.inbox / "manual-1.pdf").write_bytes(render_invoice_pdf(invoice))
    with _db() as s:
        r = process_pdfs(pdf_folders, "zugferd", s, auto_export=False, now=1e12)
        assert (r.converted, r.review) == (0, 1)
        assert _items(s, "manual-1.pdf")[0].issues == []  # nothing wrong, just not automatic


def test_unreadable_files_go_to_error_folder(pdf_folders: Folders) -> None:
    (pdf_folders.inbox / "kaputt.pdf").write_bytes(b"not a pdf")
    with _db() as s:
        r = process_pdfs(pdf_folders, "zugferd", s, now=1e12)
    assert r.failed == 1
    assert "Keine PDF-Datei" in (pdf_folders.error / "kaputt.pdf.error.txt").read_text(encoding="utf-8")


def test_scanned_pdf_in_inbox_is_ocred(pdf_folders: Folders, invoice: Invoice, monkeypatch) -> None:
    from tests.test_recognition import _pdf

    invoice.number = "SCAN-1"
    text_version = render_invoice_pdf(invoice)
    monkeypatch.setenv("OCR_URL", "http://ocr:8000")
    monkeypatch.setattr(ocr, "ocr_pdf", lambda data, filename="": text_version)
    (pdf_folders.inbox / "scan-1.pdf").write_bytes(_pdf([]))  # image-like: no text layer
    with _db() as s:
        r = process_pdfs(pdf_folders, "zugferd", s, now=1e12)
    assert r.converted == 1
    assert (pdf_folders.outbox / "SCAN-1.pdf").exists()


def test_idoc_with_validation_errors_goes_to_review_not_error(tmp_path: Path) -> None:
    folders = Folders.under(tmp_path / "idoc")
    shutil.copy(SAMPLE_IDOC, folders.inbox / "leitweg-fehlt.xml")
    with _db() as s:
        s.query(ReviewItem).delete()
        s.commit()
        # XRechnung needs a Leitweg-ID and e-addresses the IDoc does not carry
        from app.db import Customer

        s.query(Customer).filter(Customer.lookup_key.like("erp:10042%")).delete()
        s.commit()
        r = process_idocs(folders, "xrechnung", s, now=1e12)
        assert (r.converted, r.review, r.failed) == (0, 1, 0)
        [item] = _items(s, "leitweg-fehlt.xml")
        assert item.source == "idoc" and item.pdf is None
        assert any(i["code"] == "BR-DE-15" for i in item.issues)
    assert list(folders.error.iterdir()) == []


# --- API ------------------------------------------------------------------------


def _review_item(invoice: Invoice, name: str = "api.pdf") -> int:
    invoice.buyer.electronic_address = ""
    with _db() as s:
        item = review.create(
            s,
            invoice,
            source="pdf",
            source_name=name,
            profile="zugferd",
            issues=[],
            pdf=render_invoice_pdf(invoice),
        )
        s.commit()
        return item.id


def test_review_api_open_export_and_resolve(client: TestClient, invoice: Invoice) -> None:
    invoice.number = "QUEUE-1"
    rid = _review_item(invoice)

    listing = client.get("/api/review").json()
    assert any(i["id"] == rid and i["invoice_number"] == "QUEUE-1" for i in listing)
    detail = client.get(f"/api/review/{rid}").json()
    assert detail["has_pdf"] is True and detail["invoice"]["number"] == "QUEUE-1"
    assert client.get(f"/api/review/{rid}/pdf").content[:4] == b"%PDF"
    assert client.get("/api/dashboard").json()["review_open"] >= 1

    # export without sending the PDF again: the stored original is used
    r = client.post(
        "/api/export/zugferd",
        data={"invoice": Invoice.model_validate(detail["invoice"]).model_dump_json(), "review_id": str(rid)},
    )
    assert r.status_code == 200, r.text
    done = client.get(f"/api/review/{rid}").json()
    assert done["status"] == "erledigt" and done["document_id"] == int(r.headers["X-Document-Id"])
    assert all(i["id"] != rid for i in client.get("/api/review").json())

    # a finished item cannot be exported or discarded again
    again = client.post("/api/export/xrechnung", params={"review_id": rid}, json=detail["invoice"])
    assert again.status_code == 409
    assert client.post(f"/api/review/{rid}/discard").status_code == 409


def test_review_discard(client: TestClient, invoice: Invoice) -> None:
    rid = _review_item(invoice, "verwerfen.pdf")
    assert client.post(f"/api/review/{rid}/discard").status_code == 204
    assert client.get(f"/api/review/{rid}").json()["status"] == "verworfen"
    assert client.get("/api/review/999999").status_code == 404


def test_dashboard_counts(client: TestClient) -> None:
    d = client.get("/api/dashboard").json()
    assert set(d) == {"review_open", "documents_today", "documents_total", "worker"}
    assert d["documents_total"] >= d["documents_today"] >= 0


def test_pdf_inbox_settings_roundtrip(admin_client: TestClient) -> None:
    settings = admin_client.get("/api/admin/settings").json()
    assert settings["pdf_inbox"] == {"enabled": True, "profile": "zugferd", "auto_export": True}
    settings["pdf_inbox"] = {"enabled": True, "profile": "xrechnung", "auto_export": False}
    assert admin_client.put("/api/admin/settings", json=settings).status_code == 200
    assert admin_client.get("/api/admin/settings").json()["pdf_inbox"]["auto_export"] is False
    st = admin_client.get("/api/admin/inbox/status").json()
    assert {i["kind"] for i in st["inboxes"]} == {"idoc", "pdf"}
