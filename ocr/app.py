"""OCR service: scanned PDF in, PDF with an invisible text layer out.

Runs OCRmyPDF/Tesseract in its own container on the internal Docker network
(no internet access), so invoices never leave the server.

    POST /ocr     multipart "file" -> application/pdf
                  optional form field "pages": "all" or "1,3" -> these pages are
                  recognised even if they already carry a (poor) text layer, e.g.
                  from the scanner; without it, pages that already contain text
                  are passed through unchanged
    GET  /health  {"status": "ok", "languages": [...]}

Settings (environment):
    OCR_LANG      Tesseract languages, default "deu+eng"
    OCR_PARALLEL  documents processed at the same time, default 2
    OCR_TIMEOUT   seconds per page before Tesseract gives up, default 120
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import ocrmypdf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

MAX_UPLOAD = 25 * 1024 * 1024
LANG = os.environ.get("OCR_LANG", "deu+eng")
TIMEOUT = float(os.environ.get("OCR_TIMEOUT", "120"))
_slots = threading.BoundedSemaphore(int(os.environ.get("OCR_PARALLEL", "2")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("ocrmypdf").setLevel(logging.WARNING)
log = logging.getLogger("ocr")

app = FastAPI(title="E-Rechnung OCR", version="1.0.0")


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
    return {"status": "ok", "languages": langs}


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
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Datei ist größer als 25 MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Keine PDF-Datei.")
    with _slots, tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.pdf", Path(tmp) / "out.pdf"
        src.write_bytes(data)
        try:
            ocrmypdf.ocr(
                src,
                dst,
                language=LANG,
                **_mode(pages),
                rotate_pages=True,  # scans fed in upside down / sideways
                deskew=True,  # slightly skewed scans
                output_type="pdf",
                optimize=0,
                tesseract_timeout=TIMEOUT,
                progress_bar=False,
            )
        except ocrmypdf.exceptions.EncryptedPdfError as exc:
            raise HTTPException(400, "Das PDF ist verschlüsselt.") from exc
        except ocrmypdf.exceptions.InputFileError as exc:
            raise HTTPException(400, f"PDF kann nicht gelesen werden: {exc}") from exc
        except ocrmypdf.exceptions.ExitCodeException as exc:
            log.warning("ocr failed: %s", exc)
            raise HTTPException(500, f"Texterkennung fehlgeschlagen: {type(exc).__name__}") from exc
        result = dst.read_bytes()
    log.info("ocr ok: %s pages=%s (%d -> %d bytes)", file.filename, pages or "auto", len(data), len(result))
    return Response(result, media_type="application/pdf")
