"""Business-rule checks run before export.

These cover the EN 16931 / XRechnung rules that typically fail when data comes
from a PDF. They are a fast pre-check for the review form, not a replacement
for the official KoSIT validator (see README, "Roadmap").
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from .export.cii import Profile
from .model import Invoice, compute_totals

Severity = Literal["error", "warning"]


class Issue(BaseModel):
    code: str
    field: str
    message: str
    severity: Severity = "error"


class StatedTotals(BaseModel):
    """Totals as printed on the source document (for cross-checking)."""

    net: Decimal | None = None
    tax: Decimal | None = None
    gross: Decimal | None = None


VAT_ID_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{2,13}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def iban_is_valid(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    digits = "".join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    return int(digits) % 97 == 1


def validate(invoice: Invoice, profile: Profile, stated: StatedTotals | None = None) -> list[Issue]:
    issues: list[Issue] = []

    def err(code: str, field: str, message: str) -> None:
        issues.append(Issue(code=code, field=field, message=message))

    def warn(code: str, field: str, message: str) -> None:
        issues.append(Issue(code=code, field=field, message=message, severity="warning"))

    s, b = invoice.seller, invoice.buyer

    if not invoice.number.strip():
        err("BR-02", "number", "Rechnungsnummer fehlt.")
    if invoice.issue_date is None:
        err("BR-03", "issue_date", "Rechnungsdatum fehlt.")
    if not s.name.strip():
        err("BR-06", "seller.name", "Name des Verkäufers fehlt.")
    if not b.name.strip():
        err("BR-07", "buyer.name", "Name des Käufers fehlt.")
    if not (s.address.city and s.address.postcode):
        err("BR-08", "seller.address", "Anschrift des Verkäufers unvollständig (PLZ/Ort).")
    if not s.address.country_code:
        err("BR-09", "seller.address.country_code", "Ländercode des Verkäufers fehlt.")
    if not (b.address.city and b.address.postcode):
        err("BR-10", "buyer.address", "Anschrift des Käufers unvollständig (PLZ/Ort).")
    if not b.address.country_code:
        err("BR-11", "buyer.address.country_code", "Ländercode des Käufers fehlt.")
    if not invoice.lines:
        err("BR-16", "lines", "Die Rechnung braucht mindestens eine Position.")

    for i, line in enumerate(invoice.lines):
        if not line.name.strip():
            err("BR-25", f"lines.{i}.name", f"Position {i + 1}: Bezeichnung fehlt.")
        if line.quantity == 0:
            warn("QTY", f"lines.{i}.quantity", f"Position {i + 1}: Menge ist 0.")

    if any(line.vat_category == "S" for line in invoice.lines) and not (s.vat_id or s.tax_number):
        err("BR-S-02", "seller.vat_id", "USt-IdNr. oder Steuernummer des Verkäufers fehlt.")
    if any(line.vat_category in ("AE", "K") for line in invoice.lines) and not b.vat_id:
        err(
            "BR-AE-02",
            "buyer.vat_id",
            "Bei Reverse Charge / innergem. Lieferung ist die USt-IdNr. des Käufers Pflicht.",
        )
    for field, value in (("seller.vat_id", s.vat_id), ("buyer.vat_id", b.vat_id)):
        if value and not VAT_ID_RE.match(value.replace(" ", "")):
            warn("VATID", field, f"USt-IdNr. '{value}' hat ein ungewöhnliches Format.")

    pay = invoice.payment
    if pay.means_code == "58" and not pay.iban:
        err("IBAN", "payment.iban", "Für SEPA-Überweisung (Code 58) wird eine IBAN benötigt.")
    if pay.iban and not iban_is_valid(pay.iban):
        err("IBAN", "payment.iban", "IBAN-Prüfsumme ist ungültig.")

    if profile == "xrechnung":
        if not invoice.buyer_reference.strip():
            err("BR-DE-15", "buyer_reference", "XRechnung: Käuferreferenz / Leitweg-ID (BT-10) fehlt.")
        c = s.contact
        if not (c.name and c.phone and c.email):
            err(
                "XR-CONTACT",
                "seller.contact",
                "XRechnung: Kontakt des Verkäufers (Name, Telefon, E-Mail) unvollständig.",
            )
        if not s.electronic_address:
            err(
                "XR-EADDR",
                "seller.electronic_address",
                "XRechnung: elektronische Adresse des Verkäufers (BT-34) fehlt.",
            )
        if not b.electronic_address:
            err("XR-EADDR", "buyer.electronic_address", "XRechnung: elektronische Adresse des Käufers (BT-49) fehlt.")
        if not (pay.terms or pay.due_date):
            warn("XR-TERMS", "payment.terms", "XRechnung: Zahlungsbedingungen oder Fälligkeitsdatum angeben.")
    for field, value in (
        ("seller.electronic_address", s.electronic_address),
        ("buyer.electronic_address", b.electronic_address),
    ):
        if value and not EMAIL_RE.match(value):
            warn("EMAIL", field, f"'{value}' sieht nicht wie eine E-Mail-Adresse aus.")

    if stated and invoice.lines:
        totals = compute_totals(invoice)
        for label, computed, printed in (
            ("Nettobetrag", totals.line_total, stated.net),
            ("Steuerbetrag", totals.tax_total, stated.tax),
            ("Bruttobetrag", totals.grand_total, stated.gross),
        ):
            if printed is not None and abs(computed - printed) > Decimal("0.01"):
                err(
                    "TOTALS",
                    "lines",
                    f"{label} weicht vom PDF ab: berechnet {computed:.2f}, im PDF {printed:.2f}. Positionen prüfen.",
                )

    return issues
