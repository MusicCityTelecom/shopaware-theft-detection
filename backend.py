from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets as token_secrets
import threading
import time
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Suppress native codec diagnostics that can include credential-bearing URIs.
os.environ.setdefault('OPENCV_LOG_LEVEL', 'SILENT')
os.environ.setdefault('OPENCV_FFMPEG_LOGLEVEL', '-8')

import cv2
import numpy as np
import psutil
import torch
from dotenv import load_dotenv
from fastapi import Request, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, Response
from pydantic import BaseModel, Field, model_validator
from ultralytics import YOLO
from shopaware import __version__
from shopaware.models import model_path
from shopaware.media import media_writer
from shopaware.analytics import (
    CAMERA_MODES,
    DEFAULT_CAMERA_MODES,
    VEHICLE_CLASS_IDS,
    FaceCapture,
    PlateReader,
    VehicleBreakInDetector,
    normalize_modes,
)

from shopaware.ingest import ThreadedCamera
from shopaware.tracking import CameraTrackingContext
from shopaware.alerts.base import AlertEvent, AlertDispatcher
from shopaware.alerts.smtp import SMTPProvider
from shopaware.settings import SettingsStore, SettingsInput
from shopaware.training_api import training_router
from shopaware.storage import RetentionManager
from shopaware.zones import Zone
from shopaware.risk import RiskEngine
from shopaware.auth import AuthService, COOKIE
from shopaware.access import AccessControl
from shopaware.access_api import access_router
from shopaware.db import Database
from shopaware.recording import RollingClipRecorder
from shopaware.security import (
    SecretStore,
    build_runtime_url,
    clean_camera_url,
    masked_camera_url,
    redact,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger('shopaware.backend')

load_dotenv()

settings_store = SettingsStore(Database(Path(os.getenv('SHOPAWARE_DB_PATH', 'shopaware.db'))), SecretStore())
settings_store.load_environment()

APP_NAME = "ShopAware"
DB_PATH = Path(os.getenv("SHOPAWARE_DB_PATH", "shopaware.db"))
ALERT_DIR = Path(os.getenv("SHOPAWARE_ALERT_DIR", "alerts"))
INCIDENT_DIR = Path(os.getenv("SHOPAWARE_INCIDENT_DIR", "incidents"))
ALERT_DIR.mkdir(parents=True, exist_ok=True)
INCIDENT_DIR.mkdir(parents=True, exist_ok=True)

DETECTION_MODEL = os.getenv("SHOPAWARE_DETECTION_MODEL", "yolo26n.pt")
POSE_MODEL = os.getenv("SHOPAWARE_POSE_MODEL", "yolo26n-pose.pt")
SPECIALIZED_MODEL = os.getenv("SHOPAWARE_SPECIALIZED_MODEL", "shoplifting.pt")
ENABLE_SPECIALIZED_MODEL = os.getenv("SHOPAWARE_ENABLE_SPECIALIZED_MODEL", "true").lower() in {"1", "true", "yes", "on"}

ALERT_COOLDOWN = float(os.getenv("SHOPAWARE_ALERT_COOLDOWN", "8"))
LOITERING_THRESHOLD = float(os.getenv("SHOPAWARE_LOITERING_THRESHOLD", "12"))
OBJECT_INFERENCE_EVERY_N_FRAMES = max(1, int(os.getenv("SHOPAWARE_OBJECT_INFERENCE_EVERY_N_FRAMES", "5")))
JPEG_QUALITY = min(95, max(30, int(os.getenv("SHOPAWARE_JPEG_QUALITY", "65"))))

PRE_EVENT_SECONDS = max(1.0, float(os.getenv("SHOPAWARE_PRE_EVENT_SECONDS", "15")))
POST_EVENT_SECONDS = max(1.0, float(os.getenv("SHOPAWARE_POST_EVENT_SECONDS", "30")))
RECORDING_FPS = max(1.0, float(os.getenv("SHOPAWARE_RECORDING_FPS", "6")))
RECORDING_JPEG_QUALITY = min(95, max(35, int(os.getenv("SHOPAWARE_RECORDING_JPEG_QUALITY", "72"))))

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "SHOPAWARE_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

database = Database(DB_PATH)
secrets = SecretStore()
auth = AuthService(database)
COOKIE_SECURE = os.getenv("SHOPAWARE_COOKIE_SECURE", "true").lower() == "true"


class CameraInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    rtsp_url: str = Field(min_length=4, max_length=2048)
    username: str = Field(default="", max_length=512)
    password: str = Field(default="", max_length=1024)
    enabled: bool = True
    group_id: str | None = Field(default=None, min_length=1, max_length=128)
    modes: list[str] = Field(default_factory=lambda: list(DEFAULT_CAMERA_MODES), min_length=1, max_length=len(CAMERA_MODES))

    @model_validator(mode="after")
    def validate_url(self) -> "CameraInput":
        self.rtsp_url = clean_camera_url(self.rtsp_url)
        self.modes = normalize_modes(self.modes)
        return self


class CameraModesInput(BaseModel):
    modes: list[str] = Field(min_length=1, max_length=len(CAMERA_MODES))

    @model_validator(mode="after")
    def validate_modes(self) -> "CameraModesInput":
        self.modes = normalize_modes(self.modes)
        return self


class RoiInput(BaseModel):
    points: list[list[int]] = Field(default_factory=list)


class ReviewInput(BaseModel):
    status: str
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_status(self) -> "ReviewInput":
        allowed = {"needs_review", "confirmed", "false_alarm", "dismissed"}
        if self.status not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return self


def on_clip_complete(incident_id: str, path: Path) -> None:
    if database.set_incident_clip(incident_id, str(path)):
        logger.info(f"Incident clip ready: {incident_id} -> {path.name}")
    else:
        logger.warning(f"Incident clip completed for unknown incident: {incident_id}")


def on_clip_error(incident_id: str, exc: Exception) -> None:
    database.set_media_status(incident_id, "failed")
    logger.error(f"Incident clip failed for {incident_id}: {redact(str(exc))}")


def load_zones(camera_id: str) -> list[Zone]:
    conn = database.connect()
    try:
        rows = conn.execute('SELECT * FROM zones WHERE camera_id=?', (camera_id,)).fetchall()
        return [Zone(id=r['id'], name=r['name'], type=r['type'], points=json.loads(r['points_json']), enabled=bool(r['enabled'])) for r in rows]
    finally:
        conn.close()


class CameraManager:
    def __init__(self):
        self.cameras: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()
        self.load_cameras()

    def _make_recorder(self, camera_id: str) -> RollingClipRecorder:
        return RollingClipRecorder(
            camera_id=camera_id,
            output_dir=INCIDENT_DIR,
            pre_seconds=PRE_EVENT_SECONDS,
            post_seconds=POST_EVENT_SECONDS,
            sample_fps=RECORDING_FPS,
            jpeg_quality=RECORDING_JPEG_QUALITY,
            on_complete=on_clip_complete,
            on_error=on_clip_error,
        )

    def _row_to_runtime(self, row: Any) -> dict[str, Any]:
        password = secrets.decrypt(row["password_enc"])
        runtime_url = build_runtime_url(row["rtsp_url"], row["username"], password)
        recorder = self._make_recorder(row["id"])
        try:
            modes = normalize_modes(json.loads(row["modes_json"] or "[]"))
        except (KeyError, TypeError, json.JSONDecodeError, ValueError):
            modes = list(DEFAULT_CAMERA_MODES)
        return {
            "id": row["id"],
            "group_id": row['group_id'],
            "access_epoch": row['access_epoch'],
            "name": row["name"],
            "rtsp_url": row["rtsp_url"],
            "username": row["username"],
            "has_password": bool(password),
            "roi_points": json.loads(row["roi_json"] or "[]"),
            "enabled": bool(row["enabled"]),
            "modes": modes,
            "cap": ThreadedCamera(runtime_url, on_frame=recorder.push) if row["enabled"] else None,
            "runtime_url": runtime_url,
            "recorder": recorder,
            "tracking": CameraTrackingContext(),
            "risk": RiskEngine(threshold=float(os.getenv("SHOPAWARE_RISK_THRESHOLD", "65"))),
            "zones": load_zones(row["id"]),
            "last_inference_at": 0.0,
            "last_sequence": -1,
            "inference_fps": float(os.getenv("SHOPAWARE_INFERENCE_FPS", "5")),
            "last_alert_time": 0.0,
            "last_objects": [],
            "roi_entry_times": {},
            "plate_reader": PlateReader(),
            "face_capture": FaceCapture(),
            "break_in": VehicleBreakInDetector(),
            "last_detections": [],
        }

    def load_cameras(self) -> None:
        with self.lock:
            for row in database.list_cameras():
                try:
                    self.cameras[row["id"]] = self._row_to_runtime(row)
                except Exception as exc:
                    raise RuntimeError(f"Camera {row['id']} could not be initialized; check credential key and configuration") from None

    def add_camera(self, camera: CameraInput) -> str:
        camera_id = str(uuid.uuid4())
        clean_url = clean_camera_url(camera.rtsp_url)
        database.insert_camera(
            camera_id=camera_id,
            name=camera.name,
            rtsp_url=clean_url,
            username=camera.username,
            password_enc=secrets.encrypt(camera.password),
            enabled=camera.enabled,
            group_id=camera.group_id,
        )
        try:
            database.set_camera_modes(camera_id, camera.modes)
            row = database.get_camera(camera_id)
            with self.lock:
                self.cameras[camera_id] = self._row_to_runtime(row)
        except Exception:
            database.delete_camera(camera_id)
            raise
        return camera_id

    def remove_camera(self, camera_id: str) -> bool:
        with self.lock:
            existing = self.cameras.pop(camera_id, None)
        if existing:
            existing["tracking"].close()
            if existing.get("cap"):
                existing["cap"].release()
            existing["recorder"].force_finalize_all()
        return database.delete_camera(camera_id)

    def set_roi(self, camera_id: str, points: list[list[int]]) -> None:
        normalized = [[int(p[0]), int(p[1])] for p in points if len(p) >= 2]
        if not database.set_camera_roi(camera_id, normalized):
            raise KeyError(camera_id)
        with self.lock:
            if camera_id in self.cameras:
                self.cameras[camera_id]["roi_points"] = normalized

    def start_recording(self, camera_id: str, incident_id: str, trigger_at: float) -> bool:
        with self.lock:
            cam = self.cameras.get(camera_id)
            if not cam:
                return False
            recorder: RollingClipRecorder | None = cam.get("recorder")
            return bool(recorder and recorder.start_incident(incident_id, trigger_at))

    def push_evidence_frame(self, camera_id: str, frame: np.ndarray, timestamp: float) -> None:
        with self.lock:
            cam = self.cameras.get(camera_id)
            recorder: RollingClipRecorder | None = cam.get("recorder") if cam else None
        if recorder:
            recorder.push(frame, timestamp)

    def list_safe(self) -> list[dict[str, Any]]:
        with self.lock:
            result = []
            for camera_id, cam in self.cameras.items():
                cap: ThreadedCamera | None = cam.get("cap")
                recorder: RollingClipRecorder | None = cam.get("recorder")
                status = "disabled"
                if cam.get("enabled"):
                    status = "active" if cap and cap.healthy() else "connecting"
                result.append(
                    {
                        "id": camera_id,
                        "name": cam["name"],
                        "rtsp_url": cam["rtsp_url"],
                        "username": cam["username"],
                        "has_password": cam["has_password"],
                        "source": masked_camera_url(
                            cam["rtsp_url"],
                            cam["username"],
                            cam["has_password"],
                        ),
                        "status": status if status in {"disabled", "active"} else (cap.status if cap else "error"),
                        "last_frame_at": cap.last_frame_at if cap else None,
                        "enabled": cam["enabled"],
                        "modes": list(cam.get("modes", DEFAULT_CAMERA_MODES)),
                        "roi_points": cam.get("roi_points", []),
                        "recording": recorder.stats() if recorder else None,
                    }
                )
            return result

    def get_runtime_items(self) -> list[tuple[str, dict[str, Any]]]:
        with self.lock:
            return list(self.cameras.items())

    def shutdown(self) -> None:
        for _, cam in self.get_runtime_items():
            cam["tracking"].close()
            if cam.get("cap"):
                cam["cap"].release()
            recorder: RollingClipRecorder | None = cam.get("recorder")
            if recorder:
                recorder.force_finalize_all()
            if cam.get("cap"):
                cam["cap"].release()


camera_manager = CameraManager()
camera_lifecycle_lock = threading.RLock()
latest_frame: dict[str, Any] | None = None
latest_frame_lock = threading.Lock()
models_lock = threading.Lock()
model_pose: YOLO | None = None
model_obj: YOLO | None = None
model_is_specialized = False
model_load_error = ""


def check_reaching(keypoints: np.ndarray, roi_poly: list[list[int]]) -> tuple[bool, str | None]:
    if len(keypoints) < 11 or len(roi_poly) < 3:
        return False, None
    roi = np.array(roi_poly, dtype=np.int32)
    for hand, idx in (("LEFT", 9), ("RIGHT", 10)):
        wrist = keypoints[idx]
        if wrist[0] > 0 and wrist[1] > 0:
            if cv2.pointPolygonTest(roi, (int(wrist[0]), int(wrist[1])), False) >= 0:
                return True, hand
    return False, None


def check_object_in_hand(keypoints: np.ndarray, object_boxes: list[np.ndarray], hand: str) -> bool:
    if len(keypoints) < 11:
        return False
    wrist = keypoints[9] if hand == "LEFT" else keypoints[10]
    if wrist[0] <= 0 or wrist[1] <= 0:
        return False

    for box in object_boxes:
        x1, y1, x2, y2 = box[:4]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        distance = float(np.hypot(wrist[0] - cx, wrist[1] - cy))
        if distance < 120 or (x1 < wrist[0] < x2 and y1 < wrist[1] < y2):
            return True
    return False


def check_concealment(keypoints: np.ndarray, hand: str) -> bool:
    if len(keypoints) < 13:
        return False
    left_hip = keypoints[11]
    right_hip = keypoints[12]
    wrist = keypoints[9] if hand == "LEFT" else keypoints[10]
    if wrist[0] <= 0 or left_hip[0] <= 0 or right_hip[0] <= 0:
        return False

    hip_center = (left_hip + right_hip) / 2
    distance = float(np.linalg.norm(wrist - hip_center))
    hip_width = float(abs(left_hip[0] - right_hip[0]))
    threshold = max(hip_width * 1.5, 100.0)
    return distance < threshold


def check_bending(keypoints: np.ndarray) -> bool:
    if len(keypoints) < 12:
        return False
    shoulder = keypoints[5]
    hip = keypoints[11]
    if shoulder[1] <= 0 or hip[1] <= 0:
        return False
    return (hip[1] - shoulder[1]) < 50


def trigger_incident(
    camera_id: str,
    camera_name: str,
    event_type: str,
    message: str,
    risk_score: float,
    frame: np.ndarray,
    trigger_at: float,
    metadata: dict[str, Any] | None = None,
) -> str:
    incident_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    filename = ALERT_DIR / f"incident_{camera_id}_{timestamp}.jpg"
    if not cv2.imwrite(str(filename), frame):
        raise RuntimeError("Incident snapshot could not be written")

    database.insert_incident(
        incident_id=incident_id,
        camera_id=camera_id,
        camera_name=camera_name,
        event_type=event_type,
        message=message,
        risk_score=risk_score,
        snapshot_path=str(filename),
        metadata={"trigger_at": trigger_at, **(metadata or {})},
    )

    if not camera_manager.start_recording(camera_id, incident_id, trigger_at):
        database.set_media_status(incident_id, "unavailable")

    if not alert_dispatcher.enqueue(AlertEvent(incident_id, camera_name, event_type, message, risk_score, filename)):
        set_alert_status(incident_id, 'queue_full')
    return incident_id


def save_observation(
    camera_id: str,
    camera_name: str,
    mode: str,
    subject_key: str,
    label_text: str,
    confidence: float,
    crop: np.ndarray,
    metadata: dict[str, Any] | None = None,
) -> str:
    observation_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    filename = ALERT_DIR / f"observation_{mode}_{camera_id}_{timestamp}.jpg"
    if crop.size == 0 or not cv2.imwrite(str(filename), crop):
        raise RuntimeError("Analytics snapshot could not be written")
    try:
        database.insert_observation(
            observation_id=observation_id,
            camera_id=camera_id,
            camera_name=camera_name,
            mode=mode,
            subject_key=subject_key,
            label_text=label_text,
            confidence=confidence,
            snapshot_path=str(filename),
            metadata=metadata,
        )
    except Exception:
        filename.unlink(missing_ok=True)
        raise
    return observation_id


def set_alert_status(incident_id: str, status: str) -> None:
    conn = database.connect()
    try:
        conn.execute('UPDATE incidents SET alert_status=? WHERE id=?', (status, incident_id))
        conn.commit()
    finally:
        conn.close()


alert_dispatcher = AlertDispatcher(SMTPProvider(), set_alert_status)


def load_models() -> None:
    global model_pose, model_obj, model_is_specialized, model_load_error
    with models_lock:
        if model_pose is not None:
            return
        try:
            logger.info(f"Loading ShopAware pose model: {redact(POSE_MODEL)}")
            model_pose = YOLO(model_path(POSE_MODEL))

            model_is_specialized = False
            if ENABLE_SPECIALIZED_MODEL and Path(model_path(SPECIALIZED_MODEL)).exists():
                logger.info(f"Loading specialized activity model: {redact(SPECIALIZED_MODEL)}")
                model_obj = YOLO(model_path(SPECIALIZED_MODEL))
                model_is_specialized = True
            else:
                logger.info(f"Loading ShopAware detection model: {redact(DETECTION_MODEL)}")
                model_obj = YOLO(model_path(DETECTION_MODEL))
            model_load_error = ""
        except Exception as exc:
            model_load_error = redact(str(exc))
            model_pose = None
            model_obj = None
            raise


def process_camera(camera_id: str, cam: dict[str, Any], now: float,
                   run_obj: bool, no_signal: np.ndarray) -> dict[str, str] | None:
    """Called with this camera's context lock held through inference and events."""
    context = cam["tracking"]
    cap = cam["cap"]
    ret, frame, sequence, generation, captured_at = cap.snapshot()
    cam["last_sequence"] = sequence
    if not ret or frame is None:
        encode_frame = no_signal.copy()
    else:
        modes = set(cam.get("modes", DEFAULT_CAMERA_MODES))
        shoplifting_enabled = "shoplifting" in modes
        resolution = frame.shape[:2]
        if context.generation != generation or context.resolution != resolution:
            context.reset(generation, resolution)
            cam["roi_entry_times"].clear()
            cam["last_objects"] = []
            cam["last_detections"] = []
            cam["risk"] = RiskEngine(threshold=float(os.getenv("SHOPAWARE_RISK_THRESHOLD", "65")))
            cam["plate_reader"] = PlateReader()
            cam["face_capture"] = FaceCapture()
            cam["break_in"] = VehicleBreakInDetector()
        # Preserve original evidence before annotations are drawn.
        # Evidence is sampled by capture before inference/annotations.
        now = captured_at

        assert model_pose is not None
        assert model_obj is not None

        inference_frame = frame.copy()
        for zone in cam['zones']:
            if zone.enabled and zone.type == 'ignore':
                polygon = np.array([(x * frame.shape[1], y * frame.shape[0]) for x, y in zone.points], dtype=np.int32)
                cv2.fillPoly(inference_frame, [polygon], (0, 0, 0))
        pose_results = model_pose.predict(inference_frame, verbose=False, classes=[0], conf=0.1)
        pose_results = [context.update(pose_results[0], now)]
        detected_objects: list[np.ndarray] = cam.get("last_objects", [])
        detections: list[dict[str, Any]] = cam.get("last_detections", [])
        vehicle_boxes = [item["box"] for item in detections if item["class_id"] in VEHICLE_CLASS_IDS]

        if run_obj:
            detected_objects = []
            detections = []
            vehicle_boxes = []
            obj_results = model_obj(inference_frame, verbose=False, conf=0.30)
            if obj_results:
                boxes = obj_results[0].boxes.xyxy.cpu().numpy().astype(int)
                classes = obj_results[0].boxes.cls.cpu().numpy().astype(int)
                confidences = obj_results[0].boxes.conf.cpu().numpy()

                if model_is_specialized and shoplifting_enabled:
                    for box, cls_id, confidence in zip(boxes, classes, confidences):
                        class_name = str(model_obj.names[int(cls_id)]).lower()
                        if any(token in class_name for token in ("shoplift", "suspicious", "theft", "conceal")):
                            cv2.rectangle(frame, tuple(box[:2]), tuple(box[2:]), (0, 0, 255), 3)
                            cv2.putText(
                                frame,
                                f"REVIEW: {class_name} {confidence:.2f}",
                                (int(box[0]), max(20, int(box[1]) - 10)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.65,
                                (0, 0, 255),
                                2,
                            )
                            candidate = cam['risk'].observe(-1, ['specialized_model_activity'], now)
                            if candidate:
                                trigger_incident(
                                    camera_id,
                                    cam["name"],
                                    "specialized_model_candidate",
                                    f"Specialized activity model produced class '{class_name}'.",
                                    candidate["risk_score"],
                                    frame,
                                    now,
                                    {
                                        **candidate["metadata"],
                                        "class_name": class_name,
                                        "confidence": float(confidence),
                                    },
                                )
                                cam["last_alert_time"] = now
                elif not model_is_specialized:
                    # Upstream-compatible COCO classes used only as a weak interaction signal.
                    target_classes = {
                        24, 25, 26, 28, 39, 40, 41, 42, 43,
                        67, 73, 74, 75, 76, 77, 78, 79,
                    }
                    for box, cls_id, confidence in zip(boxes, classes, confidences):
                        cls_id = int(cls_id)
                        detections.append({"box": box, "class_id": cls_id, "confidence": float(confidence)})
                        if cls_id in VEHICLE_CLASS_IDS:
                            vehicle_boxes.append(box)
                        if shoplifting_enabled and cls_id in target_classes:
                            detected_objects.append(box)
                            label = f"ITEM {model_obj.names[cls_id]} {confidence:.2f}"
                            cv2.rectangle(frame, tuple(box[:2]), tuple(box[2:]), (0, 165, 255), 2)
                            cv2.putText(
                                frame,
                                label,
                                (int(box[0]), max(20, int(box[1]) - 5)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.45,
                                (0, 165, 255),
                                1,
                            )
                    cam["last_objects"] = detected_objects
                    cam["last_detections"] = detections

        people_for_face: list[tuple[int, np.ndarray]] = []
        people_for_break_in: list[tuple[int, np.ndarray, np.ndarray]] = []
        if pose_results and pose_results[0].boxes.id is not None:
            person_boxes = pose_results[0].boxes.xyxy.cpu().numpy().astype(int)
            track_ids = pose_results[0].boxes.id.cpu().numpy().astype(int)
            keypoints_all = (
                pose_results[0].keypoints.xy.cpu().numpy()
                if pose_results[0].keypoints is not None
                else []
            )

            for idx, track_id in enumerate(track_ids):
                box = person_boxes[idx]
                keypoints = (
                    keypoints_all[idx]
                    if len(keypoints_all) > idx
                    else np.empty((0, 2))
                )
                if pose_results[0].keypoints is not None and pose_results[0].keypoints.conf is not None:
                    confidence = pose_results[0].keypoints.conf[idx].cpu().numpy()
                    keypoints = keypoints.copy()
                    keypoints[confidence < 0.5] = 0
                people_for_face.append((int(track_id), box))
                people_for_break_in.append((int(track_id), box, keypoints))
                if not shoplifting_enabled:
                    continue
                state = context.person(int(track_id), now)
                point = (float((box[0] + box[2]) / 2 / frame.shape[1]), float(box[3] / frame.shape[0]))
                if any(z.type == 'ignore' and z.contains(point) for z in cam['zones']):
                    continue
                zone_signals = []
                occupied = set()
                for zone in cam['zones']:
                    if zone.contains(point):
                        occupied.add(zone.id)
                        if zone.id not in state.zone_entries:
                            state.zone_entries[zone.id] = now
                            zone_signals.append({'merchandise': 'merchandise_zone_entry',
                                'checkout': 'checkout_zone_entry', 'exit': 'exit_zone_entry'}.get(zone.type, ''))
                        elif now - state.zone_entries[zone.id] >= LOITERING_THRESHOLD:
                            zone_signals.append('excessive_dwell')
                    if zone.enabled and zone.type in {'merchandise', 'restricted'}:
                        for wrist in keypoints[9:11]:
                            if wrist[0] > 0 and wrist[1] > 0 and zone.contains((float(wrist[0]/frame.shape[1]),float(wrist[1]/frame.shape[0]))):
                                zone_signals.append('merchandise_interaction' if zone.type == 'merchandise' else 'restricted_zone_interaction')
                state.zone_entries = {key: at for key,at in state.zone_entries.items() if key in occupied}
                zone_candidate = cam['risk'].observe(int(track_id), [v for v in zone_signals if v], now)
                if zone_candidate:
                    trigger_incident(camera_id, cam['name'], zone_candidate['event_type'],
                        'Multiple activity signals require human review.', zone_candidate['risk_score'], frame, now, zone_candidate['metadata'])

                left_has_obj = check_object_in_hand(keypoints, detected_objects, "LEFT")
                right_has_obj = check_object_in_hand(keypoints, detected_objects, "RIGHT")
                current_holding = left_has_obj or right_has_obj
                holding_hand = "LEFT" if left_has_obj else "RIGHT" if right_has_obj else None

                if current_holding:
                    hand_candidate = cam["risk"].observe(int(track_id), ["object_near_hand"], now)
                    if hand_candidate:
                        trigger_incident(camera_id, cam['name'], hand_candidate['event_type'],
                            'Multiple activity signals require human review.', hand_candidate['risk_score'], frame, now, hand_candidate['metadata'])
                    state.holding_object = True
                    state.holding_hand = holding_hand
                    state.last_holding_time = now
                    cv2.putText(
                        frame,
                        f"ITEM INTERACTION ({holding_hand})",
                        (int(box[0]), max(20, int(box[1]) - 55)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        (255, 255, 0),
                        2,
                    )

                if state.holding_object and not current_holding:
                    age = now - state.last_holding_time
                    if (
                        age < 3.0
                        and state.holding_hand
                        and check_concealment(keypoints, state.holding_hand)
                    ):
                        cv2.rectangle(frame, tuple(box[:2]), tuple(box[2:]), (0, 0, 255), 3)
                        cv2.putText(
                            frame,
                            "SUSPECTED CONCEALMENT - REVIEW",
                            (int(box[0]), max(20, int(box[1]) - 80)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.65,
                            (0, 0, 255),
                            2,
                        )
                        candidate = cam['risk'].observe(int(track_id),
                            ['object_disappearance', 'hand_to_waist', 'concealment_candidate'], now)
                        if candidate:
                            trigger_incident(
                                camera_id,
                                cam["name"],
                                "suspected_concealment",
                                (
                                    "An item interaction was followed by hand movement "
                                    "near the hip/waist region. Human review required."
                                ),
                                candidate["risk_score"],
                                frame,
                                now,
                                {
                                    **candidate["metadata"],
                                    "track_id": int(track_id),
                                    "hand": state.holding_hand,
                                    "heuristic": "upstream_item_disappearance_plus_hip_proximity",
                                },
                            )
                            cam["last_alert_time"] = now
                        state.holding_object = False
                        state.holding_hand = None
                    elif age >= 3.0:
                        state.holding_object = False
                        state.holding_hand = None

                roi = cam.get("roi_points", [])
                reaching, _ = check_reaching(keypoints, roi)
                if reaching:
                    cv2.putText(
                        frame,
                        "ROI INTERACTION",
                        (int(box[0]), max(20, int(box[1]) - 30)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        (0, 0, 255),
                        2,
                    )

                center_x = int((box[0] + box[2]) / 2)
                center_y = int((box[1] + box[3]) / 2)
                inside = (
                    len(roi) >= 3
                    and cv2.pointPolygonTest(
                        np.array(roi, dtype=np.int32),
                        (center_x, center_y),
                        False,
                    ) >= 0
                )
                if inside:
                    entry_times: dict[int, float] = cam["roi_entry_times"]
                    entry_times.setdefault(int(track_id), now)
                    dwell = now - entry_times[int(track_id)]
                    if dwell >= LOITERING_THRESHOLD:
                        cv2.putText(
                            frame,
                            f"DWELL {dwell:.1f}s",
                            (int(box[0]), int(box[3]) + 20),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 165, 255),
                            2,
                        )
                else:
                    cam["roi_entry_times"].pop(int(track_id), None)

                if check_bending(keypoints):
                    cv2.putText(
                        frame,
                        "BENDING",
                        (int(box[0]), int(box[3]) + 40),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        (255, 0, 0),
                        1,
                    )

        if "vehicle_break_in" in modes and not model_is_specialized:
            parking_zones = [zone for zone in cam['zones'] if zone.enabled and zone.type == 'parking']
            scoped_vehicles = vehicle_boxes
            if parking_zones:
                scoped_vehicles = [box for box in vehicle_boxes if any(zone.contains((
                    float((box[0] + box[2]) / 2 / frame.shape[1]),
                    float((box[1] + box[3]) / 2 / frame.shape[0]),
                )) for zone in parking_zones)]
            for candidate in cam["break_in"].observe(people_for_break_in, scoped_vehicles, now):
                trigger_incident(
                    camera_id,
                    cam["name"],
                    candidate["event_type"],
                    "Person and vehicle interaction patterns require human review; this is not proof of a break-in.",
                    candidate["risk_score"],
                    frame,
                    now,
                    candidate["metadata"],
                )

        if "lpr" in modes and run_obj and not model_is_specialized:
            for observation in cam["plate_reader"].observe(inference_frame, vehicle_boxes, now):
                save_observation(
                    camera_id,
                    cam["name"],
                    "lpr",
                    observation.plate,
                    observation.plate,
                    observation.confidence,
                    observation.crop,
                    {
                        "ocr_kind": "tesseract_candidate",
                        "vehicle_color": observation.vehicle_color,
                        "vehicle_color_confidence": observation.color_confidence,
                        "vehicle_make": None,
                        "vehicle_model": None,
                    },
                )

        if "face_capture" in modes:
            for observation in cam["face_capture"].observe(
                inference_frame, people_for_face, generation, now
            ):
                save_observation(
                    camera_id,
                    cam["name"],
                    "face_capture",
                    f"{camera_id}:{observation.subject_key}",
                    "Anonymous person track",
                    observation.confidence,
                    observation.crop,
                    {
                        "grouping": "continuous_camera_track_only",
                        "quality": observation.quality,
                        "biometric_identification": False,
                    },
                )

        for zone in cam['zones']:
            if zone.enabled:
                polygon = np.array([(x * frame.shape[1], y * frame.shape[0]) for x, y in zone.points], dtype=np.int32)
                cv2.polylines(frame, [polygon], True, (0, 200, 200), 2)
        roi = cam.get("roi_points", [])
        if roi:
            cv2.polylines(frame, [np.array(roi, dtype=np.int32)], True, (0, 255, 255), 2)
        encode_frame = frame

    ok, buffer = cv2.imencode(
        ".jpg",
        encode_frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
    )
    if ok:
        return {
                "camera_id": camera_id,
                "group_id": cam.get('group_id'),
                "access_epoch": cam.get('access_epoch', 0),
                "name": cam["name"],
                "data": base64.b64encode(buffer).decode("ascii"),
            }


    return None


video_stop = threading.Event()


def active_recordings() -> set[str]:
    return set().union(*(cam['recorder'].active_incidents() for _, cam in camera_manager.get_runtime_items()))


def make_retention() -> RetentionManager:
    return RetentionManager(database, [ALERT_DIR, INCIDENT_DIR], active_recordings,
        days=float(os.getenv('SHOPAWARE_RETENTION_DAYS', '30')),
        max_bytes=int(os.getenv('SHOPAWARE_MAX_STORAGE_BYTES', str(10 * 1024**3))))


storage_status: dict[str, Any] = {}


def maintenance_loop() -> None:
    global storage_status
    last_retention = 0.0
    while not video_stop.wait(0.5):
        for _, cam in camera_manager.get_runtime_items():
            cam["recorder"].expire()
        if time.monotonic() - last_retention >= 60:
            try:
                storage_status = make_retention().enforce()
            except Exception:
                storage_status = {'error': 'Storage maintenance failed'}
            last_retention = time.monotonic()


def video_loop() -> None:
    global latest_frame
    while not video_stop.is_set():
        try:
            load_models()
            break
        except Exception as exc:
            logger.error(f"Model initialization failed; retrying in 30 seconds: {redact(str(exc))}")
            if video_stop.wait(30):
                return

    no_signal = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.putText(no_signal, "NO SIGNAL", (420, 360), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 3)
    frame_count = 0

    while not video_stop.is_set():
        try:
            frames_payload: list[dict[str, str]] = []
            run_obj = frame_count % OBJECT_INFERENCE_EVERY_N_FRAMES == 0
            now = time.time()

            for camera_id, cam in camera_manager.get_runtime_items():
                cap: ThreadedCamera | None = cam.get("cap")
                if cap is None:
                    continue

                context = cam["tracking"]
                with context.lock:
                    if context.closed:
                        continue
                    if now - cam["last_inference_at"] < 1 / max(0.1, cam["inference_fps"]):
                        cached = cam.get("preview")
                        if cached:
                            frames_payload.append(cached)
                        continue
                    if cap.healthy() and cap.sequence == cam["last_sequence"]:
                        cached = cam.get("preview")
                        if cached:
                            frames_payload.append(cached)
                        continue
                    cam["last_inference_at"] = now
                    cam["inference_count"] = cam.get("inference_count", 0) + 1
                    run_obj = not cam["last_objects"] or cam["inference_count"] % OBJECT_INFERENCE_EVERY_N_FRAMES == 0
                    payload = process_camera(camera_id, cam, now, run_obj, no_signal)
                    if payload:
                        cam["preview"] = payload
                        frames_payload.append(payload)

            frame_count += 1
            with latest_frame_lock:
                latest_frame = {"type": "multi_frame", "cameras": frames_payload}
            time.sleep(0.03)
        except Exception as exc:
            logger.error(f"Video loop error: {redact(str(exc))}")
            time.sleep(1)


app = FastAPI(title=APP_NAME, version=__version__)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    # Pydantic's default response embeds the submitted input, including secrets.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": error["loc"], "type": error["type"], "msg": "Invalid request value"}
        for error in exc.errors()
    ]})


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    if request.method == 'OPTIONS' or request.url.path == '/health/live':
        return await call_next(request)
    mutating = request.method not in {'GET', 'HEAD', 'OPTIONS'}
    if mutating and request.headers.get('origin') not in CORS_ORIGINS:
        return JSONResponse(status_code=403, content={'detail': 'Untrusted request origin'})
    session = auth.session(request.cookies.get(COOKIE))
    if request.url.path != '/auth/login':
        if session is None:
            return JSONResponse(status_code=401, content={'detail': 'Authentication required'})
        if mutating and not token_secrets.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf']):
            return JSONResponse(status_code=403, content={'detail': 'CSRF token required'})
    if session is not None and session['role'] != 'admin' and request.url.path != '/auth/login':
        path, method = request.url.path, request.method
        allowed = (method in {'GET', 'HEAD'} and path in {'/auth/session', '/cameras', '/history', '/observations', '/health', '/health/ready', '/groups'}) or (method == 'POST' and path in {'/auth/logout', '/auth/password'})
        access = AccessControl(database)
        camera_match = re.fullmatch(r'/cameras/([^/]+)/frame', path)
        incident_match = re.fullmatch(r'/incidents/([^/]+)(/media/(?:snapshot|clip)|/review)?', path)
        observation_match = re.fullmatch(r'/observations/([^/]+)/snapshot', path)
        if camera_match and method in {'GET', 'HEAD'}:
            if camera_match[1] not in access.cameras(session):
                return JSONResponse(status_code=404, content={'detail': 'Camera not found'})
            allowed = True
        if incident_match and ((method in {'GET', 'HEAD'} and incident_match[2] != '/review') or (method == 'POST' and incident_match[2] == '/review')):
            if not access.can_incident(session, incident_match[1]):
                return JSONResponse(status_code=404, content={'detail': 'Incident not found'})
            allowed = True
        if observation_match and method in {'GET', 'HEAD'}:
            if not access.can_observation(session, observation_match[1]):
                return JSONResponse(status_code=404, content={'detail': 'Observation not found'})
            allowed = True
        if not allowed:
            return JSONResponse(status_code=403, content={'detail': 'Administrator access required'})
    request.state.session = session
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


@app.post('/auth/login')
def login(credentials: LoginInput, request: Request):
    try:
        result = auth.login(credentials.username, credentials.password, request.client.host if request.client else 'unknown')
    except PermissionError:
        raise HTTPException(429, 'Too many login attempts')
    if not result:
        raise HTTPException(401, 'Invalid username or password')
    token, session = result
    auth.logout(request.cookies.get(COOKIE))
    response = JSONResponse(session)
    response.set_cookie(COOKIE, token, httponly=True, secure=COOKIE_SECURE,
                        samesite='strict', max_age=int(auth.lifetime), path='/')
    return response


@app.get('/auth/session')
def current_session(request: Request):
    return request.state.session


@app.post('/auth/logout')
def logout(request: Request):
    auth.logout(request.cookies.get(COOKIE))
    response = JSONResponse({'message': 'Logged out'})
    response.delete_cookie(COOKIE, secure=COOKIE_SECURE, httponly=True, samesite='strict')
    return response


class ChangePasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


@app.post('/auth/password')
def change_password(payload: ChangePasswordInput, request: Request):
    try:
        auth.change_password(request.state.session['id'], payload.current_password, payload.new_password)
    except PermissionError as exc:
        raise HTTPException(429, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    response = JSONResponse({'message': 'Password changed. Sign in again with your new password.'})
    response.delete_cookie(COOKIE, secure=COOKIE_SECURE, httponly=True, samesite='strict')
    return response


def move_camera_group(camera_id, group_id):
    with camera_lifecycle_lock:
        with camera_manager.lock:
            camera = camera_manager.cameras.get(camera_id)
        if camera is None:
            raise LookupError('Camera not found')
        with camera['tracking'].lock:
            row = database.get_camera(camera_id)
            if row['group_id'] == group_id:
                return
            AccessControl(database).move_camera(camera_id, group_id)
            # Reset capture, tracking and evidence buffers at a customer boundary.
            enable_camera(camera_id, EnabledInput(enabled=bool(row['enabled'])))


app.include_router(access_router(lambda: database, move_camera_group))


@app.get('/health/live')
def live():
    return {'status': 'alive'}


@app.get('/incidents/{incident_id}/media/{kind}')
def incident_media(incident_id: str, kind: str):
    incident = database.incident(incident_id)
    if not incident or kind not in {'snapshot', 'clip'}:
        raise HTTPException(404, 'Media not found')
    value = incident.get(kind + '_path')
    if not value:
        raise HTTPException(404, 'Media not available')
    root = (ALERT_DIR if kind == 'snapshot' else INCIDENT_DIR).resolve()
    path = Path(value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, 'Media not available')
    return FileResponse(path, media_type='image/jpeg' if kind == 'snapshot' else 'video/mp4')


@app.get('/observations')
def observations(request: Request, limit: int = 300, mode: str | None = None):
    if mode not in {None, 'lpr', 'face_capture'}:
        raise HTTPException(422, 'mode must be lpr or face_capture')
    return AccessControl(database).observations(request.state.session, min(max(limit, 1), 1000), mode)


@app.get('/observations/{observation_id}/snapshot')
def observation_snapshot(observation_id: str):
    observation = database.observation(observation_id)
    if not observation or not observation.get('snapshot_path'):
        raise HTTPException(404, 'Observation not found')
    path = Path(observation['snapshot_path']).resolve()
    root = ALERT_DIR.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, 'Observation image not available')
    return FileResponse(path, media_type='image/jpeg')


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    access = AccessControl(database)
    incident_counts = access.incident_counts(request.state.session)
    if request.state.session['role'] != 'admin':
        return {'status': 'ok' if model_pose is not None and model_obj is not None and not model_load_error else 'loading',
                'app': APP_NAME, 'version': __version__, 'camera_count': len(access.cameras(request.state.session)),
                'incident_counts': incident_counts}
    return {
        "status": "degraded" if model_load_error else ("ok" if model_pose is not None else "loading"),
        "app": APP_NAME,
        "version": __version__,
        "detection_model": DETECTION_MODEL,
        "pose_model": POSE_MODEL,
        "specialized_model": SPECIALIZED_MODEL if ENABLE_SPECIALIZED_MODEL else None,
        "specialized_loaded": model_is_specialized,
        "model_error": model_load_error or None,
        "camera_count": len(camera_manager.list_safe()),
        "incident_counts": incident_counts,
        "telemetry": {
            "system_cpu_percent": psutil.cpu_percent(interval=0.05),
            "process_rss_bytes": psutil.Process().memory_info().rss,
            "system_available_memory_bytes": psutil.virtual_memory().available,
            "cuda_available": torch.cuda.is_available(),
            "cuda_allocated_bytes": torch.cuda.memory_allocated() if torch.cuda.is_available() else None,
        },
        "recording": {
            "codec": media_writer().codec,
            "pre_event_seconds": PRE_EVENT_SECONDS,
            "post_event_seconds": POST_EVENT_SECONDS,
            "sample_fps": RECORDING_FPS,
        },
        "storage": storage_status,
        "alerts": {"status": alert_dispatcher.status, "queued": alert_dispatcher.queue.qsize()},
        "tracker_architecture": "shared_pose_inference_per_camera_bytetrack",
        "tracker_isolation_qualified": False,  # real multi-camera qualification pending
    }


@app.get('/health/ready')
def readiness():
    conn = database.connect()
    try:
        conn.execute('SELECT 1').fetchone()
    finally:
        conn.close()
    ready = model_pose is not None and model_obj is not None and not model_load_error
    return JSONResponse(status_code=200 if ready else 503, content={'ready': ready})


@app.get('/settings')
def get_settings():
    return settings_store.public()


@app.put('/settings')
def save_settings(payload: SettingsInput):
    return {'settings': settings_store.save(payload), 'restart_required': True}


@app.get("/cameras")
def list_cameras(request: Request) -> list[dict[str, Any]]:
    scope = AccessControl(database).cameras(request.state.session)
    rows = [dict(cam, **{k: v for k, v in scope[cam['id']].items() if k != 'id'})
            for cam in camera_manager.list_safe() if cam['id'] in scope]
    if request.state.session['role'] != 'admin':
        keys = {'id', 'name', 'status', 'enabled', 'last_frame_at', 'group_id', 'group_name'}
        rows = [{k: v for k, v in row.items() if k in keys} for row in rows]
    return rows


@app.post("/cameras", status_code=201)
def add_camera(camera: CameraInput) -> dict[str, Any]:
    try:
        camera_id = camera_manager.add_camera(camera)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=redact(str(exc), [camera.password]),
        ) from exc

    created = next((cam for cam in camera_manager.list_safe() if cam["id"] == camera_id), None)
    return {"message": "Camera added", "camera": created}


@app.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str) -> dict[str, str]:
    with camera_lifecycle_lock:
        if not camera_manager.remove_camera(camera_id):
            raise HTTPException(status_code=404, detail="Camera not found")
    return {"message": "Camera removed"}


