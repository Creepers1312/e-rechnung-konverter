"""Review queue ("Zu prüfen"): invoices the automation could not finish.

Created by the worker when an incoming PDF or IDoc fails validation (or when
fully automatic export is off). A user opens the item, corrects it in the
form and exports it – the export endpoints then mark the item as done.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import ReviewItem
from .model import Invoice
from .validation import Issue, StatedTotals


def create(
    session: Session,
    invoice: Invoice,
    *,
    source: str,
    source_name: str,
    profile: str,
    issues: list[Issue],
    stated: StatedTotals | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    template_name: str = "",
    pdf: bytes | None = None,
) -> ReviewItem:
    item = ReviewItem(
        source=source,
        source_name=source_name[:255],
        profile=profile,
        invoice_number=invoice.number[:100],
        buyer_name=invoice.buyer.name[:255],
        invoice=invoice.model_dump(mode="json"),
        stated_totals=stated.model_dump(mode="json") if stated else None,
        warnings=list(warnings or []),
        notes=list(notes or []),
        issues=[i.model_dump() for i in issues],
        template_name=template_name,
        pdf=pdf,
    )
    session.add(item)
    return item


def open_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "offen")) or 0


def list_items(session: Session, status: str = "offen", limit: int = 200) -> list[ReviewItem]:
    stmt = select(ReviewItem).order_by(ReviewItem.created_at.desc(), ReviewItem.id.desc()).limit(limit)
    if status != "alle":
        stmt = stmt.where(ReviewItem.status == status)
    return list(session.scalars(stmt))


def resolve(session: Session, item: ReviewItem, *, status: str, username: str, document_id: int | None = None) -> None:
    item.status = status
    item.resolved_by = username
    item.document_id = document_id
