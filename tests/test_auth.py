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
