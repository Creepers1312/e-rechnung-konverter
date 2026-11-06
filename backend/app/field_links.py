"""Click-to-link fields: a user clicks a value in the PDF preview, then the form field.

The spot is remembered per own company (`FieldLink`); the next invoice of that
company reads the field from the same spot, on top of the rule-based recognition.
Only header fields are linkable – line items vary in number and position.

Robustness against layout shifts: a link stores the label next to (or above) the
value ("anchor"). If that label is found again, the box moves along with it.
Values may be longer than at linking time: words that start inside the box count,
and a phrase continues to the right as long as the words are close together.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import FieldLink
from .extraction.pdf_text import PageWords, PdfText, Word
from .extraction.rules import DATE, parse_date
from .model import Invoice

# field path in the Invoice model -> (German label, kind)
LINKABLE: dict[str, tuple[str, str]] = {
    "number": ("Rechnungsnummer", "text"),
    "issue_date": ("Rechnungsdatum", "date"),
    "delivery_date": ("Liefer-/Leistungsdatum", "date"),
    "buyer_reference": ("Leitweg-ID / Käuferreferenz", "text"),
    "order_reference": ("Bestellnummer", "text"),
    "note": ("Bemerkung", "text"),
    "buyer.name": ("Käufer: Name", "text"),
    "buyer.address.line1": ("Käufer: Straße", "text"),
    "buyer.address.line2": ("Käufer: Adresszusatz", "text"),
    "buyer.address.postcode": ("Käufer: PLZ", "text"),
    "buyer.address.city": ("Käufer: Ort", "text"),
    "buyer.vat_id": ("Käufer: USt-IdNr.", "vat"),
    "buyer.electronic_address": ("Käufer: E-Rechnungs-Adresse", "text"),
    "buyer.erp_id": ("Käufer: Kundennummer", "text"),
    "payment.terms": ("Zahlungsbedingungen", "text"),
    "payment.due_date": ("Fälligkeit", "date"),
}
MAX_LINE_GAP = 2.5  # words on the same line: tops differ by less than this (points)
TOLERANCE = 2.0


class LinkIn(BaseModel):
    company_id: str = Field(min_length=1, max_length=50)
    field: str
    page: int = Field(1, ge=1, le=50)
    x0: float
    top: float
    x1: float
    bottom: float
    anchor_text: str = Field("", max_length=255)
    anchor_x0: float | None = None
    anchor_top: float | None = None
    sample: str = Field("", max_length=255)


class LinkOut(LinkIn):
    id: int
    label: str
    updated_by: str = ""

    model_config = {"from_attributes": True}


def to_out(link: FieldLink) -> LinkOut:
    return LinkOut.model_validate(
        {**{c: getattr(link, c) for c in LinkIn.model_fields}, "id": link.id, "updated_by": link.updated_by}
        | {"label": LINKABLE.get(link.field, (link.field, ""))[0]}
    )


def links_of(session: Session, company_id: str) -> list[FieldLink]:
    return list(session.scalars(select(FieldLink).where(FieldLink.company_id == company_id).order_by(FieldLink.field)))


def upsert(session: Session, data: LinkIn, username: str) -> FieldLink:
    if data.field not in LINKABLE:
        raise ValueError(f"Das Feld „{data.field}“ kann nicht verknüpft werden.")
    link = session.scalar(
        select(FieldLink).where(FieldLink.company_id == data.company_id, FieldLink.field == data.field)
    )
    if link is None:
        link = FieldLink(company_id=data.company_id, field=data.field)
        session.add(link)
    x0, x1 = sorted((data.x0, data.x1))
    top, bottom = sorted((data.top, data.bottom))
    for key, value in data.model_dump(exclude={"company_id", "field"}).items():
        setattr(link, key, value)
    link.x0, link.x1, link.top, link.bottom = x0, x1, top, bottom
    link.updated_by = username
    return link


# --- reading a linked value from a PDF ----------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _lines(words: list[Word]) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (round(w.top), w.x0)):
        for row in rows:
            if abs(row[0].top - w.top) < MAX_LINE_GAP:
                row.append(w)
                break
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w.x0)
    rows.sort(key=lambda r: r[0].top)
    return rows


def _close(a: Word, b: Word) -> bool:
    """b continues a's phrase: small horizontal gap relative to the font size."""
    return 0 <= b.x0 - a.x1 < max(4.0, 0.6 * (a.bottom - a.top))


