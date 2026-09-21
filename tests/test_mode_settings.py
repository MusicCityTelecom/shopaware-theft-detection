import numpy as np
import pytest

from shopaware.analytics import FaceCapture, PlateReader, VehicleBreakInDetector
from shopaware.db import Database
from shopaware.mode_runtime import (
    legacy_mode_settings,
    persist_mode_settings,
    load_camera_settings,
    configure_helpers,
)
from shopaware.mode_settings import CameraModeSettingsInput, parse_mode_settings
from shopaware.risk import RiskEngine


def test_beta4_defaults_are_preserved_and_complete():
    settings = parse_mode_settings(None)
    assert settings["shoplifting"] == {
        "risk_threshold": 65.0,
        "risk_window_seconds": 10.0,
        "candidate_cooldown_seconds": 60.0,
        "loitering_seconds": 12.0,
    }
    assert settings["vehicle_break_in"]["required_access_interactions"] == 3
    assert settings["vehicle_break_in"]["candidate_cooldown_seconds"] == 90.0
    assert settings["lpr"]["min_ocr_confidence"] == 0.20
    assert settings["face_capture"]["max_images_per_track"] == 5


def test_migration_snapshots_effective_beta4_global_shoplifting_tuning(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPAWARE_RISK_THRESHOLD", "82")
    db = Database(tmp_path / "legacy.db")
    db.insert_camera(
        camera_id="cam", name="Legacy", rtsp_url="rtsp://host/live", username="",
        password_enc="", enabled=False,
    )
    effective = legacy_mode_settings(19.5)
    assert effective["shoplifting"]["risk_threshold"] == 82
    assert effective["shoplifting"]["loitering_seconds"] == 19.5
    snapshotted = load_camera_settings(db, "cam", 19.5)
    assert snapshotted == effective
    row = db.get_camera("cam")
    assert row is not None
    assert parse_mode_settings(row["mode_settings_json"]) == effective
    # Later global changes must not retroactively alter this camera.
    monkeypatch.setenv("SHOPAWARE_RISK_THRESHOLD", "40")
    assert load_camera_settings(db, "cam", 4) == effective
    assert parse_mode_settings(db.get_camera("cam")["mode_settings_json"]) == effective


def test_mode_settings_reject_unknown_and_inverted_plate_lengths():
    with pytest.raises(ValueError):
        parse_mode_settings({"lpr": {"min_plate_chars": 9, "max_plate_chars": 4}})
    with pytest.raises(ValueError):
        parse_mode_settings({"face_capture": {"recognize_people": True}})
    with pytest.raises(ValueError):
        parse_mode_settings("[]")


def test_configure_helpers_applies_each_mode_independently():
    settings = parse_mode_settings({
        "shoplifting": {"risk_threshold": 80, "risk_window_seconds": 18, "candidate_cooldown_seconds": 75, "loitering_seconds": 20},
        "vehicle_break_in": {"risk_threshold": 75, "dwell_seconds": 22, "required_access_interactions": 5, "access_interval_seconds": 2.5, "candidate_cooldown_seconds": 120},
        "lpr": {"observation_cooldown_seconds": 30, "min_ocr_confidence": .55, "min_plate_chars": 5, "max_plate_chars": 8},
        "face_capture": {"capture_cooldown_seconds": 12, "max_images_per_track": 3, "min_quality": .35},
    })
    camera = {}
    configure_helpers(camera, settings)
    assert isinstance(camera["risk"], RiskEngine)
    assert (camera["risk"].threshold, camera["risk"].window_seconds, camera["risk"].quiet_seconds) == (80, 18, 75)
    assert isinstance(camera["plate_reader"], PlateReader)
    assert camera["plate_reader"].min_ocr_confidence == .55
    assert camera["plate_reader"].min_plate_chars == 5
    assert isinstance(camera["face_capture"], FaceCapture)
    assert camera["face_capture"].max_per_track == 3
    assert camera["face_capture"].min_quality == .35
    assert isinstance(camera["break_in"], VehicleBreakInDetector)
    assert camera["break_in"].dwell_seconds == 22
    assert camera["break_in"].required_access_interactions == 5


def test_vehicle_break_in_custom_thresholds_change_candidate_timing():
    vehicle = np.array([100, 100, 300, 250])
    person = np.array([80, 60, 180, 260])
    keypoints = np.zeros((17, 2), dtype=float)
    keypoints[9] = [120, 150]
    keypoints[10] = [130, 160]

    conservative = VehicleBreakInDetector(
        risk_threshold=65, dwell_seconds=60, required_access_interactions=20,
        access_interval_seconds=1.5, quiet_seconds=90,
    )
    for now in (0, 5, 10, 15):
        assert conservative.observe([(4, person, keypoints)], [vehicle], now) == []

    sensitive = VehicleBreakInDetector(
        risk_threshold=50, dwell_seconds=2, required_access_interactions=2,
        access_interval_seconds=1, quiet_seconds=90,
    )
    assert sensitive.observe([(4, person, keypoints)], [vehicle], 0) == []
    candidate = sensitive.observe([(4, person, keypoints)], [vehicle], 2)
    assert candidate and candidate[0]["event_type"] == "vehicle_break_in_candidate"


def test_persisted_mode_settings_use_schema_6_and_survive_database_reopen(tmp_path):
    db = Database(tmp_path / "app.db")
    db.insert_camera(
        camera_id="cam", name="Parking", rtsp_url="rtsp://host/live", username="",
        password_enc="", enabled=False,
    )
    settings = parse_mode_settings({"lpr": {"min_ocr_confidence": .61}})
    persist_mode_settings(db, "cam", settings)
    reopened = Database(tmp_path / "app.db")
    row = reopened.get_camera("cam")
    assert row is not None
    assert parse_mode_settings(row["mode_settings_json"])["lpr"]["min_ocr_confidence"] == .61
    conn = reopened.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    finally:
        conn.close()


def test_pydantic_payload_has_no_unbounded_extra_fields():
    with pytest.raises(Exception):
        CameraModeSettingsInput.model_validate({"lpr": {"min_ocr_confidence": .5, "shell": "no"}})
