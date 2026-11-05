"""Recognition rules with OCR-style text (layout of a scanned SAP invoice; all data invented)."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.company import Company, load_company
from app.extraction.pdf_text import PdfText
from app.extraction.rules import extract
from app.model import compute_totals
from app.validation import validate


def _pdf(rows: list[str], address: list[str] | None = None) -> bytes:
    """One text line per row; `address` goes into the DIN 5008 window (buyer)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    c.setFont("Helvetica", 9)
    for i, row in enumerate(address or []):
        c.drawString(60, height - 160 - i * 12, row)
    for i, row in enumerate(rows):
        c.drawString(330 if i < 8 else 60, height - (100 + i * 14 if i < 8 else 320 + (i - 8) * 14), row)
    c.save()
    return buf.getvalue()


SAP_HEADER = [
    "Rechnung",
    "Rechnungs-Nr. / Datum: 6000001234 / 03.11.2026",
    "Kundennummer:",  # empty on purpose: must not swallow the next line
    "Ihre USt-Id. Nr.:",
    "Ihre Bestellung: S26/1111111111-10 /",
    "Auftrags-Nr.: 3010000001",
    "Lieferschein-Nr. / Datum: 3300000001/02.11.2026",
    "Zahlungsbedingungen: 30 Tage ohne Abzug",
]
SAP_ITEM = [
    "Materialnummer 3-31000001",
    "Beschreibung Testmasse Typ 50 Plus",
    "Bruttogewicht Nettogewicht Preis /1 TO Nettosumme 24.034,38 EUR",
    "23.820 TO 23.820 TO 1.009,00 EUR 19,00 % MwSt. Total 4.566,53 EUR",  # OCR read "23,820" as "23.820"
    "Zu zahlender Gesamtbetrag 28.600,91 EUR",
    "lose verladen",
    "Bemerkung:",
    "Rechnungsdatum entspricht Lieferdatum",
]


def _company(name: str = "Rheinfelden Carbon Products GmbH") -> Company:
    """Complete master data (address, IBAN, ...) from the example file, only the name changed."""
    company = load_company().model_copy(deep=True)
    company.seller.name = name
    return company


def test_scanned_sap_invoice_layout() -> None:
    pdf = _pdf(
        ["Rheinfelden Carbon Products GmbH"] + SAP_HEADER[:7] + SAP_HEADER[7:] + SAP_ITEM,
        address=[
            "Rheinfelden Carbon Products GmbH, Musterweg 2, D-79618 Rheinfelden",
            "Beispiel Chemie AG",
            "Werkstraße 7",
            "84508 Burgkirchen",
        ],
    )
    r = extract(PdfText.from_bytes(pdf, ocr=True), _company())
    inv = r.invoice
    assert r.template_id == "rheinfelden-carbon"
    assert inv.number == "6000001234"
    assert inv.issue_date == date(2026, 11, 3)
    assert inv.delivery_date == date(2026, 11, 2)
    assert inv.order_reference == "S26/1111111111-10"
    assert inv.payment.terms == "30 Tage ohne Abzug"
    assert inv.payment.due_date == date(2026, 12, 3)  # 30 days after the invoice date
    assert inv.buyer.erp_id == ""  # "Kundennummer:" is empty – nothing from the next line
    assert inv.buyer.name == "Beispiel Chemie AG"
    assert (inv.buyer.address.postcode, inv.buyer.address.city) == ("84508", "Burgkirchen")

    [line] = inv.lines
    assert (line.seller_item_id, line.name, line.unit_code) == ("3-31000001", "Testmasse Typ 50 Plus", "TNE")
    assert (line.quantity, line.unit_price, line.vat_rate) == (Decimal("23.820"), Decimal("1009.00"), Decimal("19.00"))
    t = compute_totals(inv)
    assert (t.line_total, t.tax_total, t.grand_total) == (Decimal("24034.38"), Decimal("4566.53"), Decimal("28600.91"))
    assert (r.stated_totals.net, r.stated_totals.tax, r.stated_totals.gross) == (
        Decimal("24034.38"),
        Decimal("4566.53"),
        Decimal("28600.91"),
    )
    assert not [i for i in validate(inv, "zugferd", r.stated_totals) if i.severity == "error"]
    assert any("OCR" in n for n in r.notes) and any("Fälligkeit" in n for n in r.notes)
    assert not any("Absender" in w for w in r.warnings)


def test_ambiguous_quantity_uses_printed_total() -> None:
    """'1.000' with a net total of 2.500,00 at 2,50 each is one thousand, not 1.0."""
    rows = [
        "Rechnungsnummer: A-1",
        "Rechnungsdatum: 01.02.2026",
        "Pos Bezeichnung Menge Einzelpreis Gesamt",
        "1 Schrauben 1.000 Stk 2,50 € 2.500,00 €",
        "Nettobetrag 2.500,00 €",
    ]
    r = extract(PdfText.from_bytes(_pdf(rows)), _company("Testfirma GmbH"))
    assert r.invoice.lines[0].quantity == Decimal("1000")


def test_seller_mismatch_is_reported() -> None:
    r = extract(PdfText.from_bytes(_pdf(["Ganz andere Firma KG"] + SAP_HEADER)), _company("Musterfirma GmbH"))
    assert any("Absender" in w for w in r.warnings)


def test_no_text_without_ocr_is_explained() -> None:
    r = extract(PdfText.from_bytes(_pdf([])), _company())
    assert any("OCR" in w for w in r.warnings)


def test_article_number_is_exported(invoice) -> None:  # noqa: ANN001 - fixture
    from lxml import etree

    from app.export.cii import NS, build_cii
    from app.export.zugferd import check_xsd

    invoice.lines[0].seller_item_id = "3-31000001"
    xml = build_cii(invoice)
    assert check_xsd(xml) is None
    ids = etree.fromstring(xml).xpath("//ram:SpecifiedTradeProduct/ram:SellerAssignedID/text()", namespaces=NS)
    assert ids == ["3-31000001"]


def test_extract_endpoint_runs_ocr_for_scans(client, monkeypatch) -> None:  # noqa: ANN001 - fixture
    """A PDF without text is sent to the OCR service; its result is used for recognition."""
    from app import ocr

    text_pdf = _pdf(["Rheinfelden Carbon Products GmbH"] + SAP_HEADER + SAP_ITEM)
    calls = []
    monkeypatch.setenv("OCR_URL", "http://ocr:8000")
    monkeypatch.setattr(ocr, "ocr_pdf", lambda data, filename="": calls.append(filename) or text_pdf)

    r = client.post("/api/extract", files={"file": ("scan.pdf", _pdf([]), "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert calls == ["scan.pdf"] and body["ocr"] is True
    assert body["invoice"]["number"] == "6000001234"

    # PDFs that already contain text are not sent to OCR
    calls.clear()
    client.post("/api/extract", files={"file": ("text.pdf", text_pdf, "application/pdf")})
    assert calls == []


def test_extract_endpoint_reports_unreachable_ocr(client, monkeypatch) -> None:  # noqa: ANN001 - fixture
    monkeypatch.setenv("OCR_URL", "http://127.0.0.1:9")  # nothing listens there
    r = client.post("/api/extract", files={"file": ("scan.pdf", _pdf([]), "application/pdf")})
    assert r.status_code == 200
    assert "OCR-Dienst nicht erreichbar" in r.json()["warnings"][0]
