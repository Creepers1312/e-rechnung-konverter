# E-Rechnung Konverter

Webapp, die Rechnungen in E-Rechnungen nach EN 16931 umwandelt:

- **PDF-Rechnung → ZUGFeRD / Factur-X** (PDF mit eingebettetem XML, Profil EN 16931)
- **PDF-Rechnung → XRechnung 3.0** (CII-XML)
- **SAP ECC (INVOIC02-IDoc) → ZUGFeRD / XRechnung**: Hier gibt es keine Texterkennung, weil die Daten direkt aus SAP kommen.

Ablauf: Datei hochladen → Felder werden regelbasiert erkannt → im Formular prüfen
und ergänzen → Pflichtfelder und Summen werden laufend geprüft → herunterladen.

**Automatisierung**

- **Zwei Eingangsordner:** SAP-IDocs (`data/idoc/inbox`) und PDFs (`data/pdf/inbox`, z. B.
  von einem Scanner oder Netzlaufwerk). Der Worker liest jede Datei – **Scans per
  Texterkennung (OCR)** –, ergänzt Käuferdaten aus dem Adressbuch, prüft sie und erzeugt
  fehlerfreie E-Rechnungen **ohne Klick** (Archiv + `outbox/`).
- **„Zu prüfen“:** Rechnungen, bei denen etwas fehlt (z. B. Käuferanschrift), landen in einer
  Prüfliste in der App statt im Fehlerordner. Öffnen → ergänzen → erzeugen, danach öffnet sich
  automatisch die nächste. Im Fehlerordner landen nur noch unlesbare Dateien.
- Optional ohne Vollautomatik: Dann geht **jede** PDF-Rechnung zur Freigabe über „Zu prüfen“
  (Vier-Augen-Prinzip, Verwaltung → Einstellungen).

**Erkennung**

- **OCR-Dienst** (eigener Container, OCRmyPDF/Tesseract, Deutsch + Englisch): gescannte Seiten
  werden automatisch erkannt – Seite für Seite, auch mit Scanner-Stempel oder schlechter
  Scanner-Textebene; „Texterkennung erzwingen“ im Editor. Die Daten verlassen den Server nicht.
- **Vorlagen für die drei eigenen Firmen** (Rheinfelden Carbon, Alloys, Semis – auch englisch),
  **Preis je 100/1000 kg** (Spalte „je“, BT-149, auch aus SAP `PEINH`), **steuerfreie Ausfuhr /
  innergemeinschaftliche Lieferung**, per OCR verdrehte Dezimalzeichen („2.640.00“).
- **Auslandsanschriften** ohne deutsche PLZ-Zeile (z. B. Costa Rica) über die Länderzeile;
  **Länderauswahl** mit Kürzel und Namen.
- Kombinierte Felder („Rechnungs-Nr. / Datum“, „Lieferschein-Nr. / Datum“), Summen wie
  „Nettosumme / MwSt. Total / Zu zahlender Gesamtbetrag“, Positionen über mehrere Zeilen,
  Artikel-/Materialnummer (BT-155), Einheit Tonne, **Fälligkeit aus „30 Tage netto“**.
- Mehrdeutige Mengen („23.820“ – Tausenderpunkt oder per OCR gelesenes Komma?) werden über die
  aufgedruckten Beträge aufgelöst.
- Warnung, wenn der Absender im PDF nicht zu den eigenen Firmendaten passt.

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
- **Mehrere eigene Firmen:** bis zu 10 Rechnungssteller (Verwaltung → Firmen, Karten mit
  Bearbeiten im Fenster und Hinweisen auf fehlende/vertauschte Angaben); jede Rechnung
  wird automatisch der richtigen Firma zugeordnet (USt-IdNr., IBAN, Name; IDocs über die eigene
  USt-IdNr.), im Editor umschaltbar.
- **Felder im PDF zuordnen:** Wert in der PDF-Vorschau anklicken, dann das Formularfeld – die
  Stelle wird je Firma gemerkt und bei den nächsten Rechnungen automatisch gelesen (auch wenn
  der Block verrutscht oder der Wert länger ist).
- **Startseite mit Kennzahlen:** E-Rechnungen pro Tag (automatisch/von Hand), Volumen pro
  Monat, Automatisierungsquote, Formate/Quellen, Top-Käufer, Ø Zeit bis zur Freigabe.
