#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/NiViK0/RaspberryPI3B_barrier_bt.git"
BRANCH="${BRANCH:-main}"
INSTALL_FROM_LOCAL="${INSTALL_FROM_LOCAL:-false}"
LOCAL_SOURCE_DIR="${LOCAL_SOURCE_DIR:-}"
INSTALL_SYSTEM_PACKAGES="${INSTALL_SYSTEM_PACKAGES:-true}"
PIP_OFFLINE="${PIP_OFFLINE:-false}"
WHEELHOUSE_DIR="${WHEELHOUSE_DIR:-}"

APP_DIR="/opt/barrier"
SRC_DIR="${APP_DIR}/src"
VENV_DIR="${APP_DIR}/venv"

SERVICE_USER="${SUDO_USER:-$USER}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

PYTHON_BIN="python3"
VENV_PYTHON="${VENV_DIR}/bin/python"

BARRIER_SERVICE_NAME="barrier.service"
PANEL_SERVICE_NAME="barrier-panel.service"
WATCHDOG_SERVICE_NAME="barrier-bluetooth-watchdog.service"
WATCHDOG_TIMER_NAME="barrier-bluetooth-watchdog.timer"

BARRIER_SERVICE_FILE="/etc/systemd/system/${BARRIER_SERVICE_NAME}"
PANEL_SERVICE_FILE="/etc/systemd/system/${PANEL_SERVICE_NAME}"
WATCHDOG_SERVICE_FILE="/etc/systemd/system/${WATCHDOG_SERVICE_NAME}"
WATCHDOG_TIMER_FILE="/etc/systemd/system/${WATCHDOG_TIMER_NAME}"
PANEL_SUDOERS_FILE="/etc/sudoers.d/barrier-panel-management"

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
  if [[ "$INSTALL_SYSTEM_PACKAGES" == "0" || "$INSTALL_SYSTEM_PACKAGES" == "false" ]]; then
    log "Пропускаю установку системных пакетов: INSTALL_SYSTEM_PACKAGES=${INSTALL_SYSTEM_PACKAGES}"
    return
  fi

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
  if [[ "$INSTALL_FROM_LOCAL" == "1" || "$INSTALL_FROM_LOCAL" == "true" ]]; then
    local source_dir="${LOCAL_SOURCE_DIR:-$(pwd)}"
    log "Копирую локальные исходники из ${source_dir} в ${SRC_DIR}"
    mkdir -p "$SRC_DIR"
    cp -a "${source_dir}/." "$SRC_DIR/"
    chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$SRC_DIR"
    return
  fi

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

  for f in barrier_service.py panel.py scripts/bluetooth_watchdog.sh scripts/barrier_open.sh scripts/barrier_set_time.sh scripts/setup_wifi_ap.sh scripts/setup_ethernet_static.sh; do
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

create_compat_symlinks() {
  log "Создаю совместимые ссылки в ${APP_DIR}"
  ln -sfn "${SRC_DIR}/barrier_service.py" "${APP_DIR}/barrier_service.py"
  ln -sfn "${SRC_DIR}/panel.py" "${APP_DIR}/panel.py"
  chown -h "${SERVICE_USER}:${SERVICE_GROUP}" \
    "${APP_DIR}/barrier_service.py" \
    "${APP_DIR}/panel.py" || true
}

prepare_scripts() {
  log "Настраиваю исполняемые скрипты"
  chmod +x "${SRC_DIR}/scripts/bluetooth_watchdog.sh"
  chmod +x "${SRC_DIR}/scripts/barrier_open.sh"
  chmod +x "${SRC_DIR}/scripts/barrier_set_time.sh"
  chmod +x "${SRC_DIR}/scripts/setup_wifi_ap.sh"
  chmod +x "${SRC_DIR}/scripts/setup_ethernet_static.sh"
}

install_emergency_open_wrapper() {
  log "Устанавливаю аварийную команду /usr/local/bin/barrier-open"
  install -m 0755 "${SRC_DIR}/scripts/barrier_open.sh" /usr/local/bin/barrier-open
}

install_time_sync_wrapper() {
  log "Устанавливаю команду синхронизации времени /usr/local/bin/barrier-set-time"
  install -m 0755 "${SRC_DIR}/scripts/barrier_set_time.sh" /usr/local/bin/barrier-set-time
}

