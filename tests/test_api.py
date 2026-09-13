import importlib

import pytest
from fastapi.testclient import TestClient

from shopaware.db import Database
from shopaware.security import SecretStore


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
    client = TestClient(backend.app)
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
