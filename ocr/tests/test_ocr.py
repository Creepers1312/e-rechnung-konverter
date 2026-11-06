"""Needs tesseract (with 'deu') and ocrmypdf installed – runs in CI and in the container build context."""

from __future__ import annotations

import io
import shutil

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")

from app import app  # noqa: E402

client = TestClient(app)


def scanned_pdf(lines: list[str]) -> bytes:
    """Render text to a PDF, rasterise it and wrap the image in a new PDF (= a 'scan' without text)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 14)
    for i, line in enumerate(lines):
        c.drawString(60, 760 - i * 24, line)
    c.save()
    with pdfplumber.open(io.BytesIO(buf.getvalue())) as pdf:
        image = pdf.pages[0].to_image(resolution=200).original
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    c.drawImage(ImageReader(image), 0, 0, *A4)
    c.save()
    return out.getvalue()


def text_of(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return "\n".join(p.extract_text() or "" for p in doc.pages)


def test_health_lists_languages() -> None:
    r = client.get("/health")
    assert r.status_code == 200, r.text
    assert "deu" in r.json()["languages"]


def test_scan_gets_text_layer() -> None:
    scan = scanned_pdf(["Rechnungs-Nr. / Datum: 6000001234 / 03.11.2026", "Zu zahlender Gesamtbetrag 28.600,91 EUR"])
    assert text_of(scan).strip() == ""
    r = client.post("/ocr", files={"file": ("scan.pdf", scan, "application/pdf")})
    assert r.status_code == 200, r.text
    text = text_of(r.content)
    assert "6000001234" in text and "03.11.2026" in text and "28.600,91" in text


def test_requested_pages_are_recognised_despite_a_text_layer() -> None:
    """Scanners often add a stamp or their own poor text layer: "pages" forces OCR anyway."""
    scan = scanned_pdf(["Rechnungs-Nr. / Datum: 6000001234 / 03.11.2026"])
    # add a small real text (like a scanner stamp) on top of the image
    stamp = io.BytesIO()
    with pdfplumber.open(io.BytesIO(scan)) as doc:
        image = doc.pages[0].to_image(resolution=200).original
    c = canvas.Canvas(stamp, pagesize=A4)
    c.drawImage(ImageReader(image), 0, 0, *A4)
    c.setFont("Helvetica", 6)
    c.drawString(20, 20, "Scan 1")
    c.save()
    pdf = stamp.getvalue()

    untouched = client.post("/ocr", files={"file": ("s.pdf", pdf, "application/pdf")})
    assert "6000001234" not in text_of(untouched.content)  # default: page has text -> skipped
    r = client.post("/ocr", files={"file": ("s.pdf", pdf, "application/pdf")}, data={"pages": "1"})
    assert r.status_code == 200, r.text
    assert "6000001234" in text_of(r.content)
    bad = client.post("/ocr", files={"file": ("s.pdf", pdf, "application/pdf")}, data={"pages": "1;rm"})
    assert bad.status_code == 400


def test_rejects_non_pdf() -> None:
    assert client.post("/ocr", files={"file": ("x.pdf", b"hello", "application/pdf")}).status_code == 400