class EnabledInput(BaseModel):
    enabled: bool


@app.put('/cameras/{camera_id}/enabled')
def enable_camera(camera_id: str, payload: EnabledInput):
    with camera_lifecycle_lock:
        with camera_manager.lock:
            old = camera_manager.cameras.pop(camera_id, None)
        if not old:
            raise HTTPException(404, 'Camera not found')
        old['tracking'].close()
        if old.get('cap'):
            old['cap'].release()
        old['recorder'].force_finalize_all()
        conn = database.connect()
        try:
            conn.execute('UPDATE cameras SET enabled=? WHERE id=?', (int(payload.enabled), camera_id))
            conn.commit()
        finally:
            conn.close()
        row = database.get_camera(camera_id)
        with camera_manager.lock:
            camera_manager.cameras[camera_id] = camera_manager._row_to_runtime(row)
    return {'enabled': payload.enabled}


@app.put('/cameras/{camera_id}/modes')
def set_camera_modes(camera_id: str, payload: CameraModesInput):
    with camera_lifecycle_lock:
        with camera_manager.lock:
            camera = camera_manager.cameras.get(camera_id)
            if camera is None:
                raise HTTPException(404, 'Camera not found')
        plate_reader, face_capture, break_in = PlateReader(), FaceCapture(), VehicleBreakInDetector()
        context = camera['tracking']
        with context.lock:
            if not database.set_camera_modes(camera_id, payload.modes):
                raise HTTPException(404, 'Camera not found')
            camera['modes'] = payload.modes
            camera['risk'] = RiskEngine(threshold=float(os.getenv('SHOPAWARE_RISK_THRESHOLD', '65')))
            camera['plate_reader'] = plate_reader
            camera['face_capture'] = face_capture
            camera['break_in'] = break_in
            context.reset(-1, None)
    return {'modes': payload.modes}