- **Hilfe in der App:** Anwenderhandbuch mit Screenshots für alle, Systemdokumentation
  (Architektur, Schnittstellen, Konfiguration, Betrieb, automatisch erzeugte REST-Endpunktliste)
  nur für Administratoren. Texte in `backend/app/handbuch/`; Screenshots mit erfundenen
  Demo-Daten neu erzeugen: `backend/scripts/demo_data.py` + `frontend/scripts/screenshots.mjs`.
- **Kundenstamm aus SAP:** Kundendaten (Anschrift, USt-IdNr., E-Rechnungs-Adresse,
  Leitweg-ID) kommen täglich als CSV aus SAP und müssen nicht von Hand gepflegt werden –
  siehe „SAP ECC → Kundenstamm“.
- **Archiv-Suche:** nach Rechnungsnummer, Käufer oder eigener Firma; Filter nach Zeitraum,
  Firma, Format und Quelle; Blättern; mehrere Einträge als ZIP herunterladen.
- **Positionen mit Summen:** „Gesamt netto“ je Position und Summenzeile (netto, Steuer,
  brutto) mit Abgleich gegen das PDF; neben der PDF-Vorschau als kompakte Karten.
- **SAP-Eingang (Admin):** Status des Workers, Dateien je Ordner, fehlgeschlagene IDocs
  mit Fehlergrund und „Erneut verarbeiten“.
- **Automatische Datenbank-Backups** (siehe „Backup & Wiederherstellung“).

> Rechtlicher Hinweis: Eine E-Rechnung muss vom **Rechnungssteller** ausgestellt
> werden. Die App ist dafür gedacht, die *eigenen* Rechnungen umzuwandeln.
> Fragen zur Pflicht und zur Archivierung (GoBD) bitte mit der Steuerberatung klären.

## Installation auf einem Server (`deploy.sh`)

Auf einem frischen Linux-Server (getestet mit Debian/Ubuntu) genügt:

```bash
git clone https://github.com/Creepers1312/e-rechnung-konverter.git /opt/erechnung
cd /opt/erechnung
./deploy.sh
```

Das Skript

1. prüft Docker / Docker Compose und **installiert Docker bei Bedarf** (nach Rückfrage, über get.docker.com),
2. fragt die **Betriebsart** ab (siehe unten), den Projektnamen und den ersten Admin,
3. legt `.env` mit **zufälligen Passwörtern** an (Datei nur für den Besitzer lesbar),
4. legt die Ordner für SAP-IDocs und Sicherungen mit den richtigen Rechten an,
5. baut und startet alles, wartet bis alle Dienste gesund sind und prüft, ob die App antwortet,
6. zeigt Adresse und **Startpasswort des Admins** (einmalig, wird danach aus `.env` entfernt).

**Update:** einfach erneut `./deploy.sh` im selben Ordner – sichert zuerst die Datenbank,
holt den neuen Code (`git pull`), baut und startet neu. Einstellungen bleiben erhalten.
Zum Schluss räumt es auf: alte, nicht mehr benutzte Docker-Images dieses Updates und Build-Cache
älter als 7 Tage (nie Container, Volumes, Netzwerke oder benannte Images anderer Anwendungen);
abschaltbar mit `./deploy.sh --no-prune`.

**Ohne Rückfragen** (z. B. für Automatisierung):

```bash
ERECHNUNG_MODE=https DOMAIN=rechnung.firma.de ACME_EMAIL=it@firma.de ./deploy.sh --yes
```

### Betriebsarten – auch neben anderen Docker-Containern

| Betriebsart | Wann | Was passiert |
|---|---|---|
| **1 – Eigener Port** | Test, internes Netz, oder es läuft schon ein Proxy *direkt auf dem Host* | Port frei wählbar (belegte Ports werden erkannt); optional nur `127.0.0.1` |
| **2 – HTTPS mit Caddy** | eigener Server, Ports 80/443 frei, Domain zeigt auf den Server | Caddy-Container holt und erneuert das Let's-Encrypt-Zertifikat |
| **3 – Hinter vorhandenem Docker-Proxy** | auf dem Server läuft schon Traefik, Nginx Proxy Manager, nginx-proxy … | App tritt dem Proxy-Netzwerk bei, veröffentlicht **keinen** Port; Ziel für den Proxy: `http://<projektname>-web:80` |

