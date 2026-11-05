#!/usr/bin/env bash
# =============================================================================
#  E-Rechnung Konverter – Installation und Update mit Docker Compose
# =============================================================================
#
#  Erstinstallation (frisches System):
#     git clone <repo-url> /opt/erechnung && cd /opt/erechnung && ./deploy.sh
#
#  Update (später, im selben Ordner):
#     ./deploy.sh            -> sichert die Datenbank, holt den neuen Stand, startet neu
#
#  Optionen:
#     --mode port|https|proxy   Betriebsart vorgeben (sonst wird gefragt)
#     --yes                     keine Rückfragen (Standardwerte / Umgebungsvariablen)
#     --no-build                Images nicht bauen (müssen bereits vorhanden sein)
#     --no-pull                 Git-Update überspringen
#     -h, --help                Hilfe
#
#  Ohne Rückfragen, z. B.:
#     ERECHNUNG_MODE=https DOMAIN=rechnung.firma.de ACME_EMAIL=it@firma.de ./deploy.sh --yes
#     ERECHNUNG_MODE=proxy PROXY_NETWORK=proxy DOMAIN=rechnung.firma.de ./deploy.sh --yes
#     ERECHNUNG_MODE=port HTTP_PORT=8090 HTTP_BIND=127.0.0.1 ./deploy.sh --yes
#
#  Mehrere Installationen / andere Container auf demselben Server:
#     Jede Installation hat einen eigenen Projektnamen (COMPOSE_PROJECT_NAME),
#     eigene Netzwerke, Volumes und Images. Ports werden vor dem Start geprüft.
# =============================================================================
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

# --- Ausgabe ------------------------------------------------------------------
if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'; else B='' G='' Y='' R='' N=''; fi
step() { echo; echo "${B}==> $*${N}"; }
ok()   { echo "  ${G}✓${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
die()  { echo; echo "${R}FEHLER:${N} $*" >&2; exit 1; }

# --- Optionen -----------------------------------------------------------------
ASSUME_YES=false; BUILD=true; GIT_PULL=true; MODE="${ERECHNUNG_MODE:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) ASSUME_YES=true ;;
    --no-build) BUILD=false ;;
    --no-pull) GIT_PULL=false ;;
    --mode) MODE="${2:-}"; shift ;;
    --mode=*) MODE="${1#*=}" ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unbekannte Option: $1 (siehe --help)" ;;
  esac
  shift
done
[ -z "$MODE" ] || [[ "$MODE" =~ ^(port|https|proxy)$ ]] || die "--mode muss port, https oder proxy sein."