def training_frame(camera_id: str):
    with camera_manager.lock:
        camera = camera_manager.cameras.get(camera_id)
        cap = camera.get('cap') if camera else None
    if cap is None:
        raise HTTPException(503, 'Enable the camera and wait for a live frame before capturing.')
    ok, frame, _, _, captured_at = cap.snapshot()
    if not ok or frame is None or not captured_at or time.time() - captured_at > 10:
        raise HTTPException(503, 'Camera frame is unavailable or stale. Wait for the stream to reconnect.')
    return frame, captured_at


app.include_router(training_router(lambda: database, training_frame))


@app.get('/cameras/{camera_id}/frame')
def camera_frame(camera_id: str, request: Request):
    # A customer move may occur between middleware authorization and this handler.
    with camera_lifecycle_lock:
        if camera_id not in AccessControl(database).cameras(request.state.session):
            raise HTTPException(404, 'Camera frame unavailable')
        with camera_manager.lock:
            camera = camera_manager.cameras.get(camera_id)
        if not camera or not camera.get('cap'):
            raise HTTPException(404, 'Camera frame unavailable')
        ok, frame = camera['cap'].read()
    if not ok or frame is None:
        raise HTTPException(404, 'Camera frame unavailable')
    ok, jpeg = cv2.imencode('.jpg', frame)
    if not ok:
        raise HTTPException(503, 'Frame encoding failed')
    return Response(jpeg.tobytes(), media_type='image/jpeg')


