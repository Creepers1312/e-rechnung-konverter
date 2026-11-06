"""When does a PDF go through text recognition? Per page, also with a scanner's text layer."""

from __future__ import annotations

import io

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app import ocr
from app.extraction.pdf_text import PdfText, text_quality

TEXT = ["Rechnungs-Nr. / Datum: 6000001234 / 03.11.2026", "Zu zahlender Gesamtbetrag 28.600,91 EUR"]


def _image_of(lines: list[str]):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 14)
    for i, line in enumerate(lines):
        c.drawString(60, 760 - i * 24, line)
    c.save()
    with pdfplumber.open(io.BytesIO(buf.getvalue())) as pdf:
        return pdf.pages[0].to_image(resolution=60).original


def _pdf(pages: list[dict]) -> bytes:
    """pages: {"scan": bool, "text": [lines drawn as real text]}"""
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    for p in pages:
        if p.get("scan"):
            c.drawImage(ImageReader(_image_of(TEXT)), 0, 0, *A4)
        c.setFont("Helvetica", 8)
        for i, line in enumerate(p.get("text", [])):
            c.drawString(40, 800 - i * 11, line)
        c.showPage()
    c.save()
    return out.getvalue()


DIGITAL = {"text": TEXT * 6}


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        ([DIGITAL], []),  # real text
        ([{"scan": True}], [1]),  # pure scan
        ([{"scan": True, "text": ["Scan 1/1  06.11.2026 08:15"]}], [1]),  # scanner stamp must not stop OCR
        ([{"scan": True, "text": ["R3chn~ng5 Nr ./ D@tum 6O0OO|234 ;; 03.1l.2O26 ¦¦ ~~ ^^ Ges@mtbetr@g"] * 4}], [1]),
        ([DIGITAL, {"scan": True}], [2]),  # digital invoice + scanned delivery note: only page 2
    ],
    ids=["digital", "scan", "scan+stamp", "scan+poor-scanner-ocr", "mixed"],
)
def test_scan_pages(pages: list[dict], expected: list[int]) -> None:
    assert PdfText.from_bytes(_pdf(pages)).scan_pages() == expected


def test_scan_with_good_text_layer_is_kept() -> None:
    """A searchable PDF whose text layer is fine needs no second recognition."""
    assert PdfText.from_bytes(_pdf([{"scan": True, "text": TEXT * 4}])).scan_pages() == []


def test_text_quality() -> None:
    assert text_quality("Rechnungs-Nr. / Datum: 6000001234 / 03.11.2026") > 0.75
    assert text_quality("R3chn~ng5 ¦¦ ~~ ^^ 6O0OO|234") < 0.5


def test_only_scanned_pages_are_sent(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setenv("OCR_URL", "http://ocr:8000")
    monkeypatch.setattr(ocr, "ocr_pdf", lambda data, filename="", pages="": calls.append(pages) or data)
    digital = _pdf([DIGITAL])
    ocr.text_of_pdf(digital, "a.pdf")
    assert calls == []
    ocr.text_of_pdf(_pdf([DIGITAL, {"scan": True, "text": ["Scan"]}]), "b.pdf")
    assert calls == ["2"]
    ocr.text_of_pdf(digital, "c.pdf", force=True)
    assert calls == ["2", "all"]


def test_force_ocr_via_api(client: TestClient, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setenv("OCR_URL", "http://ocr:8000")
    monkeypatch.setattr(ocr, "ocr_pdf", lambda data, filename="", pages="": calls.append(pages) or data)
    pdf = _pdf([DIGITAL])
    files = {"file": ("x.pdf", pdf, "application/pdf")}
    assert client.post("/api/extract", files=files).status_code == 200 and calls == []
    assert client.post("/api/extract", files=files, data={"force_ocr": "true"}).status_code == 200
    assert client.post("/api/pdf/words", files=files, data={"force_ocr": "true"}).status_code == 200
    assert calls == ["all", "all"]