# ask "Frage" "Standard" -> Antwort (bei --yes oder ohne Terminal: Standard)
ask() {
  local answer
  if $ASSUME_YES || [ ! -t 0 ]; then echo "$2"; return; fi
  read -r -p "  $1 [$2]: " answer </dev/tty
  echo "${answer:-$2}"
}
confirm() {  # confirm "Frage" (Standard: ja); ohne Terminal und ohne --yes: nein
  $ASSUME_YES && return 0
  [ -t 0 ] || return 1
  local a; read -r -p "  $1 [J/n]: " a </dev/tty
  [[ ! "$a" =~ ^[nN] ]]
}
random() {  # random A-Za-z0-9 string; reads a fixed block first (no SIGPIPE under pipefail)
  local s=""
  while [ "${#s}" -lt "$1" ]; do s+=$(head -c 256 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9'); done
  printf '%s' "${s:0:$1}"
}

# .env lesen/schreiben (ohne die Datei als Shell-Skript auszuführen)
env_get() { [ -f .env ] && grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
env_set() {
  if grep -qE "^$1=" .env; then
    local esc; esc=$(printf '%s' "$2" | sed 's/[&|\\]/\\&/g')
    sed -i "s|^$1=.*|$1=$esc|" .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

port_in_use() {
  if command -v ss >/dev/null; then ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
  else (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; fi
}

# =============================================================================
step "Voraussetzungen prüfen"
# =============================================================================
[ -f compose.yaml ] || die "deploy.sh muss im Projektordner liegen (compose.yaml fehlt)."
[ "$(uname -s)" = Linux ] || warn "Getestet für Linux-Server; auf $(uname -s) evtl. Anpassungen nötig."

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null || die "Bitte als root ausführen oder sudo installieren."
  SUDO="sudo"
fi

if ! command -v docker >/dev/null; then
  warn "Docker ist nicht installiert."
  confirm "Docker jetzt mit dem offiziellen Installationsskript (get.docker.com) installieren?" \
    || die "Ohne Docker geht es nicht. Anleitung: https://docs.docker.com/engine/install/"
  command -v curl >/dev/null || { $SUDO apt-get update -qq && $SUDO apt-get install -y -qq curl; }
  curl -fsSL https://get.docker.com | $SUDO sh
  $SUDO systemctl enable --now docker 2>/dev/null || true
fi

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if [ -n "$SUDO" ] && $SUDO docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
    warn "Docker läuft nur mit sudo (Benutzer ist nicht in der Gruppe 'docker')."
  else
    $SUDO systemctl start docker 2>/dev/null || true
    docker info >/dev/null 2>&1 || $SUDO docker info >/dev/null 2>&1 || die "Docker-Dienst läuft nicht."
    docker info >/dev/null 2>&1 || DOCKER=(sudo docker)
  fi
fi
ok "Docker $("${DOCKER[@]}" version --format '{{.Server.Version}}')"

COMPOSE_VERSION=$("${DOCKER[@]}" compose version --short 2>/dev/null || true)
[ -n "$COMPOSE_VERSION" ] || die "Docker Compose v2 fehlt (Paket docker-compose-plugin)."
if ! printf '%s\n%s\n' "2.24.0" "${COMPOSE_VERSION#v}" | sort -V -C; then
  die "Docker Compose $COMPOSE_VERSION ist zu alt (mind. 2.24 nötig). Bitte Docker aktualisieren."
fi
ok "Docker Compose $COMPOSE_VERSION"
compose() { "${DOCKER[@]}" compose "$@"; }

# =============================================================================
if [ -f .env ]; then
  step "Bestehende Installation gefunden – Update"
  PROJECT=$(env_get COMPOSE_PROJECT_NAME); PROJECT=${PROJECT:-erechnung}
  ok "Projekt: $PROJECT (Einstellungen aus .env bleiben erhalten)"

  if [ -n "$(compose ps -q backup 2>/dev/null)" ]; then
    if compose exec -T backup /bin/sh /backup.sh now; then ok "Datenbank vor dem Update gesichert"
    else warn "Sicherung vor dem Update fehlgeschlagen – bitte prüfen (docker compose logs backup)"; fi
  fi

  if $GIT_PULL && [ -d .git ] && command -v git >/dev/null; then
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
      warn "Lokale Änderungen im Projektordner – Git-Update übersprungen."
    elif git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
      before=$(git rev-parse --short HEAD)
      git pull --ff-only --quiet || die "git pull fehlgeschlagen (lokaler Stand weicht ab?)."
      after=$(git rev-parse --short HEAD)
      [ "$before" = "$after" ] && ok "Code ist aktuell ($after)" || ok "Code aktualisiert: $before -> $after"
    fi
  fi
else
  step "Erstinstallation – Einstellungen"

  PROJECT=$(ask "Projektname (eindeutig auf diesem Server)" "${COMPOSE_PROJECT_NAME:-erechnung}")
  [[ "$PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "Projektname: nur Kleinbuchstaben, Ziffern, - und _."
  if compose ls -a --format json 2>/dev/null | grep -q "\"Name\":\"$PROJECT\""; then
    die "Auf diesem Server gibt es bereits ein Compose-Projekt '$PROJECT'. Anderen Namen wählen."
  fi

  if [ -z "$MODE" ]; then
    if $ASSUME_YES || [ ! -t 0 ]; then MODE=port; else
      echo
      echo "  Wie soll die App erreichbar sein?"
      echo "    1) Eigener Port (HTTP), z. B. http://server:8080 – oder nur lokal für einen Proxy auf dem Host"
      echo "    2) HTTPS mit eingebautem Caddy (Domain nötig, Ports 80/443 müssen frei sein)"
      echo "    3) Hinter vorhandenem Docker-Reverse-Proxy (Traefik, Nginx Proxy Manager, nginx-proxy …)"
      case "$(ask "Auswahl" "1")" in 1) MODE=port ;; 2) MODE=https ;; 3) MODE=proxy ;; *) die "Ungültige Auswahl." ;; esac
    fi
  fi
  ok "Betriebsart: $MODE"

  cp .env.example .env
  chmod 600 .env
  trap 'rm -f .env; echo "  (unvollständige .env entfernt – einfach erneut starten)" >&2' EXIT
  env_set COMPOSE_PROJECT_NAME "$PROJECT"
  env_set POSTGRES_PASSWORD "$(random 32)"
  ADMIN_USER_VALUE=$(ask "Benutzername des ersten Administrators" "${ADMIN_USER:-admin}")
  env_set ADMIN_USER "$ADMIN_USER_VALUE"
  env_set ADMIN_PASSWORD "$(random 16)"

  case "$MODE" in
    port)
      port=$(ask "Port für die Weboberfläche" "${HTTP_PORT:-8080}")
      while port_in_use "$port"; do
        warn "Port $port ist schon belegt."
        $ASSUME_YES && { port=$((port + 1)); continue; }
        port=$(ask "Anderen Port wählen" "$((port + 1))")
      done
      bind=$(ask "Nur lokal erreichbar (127.0.0.1, z. B. hinter Proxy auf dem Host)? j/n" \
        "$([ "${HTTP_BIND:-0.0.0.0}" = 127.0.0.1 ] && echo j || echo n)")
      [[ "$bind" =~ ^[jJyY] ]] && bind=127.0.0.1 || bind=0.0.0.0
      env_set HTTP_PORT "$port"; env_set HTTP_BIND "$bind"
      env_set COMPOSE_FILE "compose.yaml"
      [ "$bind" = 127.0.0.1 ] && env_set COOKIE_SECURE "$(ask "Läuft der Host-Proxy mit HTTPS? (true/false)" "true")"
      ;;
    https)
      domain=$(ask "Domain (DNS muss auf diesen Server zeigen)" "${DOMAIN:-}")
      [ -n "$domain" ] || die "Für HTTPS wird eine Domain benötigt."
      for p in 80 443; do port_in_use "$p" && die "Port $p ist belegt (läuft schon ein Webserver/Proxy? Dann Betriebsart 3 wählen)."; done
      getent hosts "$domain" >/dev/null || warn "$domain lässt sich (noch) nicht auflösen – ohne DNS-Eintrag gibt es kein Zertifikat."
      env_set DOMAIN "$domain"
      email=$(ask "E-Mail für Let's Encrypt (Warnungen zu Zertifikaten)" "${ACME_EMAIL:-}")
      [[ "$email" == *@*.* ]] || die "Für HTTPS wird eine gültige E-Mail-Adresse benötigt (ACME_EMAIL)."
      env_set ACME_EMAIL "$email"
      env_set COOKIE_SECURE true
      env_set COMPOSE_FILE "compose.yaml:deploy/compose.https.yaml"
      ;;
    proxy)
      echo "  Vorhandene Docker-Netzwerke:"
      "${DOCKER[@]}" network ls --format '{{.Name}}' | grep -vxE 'bridge|host|none' | sed 's/^/    - /' || true
      net=$(ask "Netzwerk des Reverse-Proxys" "${PROXY_NETWORK:-proxy}")
      "${DOCKER[@]}" network inspect "$net" >/dev/null 2>&1 \
        || die "Docker-Netzwerk '$net' gibt es nicht (docker network ls)."
      env_set PROXY_NETWORK "$net"
      env_set DOMAIN "$(ask "Domain, unter der die App erreichbar sein soll" "${DOMAIN:-}")"
      traefik=$(ask "Ist der Proxy Traefik (Labels setzen)? j/n" "$([ "${TRAEFIK_ENABLE:-false}" = true ] && echo j || echo n)")
      if [[ "$traefik" =~ ^[jJyY] ]]; then
        env_set TRAEFIK_ENABLE true
        env_set TRAEFIK_ENTRYPOINT "$(ask "Traefik-Entrypoint" "${TRAEFIK_ENTRYPOINT:-websecure}")"
        env_set TRAEFIK_CERTRESOLVER "$(ask "Traefik-Certresolver" "${TRAEFIK_CERTRESOLVER:-letsencrypt}")"
      fi
      env_set COOKIE_SECURE true
      env_set COMPOSE_FILE "compose.yaml:deploy/compose.proxy.yaml"
      ;;
  esac

  # Ordner für SAP-IDocs und Backups; die Container laufen als UID 10001 bzw. root
  idoc=$(env_get IDOC_HOST_DIR); idoc=${idoc:-./data/idoc}
  pdfdir=$(env_get PDF_HOST_DIR); pdfdir=${pdfdir:-./data/pdf}
  backups=$(env_get BACKUP_HOST_DIR); backups=${backups:-./backups}
  mkdir -p "$idoc"/{inbox,outbox,processed,error} "$pdfdir"/{inbox,outbox,processed,error} "$backups"
  $SUDO chown -R 10001 "$idoc" "$pdfdir"
  chmod 700 "$backups"
  ok ".env angelegt (Passwörter zufällig erzeugt, Datei nur für den Besitzer lesbar)"
  ok "Ordner: IDocs in $idoc, PDFs in $pdfdir, Sicherungen in $backups"
  trap - EXIT  # .env is complete from here on