@app.post('/cameras/{camera_id}/test')
def test_camera(camera_id: str):
    row = database.get_camera(camera_id)
    if not row:
        raise HTTPException(404, 'Camera not found')
    worker = ThreadedCamera(build_runtime_url(row['rtsp_url'], row['username'], secrets.decrypt(row['password_enc'])))
    try:
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and not worker.healthy():
            time.sleep(0.1)
        return {'connected': worker.healthy(), 'status': worker.status}
    finally:
        worker.release()


@app.get("/cameras/{camera_id}/roi")
def get_camera_roi(camera_id: str) -> dict[str, Any]:
    camera = next((cam for cam in camera_manager.list_safe() if cam["id"] == camera_id), None)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"points": camera["roi_points"]}


@app.post("/cameras/{camera_id}/roi")
def set_camera_roi(camera_id: str, roi: RoiInput) -> dict[str, Any]:
    try:
        camera_manager.set_roi(camera_id, roi.points)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Camera not found") from exc
    return {"status": "success", "points": roi.points}


class ZonesInput(BaseModel):
    zones: list[Zone] = Field(max_length=32)


@app.get('/cameras/{camera_id}/zones')
def get_zones(camera_id: str):
    if not database.get_camera(camera_id):
        raise HTTPException(404, 'Camera not found')
    return [z.model_dump() for z in load_zones(camera_id)]


