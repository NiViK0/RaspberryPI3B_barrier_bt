# External Wi-Fi AP for barrier auto-open

Use this path for USB Wi-Fi adapters such as Realtek RTL8188FU/RTL8188FTV when
NetworkManager can create an open AP but fails WPA with:

```text
802.1X supplicant took too long to authenticate
```

The dedicated script configures the external adapter through `hostapd` and a
small dedicated `dnsmasq` service, then writes `barrier.service` overrides so
the main service scans the external AP interface.

## Default Network

```text
SSID: Barrier-Gate
Board IP: 10.42.1.1
Interface: wlan1
```

If the board has no internet, download the Debian 13 arm64 package on another
computer and copy it to the board:

```text
https://deb.debian.org/debian/pool/main/w/wpa/hostapd_2.10-24_arm64.deb
```

## Deploy

If `hostapd` is still running manually from a test terminal, stop it with
`Ctrl+C` before enabling the service.

```bash
cd /opt/barrier/src
sudo \
  HOSTAPD_DEB=/tmp/hostapd_2.10-24_arm64.deb \
  AP_INTERFACE=wlan1 \
  AP_SSID='Barrier-Gate' \
  AP_PASSWORD='strong-password-123' \
  AP_IP=10.42.1.1 \
  AP_CHANNEL=11 \
  AP_CONFIRM=yes \
  bash scripts/setup_external_wifi_ap.sh
```

For an already installed `hostapd`, omit `HOSTAPD_DEB`.

## Check The AP

```bash
systemctl status barrier-hostapd-wlan1.service --no-pager
systemctl status barrier-dnsmasq-wlan1.service --no-pager
iw dev
ip addr show wlan1
```

Expected `iw dev` state:

```text
Interface wlan1
        ssid Barrier-Gate
        type AP
```

Connect a phone or car device to `Barrier-Gate`. The network may show as
"without internet"; this is expected. The board web panel is available at:

```text
http://10.42.1.1:8080
```

## Add A Device

Find the connected station MAC:

```bash
iw dev wlan1 station dump
cat /var/lib/misc/dnsmasq.leases 2>/dev/null
```

Add that Wi-Fi MAC to the allowed list:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py add AA:BB:CC:DD:EE:FF "Car Wi-Fi"
```

Verify barrier Wi-Fi detection:

```bash
journalctl -u barrier.service -n 80 --no-pager
BARRIER_WIFI_AUTO_OPEN=true \
BARRIER_WIFI_INTERFACE=wlan1 \
BARRIER_WIFI_LEASES_PATH=/var/lib/misc/dnsmasq.leases \
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py wifi-status
```

## Tuning

Recommended first thresholds:

```ini
BARRIER_WIFI_MIN_SIGNAL=-70
BARRIER_WIFI_MAX_INACTIVE_MS=30000
```

Increase `BARRIER_WIFI_MIN_SIGNAL` toward `-60` if the barrier opens too far
away. Decrease it toward `-75` if the device is not detected early enough.

Phone notes:

- Disable private/random MAC for `Barrier-Gate`, or add the private MAC shown by
  `iw dev wlan1 station dump`.
- Enable auto-join for `Barrier-Gate`.
- The device must actually connect to the AP; passive proximity without Wi-Fi
  association is not enough for this mode.
