import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib
import sys
import time
import serial  # ← новая библиотека для USB-реле

# --- НАСТРОЙКА USB-РЕЛЕ LCUS-1 ---
# Автоматически найдём порт с CH340 (LCUS-1)
def find_lcus_port():
    import glob
    possible_ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
    for port in possible_ports:
        try:
            ser = serial.Serial(port, 9600, timeout=1)
            ser.close()
            return port
        except:
            continue
    return None

LCUS_PORT = find_lcus_port()
if LCUS_PORT is None:
    print("ОШИБКА: LCUS-1 не найден. Проверьте подключение.")
    sys.exit(1)

print(f"LCUS-1 найден на порту {LCUS_PORT}")

def relay_on():
    """Включить реле (замкнуть контакты)"""
    ser = serial.Serial(LCUS_PORT, 9600, timeout=1)
    ser.write(b'\xA0\x01\x01\xA2')  # HEX-команда включения [citation:2][citation:9]
    ser.close()
    print("Реле ВКЛЮЧЕНО")

def relay_off():
    """Выключить реле (разомкнуть контакты)"""
    ser = serial.Serial(LCUS_PORT, 9600, timeout=1)
    ser.write(b'\xA0\x01\x00\xA1')  # HEX-команда выключения [citation:2][citation:9]
    ser.close()
    print("Реле ВЫКЛЮЧЕНО")

# --- GATT UUIDs ---
SERVICE_UUID = '12345678-1234-1234-1234-123456789abc'
CHAR_UUID = 'abcdef01-1234-1234-1234-123456789abc'

# --- Настройка D-Bus и BlueZ ---
BLUEZ_SERVICE_NAME = 'org.bluez'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
LE_ADVERTISEMENT_IFACE = 'org.bluez.LEAdvertisement1'
GATT_SERVICE_IFACE = 'org.bluez.GattService1'
GATT_CHARACTERISTIC_IFACE = 'org.bluez.GattCharacteristic1'

mainloop = None
bus = None

# ---- Исключения D-Bus ----
class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = 'org.freedesktop.DBus.Error.InvalidArgs'

class NotSupportedException(dbus.exceptions.DBusException):
    _dbus_error_name = 'org.bluez.Error.NotSupported'

class NotPermittedException(dbus.exceptions.DBusException):
    _dbus_error_name = 'org.bluez.Error.NotPermitted'

# ---- Сервис ----
class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = '/'
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature='s', out_signature='as')
    def Get(self, interface):
        if interface != GATT_MANAGER_IFACE:
            raise InvalidArgsException()
        return ['org.bluez.GattService1']

    @dbus.service.method(dbus.PROPERTIES_IFACE, out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_MANAGER_IFACE:
            raise InvalidArgsException()
        return {'Services': dbus.Array(self.get_services_path(), signature='o')}

    def get_services_path(self):
        return [service.get_path() for service in self.services]

class Service(dbus.service.Object):
    PATH_BASE = '/org/bluez/example/service'

    def __init__(self, bus, index, uuid, primary):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        dbus.service.Object.__init__(self, bus, self.path)

    def add_characteristic(self, characteristic):
        self.characteristics.append(characteristic)

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature='s', out_signature='as')
    def Get(self, interface):
        if interface != GATT_SERVICE_IFACE:
            raise InvalidArgsException()
        return ['UUID', 'Primary', 'Characteristics']

    @dbus.service.method(dbus.PROPERTIES_IFACE, out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_SERVICE_IFACE:
            raise InvalidArgsException()
        return {
            'UUID': self.uuid,
            'Primary': self.primary,
            'Characteristics': dbus.Array(self.get_characteristic_paths(), signature='o')
        }

    def get_characteristic_paths(self):
        return [c.get_path() for c in self.characteristics]

class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = service.path + '/char' + str(index)
        self.bus = bus
        self.uuid = uuid
        self.service = service
        self.flags = flags
        self.value = [dbus.Byte(b) for b in b'0']
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature='s', out_signature='as')
    def Get(self, interface):
        if interface != GATT_CHARACTERISTIC_IFACE:
            raise InvalidArgsException()
        return ['UUID', 'Service', 'Flags']

    @dbus.service.method(dbus.PROPERTIES_IFACE, out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_CHARACTERISTIC_IFACE:
            raise InvalidArgsException()
        return {
            'UUID': self.uuid,
            'Service': self.service.get_path(),
            'Flags': self.flags
        }

    @dbus.service.method(GATT_CHARACTERISTIC_IFACE, in_signature='ay', out_signature='ay')
    def ReadValue(self, options):
        print('ReadValue called')
        return self.value

    @dbus.service.method(GATT_CHARACTERISTIC_IFACE, in_signature='aya{sv}', out_signature='')
    def WriteValue(self, value, options):
        print('WriteValue called')
        cmd = bytes(value).decode('utf-8').strip()
        print(f"Получена команда: '{cmd}'")

        # --- УПРАВЛЕНИЕ ШЛАГБАУМОМ ЧЕРЕЗ LCUS-1 ---
        if cmd == "OPEN":
            print("ОТКРЫВАЮ ШЛАГБАУМ!")
            relay_on()                # Включаем реле
            time.sleep(0.5)           # Держим 0.5 секунды (можно настроить)
            relay_off()               # Выключаем реле
            self.value = [dbus.Byte(b) for b in b'OPEN_OK']
        elif cmd == "STATUS":
            print("Запрос статуса")
            self.value = [dbus.Byte(b) for b in b'IDLE']
        else:
            print(f"Неизвестная команда: {cmd}")
            self.value = [dbus.Byte(b) for b in b'ERROR']

        self.PropertiesChanged(GATT_CHARACTERISTIC_IFACE, {'Value': self.value}, [])
        return

    @dbus.service.method(GATT_CHARACTERISTIC_IFACE, out_signature='ay')
    def StartNotify(self):
        print('StartNotify')

    @dbus.service.method(GATT_CHARACTERISTIC_IFACE, out_signature='')
    def StopNotify(self):
        print('StopNotify')

    @dbus.service.signal(dbus.PROPERTIES_IFACE, signature='sa{sv}as')
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

# ---- Регистрация ----
def register_app_cb():
    print("GATT application registered")

def register_app_error_cb(error):
    print("Failed to register application: " + str(error))
    mainloop.quit()

# ---- Запуск ----
if __name__ == '__main__':
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    application = Application(bus)
    service = Service(bus, 0, SERVICE_UUID, True)
    application.add_service(service)

    characteristic = Characteristic(bus, 0, CHAR_UUID, ['read', 'write'], service)
    service.add_characteristic(characteristic)

    adapter = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, '/org/bluez/hci0'), GATT_MANAGER_IFACE)
    adapter.RegisterApplication(application.get_path(), {}, reply_handler=register_app_cb, error_handler=register_app_error_cb)

    mainloop = GLib.MainLoop()
    print("BLE GATT сервер запущен. LCUS-1 готов к работе.")
    mainloop.run()