Damit nichts mit anderen Containern kollidiert, hat jede Installation einen eigenen
**Projektnamen** (`COMPOSE_PROJECT_NAME`) und damit eigene Container-, Netzwerk-, Volume-
und Image-Namen; das Skript verweigert einen bereits vergebenen Namen. Mehrere
Installationen (z. B. Test und Produktion) können so auf einem Server laufen.

Für Betriebsart 3:
- **Nginx Proxy Manager:** neuen Proxy Host anlegen, Ziel `<projektname>-web`, Port `80`, SSL im NPM aktivieren.
- **Traefik:** im Skript „Traefik“ bejahen – Router-Labels (Domain, Entrypoint, Certresolver) werden gesetzt.
- **nginx-proxy / acme-companion:** `VIRTUAL_HOST`/`LETSENCRYPT_HOST` werden aus der Domain gesetzt.

Die gewählte Betriebsart steht in `.env` (`COMPOSE_FILE`), daher funktionieren danach
alle normalen Befehle wie `docker compose ps` oder `docker compose logs -f` unverändert.

## Betrieb mit Docker Compose (manuell)

Jeder Teil läuft in einem eigenen Container:

| Dienst | Aufgabe | Von außen erreichbar |
|---|---|---|
| `frontend` | nginx: Weboberfläche + Weiterleitung von `/api` | ja, Port 8080 |
| `backend` | FastAPI (Erkennung, Prüfung, Export) | nein |
| `worker` | überwacht SAP-IDoc- und PDF-Eingangsordner, wandelt automatisch um | nein, ohne Internet |
| `db` | PostgreSQL: Archiv aller erzeugten E-Rechnungen | nein |
| `backup` | tägliche Datenbanksicherung (`pg_dump`) nach `./backups` | nein, ohne Internet |
| `ocr` | Texterkennung für gescannte PDFs (OCRmyPDF/Tesseract) | nein, ohne Internet |

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

Die eigenen Firmen (Rechnungssteller: Name, Anschrift, USt-IdNr., Kontakt, Bankverbindung)
pflegt ein Administrator unter **Verwaltung → Firmen (Stammdaten)** – auch mehrere. Bis zum
ersten Speichern kommen die Daten aus `backend/config/company.yaml`. Als Vorlage dient `company.example.yaml`.
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

### Kundenstamm (CSV-Export)

Damit Käuferdaten, die nicht auf der Rechnung stehen (E-Rechnungs-Adresse, Leitweg-ID,
USt-IdNr. …), nicht von Hand gepflegt werden müssen, liest die App den SAP-Kundenstamm ein:

1. In SAP eine **SAP-Query** (SQVI oder SQ01) über `KNA1` (+ `ADRC`/`ADR6` für die E-Mail)
   anlegen, Felder z. B. `KUNNR, NAME1, NAME2, STRAS, PSTLZ, ORT01, LAND1, STCEG, SMTP_ADDR`
   und – falls gepflegt – das Feld mit der Leitweg-ID.
2. Ausgabe als Datei (CSV, `;`-getrennt) und als **täglichen Job** (SM36) einplanen; die Datei
   landet im Ordner `data/stammdaten/inbox` (Netzlaufwerk/Dateiübertragung).
3. Der Worker importiert jede neue Datei automatisch (danach `processed/`, unlesbare in `error/`).
   Kunden werden über die **Kundennummer** zugeordnet (führende Nullen egal).

