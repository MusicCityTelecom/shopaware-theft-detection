"""Native per-camera detector configuration and schema-6 settings persistence."""
from __future__ import annotations

import json
import os
from typing import Any

from shopaware.analytics import FaceCapture, PlateReader, VehicleBreakInDetector
from shopaware.db import Database, utc_now_iso
from shopaware.mode_settings import parse_mode_settings
from shopaware.risk import RiskEngine


def legacy_mode_settings(loitering_seconds: float) -> dict[str, Any]:
    """Effective beta.4 behavior, including prior global Shoplifting tuning."""
    settings = parse_mode_settings(None)
    try:
        settings["shoplifting"]["risk_threshold"] = float(os.getenv("SHOPAWARE_RISK_THRESHOLD", "65"))
    except ValueError:
        settings["shoplifting"]["risk_threshold"] = 65.0
    try:
        settings["shoplifting"]["loitering_seconds"] = float(loitering_seconds)
    except (TypeError, ValueError, AttributeError):
        settings["shoplifting"]["loitering_seconds"] = 12.0
    # Re-validate any environment-derived value before persisting it.
    return parse_mode_settings(settings)


def settings_from_row(row: Any, loitering_seconds: float) -> dict[str, Any]:
    raw = row["mode_settings_json"]
    try:
        loaded = json.loads(raw or "{}") if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        loaded = raw
    # Schema 6 initializes existing/new rows to {}. Interpret that sentinel as
    # the effective beta.4 configuration until it is snapshotted explicitly.
    if loaded == {}:
        return legacy_mode_settings(loitering_seconds)
    return parse_mode_settings(raw)


def _risk_matches(engine: Any, cfg: dict[str, Any]) -> bool:
    return (
        isinstance(engine, RiskEngine)
        and engine.threshold == cfg["risk_threshold"]
        and engine.window_seconds == cfg["risk_window_seconds"]
        and engine.quiet_seconds == cfg["candidate_cooldown_seconds"]
    )


def _plate_matches(reader: Any, cfg: dict[str, Any]) -> bool:
    return (
        isinstance(reader, PlateReader)
        and reader.cooldown_seconds == cfg["observation_cooldown_seconds"]
        and reader.min_ocr_confidence == cfg["min_ocr_confidence"]
        and reader.min_plate_chars == cfg["min_plate_chars"]
        and reader.max_plate_chars == cfg["max_plate_chars"]
    )


def _face_matches(capture: Any, cfg: dict[str, Any]) -> bool:
    return (
        isinstance(capture, FaceCapture)
        and capture.cooldown_seconds == cfg["capture_cooldown_seconds"]
        and capture.max_per_track == cfg["max_images_per_track"]
        and capture.min_quality == cfg["min_quality"]
    )


def _break_in_matches(detector: Any, cfg: dict[str, Any]) -> bool:
    return (
        isinstance(detector, VehicleBreakInDetector)
        and detector.quiet_seconds == cfg["candidate_cooldown_seconds"]
        and detector.risk_threshold == cfg["risk_threshold"]
        and detector.dwell_seconds == cfg["dwell_seconds"]
        and detector.required_access_interactions == cfg["required_access_interactions"]
        and detector.access_interval_seconds == cfg["access_interval_seconds"]
    )


def configure_helpers(camera: dict[str, Any], settings: dict[str, Any], *, force: bool = False) -> None:
    shop = settings["shoplifting"]
    lpr = settings["lpr"]
    face = settings["face_capture"]
    vehicle = settings["vehicle_break_in"]

    if force or not _risk_matches(camera.get("risk"), shop):
        camera["risk"] = RiskEngine(
            threshold=shop["risk_threshold"],
            window_seconds=shop["risk_window_seconds"],
            quiet_seconds=shop["candidate_cooldown_seconds"],
        )
    if force or not _plate_matches(camera.get("plate_reader"), lpr):
        camera["plate_reader"] = PlateReader(
            cooldown_seconds=lpr["observation_cooldown_seconds"],
            min_ocr_confidence=lpr["min_ocr_confidence"],
            min_plate_chars=lpr["min_plate_chars"],
            max_plate_chars=lpr["max_plate_chars"],
        )
    if force or not _face_matches(camera.get("face_capture"), face):
        camera["face_capture"] = FaceCapture(
            cooldown_seconds=face["capture_cooldown_seconds"],
            max_per_track=face["max_images_per_track"],
            min_quality=face["min_quality"],
        )
    if force or not _break_in_matches(camera.get("break_in"), vehicle):
        camera["break_in"] = VehicleBreakInDetector(
            quiet_seconds=vehicle["candidate_cooldown_seconds"],
            risk_threshold=vehicle["risk_threshold"],
            dwell_seconds=vehicle["dwell_seconds"],
            required_access_interactions=vehicle["required_access_interactions"],
            access_interval_seconds=vehicle["access_interval_seconds"],
        )


def persist_mode_settings(database: Database, camera_id: str, settings: dict[str, Any]) -> None:
    conn = database.connect()
    try:
        cur = conn.execute(
            "UPDATE cameras SET mode_settings_json=?, updated_at=? WHERE id=?",
            (json.dumps(settings, sort_keys=True), utc_now_iso(), camera_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise KeyError(camera_id)
    finally:
        conn.close()


def load_camera_settings(database: Database, camera_id: str, loitering_seconds: float) -> dict[str, Any]:
    row = database.get_camera(camera_id)
    if row is None:
        raise KeyError(camera_id)
    settings = settings_from_row(row, loitering_seconds)
    try:
        loaded = json.loads(row["mode_settings_json"] or "{}")
    except json.JSONDecodeError:
        loaded = None
    if loaded == {}:
        persist_mode_settings(database, camera_id, settings)
    return settings