@app.put('/cameras/{camera_id}/zones')
def put_zones(camera_id: str, payload: ZonesInput):
    if len({z.id for z in payload.zones}) != len(payload.zones):
        raise HTTPException(422, 'Zone identifiers must be unique')
    with camera_manager.lock:
        cam = camera_manager.cameras.get(camera_id)
    if not cam:
        raise HTTPException(404, 'Camera not found')
    with cam['tracking'].lock:
        if cam['tracking'].closed:
            raise HTTPException(404, 'Camera was removed')
        conn = database.connect()
        try:
            conn.execute('DELETE FROM zones WHERE camera_id=?', (camera_id,))
            conn.executemany('INSERT INTO zones VALUES(?,?,?,?,?,?)',
                [(z.id, camera_id, z.name, z.type, json.dumps(z.points), int(z.enabled)) for z in payload.zones])
            conn.commit()
        except Exception:
            conn.rollback()
            raise HTTPException(409, 'Zone update conflicts with existing configuration')
        finally:
            conn.close()
        cam['zones'] = payload.zones
        cam['risk'] = RiskEngine(threshold=float(os.getenv('SHOPAWARE_RISK_THRESHOLD', '65')))
    return [z.model_dump() for z in payload.zones]


@app.get("/history")
def history(request: Request, limit: int = 100) -> list[dict[str, Any]]:
    return AccessControl(database).history(request.state.session, min(max(limit, 1), 500))


