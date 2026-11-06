"""Admin-editable settings, stored in the `settings` table.

- "companies": own companies (seller + payment each; e.g. three companies of a
  group). Older installations stored one "company"; it becomes the first entry.
  Falls back to company.yaml (or the example file) until an admin saves.
- "idoc":    SAP ECC IDoc inbox connector (on/off, output format, interval).
             Falls back to the IDOC_* environment variables. The interval is
             also used for the PDF inbox (one worker loop).
- "pdf_inbox": PDF inbox folder (on/off, output format, fully automatic export).
- "customer_import": SAP customer master import (on/off, SAP data wins, CSV column mapping).
"""

from __future__ import annotations

import os
import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .company import Company, load_company
from .db import get_setting
from .model import Invoice


class IdocSettings(BaseModel):
    enabled: bool = True
    profile: Literal["zugferd", "xrechnung"] = "zugferd"
    interval_seconds: int = Field(10, ge=5, le=3600)


DEFAULT_COMPANY_ID = "firma-1"


def get_companies(session: Session) -> list[Company]:
    """All own companies, the first one is the default."""
    stored = get_setting(session, "companies")
    if stored and stored.get("items"):
        companies = [Company.model_validate(c) for c in stored["items"]]
    else:
        legacy = get_setting(session, "company")
        companies = [Company.model_validate(legacy) if legacy else load_company().model_copy(deep=True)]
    return assign_ids(companies)


def get_company(session: Session, company_id: str | None = None) -> Company:
    """The company with this id, else the default (first) one."""
    companies = get_companies(session)
    return next((c for c in companies if company_id and c.id == company_id), companies[0])


def _slug(text: str) -> str:
    s = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40]


def assign_ids(companies: list[Company]) -> list[Company]:
    """Give every company a unique, stable id (kept once set; new ones from the name)."""
    taken: set[str] = set()
    for i, c in enumerate(companies):
        if not c.id or c.id in taken:
            base = _slug(c.seller.name) or f"firma-{i + 1}"
            if i == 0 and not c.id and len(companies) == 1:
                base = DEFAULT_COMPANY_ID  # single company of older installations: links stay valid
            cid, n = base, 2
            while cid in taken:
                cid, n = f"{base}-{n}", n + 1
            c.id = cid
        taken.add(c.id)
    return companies


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def detect_company(text: str, companies: list[Company]) -> Company:
    """Which own company issued this document? Scores identifiers found in the text.

    VAT ID, tax number and IBAN count most (unique), then admin keywords, then the
    name. Without any hit the default (first) company is used.
    """
    if len(companies) == 1:
        return companies[0]
    flat = _norm(text)
    lowered = text.lower()

    def score(c: Company) -> int:
        points = 0
        for ident in (c.seller.vat_id, c.seller.tax_number, c.payment.iban):
            if ident and len(_norm(ident)) >= 6 and _norm(ident) in flat:
                points += 10
        points += sum(5 for k in c.keywords if k.strip() and k.strip().lower() in lowered)
        if c.seller.name and c.seller.name.lower() in lowered:
            points += 3
        return points

    best = max(companies, key=score)
    return best if score(best) > 0 else companies[0]


def company_for_invoice(invoice: Invoice, companies: list[Company]) -> Company:
    """The own company an (already extracted) invoice belongs to, by seller VAT ID or name."""
    vat = _norm(invoice.seller.vat_id)
    for c in companies:
        if vat and _norm(c.seller.vat_id) == vat:
            return c
    name = invoice.seller.name.strip().lower()
    return next((c for c in companies if name and c.seller.name.strip().lower() == name), companies[0])


def idoc_defaults_from_env() -> IdocSettings:
    return IdocSettings(
        profile="xrechnung" if os.environ.get("IDOC_PROFILE") == "xrechnung" else "zugferd",
        interval_seconds=max(5, int(float(os.environ.get("IDOC_INTERVAL", "10")))),
    )


def get_idoc_settings(session: Session) -> IdocSettings:
    stored = get_setting(session, "idoc")
    return IdocSettings.model_validate(stored) if stored else idoc_defaults_from_env()


class PdfInboxSettings(BaseModel):
    enabled: bool = True
    profile: Literal["zugferd", "xrechnung"] = "zugferd"
    # True: invoices without errors are converted right away; False: every PDF goes to "Zu prüfen"
    auto_export: bool = True
    # True: every PDF goes through text recognition (scanner inbox whose own text layer is poor)
    force_ocr: bool = False


def get_pdf_inbox_settings(session: Session) -> PdfInboxSettings:
    stored = get_setting(session, "pdf_inbox")
    return PdfInboxSettings.model_validate(stored) if stored else PdfInboxSettings()


class CustomerImportSettings(BaseModel):
    enabled: bool = True
    # True: buyer data from the SAP customer master replaces what was read from the PDF (OCR can misread)
    master_wins: bool = True
    # target field -> CSV column header; empty/missing = detect automatically (see customer_import.FIELDS)
    mapping: dict[str, str] = Field(default_factory=dict)


def get_customer_import_settings(session: Session) -> CustomerImportSettings:
    stored = get_setting(session, "customer_import")
    return CustomerImportSettings.model_validate(stored) if stored else CustomerImportSettings()
