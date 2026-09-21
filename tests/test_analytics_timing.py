from types import SimpleNamespace

import numpy as np
import pytest

from shopaware.analytics import FaceCapture, PlateReader
from shopaware.mode_runtime import _legacy_settings, configured_process_camera
from shopaware.mode_settings import parse_mode_settings
from shopaware.risk import RiskEngine


@pytest.mark.parametrize("cooldown", [7200, 86400])
def test_lpr_cooldown_survives_hourly_cleanup(monkeypatch, cooldown):
    reader = PlateReader(cooldown_seconds=cooldown)
    reader.locator = SimpleNamespace(detectMultiScale=lambda *a, **kw: [(20, 40, 100, 25)])
    monkeypatch.setattr(reader, "_ocr", lambda crop: ("ABC1234", .9))
    frame = np.full((300, 500, 3), 160, np.uint8)
    vehicle = np.array([50, 50, 450, 250])
    assert len(reader.observe(frame, [vehicle], 0)) == 1
    assert reader.observe(frame, [], 3601) == []
    assert reader.observe(frame, [vehicle], cooldown - .1) == []
    assert len(reader.observe(frame, [vehicle], cooldown)) == 1
    reader.observe(frame, [], 2 * cooldown)
    assert reader.last_seen == {}


def face_scene(cooldown=1, max_per_track=1):
    capture = FaceCapture(cooldown_seconds=cooldown, max_per_track=max_per_track)
    capture.detector = SimpleNamespace(detectMultiScale=lambda *a, **kw: [(10, 10, 60, 60)])
    frame = ((np.indices((240, 320)).sum(axis=0) % 2) * 255).astype(np.uint8)
    return capture, np.stack([frame] * 3, axis=2), [(7, np.array([80, 20, 220, 220]))]


@pytest.mark.parametrize("face_visible", [True, False])
def test_face_cap_survives_ten_minutes_on_continuous_track(face_visible):
    capture, frame, person = face_scene()
    assert len(capture.observe(frame, person, generation=2, now=0)) == 1
    if not face_visible:
        capture.detector = SimpleNamespace(detectMultiScale=lambda *a, **kw: [])
    assert capture.observe(frame, person, generation=2, now=601) == []
    capture.detector = SimpleNamespace(detectMultiScale=lambda *a, **kw: [(10, 10, 60, 60)])
    assert capture.observe(frame, person, generation=2, now=602) == []
    assert capture.state["track-2-7"][1] == 1
    # Brief detection loss must not bypass the cap when the same track returns.
    capture.observe(frame, [], generation=2, now=603)
    assert capture.observe(frame, person, generation=2, now=604) == []
    # Expired, absent tracks are still removed; a new generation is independent.
    capture.observe(frame, [], generation=2, now=1205)
    assert capture.state == {}
    assert capture._last_seen == {}
    assert len(capture.observe(frame, person, generation=3, now=1206)) == 1


def test_face_cooldown_survives_cleanup_while_track_is_absent():
    capture, frame, person = face_scene(cooldown=1800, max_per_track=3)
    assert len(capture.observe(frame, person, generation=2, now=0)) == 1
    capture.observe(frame, [], generation=2, now=601)
    assert capture.observe(frame, person, generation=2, now=1799) == []
    assert len(capture.observe(frame, person, generation=2, now=1800)) == 1


@pytest.mark.parametrize("elapsed,expected", [(299, True), (300, True), (300.1, False)])
def test_risk_signal_lifetime_respects_full_scoring_window(elapsed, expected):
    risk = RiskEngine(threshold=25, window_seconds=300, quiet_seconds=60)
    assert risk.observe(1, ["object_near_hand"], 100) is None
    assert risk.observe(1, [], 221) is None
    result = risk.observe(1, ["merchandise_interaction"], 100 + elapsed)
    assert (result is not None) is expected
    if expected:
        assert result["risk_score"] == .25


def test_inference_does_not_change_defaults_seen_by_new_cameras():
    settings = parse_mode_settings({"shoplifting": {"loitering_seconds": 27}})
    core = SimpleNamespace(LOITERING_THRESHOLD=12)
    camera = {"mode_settings": settings}

    def during_inference(*args):
        assert _legacy_settings(core)["shoplifting"]["loitering_seconds"] == 12
        assert camera["mode_settings"]["shoplifting"]["loitering_seconds"] == 27

    configured_process_camera(core, during_inference, "camera", camera, 0, True, None)
