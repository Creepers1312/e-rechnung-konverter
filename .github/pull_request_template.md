## Was ändert sich?

<!-- Kurz: was und warum. Bei Bedarf Issue verlinken (z. B. "Schließt #12"). -->

## Art der Änderung

- [ ] Fehlerbehebung
- [ ] Neue Funktion
- [ ] Neue Rechnungs-Vorlage (Layout) / SAP-Zuordnung
- [ ] Sicherheit / Anmeldung / Rechte
- [ ] Betrieb (Docker, CI, Abhängigkeiten)
- [ ] Doku / Aufräumen

## Prüfliste

- [ ] Tests ergänzt oder angepasst; `cd backend && pytest` ist grün
- [ ] `ruff check .` und `ruff format --check .` (Backend) sind sauber
- [ ] `pnpm typecheck && pnpm build` (Frontend) sind sauber
- [ ] Neue API-Endpunkte verlangen `current_user` bzw. `require_admin`
- [ ] Änderungen an `export/cii.py` gegen das XSD geprüft (`check_xsd`)
- [ ] Datenbankänderung? Dann Upgrade für bestehende Datenbanken vorgesehen
- [ ] README / CLAUDE.md aktualisiert, falls sich Bedienung oder Architektur ändert
- [ ] **Keine echten Rechnungen, Kundendaten, Passwörter oder `.env`-Inhalte** im Commit

## Wie getestet?

<!-- z. B. "Beispiel-PDF hochgeladen, als XRechnung exportiert", "docker compose up, als User angemeldet" -->
