#!/usr/bin/env bash
set -Eeuo pipefail

AP_INTERFACE="${AP_INTERFACE:-wlan1}"
AP_SSID="${AP_SSID:-Barrier-Gate}"
AP_PASSWORD="${AP_PASSWORD:-change-this-password}"
AP_IP="${AP_IP:-10.42.1.1}"
AP_CIDR="${AP_CIDR:-24}"
AP_DHCP_RANGE_START="${AP_DHCP_RANGE_START:-10.42.1.50}"
AP_DHCP_RANGE_END="${AP_DHCP_RANGE_END:-10.42.1.150}"
AP_DHCP_LEASE="${AP_DHCP_LEASE:-12h}"
AP_COUNTRY="${AP_COUNTRY:-RU}"
AP_CHANNEL="${AP_CHANNEL:-11}"
AP_CONFIRM="${AP_CONFIRM:-}"
AP_START_SERVICES="${AP_START_SERVICES:-yes}"

HOSTAPD_DEB="${HOSTAPD_DEB:-}"
HOSTAPD_BIN="${HOSTAPD_BIN:-/usr/sbin/hostapd}"
DNSMASQ_BIN="${DNSMASQ_BIN:-/usr/sbin/dnsmasq}"
IW_BIN="${IW_BIN:-$(command -v iw || true)}"
IP_BIN="${IP_BIN:-$(command -v ip || true)}"
RFKILL_BIN="${RFKILL_BIN:-$(command -v rfkill || true)}"
NMCLI_BIN="${NMCLI_BIN:-$(command -v nmcli || true)}"

BARRIER_SERVICE="${BARRIER_SERVICE:-barrier.service}"
PANEL_SERVICE="${PANEL_SERVICE:-barrier-panel.service}"
BARRIER_WIFI_MIN_SIGNAL="${BARRIER_WIFI_MIN_SIGNAL:--70}"
BARRIER_WIFI_MAX_INACTIVE_MS="${BARRIER_WIFI_MAX_INACTIVE_MS:-30000}"
BARRIER_WIFI_LEASES_PATH="${BARRIER_WIFI_LEASES_PATH:-/var/lib/misc/dnsmasq.leases}"
BARRIER_ENABLE_WIFI_AUTO_OPEN="${BARRIER_ENABLE_WIFI_AUTO_OPEN:-yes}"

HOSTAPD_CONF="/etc/hostapd/hostapd-${AP_INTERFACE}.conf"
DNSMASQ_CONF="/etc/dnsmasq.d/barrier-${AP_INTERFACE}.conf"
HOSTAPD_SERVICE="barrier-hostapd-${AP_INTERFACE}.service"
DNSMASQ_SERVICE="barrier-dnsmasq-${AP_INTERFACE}.service"
HOSTAPD_SERVICE_FILE="/etc/systemd/system/${HOSTAPD_SERVICE}"
DNSMASQ_SERVICE_FILE="/etc/systemd/system/${DNSMASQ_SERVICE}"
BARRIER_OVERRIDE_DIR="/etc/systemd/system/${BARRIER_SERVICE}.d"
PANEL_OVERRIDE_DIR="/etc/systemd/system/${PANEL_SERVICE}.d"

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
    err "Run as root: sudo AP_PASSWORD='strong-password' bash scripts/setup_external_wifi_ap.sh"
    exit 1
  fi
}

validate_password() {
  if [[ "${#AP_PASSWORD}" -lt 8 || "${#AP_PASSWORD}" -gt 63 ]]; then
    err "AP_PASSWORD must be 8-63 characters for WPA2."
    exit 1
  fi

  if [[ "$AP_PASSWORD" == "change-this-password" ]]; then
    err "Set a real password, for example: sudo AP_PASSWORD='strong-password' bash scripts/setup_external_wifi_ap.sh"
    exit 1
  fi
}

confirm_network_change() {
  if [[ "$AP_CONFIRM" == "yes" ]]; then
    return
  fi

  warn "This will configure ${AP_INTERFACE} as a dedicated Wi-Fi access point."
  warn "Run this from Ethernet/local console if SSH could depend on ${AP_INTERFACE}."
  read -r -p "Continue? Type yes: " answer

  if [[ "$answer" != "yes" ]]; then
    err "Cancelled."
    exit 1
  fi
}

