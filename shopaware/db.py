from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cameras (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    rtsp_url TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    password_enc TEXT NOT NULL DEFAULT '',
                    roi_json TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    camera_id TEXT NOT NULL,
                    camera_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    risk_score REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    snapshot_path TEXT,
                    clip_path TEXT,
                    review_status TEXT NOT NULL DEFAULT 'needs_review',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_incidents_camera ON incidents(camera_id, created_at DESC);
                """
            )
            conn.commit()
        finally:
            conn.close()

    def list_cameras(self) -> list[sqlite3.Row]:
        conn = self.connect()
        try:
            return conn.execute("SELECT * FROM cameras ORDER BY created_at").fetchall()
        finally:
            conn.close()

    def get_camera(self, camera_id: str) -> sqlite3.Row | None:
        conn = self.connect()
        try:
            return conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        finally:
            conn.close()

    def insert_camera(
        self,
        *,
        camera_id: str,
        name: str,
        rtsp_url: str,
        username: str,
        password_enc: str,
        enabled: bool,
    ) -> sqlite3.Row:
        now = utc_now_iso()
        conn = self.connect()
        try:
            conn.execute(
                """
                INSERT INTO cameras(id, name, rtsp_url, username, password_enc, roi_json, enabled, created_at, updated_at)
                VALUES(?,?,?,?,?,'[]',?,?,?)
                """,
                (camera_id, name, rtsp_url, username, password_enc, int(enabled), now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
            if row is None:
                raise RuntimeError("camera insert failed")
            return row
        finally:
            conn.close()

    def delete_camera(self, camera_id: str) -> bool:
        conn = self.connect()
        try:
            cur = conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_camera_roi(self, camera_id: str, points: list[list[int]]) -> bool:
        conn = self.connect()
        try:
            cur = conn.execute(
                "UPDATE cameras SET roi_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(points), utc_now_iso(), camera_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def insert_incident(
        self,
        *,
        incident_id: str,
        camera_id: str,
        camera_name: str,
        event_type: str,
        message: str,
        risk_score: float,
        snapshot_path: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conn = self.connect()
        try:
            conn.execute(
                """
                INSERT INTO incidents(
                    id, camera_id, camera_name, event_type, message, risk_score,
                    created_at, snapshot_path, clip_path, review_status, metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,'needs_review',?)
                """,
                (
                    incident_id,
                    camera_id,
                    camera_name,
                    event_type,
                    message,
                    float(max(0.0, min(1.0, risk_score))),
                    utc_now_iso(),
                    snapshot_path,
                    None,
                    json.dumps(metadata or {}),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def set_incident_clip(self, incident_id: str, clip_path: str) -> bool:
        conn = self.connect()
        try:
            cur = conn.execute("UPDATE incidents SET clip_path = ? WHERE id = ?", (clip_path, incident_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_incident_review(self, incident_id: str, status: str) -> sqlite3.Row | None:
        conn = self.connect()
        try:
            cur = conn.execute("UPDATE incidents SET review_status = ? WHERE id = ?", (status, incident_id))
            conn.commit()
            if cur.rowcount == 0:
                return None
            return conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        finally:
            conn.close()

    def history(self, limit: int) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute("SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json", "{}") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
            result.append(item)
        return result

    def incident(self, incident_id: str) -> dict[str, Any] | None:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json", "{}") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        return item
