import os
from pathlib import Path
import subprocess
import sys

import pytest

from shopaware.mode_settings import parse_mode_settings
from test_api import api


@pytest.mark.parametrize("entrypoint", ["backend", "beta5_backend"])
def test_fresh_entrypoint_has_mode_routes_without_runtime_patching(tmp_path, entrypoint):
    env = {**os.environ, "SHOPAWARE_DB_PATH": str(tmp_path / "app.db"),
           "SHOPAWARE_KEY_FILE": str(tmp_path / "key"),
           "SHOPAWARE_ALERT_DIR": str(tmp_path / "alerts"),
           "SHOPAWARE_INCIDENT_DIR": str(tmp_path / "incidents")}
    script = """
import importlib, sys
core = importlib.import_module('backend')
processor, factory = core.process_camera, core.CameraManager.add_camera
entry = importlib.import_module(sys.argv[1])
assert entry.app is core.app
assert core.process_camera is processor
assert core.CameraManager.add_camera is factory
routes = [route for route in core.app.routes if getattr(route, 'path', '') == '/cameras/{camera_id}/mode-settings']
assert len(routes) == 2
assert {method for route in routes for method in route.methods} == {'GET', 'PUT'}
core.camera_manager.shutdown()
core.alert_dispatcher.close()
"""
    subprocess.run([sys.executable, "-c", script, entrypoint], env=env,
                   cwd=Path(__file__).resolve().parents[1], check=True, timeout=60,
                   capture_output=True, text=True)


@pytest.mark.parametrize("operation", ["reload", "enable", "modes"])
def test_runtime_lifecycle_applies_saved_tuning_before_first_frame(api, operation):
    admin, core = api
    camera_id = admin.post("/cameras", json={
        "name": "Lifecycle camera", "rtsp_url": "rtsp://synthetic.invalid/live", "enabled": False,
    }).json()["camera"]["id"]
    settings = parse_mode_settings({
        "shoplifting": {"risk_threshold": 81, "risk_window_seconds": 28, "loitering_seconds": 26},
        "lpr": {"min_ocr_confidence": .72, "observation_cooldown_seconds": 7200},
        "face_capture": {"max_images_per_track": 2},
        "vehicle_break_in": {"dwell_seconds": 29},
    })
    assert admin.put(f"/cameras/{camera_id}/mode-settings", json=settings).status_code == 200
    replacement = None
    try:
        if operation == "reload":
            replacement = core.CameraManager()
            runtime = replacement.cameras[camera_id]
        elif operation == "enable":
            assert admin.put(f"/cameras/{camera_id}/enabled", json={"enabled": False}).status_code == 200
            runtime = core.camera_manager.cameras[camera_id]
        else:
            modes = ["shoplifting", "lpr", "face_capture", "vehicle_break_in"]
            assert admin.put(f"/cameras/{camera_id}/modes", json={"modes": modes}).status_code == 200
            runtime = core.camera_manager.cameras[camera_id]
        assert runtime["mode_settings"] == settings
        assert runtime["risk"].threshold == 81
        assert runtime["risk"].window_seconds == 28
        assert runtime["plate_reader"].min_ocr_confidence == .72
        assert runtime["plate_reader"].cooldown_seconds == 7200
        assert runtime["face_capture"].max_per_track == 2
        assert runtime["break_in"].dwell_seconds == 29
    finally:
        if replacement is not None:
            replacement.shutdown()


@pytest.mark.parametrize("schema", [5, 6])
def test_fresh_backend_preserves_migrated_or_saved_camera_settings(tmp_path, monkeypatch, schema):
    from shopaware import migrations
    from shopaware.db import Database
    from shopaware.mode_runtime import persist_mode_settings
    from shopaware.security import SecretStore

    database_path, key_path = tmp_path / "app.db", tmp_path / "key"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", {
            version: statements for version, statements in migrations.MIGRATIONS.items()
            if version <= schema
        })
        database = Database(database_path)
    database.insert_camera(
        camera_id="saved", name="Startup camera", rtsp_url="rtsp://synthetic.invalid/live",
        username="operator", password_enc=SecretStore(key_path).encrypt("synthetic-camera-password"),
        enabled=False,
    )
    settings = parse_mode_settings({"shoplifting": {"risk_threshold": 81, "loitering_seconds": 26}})
    if schema == 6:
        persist_mode_settings(database, "saved", settings)
    # Schema 5 must snapshot current site defaults; schema 6 must ignore changed defaults.
    env = {**os.environ, "SHOPAWARE_DB_PATH": str(database_path),
           "SHOPAWARE_KEY_FILE": str(key_path),
           "SHOPAWARE_ALERT_DIR": str(tmp_path / "alerts"),
           "SHOPAWARE_INCIDENT_DIR": str(tmp_path / "incidents"),
           "SHOPAWARE_RISK_THRESHOLD": "81" if schema == 5 else "42",
           "SHOPAWARE_LOITERING_THRESHOLD": "12"}
    script = """
import backend
from shopaware.mode_settings import parse_mode_settings
camera = backend.camera_manager.cameras['saved']
assert camera['risk'].threshold == 81
assert camera['mode_settings']['shoplifting']['loitering_seconds'] == EXPECTED_LOITERING
assert camera['cap'] is None
assert 'synthetic-camera-password' in camera['runtime_url']
row = backend.database.get_camera('saved')
assert parse_mode_settings(row['mode_settings_json']) == camera['mode_settings']
assert row['password_enc'] != 'synthetic-camera-password'
with backend.database.connect() as connection:
    assert connection.execute('PRAGMA user_version').fetchone()[0] == 6
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()
backend.camera_manager.shutdown()
backend.alert_dispatcher.close()
""".replace("EXPECTED_LOITERING", "12" if schema == 5 else "26")
    subprocess.run([sys.executable, "-c", script], env=env,
                   cwd=Path(__file__).resolve().parents[1], check=True, timeout=60,
                   capture_output=True, text=True)
