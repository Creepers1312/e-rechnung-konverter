from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.company import Company
from app.export.pdf_render import render_invoice_pdf
from app.extraction.pdf_text import PdfText
from app.extraction.rules import extract
from app.model import Invoice
from app.validation import validate


def test_extracts_rendered_invoice(invoice: Invoice, company: Company) -> None:
    invoice.buyer.vat_id = "DE987654321"
    result = extract(PdfText.from_bytes(render_invoice_pdf(invoice)), company)
    got = result.invoice

    assert result.template_id == "_generic"
    assert got.number == "RE-2026-0042"
    assert got.issue_date == date(2026, 11, 4)
    assert got.delivery_date == date(2026, 10, 31)
    assert got.payment.due_date == date(2026, 11, 18)
    assert got.buyer_reference == "04011000-12345-34"
    assert got.order_reference == "PO-778"
    assert got.buyer.name == "Kunde AG"
    assert got.buyer.address.line1 == "Hauptstraße 5"
    assert (got.buyer.address.postcode, got.buyer.address.city) == ("10115", "Berlin")
    assert got.buyer.erp_id == "10042"
    assert got.buyer.vat_id == "DE987654321"
    assert got.seller.name == company.seller.name

    assert [(line.name, line.quantity, line.unit_code, line.unit_price, line.vat_rate) for line in got.lines] == [
        ("Beratung", Decimal("8"), "HUR", Decimal("95.00"), Decimal("19")),
        ("Lizenz Software", Decimal("2"), "H87", Decimal("125.00"), Decimal("19")),
        ("Fachbuch", Decimal("1"), "H87", Decimal("39.90"), Decimal("7")),
    ]
    assert result.stated_totals.net == Decimal("1049.90")
    assert result.stated_totals.gross == Decimal("1244.59")
    assert result.stated_totals.tax is None  # two tax lines (7 % and 19 %)
    assert not any(i.code == "TOTALS" for i in validate(got, "zugferd", result.stated_totals))

    # A misread rate is caught by the cross-check against the printed totals.
    got.lines[2].vat_rate = Decimal("19")
    assert any(i.code == "TOTALS" for i in validate(got, "zugferd", result.stated_totals))


def _simple_pdf(rows: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800.0
    for row in rows:
        c.drawString(60, y, row)
        y -= 14
    c.save()
    return buf.getvalue()


def test_layout_template_overrides_generic_rules(company: Company) -> None:
    pdf = _simple_pdf(
        [
            "Beleg-Nr. B-77",
            "Belegdatum 01.02.2026",
            "Artikel Menge Preis Summe",
            "Schrauben 100 Stk 0,10 € 10,00 €",
            "Warenwert 10,00 €",
        ]
    )
    result = extract(PdfText.from_bytes(pdf), company)
    assert result.template_id == "beispiel-layout"
    assert result.invoice.number == "B-77"
    assert result.invoice.issue_date == date(2026, 2, 1)
    assert [line.name for line in result.invoice.lines] == ["Schrauben"]


def test_custom_template_dir(tmp_path: Path, company: Company) -> None:
    generic = Path(__file__).resolve().parent.parent / "app/extraction/templates/_generic.yaml"
    (tmp_path / "_generic.yaml").write_text(generic.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "acme.yaml").write_text(
        "id: acme\nname: ACME\nmatch: {any: ['ACME Corp']}\nfields: {number: 'Vorgang\\s+(\\d+)'}\n",
        encoding="utf-8",
    )
    result = extract(PdfText.from_bytes(_simple_pdf(["ACME Corp", "Vorgang 4711"])), company, tmp_path)
    assert result.template_id == "acme"
    assert result.invoice.number == "4711"


def test_warns_when_pdf_has_no_text(company: Company) -> None:
    result = extract(PdfText.from_bytes(_simple_pdf([])), company)
    assert any("OCR" in w for w in result.warnings)
