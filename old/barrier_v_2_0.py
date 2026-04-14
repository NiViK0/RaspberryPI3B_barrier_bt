import subprocess
import time
import serial
import sys

# =========================
# НАСТРОЙКИ
# =========================
TARGET_MAC = "AA:BB:CC:DD:EE:FF"   # MAC телефона
RELAY_PORT = "/dev/ttyUSB0"
RELAY_BAUDRATE = 9600

SCAN_TIME = 8          # сколько секунд сканировать Bluetooth
CHECK_INTERVAL = 2     # пауза между циклами
COOLDOWN = 15          # защита от повторного импульса на реле
PULSE_TIME = 2         # сколько держать реле включённым
MISSING_THRESHOLD = 3  # сколько циклов подряд телефон должен отсутствовать,
                       # прежде чем считать, что он действительно уехал

# Команды для LCUS-1
RELAY_ON_CMD = b'\xA0\x01\x01\xA2'
RELAY_OFF_CMD = b'\xA0\x01\x00\xA1'

last_trigger_time = 0.0
device_was_present = False
missing_count = 0


def log(msg: str) -> None:
    print(msg, flush=True)


def run_cmd(cmd: list[str], timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        log(f"Ошибка запуска команды {' '.join(cmd)}: {e}")
        return ""


def bluetooth_power_on() -> None:
    subprocess.run(
        ["bluetoothctl"],
        input="power on\nagent on\ndefault-agent\nquit\n",
        text=True,
        capture_output=True
    )


def scan_once(scan_time: int) -> str:
    """
    Включает BLE-сканирование на несколько секунд,
    затем получает список найденных устройств.
    """
    shell_cmd = f"timeout {scan_time}s bluetoothctl scan on"
    subprocess.run(shell_cmd, shell=True, capture_output=True, text=True)
    return run_cmd(["bluetoothctl", "devices"], timeout=10)


def is_target_present(devices_output: str, target_mac: str) -> bool:
    return target_mac.upper() in devices_output.upper()


def pulse_relay(ser: serial.Serial) -> None:
    ser.write(RELAY_ON_CMD)
    ser.flush()
    time.sleep(PULSE_TIME)
    ser.write(RELAY_OFF_CMD)
    ser.flush()


def trigger_barrier(ser: serial.Serial, action: str) -> bool:
    """
    Даёт импульс на реле для действия шлагбаума.
    action: 'open' или 'close' — только для логов.

    ВАЖНО:
    Если у вашего контроллера разные команды на открытие и закрытие,
    здесь нужно реализовать разные команды. Сейчас отправляется
    одинаковый импульс, как и в исходном коде.
    """
    global last_trigger_time

    now = time.time()
    if now - last_trigger_time < COOLDOWN:
        log(f"Импульс '{action}' заблокирован cooldown-ом")
        return False

    if action == "open":
        log(">>> Телефон найден, открываем шлагбаум")
    elif action == "close":
        log("<<< Телефон удалился, закрываем шлагбаум")
    else:
        log(f"*** Выполняем действие: {action}")

    pulse_relay(ser)
    last_trigger_time = now
    return True


def main() -> None:
    global device_was_present, missing_count

    if TARGET_MAC == "AA:BB:CC:DD:EE:FF":
        log("Ошибка: укажи TARGET_MAC в файле barrier.py")
        sys.exit(1)

    log("Инициализация Bluetooth...")
    bluetooth_power_on()

    log(f"Открытие порта реле: {RELAY_PORT}")
    ser = serial.Serial(RELAY_PORT, RELAY_BAUDRATE, timeout=1)

    try:
        while True:
            log("Сканирование BLE...")
            devices_output = scan_once(SCAN_TIME)
            device_is_present = is_target_present(devices_output, TARGET_MAC)

            if devices_output.strip():
                log("Найденные устройства:")
                log(devices_output.strip())
            else:
                log("Список устройств пуст")

            if device_is_present:
                missing_count = 0

                if not device_was_present:
                    triggered = trigger_barrier(ser, "open")
                    if triggered:
                        device_was_present = True
                else:
                    log("Телефон всё ещё в зоне действия")

            else:
                if device_was_present:
                    missing_count += 1
                    log(
                        f"Телефон не найден ({missing_count}/{MISSING_THRESHOLD}), "
                        "ждём подтверждение удаления"
                    )

                    if missing_count >= MISSING_THRESHOLD:
                        triggered = trigger_barrier(ser, "close")
                        if triggered:
                            device_was_present = False
                            missing_count = 0
                else:
                    log("Целевой телефон не найден")
                    missing_count = 0

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log("Остановлено пользователем")
    finally:
        ser.close()
        log("Порт реле закрыт")


if __name__ == "__main__":
    main()
