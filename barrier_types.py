from dataclasses import dataclass
from enum import Enum, auto
from typing import NamedTuple


DeviceRow = tuple[int, str, str, int]
EventRow = tuple[int, str, str, str, str, str]


class BluetoothStatusRow(NamedTuple):
    id: int
    updated_at: str
    status: str
    total_devices: int
    connected_devices: int
    allowed_seen: int
    max_rssi: int | None
    strongest_device: str
    devices_json: str
    raw_output: str
    error: str
    presence_status: str
    missing_count: int
    missing_threshold: int
    min_rssi: int | None
    allowed_present: int


class WifiStatusRow(NamedTuple):
    id: int
    updated_at: str
    status: str
    interface: str
    connected_devices: int
    allowed_seen: int
    max_signal: int | None
    strongest_station: str
    stations_json: str
    raw_output: str
    error: str
    presence_status: str
    missing_count: int
    missing_threshold: int
    min_signal: int | None
    max_inactive_ms: int | None
    allowed_present: int


class PresenceStatus(Enum):
    PRESENT = auto()
    ABSENT = auto()
    SCAN_FAILED = auto()


@dataclass
class State:
    any_device_was_present: bool = False
    missing_count: int = 0
    last_trigger_monotonic: float = 0.0
