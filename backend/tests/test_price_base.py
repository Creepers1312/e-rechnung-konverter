"""Price per 100/1000 KG (BT-149), OCR dot decimals, tax-free export and the
three own-company layouts. All customer data and figures invented."""

from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path

from lxml import etree
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.company import Company
from app.erp.sap_ecc_idoc import parse_invoic02
from app.export.cii import build_cii
from app.export.pdf_render import render_invoice_pdf
from app.export.zugferd import check_xsd
from app.extraction.pdf_text import PdfText
from app.extraction.rules import extract
from app.model import Invoice, LineItem, compute_totals
from app.validation import validate

SAMPLE_IDOC = Path(__file__).parent / "fixtures" / "invoic02_sample.xml"


def _pdf(lines: list[str]) -> PdfText:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    c.setFont("Helvetica", 9)
    for i, row in enumerate(lines):
        c.drawString(40, h - 60 - i * 13, row)
    c.save()
    return PdfText.from_bytes(buf.getvalue())


def test_net_amount_and_cii_with_price_base(invoice: Invoice) -> None:
    invoice.lines = [
        LineItem(name="Granulat", quantity=Decimal("1071"), unit_code="KGM", unit_price=Decimal("425.22"),
                 price_base_quantity=Decimal("100")),
    ]  # fmt: skip
    assert invoice.lines[0].net_amount == Decimal("4554.11")  # 1071 × 425,22 / 100
    assert compute_totals(invoice).line_total == Decimal("4554.11")
    xml = build_cii(invoice, "zugferd")
    assert check_xsd(xml) is None
    ns = {"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"}
    basis = etree.fromstring(xml).find(".//ram:NetPriceProductTradePrice/ram:BasisQuantity", ns)
    assert basis is not None and basis.text == "100" and basis.get("unitCode") == "KGM"
    assert render_invoice_pdf(invoice)[:4] == b"%PDF"

    invoice.lines[0].price_base_quantity = Decimal("0")
    assert any(i.code == "BT-149" for i in validate(invoice, "zugferd"))


def test_idoc_price_unit_peinh(company: Company) -> None:
    xml = SAMPLE_IDOC.read_bytes().replace(b"<MENGE>3</MENGE>", b"<MENGE>3</MENGE><PEINH>100</PEINH>", 1)
    line = parse_invoic02(xml, company).lines[0]
    assert line.price_base_quantity == 100 and line.net_amount == Decimal("100.00")
    assert line.unit_price == Decimal("3333.333333")  # per 100 units, round2(3 × price / 100) = 100.00


def test_alloys_layout_with_ocr_dot_decimals(company: Company) -> None:
    """German SAP layout, price per 1000 KG; OCR read "1.744,000" and "2.640,00" with dots."""
    pdf = _pdf(
        [
            "Aluminium Rheinfelden Alloys GmbH, Bukheinstrasse 2, D-79618 Rheinfelden",
            "Rechnungs-Nr. / Datum: 4000000001 / 27.10.2026",
            "Kunden-Nr.: 900001",
            "Ihre Bestellung / Datum: Mail Einkauf / 29.09.2026",
            "Materialnummer 1-00099999",
            "Beschreibung TESTLEGIERUNG AL-SI7",
            "Bruttogesamtgewicht Preis / 1000 KG Nettosumme 4.604,16 EUR",
            "1.744.000 KG 2.640.00 EUR 19,00 % Mwst. Total 874,79 EUR",
            "Zu zahlender Gesamtbetrag 5.478,95 EUR",
            "Zahlungsbedingungen im Rahmen der Warenkreditversicherung:",
            "Bar netto Kasse nach Rechnungserhalt",
        ]
    )
    r = extract(pdf, company)
    assert r.template_id == "rheinfelden-alloys"
    inv = r.invoice
    assert (inv.number, inv.buyer.erp_id, inv.order_reference) == ("4000000001", "900001", "Mail Einkauf")
    assert inv.payment.terms == "Bar netto Kasse nach Rechnungserhalt"
    [line] = inv.lines
    assert (line.quantity, line.unit_code, line.unit_price, line.price_base_quantity) == (
        Decimal("1744"), "KGM", Decimal("2640"), Decimal("1000"),
    )  # fmt: skip
    assert compute_totals(inv).grand_total == r.stated_totals.gross == Decimal("5478.95")


def test_semis_english_layout_tax_free_export(company: Company) -> None:
    pdf = _pdf(
        [
            "Aluminium Rheinfelden Semis GmbH, Friedrichstrasse 50, D-79618 Rheinfelden",
            "Invoice-No. / Date: 5000000001 / 14.09.2026",
            "Customer-No.: 900002",
            "Your PO No.: 4711/26",
            "Delivery-Note / Date: 2020000001 / 13.09.2026",
            "Payment terms: 60 days after date of B/L",
            "tax free export",
            "Pos. |Material number Quantity |Pc/Unit |Price / 100 KG Price wo. VAT",
            "10 2-00011111",
            "ROUND SLUGS, WITHOUT HOLE, FLAT",
            "1.071,00 KG 425,22 4.554,11",
            "Order Nr. / 2010000001",
            "20 2-00022222",
            "ROUND SLUGS, WITHOUT HOLE, FLAT",
            "974,00 KG 425,22 4.141,64",
            "Total net weight 2.045,000 KG Net Total 8.695,75 EUR",
            "VAT Total 0,00 EUR",
            "Total amount to be paid 8.695,75 EUR",
        ]
    )
    r = extract(pdf, company)
    assert r.template_id == "rheinfelden-semis"
    inv = r.invoice
    assert (inv.number, str(inv.issue_date), str(inv.delivery_date)) == ("5000000001", "2026-09-14", "2026-09-13")
    assert (inv.order_reference, inv.buyer.erp_id) == ("4711/26", "900002")
    assert inv.payment.due_date is None  # "after date of B/L": not computable
    assert [(line.id, line.quantity, line.price_base_quantity, line.vat_category) for line in inv.lines] == [
        ("10", Decimal("1071"), Decimal("100"), "G"),
        ("20", Decimal("974"), Decimal("100"), "G"),
    ]
    t = compute_totals(inv)
    assert (t.line_total, t.tax_total, t.grand_total) == (Decimal("8695.75"), 0, Decimal("8695.75"))
    assert any("Ausfuhr" in n for n in r.notes)
    assert not r.warnings or all("Positionen" not in w for w in r.warnings)
