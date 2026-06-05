import unittest
import tempfile
import os
import gc
import time

from barrier_bluetooth import apply_device_info, detect_allowed_presence_from_details, parse_devices_output
from barrier_config import Config
from barrier_db import add_device, get_enabled_device_map, init_db, latest_bluetooth_status, latest_wifi_status, list_devices_detailed, log_event, log_open_event, normalize_mac, recent_events, recent_open_events, save_bluetooth_status, save_wifi_status
from barrier_presence import detect_any_target_presence, process_presence, validate_mac
from barrier_service import select_trigger_context
from barrier_types import PresenceStatus, State
from barrier_wifi import detect_allowed_wifi_presence, parse_iw_station_dump


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
        bluetooth_enabled=True,
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
        wifi_auto_open=False,
        wifi_interface="wlan0",
        wifi_min_signal=None,
        wifi_max_inactive_ms=60000,
        wifi_leases_path="/var/lib/misc/dnsmasq.leases",
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

    def test_parse_wifi_stations_and_presence_filters(self) -> None:
        output = """
Station aa:bb:cc:dd:ee:ff (on wlan0)
        inactive time:  120 ms
        rx bytes:       1234
        tx bytes:       5678
        signal:         -54 dBm
        authorized:     yes
Station 11:22:33:44:55:66 (on wlan0)
        inactive time:  90000 ms
        signal:         -80 dBm
"""
        stations = parse_iw_station_dump(output)
        self.assertEqual(stations[0]["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(stations[0]["signal"], -54)
        self.assertEqual(stations[0]["inactive_ms"], 120)

        stations[0]["allowed"] = True
        stations[1]["allowed"] = True
        self.assertEqual(detect_allowed_wifi_presence(stations, -70, 60000), PresenceStatus.PRESENT)
        self.assertEqual(detect_allowed_wifi_presence(stations, -50, 60000), PresenceStatus.ABSENT)
        self.assertEqual(detect_allowed_wifi_presence([stations[1]], -90, 60000), PresenceStatus.ABSENT)

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

    def test_wifi_status_roundtrip(self) -> None:
        config = make_config()
        db_file = tempfile.NamedTemporaryFile(suffix=".db", dir=".", delete=False)
        db_file.close()
        self.addCleanup(remove_if_unlocked, db_file.name)
        config = Config(
            **{**config.__dict__, "db_path": db_file.name}
        )
        init_db(config.db_path)
        save_wifi_status(
            config.db_path,
            "ok",
            "wlan0",
            1,
            1,
            -54,
            "Phone (AA:BB:CC:DD:EE:FF)",
            [
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Phone",
                    "signal": -54,
                    "inactive_ms": 120,
                    "allowed": True,
                    "active": True,
                }
            ],
            "Station AA:BB:CC:DD:EE:FF (on wlan0)",
            presence_status="present",
            missing_count=0,
            missing_threshold=2,
            min_signal=-70,
            max_inactive_ms=60000,
            allowed_present=True,
        )

        status = latest_wifi_status(config.db_path)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status["interface"], "wlan0")
        self.assertEqual(status["connected_devices"], 1)
        self.assertEqual(status["max_signal"], -54)
        self.assertEqual(status["presence_status"], "present")
        self.assertEqual(status["min_signal"], -70)
        self.assertTrue(status["allowed_present"])

    def test_event_log_limit_prunes_old_events(self) -> None:
        old_limit = os.environ.get("BARRIER_EVENT_LOG_LIMIT")
        os.environ["BARRIER_EVENT_LOG_LIMIT"] = "2"
        self.addCleanup(
            lambda: os.environ.pop("BARRIER_EVENT_LOG_LIMIT", None)
            if old_limit is None
            else os.environ.__setitem__("BARRIER_EVENT_LOG_LIMIT", old_limit)
        )

        db_file = tempfile.NamedTemporaryFile(suffix=".db", dir=".", delete=False)
        db_file.close()
        self.addCleanup(remove_if_unlocked, db_file.name)
        init_db(db_file.name)

        log_event(db_file.name, "INFO", "test", "one", "one")
        log_event(db_file.name, "INFO", "test", "two", "two")
        log_event(db_file.name, "INFO", "test", "three", "three")

        events = recent_events(db_file.name, 10)
        self.assertEqual([event[4] for event in events], ["three", "two"])

    def test_device_profile_and_open_event_roundtrip(self) -> None:
        db_file = tempfile.NamedTemporaryFile(suffix=".db", dir=".", delete=False)
        db_file.close()
        self.addCleanup(remove_if_unlocked, db_file.name)
        init_db(db_file.name)

        add_device(db_file.name, "aa:bb:cc:dd:ee:ff", "Phone Wi-Fi", "wifi", "Driver Phone")
        devices = list_devices_detailed(db_file.name)

        self.assertEqual(devices[0]["profile_name"], "Driver Phone")
        self.assertEqual(devices[0]["device_type"], "wifi")

        log_open_event(
            db_file.name,
            "wifi",
            "Driver Phone",
            "Phone Wi-Fi",
            "AA:BB:CC:DD:EE:FF",
            -61,
            "opened",
            "auto-open via wifi",
        )
        events = recent_open_events(db_file.name, 5)
        self.assertEqual(events[0]["source"], "wifi")
        self.assertEqual(events[0]["profile_name"], "Driver Phone")
        self.assertEqual(events[0]["decision"], "opened")

    def test_select_trigger_context_prefers_wifi_over_ble(self) -> None:
        db_file = tempfile.NamedTemporaryFile(suffix=".db", dir=".", delete=False)
        db_file.close()
        self.addCleanup(remove_if_unlocked, db_file.name)
        init_db(db_file.name)
        add_device(db_file.name, "AA:BB:CC:DD:EE:FF", "Phone Wi-Fi", "wifi", "Driver Phone")
        add_device(db_file.name, "11:22:33:44:55:66", "Phone BLE", "ble", "Driver Phone")

        context = select_trigger_context(
            {
                "stations": [
                    {
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "name": "Phone",
                        "allowed": True,
                        "active": True,
                        "signal": -64,
                    }
                ]
            },
            {
                "devices": [
                    {
                        "mac": "11:22:33:44:55:66",
                        "name": "Phone BLE",
                        "allowed": True,
                        "rssi": -55,
                    }
                ]
            },
            get_enabled_device_map(db_file.name),
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["source"], "wifi")
        self.assertEqual(context["profile_name"], "Driver Phone")


if __name__ == "__main__":
    unittest.main()
