from __future__ import annotations

import asyncio
import base64
import json
import os
import smtplib
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import cv2
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from ultralytics import YOLO

from shopaware.ingest import ThreadedCamera
from shopaware.tracking import CameraTrackingContext
from shopaware.db import Database
from shopaware.recording import RollingClipRecorder
from shopaware.security import (
    SecretStore,
    build_runtime_url,
    clean_camera_url,
    masked_camera_url,
    redact,
)

load_dotenv()

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


class CameraInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    rtsp_url: str = Field(min_length=4, max_length=2048)
    username: str = Field(default="", max_length=512)
    password: str = Field(default="", max_length=1024)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_url(self) -> "CameraInput":
        self.rtsp_url = clean_camera_url(self.rtsp_url)
        return self


class RoiInput(BaseModel):
    points: list[list[int]] = Field(default_factory=list)


class ReviewInput(BaseModel):
    status: str

    @model_validator(mode="after")
    def validate_status(self) -> "ReviewInput":
        allowed = {"needs_review", "confirmed", "false_alarm", "dismissed"}
        if self.status not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return self


def on_clip_complete(incident_id: str, path: Path) -> None:
    if database.set_incident_clip(incident_id, str(path)):
        print(f"Incident clip ready: {incident_id} -> {path.name}")
    else:
        print(f"Incident clip completed for unknown incident: {incident_id}")


def on_clip_error(incident_id: str, exc: Exception) -> None:
    print(f"Incident clip failed for {incident_id}: {redact(str(exc))}")


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
        return {
            "id": row["id"],
            "name": row["name"],
            "rtsp_url": row["rtsp_url"],
            "username": row["username"],
            "has_password": bool(password),
            "roi_points": json.loads(row["roi_json"] or "[]"),
            "enabled": bool(row["enabled"]),
            "cap": ThreadedCamera(runtime_url, on_frame=recorder.push) if row["enabled"] else None,
            "runtime_url": runtime_url,
            "recorder": recorder,
            "tracking": CameraTrackingContext(),
            "last_inference_at": 0.0,
            "last_sequence": -1,
            "inference_fps": float(os.getenv("SHOPAWARE_INFERENCE_FPS", "5")),
            "last_alert_time": 0.0,
            "last_objects": [],
            "roi_entry_times": {},
        }

    def load_cameras(self) -> None:
        with self.lock:
            for row in database.list_cameras():
                try:
                    self.cameras[row["id"]] = self._row_to_runtime(row)
                except Exception as exc:
                    print(f"Camera {row['id']} could not be initialized: {redact(str(exc))}")

    def add_camera(self, camera: CameraInput) -> str:
        camera_id = str(uuid.uuid4())
        clean_url = clean_camera_url(camera.rtsp_url)
        row = database.insert_camera(
            camera_id=camera_id,
            name=camera.name,
            rtsp_url=clean_url,
            username=camera.username,
            password_enc=secrets.encrypt(camera.password),
            enabled=camera.enabled,
        )
        with self.lock:
            self.cameras[camera_id] = self._row_to_runtime(row)
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
    cv2.imwrite(str(filename), frame)

    database.insert_incident(
        incident_id=incident_id,
        camera_id=camera_id,
        camera_name=camera_name,
        event_type=event_type,
        message=message,
        risk_score=risk_score,
        snapshot_path=str(filename),
        metadata=metadata,
    )

    camera_manager.start_recording(camera_id, incident_id, trigger_at)

    threading.Thread(
        target=send_email_notification,
        args=(camera_name, event_type, message, risk_score, filename),
        daemon=True,
    ).start()
    return incident_id


def send_email_notification(
    camera_name: str,
    event_type: str,
    message: str,
    risk_score: float,
    image_path: Path,
) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", username).strip()
    recipient = os.getenv("SMTP_TO", "").strip()
    if not all([host, sender, recipient]):
        return

    port = int(os.getenv("SMTP_PORT", "587"))
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"ShopAware alert: {event_type} on {camera_name}"
    body = (
        "ShopAware detected an event requiring review.\n\n"
        f"Camera: {camera_name}\n"
        f"Event: {event_type}\n"
        f"Risk score: {risk_score:.0%}\n"
        f"Details: {message}\n\n"
        f"Evidence video is recording for approximately {POST_EVENT_SECONDS:.0f} seconds after the trigger.\n"
        "This is an automated candidate detection, not proof of theft. Human review is required."
    )
    msg.attach(MIMEText(body, "plain"))

    try:
        with image_path.open("rb") as fh:
            msg.attach(MIMEImage(fh.read(), name=image_path.name))
    except OSError:
        pass

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
    except Exception as exc:
        print(f"Email notification failed: {redact(str(exc), [password])}")


