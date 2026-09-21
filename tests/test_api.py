import importlib

import pytest
from fastapi.testclient import TestClient

from shopaware.db import Database
from shopaware.security import SecretStore
from shopaware.auth import AuthService, COOKIE
from shopaware.settings import SettingsStore


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('SHOPAWARE_DB_PATH', str(tmp_path / 'app.db'))
    monkeypatch.setenv('SHOPAWARE_KEY_FILE', str(tmp_path / 'key'))
    monkeypatch.setenv('SHOPAWARE_ALERT_DIR', str(tmp_path / 'alerts'))
    monkeypatch.setenv('SHOPAWARE_INCIDENT_DIR', str(tmp_path / 'incidents'))
    backend = importlib.import_module('backend')
    monkeypatch.setattr(backend, 'database', Database(tmp_path / 'app.db'))
    monkeypatch.setattr(backend, 'secrets', SecretStore(tmp_path / 'key'))
    monkeypatch.setattr(backend, 'camera_manager', backend.CameraManager())
    monkeypatch.setattr(backend, 'auth', AuthService(backend.database))
    monkeypatch.setattr(backend, 'settings_store', SettingsStore(backend.database, backend.secrets))
    monkeypatch.setattr(backend, 'COOKIE_SECURE', False)
    backend.auth.create_admin('test-admin', 'Synthetic-test-password-123')
    client = TestClient(backend.app)
    client.headers['origin'] = 'http://localhost:3000'
    response = client.post('/auth/login', json={'username': 'test-admin', 'password': 'Synthetic-test-password-123'})
    assert response.status_code == 200
    client.headers['x-csrf-token'] = response.json()['csrf']
    yield client, backend
    backend.camera_manager.shutdown()


def test_camera_api_persistence_password_and_deletion_cleanup(api):
    client, backend = api
    payload = dict(name='Test', rtsp_url='rtsp://embedded:old-password@[::1]:554/live',
                   username='operator', password='new-password', enabled=False)
    response = client.post('/cameras', json=payload)
    assert response.status_code == 201
    camera = response.json()['camera']
    assert camera['status'] == 'disabled'
    assert 'new-password' not in response.text
    assert 'old-password' not in response.text
    assert '********' in camera['source']
    row = backend.database.get_camera(camera['id'])
    assert row['rtsp_url'] == 'rtsp://[::1]:554/live'
    assert row['password_enc'] != payload['password']
    assert backend.secrets.decrypt(row['password_enc']) == payload['password']
    runtime = backend.camera_manager.cameras[camera['id']]
    runtime['tracking'].person(1, 1).holding_object = True
    assert client.delete('/cameras/' + camera['id']).status_code == 200
    assert runtime['tracking'].closed
    assert not runtime['tracking'].people
    assert not client.get('/cameras').json()


def test_camera_modes_default_to_shoplifting_and_support_multiple_modes(api):
    client, backend = api
    response = client.post('/cameras', json=dict(name='Modes', rtsp_url='rtsp://host/live', enabled=False,
                                                  modes=['lpr', 'shoplifting', 'face_capture']))
    assert response.status_code == 201
    camera = response.json()['camera']
    assert camera['modes'] == ['shoplifting', 'lpr', 'face_capture']
    assert client.put(f"/cameras/{camera['id']}/modes", json={'modes': ['vehicle_break_in', 'lpr']}).status_code == 200
    assert client.get('/cameras').json()[0]['modes'] == ['vehicle_break_in', 'lpr']
    row = backend.database.get_camera(camera['id'])
    assert row['modes_json'] == '["vehicle_break_in", "lpr"]'
    assert client.put(f"/cameras/{camera['id']}/modes", json={'modes': []}).status_code == 422
    assert client.put(f"/cameras/{camera['id']}/modes", json={'modes': ['recognize_faces']}).status_code == 422


def test_failed_camera_runtime_initialization_rolls_back_database_row(api, monkeypatch):
    client, backend = api
    class BrokenAnalytics:
        def __init__(self, **kwargs):
            raise RuntimeError('Synthetic analytics initialization failure')
    monkeypatch.setattr('shopaware.mode_runtime.PlateReader', BrokenAnalytics)
    response = client.post('/cameras', json=dict(name='Rollback', rtsp_url='rtsp://host/live', enabled=False))
    assert response.status_code == 400
    assert backend.database.list_cameras() == []


