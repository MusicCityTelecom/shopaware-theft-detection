import importlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import numpy as np
from fastapi.testclient import TestClient

from shopaware.mode_settings import parse_mode_settings
from shopaware.mode_settings import CameraModeSettingsInput
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

    before = parse_mode_settings(backend.database.get_camera(camera["id"])["mode_settings_json"])

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
    assert parse_mode_settings(row["mode_settings_json"]) == before


@pytest.mark.parametrize("mode,field,value,changed_helper", [
    ("shoplifting", "risk_threshold", 78, "risk"),
    ("vehicle_break_in", "dwell_seconds", 24, "break_in"),
    ("lpr", "min_ocr_confidence", .64, "plate_reader"),
    ("face_capture", "min_quality", .25, "face_capture"),
    (None, None, None, None),
])
def test_saving_mode_preserves_other_modes_live_state(api, mode, field, value, changed_helper):
    admin, backend = api
    importlib.import_module("beta5_backend")
    response = admin.post("/cameras", json={
        "name": "Independent tuning", "rtsp_url": "rtsp://synthetic.invalid/live",
        "enabled": False, "modes": ["shoplifting", "vehicle_break_in", "lpr", "face_capture"],
    })
    assert response.status_code == 201
    camera_id = response.json()["camera"]["id"]
    runtime = backend.camera_manager.cameras[camera_id]
    helpers = {name: runtime[name] for name in ("risk", "break_in", "plate_reader", "face_capture")}
    runtime["plate_reader"].last_seen["ABC123"] = 100.0
    runtime["face_capture"].state["1:42"] = (100.0, 5, .8)
    settings = admin.get(f"/cameras/{camera_id}/mode-settings").json()
    if mode is not None:
        settings[mode][field] = value
    saved = admin.put(f"/cameras/{camera_id}/mode-settings", json=settings)
    assert saved.status_code == 200
    assert saved.json()["settings"] == settings
    for name, helper in helpers.items():
        if name != changed_helper:
            assert runtime[name] is helper
        else:
            assert runtime[name] is not helper
    if changed_helper != "plate_reader":
        assert runtime["plate_reader"].last_seen["ABC123"] == 100.0
    if changed_helper != "face_capture":
        assert runtime["face_capture"].state["1:42"] == (100.0, 5, .8)
    assert parse_mode_settings(backend.database.get_camera(camera_id)["mode_settings_json"]) == settings


def test_reconnect_between_snapshots_uses_saved_tuning_on_first_frame(api, monkeypatch):
    admin, backend = api
    importlib.import_module("beta5_backend")
    camera_id = admin.post("/cameras", json={
        "name": "Reconnect race", "rtsp_url": "rtsp://synthetic.invalid/live", "enabled": False,
    }).json()["camera"]["id"]
    settings = parse_mode_settings({
        "shoplifting": {"risk_threshold": 81},
        "lpr": {"min_ocr_confidence": .71},
        "face_capture": {"max_images_per_track": 2},
        "vehicle_break_in": {"dwell_seconds": 29},
    })
    assert admin.put(f"/cameras/{camera_id}/mode-settings", json=settings).status_code == 200
    camera = backend.camera_manager.cameras[camera_id]
    frame = np.zeros((64, 96, 3), dtype=np.uint8)
    generations = iter([10, 11])

    class Capture:
        def snapshot(self):
            return True, frame, 1, next(generations), 100.0

        def release(self):
            pass

    class StopBeforeInference(Exception):
        pass

    class Pose:
        def predict(self, *args, **kwargs):
            assert camera["tracking"].generation == 11
            assert camera["risk"].threshold == 81
            assert camera["plate_reader"].min_ocr_confidence == .71
            assert camera["face_capture"].max_per_track == 2
            assert camera["break_in"].dwell_seconds == 29
            raise StopBeforeInference

    monkeypatch.setitem(camera, "cap", Capture())
    monkeypatch.setattr(backend, "model_pose", Pose())
    monkeypatch.setattr(backend, "model_obj", object())
    with camera["tracking"].lock, pytest.raises(StopBeforeInference):
        backend.process_camera(camera_id, camera, 100.0, True, frame)


