"""Import of the SAP customer master (CSV) into the buyer address book.

SAP side: an SAP Query (SQ01/SQVI) over KNA1 + address data, scheduled daily
(SM36) with the result written as a CSV file into STAMMDATEN_DIR/inbox.
Each row becomes an address-book entry keyed by the customer number; the
SAP data is authoritative ("source = sap") and is used to complete invoices
that lack buyer data (see customers.fill).

The reader is lenient about what SAP produces: UTF-8 or Windows-1252, ";" ","
tab or "|" separated, "|---|" ruler lines of list output, technical field
names (KUNNR, NAME1, ...) or German headings (Kundennummer, Name, ...).
Which CSV column holds which field is detected from the header and can be
overridden by an admin (Verwaltung -> Kundenstamm).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .countries import country_code
from .db import Customer
from .extraction.rules import POSTCODE_PREFIX
from .model import Address, Contact, Party

# target field -> (German label, header aliases). Aliases are compared normalised
# (lower case, umlauts folded, only letters and digits).
FIELDS: dict[str, tuple[str, list[str]]] = {
    "erp_id": (
        "Kundennummer",
        [
            "kunnr",
            "kunde",
            "kundennummer",
            "kundennr",
            "debitor",
            "debitornr",
            "debitorennummer",
            "customer",
            "customerno",
            "customernumber",
        ],
    ),
    "name": ("Name", ["name1", "name", "firma", "kundenname", "name1firma"]),
    "name2": ("Name 2 / Zusatz", ["name2", "namezusatz", "zusatz"]),
    "street": ("Straße", ["stras", "strasse", "street", "strassehausnummer", "strasseundhausnummer"]),
    "house_no": ("Hausnummer", ["hausn", "housenum1", "hausnummer", "hausnr"]),
    "postcode": ("PLZ", ["pstlz", "plz", "postleitzahl", "postcode", "postcode1"]),
    "city": ("Ort", ["ort01", "ort", "city", "city1", "stadt"]),
    "country": ("Land", ["land1", "land", "country", "laenderschluessel", "landschluessel"]),
    "vat_id": ("USt-IdNr.", ["stceg", "ustidnr", "ustid", "ustidnummer", "umsatzsteuerid", "vatid", "vat"]),
    "electronic_address": (
        "E-Rechnungs-Adresse",
        [
            "erechnungsadresse",
            "erechnungemail",
            "erechnung",
            "rechnungsemail",
            "rechnungsmail",
            "einvoice",
            "smtpaddr",
            "email",
            "mail",
            "emailadresse",
        ],
    ),
    "buyer_reference": ("Leitweg-ID", ["leitwegid", "leitweg", "buyerreference", "kaeuferreferenz"]),
    "contact_name": ("Ansprechpartner", ["ansprechpartner", "kontakt", "contact", "contactname"]),
    "contact_phone": ("Telefon", ["telf1", "telefon", "telefonnummer", "tel", "phone"]),
    "contact_email": ("Kontakt-E-Mail", ["kontaktemail", "kontaktmail", "contactemail", "ansprechpartneremail"]),
}
MAX_WARNINGS = 50


class ImportReport(BaseModel):
    file: str
    at: datetime
    rows: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    missing_einvoice: int = 0  # customers without e-invoice address in this file
    mapping: dict[str, str] = Field(default_factory=dict)  # target field -> CSV column used
    unmapped_columns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ImportError_(ValueError):
    """The file cannot be read as a customer list (no header, no customer number column, ...)."""


def _norm(header: str) -> str:
    h = header.strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        h = h.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", h)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def read_table(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Header + rows of a CSV / SAP list export."""
    lines = [ln for ln in _decode(data).splitlines() if ln.strip() and not re.fullmatch(r"[\s|\-+=]*", ln)]
    if not lines:
        raise ImportError_("Die Datei ist leer.")
    sample = "\n".join(lines[:20])
    delimiter = max(";,\t|", key=sample.count)
    if delimiter == "|":  # SAP list output: "| KUNNR | NAME1 |"
        lines = [ln.strip().strip("|") for ln in lines]
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    table = [[cell.strip() for cell in row] for row in reader]
    header, body = table[0], table[1:]
    if len(header) < 2:
        raise ImportError_("Keine Spaltenüberschriften gefunden (erwartet z. B. KUNNR;NAME1;STRAS;PSTLZ;ORT01).")
    rows = [dict(zip(header, row + [""] * (len(header) - len(row)), strict=False)) for row in body]
    return header, rows


