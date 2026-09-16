import importlib

from fastapi.testclient import TestClient

from shopaware.mode_settings import parse_mode_settings
from test_api import api  # shared authenticated API fixture

ORIGIN = "http://localhost:3000"
PASSWORD = "Synthetic-beta5-user-password-123"


def test_beta5_entrypoint_exposes_admin_mode_settings_and_denies_regular_users(api):
    admin, backend = api

    camera = admin.post(
        "/cameras",
        json={
            "name": "Beta5 tuning camera",
            "rtsp_url": "rtsp://synthetic.invalid/live",
            "enabled": False,
            "modes": ["shoplifting", "vehicle_break_in", "lpr", "face_capture"],
        },
    ).json()["camera"]

    entry = importlib.import_module("beta5_backend")
    assert entry.app is backend.app

    response = admin.get(f"/cameras/{camera['id']}/mode-settings")
    assert response.status_code == 200
    defaults = response.json()
    assert defaults == parse_mode_settings(None)

    updated = defaults.copy()
    updated["shoplifting"] = {**updated["shoplifting"], "risk_threshold": 78, "loitering_seconds": 21}
    updated["vehicle_break_in"] = {**updated["vehicle_break_in"], "dwell_seconds": 24, "required_access_interactions": 4}
    updated["lpr"] = {**updated["lpr"], "min_ocr_confidence": .64, "observation_cooldown_seconds": 33}
    updated["face_capture"] = {**updated["face_capture"], "min_quality": .25, "max_images_per_track": 3}

    response = admin.put(f"/cameras/{camera['id']}/mode-settings", json=updated)
    assert response.status_code == 200
    assert response.json()["restart_required"] is False
    assert response.json()["settings"] == updated

    row = backend.database.get_camera(camera["id"])
    assert row is not None
    persisted = parse_mode_settings(row["mode_settings_json"])
    assert persisted == updated

    runtime = backend.camera_manager.cameras[camera["id"]]
    assert runtime["risk"].threshold == 78
    assert runtime["plate_reader"].min_ocr_confidence == .64
    assert runtime["face_capture"].min_quality == .25
    assert runtime["break_in"].dwell_seconds == 24

    assert admin.post("/users", json={"username": "beta5-user", "password": PASSWORD}).status_code == 201
    user = TestClient(backend.app)
    user.headers["origin"] = ORIGIN
    login = user.post("/auth/login", json={"username": "beta5-user", "password": PASSWORD})
    assert login.status_code == 200
    user.headers["x-csrf-token"] = login.json()["csrf"]
    assert user.get(f"/cameras/{camera['id']}/mode-settings").status_code == 403
    assert user.put(f"/cameras/{camera['id']}/mode-settings", json=updated).status_code == 403


def test_beta5_admin_mode_settings_validation_rejects_invalid_values(api):
    admin, backend = api
    importlib.import_module("beta5_backend")
    camera = admin.post(
        "/cameras",
        json={"name": "Validation camera", "rtsp_url": "rtsp://synthetic.invalid/live", "enabled": False},
    ).json()["camera"]

    response = admin.put(
        f"/cameras/{camera['id']}/mode-settings",
        json={"lpr": {"min_plate_chars": 10, "max_plate_chars": 4}},
    )
    assert response.status_code == 422

    response = admin.put(
        f"/cameras/{camera['id']}/mode-settings",
        json={"face_capture": {"recognize_people": True}},
    )
    assert response.status_code == 422

    row = backend.database.get_camera(camera["id"])
    assert row is not None
    assert row["mode_settings_json"] == "{}"
