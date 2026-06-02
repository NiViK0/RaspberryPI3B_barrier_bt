import logging
import os
import re
import subprocess

from barrier_config import Config
from barrier_db import normalize_mac
from barrier_types import PresenceStatus


MAC_RE = re.compile(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", re.IGNORECASE)


def _parse_int(value: str) -> int | None:
    match = re.search(r"-?\d+", value)
    if not match:
        return None
    return int(match.group(0))


def parse_iw_station_dump(output: str) -> list[dict[str, object]]:
    stations: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for line in output.splitlines():
        stripped = line.strip()
        match = re.match(r"^Station\s+([0-9A-F:]{17})\s+\(on\s+([^)]+)\)", stripped, re.IGNORECASE)
        if match:
            current = {
                "mac": normalize_mac(match.group(1)),
                "interface": match.group(2),
                "name": "",
                "ip": "",
                "hostname": "",
                "signal": None,
                "inactive_ms": None,
                "rx_bytes": None,
                "tx_bytes": None,
                "authorized": None,
                "allowed": False,
                "active": False,
            }
            stations.append(current)
            continue

        if current is None:
            continue

        key, separator, value = stripped.partition(":")
        if not separator:
            continue

        key = key.strip().lower()
        value = value.strip()
        if key == "inactive time":
            current["inactive_ms"] = _parse_int(value)
        elif key == "signal":
            current["signal"] = _parse_int(value)
        elif key == "rx bytes":
            current["rx_bytes"] = _parse_int(value)
        elif key == "tx bytes":
            current["tx_bytes"] = _parse_int(value)
        elif key == "authorized":
            current["authorized"] = value.lower() == "yes"

    return stations


def read_dnsmasq_leases(path: str) -> dict[str, dict[str, str]]:
    if not path or not os.path.exists(path):
        return {}

    leases: dict[str, dict[str, str]] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as file:
            for line in file:
                parts = line.split()
                if len(parts) < 4 or not MAC_RE.fullmatch(parts[1]):
                    continue
                hostname = "" if parts[3] == "*" else parts[3]
                leases[normalize_mac(parts[1])] = {
                    "ip": parts[2],
                    "hostname": hostname,
                }
    except OSError as exc:
        logging.debug("Не удалось прочитать DHCP leases %s: %s", path, exc)

    return leases


def _run_station_dump_command(cmd: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
        encoding="utf-8",
        errors="replace",
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode == 0, output


def run_iw_station_dump(interface: str) -> str:
    commands = [
        ["iw", "dev", interface, "station", "dump"],
        ["sudo", "-n", "iw", "dev", interface, "station", "dump"],
    ]
    errors = []

    for cmd in commands:
        try:
            ok, output = _run_station_dump_command(cmd)
        except FileNotFoundError as exc:
            errors.append(f"{cmd[0]}: {exc}")
            continue

        if ok:
            return output
        errors.append(output or f"{' '.join(cmd)} failed")

    raise RuntimeError("; ".join(errors) or f"Не удалось прочитать Wi-Fi станции на {interface}")


def station_passes_filters(
    station: dict[str, object],
    min_signal: int | None,
    max_inactive_ms: int | None,
) -> bool:
    if min_signal is not None:
        signal = station.get("signal")
        if signal is None or int(signal) < min_signal:
            return False

    if max_inactive_ms is not None and max_inactive_ms > 0:
        inactive_ms = station.get("inactive_ms")
        if inactive_ms is not None and int(inactive_ms) > max_inactive_ms:
            return False

    return True


def detect_allowed_wifi_presence(
    stations: list[dict[str, object]],
    min_signal: int | None = None,
    max_inactive_ms: int | None = None,
) -> PresenceStatus:
    for station in stations:
        if not station.get("allowed"):
            continue
        if station_passes_filters(station, min_signal, max_inactive_ms):
            return PresenceStatus.PRESENT
    return PresenceStatus.ABSENT


def collect_wifi_details(config: Config, allowed_macs: list[str]) -> dict[str, object]:
    raw_output = run_iw_station_dump(config.wifi_interface)
    stations = parse_iw_station_dump(raw_output)
    leases = read_dnsmasq_leases(config.wifi_leases_path)
    allowed_set = {normalize_mac(mac) for mac in allowed_macs}

    for station in stations:
        mac = str(station["mac"])
        lease = leases.get(mac, {})
        station["ip"] = lease.get("ip", "")
        station["hostname"] = lease.get("hostname", "")
        station["name"] = station["hostname"] or station["ip"] or mac
        station["allowed"] = mac in allowed_set
        station["active"] = station_passes_filters(
            station,
            config.wifi_min_signal,
            config.wifi_max_inactive_ms,
        )

    signal_values = [int(station["signal"]) for station in stations if station.get("signal") is not None]
    max_signal = max(signal_values) if signal_values else None
    strongest = ""
    if max_signal is not None:
        strongest_station = next(station for station in stations if station.get("signal") == max_signal)
        strongest = f"{strongest_station.get('name') or strongest_station['mac']} ({strongest_station['mac']})"

    return {
        "raw_output": raw_output,
        "stations": stations,
        "connected_devices": len(stations),
        "allowed_seen": sum(1 for station in stations if station.get("allowed") and station.get("active")),
        "max_signal": max_signal,
        "strongest_station": strongest,
        "presence": detect_allowed_wifi_presence(
            stations,
            config.wifi_min_signal,
            config.wifi_max_inactive_ms,
        ),
    }


def format_wifi_devices_output(stations: list[dict[str, object]]) -> str:
    lines = []
    for station in stations:
        signal = station.get("signal")
        inactive_ms = station.get("inactive_ms")
        signal_label = f"{signal} dBm" if signal is not None else "n/a"
        inactive_label = f"{inactive_ms} ms" if inactive_ms is not None else "n/a"
        allowed = "yes" if station.get("allowed") else "no"
        active = "yes" if station.get("active") else "no"
        name = station.get("name") or station.get("mac")
        lines.append(
            f"Wi-Fi station {station['mac']} {name} "
            f"signal={signal_label} inactive={inactive_label} allowed={allowed} active={active}"
        )
    return "\n".join(lines)
