import os
import shutil
import sqlite3
from datetime import datetime

from barrier_types import DeviceRow, EventRow


def normalize_mac(mac: str) -> str:
    return mac.strip().upper()


def init_db(db_path: str) -> None:
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
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
        conn.commit()


def add_device(db_path: str, mac: str, name: str) -> None:
    mac = normalize_mac(mac)
    with sqlite3.connect(db_path) as conn:
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
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT id, name, mac, enabled FROM allowed_devices ORDER BY name"
        ).fetchall()


def set_device_enabled(db_path: str, mac: str, enabled: bool) -> bool:
    mac = normalize_mac(mac)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE allowed_devices SET enabled = ? WHERE mac = ?",
            (1 if enabled else 0, mac),
        )
        conn.commit()
    return cur.rowcount > 0


def remove_device(db_path: str, mac: str) -> bool:
    mac = normalize_mac(mac)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("DELETE FROM allowed_devices WHERE mac = ?", (mac,))
        conn.commit()
    return cur.rowcount > 0


def get_enabled_macs(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT mac FROM allowed_devices WHERE enabled = 1"
        ).fetchall()
    return [normalize_mac(row[0]) for row in rows]


def log_event(db_path: str, level: str, source: str, action: str, message: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO event_log(level, source, action, message)
            VALUES (?, ?, ?, ?)
            """,
            (level.upper(), source, action, message),
        )
        conn.commit()


def recent_events(db_path: str, limit: int = 20) -> list[EventRow]:
    with sqlite3.connect(db_path) as conn:
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
    with sqlite3.connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM allowed_devices").fetchone()[0]
        enabled = conn.execute(
            "SELECT COUNT(*) FROM allowed_devices WHERE enabled = 1"
        ).fetchone()[0]
    return total, enabled


def latest_event_for_action(db_path: str, action: str) -> EventRow | None:
    with sqlite3.connect(db_path) as conn:
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


def backup_db(db_path: str, backup_dir: str) -> str:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"База не найдена: {db_path}")

    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"barrier-{stamp}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path
