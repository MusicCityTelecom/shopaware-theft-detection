import json
import sys
from types import ModuleType, SimpleNamespace

import numpy as np

from shopaware import migrations
from shopaware.analytics import FaceCapture, PlateReader
from shopaware.db import Database
from shopaware.mode_runtime import configure_helpers, configured_process_camera
from shopaware.mode_settings import parse_mode_settings


def test_schema5_camera_upgrades_additively_to_schema6_with_default_settings(tmp_path, monkeypatch):
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", {v: statements for v, statements in all_migrations.items() if v <= 5})
    path = tmp_path / "schema5.db"
    old = Database(path)
    old.insert_camera(
        camera_id="cam", name="Existing camera", rtsp_url="rtsp://host/live",
        username="", password_enc="", enabled=False,
    )
    with old.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert "mode_settings_json" not in {row[1] for row in conn.execute("PRAGMA table_info(cameras)").fetchall()}

    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    upgraded = Database(path)
    row = upgraded.get_camera("cam")
    assert row is not None
    assert row["mode_settings_json"] == "{}"
    assert parse_mode_settings(row["mode_settings_json"])["shoplifting"]["risk_threshold"] == 65.0
    with upgraded.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_lpr_ocr_applies_configured_confidence_and_plate_length(monkeypatch):
    fake = ModuleType("pytesseract")

    class Output:
        DICT = "DICT"

    fake.Output = Output
    fake.image_to_data = lambda *args, **kwargs: {
        "text": ["ABC", "ABC1234", "ABC12345"],
        "conf": ["99", "54", "80"],
    }
    monkeypatch.setitem(sys.modules, "pytesseract", fake)

    reader = PlateReader(min_ocr_confidence=.60, min_plate_chars=4, max_plate_chars=7)
    crop = np.full((30, 100, 3), 180, dtype=np.uint8)
    assert reader._ocr(crop) == ("", 0.0)

    fake.image_to_data = lambda *args, **kwargs: {"text": ["ABC1234"], "conf": ["81"]}
    plate, confidence = reader._ocr(crop)
    assert plate == "ABC1234"
    assert confidence == .81


def test_lpr_configured_duplicate_cooldown_controls_repeat_observations(monkeypatch):
    reader = PlateReader(cooldown_seconds=5)

    class Locator:
        def detectMultiScale(self, *args, **kwargs):
            return [(20, 40, 100, 25)]

    reader.locator = Locator()
    monkeypatch.setattr(reader, "_ocr", lambda crop: ("ABC1234", .90))
    frame = np.full((300, 500, 3), 160, dtype=np.uint8)
    vehicle = np.array([50, 50, 450, 250])
    assert len(reader.observe(frame, [vehicle], 0)) == 1
    assert reader.observe(frame, [vehicle], 4.9) == []
    assert len(reader.observe(frame, [vehicle], 5.1)) == 1


def test_face_capture_minimum_quality_rejects_blurry_crops_without_identity_matching():
    class Detector:
        def detectMultiScale(self, *args, **kwargs):
            return [(10, 10, 60, 60)]

    frame = np.full((240, 320, 3), 128, dtype=np.uint8)
    people = [(7, np.array([80, 20, 220, 220]))]

    strict = FaceCapture(cooldown_seconds=0, max_per_track=2, min_quality=.10)
    strict.detector = Detector()
    assert strict.observe(frame, people, generation=2, now=1) == []

    permissive = FaceCapture(cooldown_seconds=0, max_per_track=1, min_quality=0)
    permissive.detector = Detector()
    found = permissive.observe(frame, people, generation=2, now=1)
    assert len(found) == 1
    assert found[0].subject_key == "track-2-7"
    assert permissive.observe(frame, people, generation=2, now=2) == []


def test_shoplifting_threshold_window_and_cooldown_are_independent_camera_settings():
    settings = parse_mode_settings({
        "shoplifting": {
            "risk_threshold": 30,
            "risk_window_seconds": 2,
            "candidate_cooldown_seconds": 10,
            "loitering_seconds": 18,
        }
    })
    camera = {}
    configure_helpers(camera, settings)
    risk = camera["risk"]
    assert risk.observe(1, ["object_near_hand"], 0) is None
    assert risk.observe(1, ["merchandise_interaction"], 1) is None
    candidate = risk.observe(1, ["exit_zone_entry"], 1.5)
    assert candidate is not None
    assert candidate["risk_score"] == .40
    # The prior signals expire outside this camera's two-second scoring window.
    assert risk.observe(2, ["exit_zone_entry"], 10) is None
    assert risk.observe(2, ["merchandise_interaction"], 13) is None


def test_reconnect_generation_reapplies_saved_mode_helpers_before_processing():
    settings = parse_mode_settings({
        "shoplifting": {"risk_threshold": 77, "loitering_seconds": 23},
        "lpr": {"min_ocr_confidence": .66},
        "face_capture": {"max_images_per_track": 2},
        "vehicle_break_in": {"dwell_seconds": 25},
    })

    class DatabaseStub:
        def get_camera(self, camera_id):
            return {"mode_settings_json": json.dumps(settings)}

    class Context:
        generation = 1
        resolution = (20, 20)
        reset_calls = 0

        def reset(self, generation, resolution):
            self.generation = generation
            self.resolution = resolution
            self.reset_calls += 1

    class Capture:
        def snapshot(self):
            return True, np.zeros((10, 10, 3), dtype=np.uint8), 1, 2, 100.0

    context = Context()
    camera = {
        "tracking": context,
        "cap": Capture(),
        "roi_entry_times": {1: 1.0},
        "last_objects": [np.array([1, 1, 2, 2])],
        "last_detections": [{"class_id": 2}],
        "mode_settings": settings,
    }
    core = SimpleNamespace(
        database=DatabaseStub(), LOITERING_THRESHOLD=12.0,
        logger=SimpleNamespace(warning=lambda *args: None),
    )
    observed = {}

    def original(*args):
        observed["risk_threshold"] = camera["risk"].threshold
        observed["lpr_confidence"] = camera["plate_reader"].min_ocr_confidence
        observed["face_max"] = camera["face_capture"].max_per_track
        observed["breakin_dwell"] = camera["break_in"].dwell_seconds
        observed["loitering"] = camera["mode_settings"]["shoplifting"]["loitering_seconds"]
        observed["generation"] = context.generation
        return "processed"

    assert configured_process_camera(core, original, "cam", camera, 100, True, None) == "processed"
    assert context.reset_calls == 1
    assert camera["roi_entry_times"] == {}
    assert camera["last_objects"] == []
    assert camera["last_detections"] == []
    assert observed == {
        "risk_threshold": 77,
        "lpr_confidence": .66,
        "face_max": 2,
        "breakin_dwell": 25,
        "loitering": 23,
        "generation": 2,
    }
    assert core.LOITERING_THRESHOLD == 12.0
