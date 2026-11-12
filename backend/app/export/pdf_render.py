"""Render a plain, readable invoice PDF from an `Invoice`.

Used when there is no source PDF (e.g. invoices coming from SAP via IDoc), so a
ZUGFeRD hybrid can still be produced. Deliberately simple — layout polish is
not the point; the embedded XML is the legally relevant part.
"""

from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from ..model import Invoice, compute_totals


def _eur(value: Decimal) -> str:
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


UNIT_LABELS = {
    "H87": "Stk",
    "C62": "Stk",
    "HUR": "Std",
    "KGM": "kg",
    "MTR": "m",
    "LTR": "l",
    "LS": "Pauschal",
    "TNE": "Tonne",
}


def _qty(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def render_invoice_pdf(invoice: Invoice) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    s, b = invoice.seller, invoice.buyer

    def header() -> None:
        c.setFont("Helvetica", 7)
        c.drawString(20 * mm, height - 47 * mm, f"{s.name} · {s.address.line1} · {s.address.postcode} {s.address.city}")

    header()
    c.setFont("Helvetica", 10)
    y = height - 55 * mm
    for row in (b.name, b.address.line2, b.address.line1, f"{b.address.postcode} {b.address.city}"):
        if row.strip():
            c.drawString(20 * mm, y, row)
            y -= 5 * mm

    info = [
        ("Rechnungsnummer", invoice.number),
        ("Rechnungsdatum", invoice.issue_date.strftime("%d.%m.%Y") if invoice.issue_date else ""),
        ("Lieferdatum", invoice.delivery_date.strftime("%d.%m.%Y") if invoice.delivery_date else ""),
        ("Kundennummer", b.erp_id),
        ("Ihre USt-IdNr.", b.vat_id),
        ("Bestellnummer", invoice.order_reference),
        ("Leitweg-ID", invoice.buyer_reference),
    ]
    y = height - 55 * mm
    c.setFont("Helvetica", 9)
    for label, value in info:
        if value:
            c.drawString(125 * mm, y, f"{label}: {value}")
            y -= 4.5 * mm

    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, height - 100 * mm, "Rechnungskorrektur" if invoice.type_code == "381" else "Rechnung")

    y = height - 112 * mm
    c.setFont("Helvetica-Bold", 9)
    cols = (20, 30, 120, 140, 164, 178)
    for x, label in zip(cols, ("Pos", "Bezeichnung", "Menge", "Einzelpreis", "USt", "Gesamt"), strict=True):
        c.drawString(x * mm, y, label)
    y -= 6 * mm
    c.setFont("Helvetica", 9)
    for line in invoice.lines:
        if y < 40 * mm:
            c.showPage()
            header()
            c.setFont("Helvetica", 9)
            y = height - 30 * mm
        c.drawString(20 * mm, y, line.id)
        c.drawString(30 * mm, y, line.name[:55])
        c.drawRightString(135 * mm, y, f"{_qty(line.quantity)} {UNIT_LABELS.get(line.unit_code, '')}".strip())
        price = _eur(line.unit_price)
        if line.price_base_quantity != 1:  # "425,22 € / 100 kg"
            price += f" / {_qty(line.price_base_quantity)} {UNIT_LABELS.get(line.unit_code, '')}".rstrip()
        c.drawRightString(160 * mm, y, price)
        c.drawRightString(172 * mm, y, f"{_qty(line.vat_rate if line.vat_category == 'S' else Decimal(0))} %")
        c.drawRightString(190 * mm, y, _eur(line.net_amount))
        y -= 5 * mm

    totals = compute_totals(invoice)
    y -= 4 * mm
    rows = [("Nettobetrag", totals.line_total)]
    rows += [(f"USt. {_qty(t.rate)} %", t.amount) for t in totals.breakdown]
    rows += [("Gesamtbetrag", totals.grand_total)]
    for i, (label, value) in enumerate(rows):
        c.setFont("Helvetica-Bold" if i == len(rows) - 1 else "Helvetica", 9)
        c.drawString(130 * mm, y, label)
        c.drawRightString(190 * mm, y, _eur(value))
        y -= 5 * mm

    p = invoice.payment
    c.setFont("Helvetica", 8)
    footer = [
        p.terms,
        f"Fällig am: {p.due_date.strftime('%d.%m.%Y')}" if p.due_date else "",
        f"Bankverbindung: {p.account_holder} · IBAN {p.iban} · BIC {p.bic}" if p.iban else "",
        f"USt-IdNr.: {s.vat_id}" if s.vat_id else (f"Steuernummer: {s.tax_number}" if s.tax_number else ""),
    ]
    y = 25 * mm
    for row in footer:
        if row:
            c.drawString(20 * mm, y, row)
            y -= 4 * mm

    c.save()
    return buf.getvalue()
