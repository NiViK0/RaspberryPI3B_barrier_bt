import os
import shutil
import sqlite3
import json
from contextlib import closing
from datetime import datetime

from barrier_types import BluetoothStatusRow, DeviceRow, EventRow


def normalize_mac(mac: str) -> str:
    return mac.strip().upper()


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
        ensure_column(conn, "bluetooth_status", "presence_status", "TEXT NOT NULL DEFAULT 'unknown'")
        ensure_column(conn, "bluetooth_status", "missing_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "bluetooth_status", "missing_threshold", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "bluetooth_status", "min_rssi", "INTEGER")
        ensure_column(conn, "bluetooth_status", "allowed_present", "INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def add_device(db_path: str, mac: str, name: str) -> None:
    mac = normalize_mac(mac)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO allowed_devices(name, mac, enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(mac) DO UPDATE SET
                name = excluded.name,
                enabled = 1
            """,
            (name.strip(), mac),
        )
        conn.commit()


def list_devices(db_path: str) -> list[DeviceRow]:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            "SELECT id, name, mac, enabled FROM allowed_devices ORDER BY name"
        ).fetchall()


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


def log_event(db_path: str, level: str, source: str, action: str, message: str) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO event_log(level, source, action, message)
            VALUES (?, ?, ?, ?)
            """,
            (level.upper(), source, action, message),
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

    devices_json = row[8] or "[]"
    try:
        devices = json.loads(devices_json)
    except json.JSONDecodeError:
        devices = []

    return {
        "updated_at": row[1],
        "status": row[2],
        "total_devices": row[3],
        "connected_devices": row[4],
        "allowed_seen": row[5],
        "max_rssi": row[6],
        "strongest_device": row[7],
        "devices": devices,
        "raw_output": row[9],
        "error": row[10],
        "presence_status": row[11],
        "missing_count": row[12],
        "missing_threshold": row[13],
        "min_rssi": row[14],
        "allowed_present": bool(row[15]),
    }


def backup_db(db_path: str, backup_dir: str) -> str:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"База не найдена: {db_path}")

    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"barrier-{stamp}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path