require_command() {
  local path="$1"
  local name="$2"
  if [[ -z "$path" || ! -x "$path" ]]; then
    err "${name} is required but was not found."
    exit 1
  fi
}

install_hostapd_deb_if_needed() {
  if [[ -x "$HOSTAPD_BIN" ]]; then
    return
  fi

  if [[ -n "$HOSTAPD_DEB" ]]; then
    if [[ ! -f "$HOSTAPD_DEB" ]]; then
      err "HOSTAPD_DEB does not exist: ${HOSTAPD_DEB}"
      exit 1
    fi
    log "Installing hostapd from ${HOSTAPD_DEB}"
    dpkg -i "$HOSTAPD_DEB"
  fi

  if [[ ! -x "$HOSTAPD_BIN" ]]; then
    err "hostapd is not installed. Offline Debian 13 arm64 package: https://deb.debian.org/debian/pool/main/w/wpa/hostapd_2.10-24_arm64.deb"
    err "Copy it to the board and rerun with HOSTAPD_DEB=/path/to/hostapd_2.10-24_arm64.deb"
    exit 1
  fi
}

verify_interface() {
  if [[ ! -d "/sys/class/net/${AP_INTERFACE}" ]]; then
    err "Interface ${AP_INTERFACE} was not found."
    exit 1
  fi

  if [[ -n "$IW_BIN" ]] && ! "$IW_BIN" dev "$AP_INTERFACE" info >/dev/null 2>&1; then
    warn "iw cannot read ${AP_INTERFACE}; continuing because some out-of-tree drivers are noisy."
  fi
}

disable_network_manager_for_interface() {
  if [[ -n "$NMCLI_BIN" ]]; then
    log "Disabling NetworkManager management for ${AP_INTERFACE}"
    "$NMCLI_BIN" connection down barrier-gate >/dev/null 2>&1 || true
    "$NMCLI_BIN" connection delete barrier-gate >/dev/null 2>&1 || true
    "$NMCLI_BIN" connection down barrier-gate-test >/dev/null 2>&1 || true
    "$NMCLI_BIN" connection delete barrier-gate-test >/dev/null 2>&1 || true
    "$NMCLI_BIN" device set "$AP_INTERFACE" managed no >/dev/null 2>&1 || true
  fi
}

write_hostapd_config() {
  log "Writing ${HOSTAPD_CONF}"
  mkdir -p /etc/hostapd
  cat > "$HOSTAPD_CONF" <<EOF
country_code=${AP_COUNTRY}
interface=${AP_INTERFACE}
driver=nl80211
ssid=${AP_SSID}
hw_mode=g
channel=${AP_CHANNEL}
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=${AP_PASSWORD}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=CCMP
rsn_pairwise=CCMP
EOF
  chmod 0600 "$HOSTAPD_CONF"
}

write_dnsmasq_config() {
  log "Writing ${DNSMASQ_CONF}"
  mkdir -p /etc/dnsmasq.d
  cat > "$DNSMASQ_CONF" <<EOF
interface=${AP_INTERFACE}
bind-interfaces
dhcp-range=${AP_DHCP_RANGE_START},${AP_DHCP_RANGE_END},${AP_DHCP_LEASE}
domain-needed
bogus-priv
EOF
}

