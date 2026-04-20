import subprocess
import time
import serial
import sys

# =========================
# НАСТРОЙКИ
# =========================
TARGET_MAC = "AA:BB:CC:DD:EE:FF"   # сюда MAC телефона
RELAY_PORT = "/dev/ttyUSB0"
RELAY_BAUDRATE = 9600

SCAN_TIME = 8          # сколько секунд сканировать Bluetooth
CHECK_INTERVAL = 2     # пауза между циклами
COOLDOWN = 15          # защита от повторного открытия
OPEN_TIME = 2          # сколько держать реле включенным

# Команды для LCUS-1
RELAY_ON_CMD = b'\xA0\x01\x01\xA2'
RELAY_OFF_CMD = b'\xA0\x01\x00\xA1'

last_trigger_time = 0


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


def bluetooth_power_on() -> None:
    subprocess.run(
        ["bluetoothctl"],
        input="power on\nagent on\ndefault-agent\nquit\n",
        text=True,
        capture_output=True
    )


def scan_once(scan_time: int) -> str:
    """
    На несколько секунд включает scan on, затем завершает.
    После этого запрашиваем список найденных устройств.
    """
    cmd_input = f"scan on\nmenu scan\ntransport le\nback\nsleep {scan_time}\nscan off\ndevices\nquit\n"

    # В bluetoothctl нет встроенной sleep-команды, поэтому такой путь не работает.
    # Используем shell-обертку с timeout.
    shell_cmd = f"timeout {scan_time}s bluetoothctl scan on"
    subprocess.run(shell_cmd, shell=True, capture_output=True, text=True)

    devices_output = run_cmd(["bluetoothctl", "devices"], timeout=10)
    return devices_output


def is_target_present(devices_output: str, target_mac: str) -> bool:
    return target_mac.upper() in devices_output.upper()


def open_barrier(ser: serial.Serial) -> None:
    global last_trigger_time

    now = time.time()
    if now - last_trigger_time < COOLDOWN:
        log("Повторное срабатывание заблокировано cooldown-ом")
        return

    log(">>> Телефон найден, открываем шлагбаум")
    ser.write(RELAY_ON_CMD)
    ser.flush()
    time.sleep(OPEN_TIME)
    ser.write(RELAY_OFF_CMD)
    ser.flush()

    last_trigger_time = now


def main() -> None:
    global TARGET_MAC

    if TARGET_MAC == "AA:BB:CC:DD:EE:FF":
        log("Ошибка: укажи TARGET_MAC в файле barrier_py.py")
        sys.exit(1)

    log("Инициализация Bluetooth...")
    bluetooth_power_on()

    log(f"Открытие порта реле: {RELAY_PORT}")
    ser = serial.Serial(RELAY_PORT, RELAY_BAUDRATE, timeout=1)

    try:
        while True:
            log("Сканирование BLE...")
            devices_output = scan_once(SCAN_TIME)

            if devices_output.strip():
                log("Найденные устройства:")
                log(devices_output.strip())
            else:
                log("Список устройств пуст")

            if is_target_present(devices_output, TARGET_MAC):
                open_barrier(ser)
            else:
                log("Целевой телефон не найден")

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log("Остановлено пользователем")
    finally:
        ser.close()


if __name__ == "__main__":
    main()