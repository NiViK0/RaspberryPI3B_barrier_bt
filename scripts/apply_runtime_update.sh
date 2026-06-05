#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${1:-$(pwd)}"
APP_DIR="${APP_DIR:-/opt/barrier}"
SRC_DIR="${SRC_DIR:-${APP_DIR}/src}"
DB_PATH="${DB_PATH:-${APP_DIR}/barrier.db}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-ltpibarrier}}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn "$SERVICE_USER" 2>/dev/null || echo "$SERVICE_USER")}"
PANEL_SERVICE="${PANEL_SERVICE:-barrier-panel.service}"
BARRIER_SERVICE="${BARRIER_SERVICE:-barrier.service}"
CREDENTIALS_FILE="${APP_DIR}/panel_credentials.txt"
PANEL_OVERRIDE_DIR="/etc/systemd/system/${PANEL_SERVICE}.d"
PANEL_OVERRIDE_FILE="${PANEL_OVERRIDE_DIR}/security.conf"

log() {
  echo "[INFO] $*"
}

warn() {
  echo "[WARN] $*" >&2
}

err() {
  echo "[ERROR] $*" >&2
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    err "Run as root: sudo bash scripts/apply_runtime_update.sh"
    exit 1
  fi
}

random_token() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
}

load_existing_credentials() {
  [[ -f "$CREDENTIALS_FILE" ]] || return 0

  while IFS='=' read -r key value; do
    case "$key" in
      BARRIER_PANEL_PASSWORD)
        BARRIER_PANEL_PASSWORD="${BARRIER_PANEL_PASSWORD:-$value}"
        ;;
      BARRIER_FLASK_SECRET_KEY)
        BARRIER_FLASK_SECRET_KEY="${BARRIER_FLASK_SECRET_KEY:-$value}"
        ;;
    esac
  done < "$CREDENTIALS_FILE"
}

write_panel_security_override() {
  BARRIER_PANEL_PASSWORD="${BARRIER_PANEL_PASSWORD:-}"
  BARRIER_FLASK_SECRET_KEY="${BARRIER_FLASK_SECRET_KEY:-}"

  if [[ -z "$BARRIER_PANEL_PASSWORD" ]]; then
    BARRIER_PANEL_PASSWORD="$(random_token)"
    warn "Generated a new web panel password."
  fi

  if [[ -z "$BARRIER_FLASK_SECRET_KEY" || "$BARRIER_FLASK_SECRET_KEY" == "change-me" || "$BARRIER_FLASK_SECRET_KEY" == "barrier-panel-local-secret" ]]; then
    BARRIER_FLASK_SECRET_KEY="$(random_token)"
    warn "Generated a new Flask session secret."
  fi

  mkdir -p "$APP_DIR" "$PANEL_OVERRIDE_DIR"
  cat > "$CREDENTIALS_FILE" <<EOF
BARRIER_PANEL_PASSWORD=${BARRIER_PANEL_PASSWORD}
BARRIER_FLASK_SECRET_KEY=${BARRIER_FLASK_SECRET_KEY}
EOF
  chmod 0600 "$CREDENTIALS_FILE"
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "$CREDENTIALS_FILE" 2>/dev/null || true

  cat > "$PANEL_OVERRIDE_FILE" <<EOF
[Service]
Environment=BARRIER_PANEL_PASSWORD=${BARRIER_PANEL_PASSWORD}
Environment=BARRIER_FLASK_SECRET_KEY=${BARRIER_FLASK_SECRET_KEY}
EOF
}

backup_current_runtime() {
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"

  if [[ -d "$SRC_DIR" ]]; then
    cp -a "$SRC_DIR" "${SRC_DIR}.backup.${stamp}"
    log "Source backup: ${SRC_DIR}.backup.${stamp}"
  fi

  if [[ -f "$DB_PATH" ]]; then
    cp -a "$DB_PATH" "${DB_PATH}.backup.${stamp}"
    log "Database backup: ${DB_PATH}.backup.${stamp}"
  fi
}

copy_runtime() {
  mkdir -p "$SRC_DIR"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '.git' --exclude 'deploy' --exclude 'archive' "$SOURCE_DIR/" "$SRC_DIR/"
  else
    rm -rf "$SRC_DIR"
    mkdir -p "$SRC_DIR"
    tar -C "$SOURCE_DIR" \
      --exclude './.git' \
      --exclude './deploy' \
      --exclude './archive' \
      -cf - . | tar -C "$SRC_DIR" -xf -
  fi
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$SRC_DIR"
  find "$SRC_DIR/scripts" -name '*.sh' -exec chmod +x {} + 2>/dev/null || true
}

restart_services() {
  systemctl daemon-reload
  /opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py init-db
  systemctl restart "$BARRIER_SERVICE" "$PANEL_SERVICE"
}

main() {
  require_root
  SOURCE_DIR="$(readlink -f "$SOURCE_DIR")"
  if [[ ! -f "${SOURCE_DIR}/barrier_service.py" || ! -f "${SOURCE_DIR}/panel.py" ]]; then
    err "Source directory does not look like a Barrier runtime package: ${SOURCE_DIR}"
    exit 2
  fi

  load_existing_credentials
  write_panel_security_override
  backup_current_runtime
  copy_runtime
  restart_services

  log "Runtime update applied."
  log "Web panel password: ${BARRIER_PANEL_PASSWORD}"
  log "Credentials file: ${CREDENTIALS_FILE}"
}

main "$@"
