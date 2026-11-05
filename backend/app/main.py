"""HTTP API. Every endpoint except /api/health and /api/auth/login requires a
logged-in user (see auth.py); admin endpoints live in admin.py.

The client keeps the invoice and the PDF while editing; every export is
archived in the database together with the user who made it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from .admin import router as admin_router
from .auth import bootstrap_admin, current_user
from .company import Company
from .convert import EInvoice, NotExportable, convert
from .db import Document, User, archive, audit, get_engine, get_session, list_documents
from .erp.sap_ecc_idoc import parse_invoic02
from .export.cii import Profile, build_cii
from .export.zugferd import check_xsd
from .extraction.pdf_text import PdfText
from .extraction.rules import ExtractionResult, extract
from .model import Invoice, Totals, compute_totals
from .settings import get_company
from .validation import Issue, StatedTotals, validate

MAX_UPLOAD = 20 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    engine = get_engine()  # creates tables; fails fast if the database is unreachable
    with sessionmaker(engine)() as session:
        bootstrap_admin(session)
    yield


app = FastAPI(title="E-Rechnung Konverter", version="0.3.0", lifespan=lifespan)
app.include_router(admin_router)


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
) -> Response:
    try:
        result: EInvoice = convert(invoice, profile, source_pdf)
    except NotExportable as exc:
        raise HTTPException(
            422, {"message": "Rechnung ist nicht vollständig.", "issues": [i.model_dump() for i in exc.issues]}
        ) from exc
    audit(session, user.username, "export", invoice_number=invoice.number, profile=profile)
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
def company(_: User = Depends(current_user), session: Session = Depends(get_session)) -> Company:
    return get_company(session)


@app.post("/api/extract", response_model=ExtractionResult)
async def extract_pdf(
    file: UploadFile = File(...), _: User = Depends(current_user), session: Session = Depends(get_session)
) -> ExtractionResult:
    data = await _read(file)
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Keine PDF-Datei.")
    return extract(PdfText.from_bytes(data), get_company(session))


@app.post("/api/erp/sap-ecc/idoc", response_model=Invoice)
async def import_idoc(
    file: UploadFile = File(...), _: User = Depends(current_user), session: Session = Depends(get_session)
) -> Invoice:
    """Manual import of one INVOIC02 XML file (the worker automates this for the inbox folder)."""
    try:
        return parse_invoic02(await _read(file), get_company(session))
    except (ValueError, SyntaxError) as exc:
        raise HTTPException(400, f"IDoc konnte nicht gelesen werden: {exc}") from exc


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
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    return _convert_and_archive(session, user, invoice, "xrechnung", None, source, source_name)


@app.post("/api/export/zugferd")
async def export_zugferd(
    invoice: str = Form(...),
    file: UploadFile | None = File(None),
    source: str = Form("manual", max_length=20),
    source_name: str = Form("", max_length=255),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    """Embed the XML into the uploaded PDF, or into a generated one if none is sent."""
    try:
        inv = Invoice.model_validate_json(invoice)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False)) from exc
    pdf = await _read(file) if file is not None else None
    return _convert_and_archive(session, user, inv, "zugferd", pdf, source, source_name)


@app.get("/api/documents", response_model=list[DocumentInfo])
def documents(
    limit: int = Query(100, ge=1, le=500), _: User = Depends(current_user), session: Session = Depends(get_session)
) -> list[Document]:
    return list_documents(session, limit)


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
