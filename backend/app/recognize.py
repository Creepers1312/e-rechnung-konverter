"""PDF recognition for one of several own companies: detect the issuing company,
run the rule-based extraction, then apply that company's linked fields."""

from __future__ import annotations

from sqlalchemy.orm import Session

from . import field_links
from .extraction.pdf_text import PdfText
from .extraction.rules import ExtractionResult, extract
from .settings import detect_company, get_companies


def recognize_pdf(session: Session, pdf: PdfText, company_id: str | None = None) -> ExtractionResult:
    companies = get_companies(session)
    chosen = next((c for c in companies if company_id and c.id == company_id), None)
    company = chosen or detect_company(pdf.text, companies)
    result = extract(pdf, company)
    result.company_id = company.id
    if len(companies) > 1:
        how = "gewählt" if chosen else "erkannt"
        result.notes.insert(0, f"Firma {how}: {company.seller.name or company.id}.")
    filled = field_links.apply_links(result.invoice, pdf, field_links.links_of(session, company.id))
    if filled:
        result.notes.append(f"Aus gespeicherten Feldverknüpfungen übernommen: {', '.join(filled)}.")
        # the rules' "not found" warnings are obsolete for fields a link has filled
        inv = result.invoice
        solved = {
            "Rechnungsnummer nicht erkannt": bool(inv.number),
            "Rechnungsdatum nicht erkannt": inv.issue_date is not None,
            "Käuferanschrift nicht erkannt": bool(inv.buyer.name),
        }
        result.warnings = [w for w in result.warnings if not any(w.startswith(k) and ok for k, ok in solved.items())]
    return result
