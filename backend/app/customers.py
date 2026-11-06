"""Buyer address book.

Two sources fill it:
- the SAP customer master (customer_import.py, source "sap") – authoritative;
- every successful export remembers its buyer (source "rechnung").

When an invoice comes in, the buyer is looked up by customer number (then by
name) and missing fields are completed – most importantly the e-invoice
address and the Leitweg-ID, which PDFs and IDocs usually lack. With
"SAP-Stammdaten haben Vorrang" (default), data from the SAP master also
replaces values read from the document, since OCR can misread an address.
Data learned from invoices never overwrites what the document says.
"""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .db import Customer
from .model import Invoice, Party


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def _keys(party: Party) -> list[str]:
    keys = []
    if party.erp_id.strip():
        keys.append(f"erp:{party.erp_id.strip().lstrip('0') or '0'}")
    if party.name.strip():
        keys.append(f"name:{_norm_name(party.name)}"[:255])
    return keys


def find(session: Session, party: Party) -> Customer | None:
    for key in _keys(party):  # ERP number first: more reliable than the name
        hit = session.scalar(select(Customer).where(Customer.lookup_key == key))
        if hit is not None:
            return hit
    # A customer stored under its ERP number is also found by name (e.g. a PDF without customer number).
    if party.name.strip():
        stmt = (
            select(Customer)
            .where(func.lower(Customer.name) == _norm_name(party.name))
            .order_by(Customer.updated_at.desc())
            .limit(1)
        )
        return session.scalar(stmt)
    return None


def remember(session: Session, invoice: Invoice, username: str) -> None:
    """Store/refresh the buyer of an exported invoice (caller commits)."""
    buyer = invoice.buyer
    keys = _keys(buyer)
    if not keys:
        return
    customer = find(session, buyer)
    if customer is not None and customer.source == "sap":
        # the SAP master stays authoritative: only add what it does not know
        stored = Party.model_validate(customer.party)
        for path, _ in _FILLABLE:
            if not str(_get(stored, path)).strip() and str(_get(buyer, path)).strip():
                _set(stored, path, _get(buyer, path))
        customer.party = stored.model_dump(mode="json")
        if not customer.buyer_reference and invoice.buyer_reference:
            customer.buyer_reference = invoice.buyer_reference[:100]
        return
    if customer is None:
        customer = Customer(lookup_key=keys[0])
        session.add(customer)
    customer.name = buyer.name[:255]
    customer.party = buyer.model_dump(mode="json")
    customer.buyer_reference = invoice.buyer_reference[:100]
    customer.updated_by = username


# (attribute path on Party, German label shown to the user)
_FILLABLE = [
    ("name", "Name"),
    ("electronic_address", "E-Rechnungs-Adresse"),
    ("vat_id", "USt-IdNr."),
    ("erp_id", "Kundennummer"),
    ("address.line1", "Straße"),
    ("address.line2", "Adresszusatz"),
    ("address.postcode", "PLZ"),
    ("address.city", "Ort"),
    ("contact.name", "Kontakt"),
    ("contact.phone", "Telefon"),
    ("contact.email", "Kontakt-E-Mail"),
]


def _get(obj: object, path: str) -> str:
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj  # type: ignore[return-value]


def _set(obj: object, path: str, value: str) -> None:
    *head, last = path.split(".")
    for part in head:
        obj = getattr(obj, part)
    setattr(obj, last, value)


class FillResult(list):
    """German labels of the buyer fields that were filled or corrected; `.source` = "sap" | "rechnung" | ""."""

    source: str = ""


def fill(session: Session, invoice: Invoice, master_wins: bool | None = None) -> FillResult:
    """Complete the buyer from the address book. Returns the labels of what changed."""
    result = FillResult()
    customer = find(session, invoice.buyer)
    if customer is None:
        return result
    result.source = customer.source
    if master_wins is None:
        from .settings import get_customer_import_settings  # late import: settings -> db -> ...

        master_wins = get_customer_import_settings(session).master_wins
    overwrite = master_wins and customer.source == "sap"
    stored = Party.model_validate(customer.party)
    for path, label in _FILLABLE:
        new = str(_get(stored, path)).strip()
        old = str(_get(invoice.buyer, path)).strip()
        if new and (not old or (overwrite and old != new and path != "erp_id")):
            _set(invoice.buyer, path, new)
            result.append(label)
    if overwrite and stored.address.country_code and invoice.buyer.address.country_code != stored.address.country_code:
        invoice.buyer.address.country_code = stored.address.country_code
        result.append("Land")
    replace_ref = not invoice.buyer_reference.strip() or overwrite
    if customer.buyer_reference and replace_ref and invoice.buyer_reference != customer.buyer_reference:
        invoice.buyer_reference = customer.buyer_reference
        result.append("Leitweg-ID / Käuferreferenz")
    return result


def note(result: FillResult) -> list[str]:
    """Hint for the user, e.g. ["Aus dem SAP-Kundenstamm übernommen: Name, Straße."]."""
    if not result:
        return []
    origin = "dem SAP-Kundenstamm übernommen" if result.source == "sap" else "dem Käufer-Adressbuch ergänzt"
    return [f"Aus {origin}: {', '.join(result)}."]


def search(session: Session, query: str, limit: int = 10) -> list[Customer]:
    """Customers whose name or ERP number contains `query` (case-insensitive)."""
    term = _norm_name(query)
    stmt = select(Customer).order_by(Customer.updated_at.desc()).limit(limit)
    if term:
        stmt = stmt.where(
            or_(
                # lower() on both sides: PostgreSQL's LIKE is case-sensitive (SQLite's is not)
                Customer.lookup_key.contains(term, autoescape=True),
                func.lower(Customer.name).contains(term, autoescape=True),
            )
        )
    return list(session.scalars(stmt))
