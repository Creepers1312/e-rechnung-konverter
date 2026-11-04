# E-Rechnung Konverter

Webapp, die Rechnungen in E-Rechnungen nach EN 16931 umwandelt:

- **PDF-Rechnung → ZUGFeRD / Factur-X** (PDF mit eingebettetem XML, Profil EN 16931)
- **PDF-Rechnung → XRechnung 3.0** (CII-XML)
- **SAP ECC (INVOIC02-IDoc) → ZUGFeRD / XRechnung**: Hier gibt es keine Texterkennung, weil die Daten direkt aus SAP kommen.

Ablauf: Datei hochladen → Felder werden regelbasiert erkannt → im Formular prüfen
und ergänzen → Pflichtfelder und Summen werden laufend geprüft → herunterladen.

> Rechtlicher Hinweis: Eine E-Rechnung muss vom **Rechnungssteller** ausgestellt
> werden. Die App ist dafür gedacht, die *eigenen* Rechnungen umzuwandeln.
> Fragen zur Pflicht und zur Archivierung (GoBD) bitte mit der Steuerberatung klären.

## Schnellstart

Voraussetzungen: Python ≥ 3.11, Node ≥ 20, pnpm.

```bash
# Backend (Port 8000)
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload

# Frontend (Port 5173, leitet /api an das Backend weiter) – zweites Terminal
cd frontend
pnpm install
pnpm dev
```

Dann http://localhost:5173 öffnen und z. B. `samples/beispielrechnung.pdf` oder
`samples/sap-ecc-invoic02.xml` hochladen.

```bash
cd backend && .venv/bin/pytest      # Backend-Tests
cd frontend && pnpm typecheck       # Frontend-Typprüfung
```

## Eigene Firmendaten

Die Verkäuferdaten (Name, Anschrift, USt-IdNr., Kontakt, Bankverbindung) kommen
aus `backend/config/company.yaml`. Als Vorlage dient `company.example.yaml`.
`company.yaml` ist in `.gitignore` eingetragen. Alternativ kann der Pfad über
`ERECHNUNG_COMPANY_FILE` gesetzt werden.

## Neue Rechnungslayouts anlernen (regelbasiert)

Die Erkennung arbeitet mit YAML-Vorlagen in `backend/app/extraction/templates/`:

- `_generic.yaml` enthält allgemeine Regeln für deutsche Rechnungen und gilt immer.
- Jede weitere `*.yaml` ist eine **Layout-Vorlage**. Sie greift, sobald einer der
  Texte unter `match.any` im PDF vorkommt, und überschreibt nur die Regeln, die
  sie selbst angibt. Ein Beispiel ist `beispiel-layout.yaml`.

Für ein neues Layout ist also keine Programmierung nötig: Datei kopieren,
Suchmuster anpassen, fertig. Welche Vorlage gegriffen hat, zeigt die App über dem Formular an.

## SAP ECC

Der Weg: ECC gibt pro Faktura ein **INVOIC02-IDoc als XML-Datei** aus
(Nachrichtenfindung + XML-Datei-Port). Die App liest diese Dateien. Dafür sind
kein SAP-SDK, kein RFC-Benutzer und kein Netzwerkzugriff auf SAP nötig.

- Manueller Import: die `.xml`-Datei in der Oberfläche hochladen.
- Automatischer Import: `SapEccIdocFolderAdapter` (Ordner-Eingang) ist vorhanden,
  ein Hintergrundjob dafür folgt in der nächsten Ausbaustufe.
- Die Zuordnung von Segmenten und Qualifiern steht in `MAPPING` in
  `backend/app/erp/sap_ecc_idoc.py`. **Bitte mit einem echten IDoc aus eurem
  System abgleichen** (WE02). Kundenspezifische Anpassungen sind bei ECC üblich.

Ein späterer Wechsel auf S/4HANA bedeutet nur einen neuen Adapter, der dieselbe
Schnittstelle (`backend/app/erp/base.py`) erfüllt. Der Rest bleibt unverändert.

## Aufbau

```
backend/
  app/model.py              Neutrales Rechnungsmodell (EN 16931) + Summenberechnung
  app/extraction/           PDF-Text + regelbasierte Erkennung (YAML-Vorlagen)
  app/erp/                  ERP-Adapter-Schnittstelle + SAP ECC (INVOIC02)
  app/export/cii.py         CII-XML für ZUGFeRD und XRechnung
  app/export/zugferd.py     XML in PDF einbetten (factur-x) + XSD-Prüfung
  app/export/pdf_render.py  Einfaches Rechnungs-PDF, wenn kein Quell-PDF existiert
  app/validation.py         Pflichtfeld-, IBAN- und Summenprüfung
  app/main.py               FastAPI-Endpunkte
frontend/                   React + Vite + TypeScript (Upload, Prüfformular, Export)
samples/                    Beispiel-PDF und Beispiel-IDoc
scripts/make_sample_pdf.py  Erzeugt samples/beispielrechnung.pdf neu
```

## Roadmap

- [x] Grundgerüst, Rechnungsmodell, PDF-Upload mit regelbasierter Erkennung, Prüfformular
- [x] Export ZUGFeRD (EN 16931) und XRechnung 3.0 (CII) inkl. XSD-Prüfung
- [x] SAP ECC: INVOIC02-IDoc einlesen (Upload + Ordner-Adapter)
- [ ] Offizielle Prüfung mit dem **KoSIT-Validator** (Schematron, Java) einbinden
- [ ] Echte **PDF/A-3**-Umwandlung (Ghostscript) + Prüfung mit veraPDF
- [ ] Hintergrundjob: IDoc-Eingangsordner überwachen, automatisch umwandeln und ablegen bzw. versenden
- [ ] Texterkennung (OCR) für eingescannte PDFs
- [ ] Rabatte und Zuschläge (Allowances/Charges), Teilzahlungen, Skonto
- [ ] Anmeldung, Mandanten, Archivierung
