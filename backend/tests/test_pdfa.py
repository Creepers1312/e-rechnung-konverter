"""PDF/A-3 for ZUGFeRD: conversion before embedding, veraPDF check of the finished file.

The PDF service is simulated here; the real Ghostscript/veraPDF chain is tested in ocr/tests.
"""

from __future__ import annotations

import io
from pathlib import Path

import facturx
import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app import pdfa
from app.convert import ORIGINAL_NOT_PDFA, NotExportable, convert
from app.export.pdf_render import render_invoice_pdf
from app.model import Invoice
from app.worker import Folders, process_pdfs
from tests.test_automation import _db, _items

CONVERTED_SIZE = (321, 654)  # the fake conversion returns a page of this size


def _converted_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=CONVERTED_SIZE)
    c.drawString(20, 20, "PDF/A")
    c.showPage()
    c.save()
    return buf.getvalue()


NOT_COMPLIANT = {
    "compliant": False,
    "profile": "PDF/A-3b validation profile",
    "failed_rules": [
        {"clause": "6.2.11.4.1", "test": 1, "description": "Fonts shall be embedded.", "failed_checks": 2}
    ],
}


class FakeService:
    """Stands in for httpx.post against the PDF service."""

    def __init__(self, verify: dict | None = None, down: bool = False) -> None:
        self.calls: list[str] = []
        self.verified: list[bytes] = []
        self.verify_result = verify or {"compliant": True, "profile": "PDF/A-3b", "failed_rules": []}
        self.down = down

    def __call__(self, url: str, files: dict, data: dict | None = None, timeout: float = 0) -> httpx.Response:
        path = url.rsplit("/", 1)[-1]
        self.calls.append(path)
        request = httpx.Request("POST", url)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        pdf = files["file"][1]
        if path == "pdfa":
            return httpx.Response(200, content=_converted_pdf(), request=request)
        assert path == "verify" and data == {"flavour": "3b"}
        self.verified.append(pdf)
        return httpx.Response(200, json=self.verify_result, request=request)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setenv("OCR_URL", "http://ocr:8000")
    monkeypatch.delenv("PDFA_CHECK", raising=False)
    monkeypatch.setattr(pdfa.httpx, "post", fake)
    return fake


def test_enabled_only_with_the_pdf_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCR_URL", raising=False)
    assert not pdfa.enabled()
    monkeypatch.setenv("OCR_URL", "http://ocr:8000")
    assert pdfa.enabled()
    monkeypatch.setenv("PDFA_CHECK", "off")
    assert not pdfa.enabled()


def test_converts_before_embedding_and_checks_the_finished_file(service: FakeService, invoice: Invoice) -> None:
    result = convert(invoice, "zugferd")
    assert service.calls == ["pdfa", "verify"]
    page = PdfReader(io.BytesIO(result.content)).pages[0]
    assert (page.mediabox.width, page.mediabox.height) == CONVERTED_SIZE  # XML went into the converted PDF ...
    assert service.verified == [result.content]  # ... and exactly the released file was checked
    filename, xml = facturx.get_xml_from_pdf(result.content, check_xsd=False)
    assert filename == "factur-x.xml" and invoice.number.encode() in xml


def test_xrechnung_needs_no_pdf(service: FakeService, invoice: Invoice) -> None:
    convert(invoice, "xrechnung")
    assert service.calls == []


def test_non_compliant_file_is_not_released(service: FakeService, invoice: Invoice) -> None:
    service.verify_result = NOT_COMPLIANT
    with pytest.raises(NotExportable) as exc:
        convert(invoice, "zugferd")
    [issue] = exc.value.issues
    assert issue.code == "PDFA-6.2.11.4.1-1"
    assert issue.message == "PDF/A-Prüfung (ISO 19005-3, 6.2.11.4.1): Fonts shall be embedded."


def test_original_that_cannot_become_pdfa_suggests_xrechnung(service: FakeService, invoice: Invoice) -> None:
    service.verify_result = NOT_COMPLIANT
    with pytest.raises(NotExportable) as exc:
        convert(invoice, "zugferd", render_invoice_pdf(invoice))
    assert exc.value.issues[-1].message == ORIGINAL_NOT_PDFA


def test_service_down_releases_nothing(service: FakeService, invoice: Invoice) -> None:
    service.down = True
    with pytest.raises(NotExportable) as exc:
        convert(invoice, "zugferd")
    [issue] = exc.value.issues
    assert issue.code == "PDFA"
    assert issue.message.startswith("PDF/A-Umwandlung: PDF-Dienst nicht erreichbar")


def test_export_endpoint_answers_422(service: FakeService, client: TestClient, invoice: Invoice) -> None:
    service.verify_result = NOT_COMPLIANT
    r = client.post("/api/export/zugferd", data={"invoice": invoice.model_dump_json()})
    assert r.status_code == 422
    assert [i["code"] for i in r.json()["detail"]["issues"]] == ["PDFA-6.2.11.4.1-1"]


def test_worker_puts_non_compliant_pdf_into_review(service: FakeService, invoice: Invoice, tmp_path: Path) -> None:
    service.verify_result = NOT_COMPLIANT
    folders = Folders.under(tmp_path / "pdf")
    invoice.number = "PDFA-1"
    (folders.inbox / "pdfa-1.pdf").write_bytes(render_invoice_pdf(invoice))
    with _db() as s:
        r = process_pdfs(folders, "zugferd", s, now=1e12)
        assert (r.converted, r.review, r.failed) == (0, 1, 0)
        [item] = _items(s, "pdfa-1.pdf")
        assert [i["code"] for i in item.issues] == ["PDFA-6.2.11.4.1-1", "PDFA"]
    assert list(folders.outbox.iterdir()) == []
    assert list(folders.error.iterdir()) == []
