from pathlib import Path
from shopaware.db import Database
from shopaware.storage import RetentionManager


def create(db, root, ident, status='ready'):
    path = root / (ident + '.jpg')
    path.write_bytes(b'x' * 100)
    db.insert_incident(incident_id=ident, camera_id='cam', camera_name='A', event_type='high_risk_activity',
                       message='review', risk_score=.7, snapshot_path=str(path))
    db.set_media_status(ident, status)
    return path


def test_quota_preserves_active_pending_and_unknown_files(tmp_path):
    root = tmp_path / 'media'
    root.mkdir()
    db = Database(tmp_path / 'db')
    oldest = create(db, root, 'old')
    active = create(db, root, 'active')
    pending = create(db, root, 'pending', 'pending')
    unknown = root / 'operator.txt'
    unknown.write_text('keep')
    status = RetentionManager(db, [root], lambda: {'active'}, max_bytes=150).enforce()
    assert not oldest.exists()
    assert db.incident('old') is None
    assert active.exists() and pending.exists() and unknown.exists()
    assert status['quota_exceeded']


def test_retention_missing_media_and_outside_root(tmp_path):
    root = tmp_path / 'media'
    root.mkdir()
    db = Database(tmp_path / 'db')
    path = create(db, root, 'old')
    path.unlink()
    status = RetentionManager(db, [root], lambda: set(), days=1).enforce(now=4_000_000_000)
    assert status['deleted'] == 1
    assert db.incident('old') is None
