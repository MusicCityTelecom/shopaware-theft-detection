"""Per-camera mode tuning integration for the beta.5 application entrypoint.

The legacy backend remains importable for development/tests while this module
adds validated settings persistence and applies those settings to every camera
before inference. Existing cameras snapshot their effective beta.4 global
Shoplifting values during first beta.5 startup so migration does not silently
change a site that had tuned the prior global threshold.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from fastapi import HTTPException

from shopaware.analytics import FaceCapture, PlateReader, VehicleBreakInDetector
from shopaware.db import utc_now_iso
from shopaware.mode_settings import CameraModeSettingsInput, parse_mode_settings
from shopaware.risk import RiskEngine


def _legacy_settings(core: Any) -> dict[str, Any]:
    """Effective beta.4 behavior, including prior global Shoplifting tuning."""
    settings = parse_mode_settings(None)
    try:
        settings["shoplifting"]["risk_threshold"] = float(os.getenv("SHOPAWARE_RISK_THRESHOLD", "65"))
    except ValueError:
        settings["shoplifting"]["risk_threshold"] = 65.0
    try:
        settings["shoplifting"]["loitering_seconds"] = float(core.LOITERING_THRESHOLD)
    except (TypeError, ValueError, AttributeError):
        settings["shoplifting"]["loitering_seconds"] = 12.0
    # Re-validate any environment-derived value before persisting it.
    return parse_mode_settings(settings)


def _settings_from_row(core: Any, row: Any) -> dict[str, Any]:
    raw = row["mode_settings_json"]
    try:
        loaded = json.loads(raw or "{}") if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        loaded = raw
    # Schema 6 initializes existing/new rows to {}. Interpret that sentinel as
    # the effective beta.4 configuration until it is snapshotted explicitly.
    if loaded == {}:
        return _legacy_settings(core)
    return parse_mode_settings(raw)


def _runtime_settings(core: Any, camera_id: str, camera: dict[str, Any]) -> dict[str, Any]:
    settings = camera.get("mode_settings")
    if settings is not None:
        return settings
    row = core.database.get_camera(camera_id)
    if row is None:
        raise KeyError(camera_id)
    try:
        settings = _settings_from_row(core, row)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        core.logger.warning("Invalid mode settings for camera %s; beta.4 defaults restored: %s", camera_id, exc)
        settings = _legacy_settings(core)
    camera["mode_settings"] = settings
    return settings


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


def _prepare_generation(camera: dict[str, Any], settings: dict[str, Any]) -> None:
    """Reset per-generation state before the legacy processor can install defaults."""
    cap = camera.get("cap")
    context = camera.get("tracking")
    if cap is None or context is None:
        configure_helpers(camera, settings)
        return
    ok, frame, _, generation, _ = cap.snapshot()
    if ok and frame is not None:
        resolution = frame.shape[:2]
        if context.generation != generation or context.resolution != resolution:
            context.reset(generation, resolution)
            camera["roi_entry_times"].clear()
            camera["last_objects"] = []
            camera["last_detections"] = []
            configure_helpers(camera, settings, force=True)
            return
    configure_helpers(camera, settings)


def configured_process_camera(
    core: Any,
    original: Callable[..., Any],
    camera_id: str,
    camera: dict[str, Any],
    now: float,
    run_obj: bool,
    no_signal: Any,
) -> Any:
    settings = _runtime_settings(core, camera_id, camera)
    _prepare_generation(camera, settings)

    # The beta.4 monolith reads the loitering value from a module global. The
    # inference loop is serial across cameras, so scope the global to this call
    # and restore it immediately afterward.
    previous_loitering = core.LOITERING_THRESHOLD
    core.LOITERING_THRESHOLD = settings["shoplifting"]["loitering_seconds"]
    try:
        return original(camera_id, camera, now, run_obj, no_signal)
    finally:
        core.LOITERING_THRESHOLD = previous_loitering
        # If the underlying processor observed an unexpected generation change
        # between snapshots, it may have recreated beta.4 defaults. Repair the
        # helper objects before the next frame.
        configure_helpers(camera, settings)


def _persist_settings(core: Any, camera_id: str, settings: dict[str, Any]) -> None:
    conn = core.database.connect()
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


def _snapshot_empty_settings(core: Any, camera_id: str) -> dict[str, Any]:
    row = core.database.get_camera(camera_id)
    if row is None:
        raise KeyError(camera_id)
    settings = _settings_from_row(core, row)
    try:
        loaded = json.loads(row["mode_settings_json"] or "{}")
    except json.JSONDecodeError:
        loaded = None
    if loaded == {}:
        _persist_settings(core, camera_id, settings)
    return settings


def install(core: Any) -> None:
    """Install per-camera mode settings onto the imported ShopAware backend."""
    if getattr(core, "_beta5_mode_settings_installed", False):
        return

    original = core.process_camera

    def process_camera(camera_id: str, camera: dict[str, Any], now: float, run_obj: bool, no_signal: Any) -> Any:
        return configured_process_camera(core, original, camera_id, camera, now, run_obj, no_signal)

    core.process_camera = process_camera

    # Snapshot every migrated camera's effective beta.4 settings once so later
    # global configuration edits cannot retroactively alter its per-camera values.
    with core.camera_manager.lock:
        for camera_id, camera in core.camera_manager.cameras.items():
            settings = _snapshot_empty_settings(core, camera_id)
            camera["mode_settings"] = settings
            configure_helpers(camera, settings, force=True)

    # Cameras created after startup must also get a complete persisted settings
    # object instead of depending indefinitely on the schema's {} sentinel.
    original_add_camera = core.camera_manager.add_camera

    def add_camera(camera_input: Any) -> str:
        camera_id = original_add_camera(camera_input)
        settings = _snapshot_empty_settings(core, camera_id)
        with core.camera_manager.lock:
            runtime = core.camera_manager.cameras.get(camera_id)
            if runtime is not None:
                runtime["mode_settings"] = settings
                configure_helpers(runtime, settings, force=True)
        return camera_id

    core.camera_manager.add_camera = add_camera

    @core.app.get("/cameras/{camera_id}/mode-settings")
    def get_camera_mode_settings(camera_id: str):
        row = core.database.get_camera(camera_id)
        if row is None:
            raise HTTPException(404, "Camera not found")
        try:
            return _settings_from_row(core, row)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(500, "Camera mode settings are invalid") from exc

    @core.app.put("/cameras/{camera_id}/mode-settings")
    def put_camera_mode_settings(camera_id: str, payload: CameraModeSettingsInput):
        try:
            settings = parse_mode_settings(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            _persist_settings(core, camera_id, settings)
        except KeyError:
            raise HTTPException(404, "Camera not found") from None

        with core.camera_manager.lock:
            camera = core.camera_manager.cameras.get(camera_id)
        if camera is not None:
            context = camera["tracking"]
            with context.lock:
                camera["mode_settings"] = settings
                configure_helpers(camera, settings, force=True)
        return {"settings": settings, "restart_required": False}

    core._beta5_mode_settings_installed = True
