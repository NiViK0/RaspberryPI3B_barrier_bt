#!/usr/bin/env bash
set -Eeuo pipefail

ETH_INTERFACE="${ETH_INTERFACE:-eth0}"
ETH_IP="${ETH_IP:-10.14.0.117}"
ETH_CIDR="${ETH_CIDR:-24}"
ETH_GATEWAY="${ETH_GATEWAY:-10.14.0.1}"
ETH_DNS="${ETH_DNS:-10.14.0.1 1.1.1.1}"
ETH_CONFIRM="${ETH_CONFIRM:-}"

NM_CONNECTION_NAME="${NM_CONNECTION_NAME:-barrier-eth-static}"
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
    err "Run as root: sudo bash scripts/setup_ethernet_static.sh"
    exit 1
  fi
}

confirm_network_change() {
  if [[ "$ETH_CONFIRM" == "yes" ]]; then
    return
  fi

  warn "This will configure ${ETH_INTERFACE} as ${ETH_IP}/${ETH_CIDR} with gateway ${ETH_GATEWAY}."
  warn "If you are connected through this Ethernet interface with another address, the connection can drop."
  read -r -p "Continue? Type yes: " answer

  if [[ "$answer" != "yes" ]]; then
    err "Cancelled."
    exit 1
  fi
}

backup_file() {
  local path="$1"
  if [[ -f "$path" ]]; then
    cp -a "$path" "${path}.barrier-eth.bak.$(date +%Y%m%d%H%M%S)"
  fi
}

have_active_network_manager() {
  command -v nmcli >/dev/null 2>&1 &&
    systemctl is-active --quiet NetworkManager
}

configure_with_network_manager() {
  log "Configuring static Ethernet through NetworkManager"

  if nmcli -t -f NAME connection show | grep -Fxq "$NM_CONNECTION_NAME"; then
    nmcli connection delete "$NM_CONNECTION_NAME"
  fi

  nmcli connection add \
    type ethernet \
    ifname "$ETH_INTERFACE" \
    con-name "$NM_CONNECTION_NAME" \
    autoconnect yes

  nmcli connection modify "$NM_CONNECTION_NAME" \
    ipv4.method manual \
    ipv4.addresses "${ETH_IP}/${ETH_CIDR}" \
    ipv4.gateway "$ETH_GATEWAY" \
    ipv4.dns "$ETH_DNS" \
    ipv6.method ignore

  nmcli connection up "$NM_CONNECTION_NAME"
}

configure_with_dhcpcd() {
  log "Configuring static Ethernet through dhcpcd"

  backup_file "$DHCPCD_CONF"

  if [[ -f "$DHCPCD_CONF" ]]; then
    sed -i "/# Barrier Ethernet static start/,/# Barrier Ethernet static end/d" "$DHCPCD_CONF"
  fi

  cat >>"$DHCPCD_CONF" <<EOF

# Barrier Ethernet static start
interface ${ETH_INTERFACE}
static ip_address=${ETH_IP}/${ETH_CIDR}
static routers=${ETH_GATEWAY}
static domain_name_servers=${ETH_DNS}
# Barrier Ethernet static end
EOF

  if systemctl list-unit-files | grep -q "^dhcpcd.service"; then
    systemctl restart dhcpcd || true
  fi

  ip addr flush dev "$ETH_INTERFACE" || true
  ip addr add "${ETH_IP}/${ETH_CIDR}" dev "$ETH_INTERFACE" || true
  ip link set "$ETH_INTERFACE" up
}

print_result() {
  cat <<EOF

Done.

Ethernet:
  Interface: ${ETH_INTERFACE}
  Address:   ${ETH_IP}/${ETH_CIDR}
  Gateway:   ${ETH_GATEWAY}

Open web panel through Ethernet:
  http://${ETH_IP}:8080
EOF
}

main() {
  require_root
  confirm_network_change

  if have_active_network_manager; then
    configure_with_network_manager
  else
    configure_with_dhcpcd
  fi

  systemctl restart barrier-panel.service || true
  print_result
}

main "$@"
