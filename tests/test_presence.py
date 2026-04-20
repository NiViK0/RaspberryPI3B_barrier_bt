import unittest

from barrier_config import Config
from barrier_db import normalize_mac
from barrier_presence import detect_any_target_presence, process_presence, validate_mac
from barrier_types import PresenceStatus, State


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

    def test_process_presence_opens_once_and_closes_after_threshold(self) -> None:
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

        self.assertEqual(actions, ["open", "close"])
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


if __name__ == "__main__":
    unittest.main()
