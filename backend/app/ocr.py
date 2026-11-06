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


def ocr_pdf(data: bytes, filename: str = "scan.pdf", pages: str = "") -> bytes:
    """`pages`: "" = only pages without text (service default), "all" or "1,3" = recognise these
    even if they carry a text layer."""
    url = ocr_url()
    if not url:
        raise OcrError("Texterkennung (OCR) ist nicht eingerichtet (OCR_URL).")
    try:
        r = httpx.post(
            f"{url}/ocr",
            files={"file": (filename, data, "application/pdf")},
            data={"pages": pages} if pages else None,
            timeout=float(os.environ.get("OCR_CLIENT_TIMEOUT", "300")),
        )
    except httpx.HTTPError as exc:
        raise OcrError(f"OCR-Dienst nicht erreichbar: {exc}") from exc
    if r.status_code != 200:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise OcrError(f"OCR fehlgeschlagen ({r.status_code}): {detail}")
    return r.content


def text_of_pdf(data: bytes, filename: str = "scan.pdf", force: bool = False) -> tuple[PdfText, str | None]:
    """PDF text, OCR'd where necessary. Returns (text, problem) – problem is a German message or None.

    Only scanned pages are recognised (PdfText.scan_pages); `force` recognises every page,
    e.g. when a scanner's own text layer is poor but passed the checks.
    """
    pdf = PdfText.from_bytes(data)
    if force:
        pages = "all"
    else:
        scanned = pdf.scan_pages()
        if not scanned:
            return pdf, None
        pages = ",".join(map(str, scanned))
    if not ocr_url():
        return pdf, "Texterkennung (OCR) ist nicht eingerichtet (OCR_URL)." if force else None
    try:
        return PdfText.from_bytes(ocr_pdf(data, filename, pages), ocr=True), None
    except OcrError as exc:
        log.warning("%s: %s", filename, exc)
        return pdf, str(exc)