@app.get("/incidents/{incident_id}")
def incident_detail(incident_id: str, request: Request) -> dict[str, Any]:
    incident = database.incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if request.state.session['role'] != 'admin':
        for kind in ['snapshot', 'clip']:
            incident[kind + '_path'] = kind if incident[kind + '_path'] else None
    return incident


@app.post("/incidents/{incident_id}/review")
def review_incident(incident_id: str, review: ReviewInput, request: Request) -> dict[str, Any]:
    row = database.set_incident_review(incident_id, review.status, request.state.session["username"], review.notes)
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident = database.incident(incident_id)
    assert incident is not None
    if request.state.session['role'] != 'admin':
        for kind in ['snapshot', 'clip']:
            incident[kind + '_path'] = kind if incident[kind + '_path'] else None
    return incident


def visible_frame_payload(payload, session):
    scope = AccessControl(database).cameras(session)
    return {'type': 'multi_frame', 'cameras': [dict(cam, group_id=scope[cam['camera_id']]['group_id'])
            for cam in payload.get('cameras', []) if cam['camera_id'] in scope
            and cam.get('access_epoch', 0) == scope[cam['camera_id']]['access_epoch']]}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if websocket.headers.get('origin') not in CORS_ORIGINS or not auth.session(websocket.cookies.get(COOKIE)):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            session = auth.session(websocket.cookies.get(COOKIE))
            if not session:
                await websocket.close(code=1008)
                return
            payload = None
            with latest_frame_lock:
                if latest_frame is not None:
                    payload = latest_frame
            if payload is not None:
                await websocket.send_text(json.dumps(visible_frame_payload(payload, session)))
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        return


@asynccontextmanager
async def lifespan(_: FastAPI):
    media_writer()  # Fail startup clearly if an explicitly required encoder is unavailable.
    conn = database.connect()
    try:
        conn.execute("UPDATE incidents SET media_status='interrupted' WHERE media_status='pending'")
        conn.commit()
    finally:
        conn.close()
    video_stop.clear()
    thread = threading.Thread(target=video_loop, daemon=True)
    thread.start()
    maintenance = threading.Thread(target=maintenance_loop, daemon=True, name="maintenance")
    maintenance.start()
    yield
    video_stop.set()
    maintenance.join(timeout=5)
    thread.join(timeout=30)
    camera_manager.shutdown()
    alert_dispatcher.close()


app.router.lifespan_context = lifespan


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("SHOPAWARE_API_HOST", "0.0.0.0"),
        port=int(os.getenv("SHOPAWARE_API_PORT", "8000")),
    )
