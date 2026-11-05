"""Client for the OCR service (ocr/ container).

`text_of_pdf` is the single entry point used by the API and the workers:
it returns the PDF's text layer and, for scans without one, asks the OCR
service first. Without OCR_URL (e.g. local development) scans simply yield
no text and the extraction explains why.
"""

from __future__ import annotations

import logging
import os

import httpx

from .extraction.pdf_text import PdfText

log = logging.getLogger("erechnung.ocr")


class OcrError(Exception):
    pass


def ocr_url() -> str:
    return os.environ.get("OCR_URL", "").rstrip("/")


def ocr_pdf(data: bytes, filename: str = "scan.pdf") -> bytes:
    url = ocr_url()
    if not url:
        raise OcrError("Texterkennung (OCR) ist nicht eingerichtet (OCR_URL).")
    try:
        r = httpx.post(
            f"{url}/ocr",
            files={"file": (filename, data, "application/pdf")},
            timeout=float(os.environ.get("OCR_CLIENT_TIMEOUT", "300")),
        )
    except httpx.HTTPError as exc:
        raise OcrError(f"OCR-Dienst nicht erreichbar: {exc}") from exc
    if r.status_code != 200:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise OcrError(f"OCR fehlgeschlagen ({r.status_code}): {detail}")
    return r.content


def text_of_pdf(data: bytes, filename: str = "scan.pdf") -> tuple[PdfText, str | None]:
    """PDF text, OCR'd if necessary. Returns (text, problem) – problem is a German message or None."""
    pdf = PdfText.from_bytes(data)
    if not pdf.needs_ocr or not ocr_url():
        return pdf, None
    try:
        return PdfText.from_bytes(ocr_pdf(data, filename), ocr=True), None
    except OcrError as exc:
        log.warning("%s: %s", filename, exc)
        return pdf, str(exc)