def test_model_initialization_retries_and_shutdown_interrupts_retry(api, monkeypatch):
    _, backend = api
    calls = []
    class Stop:
        def is_set(self): return False
        def wait(self, seconds):
            assert seconds == 30
            return len(calls) >= 2
    def unavailable():
        calls.append(1)
        raise OSError('Synthetic model download unavailable')
    monkeypatch.setattr(backend, 'video_stop', Stop())
    monkeypatch.setattr(backend, 'load_models', unavailable)
    backend.video_loop()
    assert len(calls) == 2


def test_incident_list_detail_review_and_clip_update(api):
    client, backend = api
    backend.database.insert_incident(incident_id='one', camera_id='cam', camera_name='A',
        event_type='suspected_concealment', message='requires review', risk_score=0.72,
        snapshot_path='snapshot.jpg', metadata={'signals': ['hand_to_waist']})
    assert client.get('/history').json()[0]['review_status'] == 'needs_review'
    assert client.get('/incidents/one').json()['metadata']['signals'] == ['hand_to_waist']
    for status in ['confirmed', 'false_alarm', 'dismissed', 'needs_review']:
        r = client.post('/incidents/one/review', json={'status': status})
        assert r.status_code == 200
        assert r.json()['review_status'] == status
    assert client.post('/incidents/one/review', json={'status': 'invalid'}).status_code == 422
    assert backend.database.set_incident_clip('one', 'evidence.mp4')
    assert client.get('/incidents/one').json()['clip_path'] == 'evidence.mp4'
    assert client.get('/incidents/missing').status_code == 404


def test_validation_errors_never_echo_submitted_secrets(api):
    client, _ = api
    response = client.post('/cameras', json=dict(name='', rtsp_url='rtsp://host:bad/live',
                                               password='must-never-return'))
    assert response.status_code == 422
    assert 'must-never-return' not in response.text
    assert 'rtsp://' not in response.text


def test_routes_media_and_websocket_require_authentication(api):
    client, backend = api
    stranger = TestClient(backend.app)
    for path in ['/cameras', '/history', '/health', '/docs', '/incidents/a/media/clip', '/alerts/test.jpg', '/incident-media/test.mp4']:
        assert stranger.get(path).status_code == 401
    assert stranger.get('/health/live').status_code == 200
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with stranger.websocket_connect('/ws', headers={'origin': 'http://localhost:3000'}):
            pass
    assert client.get('/auth/session').json()['username'] == 'test-admin'


def test_csrf_logout_and_cookie_attributes(api):
    client, backend = api
    token = client.cookies.get(COOKIE)
    csrf = client.headers.pop('x-csrf-token')
    assert client.post('/auth/logout').status_code == 403
    client.headers['x-csrf-token'] = csrf
    client.headers['origin'] = 'https://attacker.example'
    assert client.post('/auth/logout').status_code == 403
    client.headers['origin'] = 'http://localhost:3000'
    assert client.post('/auth/logout').status_code == 200
    assert backend.auth.session(token) is None
    monkey = pytest.MonkeyPatch()
    monkey.setattr(backend, 'COOKIE_SECURE', True)
    try:
        response = client.post('/auth/login', json={'username': 'test-admin', 'password': 'Synthetic-test-password-123'})
        cookie = response.headers['set-cookie'].lower()
        assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie
    finally:
        monkey.undo()


def test_media_only_serves_existing_files_under_configured_roots(api, tmp_path, monkeypatch):
    client, backend = api
    monkeypatch.setattr(backend, 'ALERT_DIR', tmp_path / 'alerts')
    (tmp_path / 'alerts').mkdir(exist_ok=True)
    image = tmp_path / 'alerts' / 'one.jpg'
    image.write_bytes(b'jpeg test')
    backend.database.insert_incident(incident_id='one', camera_id='cam', camera_name='A',
        event_type='high_risk_activity', message='review', risk_score=0.7, snapshot_path=str(image))
    assert client.get('/incidents/one/media/snapshot').content == b'jpeg test'
    image.unlink()
    assert client.get('/incidents/one/media/snapshot').status_code == 404
    backend.database.set_incident_clip('one', str(tmp_path / 'app.db'))
    assert client.get('/incidents/one/media/clip').status_code == 404


