import unittest
import tempfile
import os
import gc
import time

from barrier_bluetooth import apply_device_info, detect_allowed_presence_from_details, parse_devices_output
from barrier_config import Config
from barrier_db import init_db, latest_bluetooth_status, normalize_mac, save_bluetooth_status
from barrier_presence import detect_any_target_presence, process_presence, validate_mac
from barrier_types import PresenceStatus, State


def remove_if_unlocked(path: str) -> None:
    for _ in range(5):
        try:
            if os.path.exists(path):
                os.remove(path)
            return
        except PermissionError:
            gc.collect()
            time.sleep(0.05)


def make_config() -> Config:
    return Config(
        db_path=":memory:",
        barrier_script="barrier_service.py",
        backup_dir="backups",
        relay_port="dry-run",
        relay_baudrate=9600,
        dry_run=True,
        scan_time=1,
        check_interval=1,
        cooldown=0,
        pulse_time=0,
        missing_threshold=2,
        min_rssi=None,
        relay_on_cmd=b"on",
        relay_off_cmd=b"off",
        host="127.0.0.1",
        port=8080,
        panel_password="",
        flask_secret_key="test",
    )


class PresenceTests(unittest.TestCase):
    def test_normalize_and_validate_mac(self) -> None:
        self.assertEqual(normalize_mac(" aa:bb:cc:dd:ee:ff "), "AA:BB:CC:DD:EE:FF")
        self.assertTrue(validate_mac("aa:bb:cc:dd:ee:ff"))
        self.assertFalse(validate_mac("aa:bb:cc"))

    def test_detect_any_target_presence(self) -> None:
        output = "Device AA:BB:CC:DD:EE:FF Phone"
        status = detect_any_target_presence(output, ["aa:bb:cc:dd:ee:ff"])
        self.assertEqual(status, PresenceStatus.PRESENT)

    def test_process_presence_opens_once_and_clears_presence_after_threshold(self) -> None:
        config = make_config()
        state = State()
        actions: list[str] = []

        def trigger(action: str) -> bool:
            actions.append(action)
            return True

        process_presence(PresenceStatus.PRESENT, "Device found", config, state, trigger)
        process_presence(PresenceStatus.PRESENT, "Device found", config, state, trigger)
        process_presence(PresenceStatus.ABSENT, "", config, state, trigger)
        process_presence(PresenceStatus.ABSENT, "", config, state, trigger)

        self.assertEqual(actions, ["open"])
        self.assertFalse(state.any_device_was_present)
        self.assertEqual(state.missing_count, 0)

    def test_scan_failed_does_not_change_state(self) -> None:
        config = make_config()
        state = State(any_device_was_present=True, missing_count=1)
        actions: list[str] = []

        process_presence(PresenceStatus.SCAN_FAILED, "", config, state, actions.append)

        self.assertTrue(state.any_device_was_present)
        self.assertEqual(state.missing_count, 1)
        self.assertEqual(actions, [])

    def test_parse_bluetooth_devices_and_info(self) -> None:
        devices = parse_devices_output("Device AA:BB:CC:DD:EE:FF Phone\nDevice 11:22:33:44:55:66")
        self.assertEqual(devices[0]["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(devices[0]["name"], "Phone")
        self.assertEqual(devices[1]["name"], "11:22:33:44:55:66")

        apply_device_info(
            devices[0],
            "Name: Driver Phone\nConnected: yes\nRSSI: -58",
        )
        self.assertEqual(devices[0]["name"], "Driver Phone")
        self.assertTrue(devices[0]["connected"])
        self.assertEqual(devices[0]["rssi"], -58)

    def test_detect_presence_respects_rssi_threshold(self) -> None:
        devices = [
            {"mac": "AA:BB:CC:DD:EE:FF", "allowed": True, "rssi": -90},
            {"mac": "11:22:33:44:55:66", "allowed": False, "rssi": -40},
        ]
        self.assertEqual(detect_allowed_presence_from_details(devices), PresenceStatus.PRESENT)
        self.assertEqual(detect_allowed_presence_from_details(devices, -80), PresenceStatus.ABSENT)
        self.assertEqual(detect_allowed_presence_from_details(devices, -95), PresenceStatus.PRESENT)

    def test_bluetooth_status_roundtrip(self) -> None:
        config = make_config()
        db_file = tempfile.NamedTemporaryFile(suffix=".db", dir=".", delete=False)
        db_file.close()
        self.addCleanup(remove_if_unlocked, db_file.name)
        config = Config(
            **{**config.__dict__, "db_path": db_file.name}
        )
        init_db(config.db_path)
        save_bluetooth_status(
            config.db_path,
            "ok",
            1,
            1,
            1,
            -58,
            "Phone (AA:BB:CC:DD:EE:FF)",
            [{"mac": "AA:BB:CC:DD:EE:FF", "name": "Phone", "connected": True, "rssi": -58, "allowed": True}],
            "Device AA:BB:CC:DD:EE:FF Phone",
            presence_status="present",
            missing_count=0,
            missing_threshold=2,
            min_rssi=-80,
            allowed_present=True,
        )

        status = latest_bluetooth_status(config.db_path)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status["total_devices"], 1)
        self.assertEqual(status["connected_devices"], 1)
        self.assertEqual(status["max_rssi"], -58)
        self.assertEqual(status["presence_status"], "present")
        self.assertEqual(status["min_rssi"], -80)
        self.assertTrue(status["allowed_present"])


if __name__ == "__main__":
    unittest.main()
