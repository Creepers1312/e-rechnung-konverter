# CLAUDE.md — Project guide

Web app that converts invoices (PDF, SAP ECC IDocs) into EN 16931 e-invoices
(ZUGFeRD / Factur-X and XRechnung). User-facing text is **German**; code,
comments and identifiers are English.

## Stack (do not deviate without asking)

- Backend: Python ≥ 3.11, FastAPI, pydantic v2, SQLAlchemy 2 (PostgreSQL via psycopg 3), pdfplumber, lxml, factur-x, reportlab. Tests: pytest.
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
5. **Own company = seller**, from `backend/config/company.yaml` (git-ignored;
   `company.example.yaml` is the template). Master data wins over document data.
6. **The API is stateless while editing.** The client holds invoice + source PDF
   between calls; only finished exports are persisted (`app/db.py`, table
   `documents`). `app/convert.py` is the single validate-then-build path used by
   both the API and the IDoc worker.
7. **Deployment is Docker Compose** (`compose.yaml`): frontend (nginx, only
   published port), backend, worker (same image, `python -m app.worker`), db
   (PostgreSQL). Network `data` is internal: worker and db have no internet.
   Locally without Docker the backend falls back to SQLite.
8. **Export only when valid**: export endpoints re-run validation and return 422
   with the issues. CII element order follows the XSD — check `check_xsd` after
   any change in `export/cii.py`.

## Commands

```bash
cd backend && .venv/bin/pytest          # must stay green
cd frontend && pnpm typecheck && pnpm build
```

## Not yet (see README roadmap)

KoSIT Schematron validation, strict PDF/A-3 conversion, sending (e-mail/PEPPOL),
Alembic migrations, OCR, allowances/charges, auth/multi-tenancy.
