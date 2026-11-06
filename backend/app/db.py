"""Persistence: archive of every generated e-invoice.

PostgreSQL in Docker (DATABASE_URL), SQLite for local development and tests.
Tables are created on startup; there is no migration tool yet (add Alembic
before the first schema change on a production database).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    create_engine,
    func,
    inspect,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .export.cii import Profile
from .model import Invoice, compute_totals

DEFAULT_URL = "sqlite:///./erechnung.db"


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
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
    created_by: Mapped[str] = mapped_column(String(100), default="")  # username, "worker" for IDocs


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))  # "admin" | "user"
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginSession(Base):
    """Server-side session. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Setting(Base):
    """Admin-editable configuration, one JSON document per key ("company", "idoc")."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    updated_by: Mapped[str] = mapped_column(String(100), default="")


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    username: Mapped[str] = mapped_column(String(100), default="")
    action: Mapped[str] = mapped_column(String(50))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Customer(Base):
    """Buyer address book: imported from the SAP customer master and learned from exported invoices."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lookup_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # "erp:<id>" or "name:<name>"
    name: Mapped[str] = mapped_column(String(255), index=True)
    party: Mapped[dict] = mapped_column(JSON)  # model.Party as JSON
    buyer_reference: Mapped[str] = mapped_column(String(100), default="")  # last used Leitweg-ID
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    updated_by: Mapped[str] = mapped_column(String(100), default="")
    # "rechnung": learned from exported invoices; "sap": imported from the SAP customer master (authoritative)
    source: Mapped[str] = mapped_column(String(20), default="rechnung")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewItem(Base):
    """An invoice the automation could not finish on its own ("Zu prüfen")."""

    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    status: Mapped[str] = mapped_column(String(20), default="offen", index=True)  # offen | erledigt | verworfen
    source: Mapped[str] = mapped_column(String(20))  # pdf | idoc
    source_name: Mapped[str] = mapped_column(String(255), default="")
    profile: Mapped[str] = mapped_column(String(20))
    invoice_number: Mapped[str] = mapped_column(String(100), default="")
    buyer_name: Mapped[str] = mapped_column(String(255), default="")
    invoice: Mapped[dict] = mapped_column(JSON)  # extracted Invoice as JSON
    stated_totals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    issues: Mapped[list] = mapped_column(JSON, default=list)  # validation errors that stopped the automation
    template_name: Mapped[str] = mapped_column(String(255), default="")
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # original file (PDF sources)
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # archive entry once done
    resolved_by: Mapped[str] = mapped_column(String(100), default="")


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            # backend and worker start at the same time: let only one create the schema
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_LOCK_KEY})
        Base.metadata.create_all(conn)
        _upgrade(conn)
    return engine


SCHEMA_LOCK_KEY = 72_310_001  # arbitrary, app-wide constant


def _upgrade(conn: Connection) -> None:
    """Minimal in-place upgrades for databases created by older versions.
    Replace with Alembic before the next non-trivial schema change."""
    columns = {c["name"] for c in inspect(conn).get_columns("documents")}
    if "created_by" not in columns:
        conn.execute(text("ALTER TABLE documents ADD COLUMN created_by VARCHAR(100) NOT NULL DEFAULT ''"))
    columns = {c["name"] for c in inspect(conn).get_columns("customers")}
    if "source" not in columns:
        conn.execute(text("ALTER TABLE customers ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'rechnung'"))
    if "synced_at" not in columns:
        conn.execute(text("ALTER TABLE customers ADD COLUMN synced_at TIMESTAMP WITH TIME ZONE"))


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
    created_by: str = "",
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
        created_by=created_by,
    )
    session.add(doc)
    session.commit()
    return doc


def search_documents(
    session: Session,
    *,
    query: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    profile: str | None = None,
    source: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Document], int]:
    """Archive search, newest first. `query` matches invoice number or buyer (case-insensitive)."""
    conditions = []
    if query.strip():
        term = query.strip().lower()
        conditions.append(
            or_(
                func.lower(Document.invoice_number).contains(term, autoescape=True),
                func.lower(Document.buyer_name).contains(term, autoescape=True),
            )
        )
    if date_from:
        conditions.append(Document.created_at >= datetime.combine(date_from, time.min, UTC))
    if date_to:
        conditions.append(Document.created_at < datetime.combine(date_to + timedelta(days=1), time.min, UTC))
    if profile:
        conditions.append(Document.profile == profile)
    if source:
        conditions.append(Document.source == source)
    total = session.scalar(select(func.count()).select_from(Document).where(*conditions)) or 0
    stmt = (
        select(Document)
        .where(*conditions)
        .order_by(Document.created_at.desc(), Document.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(stmt)), total


def audit(session: Session, actor: str, action: str, /, **detail: object) -> None:
    """Append to the audit log (committed together with the caller's changes).
    `actor` is who did it; `detail` may contain e.g. the affected `username`."""
    session.add(AuditEntry(username=actor, action=action, detail=detail))


def get_setting(session: Session, key: str) -> dict | None:
    row = session.get(Setting, key)
    return dict(row.value) if row else None


def put_setting(session: Session, key: str, value: dict, username: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value, updated_by=username))
    else:
        row.value = value
        row.updated_by = username