create_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    log "Создаю virtualenv"
    sudo -u "$SERVICE_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  if [[ "$PIP_OFFLINE" == "1" || "$PIP_OFFLINE" == "true" ]]; then
    if [[ -z "$WHEELHOUSE_DIR" || ! -d "$WHEELHOUSE_DIR" ]]; then
      err "Для PIP_OFFLINE=1 укажи WHEELHOUSE_DIR с wheel-файлами Python-зависимостей"
      exit 1
    fi

    log "Ставлю Python-зависимости офлайн из ${WHEELHOUSE_DIR}"
    if [[ -f "${SRC_DIR}/requirements.txt" ]]; then
      sudo -u "$SERVICE_USER" "$VENV_PYTHON" -m pip install --no-index --find-links "$WHEELHOUSE_DIR" -r "${SRC_DIR}/requirements.txt"
    else
      sudo -u "$SERVICE_USER" "$VENV_PYTHON" -m pip install --no-index --find-links "$WHEELHOUSE_DIR" pyserial flask
    fi
  else
    log "Обновляю pip и ставлю зависимости"
    sudo -u "$SERVICE_USER" "$VENV_PYTHON" -m pip install --upgrade pip
    if [[ -f "${SRC_DIR}/requirements.txt" ]]; then
      sudo -u "$SERVICE_USER" "$VENV_PYTHON" -m pip install -r "${SRC_DIR}/requirements.txt"
    else
      sudo -u "$SERVICE_USER" "$VENV_PYTHON" -m pip install pyserial flask
    fi
  fi

  sudo -u "$SERVICE_USER" "$VENV_PYTHON" - <<'PY'
import flask
import serial

print("Python-зависимости проверены")
PY
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
Environment=BARRIER_DB_PATH=${APP_DIR}/barrier.db
Environment=BARRIER_BACKUP_DIR=${APP_DIR}/backups
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
Environment=BARRIER_DB_PATH=${APP_DIR}/barrier.db
Environment=BARRIER_BACKUP_DIR=${APP_DIR}/backups
Environment=BARRIER_SCRIPT=${SRC_DIR}/barrier_service.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
}

write_watchdog_units() {
  log "Создаю unit ${WATCHDOG_SERVICE_NAME}"
  cat > "$WATCHDOG_SERVICE_FILE" <<EOF
[Unit]
Description=Barrier Bluetooth Watchdog
After=bluetooth.service

[Service]
Type=oneshot
ExecStart=${SRC_DIR}/scripts/bluetooth_watchdog.sh
EOF

  log "Создаю timer ${WATCHDOG_TIMER_NAME}"
  cat > "$WATCHDOG_TIMER_FILE" <<EOF
[Unit]
Description=Run Barrier Bluetooth Watchdog periodically

[Timer]
OnBootSec=30
OnUnitActiveSec=60
AccuracySec=10
Unit=${WATCHDOG_SERVICE_NAME}

[Install]
WantedBy=timers.target
EOF
}

write_panel_sudoers() {
  log "Настраиваю sudo-доступ web-панели к ограниченным management-командам"
  cat > "$PANEL_SUDOERS_FILE" <<EOF
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart bluetooth, /usr/bin/systemctl restart barrier.service, /usr/bin/systemctl restart barrier-bluetooth-watchdog.timer, /usr/bin/systemctl start barrier-bluetooth-watchdog.service, /usr/bin/systemctl reboot, /bin/systemctl restart bluetooth, /bin/systemctl restart barrier.service, /bin/systemctl restart barrier-bluetooth-watchdog.timer, /bin/systemctl start barrier-bluetooth-watchdog.service, /bin/systemctl reboot
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/local/bin/barrier-set-time *
EOF

  chmod 0440 "$PANEL_SUDOERS_FILE"
  visudo -cf "$PANEL_SUDOERS_FILE"
}

enable_and_start_services() {
  log "Перечитываю systemd"
  systemctl daemon-reload

  log "Включаю автозапуск сервисов"
  systemctl enable "${BARRIER_SERVICE_NAME}"
  systemctl enable "${PANEL_SERVICE_NAME}"
  systemctl enable "${WATCHDOG_TIMER_NAME}"

  log "Запускаю web-панель"
  systemctl restart "${PANEL_SERVICE_NAME}"

  log "Запускаю Bluetooth watchdog timer"
  systemctl restart "${WATCHDOG_TIMER_NAME}"

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
  barrier-open

Сервисы:
  sudo systemctl status ${BARRIER_SERVICE_NAME}
  sudo systemctl status ${PANEL_SERVICE_NAME}
  sudo systemctl status ${WATCHDOG_TIMER_NAME}

Логи:
  journalctl -u ${BARRIER_SERVICE_NAME} -f
  journalctl -u ${PANEL_SERVICE_NAME} -f
  journalctl -u ${WATCHDOG_SERVICE_NAME} -f

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
  create_compat_symlinks
  prepare_scripts
  create_venv
  install_emergency_open_wrapper
  install_time_sync_wrapper
  enable_bluetooth
  grant_serial_access
  init_database
  write_barrier_service
  write_panel_service
  write_watchdog_units
  write_panel_sudoers
  enable_and_start_services
  show_summary
}

main "$@"
