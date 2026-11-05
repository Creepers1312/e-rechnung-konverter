"""Buyer address book.

Every successful export remembers the buyer (keyed by ERP customer number,
otherwise by name). When a new invoice for the same buyer comes in, empty
buyer fields are filled from the address book – most importantly the
e-invoice address and the Leitweg-ID, which PDFs and IDocs usually lack.
Fields that are already filled are never overwritten.
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


def fill(session: Session, invoice: Invoice) -> list[str]:
    """Fill empty buyer fields from the address book. Returns German labels of what was filled."""
    customer = find(session, invoice.buyer)
    if customer is None:
        return []
    stored = Party.model_validate(customer.party)
    filled: list[str] = []
    for path, label in _FILLABLE:
        if not str(_get(invoice.buyer, path)).strip() and str(_get(stored, path)).strip():
            _set(invoice.buyer, path, _get(stored, path))
            filled.append(label)
    if not invoice.buyer_reference.strip() and customer.buyer_reference:
        invoice.buyer_reference = customer.buyer_reference
        filled.append("Leitweg-ID / Käuferreferenz")
    return filled


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
