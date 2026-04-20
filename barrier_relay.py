import glob
import logging
import time

import serial
from serial import SerialException

from barrier_config import Config

SERIAL_PORT_PATTERNS = ("/dev/ttyUSB*", "/dev/ttyACM*")


def detect_relay_port() -> str | None:
    for pattern in SERIAL_PORT_PATTERNS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    return None


def resolve_relay_port(config: Config) -> str:
    if config.relay_port.lower() != "auto":
        return config.relay_port

    port = detect_relay_port()
    if port is None:
        raise SerialException("Не найден serial-порт реле: /dev/ttyUSB* или /dev/ttyACM*")
    return port


class RelayController:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.port = ""
        self.ser: serial.Serial | None = None

    def __enter__(self) -> "RelayController":
        if self.config.dry_run:
            logging.warning("BARRIER_DRY_RUN включён: реле не будет активироваться")
            return self

        self.port = resolve_relay_port(self.config)
        self.ser = serial.Serial(self.port, self.config.relay_baudrate, timeout=1)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.ser is not None:
            self.ser.close()

    def pulse(self) -> None:
        if self.config.dry_run:
            logging.info("dry-run: тестовый импульс реле пропущен")
            return

        if self.ser is None:
            raise SerialException("Serial-порт реле не открыт")

        self.ser.write(self.config.relay_on_cmd)
        self.ser.flush()
        try:
            time.sleep(self.config.pulse_time)
        finally:
            self.ser.write(self.config.relay_off_cmd)
            self.ser.flush()