def test_real_video_processing_contract_and_cross_camera_concealment(api, monkeypatch):
    import numpy as np
    import torch
    from ultralytics.engine.results import Results
    from shopaware.tracking import CameraTrackingContext
    from shopaware.risk import RiskEngine
    _, backend = api
    image = np.zeros((200, 200, 3), np.uint8)

    class Capture:
        generation = 1
        def snapshot(self): return True, image.copy(), 1, self.generation, 100

    class Pose:
        def predict(self, frame, **kwargs):
            points = torch.ones((1,17,3))
            points[:,:,2] = .99
            points[0,9,:2] = torch.tensor([35,100])
            points[0,11,:2] = torch.tensor([25,100])
            points[0,12,:2] = torch.tensor([45,100])
            return [Results(frame, 'synthetic', {0:'person'}, boxes=torch.tensor([[10,10,60,150,.9,0]]), keypoints=points)]

    class Objects:
        holding = True
        names = {39:'bottle'}
        def __call__(self, frame, **kwargs):
            boxes = torch.tensor([[30,95,40,105,.9,39]]) if self.holding else torch.empty((0,6))
            return [Results(frame,'synthetic',self.names,boxes=boxes)]

    objects = Objects()
    monkeypatch.setattr(backend,'model_pose',Pose())
    monkeypatch.setattr(backend,'model_obj',objects)
    monkeypatch.setattr(backend,'model_is_specialized',False)
    incidents = []
    monkeypatch.setattr(backend,'trigger_incident',lambda *args: incidents.append(args))

    def camera():
        return dict(tracking=CameraTrackingContext(), cap=Capture(), name='Test', roi_entry_times={},
                    last_objects=[], zones=[], risk=RiskEngine(), last_alert_time=0)

    a,b=camera(),camera()
    assert backend.process_camera('a',a,100,True,image)['camera_id']=='a'
    assert a['tracking'].people[1].holding_object
    objects.holding=False
    backend.process_camera('b',b,100,True,image)
    assert not incidents
    backend.process_camera('a',a,100,True,image)
    assert len(incidents)==1
    assert incidents[0][2]=='suspected_concealment'
    assert 'concealment_candidate' in incidents[0][-1]['signals']


def test_zone_crud_preserves_camera_and_cascades_on_deletion(api):
    client, backend = api
    camera = client.post('/cameras',json=dict(name='A',rtsp_url='rtsp://host/live',enabled=False)).json()['camera']
    zone=dict(id='z',name='Shelf',type='merchandise',points=[[0,0],[1,0],[1,1]],enabled=True)
    assert client.put(f"/cameras/{camera['id']}/zones",json={'zones':[zone]}).status_code==200
    assert client.get(f"/cameras/{camera['id']}/zones").json()[0]['points']==[[0,0],[1,0],[1,1]]
    assert client.delete(f"/cameras/{camera['id']}").status_code==200
    conn=backend.database.connect()
    try:
        assert conn.execute('SELECT COUNT(*) FROM zones').fetchone()[0]==0
    finally:
        conn.close()


def test_training_api_auth_capture_annotation_export_and_stale_frames(api):
    import time
    import numpy as np
    client, backend = api
    stranger = TestClient(backend.app)
    for path in ['/training?camera_id=a', '/training/export?camera_id=a', '/training/samples/a/image']:
        assert stranger.get(path).status_code == 401
    csrf = client.headers.pop('x-csrf-token')
    assert client.post('/training/sessions', json={}).status_code == 403
    client.headers['x-csrf-token'] = csrf
    camera = client.post('/cameras', json=dict(name='Training test', rtsp_url='rtsp://host/live', enabled=False)).json()['camera']
    cid = camera['id']
    session = client.post('/training/sessions', json=dict(camera_id=cid, name='Morning', split='train')).json()['id']
    assert client.post(f'/training/sessions/{session}/capture').status_code == 503

    class Capture:
        stale = False
        def snapshot(self): return True, np.zeros((100, 200, 3), np.uint8), 1, 1, time.time() - (30 if self.stale else 0)
        def release(self): pass
    cap = Capture()
    backend.camera_manager.cameras[cid]['cap'] = cap
    response = client.post(f'/training/sessions/{session}/capture')
    assert response.status_code == 201
    sample = response.json()['id']
    image = client.get(f'/training/samples/{sample}/image')
    assert image.status_code == 200 and image.headers['cache-control'] == 'no-store'
    assert client.put(f'/training/samples/{sample}', json=dict(boxes=[], reviewed=False)).status_code == 422
    assert client.put(f'/training/samples/{sample}', json=dict(boxes=[], reviewed=True)).status_code == 200
    overview = client.get('/training', params={'camera_id':cid}).json()
    assert overview['samples'][0]['reviewer'] == 'test-admin'
    assert client.get('/training/export', params={'camera_id':cid}).status_code == 409
    cap.stale = True
    assert client.post(f'/training/sessions/{session}/capture').status_code == 503
    assert client.delete(f'/training/sessions/{session}').status_code == 200
    assert client.get(f'/training/samples/{sample}/image').status_code == 404
