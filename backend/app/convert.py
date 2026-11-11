"""Invoice -> e-invoice file. Shared by the HTTP API and the IDoc worker.

Release only after every check passed: business rules on the model
(`validation.py`), then the generated XML against the XSD and the official
Schematron rules (`schematron.py`); for ZUGFeRD the PDF is converted to
PDF/A-3 and the finished file is checked with veraPDF (`pdfa.py`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import pdfa, schematron
from .export.cii import Profile, build_cii
from .export.pdf_render import render_invoice_pdf
from .export.zugferd import build_zugferd, check_xsd
from .model import Invoice
from .validation import Issue, validate

log = logging.getLogger("erechnung.convert")


class NotExportable(Exception):
    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("; ".join(f"{i.code}: {i.message}" for i in issues))
        self.issues = issues


@dataclass
class EInvoice:
    filename: str
    media_type: str
    content: bytes


def safe_filename(invoice: Invoice, ext: str) -> str:
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in invoice.number) or "rechnung"
    return f"{stem}.{ext}"


def check_xml(xml: bytes, profile: Profile) -> list[Issue]:
    """XSD first (Schematron assumes a well-formed structure), then the official rules."""
    xsd_error = check_xsd(xml)
    if xsd_error:
        return [Issue(code="XSD", field="", message=f"Schema-Fehler: {xsd_error}")]
    return schematron.check(xml, profile)


def convert(invoice: Invoice, profile: Profile, source_pdf: bytes | None = None) -> EInvoice:
    """Validate, then build the file. ZUGFeRD without a source PDF renders one."""
    errors = [i for i in validate(invoice, profile) if i.severity == "error"]
    if errors:
        raise NotExportable(errors)
    xml = build_cii(invoice, profile)
    errors = [i for i in check_xml(xml, profile) if i.severity == "error"]
    if errors:
        raise NotExportable(errors)
    if profile == "xrechnung":
        return EInvoice(safe_filename(invoice, "xml"), "application/xml", xml)
    return EInvoice(safe_filename(invoice, "pdf"), "application/pdf", _zugferd(invoice, xml, source_pdf))


def _zugferd(invoice: Invoice, xml: bytes, source_pdf: bytes | None) -> bytes:
    """PDF/A-3b first, then embed the XML, then check the finished file (see `pdfa.py`)."""
    visual = source_pdf or render_invoice_pdf(invoice)
    if not pdfa.enabled():
        log.warning("PDF/A conversion and check skipped (no PDF service configured)")
        return build_zugferd(visual, invoice, xml)
    try:
        visual = pdfa.to_pdfa(visual)
    except pdfa.PdfaError as exc:
        raise NotExportable([pdfa.problem(exc, "PDF/A-Umwandlung")]) from exc
    result = build_zugferd(visual, invoice, xml)
    try:
        issues = pdfa.verify(result)
    except pdfa.PdfaError as exc:
        raise NotExportable([pdfa.problem(exc, "PDF/A-Prüfung")]) from exc
    if issues:
        if source_pdf is not None:
            issues.append(Issue(code="PDFA", field="", message=ORIGINAL_NOT_PDFA))
        raise NotExportable(issues)
    return result


ORIGINAL_NOT_PDFA = (
    "Das Original-PDF lässt sich nicht in ein gültiges PDF/A-3 umwandeln. Ausweg: die Rechnung als XRechnung erzeugen."
)
