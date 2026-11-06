"""In-app documentation: user manual (every user) and system documentation (admins only).

The texts are Markdown files in app/handbuch/, screenshots in app/handbuch/img/
(regenerated with frontend/scripts/screenshots.mjs). They are served by the API
and not as static files, so the system documentation stays behind require_admin.
The REST endpoint list of the system documentation is generated from the
application's routes and therefore always matches the running version.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from .auth import current_user, require_admin
from .db import User

router = APIRouter(prefix="/api/docs", tags=["docs"])

DOCS_DIR = Path(__file__).parent / "handbuch"
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\.png")


class Doc(BaseModel):
    markdown: str


class Endpoint(BaseModel):
    method: str
    path: str
    access: str  # "öffentlich" | "angemeldet" | "Admin"
    summary: str


class SystemDoc(Doc):
    endpoints: list[Endpoint]


# German description per endpoint (route name). A test makes sure every /api route has one.
DESCRIPTIONS: dict[str, str] = {
    "health": "Lebenszeichen für Healthchecks (ohne Anmeldung).",
    "login": "Anmelden; setzt das Sitzungs-Cookie.",
    "logout": "Abmelden; beendet die Sitzung.",
    "me": "Angemeldeter Benutzer und Rolle.",
    "change_password": "Eigenes Passwort ändern.",
    "company": "Eigene Firmendaten (Verkäufer) für neue Rechnungen.",
    "extract_pdf": "PDF hochladen und Rechnungsfelder erkennen (Scans über OCR).",
    "import_idoc": "SAP-INVOIC02-IDoc (XML) einlesen.",
    "validate_invoice": "Rechnung prüfen (EN 16931, XRechnung) und Summen berechnen.",
    "export_zugferd": "ZUGFeRD-PDF erzeugen und archivieren (nur wenn gültig, sonst 422).",
    "export_xrechnung": "XRechnung-XML erzeugen und archivieren (nur wenn gültig, sonst 422).",
    "search_customers": "Käufer-Adressbuch durchsuchen (Name, Kundennummer).",
    "documents": "Archiv durchsuchen: Text, Zeitraum, Format, Quelle, Blättern.",
    "download_document": "Archivierte E-Rechnung herunterladen.",
    "download_zip": "Mehrere archivierte E-Rechnungen als ZIP.",
    "review_list": "Offene Einträge in „Zu prüfen“.",
    "review_detail": "Eintrag aus „Zu prüfen“ mit erkannter Rechnung und Fehlern.",
    "review_pdf": "Original-PDF eines Eintrags aus „Zu prüfen“.",
    "review_discard": "Eintrag aus „Zu prüfen“ verwerfen.",
    "dashboard": "Startseite: offene Prüfungen, heute erzeugt, Status der Automatik.",
    "stats": "Kennzahlen der Startseite: pro Tag/Monat, Volumen, Automatisierungsquote, Top-Käufer.",
    "user_manual": "Anwenderhandbuch (Markdown).",
    "system_doc": "Diese Systemdokumentation inkl. Endpunktliste.",
    "image": "Screenshot der Dokumentation („admin-*“ nur für Administratoren).",
    "list_users": "Benutzer auflisten.",
    "create_user": "Benutzer anlegen (Startpasswort, muss bei der ersten Anmeldung geändert werden).",
    "update_user": "Benutzer ändern: Rolle, sperren/entsperren, Passwort zurücksetzen.",
    "read_settings": "Einstellungen lesen: Firmendaten, IDoc-/PDF-Eingang, Kundenstamm-Import.",
    "write_settings": "Einstellungen speichern (wird protokolliert).",
    "inbox_status": "Status des Workers und der Eingangsordner, fehlgeschlagene Dateien.",
    "inbox_retry": "Fehlgeschlagene Datei erneut in den Eingang legen.",
    "list_customers": "Kundenstamm/Adressbuch mit Filter „ohne E-Rechnungs-Adresse“.",
    "customer_import_info": "Letzter Kundenstamm-Import und zuordenbare Felder.",
    "customer_import_upload": "Kundenstamm-CSV hochladen und sofort importieren.",
    "list_countries": "Länderliste (ISO-Kürzel und Name) für die Länderauswahl.",
    "companies": "Eigene Firmen (Auswahl im Editor); die erste ist der Standard.",
    "pdf_words": "Wörter mit Position aus einem PDF (Scans per OCR) – für die Feldverknüpfung in der Vorschau.",
    "linkable_fields": "Formularfelder, die mit einer Stelle im PDF verknüpft werden können.",
    "list_links": "Gespeicherte Feldverknüpfungen einer Firma.",
    "save_link": "Feldverknüpfung speichern: Feld X steht bei Firma Y an dieser Stelle (wird protokolliert).",
    "delete_link": "Feldverknüpfung löschen (wird protokolliert).",
    "read_audit": "Protokoll: Anmeldungen, Exporte, Downloads, Änderungen.",
}


def _read(name: str) -> str:
    return (DOCS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _calls(dependant) -> set:
    found = set()
    for dep in dependant.dependencies:
        found.add(dep.call)
        found |= _calls(dep)
    return found


def _api_routes(routes) -> list[APIRoute]:
    """All APIRoutes, also those of included routers (FastAPI >= 0.16x keeps them nested).
    Our routers carry their full prefix themselves (include_router is called without one)."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif (inner := getattr(route, "original_router", None)) is not None:
            found += _api_routes(inner.routes)
    return found


def endpoints(request: Request) -> list[Endpoint]:
    result = []
    for route in _api_routes(request.app.routes):
        if not route.path.startswith("/api"):
            continue
        calls = _calls(route.dependant)
        access = "Admin" if require_admin in calls else "angemeldet" if current_user in calls else "öffentlich"
        doc = DESCRIPTIONS.get(route.name) or route.summary or route.name.replace("_", " ")
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            result.append(Endpoint(method=method, path=route.path, access=access, summary=doc))
    return sorted(result, key=lambda e: (e.path, e.method))


@router.get("/anwender", response_model=Doc)
def user_manual(_: User = Depends(current_user)) -> Doc:
    """User manual (Markdown)."""
    return Doc(markdown=_read("anwender"))


@router.get("/system", response_model=SystemDoc)
def system_doc(request: Request, _: User = Depends(require_admin)) -> SystemDoc:
    """System documentation (Markdown) plus the generated REST endpoint list."""
    return SystemDoc(markdown=_read("system"), endpoints=endpoints(request))


@router.get("/img/{name}")
def image(name: str, user: User = Depends(current_user)) -> FileResponse:
    """Screenshot of the documentation ("admin-*.png" only for admins)."""
    path = DOCS_DIR / "img" / name
    if not IMAGE_NAME.fullmatch(name) or not path.is_file():
        raise HTTPException(404, "Bild nicht gefunden.")
    if name.startswith("admin-") and user.role != "admin":
        raise HTTPException(403, "Nur für Administratoren.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
