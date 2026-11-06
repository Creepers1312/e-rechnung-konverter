"""API for own companies (selection in the editor) and click-to-link fields."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import field_links
from .auth import current_user
from .company import Company
from .countries import countries
from .db import FieldLink, User, audit, get_session
from .ocr import text_of_pdf
from .settings import get_companies

router = APIRouter(prefix="/api", tags=["companies"])

MAX_UPLOAD = 20 * 1024 * 1024


class WordOut(BaseModel):
    text: str
    x0: float
    top: float
    x1: float
    bottom: float


class PageOut(BaseModel):
    page: int
    width: float
    height: float
    words: list[WordOut]


class PdfWords(BaseModel):
    pages: list[PageOut]
    ocr: bool = False
    warning: str = ""


class LinkableField(BaseModel):
    path: str
    label: str


class Country(BaseModel):
    code: str
    name: str


@router.get("/countries", response_model=list[Country])
def list_countries(_: User = Depends(current_user)) -> list[Country]:
    """ISO country codes with German names (country picker)."""
    return [Country(code=c["code"], name=c["name"]) for c in countries()]


@router.get("/companies", response_model=list[Company])
def companies(_: User = Depends(current_user), session: Session = Depends(get_session)) -> list[Company]:
    """Own companies (for the selection in the editor); the first one is the default."""
    return get_companies(session)


@router.post("/pdf/words", response_model=PdfWords)
async def pdf_words(
    file: UploadFile = File(...), force_ocr: bool = Form(False), _: User = Depends(current_user)
) -> PdfWords:
    """Words with positions of an uploaded PDF (scans via OCR) for click-to-link in the preview."""
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Datei zu groß.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Keine PDF-Datei.")
    pdf, problem = text_of_pdf(data, file.filename or "scan.pdf", force=force_ocr)
    pages = [PageOut.model_validate(p, from_attributes=True) for p in pdf.pages()]
    if not any(p.words for p in pages) and not problem:
        problem = "Im PDF wurde kein Text gefunden."
    return PdfWords(pages=pages, ocr=pdf.ocr, warning=problem or "")


@router.get("/field-links/fields", response_model=list[LinkableField])
def linkable_fields(_: User = Depends(current_user)) -> list[LinkableField]:
    """Form fields that can be linked to a spot in the PDF."""
    return [LinkableField(path=p, label=label) for p, (label, _kind) in field_links.LINKABLE.items()]


@router.get("/field-links", response_model=list[field_links.LinkOut])
def list_links(
    company_id: str = Query(..., max_length=50),
    _: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[field_links.LinkOut]:
    """Linked fields of one own company."""
    return [field_links.to_out(link) for link in field_links.links_of(session, company_id)]


@router.put("/field-links", response_model=field_links.LinkOut)
def save_link(
    body: field_links.LinkIn, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> field_links.LinkOut:
    """Remember where a field is printed on this company's invoices (replaces an earlier link)."""
    if body.company_id not in {c.id for c in get_companies(session)}:
        raise HTTPException(404, "Firma nicht gefunden.")
    try:
        link = field_links.upsert(session, body, user.username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.flush()
    audit(session, user.username, "field_linked", company=body.company_id, field=body.field, sample=body.sample[:60])
    session.commit()
    return field_links.to_out(link)


@router.delete("/field-links/{link_id}", status_code=204)
def delete_link(link_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)) -> Response:
    """Remove a field link (the field is recognised by the rules again)."""
    link = session.get(FieldLink, link_id)
    if link is None:
        raise HTTPException(404, "Verknüpfung nicht gefunden.")
    audit(session, user.username, "field_unlinked", company=link.company_id, field=link.field)
    session.delete(link)
    session.commit()
    return Response(status_code=204)