def load_models() -> None:
    global model_pose, model_obj, model_is_specialized, model_load_error
    with models_lock:
        if model_pose is not None:
            return
        try:
            print(f"Loading ShopAware pose model: {POSE_MODEL}")
            model_pose = YOLO(POSE_MODEL)

            model_is_specialized = False
            if ENABLE_SPECIALIZED_MODEL and Path(SPECIALIZED_MODEL).exists():
                print(f"Loading specialized activity model: {SPECIALIZED_MODEL}")
                model_obj = YOLO(SPECIALIZED_MODEL)
                model_is_specialized = True
            else:
                print(f"Loading ShopAware detection model: {DETECTION_MODEL}")
                model_obj = YOLO(DETECTION_MODEL)
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
        resolution = frame.shape[:2]
        if context.generation != generation or context.resolution != resolution:
            context.reset(generation, resolution)
            cam["roi_entry_times"].clear()
            cam["last_objects"] = []
        # Preserve original evidence before annotations are drawn.
        # Evidence is sampled by capture before inference/annotations.
        now = captured_at

        assert model_pose is not None
        assert model_obj is not None

        pose_results = model_pose.predict(frame, verbose=False, classes=[0], conf=0.1)
        pose_results = [context.update(pose_results[0], now)]
        detected_objects: list[np.ndarray] = cam.get("last_objects", [])

        if run_obj:
            detected_objects = []
            obj_results = model_obj(frame, verbose=False, conf=0.30)
            if obj_results:
                boxes = obj_results[0].boxes.xyxy.cpu().numpy().astype(int)
                classes = obj_results[0].boxes.cls.cpu().numpy().astype(int)
                confidences = obj_results[0].boxes.conf.cpu().numpy()

                if model_is_specialized:
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
                            if now - cam["last_alert_time"] > ALERT_COOLDOWN:
                                trigger_incident(
                                    camera_id,
                                    cam["name"],
                                    "specialized_model_candidate",
                                    f"Specialized activity model produced class '{class_name}'.",
                                    float(confidence),
                                    frame,
                                    now,
                                    {
                                        "class_name": class_name,
                                        "confidence": float(confidence),
                                    },
                                )
                                cam["last_alert_time"] = now
                else:
                    # Upstream-compatible COCO classes used only as a weak interaction signal.
                    target_classes = {
                        24, 25, 26, 28, 39, 40, 41, 42, 43,
                        67, 73, 74, 75, 76, 77, 78, 79,
                    }
                    for box, cls_id, confidence in zip(boxes, classes, confidences):
                        if int(cls_id) in target_classes:
                            detected_objects.append(box)
                            label = f"ITEM {model_obj.names[int(cls_id)]} {confidence:.2f}"
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
                state = context.person(int(track_id), now)

                left_has_obj = check_object_in_hand(keypoints, detected_objects, "LEFT")
                right_has_obj = check_object_in_hand(keypoints, detected_objects, "RIGHT")
                current_holding = left_has_obj or right_has_obj
                holding_hand = "LEFT" if left_has_obj else "RIGHT" if right_has_obj else None

                if current_holding:
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
                        if now - cam["last_alert_time"] > ALERT_COOLDOWN:
                            trigger_incident(
                                camera_id,
                                cam["name"],
                                "suspected_concealment",
                                (
                                    "An item interaction was followed by hand movement "
                                    "near the hip/waist region. Human review required."
                                ),
                                0.72,
                                frame,
                                now,
                                {
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
                "name": cam["name"],
                "data": base64.b64encode(buffer).decode("ascii"),
            }


    return None


video_stop = threading.Event()


def maintenance_loop() -> None:
    while not video_stop.wait(0.5):
        for _, cam in camera_manager.get_runtime_items():
            cam["recorder"].expire()


def video_loop() -> None:
    global latest_frame
    try:
        load_models()
    except Exception as exc:
        print(f"Model initialization failed: {redact(str(exc))}")
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
            print(f"Video loop error: {redact(str(exc))}")
            time.sleep(1)


app = FastAPI(title=APP_NAME, version="0.2.0")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    # Pydantic's default response embeds the submitted input, including secrets.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": error["loc"], "type": error["type"], "msg": "Invalid request value"}
        for error in exc.errors()
    ]})


app.mount("/alerts", StaticFiles(directory=str(ALERT_DIR)), name="alerts")
app.mount("/incident-media", StaticFiles(directory=str(INCIDENT_DIR)), name="incident-media")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if not model_load_error else "degraded",
        "app": APP_NAME,
        "detection_model": DETECTION_MODEL,
        "pose_model": POSE_MODEL,
        "specialized_model": SPECIALIZED_MODEL if ENABLE_SPECIALIZED_MODEL else None,
        "specialized_loaded": model_is_specialized,
        "model_error": model_load_error or None,
        "camera_count": len(camera_manager.list_safe()),
        "recording": {
            "pre_event_seconds": PRE_EVENT_SECONDS,
            "post_event_seconds": POST_EVENT_SECONDS,
            "sample_fps": RECORDING_FPS,
        },
        "tracker_architecture": "shared_pose_inference_per_camera_bytetrack",
        "tracker_isolation_qualified": False,  # real multi-camera qualification pending
    }


@app.get("/cameras")
def list_cameras() -> list[dict[str, Any]]:
    return camera_manager.list_safe()


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
    if not camera_manager.remove_camera(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"message": "Camera removed"}


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


@app.get("/history")
def history(limit: int = 100) -> list[dict[str, Any]]:
    return database.history(min(max(limit, 1), 500))


@app.get("/incidents/{incident_id}")
def incident_detail(incident_id: str) -> dict[str, Any]:
    incident = database.incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@app.post("/incidents/{incident_id}/review")
def review_incident(incident_id: str, review: ReviewInput) -> dict[str, Any]:
    row = database.set_incident_review(incident_id, review.status)
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident = database.incident(incident_id)
    assert incident is not None
    return incident


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = None
            with latest_frame_lock:
                if latest_frame is not None:
                    payload = latest_frame
            if payload is not None:
                await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        return


@asynccontextmanager
async def lifespan(_: FastAPI):
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


app.router.lifespan_context = lifespan


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("SHOPAWARE_API_HOST", "0.0.0.0"),
        port=int(os.getenv("SHOPAWARE_API_PORT", "8000")),
    )
