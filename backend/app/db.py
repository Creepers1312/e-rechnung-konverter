"""Persistence: archive of every generated e-invoice.

PostgreSQL in Docker (DATABASE_URL), SQLite for local development and tests.
Tables are created on startup; there is no migration tool yet (add Alembic
before the first schema change on a production database).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

from sqlalchemy import JSON, DateTime, Integer, LargeBinary, Numeric, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .export.cii import Profile
from .model import Invoice, compute_totals

DEFAULT_URL = "sqlite:///./erechnung.db"


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    source: Mapped[str] = mapped_column(String(20))  # "pdf" | "idoc" | "manual"
    source_name: Mapped[str] = mapped_column(String(255), default="")
    profile: Mapped[str] = mapped_column(String(20))  # "zugferd" | "xrechnung"
    invoice_number: Mapped[str] = mapped_column(String(100), index=True)
    buyer_name: Mapped[str] = mapped_column(String(255), default="")
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    invoice: Mapped[dict] = mapped_column(JSON)  # the reviewed Invoice as JSON
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    content: Mapped[bytes] = mapped_column(LargeBinary)  # the generated file


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with sessionmaker(get_engine(), expire_on_commit=False)() as session:
        yield session


def archive(
    session: Session,
    invoice: Invoice,
    *,
    profile: Profile,
    source: str,
    source_name: str,
    filename: str,
    media_type: str,
    content: bytes,
) -> Document:
    doc = Document(
        source=source,
        source_name=source_name[:255],
        profile=profile,
        invoice_number=invoice.number,
        buyer_name=invoice.buyer.name[:255],
        grand_total=compute_totals(invoice).grand_total,
        currency=invoice.currency,
        invoice=invoice.model_dump(mode="json"),
        filename=filename,
        media_type=media_type,
        content=content,
    )
    session.add(doc)
    session.commit()
    return doc


def list_documents(session: Session, limit: int = 100) -> list[Document]:
    return list(session.scalars(select(Document).order_by(Document.created_at.desc(), Document.id.desc()).limit(limit)))
