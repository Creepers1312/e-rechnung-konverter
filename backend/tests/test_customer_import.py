"""SAP customer master import (CSV) and its use for completing invoices. All data invented."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import customer_import, customers
from app.db import Customer, get_engine, get_setting
from app.model import Address, Invoice, Party
from app.worker import IMPORT_STATUS_KEY, Folders, process_customer_files

# typical SAP Query download: Windows-1252, ";" separated, technical field names, leading zeros
SAP_CSV = (
    "KUNNR;NAME1;NAME2;STRAS;PSTLZ;ORT01;LAND1;STCEG;SMTP_ADDR;ZZLEITWEG\n"
    "0000710001;Beispiel Chemie AG;Werk Süd;Werkstraße 7;84508;Burgkirchen;D;DE 123 456 789;"
    "eingang@beispiel-chemie.example;\n"
    "0000710002;Muster Metall GmbH;;Industriestraße 1;4020;Linz;AT;ATU12345678;;\n"
    "0000710003;Stadtwerke Musterstadt;;Am Markt 1;12345;Musterstadt;DE;;rechnung@sw.example;991-12345-67\n"
    ";Ohne Nummer GmbH;;Weg 1;11111;Irgendwo;DE;;;\n"
).encode("cp1252")


def _db() -> Session:
    return sessionmaker(get_engine(), expire_on_commit=False)()


def _customer(session: Session, erp: str) -> Customer:
    return session.scalar(select(Customer).where(Customer.lookup_key == customer_import.customer_key(erp)))


def test_reads_sap_export() -> None:
    with _db() as s:
        report = customer_import.import_customers(s, SAP_CSV, "kunden.csv", {"buyer_reference": "ZZLEITWEG"})
        s.commit()
        assert (report.rows, report.skipped, report.missing_einvoice) == (4, 1, 1)
        assert report.mapping["erp_id"] == "KUNNR" and report.mapping["electronic_address"] == "SMTP_ADDR"
        assert any("keine Kundennummer" in w for w in report.warnings)

        c = _customer(s, "710001")
        p = Party.model_validate(c.party)
        assert c.source == "sap" and c.synced_at is not None
        assert (p.name, p.address.line1, p.address.line2) == ("Beispiel Chemie AG", "Werkstraße 7", "Werk Süd")
        assert (p.address.postcode, p.address.city, p.address.country_code) == ("84508", "Burgkirchen", "DE")
        assert p.vat_id == "DE123456789" and p.electronic_address == "eingang@beispiel-chemie.example"
        assert _customer(s, "710003").buyer_reference == "991-12345-67"
        assert Party.model_validate(_customer(s, "710002").party).address.country_code == "AT"


def test_german_headers_and_sap_list_format() -> None:
    listing = (
        "----------------------------------------------\n"
        "| Kundennummer | Name | Straße | PLZ | Ort | E-Mail |\n"
        "----------------------------------------------\n"
        "| 720001 | Liste & Co KG | Ringweg 3 | 54321 | Beispielstadt | ap@liste.example |\n"
    ).encode()
    with _db() as s:
        report = customer_import.import_customers(s, listing, "liste.txt")
        s.commit()
        p = Party.model_validate(_customer(s, "720001").party)
    assert report.created + report.updated == 1
    assert (p.name, p.address.city, p.electronic_address) == ("Liste & Co KG", "Beispielstadt", "ap@liste.example")


def test_unusable_file_is_rejected() -> None:
    with _db() as s, pytest.raises(customer_import.ImportError_, match="Kundennummer"):
        customer_import.import_customers(s, b"Name;Ort\nA;B\n", "x.csv")


def test_sap_master_completes_and_corrects_invoice() -> None:
    with _db() as s:
        customer_import.import_customers(s, SAP_CSV, "kunden.csv", {"buyer_reference": "ZZLEITWEG"})
        s.commit()
        # as read from a scan: customer number found, OCR misread the street, no e-invoice address
        inv = Invoice(
            buyer=Party(
                name="Stadtwerke Musterstadt",
                erp_id="710003",
                address=Address(line1="Am Markt l", postcode="12345", city="Musterstadt"),
            )
        )
        result = customers.fill(s, inv, master_wins=True)
        assert result.source == "sap"
        assert inv.buyer.address.line1 == "Am Markt 1"  # SAP wins over OCR
        assert inv.buyer.electronic_address == "rechnung@sw.example"
        assert inv.buyer_reference == "991-12345-67"
        assert customers.note(result)[0].startswith("Aus dem SAP-Kundenstamm übernommen")

        # without "SAP wins" only empty fields are filled
        inv2 = Invoice(buyer=Party(erp_id="710003", address=Address(line1="Am Markt l")))
        customers.fill(s, inv2, master_wins=False)
        assert inv2.buyer.address.line1 == "Am Markt l" and inv2.buyer.name == "Stadtwerke Musterstadt"


def test_export_does_not_overwrite_sap_master() -> None:
    with _db() as s:
        customer_import.import_customers(s, SAP_CSV, "kunden.csv")
        s.commit()
        inv = Invoice(buyer=Party(name="Falsch gelesen AG", erp_id="0000710002", contact={"phone": "+43 1 234"}))
        customers.remember(s, inv, "tester")
        s.commit()
        p = Party.model_validate(_customer(s, "710002").party)
        assert p.name == "Muster Metall GmbH"  # unchanged
        assert p.contact.phone == "+43 1 234"  # gap filled


def test_worker_imports_folder(tmp_path: Path) -> None:
    folders = Folders.under(tmp_path / "stammdaten")
    (folders.inbox / "kunden.csv").write_bytes(SAP_CSV)
    (folders.inbox / "kaputt.csv").write_bytes(b"nur;quatsch\n1;2\n")
    with _db() as s:
        r = process_customer_files(folders, s, now=1e12)
        assert (r.converted, r.failed) == (1, 1)
        assert get_setting(s, IMPORT_STATUS_KEY)["file"] == "kunden.csv"
    assert (folders.processed / "kunden.csv").exists()
    assert "Kundennummer" in (folders.error / "kaputt.csv.error.txt").read_text(encoding="utf-8")


def test_admin_upload_list_and_mapping(admin_client: TestClient, client: TestClient) -> None:
    csv = b"Debitor;Firma;Ort;Rechnungs-Mail\n730001;Upload GmbH;Testhausen;\n"
    r = admin_client.post("/api/admin/customers/import", files={"file": ("k.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["missing_einvoice"] == 1

    page = admin_client.get("/api/admin/customers", params={"q": "upload"}).json()
    assert [c["erp_id"] for c in page["items"]] == ["730001"]
    assert page["items"][0]["source"] == "sap"
    missing = admin_client.get("/api/admin/customers", params={"missing_einvoice": True, "q": "upload"}).json()
    assert missing["total"] == 1

    info = admin_client.get("/api/admin/customers/import").json()
    assert info["last"]["file"] == "k.csv" and any(f["key"] == "buyer_reference" for f in info["fields"])

    # manual mapping: the column "Rechnungs-Mail" is the e-invoice address
    settings = admin_client.get("/api/admin/settings").json()
    settings["customer_import"]["mapping"] = {"electronic_address": "Rechnungs-Mail"}
    assert admin_client.put("/api/admin/settings", json=settings).status_code == 200
    csv2 = b"Debitor;Firma;Ort;Rechnungs-Mail\n730001;Upload GmbH;Testhausen;er@upload.example\n"
    r = admin_client.post("/api/admin/customers/import", files={"file": ("k2.csv", csv2, "text/csv")})
    assert r.json()["mapping"]["electronic_address"] == "Rechnungs-Mail" and r.json()["updated"] == 1

    assert client.get("/api/admin/customers").status_code == 403
    bad = admin_client.post("/api/admin/customers/import", files={"file": ("x.csv", b"a;b\n1;2\n", "text/csv")})
    assert bad.status_code == 400
