# E-Rechnung Konverter

Webapp, die Rechnungen in E-Rechnungen nach EN 16931 umwandelt:

- **PDF-Rechnung → ZUGFeRD / Factur-X** (PDF mit eingebettetem XML, Profil EN 16931)
- **PDF-Rechnung → XRechnung 3.0** (CII-XML)
- **SAP ECC (INVOIC02-IDoc) → ZUGFeRD / XRechnung**: Hier gibt es keine Texterkennung, weil die Daten direkt aus SAP kommen.

Ablauf: Datei hochladen → Felder werden regelbasiert erkannt → im Formular prüfen
und ergänzen → Pflichtfelder und Summen werden laufend geprüft → herunterladen.

**Komfortfunktionen**

- **Stapelverarbeitung:** mehrere PDFs/IDocs auf einmal hochladen – fehlerfreie werden
  sofort umgewandelt, nur die mit Fehlern müssen geöffnet und korrigiert werden;
  alle Ergebnisse als ZIP.
- **PDF-Vorschau** neben dem Prüfformular (abschaltbar, die Einstellung wird gemerkt).
- **Käufer-Adressbuch:** jede erzeugte E-Rechnung merkt sich den Käufer (über die
  Kundennummer, sonst den Namen). Fehlen bei der nächsten Rechnung z. B.
  E-Rechnungs-Adresse oder Leitweg-ID, werden sie automatisch ergänzt – auch beim
  SAP-Worker. Vorhandene Werte werden nie überschrieben. Manuelle Suche über
  „Aus Adressbuch übernehmen“ im Käufer-Bereich.
- **Archiv-Suche:** nach Rechnungsnummer/Käufer, Zeitraum, Format und Quelle; Blättern;
  mehrere Einträge als ZIP herunterladen.
- **SAP-Eingang (Admin):** Status des Workers, Dateien je Ordner, fehlgeschlagene IDocs
  mit Fehlergrund und „Erneut verarbeiten“.
- **Automatische Datenbank-Backups** (siehe „Backup & Wiederherstellung“).

> Rechtlicher Hinweis: Eine E-Rechnung muss vom **Rechnungssteller** ausgestellt
> werden. Die App ist dafür gedacht, die *eigenen* Rechnungen umzuwandeln.
> Fragen zur Pflicht und zur Archivierung (GoBD) bitte mit der Steuerberatung klären.

## Betrieb mit Docker Compose (empfohlen)

Jeder Teil läuft in einem eigenen Container:

| Dienst | Aufgabe | Von außen erreichbar |
|---|---|---|
| `frontend` | nginx: Weboberfläche + Weiterleitung von `/api` | ja, Port 8080 |
| `backend` | FastAPI (Erkennung, Prüfung, Export) | nein |
| `worker` | überwacht den SAP-IDoc-Eingangsordner, wandelt automatisch um | nein, ohne Internet |
| `db` | PostgreSQL: Archiv aller erzeugten E-Rechnungen | nein |
| `backup` | tägliche Datenbanksicherung (`pg_dump`) nach `./backups` | nein, ohne Internet |

Netzwerke: `web` (frontend ↔ backend) und `data` (backend, worker, db; intern,
ohne Internetzugang). Die Oberfläche kann die Datenbank also nicht direkt erreichen.

```bash
cp .env.example .env            # Passwort für die Datenbank setzen
mkdir -p data/idoc && sudo chown -R 10001 data/idoc   # Container läuft als UID 10001
docker compose up -d --build
# -> http://localhost:8080
```

Danach mit dem Admin aus `.env` anmelden (siehe „Anmeldung & Rollen“) und unter
**Verwaltung → Einstellungen & Konnektoren** die eigenen Firmendaten eintragen.

**SAP-Eingang:** ECC legt INVOIC02-Dateien in `data/idoc/inbox/` ab (Pfad über
`IDOC_HOST_DIR` änderbar, z. B. eine Netzwerkfreigabe). Der Worker erzeugt die
E-Rechnung in `outbox/`, verschiebt das IDoc nach `processed/` bzw. bei Fehlern
nach `error/` (mit `.error.txt` daneben) und archiviert jede Rechnung in der
Datenbank. Format über `IDOC_PROFILE` (`zugferd` oder `xrechnung`).

