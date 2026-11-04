from __future__ import annotations

from decimal import Decimal

import facturx
from lxml import etree

from app.export.cii import GUIDELINE_IDS, NS, build_cii
from app.export.pdf_render import render_invoice_pdf
from app.export.zugferd import build_zugferd, check_xsd
from app.model import Invoice, LineItem, compute_totals
from app.validation import StatedTotals, iban_is_valid, validate


def test_totals_group_tax_by_rate(invoice: Invoice) -> None:
    t = compute_totals(invoice)
    assert t.line_total == Decimal("1049.90")  # 760 + 250 + 39.90
    rates = {b.rate: (b.basis, b.amount) for b in t.breakdown}
    assert rates[Decimal("19")] == (Decimal("1010.00"), Decimal("191.90"))
    assert rates[Decimal("7")] == (Decimal("39.90"), Decimal("2.79"))
    assert t.tax_total == Decimal("194.69")
    assert t.grand_total == Decimal("1244.59")


def test_german_number_notation_is_accepted() -> None:
    line = LineItem(quantity="1.000,5", unit_price="2,00")
    assert line.quantity == Decimal("1000.5")
    assert line.net_amount == Decimal("2001.00")


def test_cii_is_xsd_valid_for_both_profiles(invoice: Invoice) -> None:
    for profile in ("zugferd", "xrechnung"):
        xml = build_cii(invoice, profile)  # type: ignore[arg-type]
        assert check_xsd(xml) is None
        root = etree.fromstring(xml)
        gid = root.findtext(".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", namespaces=NS)
        assert gid == GUIDELINE_IDS[profile]  # type: ignore[index]
        assert root.findtext(".//ram:GrandTotalAmount", namespaces=NS) == "1244.59"


def test_zugferd_embeds_xml_that_can_be_read_back(invoice: Invoice) -> None:
    pdf = build_zugferd(render_invoice_pdf(invoice), invoice)
    filename, xml = facturx.get_xml_from_pdf(pdf, check_xsd=True)
    assert filename == "factur-x.xml"
    assert b"RE-2026-0042" in xml


def test_validation_passes_for_complete_invoice(invoice: Invoice) -> None:
    assert [i for i in validate(invoice, "xrechnung") if i.severity == "error"] == []


def test_validation_reports_missing_mandatory_fields(invoice: Invoice) -> None:
    invoice.number = ""
    invoice.buyer_reference = ""
    invoice.lines = []
    codes = {i.code for i in validate(invoice, "xrechnung")}
    assert {"BR-02", "BR-16", "BR-DE-15"} <= codes
    # Leitweg-ID is only required for XRechnung
    assert "BR-DE-15" not in {i.code for i in validate(invoice, "zugferd")}


def test_validation_compares_stated_totals(invoice: Invoice) -> None:
    ok = StatedTotals(net=Decimal("1049.90"), gross=Decimal("1244.59"))
    assert not [i for i in validate(invoice, "zugferd", ok) if i.code == "TOTALS"]
    wrong = StatedTotals(gross=Decimal("1300.00"))
    assert [i for i in validate(invoice, "zugferd", wrong) if i.code == "TOTALS"]


def test_iban_checksum() -> None:
    assert iban_is_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_is_valid("DE89 3704 0044 0532 0130 01")
