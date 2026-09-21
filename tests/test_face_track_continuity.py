import numpy as np
import pytest
import torch
from ultralytics.engine.results import Results

from shopaware.analytics import FaceCapture, FaceObservation
from test_api import api


@pytest.mark.parametrize("change", ["reload", "enabled", "modes", "resolution", "reconnect", "tuning"])
def test_persisted_face_groups_split_when_tracking_restarts(api, monkeypatch, tmp_path, change):
    admin, backend = api
    camera_id = admin.post("/cameras", json={
        "name": "Continuity", "rtsp_url": "rtsp://synthetic.invalid/live", "enabled": False,
        "modes": ["face_capture"],
    }).json()["camera"]["id"]
    camera = backend.camera_manager.cameras[camera_id]

    class Capture:
        generation = 1
        image = np.zeros((200, 200, 3), np.uint8)
        def snapshot(self):
            return True, self.image.copy(), 1, self.generation, 100.0
        def release(self):
            pass

    class Pose:
        def predict(self, frame, **kwargs):
            return [Results(frame, "synthetic", {0: "person"},
                            boxes=torch.tensor([[10, 10, 60, 150, .9, 0]]),
                            keypoints=torch.zeros((1, 17, 3)))]

    def observe(self, frame, people, generation, now):
        assert len(people) == 1
        return [FaceObservation(f"track-{generation}-{people[0][0]}", .8, frame, .6)]

    capture = Capture()
    monkeypatch.setattr(FaceCapture, "observe", observe)
    monkeypatch.setattr(backend, "model_pose", Pose())
    monkeypatch.setattr(backend, "model_obj", object())
    saved = []
    monkeypatch.setattr(backend, "ALERT_DIR", tmp_path)

    def process(runtime):
        runtime["cap"] = capture
        with runtime["tracking"].lock:
            backend.process_camera(camera_id, runtime, 100, False, capture.image)
        response = admin.get("/observations")
        assert response.status_code == 200
        saved.append(response.json()[0])

    process(camera)
    process(camera)
    assert saved[0]["subject_key"] == saved[1]["subject_key"]
    replacement = None
    try:
        if change == "reload":
            replacement = backend.CameraManager()
            camera = replacement.cameras[camera_id]
        elif change == "enabled":
            assert admin.put(f"/cameras/{camera_id}/enabled", json={"enabled": False}).status_code == 200
            camera = backend.camera_manager.cameras[camera_id]
        elif change == "modes":
            assert admin.put(f"/cameras/{camera_id}/modes", json={"modes": ["face_capture", "lpr"]}).status_code == 200
        elif change == "resolution":
            capture.image = np.zeros((240, 240, 3), np.uint8)
        elif change == "reconnect":
            capture.generation = 2
        else:
            assert admin.put(f"/cameras/{camera_id}/mode-settings", json={"face_capture": {"max_images_per_track": 3}}).status_code == 200
        process(camera)
        same_track = change == "tuning"
        assert (saved[2]["subject_key"] == saved[0]["subject_key"]) is same_track
        assert (saved[2]["metadata"]["track_scope"] == saved[0]["metadata"]["track_scope"]) is same_track
        assert saved[0]["metadata"]["track_scope"] == saved[1]["metadata"]["track_scope"]
        assert len(admin.get("/observations").json()) == 3
    finally:
        if replacement:
            replacement.shutdown()