def _find_anchor(words: list[Word], wanted: str) -> list[tuple[float, float]]:
    """Positions (x0, top) where the label text occurs as consecutive words on one line.
    Works whether or not the label is printed close to its value ("Nr.: 4711")."""
    hits = []
    for row in _lines(words):
        for start in range(len(row)):
            text = ""
            for end in range(start, len(row)):
                text = f"{text} {row[end].text}".strip().lower()
                if text == wanted:
                    hits.append((row[start].x0, min(w.top for w in row[start : end + 1])))
                    break
                if not wanted.startswith(text):
                    break
    return hits


def _anchor_offset(link: FieldLink, page: PageWords) -> tuple[float, float]:
    """Shift of the layout since linking, measured at the anchor label (0, 0 if not found)."""
    if not link.anchor_text or link.anchor_x0 is None or link.anchor_top is None:
        return 0.0, 0.0
    hits = _find_anchor(page.words, _norm(link.anchor_text))
    if not hits:
        return 0.0, 0.0
    ax, ay = link.anchor_x0, link.anchor_top
    x, y = min(hits, key=lambda h: abs(h[0] - ax) + abs(h[1] - ay))
    return x - ax, y - ay


def read_value(link: FieldLink, pages: list[PageWords]) -> str:
    page = next((p for p in pages if p.page == link.page), None)
    if page is None:
        return ""
    dx, dy = _anchor_offset(link, page)
    x0, x1 = link.x0 + dx - TOLERANCE, link.x1 + dx + TOLERANCE
    top, bottom = link.top + dy - TOLERANCE, link.bottom + dy + TOLERANCE
    inside = [w for w in page.words if x0 <= w.x0 <= x1 and top <= (w.top + w.bottom) / 2 <= bottom]
    if not inside:
        return ""
    lines_out = []
    for row in _lines(inside):
        # continue the phrase to the right (the value may be longer than when it was linked)
        line_words = [w for w in page.words if abs(w.top - row[0].top) < MAX_LINE_GAP and w.x0 >= row[0].x0]
        line_words.sort(key=lambda w: w.x0)
        taken = [row[0]]
        for w in line_words[1:]:
            if w in row or _close(taken[-1], w):
                taken.append(w)
            elif w.x0 > row[-1].x1:
                break
        lines_out.append(" ".join(w.text for w in taken))
    return " ".join(lines_out).strip()


def convert(field: str, raw: str) -> str | date | None:
    kind = LINKABLE[field][1]
    if kind == "date":
        m = re.search(DATE, raw)
        if m:
            return parse_date(m.group(0))
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            return None
    if kind == "vat":
        return raw.replace(" ", "").upper()
    return raw.strip()


def set_field(invoice: Invoice, path: str, value: object) -> None:
    *parents, last = path.split(".")
    target: object = invoice
    for p in parents:
        target = getattr(target, p)
    setattr(target, last, value)


def apply_links(invoice: Invoice, pdf: PdfText, links: list[FieldLink]) -> list[str]:
    """Overwrite linked fields with what is printed at their spot. Returns the labels filled."""
    if not links or not pdf.text.strip():
        return []
    pages = pdf.pages(max_pages=max(link.page for link in links))
    filled = []
    for link in links:
        if link.field not in LINKABLE:
            continue
        value = convert(link.field, read_value(link, pages))
        if value:
            set_field(invoice, link.field, value)
            filled.append(LINKABLE[link.field][0])
    return filled
