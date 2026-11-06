"""Neutral invoice model (subset of EN 16931).

Every input source (PDF extraction, SAP ECC IDoc, ...) produces an `Invoice`;
every output (XRechnung, ZUGFeRD) consumes one. Sources and outputs never talk
to each other directly.

Amounts are `Decimal` — never float. Totals are always *computed* from the line
items (`compute_totals`), never trusted from the input.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field

TWO_PLACES = Decimal("0.01")


def parse_decimal(value: object) -> object:
    """Accept German number notation ("1.234,56") in addition to "1234.56"."""
    if isinstance(value, str):
        s = value.strip().replace("€", "").replace("%", "").replace(" ", "")
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        return s or "0"
    return value


Amount = Annotated[Decimal, BeforeValidator(parse_decimal)]


def round2(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class Address(BaseModel):
    line1: str = ""  # street + number (BT-35 / BT-50)
    line2: str = ""
    postcode: str = ""  # BT-38 / BT-53
    city: str = ""  # BT-37 / BT-52
    country_code: str = "DE"  # ISO 3166-1 alpha-2 (BT-40 / BT-55)


class Contact(BaseModel):
    name: str = ""  # BT-41
    phone: str = ""  # BT-42
    email: str = ""  # BT-43


class Party(BaseModel):
    name: str = ""  # BT-27 / BT-44
    address: Address = Field(default_factory=Address)
    vat_id: str = ""  # USt-IdNr. (BT-31 / BT-48)
    tax_number: str = ""  # Steuernummer (BT-32), seller only
    electronic_address: str = ""  # e-mail used as BT-34 / BT-49 (scheme EM)
    contact: Contact = Field(default_factory=Contact)
    erp_id: str = ""  # e.g. SAP customer number, informational only


# UNTDID 5305 VAT category codes used here:
# S = standard rate, Z = zero rated, E = exempt, AE = reverse charge,
# K = intra-community supply, G = export outside the EU.
VatCategory = Literal["S", "Z", "E", "AE", "K", "G"]


class LineItem(BaseModel):
    id: str = "1"  # BT-126
    name: str = ""  # BT-153
    seller_item_id: str = ""  # BT-155, e.g. SAP material number
    quantity: Amount = Decimal("1")  # BT-129
    unit_code: str = "H87"  # UN/ECE Rec 20 (H87 piece, HUR hour, C62 one, ...)
    unit_price: Amount = Decimal("0")  # net price (BT-146) for `price_base_quantity` units
    # BT-149: the price refers to this many units, e.g. "Preis / 100 KG" -> 100 (common in SAP: PEINH)
    price_base_quantity: Amount = Decimal("1")
    vat_category: VatCategory = "S"  # BT-151
    vat_rate: Amount = Decimal("19")  # BT-152, percent

    @property
    def net_amount(self) -> Decimal:  # BT-131
        base = self.price_base_quantity if self.price_base_quantity > 0 else Decimal("1")
        return round2(self.quantity * self.unit_price / base)


class PaymentInfo(BaseModel):
    means_code: str = "58"  # UNTDID 4461: 58 = SEPA credit transfer
    iban: str = ""  # BT-84
    bic: str = ""  # BT-86
    account_holder: str = ""  # BT-85
    terms: str = ""  # BT-20
    due_date: date | None = None  # BT-9


class Invoice(BaseModel):
    number: str = ""  # BT-1
    issue_date: date | None = None  # BT-2
    type_code: str = "380"  # BT-3: 380 commercial invoice, 381 credit note
    currency: str = "EUR"  # BT-5
    buyer_reference: str = ""  # BT-10, Leitweg-ID for public authorities
    order_reference: str = ""  # BT-13
    delivery_date: date | None = None  # BT-72
    note: str = ""  # BT-22
    seller: Party = Field(default_factory=Party)
    buyer: Party = Field(default_factory=Party)
    lines: list[LineItem] = Field(default_factory=list)
    payment: PaymentInfo = Field(default_factory=PaymentInfo)


class TaxBreakdown(BaseModel):
    category: VatCategory
    rate: Decimal
    basis: Decimal  # BT-116
    amount: Decimal  # BT-117


class Totals(BaseModel):
    line_total: Decimal  # BT-106
    tax_basis_total: Decimal  # BT-109
    tax_total: Decimal  # BT-110
    grand_total: Decimal  # BT-112
    due_payable: Decimal  # BT-115
    breakdown: list[TaxBreakdown]


def compute_totals(invoice: Invoice) -> Totals:
    """EN 16931 calculation: tax is computed per (category, rate) group."""
    groups: dict[tuple[str, Decimal], Decimal] = {}
    for line in invoice.lines:
        rate = line.vat_rate if line.vat_category == "S" else Decimal("0")
        key = (line.vat_category, rate)
        groups[key] = groups.get(key, Decimal("0")) + line.net_amount

    breakdown = [
        TaxBreakdown(
            category=cat,  # type: ignore[arg-type]
            rate=rate,
            basis=round2(basis),
            amount=round2(basis * rate / Decimal("100")),
        )
        for (cat, rate), basis in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    ]
    line_total = round2(sum((line.net_amount for line in invoice.lines), Decimal("0")))
    tax_total = round2(sum((b.amount for b in breakdown), Decimal("0")))
    grand = line_total + tax_total
    return Totals(
        line_total=line_total,
        tax_basis_total=line_total,
        tax_total=tax_total,
        grand_total=grand,
        due_payable=grand,
        breakdown=breakdown,
    )
