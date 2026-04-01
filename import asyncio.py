import asyncio
import time
import serial
import subprocess

# ================= НАСТРОЙКИ =================
TARGET_MAC = "6C:F7:84:67:CF:E3"
RSSI_THRESHOLD = -70      # чем ближе к 0, тем ближе устройство
COOLDOWN = 15            # защита от повторного срабатывания (сек)
RELAY_PORT = "/dev/ttyUSB0"

# =============================================

last_trigger_time = 0

# Инициализация реле
ser = serial.Serial(RELAY_PORT, 9600, timeout=1)

def relay_on():
    ser.write(b'\xA0\x01\x01\xA2')

def relay_off():
    ser.write(b'\xA0\x01\x00\xA1')

def trigger_relay():
    global last_trigger_time

    now = time.time()
    if now - last_trigger_time < COOLDOWN:
        return

    print(">>> ОТКРЫВАЕМ ШЛАГБАУМ")
    relay_on()
    time.sleep(2)
    relay_off()

    last_trigger_time = now


async def scan_ble():
    """
    Используем hcitool lescan + hcidump для RSSI
    """
    print("Сканирование BLE...")

    # запуск lescan
    lescan = await asyncio.create_subprocess_exec(
        "hcitool", "lescan", "--duplicates",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )

    # слушаем RSSI через hcidump
    hcidump = await asyncio.create_subprocess_exec(
        "hcidump", "--raw",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )

    while True:
        line = await hcidump.stdout.readline()
        if not line:
            continue

        data = line.hex()

        # ищем MAC в пакете
        if TARGET_MAC.lower().replace(":", "") in data:
            # RSSI — последние байты пакета (грубо)
            try:
                rssi = int(data[-2:], 16) - 256
            except:
                continue

            print(f"Найдено устройство RSSI={rssi}")

            if rssi > RSSI_THRESHOLD:
                trigger_relay()


async def main():
    while True:
        try:
            await scan_ble()
        except Exception as e:
            print("Ошибка:", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())