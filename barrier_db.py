import os
import re
import sqlite3
import json
from contextlib import closing
from datetime import datetime

from barrier_types import BluetoothStatusRow, DeviceRow, EventRow, WifiStatusRow


def normalize_mac(mac: str) -> str:
    return mac.strip().upper()


def event_log_limit() -> int:
    value = os.getenv("BARRIER_EVENT_LOG_LIMIT", "2000").strip()
    try:
        limit = int(value)
    except ValueError:
        return 2000
    return max(0, limit)


def init_db(db_path: str) -> None:
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS allowed_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mac TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                action TEXT NOT NULL,
                message TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS open_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source TEXT NOT NULL,
                profile_name TEXT NOT NULL DEFAULT '',
                device_name TEXT NOT NULL DEFAULT '',
                mac TEXT NOT NULL DEFAULT '',
                signal INTEGER,
                decision TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bluetooth_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                total_devices INTEGER NOT NULL DEFAULT 0,
                connected_devices INTEGER NOT NULL DEFAULT 0,
                allowed_seen INTEGER NOT NULL DEFAULT 0,
                max_rssi INTEGER,
                strongest_device TEXT NOT NULL DEFAULT '',
                devices_json TEXT NOT NULL DEFAULT '[]',
                raw_output TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                presence_status TEXT NOT NULL DEFAULT 'unknown',
                missing_count INTEGER NOT NULL DEFAULT 0,
                missing_threshold INTEGER NOT NULL DEFAULT 0,
                min_rssi INTEGER,
                allowed_present INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wifi_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                interface TEXT NOT NULL DEFAULT '',
                connected_devices INTEGER NOT NULL DEFAULT 0,
                allowed_seen INTEGER NOT NULL DEFAULT 0,
                max_signal INTEGER,
                strongest_station TEXT NOT NULL DEFAULT '',
                stations_json TEXT NOT NULL DEFAULT '[]',
                raw_output TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                presence_status TEXT NOT NULL DEFAULT 'unknown',
                missing_count INTEGER NOT NULL DEFAULT 0,
                missing_threshold INTEGER NOT NULL DEFAULT 0,
                min_signal INTEGER,
                max_inactive_ms INTEGER,
                allowed_present INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        ensure_column(conn, "allowed_devices", "profile_name", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "allowed_devices", "device_type", "TEXT NOT NULL DEFAULT 'unknown'")
        conn.execute(
            """
            UPDATE allowed_devices
            SET profile_name = name
            WHERE profile_name = ''
            """
        )
        ensure_column(conn, "open_events", "profile_name", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "open_events", "device_name", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "open_events", "signal", "INTEGER")
        ensure_column(conn, "bluetooth_status", "presence_status", "TEXT NOT NULL DEFAULT 'unknown'")
        ensure_column(conn, "bluetooth_status", "missing_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "bluetooth_status", "missing_threshold", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "bluetooth_status", "min_rssi", "INTEGER")
        ensure_column(conn, "bluetooth_status", "allowed_present", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "wifi_status", "presence_status", "TEXT NOT NULL DEFAULT 'unknown'")
        ensure_column(conn, "wifi_status", "missing_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "wifi_status", "missing_threshold", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "wifi_status", "min_signal", "INTEGER")
        ensure_column(conn, "wifi_status", "max_inactive_ms", "INTEGER")
        ensure_column(conn, "wifi_status", "allowed_present", "INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    _IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
    if not _IDENT_RE.match(table) or not _IDENT_RE.match(column):
        raise ValueError(f"Invalid SQL identifier: table={table!r}, column={column!r}")
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def add_device(
    db_path: str,
    mac: str,
    name: str,
    device_type: str = "unknown",
    profile_name: str | None = None,
) -> None:
    mac = normalize_mac(mac)
    clean_name = name.strip() or mac
    clean_profile = (profile_name or clean_name).strip() or clean_name
    clean_type = device_type.strip().lower() or "unknown"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO allowed_devices(name, mac, enabled, profile_name, device_type)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(mac) DO UPDATE SET
                name = excluded.name,
                enabled = 1,
                profile_name = excluded.profile_name,
                device_type = excluded.device_type
            """,
            (clean_name, mac, clean_profile, clean_type),
        )
        conn.commit()


def list_devices(db_path: str) -> list[DeviceRow]:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            "SELECT id, name, mac, enabled FROM allowed_devices ORDER BY name"
        ).fetchall()


def list_devices_detailed(db_path: str) -> list[dict[str, object]]:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, name, mac, enabled, profile_name, device_type
            FROM allowed_devices
            ORDER BY profile_name, name
            """
        ).fetchall()
    return [
        {
            "id": row[0],
            "name": row[1],
            "mac": normalize_mac(row[2]),
            "enabled": bool(row[3]),
            "profile_name": row[4] or row[1],
            "device_type": row[5] or "unknown",
        }
        for row in rows
    ]


def set_device_enabled(db_path: str, mac: str, enabled: bool) -> bool:
    mac = normalize_mac(mac)
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute(
            "UPDATE allowed_devices SET enabled = ? WHERE mac = ?",
            (1 if enabled else 0, mac),
        )
        conn.commit()
    return cur.rowcount > 0


def remove_device(db_path: str, mac: str) -> bool:
    mac = normalize_mac(mac)
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute("DELETE FROM allowed_devices WHERE mac = ?", (mac,))
        conn.commit()
    return cur.rowcount > 0


def get_enabled_macs(db_path: str) -> list[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT mac FROM allowed_devices WHERE enabled = 1"
        ).fetchall()
    return [normalize_mac(row[0]) for row in rows]


def get_enabled_device_map(db_path: str) -> dict[str, dict[str, object]]:
    devices = list_devices_detailed(db_path)
    return {
        str(device["mac"]): device
        for device in devices
        if device["enabled"]
    }


def log_event(db_path: str, level: str, source: str, action: str, message: str) -> None:
    limit = event_log_limit()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO event_log(level, source, action, message)
            VALUES (?, ?, ?, ?)
            """,
            (level.upper(), source, action, message),
        )
        if limit > 0:
            conn.execute(
                """
                DELETE FROM event_log
                WHERE id NOT IN (
                    SELECT id FROM event_log ORDER BY id DESC LIMIT ?
                )
                """,
                (limit,),
            )
        conn.commit()


def recent_events(db_path: str, limit: int = 20) -> list[EventRow]:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            """
            SELECT id, created_at, level, source, action, message
            FROM event_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def log_open_event(
    db_path: str,
    source: str,
    profile_name: str,
    device_name: str,
    mac: str,
    signal: int | None,
    decision: str,
    message: str = "",
) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO open_events(source, profile_name, device_name, mac, signal, decision, message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                profile_name,
                device_name,
                normalize_mac(mac) if mac else "",
                signal,
                decision,
                message,
            ),
        )
        conn.commit()


def recent_open_events(db_path: str, limit: int = 50) -> list[dict[str, object]]:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, source, profile_name, device_name, mac, signal, decision, message
            FROM open_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": row[0],
            "created_at": row[1],
            "source": row[2],
            "profile_name": row[3],
            "device_name": row[4],
            "mac": row[5],
            "signal": row[6],
            "decision": row[7],
            "message": row[8],
        }
        for row in rows
    ]


