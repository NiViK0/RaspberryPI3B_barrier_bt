import logging
import re
import subprocess
import time

from barrier_db import normalize_mac
from barrier_types import PresenceStatus


MAC_RE = re.compile(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", re.IGNORECASE)


class BluetoothCtlSession:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return

        self.proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        time.sleep(1.0)
        self._send_to_running_process("power on")
        self._send_to_running_process("agent on")
        self._send_to_running_process("default-agent")
        self._send_to_running_process("scan on")
        logging.info("bluetoothctl запущен, Bluetooth включён, scan on активирован")

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return

        try:
            if proc.poll() is None:
                self._send_to_process(proc, "scan off")
                self._send_to_process(proc, "quit")
                proc.terminate()
        except Exception:
            logging.debug("Не удалось корректно остановить bluetoothctl", exc_info=True)

    def ensure_alive(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            logging.warning("bluetoothctl не запущен, выполняется перезапуск")
            self.start()

    def send(self, command: str) -> None:
        self.ensure_alive()
        self._send_to_running_process(command)

    def _send_to_running_process(self, command: str) -> None:
        assert self.proc is not None
        self._send_to_process(self.proc, command)

    @staticmethod
    def _send_to_process(proc: subprocess.Popen, command: str) -> None:
        if proc.stdin is None:
            raise RuntimeError("stdin bluetoothctl недоступен")
        proc.stdin.write(command + "\n")
        proc.stdin.flush()

    def ensure_scan_on(self) -> None:
        self.send("scan on")

    def get_devices_output(self) -> str:
        result = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            raise RuntimeError(output or "Не удалось выполнить bluetoothctl devices")
        return output

    def get_device_info(self, mac: str) -> str:
        result = subprocess.run(
            ["bluetoothctl", "info", mac],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            logging.debug("bluetoothctl info %s returned an error: %s", mac, output)
        return output


def scan_once(bt: BluetoothCtlSession, scan_time: int) -> tuple[PresenceStatus, str]:
    try:
        bt.ensure_alive()
        bt.ensure_scan_on()
        time.sleep(scan_time)
        devices_output = bt.get_devices_output()
        return PresenceStatus.ABSENT, devices_output
    except Exception as exc:
        logging.warning("Ошибка BLE-сканирования: %s", exc)
        return PresenceStatus.SCAN_FAILED, ""


def parse_devices_output(devices_output: str) -> list[dict[str, object]]:
    devices: list[dict[str, object]] = []
    for line in devices_output.splitlines():
        match = MAC_RE.search(line)
        if not match:
            continue
        mac = normalize_mac(match.group(0))
        name = line[match.end() :].strip() or mac
        devices.append(
            {
                "mac": mac,
                "name": name,
                "connected": False,
                "rssi": None,
                "allowed": False,
            }
        )
    return devices


def apply_device_info(device: dict[str, object], info_output: str) -> None:
    for line in info_output.splitlines():
        key, separator, value = line.strip().partition(":")
        if not separator:
            continue
        key = key.strip().lower()
        value = value.strip()
        if key == "name" and value:
            device["name"] = value
        elif key == "connected":
            device["connected"] = value.lower() == "yes"
        elif key == "rssi":
            match = re.search(r"-?\d+", value)
            if match:
                device["rssi"] = int(match.group(0))


def collect_scan_details(
    bt: BluetoothCtlSession,
    devices_output: str,
    allowed_macs: list[str],
) -> dict[str, object]:
    allowed_set = {normalize_mac(mac) for mac in allowed_macs}
    devices = parse_devices_output(devices_output)

    for device in devices:
        mac = str(device["mac"])
        device["allowed"] = mac in allowed_set
        apply_device_info(device, bt.get_device_info(mac))

    rssi_values = [int(device["rssi"]) for device in devices if device.get("rssi") is not None]
    max_rssi = max(rssi_values) if rssi_values else None
    strongest = ""
    if max_rssi is not None:
        strongest_device = next(device for device in devices if device.get("rssi") == max_rssi)
        strongest = f"{strongest_device.get('name') or strongest_device['mac']} ({strongest_device['mac']})"

    return {
        "devices": devices,
        "total_devices": len(devices),
        "connected_devices": sum(1 for device in devices if device.get("connected")),
        "allowed_seen": sum(1 for device in devices if device.get("allowed")),
        "max_rssi": max_rssi,
        "strongest_device": strongest,
    }


def detect_allowed_presence_from_details(
    devices: list[dict[str, object]],
    min_rssi: int | None = None,
) -> PresenceStatus:
    for device in devices:
        if not device.get("allowed"):
            continue
        if min_rssi is None:
            return PresenceStatus.PRESENT
        rssi = device.get("rssi")
        if rssi is not None and int(rssi) >= min_rssi:
            return PresenceStatus.PRESENT
    return PresenceStatus.ABSENT
