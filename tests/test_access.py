import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_api import api  # shared authenticated API fixture
from shopaware.access import AccessControl
from shopaware.access_api import UserInput
from shopaware.auth import AuthService, COOKIE
from shopaware.db import Database

PASSWORD = 'Synthetic-customer-password-123'
ORIGIN = 'http://localhost:3000'


def sign_in(backend, username, password=PASSWORD):
    client = TestClient(backend.app)
    client.headers['origin'] = ORIGIN
    response = client.post('/auth/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text
    client.headers['x-csrf-token'] = response.json()['csrf']
    return client


@pytest.fixture
def customers(api, tmp_path, monkeypatch):
    admin, backend = api
    monkeypatch.setattr(backend, 'ALERT_DIR', tmp_path)
    monkeypatch.setattr(backend, 'INCIDENT_DIR', tmp_path)
    groups, cameras = [], []
    for name in ['Customer A', 'Customer B']:
        response = admin.post('/groups', json={'name': name})
        assert response.status_code == 201
        group = response.json()['id']; groups.append(group)
        response = admin.post('/cameras', json={'name': name + ' camera', 'rtsp_url': 'rtsp://synthetic.invalid/live',
                                               'username': 'hidden-camera-login', 'password': 'hidden-camera-password',
                                               'enabled': False, 'group_id': group})
        assert response.status_code == 201
        camera = response.json()['camera']['id']; cameras.append(camera)
    response = admin.post('/users', json={'username': 'customer-a', 'password': PASSWORD, 'group_ids': [groups[0]]})
    assert response.status_code == 201
    user_id = response.json()['id']
    for i, camera in enumerate(cameras):
        path = tmp_path / f'incident-{i}.jpg'; path.write_bytes(b'synthetic evidence')
        backend.database.insert_incident(incident_id=f'incident-{i}', camera_id=camera, camera_name=f'Camera {i}',
            event_type='high_risk_activity', message='Synthetic candidate', risk_score=.5, snapshot_path=str(path))
        backend.database.set_incident_clip(f'incident-{i}', str(path))
    return admin, backend, groups, cameras, user_id, sign_in(backend, 'customer-a')


def test_camera_history_health_and_media_are_customer_scoped(customers):
    admin, _, groups, cameras, _, user = customers
    rows = user.get('/cameras').json()
    assert [r['id'] for r in rows] == [cameras[0]]
    assert not {'source', 'rtsp_url', 'username', 'has_password'} & rows[0].keys()
    assert [g['id'] for g in user.get('/groups').json()] == [groups[0]]
    assert [i['id'] for i in user.get('/history?limit=1').json()] == ['incident-0']
    health = user.get('/health').json()
    assert health['camera_count'] == 1 and health['incident_counts'] == {'needs_review': 1}
    assert not {'telemetry', 'storage', 'alerts', 'model_error', 'detection_model'} & health.keys()
    assert user.get('/incidents/incident-0').json()['snapshot_path'] == 'snapshot'
    assert user.get('/incidents/incident-0/media/snapshot').content == b'synthetic evidence'
    assert user.get('/incidents/incident-0/media/clip', headers={'Range': 'bytes=0-3'}).status_code == 206
    assert user.get('/incidents/incident-1').status_code == 404
    assert user.get('/incidents/incident-1/media/snapshot').status_code == 404
    assert user.get('/incidents/incident-1/media/clip').status_code == 404
    assert user.get(f'/cameras/{cameras[1]}/frame').status_code == 404
    assert user.post('/incidents/incident-1/review', json={'status': 'confirmed'}).status_code == 404
    assert user.post('/incidents/incident-0/review', json={'status': 'dismissed'}).status_code == 200
    assert len(admin.get('/cameras').json()) == 2
    assert len(admin.get('/history').json()) == 2


@pytest.mark.parametrize('method,path', [('GET', '/users'), ('POST', '/users'), ('PUT', '/users/1'),
    ('POST', '/users/1/password'), ('DELETE', '/users/1'), ('POST', '/groups'), ('PUT', '/groups/test'),
    ('DELETE', '/groups/test'), ('GET', '/settings'), ('PUT', '/settings'), ('GET', '/docs'), ('GET', '/openapi.json'),
    ('GET', '/training?camera_id=test'), ('GET', '/training/export?camera_id=test'),
    ('GET', '/training/samples/test/image'), ('POST', '/training/sessions'), ('POST', '/cameras'),
    ('DELETE', '/cameras/test'), ('PUT', '/cameras/test/enabled'), ('PUT', '/cameras/test/group'),
    ('POST', '/cameras/test/test'), ('GET', '/cameras/test/zones'), ('PUT', '/cameras/test/zones'),
    ('POST', '/cameras/test/roi'), ('GET', '/alerts/test.jpg'), ('GET', '/incident-media/test.mp4')])
def test_regular_user_cannot_reach_admin_or_legacy_routes(api, method, path):
    admin, backend = api
    assert admin.post('/users', json={'username': 'limited', 'password': PASSWORD}).status_code == 201
    user = sign_in(backend, 'limited')
    assert user.request(method, path, json={}).status_code == 403


def test_direct_camera_and_group_grants_are_additive_and_default_is_no_access(customers):
    admin, backend, groups, cameras, uid, user = customers
    payload = {'username': 'customer-a', 'role': 'user', 'camera_ids': [cameras[1]], 'group_ids': [groups[0]]}
    assert admin.put(f'/users/{uid}', json=payload).status_code == 200
    assert user.get('/cameras').status_code == 401
    user = sign_in(backend, 'customer-a')
    assert len(user.get('/cameras').json()) == 2
    payload.update(camera_ids=[], group_ids=[])
    assert admin.put(f'/users/{uid}', json=payload).status_code == 200
    user = sign_in(backend, 'customer-a')
    assert user.get('/cameras').json() == [] and user.get('/history').json() == []
    assert user.get('/health').json()['incident_counts'] == {}
    assert user.get('/groups').json() == []


def test_group_move_drops_grants_old_frames_and_historical_evidence_even_when_moved_back(customers):
    admin, backend, groups, cameras, uid, _ = customers
    assert admin.put(f'/users/{uid}', json={'username': 'customer-a', 'camera_ids': [cameras[0]], 'group_ids': groups}).status_code == 200
    user = sign_in(backend, 'customer-a')
    session = backend.auth.session(user.cookies.get(COOKIE))
    old_runtime = backend.camera_manager.cameras[cameras[0]]
    payload = {'type': 'multi_frame', 'cameras': [{'camera_id': cameras[0], 'data': 'old-customer-frame', 'access_epoch': 0}]}
    assert backend.visible_frame_payload(payload, session)['cameras']
    assert admin.put(f'/cameras/{cameras[0]}/group', json={'group_id': groups[1]}).status_code == 200
    assert old_runtime['tracking'].closed
    assert backend.visible_frame_payload(payload, session)['cameras'] == []
    assert user.get('/incidents/incident-0').status_code == 404
    assert admin.put(f'/cameras/{cameras[0]}/group', json={'group_id': groups[0]}).status_code == 200
    assert user.get('/incidents/incident-0').status_code == 404
    assert admin.get('/incidents/incident-0').status_code == 200
    saved = next(u for u in admin.get('/users').json() if u['id'] == uid)
    assert saved['camera_ids'] == []
    assert admin.delete('/groups/' + groups[0]).status_code == 409


def test_websocket_is_filtered_and_revocation_closes_active_connection(customers, monkeypatch):
    admin, backend, _, cameras, uid, user = customers
    monkeypatch.setattr(backend, 'latest_frame', {'type': 'multi_frame', 'cameras': [
        {'camera_id': cid, 'name': cid, 'data': 'synthetic-frame', 'access_epoch': 0} for cid in cameras]})
    with user.websocket_connect('/ws', headers={'origin': ORIGIN}) as ws:
        assert [c['camera_id'] for c in ws.receive_json()['cameras']] == [cameras[0]]
        assert admin.put(f'/users/{uid}', json={'username': 'customer-a', 'enabled': False}).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            for _ in range(30):
                ws.receive_json()
    assert user.get('/cameras').status_code == 401
    assert TestClient(backend.app).post('/auth/login', headers={'origin': ORIGIN}, json={'username': 'customer-a', 'password': PASSWORD}).status_code == 401


def test_password_change_reset_and_old_sessions(customers):
    admin, backend, _, _, uid, user = customers
    second = sign_in(backend, 'customer-a')
    assert user.post('/auth/password', json={'current_password': 'wrong', 'new_password': PASSWORD + 'new'}).status_code == 400
    assert user.post('/auth/password', json={'current_password': PASSWORD, 'new_password': PASSWORD + 'new'}).status_code == 200
    assert second.get('/auth/session').status_code == 401
    user = sign_in(backend, 'customer-a', PASSWORD + 'new')
    assert admin.post(f'/users/{uid}/password', json={'password': PASSWORD}).status_code == 200
    assert user.get('/auth/session').status_code == 401
    sign_in(backend, 'customer-a')
    own_id = admin.get('/auth/session').json()['id']
    assert admin.post(f'/users/{own_id}/password', json={'password': PASSWORD}).status_code == 409
    assert admin.delete(f'/users/{own_id}').status_code == 409
    assert admin.put(f'/users/{own_id}', json={'username': 'test-admin', 'role': 'user'}).status_code == 409


def test_invalid_assignments_rollback_user_and_passwords_are_never_returned(customers):
    admin, _, _, _, _, _ = customers
    before = admin.get('/users').json()
    assert admin.post('/users', json={'username': 'bad-grant', 'password': PASSWORD, 'camera_ids': ['missing']}).status_code == 409
    assert admin.get('/users').json() == before
    assert PASSWORD not in admin.get('/users').text and 'password_hash' not in admin.get('/users').text
    assert admin.post('/users', json={'username': 'bad-role', 'password': PASSWORD, 'role': 'superadmin'}).status_code == 422


def test_group_grants_include_future_cameras_and_empty_group_ids_are_rejected(customers):
    admin, _, groups, cameras, _, user = customers
    response = admin.post('/cameras', json={'name': 'Future camera', 'rtsp_url': 'rtsp://synthetic.invalid/new',
                                          'enabled': False, 'group_id': groups[0]})
    assert response.status_code == 201
    future = response.json()['camera']['id']
    assert {c['id'] for c in user.get('/cameras').json()} == {cameras[0], future}
    assert admin.put(f'/cameras/{future}/group', json={'group_id': ''}).status_code == 422
    assert admin.post('/cameras', json={'name': 'Invalid', 'rtsp_url': 'rtsp://synthetic.invalid/new',
                                      'enabled': False, 'group_id': ''}).status_code == 422


def test_v3_upgrade_preserves_users_credentials_cameras_and_invalidates_old_sessions(tmp_path, monkeypatch):
    from shopaware import migrations
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, 'MIGRATIONS', {v: s for v, s in all_migrations.items() if v <= 3})
    path = tmp_path / 'old.db'; old = Database(path)
    AuthService(old).create_admin('original', PASSWORD)
    with old.connect() as conn:
        # Seed the old schema directly: current insert helpers require v4 columns.
        conn.execute("INSERT INTO cameras(id,name,rtsp_url,username,password_enc,enabled,created_at,updated_at) VALUES('old','Old','rtsp://host/live','u','preserve-ciphertext',0,'2026-01-01','2026-01-01')")
        conn.execute("INSERT INTO incidents(id,camera_id,camera_name,event_type,message,created_at,snapshot_path) VALUES('old','old','Old','high_risk_activity','preserve','2026-01-01','preserve.jpg')")
        uid = conn.execute('SELECT id FROM users').fetchone()[0]
        conn.execute('INSERT INTO sessions VALUES(?,?,?,?)', (hashlib.sha256(b'oldtoken').hexdigest(), uid, 'csrf', time.time()+3600))
    monkeypatch.setattr(migrations, 'MIGRATIONS', all_migrations)
    new = Database(path)
    auth = AuthService(new)
    assert auth.session('oldtoken') is None
    assert auth.login('original', PASSWORD, 'local')[1]['id'] == uid
    assert new.get_camera('old')['password_enc'] == 'preserve-ciphertext'
    assert new.incident('old')['snapshot_path'] == 'preserve.jpg'
    with new.connect() as conn:
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_concurrent_demotions_cannot_remove_last_admin(api):
    admin, backend = api
    first = admin.get('/auth/session').json()['id']
    second = admin.post('/users', json={'username': 'second-admin', 'role': 'admin', 'password': PASSWORD}).json()['id']
    def demote(actor, target, name):
        try:
            AccessControl(backend.database).save_user(actor, UserInput(username=name, role='user'), target)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(demote, first, second, 'second-admin')
        b = pool.submit(demote, second, first, 'test-admin')
        assert sorted([a.result(), b.result()]) == [False, True]