Die Spaltennamen werden automatisch erkannt (technische Namen wie `KUNNR` oder deutsche
Überschriften wie „Kundennummer“, Kodierung UTF-8 oder Windows-1252, auch SAP-Listenausgabe
mit `|`). Unter **Verwaltung → Kundenstamm (SAP)** sieht man den letzten Import, kann eine
Datei von Hand hochladen, die Spaltenzuordnung anpassen und Kunden **ohne
E-Rechnungs-Adresse** finden. Standardmäßig haben die **SAP-Stammdaten Vorrang** vor dem, was
aus einem PDF/Scan gelesen wurde (abschaltbar); Exporte überschreiben SAP-Kunden nicht.

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
  app/schematron.py         Offizielle Prüfung (EN 16931 + XRechnung, Schematron via Saxon)
  app/data/schematron/      Offizielle Regelwerke als XSLT (Version und Aktualisierung: README dort)
  app/export/pdf_render.py  Einfaches Rechnungs-PDF, wenn kein Quell-PDF existiert
  app/validation.py         Vorprüfung: Pflichtfelder, IBAN, Summen (deutsche Meldungen am Feld)
  app/convert.py            Prüfen (Vorprüfung, XSD, Schematron, PDF/A) + Datei erzeugen (API und Worker)
  app/pdfa.py               ZUGFeRD: PDF/A-3-Umwandlung und veraPDF-Prüfung über den PDF-Dienst
  app/db.py                 Archiv (PostgreSQL / SQLite) mit SQLAlchemy
  app/auth.py               Anmeldung, Sitzungen, Rollen, Schutz gegen Passwort-Raten
  app/admin.py              Endpunkte für Anmeldung und Verwaltung (/api/auth, /api/admin)
  app/settings.py           Einstellungen aus der Datenbank (Firmendaten, SAP-Konnektor)
  app/customers.py          Käufer-Adressbuch (merken, ergänzen, suchen)
  app/customer_import.py    SAP-Kundenstamm (CSV) ins Adressbuch importieren
  app/stats.py              Kennzahlen der Startseite
  app/recognize.py          Firma erkennen + Regeln + Feldverknüpfungen
  app/field_links.py        Feldverknüpfungen (Klick im PDF -> Formularfeld), je Firma
  app/docs.py, app/handbuch/  Anwenderhandbuch + Systemdokumentation (Markdown, Screenshots)
  app/ocr.py                Anbindung des OCR-Dienstes (gescannte Seiten erkennen, Texterkennung erzwingen)
  app/countries.py          Länderliste (app/data/countries.json): Auswahl + Erkennung von Ländernamen
  app/companies_api.py      Eigene Firmen, Wörter mit Position (PDF-Vorschau), Feldverknüpfungen
  app/review.py             Prüfliste „Zu prüfen“
  app/worker.py             SAP-IDoc- und PDF-Eingang -> E-Rechnungen oder Prüfliste
  app/manage.py             Kommandozeile: Admin anlegen, Passwort zurücksetzen
  app/main.py               FastAPI-Endpunkte (Umwandeln, Archiv)
frontend/                   React + Vite + TypeScript (Start, Zu prüfen, Umwandeln, Archiv, Verwaltung)
ocr/                        PDF-Dienst (FastAPI): OCR (OCRmyPDF/Tesseract), PDF/A-3 (Ghostscript), veraPDF
compose.yaml                frontend, backend, worker, db, backup
deploy.sh                   Installation & Update (Docker-Check, .env, Ordner, Start, Prüfung)
deploy/                     Overlays: compose.https.yaml (Caddy), compose.proxy.yaml (vorhandener Proxy)
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
- [x] Offizielle Prüfung mit den **Schematron-Regeln** von CEN (EN 16931) und KoSIT (XRechnung) vor jeder Freigabe
- [x] Echte **PDF/A-3**-Umwandlung (Ghostscript) + Prüfung der fertigen ZUGFeRD-Datei mit veraPDF
- [ ] Versand der E-Rechnungen (E-Mail / PEPPOL) direkt aus dem Worker
- [ ] Datenbank-Migrationen (Alembic) vor der ersten Schemaänderung
- [ ] Rabatte und Zuschläge (Allowances/Charges), Teilzahlungen, Skonto
- [x] Anmeldung mit Rollen (Admin / Benutzer), Protokoll
- [x] Stapelverarbeitung, PDF-Vorschau, Käufer-Adressbuch, Archiv-Suche, SAP-Eingang-Status, Backups
- [x] OCR für Scans, PDF-Eingangsordner, Prüfliste „Zu prüfen“, neue Oberfläche
- [x] Kundenstamm aus SAP (CSV), Kennzahlen-Startseite, Anwender- und Systemdoku in der App
- [x] Mehrere eigene Firmen, Felder im PDF zuordnen, Vorlagen für alle drei Firmen, Preis je 100/1000 kg
- [x] Texterkennung pro Seite, Länderauswahl, Auslandsanschriften, Firma im Archiv
- [ ] E-Mail-Eingang (Rechnungen per Mail an ein Postfach)
- [ ] Firmen-Login (Microsoft Entra ID / SSO), Mandanten
- [ ] GoBD-konforme Archivierung (das Archiv ist bisher eine einfache Ablage)
