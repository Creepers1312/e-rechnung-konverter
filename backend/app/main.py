"""HTTP API. Stateless: the client keeps the invoice and the PDF between calls."""

from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from .company import Company, load_company
from .erp.sap_ecc_idoc import parse_invoic02
from .export.cii import Profile, build_cii
from .export.pdf_render import render_invoice_pdf
from .export.zugferd import build_zugferd, check_xsd
from .extraction.pdf_text import PdfText
from .extraction.rules import ExtractionResult, extract
from .model import Invoice, Totals, compute_totals
from .validation import Issue, StatedTotals, validate

MAX_UPLOAD = 20 * 1024 * 1024

app = FastAPI(title="E-Rechnung Konverter", version="0.1.0")


class ValidateRequest(BaseModel):
    invoice: Invoice
    profile: Profile = "zugferd"
    stated_totals: StatedTotals | None = None


class ValidateResponse(BaseModel):
    totals: Totals
    issues: list[Issue]


async def _read(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Datei ist größer als 20 MB.")
    return data


def _filename(invoice: Invoice, ext: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in invoice.number) or "rechnung"
    return f"{safe}.{ext}"


def _ensure_exportable(invoice: Invoice, profile: Profile) -> None:
    errors = [i for i in validate(invoice, profile) if i.severity == "error"]
    if errors:
        raise HTTPException(422, {"message": "Rechnung ist nicht vollständig.", "issues": [e.model_dump() for e in errors]})


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/company", response_model=Company)
def company() -> Company:
    return load_company()


@app.post("/api/extract", response_model=ExtractionResult)
async def extract_pdf(file: UploadFile = File(...)) -> ExtractionResult:
    data = await _read(file)
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Keine PDF-Datei.")
    return extract(PdfText.from_bytes(data), load_company())


@app.post("/api/erp/sap-ecc/idoc", response_model=Invoice)
async def import_idoc(file: UploadFile = File(...)) -> Invoice:
    """Manual import of one INVOIC02 XML file (the folder adapter automates this later)."""
    try:
        return parse_invoic02(await _read(file), load_company())
    except (ValueError, SyntaxError) as exc:
        raise HTTPException(400, f"IDoc konnte nicht gelesen werden: {exc}") from exc


@app.post("/api/validate", response_model=ValidateResponse)
def validate_invoice(req: ValidateRequest) -> ValidateResponse:
    issues = validate(req.invoice, req.profile, req.stated_totals)
    if not any(i.severity == "error" for i in issues):
        xsd_error = check_xsd(build_cii(req.invoice, req.profile))
        if xsd_error:
            issues.append(Issue(code="XSD", field="", message=f"Schema-Fehler: {xsd_error}"))
    return ValidateResponse(totals=compute_totals(req.invoice), issues=issues)


@app.post("/api/export/xrechnung")
def export_xrechnung(invoice: Invoice) -> Response:
    _ensure_exportable(invoice, "xrechnung")
    xml = build_cii(invoice, "xrechnung")
    return Response(
        xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{_filename(invoice, "xml")}"'},
    )


@app.post("/api/export/zugferd")
async def export_zugferd(invoice: str = Form(...), file: UploadFile | None = File(None)) -> Response:
    """Embed the XML into the uploaded PDF, or into a generated one if none is sent."""
    try:
        inv = Invoice.model_validate_json(invoice)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False)) from exc
    _ensure_exportable(inv, "zugferd")
    source = await _read(file) if file is not None else render_invoice_pdf(inv)
    pdf = build_zugferd(source, inv)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_filename(inv, "pdf")}"'},
    )
