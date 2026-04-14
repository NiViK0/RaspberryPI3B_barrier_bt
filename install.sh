#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/NiViK0/RaspberryPI3B_barrier_bt.git"
BRANCH="main"

APP_DIR="/opt/barrier"
SRC_DIR="${APP_DIR}/src"
VENV_DIR="${APP_DIR}/venv"

SERVICE_USER="${SUDO_USER:-$USER}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

PYTHON_BIN="python3"
VENV_PYTHON="${VENV_DIR}/bin/python"

BARRIER_SERVICE_NAME="barrier.service"
PANEL_SERVICE_NAME="barrier-panel.service"

BARRIER_SERVICE_FILE="/etc/systemd/system/${BARRIER_SERVICE_NAME}"
PANEL_SERVICE_FILE="/etc/systemd/system/${PANEL_SERVICE_NAME}"

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
    err "Запусти скрипт через sudo: sudo bash install.sh"
    exit 1
  fi
}

install_packages() {
  log "Устанавливаю системные пакеты"
  apt update
  apt install -y \
    git \
    python3 python3-pip python3-venv \
    bluetooth bluez sqlite3
}

prepare_dirs() {
  log "Создаю директории ${APP_DIR} и ${SRC_DIR}"
  mkdir -p "$APP_DIR"
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$APP_DIR"
}

fetch_repo() {
  if [[ -d "${SRC_DIR}/.git" ]]; then
    log "Репозиторий уже существует, обновляю"
    sudo -u "$SERVICE_USER" git -C "$SRC_DIR" fetch --all --prune
    sudo -u "$SERVICE_USER" git -C "$SRC_DIR" checkout "$BRANCH"
    sudo -u "$SERVICE_USER" git -C "$SRC_DIR" pull --ff-only origin "$BRANCH"
  else
    log "Клонирую репозиторий"
    sudo -u "$SERVICE_USER" git clone --branch "$BRANCH" "$REPO_URL" "$SRC_DIR"
  fi
}

check_repo_files() {
  local missing=0

  for f in barrier_service.py panel.py; do
    if [[ ! -f "${SRC_DIR}/${f}" ]]; then
      err "Не найден файл ${SRC_DIR}/${f}"
      missing=1
    fi
  done

  if [[ "$missing" -ne 0 ]]; then
    err "Структура репозитория не совпала с ожидаемой"
    exit 1
  fi
}

create_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    log "Создаю virtualenv"
    sudo -u "$SERVICE_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  log "Обновляю pip и ставлю зависимости"
  sudo -u "$SERVICE_USER" "$VENV_PYTHON" -m pip install --upgrade pip
  sudo -u "$SERVICE_USER" "$VENV_PYTHON" -m pip install pyserial flask
}

enable_bluetooth() {
  log "Включаю bluetooth.service"
  systemctl enable bluetooth
  systemctl restart bluetooth || true
}

grant_serial_access() {
  log "Добавляю пользователя ${SERVICE_USER} в группу dialout"
  usermod -aG dialout "$SERVICE_USER" || true
}

init_database() {
  log "Инициализирую SQLite"
  sudo -u "$SERVICE_USER" "$VENV_PYTHON" "${SRC_DIR}/barrier_service.py" init-db
}

write_barrier_service() {
  log "Создаю unit ${BARRIER_SERVICE_NAME}"
  cat > "$BARRIER_SERVICE_FILE" <<EOF
[Unit]
Description=Barrier BLE Service
After=network.target bluetooth.service
Wants=bluetooth.service

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${SRC_DIR}
ExecStart=${VENV_PYTHON} ${SRC_DIR}/barrier_service.py run
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
}

write_panel_service() {
  log "Создаю unit ${PANEL_SERVICE_NAME}"
  cat > "$PANEL_SERVICE_FILE" <<EOF
[Unit]
Description=Barrier Web Panel
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${SRC_DIR}
ExecStart=${VENV_PYTHON} ${SRC_DIR}/panel.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
}

enable_and_start_services() {
  log "Перечитываю systemd"
  systemctl daemon-reload

  log "Включаю автозапуск сервисов"
  systemctl enable "${BARRIER_SERVICE_NAME}"
  systemctl enable "${PANEL_SERVICE_NAME}"

  log "Запускаю web-панель"
  systemctl restart "${PANEL_SERVICE_NAME}"

  log "Пробую запустить BLE-сервис"
  systemctl restart "${BARRIER_SERVICE_NAME}" || warn "BLE-сервис не стартовал. Возможно, ещё не добавлены MAC или не подключено реле."
}

show_summary() {
  local ips
  ips="$(hostname -I 2>/dev/null || true)"

  cat <<EOF

Готово.

Исходники:
  ${SRC_DIR}

Virtualenv:
  ${VENV_DIR}

Следующие команды:
  ${VENV_PYTHON} ${SRC_DIR}/barrier_service.py add AA:BB:CC:DD:EE:FF "My Phone"
  ${VENV_PYTHON} ${SRC_DIR}/barrier_service.py list
  ${VENV_PYTHON} ${SRC_DIR}/barrier_service.py test-open

Сервисы:
  sudo systemctl status ${BARRIER_SERVICE_NAME}
  sudo systemctl status ${PANEL_SERVICE_NAME}

Логи:
  journalctl -u ${BARRIER_SERVICE_NAME} -f
  journalctl -u ${PANEL_SERVICE_NAME} -f

Web-панель:
  http://<IP_ОДНОПЛАТНИКА>:8080

Текущие IP:
  ${ips}

Важно:
- после добавления пользователя в dialout лучше перелогиниться или перезагрузиться;
- если хочешь обновить код позже, достаточно снова запустить этот install.sh.
EOF
}

main() {
  require_root
  install_packages
  prepare_dirs
  fetch_repo
  check_repo_files
  create_venv
  enable_bluetooth
  grant_serial_access
  init_database
  write_barrier_service
  write_panel_service
  enable_and_start_services
  show_summary
}

main "$@"