def device_counts(db_path: str) -> tuple[int, int]:
    with closing(sqlite3.connect(db_path)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM allowed_devices").fetchone()[0]
        enabled = conn.execute(
            "SELECT COUNT(*) FROM allowed_devices WHERE enabled = 1"
        ).fetchone()[0]
    return total, enabled


def latest_event_for_action(db_path: str, action: str) -> EventRow | None:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            """
            SELECT id, created_at, level, source, action, message
            FROM event_log
            WHERE action = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (action,),
        ).fetchone()


def save_bluetooth_status(
    db_path: str,
    status: str,
    total_devices: int,
    connected_devices: int,
    allowed_seen: int,
    max_rssi: int | None,
    strongest_device: str,
    devices: list[dict[str, object]],
    raw_output: str,
    error: str = "",
    presence_status: str = "unknown",
    missing_count: int = 0,
    missing_threshold: int = 0,
    min_rssi: int | None = None,
    allowed_present: bool = False,
) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO bluetooth_status(
                id, updated_at, status, total_devices, connected_devices,
                allowed_seen, max_rssi, strongest_device, devices_json, raw_output, error,
                presence_status, missing_count, missing_threshold, min_rssi, allowed_present
            )
            VALUES (1, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at = excluded.updated_at,
                status = excluded.status,
                total_devices = excluded.total_devices,
                connected_devices = excluded.connected_devices,
                allowed_seen = excluded.allowed_seen,
                max_rssi = excluded.max_rssi,
                strongest_device = excluded.strongest_device,
                devices_json = excluded.devices_json,
                raw_output = excluded.raw_output,
                error = excluded.error,
                presence_status = excluded.presence_status,
                missing_count = excluded.missing_count,
                missing_threshold = excluded.missing_threshold,
                min_rssi = excluded.min_rssi,
                allowed_present = excluded.allowed_present
            """,
            (
                status,
                total_devices,
                connected_devices,
                allowed_seen,
                max_rssi,
                strongest_device,
                json.dumps(devices, ensure_ascii=False),
                raw_output,
                error,
                presence_status,
                missing_count,
                missing_threshold,
                min_rssi,
                1 if allowed_present else 0,
            ),
        )
        conn.commit()


