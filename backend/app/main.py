"""HTTP API. Every endpoint except /api/health and /api/auth/login requires a
logged-in user (see auth.py); admin endpoints live in admin.py.

The client keeps the invoice and the PDF while editing; every export is
archived in the database together with the user who made it."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from . import customers, review
from .admin import router as admin_router
from .auth import bootstrap_admin, current_user
from .companies_api import router as companies_router
from .company import Company
from .convert import EInvoice, NotExportable, convert
from .db import Document, ReviewItem, User, archive, audit, get_engine, get_session, get_setting, search_documents
from .docs import router as docs_router
from .erp.sap_ecc_idoc import parse_invoic02, pick_company
from .export.cii import Profile, build_cii
from .export.zugferd import check_xsd
from .extraction.rules import ExtractionResult
from .model import Invoice, Party, Totals, compute_totals
from .ocr import text_of_pdf
from .recognize import recognize_pdf
from .settings import company_for_invoice, get_companies, get_company
from .stats import router as stats_router
from .validation import Issue, StatedTotals, validate
from .worker import STATUS_KEY

MAX_UPLOAD = 20 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    engine = get_engine()  # creates tables; fails fast if the database is unreachable
    with sessionmaker(engine)() as session:
        bootstrap_admin(session)
    yield


app = FastAPI(title="E-Rechnung Konverter", version="0.3.0", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(stats_router)
app.include_router(docs_router)
app.include_router(companies_router)


class ValidateRequest(BaseModel):
    invoice: Invoice
    profile: Profile = "zugferd"
    stated_totals: StatedTotals | None = None


class ValidateResponse(BaseModel):
    totals: Totals
    issues: list[Issue]


class DocumentInfo(BaseModel):
    id: int
    created_at: datetime
    created_by: str
    source: str
    source_name: str
    profile: str
    invoice_number: str
    buyer_name: str
    grand_total: Decimal
    currency: str
    filename: str

    model_config = {"from_attributes": True}


class DocumentPage(BaseModel):
    items: list[DocumentInfo]
    total: int


class ZipRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


class ImportResult(BaseModel):
    invoice: Invoice
    notes: list[str] = Field(default_factory=list)
    company_id: str = ""


class ReviewInfo(BaseModel):
    id: int
    created_at: datetime
    status: str
    source: str
    source_name: str
    profile: str
    invoice_number: str
    buyer_name: str
    issues: list[dict]
    resolved_by: str
    document_id: int | None

    model_config = {"from_attributes": True}


class ReviewDetail(ReviewInfo):
    invoice: Invoice
    stated_totals: StatedTotals | None
    warnings: list[str]
    notes: list[str]
    template_name: str
    has_pdf: bool
    company_id: str = ""


class Dashboard(BaseModel):
    review_open: int
    documents_today: int
    documents_total: int
    worker: dict  # last report of the inbox worker (see worker.report_status)


class CustomerInfo(BaseModel):
    id: int
    name: str
    party: Party
    buyer_reference: str
    updated_at: datetime

    model_config = {"from_attributes": True}


def _filled_note(result: customers.FillResult) -> list[str]:
    return customers.note(result)


async def _read(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Datei ist größer als 20 MB.")
    return data


def _convert_and_archive(
    session: Session,
    user: User,
    invoice: Invoice,
    profile: Profile,
    source_pdf: bytes | None,
    source: str,
    source_name: str,
    review_id: int | None = None,
) -> Response:
    item = _open_review_item(session, review_id) if review_id is not None else None
    try:
        result: EInvoice = convert(invoice, profile, source_pdf)
    except NotExportable as exc:
        raise HTTPException(
            422, {"message": "Rechnung ist nicht vollständig.", "issues": [i.model_dump() for i in exc.issues]}
        ) from exc
    audit(session, user.username, "export", invoice_number=invoice.number, profile=profile)
    customers.remember(session, invoice, user.username)  # committed together with the archive entry
    if item is not None and source_pdf is None and item.pdf and profile == "zugferd":
        source_pdf = item.pdf  # embed into the original scan, as for a direct upload
        result = convert(invoice, profile, source_pdf)
    doc = archive(
        session,
        invoice,
        profile=profile,
        source=source,
        source_name=source_name,
        filename=result.filename,
        media_type=result.media_type,
        content=result.content,
        created_by=user.username,
    )
    if item is not None:
        review.resolve(session, item, status="erledigt", username=user.username, document_id=doc.id)
        session.commit()
    return Response(
        result.content,
        media_type=result.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "X-Document-Id": str(doc.id),
        },
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/company", response_model=Company)
def company(
    company_id: str | None = Query(None, max_length=50),
    _: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Company:
    return get_company(session, company_id)


@app.post("/api/extract", response_model=ExtractionResult)
async def extract_pdf(
    file: UploadFile = File(...),
    company_id: str = Form(""),
    _: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ExtractionResult:
    data = await _read(file)
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Keine PDF-Datei.")
    pdf, ocr_problem = text_of_pdf(data, file.filename or "scan.pdf")
    result = recognize_pdf(session, pdf, company_id or None)
    if ocr_problem:
        result.warnings.insert(0, ocr_problem)
    result.notes += _filled_note(customers.fill(session, result.invoice))
    return result


@app.post("/api/erp/sap-ecc/idoc", response_model=ImportResult)
async def import_idoc(
    file: UploadFile = File(...), _: User = Depends(current_user), session: Session = Depends(get_session)
) -> ImportResult:
    """Manual import of one INVOIC02 XML file (the worker automates this for the inbox folder)."""
    try:
        xml = await _read(file)
        company = pick_company(xml, get_companies(session))
        invoice = parse_invoic02(xml, company)
    except (ValueError, SyntaxError) as exc:
        raise HTTPException(400, f"IDoc konnte nicht gelesen werden: {exc}") from exc
    return ImportResult(invoice=invoice, notes=_filled_note(customers.fill(session, invoice)), company_id=company.id)


@app.get("/api/customers", response_model=list[CustomerInfo])
def search_customers(
    q: str = Query("", max_length=100),
    limit: int = Query(10, ge=1, le=50),
    _: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> list:
    """Buyer address book (autocomplete in the form)."""
    return customers.search(session, q, limit)


@app.post("/api/validate", response_model=ValidateResponse)
def validate_invoice(req: ValidateRequest, _: User = Depends(current_user)) -> ValidateResponse:
    issues = validate(req.invoice, req.profile, req.stated_totals)
    if not any(i.severity == "error" for i in issues):
        xsd_error = check_xsd(build_cii(req.invoice, req.profile))
        if xsd_error:
            issues.append(Issue(code="XSD", field="", message=f"Schema-Fehler: {xsd_error}"))
    return ValidateResponse(totals=compute_totals(req.invoice), issues=issues)


@app.post("/api/export/xrechnung")
def export_xrechnung(
    invoice: Invoice,
    source: str = Query("manual", max_length=20),
    source_name: str = Query("", max_length=255),
    review_id: int | None = Query(None, description="Prüfeintrag, der damit erledigt ist"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    return _convert_and_archive(session, user, invoice, "xrechnung", None, source, source_name, review_id)


@app.post("/api/export/zugferd")
async def export_zugferd(
    invoice: str = Form(...),
    file: UploadFile | None = File(None),
    source: str = Form("manual", max_length=20),
    source_name: str = Form("", max_length=255),
    review_id: int | None = Form(None),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    """Embed the XML into the uploaded PDF (or the review item's original, or a generated one)."""
    try:
        inv = Invoice.model_validate_json(invoice)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False)) from exc
    pdf = await _read(file) if file is not None else None
    return _convert_and_archive(session, user, inv, "zugferd", pdf, source, source_name, review_id)


