"""The "*" in the form (`required_fields`) must match what `validate` actually rejects."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.model import Invoice
from app.validation import required_fields, validate

# every input of the invoice form (frontend/src/InvoiceForm.tsx)
PARTY = [
    "name",
    "address.line1",
    "address.line2",
    "address.postcode",
    "address.city",
    "address.country_code",
    "vat_id",
    "electronic_address",
]
FORM_FIELDS = (
    ["number", "issue_date", "delivery_date", "buyer_reference", "order_reference", "note"]
    + [f"buyer.{p}" for p in PARTY + ["erp_id"]]
    + [f"seller.{p}" for p in PARTY + ["tax_number", "contact.name", "contact.phone", "contact.email"]]
    + ["lines.0.name", "lines.0.seller_item_id"]
    + ["payment.iban", "payment.bic", "payment.account_holder", "payment.due_date", "payment.terms"]
)
DATES = {"issue_date", "delivery_date", "payment.due_date"}


def _clear(invoice: Invoice, path: str) -> Invoice:
    inv = invoice.model_copy(deep=True)
    *parents, last = path.split(".")
    obj: object = inv
    for part in parents:
        obj = obj[int(part)] if part.isdigit() else getattr(obj, part)  # type: ignore[index]
    setattr(obj, last, None if path in DATES else "")
    return inv


def _reverse_charge(inv: Invoice) -> None:
    for line in inv.lines:
        line.vat_category, line.vat_rate = "AE", Decimal("0")
    inv.buyer.vat_id, inv.buyer.address.country_code, inv.buyer.address.postcode = "ATU12345678", "AT", "1010"


def _export_without_postcode(inv: Invoice) -> None:
    for line in inv.lines:
        line.vat_category, line.vat_rate = "G", Decimal("0")
    inv.buyer.address.country_code, inv.buyer.address.postcode = "CR", ""


VARIANTS: dict[str, Callable[[Invoice], None]] = {
    "standard": lambda inv: None,
    "reverse_charge": _reverse_charge,
    "export_without_postcode": _export_without_postcode,
}


@pytest.mark.parametrize("profile", ["zugferd", "xrechnung"])
@pytest.mark.parametrize("variant", VARIANTS)
def test_star_means_error_when_empty(invoice: Invoice, variant: str, profile: str) -> None:
    VARIANTS[variant](invoice)
    if variant == "export_without_postcode" and profile == "xrechnung":
        invoice.buyer.address.postcode = "10101"  # XRechnung always needs one
    assert [i for i in validate(invoice, profile) if i.severity == "error"] == []  # type: ignore[arg-type]
    required = set(required_fields(invoice, profile))  # type: ignore[arg-type]
    form = FORM_FIELDS + [
        f"lines.{i}.{key}" for i in range(1, len(invoice.lines)) for key in ("name", "seller_item_id")
    ]
    assert required <= set(form)
    for path in form:
        errors = [
            i.field
            for i in validate(_clear(invoice, path), profile)  # type: ignore[arg-type]
            if i.severity == "error" and i.field and (path == i.field or path.startswith(i.field + "."))
        ]
        if path in required:
            assert errors, f"{path} is marked mandatory, but an empty value passes"
        else:
            assert not errors, f"{path} is not marked mandatory, but an empty value fails: {errors}"


def test_zugferd_needs_less_than_xrechnung(invoice: Invoice) -> None:
    zugferd, xrechnung = set(required_fields(invoice, "zugferd")), set(required_fields(invoice, "xrechnung"))
    assert zugferd < xrechnung
    assert {"buyer_reference", "buyer.electronic_address", "seller.contact.phone"} <= xrechnung - zugferd
    assert "buyer.vat_id" not in zugferd  # only for reverse charge / intra-community supplies


def test_validate_endpoint_returns_required_fields(client: TestClient, invoice: Invoice) -> None:
    r = client.post("/api/validate", json={"invoice": invoice.model_dump(mode="json"), "profile": "zugferd"})
    assert r.status_code == 200
    assert r.json()["required"] == required_fields(invoice, "zugferd")


@pytest.mark.parametrize("profile", ["zugferd", "xrechnung"])
def test_german_vat_law_needs_delivery_date_and_street(invoice: Invoice, profile: str) -> None:
    """§ 14 Abs. 4 UStG: optional in EN 16931, mandatory for an invoice that allows input tax deduction."""
    invoice.delivery_date = None
    invoice.seller.address.line1 = ""
    invoice.buyer.address.line1 = ""
    errors = [(i.code, i.field) for i in validate(invoice, profile) if i.severity == "error"]  # type: ignore[arg-type]
    assert errors == [
        ("UStG-14-6", "delivery_date"),
        ("UStG-14-1", "seller.address.line1"),
        ("UStG-14-1", "buyer.address.line1"),
    ]