```bash
docker compose ps                 # Status aller Dienste
docker compose logs -f worker     # was der Worker gerade macht
docker compose down               # stoppen (Daten bleiben im Volume pgdata)
```

## Anmeldung & Rollen

Ohne Anmeldung ist nichts erreichbar (außer `/api/health`).

| | Benutzer | Administrator |
|---|---|---|
| Rechnungen hochladen, prüfen, umwandeln | ✓ | ✓ |
| Archiv ansehen und herunterladen (alle Rechnungen) | ✓ | ✓ |
| Benutzer anlegen, sperren, Rolle ändern, Passwort zurücksetzen | – | ✓ |
| Einstellungen & Konnektoren: Firmendaten, SAP-IDoc-Eingang (an/aus, Format, Intervall) | – | ✓ |
| Protokoll (Anmeldungen, Fehlversuche, Exporte, Downloads, Änderungen) | – | ✓ |

**Erster Start:** Der erste Administrator wird aus `ADMIN_USER` / `ADMIN_PASSWORD`
(`.env`) angelegt, solange es noch keinen Benutzer gibt. Das Startpasswort muss
bei der ersten Anmeldung geändert werden – das gilt auch für alle Benutzer, die
ein Admin anlegt oder deren Passwort er zurücksetzt.

**Notfall** (niemand kann sich mehr anmelden):
```bash
docker compose exec backend python -m app.manage reset-password admin
docker compose exec backend python -m app.manage create-admin chef
```

**Sicherheit im Überblick:** Passwörter mit Argon2id gehasht (mind. 10 Zeichen);
Sitzung per HttpOnly-/SameSite=Strict-Cookie, in der Datenbank liegt nur ein Hash
des Tokens; Abmeldung nach `SESSION_IDLE_MINUTES` Inaktivität bzw. spätestens
nach `SESSION_MAX_HOURS`; Sperre nach 5 Fehlversuchen für 5 Minuten; Schutz-Header
gegen CSRF bei allen schreibenden Anfragen; der letzte aktive Admin kann sich
nicht selbst entfernen; Sperren eines Benutzers beendet seine Sitzungen sofort.

**HTTPS:** Sobald die App nicht nur lokal läuft, gehört ein HTTPS-Reverse-Proxy
davor (z. B. vorhandener Firmen-Proxy, Caddy oder Traefik) und in `.env`
`COOKIE_SECURE=true` – sonst gehen Passwörter unverschlüsselt durchs Netz.

Die Firmendaten und Konnektor-Einstellungen werden nach dem ersten Speichern in
der Datenbank gehalten; `company.yaml` und `IDOC_PROFILE`/`IDOC_INTERVAL` sind
nur noch Startwerte.

## Backup & Wiederherstellung

Der Dienst `backup` sichert die Datenbank beim Start (falls es für heute noch keine
Sicherung gibt) und danach täglich ab `BACKUP_TIME` (UTC) nach `BACKUP_HOST_DIR`
(Standard `./backups`). Sicherungen älter als `BACKUP_KEEP_DAYS` Tage werden gelöscht.
Der Container gilt als „unhealthy“, wenn die neueste Sicherung älter als 26 Stunden ist.

```bash
docker compose exec backup /bin/sh /backup.sh now   # sofort sichern
ls backups/                                         # erechnung-JJJJ-MM-TT_HHMMSS.dump
```

**Wiederherstellen** (überschreibt die aktuelle Datenbank):

```bash
docker compose stop backend worker
docker compose exec backup pg_restore --clean --if-exists --no-owner -d erechnung /backups/<datei>.dump
docker compose start backend worker
```

Den Backup-Ordner zusätzlich auf ein anderes System kopieren (NAS, Bandsicherung …) –
eine Sicherung auf derselben Festplatte schützt nicht vor deren Ausfall.
Die IDoc-Ordner (`data/idoc`) gehören ebenfalls in die normale Dateisicherung.

