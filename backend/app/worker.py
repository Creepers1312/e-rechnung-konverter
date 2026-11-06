"""Inbox worker: converts incoming invoices without anyone clicking.

Two inbox folders, each with the same layout:

    <dir>/inbox/      <- new files arrive here (SAP, scanner, file share, ...)
    <dir>/outbox/     -> finished e-invoices (ZUGFeRD PDF or XRechnung XML)
    <dir>/processed/  <- source files that were handled (converted or queued for review)
    <dir>/error/      <- unreadable files, each with a <name>.error.txt

- IDOC_DIR (default ./data/idoc): SAP ECC INVOIC02 IDocs (*.xml)
- PDF_DIR  (default ./data/pdf):  PDF invoices (*.pdf), scans are OCR'd first
- STAMMDATEN_DIR (default ./data/stammdaten): SAP customer master exports (*.csv / *.txt)
  -> buyer address book (customer_import.py); handled before the invoices

Per file: read -> fill buyer gaps from the address book -> validate.
Valid invoices are converted, archived and written to outbox/. Invoices with
errors (e.g. missing buyer data) go to the review queue ("Zu prüfen") in the
web UI instead of the error folder; only unreadable files land in error/.

Run with `python -m app.worker` (Docker service `worker`). On/off, format,
interval and "fully automatic" are set by an admin in the web UI and re-read
every round.
    WORKER_HEARTBEAT  file touched regularly (default /tmp/worker-heartbeat), Docker health check
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from . import customer_import, customers, review
from .convert import NotExportable, convert
from .db import archive, get_engine, get_setting, put_setting
from .erp.sap_ecc_idoc import SapEccIdocFolderAdapter
from .export.cii import Profile
from .model import Invoice
from .ocr import text_of_pdf
from .recognize import recognize_pdf
from .settings import get_companies, get_customer_import_settings, get_idoc_settings, get_pdf_inbox_settings
from .validation import validate

log = logging.getLogger("erechnung.worker")

# A file younger than this may still be being written.
MIN_FILE_AGE_SECONDS = 2.0
STATUS_KEY = "idoc_status"  # historic name: holds the status of both inboxes
IMPORT_STATUS_KEY = "customer_import_status"  # last customer master import report


@dataclass
class Folders:
    inbox: Path
    outbox: Path
    processed: Path
    error: Path

    @classmethod
    def under(cls, base: Path) -> Folders:
        f = cls(base / "inbox", base / "outbox", base / "processed", base / "error")
        for p in (f.inbox, f.outbox, f.processed, f.error):
            p.mkdir(parents=True, exist_ok=True)
        return f


@dataclass
class RoundResult:
    converted: int = 0
    review: int = 0
    failed: int = 0


def _move(src: Path, dest_dir: Path) -> Path:
    """Move without overwriting an earlier file of the same name."""
    target = dest_dir / src.name
    n = 1
    while target.exists():
        target = dest_dir / f"{src.stem}.{n}{src.suffix}"
        n += 1
    shutil.move(str(src), target)
    return target


def _ready_files(folder: Path, suffix: str, now: float) -> Iterator[Path]:
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() == suffix and now - path.stat().st_mtime >= MIN_FILE_AGE_SECONDS:
            yield path


def _fail(folders: Folders, path: Path, exc: Exception) -> None:
    moved = _move(path, folders.error)
    moved.with_name(moved.name + ".error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
    log.warning("failed %s: %s", path.name, exc)


def _finish(
    session: Session,
    folders: Folders,
    path: Path,
    invoice: Invoice,
    profile: Profile,
    *,
    source: str,
    source_pdf: bytes | None,
    result: RoundResult,
) -> None:
    """Convert + archive + outbox. Raises NotExportable if the invoice has errors."""
    converted = convert(invoice, profile, source_pdf)
    customers.remember(session, invoice, "worker")
    archive(
        session,
        invoice,
        profile=profile,
        source=source,
        source_name=path.name,
        filename=converted.filename,
        media_type=converted.media_type,
        content=converted.content,
        created_by="worker",
    )  # commits
    (folders.outbox / converted.filename).write_bytes(converted.content)
    _move(path, folders.processed)
    result.converted += 1
    log.info("converted %s -> %s", path.name, converted.filename)


def process_once(folders: Folders, profile: Profile, session: Session, now: float | None = None) -> tuple[int, int]:
    """SAP IDoc inbox. Returns (converted, failed); invoices with errors go to the review queue."""
    r = process_idocs(folders, profile, session, now)
    return r.converted, r.failed


def process_idocs(folders: Folders, profile: Profile, session: Session, now: float | None = None) -> RoundResult:
    adapter = SapEccIdocFolderAdapter(folders.inbox, get_companies(session))
    result = RoundResult()
    for path in list(_ready_files(folders.inbox, ".xml", time.time() if now is None else now)):
        try:
            invoice = adapter.get_invoice(path.name)
        except Exception as exc:  # unreadable: not an INVOIC02, broken XML, ...
            session.rollback()
            _fail(folders, path, exc)
            result.failed += 1
            continue
        notes = customers.note(customers.fill(session, invoice))
        try:
            _finish(session, folders, path, invoice, profile, source="idoc", source_pdf=None, result=result)
        except NotExportable as exc:
            session.rollback()
            review.create(
                session,
                invoice,
                source="idoc",
                source_name=path.name,
                profile=profile,
                issues=exc.issues,
                notes=notes,
                template_name="SAP ECC IDoc (INVOIC02)",
            )
            session.commit()
            _move(path, folders.processed)
            result.review += 1
            log.info("review needed %s: %s", path.name, exc)
        except Exception as exc:
            session.rollback()
            _fail(folders, path, exc)
            result.failed += 1
    return result


def process_pdfs(
    folders: Folders, profile: Profile, session: Session, *, auto_export: bool = True, now: float | None = None
) -> RoundResult:
    """PDF inbox: OCR (scans) -> recognition -> address book -> validate -> convert or review queue."""
    result = RoundResult()
    for path in list(_ready_files(folders.inbox, ".pdf", time.time() if now is None else now)):
        try:
            data = path.read_bytes()
            if not data.startswith(b"%PDF"):
                raise ValueError("Keine PDF-Datei.")
            pdf, ocr_problem = text_of_pdf(data, path.name)
            extracted = recognize_pdf(session, pdf)
        except Exception as exc:
            session.rollback()
            _fail(folders, path, exc)
            result.failed += 1
            continue
        invoice = extracted.invoice
        warnings = ([ocr_problem] if ocr_problem else []) + extracted.warnings
        notes = list(extracted.notes)
        notes += customers.note(customers.fill(session, invoice))
        try:
            if not auto_export:
                errors = [i for i in validate(invoice, profile, extracted.stated_totals) if i.severity == "error"]
                raise NotExportable(errors)
            # printed totals must match too – a mismatch means a misread position
            mismatch = [i for i in validate(invoice, profile, extracted.stated_totals) if i.code == "TOTALS"]
            if mismatch:
                raise NotExportable(mismatch)
            _finish(session, folders, path, invoice, profile, source="pdf", source_pdf=data, result=result)
        except NotExportable as exc:
            session.rollback()
            review.create(
                session,
                invoice,
                source="pdf",
                source_name=path.name,
                profile=profile,
                issues=exc.issues,
                stated=extracted.stated_totals,
                warnings=warnings,
                notes=notes,
                template_name=extracted.template_name,
                pdf=data,
            )
            session.commit()
            _move(path, folders.processed)
            result.review += 1
            log.info("review needed %s (%d issues)", path.name, len(exc.issues))
        except Exception as exc:
            session.rollback()
            _fail(folders, path, exc)
            result.failed += 1
    return result


def process_customer_files(folders: Folders, session: Session, now: float | None = None) -> RoundResult:
    """SAP customer master CSVs -> address book. The last report is kept for the admin page."""
    cfg = get_customer_import_settings(session)
    result = RoundResult()
    now = time.time() if now is None else now
    files = [p for suffix in (".csv", ".txt") for p in _ready_files(folders.inbox, suffix, now)]
    for path in sorted(files, key=lambda p: p.stat().st_mtime):  # oldest first: the newest export wins
        try:
            report = customer_import.import_customers(session, path.read_bytes(), path.name, cfg.mapping, "sap-import")
            put_setting(session, IMPORT_STATUS_KEY, report.model_dump(mode="json"), "worker")
            session.commit()
            _move(path, folders.processed)
            result.converted += 1
            log.info("customer master %s: %d new, %d updated", path.name, report.created, report.updated)
        except Exception as exc:
            session.rollback()
            _fail(folders, path, exc)
            result.failed += 1
    return result


def report_status(
    session: Session,
    enabled: bool,
    profile: str,
    interval: int,
    ok: int,
    failed: int,
    review_count: int = 0,
    pdf_enabled: bool | None = None,
) -> None:
    """Heartbeat + counters for the admin status page."""
    previous = get_setting(session, STATUS_KEY) or {}
    now = datetime.now(UTC).isoformat()
    status = {
        "last_round_at": now,
        "enabled": enabled,
        "pdf_enabled": enabled if pdf_enabled is None else pdf_enabled,
        "profile": profile,
        "interval_seconds": interval,
        "converted_total": int(previous.get("converted_total", 0)) + ok,
        "failed_total": int(previous.get("failed_total", 0)) + failed,
        "review_total": int(previous.get("review_total", 0)) + review_count,
        "last_converted_at": now if ok else previous.get("last_converted_at"),
        "last_failed_at": now if failed else previous.get("last_failed_at"),
    }
    put_setting(session, STATUS_KEY, status, "worker")
    session.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    idoc_folders = Folders.under(Path(os.environ.get("IDOC_DIR", "./data/idoc")))
    pdf_folders = Folders.under(Path(os.environ.get("PDF_DIR", "./data/pdf")))
    customer_folders = Folders.under(Path(os.environ.get("STAMMDATEN_DIR", "./data/stammdaten")))
    make_session = sessionmaker(get_engine(), expire_on_commit=False)
    heartbeat = Path(os.environ.get("WORKER_HEARTBEAT", "/tmp/worker-heartbeat"))
    log.info("watching %s and %s", idoc_folders.inbox, pdf_folders.inbox)
    interval = 10
    last: tuple | None = None
    while True:
        try:
            with make_session() as session:
                idoc_cfg = get_idoc_settings(session)
                pdf_cfg = get_pdf_inbox_settings(session)
                interval = idoc_cfg.interval_seconds
                state = (idoc_cfg.enabled, idoc_cfg.profile, pdf_cfg.enabled, pdf_cfg.profile, pdf_cfg.auto_export)
                if last != state:
                    log.info(
                        "IDoc inbox %s (%s), PDF inbox %s (%s, %s), every %ss",
                        "on" if idoc_cfg.enabled else "OFF",
                        idoc_cfg.profile,
                        "on" if pdf_cfg.enabled else "OFF",
                        pdf_cfg.profile,
                        "automatic" if pdf_cfg.auto_export else "review everything",
                        interval,
                    )
                    last = state
                total = RoundResult()
                if get_customer_import_settings(session).enabled:
                    # master data first, so invoices of this round already see new customers
                    process_customer_files(customer_folders, session)
                rounds = []
                if idoc_cfg.enabled:
                    rounds.append(process_idocs(idoc_folders, idoc_cfg.profile, session))
                if pdf_cfg.enabled:
                    rounds.append(process_pdfs(pdf_folders, pdf_cfg.profile, session, auto_export=pdf_cfg.auto_export))
                for r in rounds:
                    total.converted += r.converted
                    total.review += r.review
                    total.failed += r.failed
                report_status(
                    session,
                    idoc_cfg.enabled,
                    idoc_cfg.profile,
                    interval,
                    total.converted,
                    total.failed,
                    total.review,
                    pdf_enabled=pdf_cfg.enabled,
                )
            heartbeat.touch()  # also while disabled: the worker itself is healthy
        except Exception:  # e.g. database briefly unreachable: retry next round
            log.exception("round failed")
        # sleep in short steps and keep the heartbeat fresh, so long intervals
        # (up to an hour) do not make the Docker health check fail
        deadline = time.monotonic() + interval
        while (remaining := deadline - time.monotonic()) > 0:
            time.sleep(min(5.0, remaining))
            heartbeat.touch()


if __name__ == "__main__":
    main()
