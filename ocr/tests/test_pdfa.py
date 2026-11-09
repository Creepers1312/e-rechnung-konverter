"""PDF/A-3 conversion (Ghostscript via OCRmyPDF) and validation (veraPDF).

/verify needs veraPDF (`python install_verapdf.py DIR`, then VERAPDF=DIR/verapdf),
/pdfa needs Ghostscript – both are installed in CI and in the container.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import app as service

client = TestClient(service.app)
FIXTURES = Path(__file__).parent / "fixtures"

needs_verapdf = pytest.mark.skipif(shutil.which(service.VERAPDF) is None, reason="veraPDF not installed")
needs_ghostscript = pytest.mark.skipif(
    shutil.which("gs") is None and shutil.which("gswin64c") is None, reason="Ghostscript not installed"
)


def plain_pdf() -> bytes:
    """A typical invoice PDF: standard fonts not embedded, no output intent -> not PDF/A."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, 780, "Rechnung RE-2026-0042")
    c.setFont("Helvetica", 11)
    c.setFillGray(0.4)
    c.drawString(60, 750, "Beratung  8 h  760,00 EUR")
    c.save()
    return buf.getvalue()


def post(path: str, pdf: bytes, **data: str):
    return client.post(path, files={"file": ("rechnung.pdf", pdf, "application/pdf")}, data=data or None)


# --- veraPDF report parsing (no external tools) --------------------------------


def test_parse_verapdf_lists_failed_rules() -> None:
    report = {
        "report": {
            "jobs": [
                {
                    "validationResult": [
                        {
                            "compliant": False,
                            "profileName": "PDF/A-3b validation profile",
                            "details": {
                                "ruleSummaries": [
                                    {
                                        "ruleStatus": "FAILED",
                                        "clause": "6.2.11.4.1",
                                        "testNumber": 1,
                                        "failedChecks": 2,
                                        "description": "The font programs  shall be\n embedded",
                                        "checks": [
                                            {"status": "failed", "errorMessage": "The font program is not embedded"}
                                        ],
                                    }
                                ]
                            },
                        }
                    ]
                }
            ]
        }
    }
    result = service.parse_verapdf("log line before the JSON\n" + json.dumps(report))
    assert result["compliant"] is False
    assert result["failed_rules"] == [
        {
            "clause": "6.2.11.4.1",
            "test": 1,
            "description": "The font programs shall be embedded",
            "failed_checks": 2,
            "message": "The font program is not embedded",
        }
    ]


def test_parse_verapdf_without_result_is_an_error() -> None:
    report = {"report": {"jobs": [{"taskException": {"message": "Couldn't parse stream"}}]}}
    with pytest.raises(ValueError):
        service.parse_verapdf(json.dumps(report))


def test_verify_rejects_bad_input() -> None:
    assert post("/verify", b"kein pdf").status_code == 400
    assert post("/verify", plain_pdf(), flavour="3b; rm -rf /").status_code == 400


# --- veraPDF -------------------------------------------------------------------


@needs_verapdf
def test_verify_finds_what_makes_a_pdf_not_pdfa() -> None:
    r = post("/verify", plain_pdf())
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["compliant"] is False
    assert "PDF/A-3" in result["profile"]
    assert "6.2.11.4.1" in [rule["clause"] for rule in result["failed_rules"]]  # fonts not embedded


@needs_verapdf
def test_verify_unreadable_pdf_is_422() -> None:
    assert post("/verify", b"%PDF-1.7\nnothing else").status_code == 422


# --- Ghostscript (+ veraPDF) ---------------------------------------------------


@needs_ghostscript
def test_pdfa_output_declares_pdfa_3b() -> None:
    import pikepdf

    r = post("/pdfa", plain_pdf())
    assert r.status_code == 200, r.text
    with pikepdf.open(io.BytesIO(r.content)) as pdf:
        assert pdf.open_metadata().pdfa_status == "3B"


@needs_ghostscript
@needs_verapdf
def test_zugferd_built_on_the_converted_pdf_is_pdfa_3b() -> None:
    """The real chain of the backend: PDF -> /pdfa -> embed CII XML (factur-x) -> /verify."""
    facturx = pytest.importorskip("facturx")
    converted = post("/pdfa", plain_pdf())
    assert converted.status_code == 200, converted.text
    zugferd = facturx.generate_from_binary(
        converted.content,
        (FIXTURES / "factur-x.xml").read_bytes(),
        flavor="factur-x",
        level="en16931",
        check_xsd=True,
        lang="de-DE",
    )
    r = post("/verify", zugferd)
    assert r.status_code == 200, r.text
    assert r.json()["compliant"] is True, r.json()["failed_rules"]
