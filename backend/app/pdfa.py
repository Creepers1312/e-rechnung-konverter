"""PDF/A-3 for ZUGFeRD: conversion and validation via the PDF service (ocr/ container).

`convert.convert` uses both for every ZUGFeRD file: the visual PDF is converted
to PDF/A-3b (Ghostscript) *before* the XML is embedded, and the finished file is
validated with veraPDF *after* – embedding is what usually breaks conformance.

Active when the PDF service is configured (OCR_URL, always set in Docker
Compose); `PDFA_CHECK=off` switches it off explicitly. Without the service
(local development, tests) the check is skipped and that is logged. When it is
active and the service fails, nothing is released (fail closed).
"""

from __future__ import annotations

import logging
import os

import httpx

from .ocr import ocr_url
from .validation import Issue

log = logging.getLogger("erechnung.pdfa")

FLAVOUR = "3b"  # ZUGFeRD / Factur-X: PDF/A-3, level B is sufficient


class PdfaError(Exception):
    """The PDF service could not convert or check the file (German message)."""


def enabled() -> bool:
    return bool(ocr_url()) and os.environ.get("PDFA_CHECK", "on").strip().lower() not in ("off", "0", "false", "no")


def _post(path: str, pdf: bytes, data: dict[str, str] | None = None) -> httpx.Response:
    try:
        r = httpx.post(
            f"{ocr_url()}{path}",
            files={"file": ("rechnung.pdf", pdf, "application/pdf")},
            data=data,
            timeout=float(os.environ.get("OCR_CLIENT_TIMEOUT", "300")),
        )
    except httpx.HTTPError as exc:
        raise PdfaError(f"PDF-Dienst nicht erreichbar ({exc}) – bitte später erneut versuchen.") from exc
    if r.status_code != 200:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise PdfaError(f"{detail} (PDF-Dienst, {r.status_code})")
    return r


def to_pdfa(pdf: bytes) -> bytes:
    """Convert the visual invoice to PDF/A-3b."""
    return _post("/pdfa", pdf).content


def verify(pdf: bytes) -> list[Issue]:
    """veraPDF findings for the finished file; empty = PDF/A-3b compliant."""
    result = _post("/verify", pdf, {"flavour": FLAVOUR}).json()
    if result.get("compliant") is True:
        return []
    rules = result.get("failed_rules") or []
    issues = [
        Issue(
            code=f"PDFA-{rule.get('clause', '')}-{rule.get('test', '')}",
            field="",
            message=f"PDF/A-Prüfung (ISO 19005-3, {rule.get('clause', '')}): {rule.get('description', '')}",
        )
        for rule in rules
    ]
    return issues or [Issue(code="PDFA", field="", message="PDF/A-Prüfung: Die Datei ist kein gültiges PDF/A-3.")]


def problem(exc: PdfaError, step: str) -> Issue:
    return Issue(code="PDFA", field="", message=f"{step}: {exc}")
