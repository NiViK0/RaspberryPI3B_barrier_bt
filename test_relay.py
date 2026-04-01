import serial
import time

ser = serial.Serial('/dev/ttyUSB0', 9600)

ser.write(b'\xA0\x01\x01\xA2')
time.sleep(2)
ser.write(b'\xA0\x01\x00\xA1')

ser.close()