def test_concurrent_saves_serialize_database_and_live_tuning(api, monkeypatch):
    admin, backend = api
    importlib.import_module("beta5_backend")
    from shopaware import mode_runtime
    camera_id = admin.post("/cameras", json={
        "name": "Concurrent save", "rtsp_url": "rtsp://synthetic.invalid/live", "enabled": False,
    }).json()["camera"]["id"]
    endpoint = next(route.endpoint for route in backend.app.routes
                    if getattr(route, "path", "") == "/cameras/{camera_id}/mode-settings"
                    and "PUT" in route.methods)
    first_persisted, release_first, second_attempted = (threading.Event() for _ in range(3))
    second_persisted = threading.Event()
    actual_lock = threading.RLock()

    class ObservedLock:
        def __enter__(self):
            if first_persisted.is_set():
                second_attempted.set()
            actual_lock.acquire()

        def __exit__(self, *args):
            actual_lock.release()

    original = mode_runtime._persist_settings

    def persist(core, camera_id, settings):
        original(core, camera_id, settings)
        if settings["shoplifting"]["risk_threshold"] == 71:
            first_persisted.set()
            assert release_first.wait(10)
        else:
            second_persisted.set()

    monkeypatch.setattr(backend, "camera_lifecycle_lock", ObservedLock())
    monkeypatch.setattr(mode_runtime, "_persist_settings", persist)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(endpoint, camera_id, CameraModeSettingsInput(shoplifting={"risk_threshold": 71}))
        second = None
        try:
            assert first_persisted.wait(10)
            second = pool.submit(endpoint, camera_id, CameraModeSettingsInput(shoplifting={"risk_threshold": 82}))
            assert second_attempted.wait(10)
            assert not second_persisted.is_set()
        finally:
            release_first.set()
        first.result(timeout=10)
        second.result(timeout=10)
    runtime = backend.camera_manager.cameras[camera_id]
    persisted = parse_mode_settings(backend.database.get_camera(camera_id)["mode_settings_json"])
    assert persisted == runtime["mode_settings"]
    assert runtime["risk"].threshold == 82


def test_closed_camera_cannot_persist_settings(api):
    admin, backend = api
    importlib.import_module("beta5_backend")
    camera_id = admin.post("/cameras", json={
        "name": "Closed camera", "rtsp_url": "rtsp://synthetic.invalid/live", "enabled": False,
    }).json()["camera"]["id"]
    before = backend.database.get_camera(camera_id)["mode_settings_json"]
    backend.camera_manager.cameras[camera_id]["tracking"].close()
    response = admin.put(f"/cameras/{camera_id}/mode-settings", json={"shoplifting": {"risk_threshold": 81}})
    assert response.status_code == 404
    assert backend.database.get_camera(camera_id)["mode_settings_json"] == before


@pytest.mark.parametrize("loitering,expected", [(5, True), (50, False)])
def test_frame_processing_uses_camera_loitering_for_zone_signals_and_roi_label(api, monkeypatch, loitering, expected):
    import torch
    from ultralytics.engine.results import Results
    from shopaware.zones import Zone

    admin, backend = api
    importlib.import_module("beta5_backend")
    camera_id = admin.post("/cameras", json={
        "name": "Local dwell", "rtsp_url": "rtsp://synthetic.invalid/live", "enabled": False,
    }).json()["camera"]["id"]
    settings = parse_mode_settings({"shoplifting": {"loitering_seconds": loitering, "risk_threshold": 5}})
    assert admin.put(f"/cameras/{camera_id}/mode-settings", json=settings).status_code == 200
    camera = backend.camera_manager.cameras[camera_id]
    image = np.zeros((200, 200, 3), np.uint8)
    monkeypatch.setattr(backend, "LOITERING_THRESHOLD", 100)

    class Capture:
        now = 100
        def snapshot(self):
            return True, image.copy(), 1, 1, self.now
        def release(self):
            pass

    class Pose:
        def predict(self, frame, **kwargs):
            # A new camera created during inference must still see site defaults.
            from shopaware.mode_runtime import _legacy_settings
            assert _legacy_settings(backend)["shoplifting"]["loitering_seconds"] == 100
            return [Results(frame, "synthetic", {0: "person"},
                            boxes=torch.tensor([[10, 10, 60, 150, .9, 0]]),
                            keypoints=torch.zeros((1, 17, 3)))]

    class Objects:
        names = {39: "bottle"}
        def __call__(self, frame, **kwargs):
            return [Results(frame, "synthetic", self.names, boxes=torch.empty((0, 6)))]

    capture = Capture()
    monkeypatch.setitem(camera, "cap", capture)
    camera["zones"] = [Zone(id="parking", name="Parking", type="parking",
                            points=[(0, 0), (1, 0), (1, 1), (0, 1)])]
    camera["roi_points"] = [[0, 0], [199, 0], [199, 199], [0, 199]]
    monkeypatch.setattr(backend, "model_pose", Pose())
    monkeypatch.setattr(backend, "model_obj", Objects())
    monkeypatch.setattr(backend, "model_is_specialized", False)
    incidents, labels = [], []
    monkeypatch.setattr(backend, "trigger_incident", lambda *args: incidents.append(args))
    real_put_text = backend.cv2.putText
    def put_text(frame, text, *args, **kwargs):
        labels.append(text)
        return real_put_text(frame, text, *args, **kwargs)
    monkeypatch.setattr(backend.cv2, "putText", put_text)
    with camera["tracking"].lock:
        backend.process_camera(camera_id, camera, capture.now, True, image)
        capture.now = 110
        backend.process_camera(camera_id, camera, capture.now, True, image)
    assert bool(incidents) is expected
    assert any(label.startswith("DWELL ") for label in labels) is expected
    if expected:
        assert incidents[0][-1]["signals"] == ["excessive_dwell"]
