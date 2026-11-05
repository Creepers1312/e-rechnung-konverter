#!/bin/sh
# Daily PostgreSQL backup for the "backup" service (compose.yaml).
#
# - On start: one backup if none exists for today yet.
# - Then every day once the clock passes BACKUP_TIME (HH:MM, container time = UTC).
# - Keeps BACKUP_KEEP_DAYS days of backups in /backups.
# - Manual backup any time:  docker compose exec backup /bin/sh /backup.sh now
#
# Restore (see README):  pg_restore --clean --if-exists -d <db> <file>.dump
set -eu

BACKUP_DIR=/backups
BACKUP_TIME="${BACKUP_TIME:-02:30}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') backup: $*"; }

run_backup() {
  stamp=$(date -u '+%Y-%m-%d_%H%M%S')
  target="$BACKUP_DIR/erechnung-$stamp.dump"
  # write to a temp file first, so a half-written dump never looks like a backup
  if pg_dump --format=custom --file="$target.part"; then
    mv "$target.part" "$target"
    log "OK $(basename "$target") ($(du -h "$target" | cut -f1))"
  else
    rm -f "$target.part"
    log "FEHLER: pg_dump fehlgeschlagen"
    return 1
  fi
  find "$BACKUP_DIR" -name 'erechnung-*.dump' -mtime +"$KEEP_DAYS" -print -delete | sed 's/^/  gelöscht (älter als '"$KEEP_DAYS"' Tage): /'
}

done_today() {
  ls "$BACKUP_DIR"/erechnung-"$(date -u '+%Y-%m-%d')"_*.dump >/dev/null 2>&1
}

mkdir -p "$BACKUP_DIR"

if [ "${1:-}" = "now" ]; then
  run_backup
  exit $?
fi

log "gestartet: täglich ab $BACKUP_TIME UTC, Aufbewahrung $KEEP_DAYS Tage, Ziel $BACKUP_DIR"
done_today || run_backup || true

while true; do
  sleep 300
  now=$(date -u '+%H:%M')
  if ! done_today && [ "$(printf '%s\n%s\n' "$BACKUP_TIME" "$now" | sort | head -n1)" = "$BACKUP_TIME" ]; then
    run_backup || true
  fi
done
