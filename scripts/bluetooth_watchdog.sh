#!/usr/bin/env bash
set -Eeuo pipefail

LOGGER_TAG="barrier-bluetooth-watchdog"

log() {
  logger -t "$LOGGER_TAG" "$*"
  echo "[$LOGGER_TAG] $*"
}

bluetooth_state() {
  bluetoothctl show 2>&1 || true
}

is_healthy() {
  local state
  state="$(bluetooth_state)"

  grep -q "Powered: yes" <<<"$state" &&
    grep -q "PowerState: on" <<<"$state"
}

if is_healthy; then
  exit 0
fi

log "Bluetooth is not healthy, trying recovery"
log "$(bluetooth_state)"

rfkill unblock bluetooth || true
systemctl restart bluetooth || true
sleep 2
bluetoothctl power on || true
sleep 1

if is_healthy; then
  log "Bluetooth recovered, restarting barrier.service"
  systemctl restart barrier.service || true
  exit 0
fi

log "Bluetooth recovery failed"
log "$(bluetooth_state)"
exit 1
