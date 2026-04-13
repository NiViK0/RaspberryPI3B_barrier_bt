# =========================
#
# =========================
# Установка 
# =========================
#
# Скопировать файлы в /opt/barrier:
#
# sudo mkdir -p /opt/barrier
# sudo cp barrier_service.py /opt/barrier/
# sudo cp panel.py /opt/barrier/
# sudo chmod +x /opt/barrier/barrier_service.py
# sudo chmod +x /opt/barrier/panel.py
#
# Установить зависимости:
#
# sudo apt update
# sudo apt install -y python3 python3-pip bluetooth bluez
# pip3 install pyserial flask
#
# Проверить, что bluetoothctl работает:
#
# bluetoothctl show
#
# Создание базы и добавление телефонов
#
# Создать базу:
#
# python3 /opt/barrier/barrier_service.py init-db
#
# Добавить телефон:
#
# python3 /opt/barrier/barrier_service.py add AA:BB:CC:DD:EE:FF "Телефон 1"
#
# Посмотреть список:
#
# python3 /opt/barrier/barrier_service.py list

# Отключить устройство без удаления:
#
# python3 /opt/barrier/barrier_service.py disable AA:BB:CC:DD:EE:FF
#
# Включить обратно:
#
# python3 /opt/barrier/barrier_service.py enable AA:BB:CC:DD:EE:FF
#
# Удалить:
#
# python3 /opt/barrier/barrier_service.py remove AA:BB:CC:DD:EE:FF
#
#Проверка реле
#
#До запуска BLE-логики лучше проверить только реле:
#
#python3 /opt/barrier/barrier_service.py test-open
#
#Если реле не срабатывает, сначала надо проверить:
#
#правильный relay_port
#правильную скорость relay_baudrate
#правильные команды модуля
#
#Сейчас в коде оставлены ваши команды из исходника.
#
# Запуск основного сервиса вручную
#python3 /opt/barrier/barrier_service.py run
#
#
# Автозапуск через systemd
#
# Скопировать unit-файлы:
#
# sudo cp barrier.service /etc/systemd/system/
#sudo cp barrier-panel.service /etc/systemd/system/
#sudo systemctl daemon-reload
#
#Включить автозапуск:
#
#sudo systemctl enable barrier.service
#sudo systemctl enable barrier-panel.service
#
#Запустить:
#
#sudo systemctl start barrier.service
#sudo systemctl start barrier-panel.service
#
#Проверить статус:
#
#sudo systemctl status barrier.service
#sudo systemctl status barrier-panel.service
#
#Логи:
#
#journalctl -u barrier.service -f
#journalctl -u barrier-panel.service -f
#
#Управление с телефона
#
#panel.py поднимает простую web-панель на порту 8080.
#
#После запуска открывайте в телефоне:
#
#http://IP_ОДНОПЛАТНИКА:8080
#
# =========================
#
# =========================
# Установка 
# =========================
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


def validate_mac(mac: str) -> bool:
    return bool(re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac.strip()))


# =========================
# SQLITE (База данных)
# =========================

def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS allowed_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mac TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def add_device(db_path: str, mac: str, name: str) -> None:
    mac = mac.upper().strip()
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


def list_devices(db_path: str) -> list[tuple[int, str, str, int]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, mac, enabled FROM allowed_devices ORDER BY name"
        ).fetchall()
    return rows


def get_enabled_macs(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT mac FROM allowed_devices WHERE enabled = 1"
        ).fetchall()
    return [row[0].upper() for row in rows]


# =========================
# BLUETOOTHCTL-служба
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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        time.sleep(1.0)
        self.send("power on")
        self.send("agent on")
        self.send("default-agent")
        self.send("scan on")
        logging.info("bluetoothctl запущен и scan on включён")

    def stop(self) -> None:
        if self.proc:
            try:
                self.send("scan off")
                self.send("quit")
            except Exception:
                pass
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None

    def ensure_alive(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            logging.warning("bluetoothctl не запущен, перезапускаем")
            self.start()

    def send(self, command: str) -> None:
        self.ensure_alive()
        assert self.proc is not None
        assert self.proc.stdin is not None
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

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
        if result.returncode != 0:
            raise RuntimeError((result.stdout or "") + (result.stderr or ""))
        return result.stdout.strip()


def scan_once(bt: BluetoothCtlSession, scan_time: int) -> tuple[PresenceStatus, str]:
    try:
        bt.ensure_alive()
        bt.ensure_scan_on()   
        time.sleep(scan_time)
        devices_output = bt.get_devices_output()
        return PresenceStatus.ABSENT, devices_output
    except Exception as e:
        logging.warning("Ошибка BLE-сканирования: %s", e)
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


def trigger_barrier(
    ser: serial.Serial,
    config: Config,
    state: State,
    action: str,
) -> bool:
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


# =========================
# КОМАНДЫ
# =========================

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Barrier BLE controller")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_add = subparsers.add_parser("add", help="Добавить/обновить устройство")
    p_add.add_argument("mac", help="MAC-адрес телефона")
    p_add.add_argument("name", help="Имя устройства")

    subparsers.add_parser("list", help="Показать устройства")
    subparsers.add_parser("run", help="Запустить основной цикл")

    return parser


def main() -> None:
    setup_logging()
    config = Config()

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "add":
        cmd_add(config, args.mac, args.name)
    elif args.command == "list":
        cmd_list(config)
    elif args.command == "run":
        cmd_run(config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()