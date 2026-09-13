import pytest
from shopaware.auth import AuthService
from shopaware.db import Database


def test_hash_expiry_logout_and_rate_limit(tmp_path):
    db = Database(tmp_path / 'auth.db')
    auth = AuthService(db, lifetime=10)
    auth.create_admin('admin', 'Synthetic-test-password-123')
    conn = db.connect()
    try:
        row = conn.execute('SELECT password_hash FROM users').fetchone()
        assert row[0].startswith('$argon2id$')
        assert 'Synthetic' not in row[0]
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    finally:
        conn.close()
    assert auth.login('admin', 'wrong', 'test', now=100) is None
    token, session = auth.login('admin', 'Synthetic-test-password-123', 'test', now=100)
    assert auth.session(token, now=109)['username'] == 'admin'
    assert auth.session(token, now=110) is None
    auth.logout(token)
    assert auth.session(token, now=105) is None
    for _ in range(10):
        assert auth.login('missing', 'wrong', 'bad', now=100) is None
    with pytest.raises(PermissionError):
        auth.login('missing', 'wrong', 'bad', now=100)


def test_migration_reopen_preserves_existing_camera_incident_and_user(tmp_path):
    path = tmp_path / 'db'
    db = Database(path)
    db.insert_camera(camera_id='a', name='A', rtsp_url='rtsp://host/live', username='', password_enc='', enabled=False)
    db.insert_incident(incident_id='i', camera_id='a', camera_name='A', event_type='high_risk_activity', message='review', risk_score=0.8, snapshot_path=None)
    AuthService(db).create_admin('admin', 'Synthetic-test-password-123')
    reopened = Database(path)
    assert reopened.get_camera('a')['name'] == 'A'
    assert reopened.incident('i')['risk_score'] == 0.8


def test_unversioned_baseline_database_upgrade_preserves_existing_rows(tmp_path):
    import sqlite3
    path = tmp_path / 'legacy.db'
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE cameras(id TEXT PRIMARY KEY,name TEXT NOT NULL,rtsp_url TEXT NOT NULL,
        username TEXT NOT NULL DEFAULT '',password_enc TEXT NOT NULL DEFAULT '',roi_json TEXT NOT NULL DEFAULT '[]',
        enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE incidents(id TEXT PRIMARY KEY,camera_id TEXT NOT NULL,camera_name TEXT NOT NULL,event_type TEXT NOT NULL,
        message TEXT NOT NULL,risk_score REAL NOT NULL DEFAULT 0,created_at TEXT NOT NULL,snapshot_path TEXT,
        clip_path TEXT,review_status TEXT NOT NULL DEFAULT 'needs_review',metadata_json TEXT NOT NULL DEFAULT '{}');
    INSERT INTO cameras VALUES('legacy','Legacy','rtsp://host/live','user','existing-ciphertext','[]',0,'2026-01-01','2026-01-01');
    INSERT INTO incidents VALUES('i','legacy','Legacy','suspected_concealment','review',.8,'2026-01-01','snapshot.jpg',NULL,'needs_review','{}');
    """)
    conn.close()
    db = Database(path)
    assert db.get_camera('legacy')['password_enc'] == 'existing-ciphertext'
    assert db.incident('i')['snapshot_path'] == 'snapshot.jpg'
    conn = db.connect()
    try:
        from shopaware.migrations import MIGRATIONS
        assert conn.execute('PRAGMA user_version').fetchone()[0] == max(MIGRATIONS)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        conn.close()
