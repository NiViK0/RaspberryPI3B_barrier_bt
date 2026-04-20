#!/usr/bin/env python3
import argparse
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum, auto

import serial
from serial import SerialException

DeviceRow = tuple[int, str, str, int]


# =========================
# НАСТРОЙКИ
# =========================

@dataclass(frozen=True)
class Config:
    db_path: str = "/opt/barrier/barrier.db"

    relay_port: str = "/dev/ttyUSB0"
    relay_baudrate: int = 9600

    scan_time: int = 8
    check_interval: int = 2
    cooldown: int = 15
    pulse_time: int = 2
    missing_threshold: int = 3

    relay_on_cmd: bytes = b"\xA0\x01\x01\xA2"
    relay_off_cmd: bytes = b"\xA0\x01\x00\xA1"


class PresenceStatus(Enum):
    PRESENT = auto()
    ABSENT = auto()
    SCAN_FAILED = auto()


@dataclass
class State:
    any_device_was_present: bool = False
    missing_count: int = 0
    last_trigger_monotonic: float = 0.0


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def normalize_mac(mac: str) -> str:
    return mac.strip().upper()


def validate_mac(mac: str) -> bool:
    return bool(re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", normalize_mac(mac)))


# =========================
# SQLITE
# =========================

def init_db(db_path: str) -> None:
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS allowed_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mac TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def add_device(db_path: str, mac: str, name: str) -> None:
    mac = normalize_mac(mac)
    if not validate_mac(mac):
        raise ValueError(f"Некорректный MAC: {mac}")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO allowed_devices(name, mac, enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(mac) DO UPDATE SET
                name = excluded.name,
                enabled = 1
            """,
            (name.strip(), mac),
        )
        conn.commit()


def list_devices(db_path: str) -> list[DeviceRow]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, mac, enabled FROM allowed_devices ORDER BY name"
        ).fetchall()
    return rows


def set_device_enabled(db_path: str, mac: str, enabled: bool) -> bool:
    mac = normalize_mac(mac)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE allowed_devices SET enabled = ? WHERE mac = ?",
            (1 if enabled else 0, mac),
        )
        conn.commit()
    return cur.rowcount > 0


def remove_device(db_path: str, mac: str) -> bool:
    mac = normalize_mac(mac)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("DELETE FROM allowed_devices WHERE mac = ?", (mac,))
        conn.commit()
    return cur.rowcount > 0


def get_enabled_macs(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT mac FROM allowed_devices WHERE enabled = 1"
        ).fetchall()
    return [normalize_mac(row[0]) for row in rows]


# =========================
# BLUETOOTHCTL
# =========================

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


# =========================
# РЕЛЕ / ЛОГИКА
# =========================

def detect_any_target_presence(devices_output: str, allowed_macs: list[str]) -> PresenceStatus:
    devices_upper = devices_output.upper()
    for mac in allowed_macs:
        if mac in devices_upper:
            logging.info("Обнаружен разрешённый MAC: %s", mac)
            return PresenceStatus.PRESENT
    return PresenceStatus.ABSENT


def pulse_relay(ser: serial.Serial, config: Config) -> None:
    ser.write(config.relay_on_cmd)
    ser.flush()
    try:
        time.sleep(config.pulse_time)
    finally:
        ser.write(config.relay_off_cmd)
        ser.flush()


def trigger_barrier(ser: serial.Serial, config: Config, state: State, action: str) -> bool:
    now = time.monotonic()
    if now - state.last_trigger_monotonic < config.cooldown:
        logging.info("Импульс '%s' заблокирован cooldown", action)
        return False

    if action == "open":
        logging.info(">>> Разрешённый телефон найден, открываем шлагбаум")
    elif action == "close":
        logging.info("<<< Телефон исчез, закрываем шлагбаум")
    else:
        logging.info("*** Выполняем действие: %s", action)

    try:
        pulse_relay(ser, config)
    except SerialException:
        logging.exception("Ошибка работы с реле")
        return False
    except Exception:
        logging.exception("Неожиданная ошибка при работе с реле")
        return False

    state.last_trigger_monotonic = now
    return True


def process_presence(
    presence: PresenceStatus,
    devices_output: str,
    ser: serial.Serial,
    config: Config,
    state: State,
) -> None:
    if devices_output.strip():
        logging.info("Найденные устройства:\n%s", devices_output)
    else:
        logging.info("Список устройств пуст")

    if presence == PresenceStatus.SCAN_FAILED:
        logging.warning("Сканирование не удалось, состояние не меняем")
        return

    if presence == PresenceStatus.PRESENT:
        state.missing_count = 0
        if not state.any_device_was_present:
            if trigger_barrier(ser, config, state, "open"):
                state.any_device_was_present = True
        else:
            logging.info("Разрешённое устройство всё ещё в зоне")
        return

    if state.any_device_was_present:
        state.missing_count += 1
        logging.info(
            "Разрешённое устройство не найдено (%s/%s)",
            state.missing_count,
            config.missing_threshold,
        )
        if state.missing_count >= config.missing_threshold:
            if trigger_barrier(ser, config, state, "close"):
                state.any_device_was_present = False
                state.missing_count = 0
    else:
        logging.info("Разрешённые устройства не найдены")
        state.missing_count = 0


def test_open(config: Config) -> None:
    with serial.Serial(config.relay_port, config.relay_baudrate, timeout=1) as ser:
        pulse_relay(ser, config)


# =========================
# КОМАНДЫ
# =========================

def cmd_init_db(config: Config) -> None:
    init_db(config.db_path)
    print(f"База инициализирована: {config.db_path}")


def cmd_add(config: Config, mac: str, name: str) -> None:
    init_db(config.db_path)
    add_device(config.db_path, mac, name)
    print(f"Добавлено: {name} [{mac.upper()}]")


def cmd_list(config: Config) -> None:
    init_db(config.db_path)
    rows = list_devices(config.db_path)
    if not rows:
        print("База пуста")
        return

    for row_id, name, mac, enabled in rows:
        status = "enabled" if enabled else "disabled"
        print(f"{row_id}: {name} | {mac} | {status}")


def cmd_enable(config: Config, mac: str) -> None:
    init_db(config.db_path)
    if set_device_enabled(config.db_path, mac, True):
        print(f"Устройство включено: {mac.upper()}")
    else:
        print(f"Устройство не найдено: {mac.upper()}")
        sys.exit(1)


def cmd_disable(config: Config, mac: str) -> None:
    init_db(config.db_path)
    if set_device_enabled(config.db_path, mac, False):
        print(f"Устройство отключено: {mac.upper()}")
    else:
        print(f"Устройство не найдено: {mac.upper()}")
        sys.exit(1)


def cmd_remove(config: Config, mac: str) -> None:
    init_db(config.db_path)
    if remove_device(config.db_path, mac):
        print(f"Устройство удалено: {mac.upper()}")
    else:
        print(f"Устройство не найдено: {mac.upper()}")
        sys.exit(1)


def cmd_test_open(config: Config) -> None:
    test_open(config)
    print("Тестовый импульс на реле отправлен")


def cmd_run(config: Config) -> None:
    init_db(config.db_path)
    state = State()
    bt = BluetoothCtlSession()

    try:
        allowed_macs = get_enabled_macs(config.db_path)
        if not allowed_macs:
            logging.error("В базе нет разрешённых MAC-адресов")
            sys.exit(1)

        logging.info("Разрешённых MAC-адресов: %s", len(allowed_macs))
        bt.start()

        with serial.Serial(config.relay_port, config.relay_baudrate, timeout=1) as ser:
            while True:
                allowed_macs = get_enabled_macs(config.db_path)
                if not allowed_macs:
                    logging.warning("Список разрешённых MAC пуст")
                    time.sleep(config.check_interval)
                    continue

                base_status, devices_output = scan_once(bt, config.scan_time)

                if base_status == PresenceStatus.SCAN_FAILED:
                    process_presence(base_status, devices_output, ser, config, state)
                else:
                    actual_presence = detect_any_target_presence(devices_output, allowed_macs)
                    process_presence(actual_presence, devices_output, ser, config, state)

                time.sleep(config.check_interval)

    except KeyboardInterrupt:
        logging.info("Остановлено пользователем")
    except SerialException:
        logging.exception("Ошибка доступа к порту реле: %s", config.relay_port)
        sys.exit(1)
    except Exception:
        logging.exception("Критическая ошибка")
        sys.exit(1)
    finally:
        bt.stop()


# =========================
# CLI
# =========================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Barrier BLE controller")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init_db = subparsers.add_parser("init-db", help="Создать SQLite-базу")
    p_init_db.set_defaults(handler=lambda config, args: cmd_init_db(config))

    p_add = subparsers.add_parser("add", help="Добавить или обновить устройство")
    p_add.add_argument("mac", help="MAC-адрес телефона")
    p_add.add_argument("name", help="Имя устройства")
    p_add.set_defaults(handler=lambda config, args: cmd_add(config, args.mac, args.name))

    p_enable = subparsers.add_parser("enable", help="Включить устройство")
    p_enable.add_argument("mac", help="MAC-адрес устройства")
    p_enable.set_defaults(handler=lambda config, args: cmd_enable(config, args.mac))

    p_disable = subparsers.add_parser("disable", help="Отключить устройство")
    p_disable.add_argument("mac", help="MAC-адрес устройства")
    p_disable.set_defaults(handler=lambda config, args: cmd_disable(config, args.mac))

    p_remove = subparsers.add_parser("remove", help="Удалить устройство")
    p_remove.add_argument("mac", help="MAC-адрес устройства")
    p_remove.set_defaults(handler=lambda config, args: cmd_remove(config, args.mac))

    p_list = subparsers.add_parser("list", help="Показать устройства")
    p_list.set_defaults(handler=lambda config, args: cmd_list(config))

    p_test_open = subparsers.add_parser("test-open", help="Тестовый импульс на реле")
    p_test_open.set_defaults(handler=lambda config, args: cmd_test_open(config))

    p_run = subparsers.add_parser("run", help="Запустить основной цикл")
    p_run.set_defaults(handler=lambda config, args: cmd_run(config))

    return parser


def main() -> None:
    setup_logging()
    config = Config()

    parser = build_parser()
    args = parser.parse_args()
    args.handler(config, args)


if __name__ == "__main__":
    main()
