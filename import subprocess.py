import subprocess
import time
import serial

TARGET_MAC = "6C:F7:84:67:CF:E3"

ser = serial.Serial('/dev/ttyUSB0', 9600)

def is_device_near():
    try:
        output = subprocess.check_output(["hcitool", "name", TARGET_MAC])
        return TARGET_MAC in output.decode()
    except:
        return False

while True:
    if is_device_near():
        print("Телефон рядом → открываем шлагбаум")

        ser.write(b'\xA0\x01\x01\xA2')  # ON
        time.sleep(3)
        ser.write(b'\xA0\x01\x00\xA1')  # OFF

        time.sleep(10)  # защита от повторного срабатывания
    else:
        print("Телефон не найден")

    time.sleep(2)