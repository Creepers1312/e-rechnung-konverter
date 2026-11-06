"""Several own companies and click-to-link fields (all companies and data invented)."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session, sessionmaker

from app import field_links
from app.company import Company
from app.db import FieldLink, Setting, get_engine
from app.erp.sap_ecc_idoc import pick_company
from app.extraction.pdf_text import PdfText
from app.model import Party, PaymentInfo
from app.settings import assign_ids, detect_company

ALPHA = Company(
    seller=Party(name="Alpha Kunststoff GmbH", vat_id="DE111111111"), payment=PaymentInfo(iban="DE02100100100006820101")
)
BETA = Company(seller=Party(name="Beta Carbon GmbH", vat_id="DE222222222"), keywords=["Werk Beta"])
GAMMA = Company(seller=Party(name="Gamma Logistik GmbH", vat_id="DE333333333"))


def _db() -> Session:
    return sessionmaker(get_engine(), expire_on_commit=False)()


@pytest.fixture
def three_companies(admin_client: TestClient) -> Iterator[list[dict]]:
    """Store three companies; restore the previous settings afterwards (the test DB is shared)."""
    with _db() as s:
        before = {k: s.get(Setting, k) for k in ("companies", "company")}
        saved = {k: (v.value if v else None) for k, v in before.items()}
    settings = admin_client.get("/api/admin/settings").json()
    settings["companies"] = [c.model_dump(mode="json") for c in (ALPHA, BETA, GAMMA)]
    r = admin_client.put("/api/admin/settings", json=settings)
    assert r.status_code == 200, r.text
    yield r.json()["companies"]
    with _db() as s:
        for key, value in saved.items():
            row = s.get(Setting, key)
            if value is None and row is not None:
                s.delete(row)
            elif value is not None:
                s.merge(Setting(key=key, value=value))
        s.query(FieldLink).delete()
        s.commit()


def _pdf(company: str, label_y: float, number: str, extra_lines: int = 0) -> bytes:
    """A letterhead with an unusual label ("Vorgang") the generic rules do not know."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    c.setFont("Helvetica", 10)
    c.drawString(60, h - 60, company)
    for i in range(extra_lines):  # pushes the block down: the anchor must follow
        c.drawString(330, h - label_y + 14 * (i + 1), f"Zusatzzeile {i + 1}")
    y = h - label_y - 14 * extra_lines
    c.drawString(330, y, "Vorgang")
    c.drawString(420, y, number)
    c.drawString(330, y - 14, "Datum 03.11.2026")
    c.save()
    return buf.getvalue()


def _word(pages: list[dict], text: str) -> tuple[int, dict]:
    for p in pages:
        for w in p["words"]:
            if w["text"] == text:
                return p["page"], w
    raise AssertionError(f"{text} not found")


def test_ids_are_unique_and_stable() -> None:
    a, b = Company(seller=Party(name="Müller & Söhne GmbH")), Company(seller=Party(name="Müller & Söhne GmbH"))
    ids = [c.id for c in assign_ids([a, b])]
    assert ids == ["mueller-soehne-gmbh", "mueller-soehne-gmbh-2"]
    assert assign_ids([Company(id="fest", seller=Party(name="Anders"))])[0].id == "fest"
    assert assign_ids([Company()])[0].id == "firma-1"  # single legacy company


def test_detect_company_by_identifiers() -> None:
    companies = assign_ids([c.model_copy(deep=True) for c in (ALPHA, BETA, GAMMA)])
    assert detect_company("USt-IdNr.: DE 222 222 222", companies).id == companies[1].id
    assert detect_company("IBAN DE02 1001 0010 0006 8201 01", companies).id == companies[0].id
    assert detect_company("Lieferung ab Werk Beta", companies).id == companies[1].id
    assert detect_company("Gamma Logistik GmbH · Hafenweg 1", companies).id == companies[2].id
    assert detect_company("nichts Bekanntes", companies).id == companies[0].id  # default


def test_idoc_company_by_own_vat_id() -> None:
    companies = assign_ids([c.model_copy(deep=True) for c in (ALPHA, BETA, GAMMA)])
    xml = b"<INVOIC02><IDOC><E1EDK01><EIGENUINR>DE333333333</EIGENUINR></E1EDK01></IDOC></INVOIC02>"
    assert pick_company(xml, companies).id == companies[2].id
    assert pick_company(b"kaputt", companies).id == companies[0].id


def test_read_value_follows_anchor_and_longer_values() -> None:
    first = PdfText.from_bytes(_pdf("Beta Carbon GmbH", 150, "VG-4711"))
    page = first.pages()[0]
    w = next(w for w in page.words if w.text == "VG-4711")
    anchor = next(w for w in page.words if w.text == "Vorgang")
    link = FieldLink(
        company_id="x", field="number", page=1, x0=w.x0, top=w.top, x1=w.x1, bottom=w.bottom,
        anchor_text="Vorgang", anchor_x0=anchor.x0, anchor_top=anchor.top,
    )  # fmt: skip
    shifted = PdfText.from_bytes(_pdf("Beta Carbon GmbH", 150, "VG-2026-000123 A", extra_lines=3))
    assert field_links.read_value(link, shifted.pages()) == "VG-2026-000123 A"
    assert field_links.convert("issue_date", "Datum 03.11.2026").isoformat() == "2026-11-03"  # type: ignore[union-attr]
    assert field_links.convert("buyer.vat_id", "de 123 456 789") == "DE123456789"


