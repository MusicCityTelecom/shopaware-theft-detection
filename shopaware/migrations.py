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
    3: [
        "CREATE TABLE training_sessions (id TEXT PRIMARY KEY, camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE, name TEXT NOT NULL, split TEXT NOT NULL CHECK(split IN ('train','val','test')), created_at TEXT NOT NULL)",
        "CREATE INDEX idx_training_sessions_camera ON training_sessions(camera_id)",
        "CREATE TABLE training_samples (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES training_sessions(id) ON DELETE CASCADE, camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE, captured_at REAL NOT NULL, jpeg BLOB NOT NULL, digest TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, boxes_json TEXT NOT NULL DEFAULT '[]', reviewed INTEGER NOT NULL DEFAULT 0, reviewer TEXT, reviewed_at TEXT, UNIQUE(camera_id,digest))",
        "CREATE INDEX idx_training_samples_session ON training_samples(session_id)",
    ],
    4: [
        "CREATE TABLE users_v4 (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')), enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)), created_at TEXT NOT NULL)",
        "INSERT INTO users_v4(id,username,password_hash,role,created_at) SELECT id,username,password_hash,role,created_at FROM users",
        "DROP TABLE sessions",
        "DROP TABLE users",
        "ALTER TABLE users_v4 RENAME TO users",
        "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires_at REAL NOT NULL)",
        "CREATE INDEX idx_sessions_expiry ON sessions(expires_at)",
        "CREATE TABLE camera_groups (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL)",
        "ALTER TABLE cameras ADD COLUMN group_id TEXT REFERENCES camera_groups(id) ON DELETE SET NULL",
        "ALTER TABLE incidents ADD COLUMN group_id TEXT",
        "ALTER TABLE cameras ADD COLUMN access_epoch INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE incidents ADD COLUMN access_epoch INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX idx_cameras_group ON cameras(group_id)",
        "CREATE TABLE user_camera_access (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE, PRIMARY KEY(user_id,camera_id))",
        "CREATE TABLE user_group_access (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, group_id TEXT NOT NULL REFERENCES camera_groups(id) ON DELETE CASCADE, PRIMARY KEY(user_id,group_id))",
    ],
    5: [
        "ALTER TABLE cameras ADD COLUMN modes_json TEXT NOT NULL DEFAULT '[\"shoplifting\"]'",
        "CREATE TABLE observations (id TEXT PRIMARY KEY, camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE, camera_name TEXT NOT NULL, group_id TEXT, access_epoch INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL CHECK(mode IN ('lpr','face_capture')), observed_at TEXT NOT NULL, subject_key TEXT NOT NULL, label_text TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0, snapshot_path TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}')",
        "CREATE INDEX idx_observations_camera_time ON observations(camera_id, observed_at DESC)",
        "CREATE INDEX idx_observations_mode_time ON observations(mode, observed_at DESC)",
        "CREATE INDEX idx_observations_subject ON observations(subject_key, observed_at DESC)",
    ],
    6: [
        "ALTER TABLE cameras ADD COLUMN mode_settings_json TEXT NOT NULL DEFAULT '{}'",
    ],
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