fi

# Initial admin password not shown yet (e.g. the first start failed)? Show it after this run.
ADMIN_PW=$(env_get ADMIN_PASSWORD)

# From here on .env is the single source of truth: environment variables were only
# inputs for the questions above, and Compose would otherwise let them override .env.
while IFS='=' read -r key _; do
  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] && unset "$key"
done < .env

# Folders added in later versions (e.g. the PDF inbox) also exist after an update.
pdfdir=$(env_get PDF_HOST_DIR)
if [ -z "$pdfdir" ]; then env_set PDF_HOST_DIR ./data/pdf; pdfdir=./data/pdf; fi
if [ ! -d "$pdfdir/inbox" ]; then
  mkdir -p "$pdfdir"/{inbox,outbox,processed,error} && $SUDO chown -R 10001 "$pdfdir"
  ok "PDF-Eingang angelegt: $pdfdir/inbox"
fi

MODE_NOW=$(env_get COMPOSE_FILE)
case "$MODE_NOW" in *https*) MODE=https ;; *proxy*) MODE=proxy ;; *) MODE=port ;; esac

# =============================================================================
step "Starten"
# =============================================================================
compose config -q || die "Konfiguration ungültig (siehe Meldung oben)."
compose pull --quiet --ignore-buildable 2>/dev/null || compose pull --quiet --ignore-pull-failures 2>/dev/null || true
if $BUILD; then
  compose build --pull >/dev/null || compose build || die "Bauen der Images fehlgeschlagen."
  ok "Images gebaut"
