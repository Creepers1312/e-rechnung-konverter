"""IDoc inbox worker: SAP ECC drops INVOIC02 XML files, we convert them.

    inbox/        <- SAP writes *.xml here
    outbox/       -> finished e-invoices (ZUGFeRD PDF or XRechnung XML)
    processed/    <- source IDocs that were converted
    error/        <- source IDocs that failed, each with a <name>.error.txt

Every converted invoice is also archived in the database. Run with
`python -m app.worker` (Docker service `worker`).

Settings (environment):
    IDOC_DIR        base folder (default ./data/idoc)
    IDOC_PROFILE    zugferd | xrechnung (default zugferd)
    IDOC_INTERVAL   poll interval in seconds (default 10)
    WORKER_HEARTBEAT  file touched after every round (default /tmp/worker-heartbeat),
                      used by the Docker health check
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from .company import load_company
from .convert import convert
from .db import archive, get_engine
from .erp.sap_ecc_idoc import SapEccIdocFolderAdapter
from .export.cii import Profile

log = logging.getLogger("erechnung.worker")

# A file younger than this may still be being written by SAP.
MIN_FILE_AGE_SECONDS = 2.0


@dataclass
class Folders:
    inbox: Path
    outbox: Path
    processed: Path
    error: Path

    @classmethod
    def under(cls, base: Path) -> "Folders":
        f = cls(base / "inbox", base / "outbox", base / "processed", base / "error")
        for p in (f.inbox, f.outbox, f.processed, f.error):
            p.mkdir(parents=True, exist_ok=True)
        return f


def _move(src: Path, dest_dir: Path) -> Path:
    """Move without overwriting an earlier file of the same name."""
    target = dest_dir / src.name
    n = 1
    while target.exists():
        target = dest_dir / f"{src.stem}.{n}{src.suffix}"
        n += 1
    shutil.move(str(src), target)
    return target


def process_once(folders: Folders, profile: Profile, session: Session, now: float | None = None) -> tuple[int, int]:
    """Convert every ready file in the inbox. Returns (converted, failed)."""
    adapter = SapEccIdocFolderAdapter(folders.inbox, load_company())
    now = time.time() if now is None else now
    ok = failed = 0
    for name in adapter.list_new_invoices():
        path = folders.inbox / name
        if now - path.stat().st_mtime < MIN_FILE_AGE_SECONDS:
            continue
        try:
            invoice = adapter.get_invoice(name)
            result = convert(invoice, profile)
            archive(
                session, invoice, profile=profile, source="idoc", source_name=name,
                filename=result.filename, media_type=result.media_type, content=result.content,
            )
            (folders.outbox / result.filename).write_bytes(result.content)
            _move(path, folders.processed)
            ok += 1
            log.info("converted %s -> %s", name, result.filename)
        except Exception as exc:  # one bad IDoc must not stop the others
            session.rollback()
            moved = _move(path, folders.error)
            moved.with_name(moved.name + ".error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            failed += 1
            log.warning("failed %s: %s", name, exc)
    return ok, failed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    folders = Folders.under(Path(os.environ.get("IDOC_DIR", "./data/idoc")))
    profile: Profile = "xrechnung" if os.environ.get("IDOC_PROFILE") == "xrechnung" else "zugferd"
    interval = float(os.environ.get("IDOC_INTERVAL", "10"))
    make_session = sessionmaker(get_engine(), expire_on_commit=False)
    log.info("watching %s (profile=%s, every %ss)", folders.inbox, profile, interval)
    heartbeat = Path(os.environ.get("WORKER_HEARTBEAT", "/tmp/worker-heartbeat"))
    while True:
        try:
            with make_session() as session:
                process_once(folders, profile, session)
            heartbeat.touch()
        except Exception:  # e.g. database briefly unreachable: retry next round
            log.exception("round failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