write_systemd_units() {
  require_command "$IP_BIN" ip
  require_command "$RFKILL_BIN" rfkill

  log "Writing ${HOSTAPD_SERVICE_FILE}"
  cat > "$HOSTAPD_SERVICE_FILE" <<EOF
[Unit]
Description=Barrier Wi-Fi AP on ${AP_INTERFACE}
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStartPre=${RFKILL_BIN} unblock wifi
ExecStartPre=${IP_BIN} addr flush dev ${AP_INTERFACE}
ExecStartPre=${IP_BIN} addr add ${AP_IP}/${AP_CIDR} dev ${AP_INTERFACE}
ExecStartPre=${IP_BIN} link set ${AP_INTERFACE} up
ExecStart=${HOSTAPD_BIN} ${HOSTAPD_CONF}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  log "Writing ${DNSMASQ_SERVICE_FILE}"
  cat > "$DNSMASQ_SERVICE_FILE" <<EOF
[Unit]
Description=Barrier DHCP on ${AP_INTERFACE}
After=${HOSTAPD_SERVICE}
Requires=${HOSTAPD_SERVICE}

[Service]
Type=simple
ExecStart=${DNSMASQ_BIN} --no-daemon --conf-file=${DNSMASQ_CONF}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

write_barrier_overrides() {
  if [[ "$BARRIER_ENABLE_WIFI_AUTO_OPEN" != "yes" ]]; then
    return
  fi

  log "Writing Barrier Wi-Fi override for ${BARRIER_SERVICE}"
  mkdir -p "$BARRIER_OVERRIDE_DIR"
  cat > "${BARRIER_OVERRIDE_DIR}/wifi-${AP_INTERFACE}.conf" <<EOF
[Service]
Environment=BARRIER_WIFI_AUTO_OPEN=true
Environment=BARRIER_WIFI_INTERFACE=${AP_INTERFACE}
Environment=BARRIER_WIFI_MIN_SIGNAL=${BARRIER_WIFI_MIN_SIGNAL}
Environment=BARRIER_WIFI_MAX_INACTIVE_MS=${BARRIER_WIFI_MAX_INACTIVE_MS}
Environment=BARRIER_WIFI_LEASES_PATH=${BARRIER_WIFI_LEASES_PATH}
EOF

  if systemctl list-unit-files "$PANEL_SERVICE" >/dev/null 2>&1; then
    log "Writing Barrier panel Wi-Fi override for ${PANEL_SERVICE}"
    mkdir -p "$PANEL_OVERRIDE_DIR"
    cat > "${PANEL_OVERRIDE_DIR}/wifi-${AP_INTERFACE}.conf" <<EOF
[Service]
Environment=BARRIER_WIFI_AUTO_OPEN=true
Environment=BARRIER_WIFI_INTERFACE=${AP_INTERFACE}
Environment=BARRIER_WIFI_MIN_SIGNAL=${BARRIER_WIFI_MIN_SIGNAL}
Environment=BARRIER_WIFI_MAX_INACTIVE_MS=${BARRIER_WIFI_MAX_INACTIVE_MS}
Environment=BARRIER_WIFI_LEASES_PATH=${BARRIER_WIFI_LEASES_PATH}
EOF
  fi
}

start_services() {
  log "Reloading systemd"
  systemctl daemon-reload
  systemctl enable "$HOSTAPD_SERVICE" "$DNSMASQ_SERVICE"

  if [[ "$AP_START_SERVICES" == "yes" ]]; then
    log "Starting ${HOSTAPD_SERVICE} and ${DNSMASQ_SERVICE}"
    systemctl restart "$HOSTAPD_SERVICE"
    systemctl restart "$DNSMASQ_SERVICE"
    if [[ "$BARRIER_ENABLE_WIFI_AUTO_OPEN" == "yes" ]]; then
      systemctl restart "$BARRIER_SERVICE" || true
      systemctl restart "$PANEL_SERVICE" || true
    fi
  fi
}

print_result() {
  cat <<EOF

Done.

Access point:
  Interface: ${AP_INTERFACE}
  SSID: ${AP_SSID}
  Board IP: ${AP_IP}

Connect a phone/car device to ${AP_SSID}, then check:
  iw dev ${AP_INTERFACE} station dump
  /opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py wifi-status

Add the connected station MAC to the allow list:
  /opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py add AA:BB:CC:DD:EE:FF "Car Wi-Fi"

Services:
  systemctl status ${HOSTAPD_SERVICE}
  systemctl status ${DNSMASQ_SERVICE}
  journalctl -u ${BARRIER_SERVICE} -n 80 --no-pager
EOF
}

main() {
  require_root
  validate_password
  confirm_network_change
  install_hostapd_deb_if_needed
  require_command "$HOSTAPD_BIN" hostapd
  require_command "$DNSMASQ_BIN" dnsmasq
  verify_interface
  disable_network_manager_for_interface
  write_hostapd_config
  write_dnsmasq_config
  write_systemd_units
  write_barrier_overrides
  start_services
  print_result
}

main "$@"
