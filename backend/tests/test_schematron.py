"""Official Schematron rules (EN 16931, XRechnung) on the generated XML."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from app import convert as convert_module
from app import schematron
from app.convert import NotExportable, check_xml, convert
from app.export.cii import BUSINESS_PROCESS_ID, NS, build_cii
from app.model import Invoice
from app.validation import Issue, validate


def _category(code: str, country: str = "AT", postcode: str = "1010", vat_id: str = "ATU12345678"):
    def apply(inv: Invoice) -> None:
        for line in inv.lines:
            line.vat_category = code  # type: ignore[assignment]
            line.vat_rate = Decimal("0")
        inv.buyer.vat_id = vat_id
        inv.buyer.address.country_code = country
        inv.buyer.address.postcode = postcode

    return apply


def _price_per_100kg(inv: Invoice) -> None:
    line = inv.lines[0]
    line.unit_code, line.quantity = "KGM", Decimal("2500")
    line.unit_price, line.price_base_quantity = Decimal("312.50"), Decimal("100")  # 312.50 EUR per 100 kg


def _credit_note(inv: Invoice) -> None:
    inv.type_code = "381"


VARIANTS: dict[str, Callable[[Invoice], None]] = {
    "standard": lambda inv: None,
    "reverse_charge": _category("AE"),
    "intra_community": _category("K"),
    "export": _category("G", country="CH", postcode="8001", vat_id=""),
    "exempt": _category("E", country="DE", postcode="10115", vat_id=""),
    "price_per_100kg": _price_per_100kg,
    "credit_note": _credit_note,
}


@pytest.mark.parametrize("profile", ["zugferd", "xrechnung"])
@pytest.mark.parametrize("variant", VARIANTS)
def test_generated_xml_passes_official_rules(invoice: Invoice, variant: str, profile: str) -> None:
    VARIANTS[variant](invoice)
    assert [i for i in validate(invoice, profile) if i.severity == "error"] == []  # type: ignore[arg-type]
    issues = check_xml(build_cii(invoice, profile), profile)  # type: ignore[arg-type]
    assert [i for i in issues if i.severity == "error"] == []


def test_xrechnung_names_the_business_process(invoice: Invoice) -> None:
    root = etree.fromstring(build_cii(invoice, "xrechnung"))
    path = ".//ram:BusinessProcessSpecifiedDocumentContextParameter/ram:ID"
    assert root.findtext(path, namespaces=NS) == BUSINESS_PROCESS_ID


def test_official_rules_find_what_the_precheck_misses(invoice: Invoice) -> None:
    root = etree.fromstring(build_cii(invoice, "xrechnung"))
    process = root.find(".//ram:BusinessProcessSpecifiedDocumentContextParameter", namespaces=NS)
    process.getparent().remove(process)
    issues = schematron.check(etree.tostring(root), "xrechnung")
    finding = next(i for i in issues if i.code == "PEPPOL-EN16931-R001")
    assert finding.severity == "error"  # flag="fatal"
    assert finding.message.startswith("Offizielle Prüfung XRechnung: ")
    assert "[PEPPOL" not in finding.message  # the id is in `code`, not repeated in the text


def test_zugferd_is_not_checked_against_xrechnung_rules(invoice: Invoice) -> None:
    invoice.buyer_reference = ""  # mandatory in XRechnung only (BR-DE-15)
    assert check_xml(build_cii(invoice, "zugferd"), "zugferd") == []


def test_svrl_flags_map_to_severity() -> None:
    svrl = b"""<svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl">
      <svrl:failed-assert id="BR-X-1" flag="fatal"><svrl:text>[BR-X-1]-Must  be
        there.</svrl:text></svrl:failed-assert>
      <svrl:failed-assert id="BR-X-2" flag="warning"><svrl:text>Should be there.</svrl:text></svrl:failed-assert>
      <svrl:successful-report id="BR-X-3" flag="information"><svrl:text>Note.</svrl:text></svrl:successful-report>
    </svrl:schematron-output>"""
    issues = schematron.parse_svrl(svrl, "EN 16931")
    assert [(i.code, i.severity) for i in issues] == [("BR-X-1", "error"), ("BR-X-2", "warning"), ("BR-X-3", "warning")]
    assert issues[0].message == "Offizielle Prüfung EN 16931: Must be there."


def test_check_fails_closed_when_saxon_is_unavailable(invoice: Invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(xml: bytes, name: str) -> bytes:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(schematron, "run_rules", broken)
    issues = schematron.check(build_cii(invoice, "zugferd"), "zugferd")
    assert [(i.code, i.severity) for i in issues] == [("SCHEMATRON", "error")]
    with pytest.raises(NotExportable):
        convert(invoice, "zugferd")


def _rule_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    finding = Issue(code="BR-CO-99", field="", message="Offizielle Prüfung EN 16931: Test.")
    monkeypatch.setattr(convert_module.schematron, "check", lambda xml, profile: [finding])


def test_convert_releases_nothing_with_rule_errors(invoice: Invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    _rule_violation(monkeypatch)
    with pytest.raises(NotExportable) as exc:
        convert(invoice, "xrechnung")
    assert [i.code for i in exc.value.issues] == ["BR-CO-99"]


def test_validate_and_export_endpoints_report_rule_errors(
    client: TestClient, invoice: Invoice, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rule_violation(monkeypatch)
    body = invoice.model_dump(mode="json")
    r = client.post("/api/validate", json={"invoice": body, "profile": "xrechnung"})
    assert r.status_code == 200
    assert "BR-CO-99" in [i["code"] for i in r.json()["issues"]]
    r = client.post("/api/export/xrechnung", json=body)
    assert r.status_code == 422
    assert [i["code"] for i in r.json()["detail"]["issues"]] == ["BR-CO-99"]


def test_seller_needs_a_vat_id(invoice: Invoice) -> None:
    invoice.seller.vat_id, invoice.seller.tax_number = "", "201/123/45678"
    issues = [i for i in validate(invoice, "zugferd") if i.severity == "error"]
    assert [(i.code, i.field) for i in issues] == [("BR-CO-26", "seller.vat_id")]


def test_xrechnung_needs_payment_instructions(invoice: Invoice) -> None:
    invoice.payment.iban, invoice.payment.means_code = "", ""
    codes = [i.code for i in validate(invoice, "xrechnung") if i.severity == "error"]
    assert codes == ["BR-DE-1"]
