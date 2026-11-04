from __future__ import annotations

import shutil
from pathlib import Path

import facturx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base, list_documents
from app.worker import Folders, process_once

SAMPLE = Path(__file__).parent / "fixtures" / "invoic02_sample.xml"


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path}/w.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_worker_converts_inbox_and_archives(tmp_path: Path) -> None:
    folders = Folders.under(tmp_path / "idoc")
    shutil.copy(SAMPLE, folders.inbox / "rechnung1.xml")
    (folders.inbox / "kaputt.xml").write_text("<nicht-sap/>", encoding="utf-8")

    with _session(tmp_path) as session:
        ok, failed = process_once(folders, "zugferd", session, now=1e12)
        assert (ok, failed) == (1, 1)
        docs = list_documents(session)
        assert [d.source_name for d in docs] == ["rechnung1.xml"]

    out = folders.outbox / "0090001234.pdf"
    _, xml = facturx.get_xml_from_pdf(out.read_bytes())
    assert b"0090001234" in xml
    assert list(folders.inbox.iterdir()) == []
    assert (folders.processed / "rechnung1.xml").exists()
    assert "E1EDK01" in (folders.error / "kaputt.xml.error.txt").read_text(encoding="utf-8")


def test_worker_skips_files_still_being_written(tmp_path: Path) -> None:
    folders = Folders.under(tmp_path / "idoc")
    shutil.copy(SAMPLE, folders.inbox / "neu.xml")
    with _session(tmp_path) as session:
        assert process_once(folders, "zugferd", session, now=0) == (0, 0)  # mtime is "in the future"
    assert (folders.inbox / "neu.xml").exists()


def test_worker_does_not_overwrite_processed_files(tmp_path: Path) -> None:
    folders = Folders.under(tmp_path / "idoc")
    with _session(tmp_path) as session:
        for _ in range(2):
            shutil.copy(SAMPLE, folders.inbox / "same.xml")
            process_once(folders, "zugferd", session, now=1e12)
    assert sorted(p.name for p in folders.processed.iterdir()) == ["same.1.xml", "same.xml"]
