"""Country picker data, country names in addresses, foreign addresses without postcode."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.company import Company
from app.countries import country_code
from app.extraction.pdf_text import PdfText
from app.extraction.rules import extract
from app.model import Address, Invoice
from app.validation import validate
from tests.test_recognition import _pdf

HEADER = ["Rechnung", "Rechnungsnummer: RE-1", "Rechnungsdatum: 03.11.2026"]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("COSTA RICA", "CR"),
        ("Österreich", "AT"),
        ("Oesterreich", "AT"),
        ("España", "ES"),
        ("ESPANA", "ES"),
        ("Schweiz", "CH"),
        ("United States", "US"),
        ("USA", "US"),
        ("Deutschland", "DE"),
        ("fr", "FR"),
        ("Musterstraße 1", None),
        ("XY", None),
    ],
)
def test_country_code(text: str, code: str | None) -> None:
    assert country_code(text) == code


def test_country_list_endpoint(client: TestClient) -> None:
    items = client.get("/api/countries").json()
    assert {"code": "CR", "name": "Costa Rica"} in items and {"code": "DE", "name": "Deutschland"} in items
    assert len(items) > 240


def test_foreign_address_without_postcode(company: Company) -> None:
    pdf = PdfText.from_bytes(
        _pdf(HEADER, ["Ejemplo Importaciones S.A.", "Calle 5, Avenida 3 # 120", "BARRIO AMÓN, SAN JOSÉ", "COSTA RICA"])
    )
    buyer = extract(pdf, company).invoice.buyer
    assert buyer.name == "Ejemplo Importaciones S.A."
    a = buyer.address
    assert (a.line1, a.line2, a.postcode, a.city, a.country_code) == (
        "Calle 5, Avenida 3 # 120", "BARRIO AMÓN", "", "SAN JOSÉ", "CR",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("city_line", "country", "expected"),
    [
        ("SAN JOSÉ 10101", "COSTA RICA", ("10101", "SAN JOSÉ", "CR")),
        ("75001 PARIS", "FRANCE", ("75001", "PARIS", "FR")),  # German-style line, country below
        ("1010 Wien", "Österreich", ("1010", "Wien", "AT")),
    ],
)
def test_foreign_postcodes(company: Company, city_line: str, country: str, expected: tuple) -> None:
    pdf = PdfText.from_bytes(_pdf(HEADER, ["Beispiel Kunde", "Hauptweg 1", city_line, country]))
    a = extract(pdf, company).invoice.buyer.address
    assert (a.postcode, a.city, a.country_code) == expected


def test_postcode_required_only_where_needed(invoice: Invoice) -> None:
    invoice.buyer.address = Address(line1="Calle 5", city="SAN JOSÉ", country_code="CR")
    codes = lambda profile: {i.code for i in validate(invoice, profile)}  # noqa: E731
    assert "BR-10" not in codes("zugferd")  # abroad: city is enough
    assert "BR-10" in codes("xrechnung")  # XRechnung requires the postcode
    invoice.buyer.address = Address(line1="Hauptstraße 5", city="Berlin", country_code="DE")
    assert "BR-10" in codes("zugferd")  # German address without postcode
    invoice.buyer.address = Address(line1="Calle 5", postcode="", city="", country_code="CR")
    assert "BR-10" in codes("zugferd")  # the city is always needed
