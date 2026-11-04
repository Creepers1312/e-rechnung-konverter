"""Embed CII XML into a PDF to produce a ZUGFeRD / Factur-X hybrid invoice.

Uses the `factur-x` library, which attaches the XML as `factur-x.xml`, writes
the required XMP metadata and validates the XML against the Factur-X XSD.

Limitation: the *visual* PDF is not converted to strict PDF/A-3 here (fonts,
colour profiles). Most receivers accept the result; for strict conformance add
a Ghostscript/veraPDF step later (see README, "Roadmap").
"""

from __future__ import annotations

import facturx

from ..model import Invoice
from .cii import build_cii


def build_zugferd(pdf_bytes: bytes, invoice: Invoice) -> bytes:
    xml = build_cii(invoice, "zugferd")
    return facturx.generate_from_binary(
        pdf_bytes,
        xml,
        flavor="factur-x",
        level="en16931",
        check_xsd=True,
        pdf_metadata={
            "author": invoice.seller.name,
            "keywords": "Factur-X, ZUGFeRD, Rechnung",
            "title": f"Rechnung {invoice.number}",
            "subject": f"Rechnung {invoice.number} an {invoice.buyer.name}",
        },
        lang="de-DE",
    )


def check_xsd(xml: bytes) -> str | None:
    """Return None if the XML is XSD-valid, otherwise the error message."""
    try:
        facturx.xml_check_xsd(xml, flavor="factur-x", level="en16931")
    except Exception as exc:  # the library raises a generic Exception
        return str(exc)
    return None
