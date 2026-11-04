"""Invoice -> e-invoice file. Shared by the HTTP API and the IDoc worker."""

from __future__ import annotations

from dataclasses import dataclass

from .export.cii import Profile, build_cii
from .export.pdf_render import render_invoice_pdf
from .export.zugferd import build_zugferd
from .model import Invoice
from .validation import Issue, validate


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


def convert(invoice: Invoice, profile: Profile, source_pdf: bytes | None = None) -> EInvoice:
    """Validate, then build the file. ZUGFeRD without a source PDF renders one."""
    errors = [i for i in validate(invoice, profile) if i.severity == "error"]
    if errors:
        raise NotExportable(errors)
    if profile == "xrechnung":
        return EInvoice(safe_filename(invoice, "xml"), "application/xml", build_cii(invoice, "xrechnung"))
    pdf = build_zugferd(source_pdf or render_invoice_pdf(invoice), invoice)
    return EInvoice(safe_filename(invoice, "pdf"), "application/pdf", pdf)
