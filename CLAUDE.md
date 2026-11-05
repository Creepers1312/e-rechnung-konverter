# CLAUDE.md — Project guide

Web app that converts invoices (PDF, SAP ECC IDocs) into EN 16931 e-invoices
(ZUGFeRD / Factur-X and XRechnung). User-facing text is **German**; code,
comments and identifiers are English.

## Stack (do not deviate without asking)

- Backend: Python ≥ 3.11, FastAPI, pydantic v2, SQLAlchemy 2 (PostgreSQL via psycopg 3), argon2-cffi, pdfplumber, lxml, factur-x, reportlab. Tests: pytest.
- Frontend: React 18 + Vite + TypeScript (strict), pnpm. Plain CSS, no UI framework.

## Architecture (keep these)

1. **One neutral model** (`backend/app/model.py`). Sources produce an `Invoice`,
   outputs consume one; sources and outputs never import each other.
2. **Money is `Decimal`**, never float. JSON carries decimals as strings.
   Totals are always computed (`compute_totals`), never taken from input.
   Printed totals from a PDF are only used for the cross-check in `validation.py`.
3. **Extraction is rule-based and data-driven.** New invoice layouts = new YAML
   file in `app/extraction/templates/`, no code change. `_generic.yaml` always
   applies; a matching layout template is merged on top.
4. **ERP access goes through adapters** implementing `ErpAdapter`
   (`app/erp/base.py`). SAP ECC is read via INVOIC02 IDoc XML files — no RFC/SDK.
   SAP field mapping lives in one `MAPPING` dict.
5. **Own company = seller**, edited by admins in the UI (`settings` table, read
   via `settings.get_company`); `backend/config/company.yaml` (git-ignored,
   template `company.example.yaml`) is only the fallback until then.
   Master data wins over document data.
6. **The API is stateless while editing.** The client holds invoice + source PDF
   between calls; only finished exports are persisted (`app/db.py`, table
   `documents`). `app/convert.py` is the single validate-then-build path used by
   both the API and the IDoc worker.
7. **Deployment is Docker Compose** (`compose.yaml`): frontend (nginx, only
   published port), backend, worker (same image, `python -m app.worker`), db
   (PostgreSQL), backup (`backup/backup.sh`, pg_dump). Network `data` is
   internal: worker, db and backup have no internet. The IDoc folder is mounted
   into worker (processing) and backend (admin status/retry).
   `deploy.sh` installs/updates; overlays in `deploy/` (Caddy HTTPS, existing
   Docker proxy) are selected via `COMPOSE_FILE` in `.env`. Everything is
   namespaced by `COMPOSE_PROJECT_NAME` – never hard-code container, network,
   volume or image names (other stacks may share the host).
   Locally without Docker the backend falls back to SQLite.
8. **Export only when valid**: export endpoints re-run validation and return 422
   with the issues. CII element order follows the XSD — check `check_xsd` after
   any change in `export/cii.py`.
9. **Every endpoint is behind login** (`app/auth.py`): `current_user` for
   normal work, `require_admin` for users/settings/audit (`app/admin.py`).
   New endpoints must take one of these dependencies; only `/api/health` and
   `/api/auth/login` are public. Writes need the `X-Requested-With: erechnung`
   header. Security-relevant actions go to the audit log (`db.audit`).
   Admin-editable configuration lives in the `settings` table (`app/settings.py`).
10. **Buyer address book** (`app/customers.py`): exports remember the buyer;
    `customers.fill` only fills *empty* fields. Text search must lower-case both
    sides – PostgreSQL's LIKE is case-sensitive, SQLite's (used in tests) is not.

## Commands

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest   # must stay green (CI)
cd frontend && pnpm typecheck && pnpm build
```

## Not yet (see README roadmap)

KoSIT Schematron validation, strict PDF/A-3 conversion, sending (e-mail/PEPPOL),
Alembic migrations, OCR, allowances/charges, SSO (Entra ID), multi-tenancy.
