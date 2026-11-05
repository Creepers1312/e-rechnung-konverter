# Mitarbeit

## Ablauf

1. Für jede Änderung einen Branch anlegen und einen Pull Request gegen `main` öffnen.
   Die PR-Vorlage enthält die Prüfliste.
2. Die CI (GitHub Actions) muss grün sein: Backend-Lint/Format/Tests, Frontend-Typecheck/Build,
   Docker-Build. Erst dann wird gemergt.
3. Fehler, Wünsche und schlecht erkannte Rechnungslayouts über die **Issue-Vorlagen** melden.

## Lokal prüfen (wie die CI)

```bash
cd backend
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest
cd ../frontend
pnpm typecheck && pnpm build
```

Optional automatisch vor jedem Commit: `pip install pre-commit && pre-commit install`.

## Regeln

- **Keine echten Rechnungen, Kundendaten, Passwörter oder `.env`-Dateien committen.**
  Für Tests nachgebaute Beispielrechnungen verwenden (`scripts/make_sample_pdf.py`).
- Beträge immer als `Decimal`, nie als `float`.
- Neue Rechnungslayouts sind YAML-Vorlagen, kein Code (`backend/app/extraction/templates/`).
- Neue API-Endpunkte verlangen `current_user` oder `require_admin`.
- Architekturregeln stehen in `CLAUDE.md`.