def detect_mapping(headers: list[str], overrides: dict[str, str] | None = None) -> dict[str, str]:
    """target field -> CSV header. Admin overrides win; the rest is matched by alias."""
    by_norm = {_norm(h): h for h in headers}
    mapping: dict[str, str] = {}
    for target, header in (overrides or {}).items():
        if target in FIELDS and header and _norm(header) in by_norm:
            mapping[target] = by_norm[_norm(header)]
    taken = set(mapping.values())
    for target, (_, aliases) in FIELDS.items():
        if target in mapping:
            continue
        for alias in aliases:
            header = by_norm.get(alias)
            if header and header not in taken:
                mapping[target] = header
                taken.add(header)
                break
    return mapping


def _country(value: str) -> str:
    v = value.strip()
    if not v:
        return "DE"
    if v.upper() in POSTCODE_PREFIX and len(v) <= 2:  # SAP LAND1 "D", "A", "CH"
        return POSTCODE_PREFIX[v.upper()]
    return country_code(v) or POSTCODE_PREFIX.get(v.upper(), v.upper()[:2])


def party_from_row(row: dict[str, str], mapping: dict[str, str]) -> tuple[Party, str]:
    """(Party, Leitweg-ID) from one CSV row."""

    def get(field: str) -> str:
        header = mapping.get(field)
        return (row.get(header) or "").strip() if header else ""

    street = " ".join(p for p in (get("street"), get("house_no")) if p)
    party = Party(
        name=get("name"),
        address=Address(
            line1=street,
            line2=get("name2"),
            postcode=get("postcode"),
            city=get("city"),
            country_code=_country(get("country")),
        ),
        vat_id=get("vat_id").replace(" ", "").upper(),
        electronic_address=get("electronic_address"),
        contact=Contact(name=get("contact_name"), phone=get("contact_phone"), email=get("contact_email")),
        erp_id=get("erp_id"),
    )
    return party, get("buyer_reference")


def customer_key(erp_id: str) -> str:
    return f"erp:{erp_id.strip().lstrip('0') or '0'}"


def import_customers(
    session: Session, data: bytes, filename: str, overrides: dict[str, str] | None = None, username: str = "sap"
) -> ImportReport:
    """Upsert every row into the address book (caller commits). Raises ImportError_ for unusable files."""
    headers, rows = read_table(data)
    mapping = detect_mapping(headers, overrides)
    if "erp_id" not in mapping:
        raise ImportError_(
            "Keine Spalte für die Kundennummer gefunden (erwartet z. B. „KUNNR“ oder „Kundennummer“). "
            f"Gefundene Spalten: {', '.join(headers)}"
        )
    report = ImportReport(
        file=filename,
        at=datetime.now(UTC),
        rows=len(rows),
        mapping=mapping,
        unmapped_columns=[h for h in headers if h not in mapping.values()],
    )

    def warn(text: str) -> None:
        if len(report.warnings) < MAX_WARNINGS:
            report.warnings.append(text)

    existing = {c.lookup_key: c for c in session.scalars(select(Customer).where(Customer.lookup_key.like("erp:%")))}
    seen: set[str] = set()
    now = datetime.now(UTC)
    for line_no, row in enumerate(rows, start=2):
        party, leitweg = party_from_row(row, mapping)
        if not party.erp_id:
            report.skipped += 1
            warn(f"Zeile {line_no}: keine Kundennummer – übersprungen.")
            continue
        key = customer_key(party.erp_id)
        if key in seen:
            warn(f"Zeile {line_no}: Kundennummer {party.erp_id} doppelt – die letzte Zeile gilt.")
        seen.add(key)
        if not party.name:
            warn(f"Zeile {line_no}: Kunde {party.erp_id} ohne Namen.")
        if not party.electronic_address:
            report.missing_einvoice += 1

        customer = existing.get(key)
        if customer is None:
            customer = Customer(lookup_key=key)
            session.add(customer)
            existing[key] = customer
            report.created += 1
        else:
            report.updated += 1
        customer.name = party.name[:255]
        customer.party = party.model_dump(mode="json")
        if "buyer_reference" in mapping:  # only touch the Leitweg-ID if the export carries it
            customer.buyer_reference = leitweg[:100]
        customer.source = "sap"
        customer.synced_at = now
        customer.updated_by = username
    return report