fi
compose up -d --remove-orphans --wait --wait-timeout 180 \
  || { compose ps; die "Nicht alle Dienste sind gesund gestartet (docker compose logs)."; }
ok "Alle Dienste laufen"

# =============================================================================
step "Prüfen"
# =============================================================================
compose exec -T frontend wget -qO- http://127.0.0.1/api/health | grep -q '"ok"' \
  || die "Die App antwortet nicht (docker compose logs frontend backend)."
ok "Weboberfläche und API antworten"

case "$MODE" in
  port)
    port=$(env_get HTTP_PORT); bind=$(env_get HTTP_BIND)
    if command -v curl >/dev/null && curl -fsS "http://127.0.0.1:${port}/api/health" >/dev/null; then
      ok "Erreichbar auf Port $port"
    fi
    host=$([ "$bind" = 127.0.0.1 ] && echo 127.0.0.1 || hostname -I 2>/dev/null | awk '{print $1}')
    URL="http://${host:-localhost}:${port}"
    ;;
  https)
    domain=$(env_get DOMAIN); URL="https://$domain"
    # ask Caddy directly (no DNS needed); a fresh certificate can take a few seconds
    https_ok=false
    for _ in 1 2 3 4 5 6; do
      if curl --noproxy '*' -fsSk --resolve "$domain:443:127.0.0.1" "https://$domain/api/health" >/dev/null 2>&1; then
        https_ok=true; break
      fi
      sleep 5
    done
    if $https_ok; then ok "HTTPS über Caddy antwortet"
    else warn "HTTPS antwortet noch nicht – DNS-Eintrag und 'docker compose logs caddy' prüfen."; fi
    ;;
  proxy) d=$(env_get DOMAIN); URL=${d:+https://$d}; URL=${URL:-"(im Reverse-Proxy einrichten)"} ;;
esac

if [ -n "$ADMIN_PW" ]; then
  # The admin exists now; do not keep the initial password lying around in .env.
  env_set ADMIN_PASSWORD ""
fi

# =============================================================================
step "Fertig"
# =============================================================================
echo "  Adresse:      ${B}$URL${N}"
if [ -n "$ADMIN_PW" ]; then
  echo "  Anmeldung:    ${B}$(env_get ADMIN_USER)${N} / ${B}$ADMIN_PW${N}"
  echo "                ${Y}Jetzt notieren – das Passwort wird nicht gespeichert und muss bei der ersten"
  echo "                Anmeldung geändert werden.${N}"
fi
case "$MODE" in
  https) echo "  HTTPS:        Caddy holt das Zertifikat beim ersten Aufruf (DNS muss auf diesen Server zeigen)." ;;
  proxy)
    echo "  Proxy-Ziel:   ${B}http://${PROJECT}-web:80${N} im Netzwerk $(env_get PROXY_NETWORK)"
    [ "$(env_get TRAEFIK_ENABLE)" = true ] \
      && echo "                Traefik übernimmt die Route automatisch (Labels gesetzt)." \
      || echo "                Im Proxy (z. B. Nginx Proxy Manager) einen Host auf dieses Ziel anlegen." ;;
  port)
    [ "$(env_get HTTP_BIND)" = 127.0.0.1 ] \
      && echo "  Hinweis:      Nur lokal erreichbar – im Host-Proxy auf http://127.0.0.1:$(env_get HTTP_PORT) weiterleiten." \
      || echo "  Hinweis:      Für den Betrieb im Netz/Internet HTTPS verwenden (Betriebsart 2 oder 3)." ;;
esac
echo
echo "  SAP-IDocs:    $(env_get IDOC_HOST_DIR)/inbox"
echo "  PDF-Eingang:  $(env_get PDF_HOST_DIR)/inbox   (Scanner/Netzlaufwerk hier ablegen lassen)"
echo "  Sicherungen:  $(env_get BACKUP_HOST_DIR) (täglich $(env_get BACKUP_TIME) UTC, $(env_get BACKUP_KEEP_DAYS) Tage)"
echo "  Update:       ./deploy.sh"
echo "  Status/Logs:  docker compose ps   ·   docker compose logs -f backend worker"
