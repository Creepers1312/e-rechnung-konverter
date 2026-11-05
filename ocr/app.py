"""OCR service: scanned PDF in, PDF with an invisible text layer out.

Runs OCRmyPDF/Tesseract in its own container on the internal Docker network
(no internet access), so invoices never leave the server.

    POST /ocr     multipart "file" -> application/pdf (pages that already
                  contain text are passed through unchanged)
    GET  /health  {"status": "ok", "languages": [...]}

Settings (environment):
    OCR_LANG      Tesseract languages, default "deu+eng"
    OCR_PARALLEL  documents processed at the same time, default 2
    OCR_TIMEOUT   seconds per page before Tesseract gives up, default 120
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import ocrmypdf
from fastapi import FastAPI, File, HTTPException, UploadFile
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


@app.post("/ocr")
def ocr(file: UploadFile = File(...)) -> Response:  # sync: runs in FastAPI's thread pool
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
                skip_text=True,  # pages with real text stay untouched
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
    log.info("ocr ok: %s (%d -> %d bytes)", file.filename, len(data), len(result))
    return Response(result, media_type="application/pdf")
