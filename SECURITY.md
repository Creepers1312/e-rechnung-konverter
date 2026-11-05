# Sicherheit

Die App verarbeitet Rechnungen, also personenbezogene und geschäftliche Daten.

## Sicherheitslücke melden

Bitte **nicht** als öffentliches Issue, sondern vertraulich über
GitHub → *Security* → *Report a vulnerability* (private Meldung) an den Repository-Inhaber.

## Betriebshinweise

- Außerhalb von `localhost` nur hinter HTTPS betreiben und `COOKIE_SECURE=true` setzen.
- Starke Passwörter für `POSTGRES_PASSWORD` und den ersten Admin; `ADMIN_PASSWORD`
  nach der ersten Anmeldung aus `.env` entfernen.
- Regelmäßige Backups des Volumes `pgdata` (Archiv) und des IDoc-Ordners einrichten.
- Das Protokoll (Verwaltung → Protokoll) auf fehlgeschlagene Anmeldungen prüfen.
- Abhängigkeiten aktuell halten (Dependabot-PRs zeitnah mergen).
