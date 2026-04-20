import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum, auto

import serial
from serial import SerialException


# =========================
# НАСТРОЙКИ
# =========================

@dataclass(frozen=True)
class Config:
    target_mac: str = "AA:BB:CC:DD:EE:FF"   # MAC телефона
    relay_port: str = "/dev/ttyUSB0"
    relay_baudrate: int = 9600

    scan_time: int = 8          # сколько секунд сканировать Bluetooth
    check_interval: int = 2     # пауза между циклами
    cooldown: int = 15          # защита от повторного импульса на реле
    pulse_time: int = 2         # сколько держать реле включённым
    missing_threshold: int = 3  # сколько циклов подряд телефон должен отсутствовать

    # Команды для LCUS-1
    relay_on_cmd: bytes = b"\xA0\x01\x01\xA2"
    relay_off_cmd: bytes = b"\xA0\x01\x00\xA1"


class PresenceStatus(Enum):
    PRESENT = auto()
    ABSENT = auto()
    SCAN_FAILED = auto()


@dataclass
class State:
    device_was_present: bool = False
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


def run_cmd(cmd: list[str], timeout: int = 15, input_text: str | None = None) -> tuple[bool, str]:
    """
    Выполняет команду и возвращает:
    (успех, stdout+stderr)
    """
    try:
        result = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        output = (result.stdout or "") + (result.stderr or "")
        success = (result.returncode == 0)
        if not success:
            logging.warning(
                "Команда завершилась с кодом %s: %s",
                result.returncode,
                " ".join(cmd),
            )
        return success, output.strip()

    except subprocess.TimeoutExpired:
        logging.warning("Таймаут выполнения команды: %s", " ".join(cmd))
        return False, ""

    except Exception:
        logging.exception("Ошибка запуска команды: %s", " ".join(cmd))
        return False, ""


def bluetooth_power_on() -> bool:
    success, output = run_cmd(
        ["bluetoothctl"],
        timeout=10,
        input_text="power on\nagent on\ndefault-agent\nquit\n",
    )
    if success:
        logging.info("Bluetooth инициализирован")
    else:
        logging.warning("Не удалось корректно инициализировать Bluetooth: %s", output)
    return success


def scan_once(scan_time: int) -> tuple[PresenceStatus, str]:
    """
    Включает BLE-сканирование на несколько секунд,
    затем получает список найденных устройств.

    Возвращает:
      - PresenceStatus.PRESENT / ABSENT / SCAN_FAILED
      - сырой вывод bluetoothctl devices
    """
    scan_ok, scan_output = run_cmd(
        ["timeout", f"{scan_time}s", "bluetoothctl", "scan", "on"],
        timeout=scan_time + 5,
    )

    # timeout обычно завершает процесс не кодом 0 — это для нас не критично.
    # Поэтому отдельно пробуем получить список устройств.
    devices_ok, devices_output = run_cmd(["bluetoothctl", "devices"], timeout=10)

    if not devices_ok:
        logging.warning("Не удалось получить список Bluetooth-устройств")
        if scan_output:
            logging.warning("Вывод scan on: %s", scan_output)
        return PresenceStatus.SCAN_FAILED, ""

    return PresenceStatus.ABSENT, devices_output


def detect_target_presence(devices_output: str, target_mac: str) -> PresenceStatus:
    if target_mac.upper() in devices_output.upper():
        return PresenceStatus.PRESENT
    return PresenceStatus.ABSENT


def pulse_relay(ser: serial.Serial, config: Config) -> None:
    """
    Даёт импульс на реле и гарантирует попытку выключить его даже при ошибке.
    """
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
    """
    Даёт импульс на реле для действия шлагбаума.
    action: 'open' или 'close' — используется только для логов.
    """
    now = time.monotonic()
    if now - state.last_trigger_monotonic < config.cooldown:
        logging.info("Импульс '%s' заблокирован cooldown-ом", action)
        return False

    if action == "open":
        logging.info(">>> Телефон найден, открываем шлагбаум")
    elif action == "close":
        logging.info("<<< Телефон удалился, закрываем шлагбаум")
    else:
        logging.info("*** Выполняем действие: %s", action)

    try:
        pulse_relay(ser, config)
    except SerialException:
        logging.exception("Ошибка работы с реле при действии '%s'", action)
        return False
    except Exception:
        logging.exception("Неожиданная ошибка при срабатывании реле '%s'", action)
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
    """
    Обновляет состояние автомата и при необходимости триггерит шлагбаум.
    """

    if devices_output.strip():
        logging.info("Найденные устройства:\n%s", devices_output.strip())
    else:
        logging.info("Список устройств пуст")

    if presence == PresenceStatus.SCAN_FAILED:
        logging.warning("Сканирование не удалось, состояние присутствия не меняем")
        return

    if presence == PresenceStatus.PRESENT:
        state.missing_count = 0

        if not state.device_was_present:
            triggered = trigger_barrier(ser, config, state, "open")
            if triggered:
                state.device_was_present = True
        else:
            logging.info("Телефон всё ещё в зоне действия")
        return

    # presence == ABSENT
    if state.device_was_present:
        state.missing_count += 1
        logging.info(
            "Телефон не найден (%s/%s), ждём подтверждение удаления",
            state.missing_count,
            config.missing_threshold,
        )

        if state.missing_count >= config.missing_threshold:
            triggered = trigger_barrier(ser, config, state, "close")
            if triggered:
                state.device_was_present = False
                state.missing_count = 0
    else:
        logging.info("Целевой телефон не найден")
        state.missing_count = 0


def main() -> None:
    setup_logging()

    config = Config()
    state = State()

    if config.target_mac == "AA:BB:CC:DD:EE:FF":
        logging.error("Укажи реальный TARGET_MAC в конфиге")
        sys.exit(1)

    if not validate_mac(config.target_mac):
        logging.error("Некорректный формат TARGET_MAC: %s", config.target_mac)
        sys.exit(1)

    logging.info("Инициализация Bluetooth...")
    bluetooth_power_on()

    logging.info("Открытие порта реле: %s", config.relay_port)

    try:
        with serial.Serial(config.relay_port, config.relay_baudrate, timeout=1) as ser:
            while True:
                logging.info("Сканирование BLE...")

                base_status, devices_output = scan_once(config.scan_time)

                if base_status == PresenceStatus.SCAN_FAILED:
                    process_presence(base_status, devices_output, ser, config, state)
                else:
                    actual_presence = detect_target_presence(devices_output, config.target_mac)
                    process_presence(actual_presence, devices_output, ser, config, state)

                time.sleep(config.check_interval)

    except SerialException:
        logging.exception("Не удалось открыть или использовать порт реле: %s", config.relay_port)
        sys.exit(1)
    except KeyboardInterrupt:
        logging.info("Остановлено пользователем")
    except Exception:
        logging.exception("Критическая ошибка в основном цикле")
        sys.exit(1)


if __name__ == "__main__":
    main()