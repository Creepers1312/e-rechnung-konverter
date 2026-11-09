"""PDF service: OCR for scans, PDF/A-3 conversion and PDF/A validation.

Runs OCRmyPDF/Tesseract/Ghostscript and veraPDF in its own container on the
internal Docker network (no internet access), so invoices never leave the server.

    POST /ocr     multipart "file" -> application/pdf
                  optional form field "pages": "all" or "1,3" -> these pages are
                  recognised even if they already carry a (poor) text layer, e.g.
                  from the scanner; without it, pages that already contain text
                  are passed through unchanged
    POST /pdfa    multipart "file" -> application/pdf (PDF/A-3b via Ghostscript, no OCR)
    POST /verify  multipart "file", optional "flavour" (default "3b") ->
                  {"compliant": bool, "profile": str, "failed_rules": [...]} (veraPDF)
    GET  /health  {"status": "ok", "languages": [...], "verapdf": "..."}

Settings (environment):
    OCR_LANG         Tesseract languages, default "deu+eng"
    OCR_PARALLEL     documents processed at the same time, default 2
    OCR_TIMEOUT      seconds per page before Tesseract gives up, default 120
    VERAPDF          veraPDF launcher, default "verapdf"
    VERAPDF_TIMEOUT  seconds per validation, default 120
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import ocrmypdf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

MAX_UPLOAD = 25 * 1024 * 1024
LANG = os.environ.get("OCR_LANG", "deu+eng")
TIMEOUT = float(os.environ.get("OCR_TIMEOUT", "120"))
VERAPDF = os.environ.get("VERAPDF", "verapdf")
VERAPDF_TIMEOUT = float(os.environ.get("VERAPDF_TIMEOUT", "120"))
_slots = threading.BoundedSemaphore(int(os.environ.get("OCR_PARALLEL", "2")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("ocrmypdf").setLevel(logging.WARNING)
log = logging.getLogger("ocr")

app = FastAPI(title="E-Rechnung PDF-Dienst", version="1.1.0")


def installed_languages() -> list[str]:
    try:
        out = subprocess.run(
            ["tesseract", "--list-langs"], check=False, capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in out.splitlines()[1:] if line.strip()]


@app.get("/health")
def health() -> dict[str, object]:
    langs = installed_languages()
    missing = [lang for lang in LANG.split("+") if lang not in langs]
    if missing:
        raise HTTPException(503, f"Tesseract-Sprachen fehlen: {', '.join(missing)}")
    verapdf = shutil.which(VERAPDF)
    if not verapdf:
        raise HTTPException(503, "veraPDF (PDF/A-Prüfung) ist nicht installiert.")
    return {"status": "ok", "languages": langs, "verapdf": verapdf}


def _read_pdf(file: UploadFile) -> bytes:
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Datei ist größer als 25 MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Keine PDF-Datei.")
    return data


def _ocrmypdf(data: bytes, what: str, **options: Any) -> bytes:
    """Run OCRmyPDF on `data`, map its errors to HTTP errors with German messages."""
    with _slots, tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.pdf", Path(tmp) / "out.pdf"
        src.write_bytes(data)
        try:
            exit_code = ocrmypdf.ocr(src, dst, optimize=0, progress_bar=False, **options)
        except ocrmypdf.exceptions.EncryptedPdfError as exc:
            raise HTTPException(400, "Das PDF ist verschlüsselt.") from exc
        except ocrmypdf.exceptions.InputFileError as exc:
            raise HTTPException(400, f"PDF kann nicht gelesen werden: {exc}") from exc
        except ocrmypdf.exceptions.ExitCodeException as exc:
            log.warning("%s failed: %s", what, exc)
            raise HTTPException(500, f"{what} fehlgeschlagen: {type(exc).__name__}") from exc
        if exit_code != ocrmypdf.ExitCode.ok:  # e.g. pdfa_conversion_failed – /verify has the final say
            log.warning("%s: ocrmypdf exit code %s", what, exit_code)
        return dst.read_bytes()


def _mode(pages: str) -> dict[str, object]:
    """Default: pages with text stay untouched. Requested pages are recognised anyway
    (their old text layer is replaced; with force_ocr the page is rasterised)."""
    if not pages:
        return {"skip_text": True}
    return {"force_ocr": True} if pages == "all" else {"force_ocr": True, "pages": pages}


@app.post("/ocr")
def ocr(file: UploadFile = File(...), pages: str = Form("")) -> Response:  # sync: runs in FastAPI's thread pool
    pages = pages.strip().lower()
    if pages and pages != "all" and not re.fullmatch(r"\d{1,4}(?:,\d{1,4})*", pages):
        raise HTTPException(400, "Ungültige Seitenangabe (z. B. „1,3“ oder „all“).")
    data = _read_pdf(file)
    result = _ocrmypdf(
        data,
        "Texterkennung",
        language=LANG,
        **_mode(pages),
        rotate_pages=True,  # scans fed in upside down / sideways
        deskew=True,  # slightly skewed scans
        output_type="pdf",
        tesseract_timeout=TIMEOUT,
    )
    log.info("ocr ok: %s pages=%s (%d -> %d bytes)", file.filename, pages or "auto", len(data), len(result))
    return Response(result, media_type="application/pdf")


@app.post("/pdfa")
def pdfa(file: UploadFile = File(...)) -> Response:
    """PDF/A-3b for ZUGFeRD: Ghostscript embeds the fonts and adds an sRGB output intent.
    No OCR and no image processing – the page looks as before."""
    data = _read_pdf(file)
    result = _ocrmypdf(
        data,
        "PDF/A-Umwandlung",
        ocr_engine="none",
        skip_text=True,
        output_type="pdfa-3",
        invalidate_digital_signatures=True,  # embedding the XML breaks a signature anyway
    )
    log.info("pdfa ok: %s (%d -> %d bytes)", file.filename, len(data), len(result))
    return Response(result, media_type="application/pdf")


def parse_verapdf(stdout: str) -> dict[str, object]:
    """veraPDF's JSON report -> compact result. Raises ValueError if there is no validation result."""
    report = json.loads(stdout[stdout.index("{") :])
    job = report["report"]["jobs"][0]
    results = job.get("validationResult")
    result = results[0] if isinstance(results, list) and results else results
    if not isinstance(result, dict):
        raise ValueError(job.get("taskException") or "no validation result")
    rules = result.get("details", {}).get("ruleSummaries", [])
    return {
        "compliant": bool(result.get("compliant")),
        "profile": result.get("profileName", ""),
        "failed_rules": [
            {
                "clause": r.get("clause", ""),
                "test": r.get("testNumber"),
                "description": " ".join(str(r.get("description", "")).split()),
                "failed_checks": r.get("failedChecks", 0),
                "message": next((c.get("errorMessage") for c in r.get("checks", []) if c.get("errorMessage")), ""),
            }
            for r in rules
            if str(r.get("ruleStatus", r.get("status", ""))).lower() == "failed"
        ],
    }


@app.post("/verify")
def verify(file: UploadFile = File(...), flavour: str = Form("3b")) -> dict[str, object]:
    flavour = flavour.strip().lower()
    if not re.fullmatch(r"[1-4][abuf]?", flavour):
        raise HTTPException(400, "Ungültiges PDF/A-Profil (z. B. „3b“).")
    data = _read_pdf(file)
    with _slots, tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(data)
        try:
            proc = subprocess.run(
                [VERAPDF, "--flavour", flavour, "--format", "json", str(src)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=VERAPDF_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise HTTPException(503, "veraPDF (PDF/A-Prüfung) ist nicht installiert.") from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "PDF/A-Prüfung hat zu lange gedauert.") from exc
    try:
        result = parse_verapdf(proc.stdout)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("verapdf failed (exit %s): %s %s", proc.returncode, exc, proc.stderr[-500:])
        raise HTTPException(422, "PDF/A-Prüfung nicht möglich – das PDF ist nicht lesbar.") from exc
    log.info("verify %s: %s compliant=%s", flavour, file.filename, result["compliant"])
    return result
