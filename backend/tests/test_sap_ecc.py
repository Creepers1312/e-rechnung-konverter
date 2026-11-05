from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.company import Company
from app.erp.base import ErpAdapter
from app.erp.sap_ecc_idoc import SapEccIdocFolderAdapter, parse_invoic02
from app.model import compute_totals

SAMPLE = Path(__file__).parent / "fixtures" / "invoic02_sample.xml"


def test_parse_invoic02(company: Company) -> None:
    inv = parse_invoic02(SAMPLE.read_bytes(), company)
    assert inv.number == "0090001234"
    assert inv.issue_date == date(2026, 11, 4)
    assert inv.delivery_date == date(2026, 10, 31)
    assert inv.order_reference == "PO-778"
    assert inv.buyer.name == "Kunde AG Einkauf"
    assert inv.buyer.vat_id == "DE987654321"
    assert inv.buyer.erp_id == "0000010042"
    assert inv.seller.contact.email  # own master data wins over the IDoc
    assert inv.payment.terms == "Zahlbar innerhalb 14 Tagen netto"

    assert [(line.id, line.name, line.unit_code) for line in inv.lines] == [
        ("10", "Wartungspaket", "H87"),
        ("20", "Techniker vor Ort", "HUR"),
    ]
    # 3 × 33.333333 must still give SAP's 100.00
    assert [line.net_amount for line in inv.lines] == [Decimal("100.00"), Decimal("237.50")]
    assert compute_totals(inv).grand_total == Decimal("401.63")


def test_rejects_non_idoc(company: Company) -> None:
    with pytest.raises(ValueError):
        parse_invoic02(b"<foo/>", company)


def test_folder_adapter(tmp_path: Path, company: Company) -> None:
    shutil.copy(SAMPLE, tmp_path / "a.xml")
    adapter = SapEccIdocFolderAdapter(tmp_path, company)
    assert isinstance(adapter, ErpAdapter)
    assert adapter.list_new_invoices() == ["a.xml"]
    assert adapter.get_invoice("a.xml").number == "0090001234"
    with pytest.raises(ValueError):
        adapter.get_invoice("../secret.xml")
