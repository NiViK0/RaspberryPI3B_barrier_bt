import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    db_path: str
    barrier_script: str
    backup_dir: str

    relay_port: str
    relay_baudrate: int
    dry_run: bool

    scan_time: int
    check_interval: int
    cooldown: int
    pulse_time: int
    missing_threshold: int

    relay_on_cmd: bytes
    relay_off_cmd: bytes

    host: str
    port: int
    panel_password: str
    flask_secret_key: str


def load_config() -> Config:
    return Config(
        db_path=os.getenv("BARRIER_DB_PATH", "/opt/barrier/barrier.db"),
        barrier_script=os.getenv("BARRIER_SCRIPT", "/opt/barrier/barrier_service.py"),
        backup_dir=os.getenv("BARRIER_BACKUP_DIR", "/opt/barrier/backups"),
        relay_port=os.getenv("BARRIER_RELAY_PORT", "/dev/ttyUSB0"),
        relay_baudrate=_env_int("BARRIER_RELAY_BAUDRATE", 9600),
        dry_run=_env_bool("BARRIER_DRY_RUN", False),
        scan_time=_env_int("BARRIER_SCAN_TIME", 8),
        check_interval=_env_int("BARRIER_CHECK_INTERVAL", 2),
        cooldown=_env_int("BARRIER_COOLDOWN", 15),
        pulse_time=_env_int("BARRIER_PULSE_TIME", 2),
        missing_threshold=_env_int("BARRIER_MISSING_THRESHOLD", 3),
        relay_on_cmd=b"\xA0\x01\x01\xA2",
        relay_off_cmd=b"\xA0\x01\x00\xA1",
        host=os.getenv("BARRIER_PANEL_HOST", "0.0.0.0"),
        port=_env_int("BARRIER_PANEL_PORT", 8080),
        panel_password=os.getenv("BARRIER_PANEL_PASSWORD", ""),
        flask_secret_key=os.getenv("BARRIER_FLASK_SECRET_KEY", "barrier-panel-local-secret"),
    )