def test_link_in_preview_is_used_for_the_next_invoice(
    three_companies: list[dict], client: TestClient, admin_client: TestClient
) -> None:
    alpha, beta, _ = (c["id"] for c in three_companies)
    assert [c["id"] for c in client.get("/api/companies").json()] == [alpha, beta, three_companies[2]["id"]]

    first = _pdf("Beta Carbon GmbH – Werk Beta", 150, "VG-4711")
    result = client.post("/api/extract", files={"file": ("a.pdf", first, "application/pdf")}).json()
    assert result["company_id"] == beta  # detected by the keyword
    assert result["invoice"]["seller"]["name"] == "Beta Carbon GmbH"
    assert result["invoice"]["number"] != "VG-4711"  # the rules do not know "Vorgang"

    words = client.post("/api/pdf/words", files={"file": ("a.pdf", first, "application/pdf")}).json()
    page, w = _word(words["pages"], "VG-4711")
    _, anchor = _word(words["pages"], "Vorgang")
    link = {
        "company_id": beta,
        "field": "number",
        "page": page,
        **{k: w[k] for k in ("x0", "top", "x1", "bottom")},
        "anchor_text": "Vorgang",
        "anchor_x0": anchor["x0"],
        "anchor_top": anchor["top"],
        "sample": "VG-4711",
    }
    r = client.put("/api/field-links", json=link)
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Rechnungsnummer"

    # next invoice of the same company, block moved down, longer number
    nxt = _pdf("Beta Carbon GmbH – Werk Beta", 150, "VG-4712-B", extra_lines=2)
    result = client.post("/api/extract", files={"file": ("b.pdf", nxt, "application/pdf")}).json()
    assert result["invoice"]["number"] == "VG-4712-B"
    assert any("Feldverknüpfungen" in n and "Rechnungsnummer" in n for n in result["notes"])
    assert not any("Rechnungsnummer nicht erkannt" in w for w in result["warnings"])

    # links are per company: forcing Alpha does not use Beta's link
    forced = client.post(
        "/api/extract", files={"file": ("b.pdf", nxt, "application/pdf")}, data={"company_id": alpha}
    ).json()
    assert forced["company_id"] == alpha and forced["invoice"]["number"] != "VG-4712-B"
    assert forced["invoice"]["seller"]["name"] == "Alpha Kunststoff GmbH"

    # listing, audit, delete
    [saved] = client.get("/api/field-links", params={"company_id": beta}).json()
    assert saved["updated_by"] and saved["sample"] == "VG-4711"
    assert any(e["action"] == "field_linked" for e in admin_client.get("/api/admin/audit").json())
    assert client.delete(f"/api/field-links/{saved['id']}").status_code == 204
    assert client.get("/api/field-links", params={"company_id": beta}).json() == []


def test_link_validation_and_cleanup(three_companies: list[dict], client: TestClient, admin_client: TestClient) -> None:
    gamma = three_companies[2]["id"]
    box = {"page": 1, "x0": 10, "top": 10, "x1": 50, "bottom": 20}
    assert (
        client.put("/api/field-links", json={"company_id": "gibtsnicht", "field": "number", **box}).status_code == 404
    )
    bad = client.put("/api/field-links", json={"company_id": gamma, "field": "lines.0.name", **box})
    assert bad.status_code == 400
    assert (
        client.put("/api/field-links", json={"company_id": gamma, "field": "order_reference", **box}).status_code == 200
    )
    assert any(f["path"] == "buyer.name" for f in client.get("/api/field-links/fields").json())

    # removing a company in the settings removes its links
    settings = admin_client.get("/api/admin/settings").json()
    settings["companies"] = settings["companies"][:2]
    assert admin_client.put("/api/admin/settings", json=settings).status_code == 200
    assert client.get("/api/field-links", params={"company_id": gamma}).json() == []
    settings["companies"] = []
    assert admin_client.put("/api/admin/settings", json=settings).status_code == 422


def test_anchor_label_printed_close_to_the_value() -> None:
    """ "Beleg-Nr.: 4711" printed as one run of text: the label is still found when the layout shifts."""

    def pdf(y_shift: float, number: str) -> PdfText:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        _, h = A4
        c.setFont("Helvetica", 10)
        c.drawString(330, h - 150 - y_shift, f"Beleg-Nr.: {number}")
        c.save()
        return PdfText.from_bytes(buf.getvalue())

    words = pdf(0, "4711").pages()[0].words
    label, value = words[0], words[1]
    link = FieldLink(
        company_id="x", field="number", page=1, x0=value.x0, top=value.top, x1=value.x1, bottom=value.bottom,
        anchor_text="Beleg-Nr.:", anchor_x0=label.x0, anchor_top=label.top,
    )  # fmt: skip
    assert field_links.read_value(link, pdf(40, "4712-B").pages()) == "4712-B"