@app.get("/api/documents", response_model=DocumentPage)
def documents(
    q: str = Query("", max_length=100, description="Rechnungsnummer oder Käufer"),
    date_from: date | None = None,
    date_to: date | None = None,
    profile: Literal["zugferd", "xrechnung"] | None = None,
    source: Literal["pdf", "idoc", "manual"] | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> DocumentPage:
    items, total = search_documents(
        session,
        query=q,
        date_from=date_from,
        date_to=date_to,
        profile=profile,
        source=source,
        offset=offset,
        limit=limit,
    )
    return DocumentPage(items=[DocumentInfo.model_validate(d) for d in items], total=total)


@app.post("/api/documents/zip")
def download_zip(
    req: ZipRequest, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> Response:
    """Several archived e-invoices as one ZIP file (e.g. after a batch upload)."""
    docs = [d for d in (session.get(Document, i) for i in dict.fromkeys(req.ids)) if d is not None]
    if not docs:
        raise HTTPException(404, "Keine der Rechnungen wurde gefunden.")
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in docs:
            name, n = d.filename, 1
            while name in used:  # same invoice exported twice
                stem, dot, ext = d.filename.rpartition(".")
                name, n = f"{stem}_{n}{dot}{ext}", n + 1
            used.add(name)
            zf.writestr(name, d.content)
    audit(session, user.username, "download", document_ids=[d.id for d in docs], zip=True)
    session.commit()
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="e-rechnungen.zip"'},
    )


@app.get("/api/documents/{doc_id}/download")
def download_document(
    doc_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> Response:
    doc = session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "Dokument nicht gefunden.")
    audit(session, user.username, "download", document_id=doc_id, invoice_number=doc.invoice_number)
    session.commit()
    return Response(
        doc.content,
        media_type=doc.media_type,
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'},
    )


# --- review queue ("Zu prüfen") ------------------------------------------------


def _open_review_item(session: Session, review_id: int) -> ReviewItem:
    item = session.get(ReviewItem, review_id)
    if item is None:
        raise HTTPException(404, "Prüfeintrag nicht gefunden.")
    if item.status != "offen":
        raise HTTPException(409, f"Dieser Prüfeintrag ist bereits {item.status}.")
    return item


@app.get("/api/review", response_model=list[ReviewInfo])
def review_list(
    status: Literal["offen", "erledigt", "verworfen", "alle"] = "offen",
    _: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[ReviewItem]:
    return review.list_items(session, status)


@app.get("/api/review/{review_id}", response_model=ReviewDetail)
def review_detail(review_id: int, _: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    item = session.get(ReviewItem, review_id)
    if item is None:
        raise HTTPException(404, "Prüfeintrag nicht gefunden.")
    data = ReviewInfo.model_validate(item).model_dump()
    return {
        **data,
        "invoice": item.invoice,
        "stated_totals": item.stated_totals,
        "warnings": item.warnings,
        "notes": item.notes,
        "template_name": item.template_name,
        "has_pdf": item.pdf is not None,
        "company_id": company_for_invoice(Invoice.model_validate(item.invoice), get_companies(session)).id,
    }


@app.get("/api/review/{review_id}/pdf")
def review_pdf(review_id: int, _: User = Depends(current_user), session: Session = Depends(get_session)) -> Response:
    item = session.get(ReviewItem, review_id)
    if item is None or item.pdf is None:
        raise HTTPException(404, "Kein PDF vorhanden.")
    return Response(
        item.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{item.source_name or "rechnung.pdf"}"'},
    )


@app.post("/api/review/{review_id}/discard", status_code=204)
def review_discard(
    review_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> Response:
    item = _open_review_item(session, review_id)
    review.resolve(session, item, status="verworfen", username=user.username)
    audit(session, user.username, "review_discarded", review_id=review_id, source_name=item.source_name)
    session.commit()
    return Response(status_code=204)


@app.get("/api/dashboard", response_model=Dashboard)
def dashboard(_: User = Depends(current_user), session: Session = Depends(get_session)) -> Dashboard:
    today = datetime.now(UTC).date()
    _, today_count = search_documents(session, date_from=today, limit=1)
    total = session.scalar(select(func.count()).select_from(Document)) or 0
    return Dashboard(
        review_open=review.open_count(session),
        documents_today=today_count,
        documents_total=total,
        worker=get_setting(session, STATUS_KEY) or {},
    )