def latest_bluetooth_status(db_path: str) -> dict[str, object] | None:
    with closing(sqlite3.connect(db_path)) as conn:
        row: BluetoothStatusRow | None = conn.execute(
            """
            SELECT id, updated_at, status, total_devices, connected_devices,
                   allowed_seen, max_rssi, strongest_device, devices_json, raw_output, error,
                   presence_status, missing_count, missing_threshold, min_rssi, allowed_present
            FROM bluetooth_status
            WHERE id = 1
            """
        ).fetchone()

    if row is None:
        return None

    row = BluetoothStatusRow(*row)
    try:
        devices = json.loads(row.devices_json or "[]")
    except json.JSONDecodeError:
        devices = []

    return {
        "updated_at": row.updated_at,
        "status": row.status,
        "total_devices": row.total_devices,
        "connected_devices": row.connected_devices,
        "allowed_seen": row.allowed_seen,
        "max_rssi": row.max_rssi,
        "strongest_device": row.strongest_device,
        "devices": devices,
        "raw_output": row.raw_output,
        "error": row.error,
        "presence_status": row.presence_status,
        "missing_count": row.missing_count,
        "missing_threshold": row.missing_threshold,
        "min_rssi": row.min_rssi,
        "allowed_present": bool(row.allowed_present),
    }


def save_wifi_status(
    db_path: str,
    status: str,
    interface: str,
    connected_devices: int,
    allowed_seen: int,
    max_signal: int | None,
    strongest_station: str,
    stations: list[dict[str, object]],
    raw_output: str,
    error: str = "",
    presence_status: str = "unknown",
    missing_count: int = 0,
    missing_threshold: int = 0,
    min_signal: int | None = None,
    max_inactive_ms: int | None = None,
    allowed_present: bool = False,
) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO wifi_status(
                id, updated_at, status, interface, connected_devices,
                allowed_seen, max_signal, strongest_station, stations_json, raw_output, error,
                presence_status, missing_count, missing_threshold, min_signal,
                max_inactive_ms, allowed_present
            )
            VALUES (1, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at = excluded.updated_at,
                status = excluded.status,
                interface = excluded.interface,
                connected_devices = excluded.connected_devices,
                allowed_seen = excluded.allowed_seen,
                max_signal = excluded.max_signal,
                strongest_station = excluded.strongest_station,
                stations_json = excluded.stations_json,
                raw_output = excluded.raw_output,
                error = excluded.error,
                presence_status = excluded.presence_status,
                missing_count = excluded.missing_count,
                missing_threshold = excluded.missing_threshold,
                min_signal = excluded.min_signal,
                max_inactive_ms = excluded.max_inactive_ms,
                allowed_present = excluded.allowed_present
            """,
            (
                status,
                interface,
                connected_devices,
                allowed_seen,
                max_signal,
                strongest_station,
                json.dumps(stations, ensure_ascii=False),
                raw_output,
                error,
                presence_status,
                missing_count,
                missing_threshold,
                min_signal,
                max_inactive_ms,
                1 if allowed_present else 0,
            ),
        )
        conn.commit()


def latest_wifi_status(db_path: str) -> dict[str, object] | None:
    with closing(sqlite3.connect(db_path)) as conn:
        row: WifiStatusRow | None = conn.execute(
            """
            SELECT id, updated_at, status, interface, connected_devices,
                   allowed_seen, max_signal, strongest_station, stations_json, raw_output, error,
                   presence_status, missing_count, missing_threshold, min_signal,
                   max_inactive_ms, allowed_present
            FROM wifi_status
            WHERE id = 1
            """
        ).fetchone()

    if row is None:
        return None

    row = WifiStatusRow(*row)
    try:
        stations = json.loads(row.stations_json or "[]")
    except json.JSONDecodeError:
        stations = []

    return {
        "updated_at": row.updated_at,
        "status": row.status,
        "interface": row.interface,
        "connected_devices": row.connected_devices,
        "allowed_seen": row.allowed_seen,
        "max_signal": row.max_signal,
        "strongest_station": row.strongest_station,
        "stations": stations,
        "raw_output": row.raw_output,
        "error": row.error,
        "presence_status": row.presence_status,
        "missing_count": row.missing_count,
        "missing_threshold": row.missing_threshold,
        "min_signal": row.min_signal,
        "max_inactive_ms": row.max_inactive_ms,
        "allowed_present": bool(row.allowed_present),
    }


def backup_db(db_path: str, backup_dir: str) -> str:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"База не найдена: {db_path}")

    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"barrier-{stamp}.db")
    with closing(sqlite3.connect(db_path)) as src, \
         closing(sqlite3.connect(backup_path)) as dst:
        src.backup(dst)
    return backup_path
