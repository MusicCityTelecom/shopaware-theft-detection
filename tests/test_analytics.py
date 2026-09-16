import numpy as np
import pytest

from shopaware.analytics import FaceCapture, PlateReader, VehicleBreakInDetector, estimate_vehicle_color, normalize_modes


def test_camera_modes_are_validated_deduplicated_and_canonical():
    assert normalize_modes(["lpr", "shoplifting", "lpr"]) == ["shoplifting", "lpr"]
    with pytest.raises(ValueError, match="at least one"):
        normalize_modes([])
    with pytest.raises(ValueError, match="Unsupported"):
        normalize_modes(["identify_people"])


def test_vehicle_color_estimation_uses_broad_confidence_scored_labels():
    red = np.full((120, 180, 3), (0, 0, 255), dtype=np.uint8)
    black = np.zeros((120, 180, 3), dtype=np.uint8)
    assert estimate_vehicle_color(red)[0] == "red"
    assert estimate_vehicle_color(black)[0] == "black"


def test_plate_reader_deduplicates_ocr_candidates(monkeypatch):
    reader = PlateReader(cooldown_seconds=60)

    class Locator:
        def detectMultiScale(self, *args, **kwargs):
            return [(20, 40, 100, 25)]

    reader.locator = Locator()
    monkeypatch.setattr(reader, "_ocr", lambda crop: ("ABC1234", .82))
    frame = np.full((300, 500, 3), 160, dtype=np.uint8)
    box = np.array([50, 50, 450, 250])
    result = reader.observe(frame, [box], 100)
    assert len(result) == 1 and result[0].plate == "ABC1234"
    assert reader.observe(frame, [box], 110) == []


def test_face_capture_groups_only_a_continuous_camera_track():
    capture = FaceCapture(cooldown_seconds=1, max_per_track=2)

    class Detector:
        def detectMultiScale(self, *args, **kwargs):
            return [(10, 10, 60, 60)]

    capture.detector = Detector()
    frame = ((np.indices((240, 320)).sum(axis=0) % 2) * 255).astype(np.uint8)
    frame = np.stack([frame, frame, frame], axis=2)
    person = [(7, np.array([80, 20, 220, 220]))]
    first = capture.observe(frame, person, generation=2, now=1)
    second = capture.observe(frame, person, generation=2, now=3)
    assert first[0].subject_key == second[0].subject_key == "track-2-7"
    assert capture.observe(frame, person, generation=2, now=5) == []
    assert capture.observe(frame, person, generation=3, now=6)[0].subject_key == "track-3-7"


def test_vehicle_break_in_is_a_temporal_review_candidate():
    detector = VehicleBreakInDetector(quiet_seconds=90)
    vehicle = np.array([100, 100, 300, 250])
    person = np.array([80, 60, 180, 260])
    keypoints = np.zeros((17, 2), dtype=float)
    keypoints[9] = [120, 150]
    keypoints[10] = [130, 160]
    assert detector.observe([(4, person, keypoints)], [vehicle], 0) == []
    assert detector.observe([(4, person, keypoints)], [vehicle], 6) == []
    candidates = detector.observe([(4, person, keypoints)], [vehicle], 13)
    assert candidates[0]["event_type"] == "vehicle_break_in_candidate"
    assert "repeated_vehicle_access" in candidates[0]["metadata"]["signals"]
    assert detector.observe([(4, person, keypoints)], [vehicle], 14) == []
