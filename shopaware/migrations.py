"""Additive SQLite schema migrations. Existing cameras/evidence remain intact."""
from __future__ import annotations

import sqlite3

MIGRATIONS = {
    1: [
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role='admin'), created_at TEXT NOT NULL)",
        "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires_at REAL NOT NULL)",
        "CREATE INDEX idx_sessions_expiry ON sessions(expires_at)",
        "CREATE TABLE zones (id TEXT PRIMARY KEY, camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE, name TEXT NOT NULL, type TEXT NOT NULL, points_json TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1)",
        "CREATE INDEX idx_zones_camera ON zones(camera_id)",
        "ALTER TABLE incidents ADD COLUMN reviewer TEXT",
        "ALTER TABLE incidents ADD COLUMN review_notes TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE incidents ADD COLUMN reviewed_at TEXT",
        "ALTER TABLE incidents ADD COLUMN media_status TEXT NOT NULL DEFAULT 'pending'",
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)",
    ],
    2: ["ALTER TABLE incidents ADD COLUMN alert_status TEXT NOT NULL DEFAULT 'not_dispatched'"],
}


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute('BEGIN IMMEDIATE')
    try:
        current = conn.execute('PRAGMA user_version').fetchone()[0]
        if current > max(MIGRATIONS):
            raise RuntimeError('Database schema is newer than this application')
        for version, statements in MIGRATIONS.items():
            if version > current:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(f'PRAGMA user_version={version}')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
