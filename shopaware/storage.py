"""Retention deletes only recorded incident paths contained in configured roots."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from shopaware.db import Database

logger = logging.getLogger(__name__)


class RetentionManager:
    def __init__(self, database: Database, roots: list[Path], active: Callable[[], set[str]],
                 days: float = 30, max_bytes: int = 10 * 1024**3):
        if days <= 0 or max_bytes <= 0:
            raise ValueError('Retention days and storage quota must be positive')
        self.database, self.roots, self.active = database, [p.resolve() for p in roots], active
        self.days, self.max_bytes = days, max_bytes

    def usage(self) -> int:
        total = 0
        for root in self.roots:
            for path in root.glob('*'):
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
        return total

    def enforce(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        used = self.usage()
        deleted, failures = 0, 0
        conn = self.database.connect()
        try:
            rows = conn.execute("SELECT * FROM incidents WHERE media_status != 'pending' ORDER BY created_at ASC").fetchall()
            for row in rows:
                if row['id'] in self.active():
                    continue
                expired = datetime.fromisoformat(row['created_at']).timestamp() < now - self.days * 86400
                if not expired and used <= self.max_bytes:
                    continue
                paths = []
                for field in ('snapshot_path', 'clip_path'):
                    if row[field]:
                        path = Path(row[field]).resolve()
                        if not any(path.is_relative_to(root) for root in self.roots):
                            raise ValueError('Incident media path is outside configured roots')
                        paths.append(path)
                        if field == 'clip_path':
                            paths.append(path.with_suffix('.json'))
                try:
                    # Active/pending IDs are excluded until writing and callbacks finish.
                    for path in paths:
                        path.unlink(missing_ok=True)
                    conn.execute('DELETE FROM incidents WHERE id=?', (row['id'],))
                    conn.commit()
                    deleted += 1
                except OSError:
                    conn.rollback()
                    failures += 1
                    logger.warning('Retention deletion deferred', extra={'incident_id': row['id']})
                used = self.usage()

            observations = conn.execute("SELECT * FROM observations ORDER BY observed_at ASC").fetchall()
            for row in observations:
                expired = datetime.fromisoformat(row['observed_at']).timestamp() < now - self.days * 86400
                if not expired and used <= self.max_bytes:
                    continue
                path = Path(row['snapshot_path']).resolve()
                if not any(path.is_relative_to(root) for root in self.roots):
                    raise ValueError('Observation media path is outside configured roots')
                try:
                    path.unlink(missing_ok=True)
                    conn.execute('DELETE FROM observations WHERE id=?', (row['id'],))
                    conn.commit()
                    deleted += 1
                except OSError:
                    conn.rollback()
                    failures += 1
                    logger.warning('Observation retention deletion deferred', extra={'observation_id': row['id']})
                used = self.usage()
        finally:
            conn.close()
        return dict(bytes_used=used, max_bytes=self.max_bytes, quota_exceeded=used > self.max_bytes,
                    deleted=deleted, failures=failures, retention_days=self.days)
