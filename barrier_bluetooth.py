import logging
import subprocess
import time

from barrier_types import PresenceStatus


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
