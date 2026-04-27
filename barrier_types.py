from dataclasses import dataclass
from enum import Enum, auto


DeviceRow = tuple[int, str, str, int]
EventRow = tuple[int, str, str, str, str, str]
BluetoothStatusRow = tuple[int, str, str, int, int, int, int | None, str, str, str, str]


class PresenceStatus(Enum):
    PRESENT = auto()
    ABSENT = auto()
    SCAN_FAILED = auto()


@dataclass
class State:
    any_device_was_present: bool = False
    missing_count: int = 0
    last_trigger_monotonic: float = 0.0
