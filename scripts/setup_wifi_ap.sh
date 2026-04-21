#!/usr/bin/env bash
set -Eeuo pipefail

AP_INTERFACE="${AP_INTERFACE:-wlan0}"
AP_SSID="${AP_SSID:-Barrier-Panel}"
AP_PASSWORD="${AP_PASSWORD:-change-this-password}"
AP_IP="${AP_IP:-10.42.0.1}"
AP_CIDR="${AP_CIDR:-24}"
AP_DHCP_RANGE_START="${AP_DHCP_RANGE_START:-10.42.0.50}"
AP_DHCP_RANGE_END="${AP_DHCP_RANGE_END:-10.42.0.150}"
AP_DHCP_LEASE="${AP_DHCP_LEASE:-12h}"
AP_COUNTRY="${AP_COUNTRY:-RU}"
AP_CHANNEL="${AP_CHANNEL:-6}"
AP_CONFIRM="${AP_CONFIRM:-}"

NM_CONNECTION_NAME="${NM_CONNECTION_NAME:-barrier-ap}"
DNSMASQ_CONF="/etc/dnsmasq.d/barrier-ap.conf"
HOSTAPD_CONF="/etc/hostapd/hostapd.conf"
HOSTAPD_DEFAULT="/etc/default/hostapd"
DHCPCD_CONF="/etc/dhcpcd.conf"

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
    err "Run as root: sudo AP_PASSWORD='strong-password' bash scripts/setup_wifi_ap.sh"
    exit 1
  fi
}

validate_password() {
  if [[ "${#AP_PASSWORD}" -lt 8 || "${#AP_PASSWORD}" -gt 63 ]]; then
    err "AP_PASSWORD must be 8-63 characters for WPA2."
    exit 1
  fi

  if [[ "$AP_PASSWORD" == "change-this-password" ]]; then
    err "Set a real password, for example: sudo AP_PASSWORD='strong-password' bash scripts/setup_wifi_ap.sh"
    exit 1
  fi
}

confirm_network_change() {
  if [[ "$AP_CONFIRM" == "yes" ]]; then
    return
  fi

  warn "This will turn ${AP_INTERFACE} into a Wi-Fi access point."
  warn "If you are connected to this Raspberry Pi over Wi-Fi, the connection can drop."
  read -r -p "Continue? Type yes: " answer

  if [[ "$answer" != "yes" ]]; then
    err "Cancelled."
    exit 1
  fi
}

backup_file() {
  local path="$1"
  if [[ -f "$path" ]]; then
    cp -a "$path" "${path}.barrier-ap.bak.$(date +%Y%m%d%H%M%S)"
  fi
}

have_active_network_manager() {
  command -v nmcli >/dev/null 2>&1 &&
    systemctl is-active --quiet NetworkManager
}

configure_with_network_manager() {
  log "Configuring access point through NetworkManager"

  if nmcli -t -f NAME connection show | grep -Fxq "$NM_CONNECTION_NAME"; then
    nmcli connection delete "$NM_CONNECTION_NAME"
  fi

  nmcli connection add \
    type wifi \
    ifname "$AP_INTERFACE" \
    con-name "$NM_CONNECTION_NAME" \
    autoconnect yes \
    ssid "$AP_SSID"

  nmcli connection modify "$NM_CONNECTION_NAME" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    802-11-wireless.channel "$AP_CHANNEL" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$AP_PASSWORD" \
    ipv4.method shared \
    ipv4.addresses "${AP_IP}/${AP_CIDR}" \
    ipv6.method ignore

  nmcli connection up "$NM_CONNECTION_NAME"
}

install_hostapd_dnsmasq() {
  log "Installing hostapd and dnsmasq"
  apt update
  apt install -y hostapd dnsmasq
}

configure_with_hostapd() {
  log "Configuring access point through hostapd and dnsmasq"
  install_hostapd_dnsmasq

  systemctl stop hostapd || true
  systemctl stop dnsmasq || true

  backup_file "$DHCPCD_CONF"
  backup_file "$DNSMASQ_CONF"
  backup_file "$HOSTAPD_CONF"
  backup_file "$HOSTAPD_DEFAULT"

  if [[ -f "$DHCPCD_CONF" ]] && ! grep -q "# Barrier AP" "$DHCPCD_CONF"; then
    cat >>"$DHCPCD_CONF" <<EOF

# Barrier AP
interface ${AP_INTERFACE}
static ip_address=${AP_IP}/${AP_CIDR}
nohook wpa_supplicant
EOF
  fi

  cat >"$DNSMASQ_CONF" <<EOF
interface=${AP_INTERFACE}
bind-interfaces
dhcp-range=${AP_DHCP_RANGE_START},${AP_DHCP_RANGE_END},${AP_DHCP_LEASE}
domain-needed
bogus-priv
EOF

  cat >"$HOSTAPD_CONF" <<EOF
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

  if grep -q "^#*DAEMON_CONF=" "$HOSTAPD_DEFAULT"; then
    sed -i "s|^#*DAEMON_CONF=.*|DAEMON_CONF=\"${HOSTAPD_CONF}\"|" "$HOSTAPD_DEFAULT"
  else
    echo "DAEMON_CONF=\"${HOSTAPD_CONF}\"" >>"$HOSTAPD_DEFAULT"
  fi

  systemctl unmask hostapd || true
  systemctl enable hostapd dnsmasq

  if systemctl list-unit-files | grep -q "^dhcpcd.service"; then
    systemctl restart dhcpcd || true
  fi

  ip addr flush dev "$AP_INTERFACE" || true
  ip addr add "${AP_IP}/${AP_CIDR}" dev "$AP_INTERFACE" || true
  ip link set "$AP_INTERFACE" up

  systemctl restart dnsmasq
  systemctl restart hostapd
}

print_result() {
  cat <<EOF

Done.

Connect your phone to Wi-Fi:
  SSID: ${AP_SSID}

Then open:
  http://${AP_IP}:8080

Recommended:
  sudo systemctl edit barrier-panel.service
  set BARRIER_PANEL_PASSWORD and BARRIER_FLASK_SECRET_KEY
EOF
}

main() {
  require_root
  validate_password
  confirm_network_change

  if have_active_network_manager; then
    configure_with_network_manager
  else
    configure_with_hostapd
  fi

  systemctl restart barrier-panel.service || true
  print_result
}

main "$@"
