from __future__ import annotations

from pathlib import Path

import facturx
from fastapi.testclient import TestClient

from app.export.pdf_render import render_invoice_pdf
from app.model import Invoice
SAMPLE_IDOC = Path(__file__).parent / "fixtures" / "invoic02_sample.xml"


def test_extract_then_validate_then_export(client: TestClient, invoice: Invoice) -> None:
    pdf = render_invoice_pdf(invoice)
    r = client.post("/api/extract", files={"file": ("r.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    extracted = body["invoice"]
    assert extracted["number"] == "RE-2026-0042"

    r = client.post("/api/validate", json={"invoice": extracted, "profile": "zugferd", "stated_totals": body["stated_totals"]})
    assert r.status_code == 200
    assert r.json()["totals"]["grand_total"] == "1244.59"
    assert not [i for i in r.json()["issues"] if i["severity"] == "error"]

    r = client.post("/api/export/zugferd", data={"invoice": Invoice.model_validate(extracted).model_dump_json()},
                    files={"file": ("r.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    _, xml = facturx.get_xml_from_pdf(r.content)
    assert b"RE-2026-0042" in xml

    # XRechnung additionally needs the buyer's e-address, which the PDF lacks:
    r = client.post("/api/export/xrechnung", json=extracted)
    assert r.status_code == 422
    extracted["buyer"]["electronic_address"] = "eingang@kunde.example"  # user fills it in the form
    r = client.post("/api/export/xrechnung", json=extracted)
    assert r.status_code == 200, r.text
    assert b"xrechnung_3.0" in r.content


def test_export_refuses_incomplete_invoice(client: TestClient, invoice: Invoice) -> None:
    invoice.number = ""
    r = client.post("/api/export/xrechnung", json=invoice.model_dump(mode="json"))
    assert r.status_code == 422
    assert any(i["code"] == "BR-02" for i in r.json()["detail"]["issues"])


def test_extract_rejects_non_pdf(client: TestClient) -> None:
    r = client.post("/api/extract", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 400


def test_idoc_import_and_zugferd_without_source_pdf(client: TestClient) -> None:
    r = client.post("/api/erp/sap-ecc/idoc", files={"file": ("i.xml", SAMPLE_IDOC.read_bytes(), "application/xml")})
    assert r.status_code == 200, r.text
    inv = r.json()
    r = client.post("/api/export/zugferd", data={"invoice": Invoice.model_validate(inv).model_dump_json()})
    assert r.status_code == 200, r.text
    _, xml = facturx.get_xml_from_pdf(r.content)
    assert b"0090001234" in xml


def test_exports_are_archived_and_downloadable(client: TestClient, invoice: Invoice) -> None:
    r = client.post(
        "/api/export/zugferd",
        data={"invoice": invoice.model_dump_json(), "source": "pdf", "source_name": "kunde.pdf"},
    )
    assert r.status_code == 200, r.text
    doc_id = int(r.headers["X-Document-Id"])

    listing = client.get("/api/documents").json()
    entry = next(d for d in listing if d["id"] == doc_id)
    assert entry["invoice_number"] == "RE-2026-0042"
    assert entry["source_name"] == "kunde.pdf"
    assert entry["grand_total"] == "1244.59"

    r = client.get(f"/api/documents/{doc_id}/download")
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"
    assert client.get("/api/documents/999999/download").status_code == 404