## Lokale Entwicklung ohne Docker

Voraussetzungen: Python ≥ 3.11, Node ≥ 20, pnpm. Ohne `DATABASE_URL` nutzt das
Backend eine lokale SQLite-Datei (`backend/erechnung.db`).

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
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .   # Lint/Format
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
- Automatischer Import: der `worker`-Dienst überwacht den Eingangsordner
  (siehe „Betrieb mit Docker Compose“).
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
  app/convert.py            Prüfen + Datei erzeugen (gemeinsam für API und Worker)
  app/db.py                 Archiv (PostgreSQL / SQLite) mit SQLAlchemy
  app/worker.py             SAP-IDoc-Eingangsordner -> E-Rechnungen
  app/auth.py               Anmeldung, Sitzungen, Rollen, Schutz gegen Passwort-Raten
  app/admin.py              Endpunkte für Anmeldung und Verwaltung (/api/auth, /api/admin)
  app/settings.py           Einstellungen aus der Datenbank (Firmendaten, SAP-Konnektor)
  app/customers.py          Käufer-Adressbuch (merken, ergänzen, suchen)
  app/manage.py             Kommandozeile: Admin anlegen, Passwort zurücksetzen
  app/main.py               FastAPI-Endpunkte (Umwandeln, Archiv)
frontend/                   React + Vite + TypeScript (Upload, Prüfformular, Export, Archiv)
compose.yaml                frontend, backend, worker, db, backup
backup/backup.sh            Sicherungsskript des backup-Dienstes
samples/                    Beispiel-PDF und Beispiel-IDoc
scripts/make_sample_pdf.py  Erzeugt samples/beispielrechnung.pdf neu
```

## Qualitätssicherung

- **CI** (`.github/workflows/ci.yml`) bei jedem Push und Pull Request: Backend-Lint und
  -Format (ruff), Backend-Tests (pytest), Frontend-Typecheck und -Build, Docker-Build.
- **Vorlagen:** Pull-Request-Vorlage mit Prüfliste; Issue-Vorlagen für Fehler,
  Funktionswünsche und schlecht erkannte Rechnungslayouts.
- **Dependabot** schlägt wöchentlich Updates für Python, npm, Docker-Images und
  GitHub Actions vor.
- Details zur Mitarbeit: `CONTRIBUTING.md`, Sicherheit: `SECURITY.md`.

## Roadmap

- [x] Grundgerüst, Rechnungsmodell, PDF-Upload mit regelbasierter Erkennung, Prüfformular
- [x] Export ZUGFeRD (EN 16931) und XRechnung 3.0 (CII) inkl. XSD-Prüfung
- [x] SAP ECC: INVOIC02-IDoc einlesen (Upload + Ordner-Adapter)
- [x] Docker Compose: getrennte Dienste, Archiv in PostgreSQL, Worker für den IDoc-Eingang
- [ ] Offizielle Prüfung mit dem **KoSIT-Validator** (Schematron, Java) einbinden
- [ ] Echte **PDF/A-3**-Umwandlung (Ghostscript) + Prüfung mit veraPDF
- [ ] Versand der E-Rechnungen (E-Mail / PEPPOL) direkt aus dem Worker
- [ ] Datenbank-Migrationen (Alembic) vor der ersten Schemaänderung
- [ ] Texterkennung (OCR) für eingescannte PDFs
- [ ] Rabatte und Zuschläge (Allowances/Charges), Teilzahlungen, Skonto
- [x] Anmeldung mit Rollen (Admin / Benutzer), Protokoll
- [x] Stapelverarbeitung, PDF-Vorschau, Käufer-Adressbuch, Archiv-Suche, SAP-Eingang-Status, Backups
- [ ] Firmen-Login (Microsoft Entra ID / SSO), Mandanten
- [ ] GoBD-konforme Archivierung (das Archiv ist bisher eine einfache Ablage)
