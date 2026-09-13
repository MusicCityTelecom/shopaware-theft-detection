from __future__ import annotations

import asyncio
import base64
import json
import os
import smtplib
import sqlite3
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
from urllib.parse import quote, urlsplit, urlunsplit

import cv2
import numpy as np
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from ultralytics import YOLO

load_dotenv()

APP_NAME = "ShopAware"
DB_PATH = Path(os.getenv("SHOPAWARE_DB_PATH", "shopaware.db"))
ALERT_DIR = Path(os.getenv("SHOPAWARE_ALERT_DIR", "alerts"))
ALERT_DIR.mkdir(parents=True, exist_ok=True)

DETECTION_MODEL = os.getenv("SHOPAWARE_DETECTION_MODEL", "yolo26n.pt")
POSE_MODEL = os.getenv("SHOPAWARE_POSE_MODEL", "yolo26n-pose.pt")
SPECIALIZED_MODEL = os.getenv("SHOPAWARE_SPECIALIZED_MODEL", "shoplifting.pt")
ENABLE_SPECIALIZED_MODEL = os.getenv("SHOPAWARE_ENABLE_SPECIALIZED_MODEL", "true").lower() in {"1", "true", "yes", "on"}

ALERT_COOLDOWN = float(os.getenv("SHOPAWARE_ALERT_COOLDOWN", "8"))
LOITERING_THRESHOLD = float(os.getenv("SHOPAWARE_LOITERING_THRESHOLD", "12"))
OBJECT_INFERENCE_EVERY_N_FRAMES = max(1, int(os.getenv("SHOPAWARE_OBJECT_INFERENCE_EVERY_N_FRAMES", "5")))
JPEG_QUALITY = min(95, max(30, int(os.getenv("SHOPAWARE_JPEG_QUALITY", "65"))))

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("SHOPAWARE_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if origin.strip()
]

KEY_FILE = Path(os.getenv("SHOPAWARE_KEY_FILE", ".shopaware.key"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_or_create_fernet() -> Fernet:
    env_key = os.getenv("SHOPAWARE_FERNET_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode("utf-8"))

    if KEY_FILE.exists():
        return Fernet(KEY_FILE.read_bytes().strip())

    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    return Fernet(key)


FERNET = load_or_create_fernet()


def encrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return FERNET.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    try:
        return FERNET.decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("Unable to decrypt stored camera credential") from exc


def masked_rtsp_url(url: str, username: str = "", has_password: bool = False) -> str:
    """Return an API/log-safe representation of a stream URL."""
    try:
        parts = urlsplit(url)
    except Exception:
        return "<invalid-stream-url>"

    hostname = parts.hostname or ""
    if parts.port:
        hostname = f"{hostname}:{parts.port}"

    auth = ""
    if username:
        auth = quote(username, safe="")
        if has_password:
            auth += ":********"
        auth += "@"

    netloc = f"{auth}{hostname}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def build_runtime_rtsp_url(url: str, username: str = "", password: str = "") -> str:
    parts = urlsplit(url)
    if not parts.scheme:
        raise ValueError("RTSP URL must include a scheme such as rtsp://")

    # Strip any credentials embedded in a supplied URL. ShopAware stores credentials separately.
    hostname = parts.hostname or ""
    if not hostname:
        raise ValueError("RTSP URL must include a hostname or IP address")

    host = hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"

    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth += f":{quote(password, safe='')}"
        auth += "@"

    return urlunsplit((parts.scheme, f"{auth}{host}", parts.path, parts.query, parts.fragment))


def redact_exception_message(message: str, secrets: list[str] | None = None) -> str:
    redacted = message
    for secret in secrets or []:
        if secret:
            redacted = redacted.replace(secret, "********")
    return redacted


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db_connect()
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS cameras (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                rtsp_url TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                password_enc TEXT NOT NULL DEFAULT '',
                roi_json TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                camera_id TEXT NOT NULL,
                camera_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                risk_score REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                snapshot_path TEXT,
                clip_path TEXT,
                review_status TEXT NOT NULL DEFAULT 'needs_review',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_incidents_camera ON incidents(camera_id, created_at DESC);
            """
        )
        conn.commit()
    finally:
        conn.close()


init_db()


class CameraInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    rtsp_url: str = Field(min_length=4, max_length=2048)
    username: str = Field(default="", max_length=512)
    password: str = Field(default="", max_length=1024)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_url(self) -> "CameraInput":
        parts = urlsplit(self.rtsp_url)
        if parts.scheme.lower() not in {"rtsp", "rtsps", "http", "https"}:
            raise ValueError("Camera URL must use rtsp://, rtsps://, http://, or https://")
        if not parts.hostname:
            raise ValueError("Camera URL must include a hostname or IP address")
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


class PersonState:
    def __init__(self, track_id: int):
        self.track_id = track_id
        self.holding_object = False
        self.holding_hand: str | None = None
        self.last_holding_time = 0.0
        self.last_seen = time.time()


class ThreadedCamera:
    """Small reconnecting OpenCV capture worker.

    This intentionally stays close to the upstream prototype while adding reconnect/backoff,
    stall timestamps, and explicit stop handling. A future production pass may replace OpenCV
    ingest with FFmpeg/GStreamer.
    """

    def __init__(self, runtime_url: str):
        self.runtime_url = runtime_url
        self.cap: cv2.VideoCapture | None = None
        self.frame: np.ndarray | None = None
        self.ret = False
        self.running = True
        self.lock = threading.Lock()
        self.last_frame_at = 0.0
        self.last_error = ""
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _open(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self.runtime_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        return cap

    def _run(self) -> None:
        backoff = 1.0
        while self.running:
            if self.cap is None or not self.cap.isOpened():
                try:
                    self.cap = self._open()
                except Exception as exc:
                    self.last_error = redact_exception_message(str(exc), [self.runtime_url])
                    self.cap = None

                if self.cap is None or not self.cap.isOpened():
                    self.ret = False
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue
                backoff = 1.0

            ret, frame = self.cap.read()
            if not ret or frame is None:
                self.ret = False
                self.last_error = "stream read failed"
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
                time.sleep(0.25)
                continue

            with self.lock:
                self.ret = True
                self.frame = frame
                self.last_frame_at = time.time()
                self.last_error = ""

            time.sleep(0.005)

    def read(self) -> tuple[bool, np.ndarray | None]:
        with self.lock:
            return self.ret, self.frame.copy() if self.frame is not None else None

    def healthy(self) -> bool:
        return self.ret and self.last_frame_at > 0 and (time.time() - self.last_frame_at) < 10

    def release(self) -> None:
        self.running = False
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass


class CameraManager:
    def __init__(self):
        self.cameras: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()
        self.load_enabled_cameras()

    def _row_to_runtime(self, row: sqlite3.Row) -> dict[str, Any]:
        password = decrypt_secret(row["password_enc"])
        runtime_url = build_runtime_rtsp_url(row["rtsp_url"], row["username"], password)
        return {
            "id": row["id"],
            "name": row["name"],
            "rtsp_url": row["rtsp_url"],
            "username": row["username"],
            "has_password": bool(password),
            "roi_points": json.loads(row["roi_json"] or "[]"),
            "enabled": bool(row["enabled"]),
            "cap": ThreadedCamera(runtime_url) if row["enabled"] else None,
            "runtime_url": runtime_url,
            "last_alert_time": 0.0,
            "last_objects": [],
            "roi_entry_times": {},
        }

    def load_enabled_cameras(self) -> None:
        conn = db_connect()
        try:
            rows = conn.execute("SELECT * FROM cameras ORDER BY created_at").fetchall()
        finally:
            conn.close()

        with self.lock:
            for row in rows:
                try:
                    self.cameras[row["id"]] = self._row_to_runtime(row)
                except Exception as exc:
                    # Do not print runtime URLs or decrypted passwords.
                    print(f"Camera {row['id']} could not be initialized: {redact_exception_message(str(exc))}")

    def add_camera(self, camera: CameraInput) -> str:
        camera_id = str(uuid.uuid4())
        now = utc_now_iso()
        clean_parts = urlsplit(camera.rtsp_url)
        clean_host = clean_parts.hostname or ""
        if ":" in clean_host and not clean_host.startswith("["):
            clean_host = f"[{clean_host}]"
        if clean_parts.port:
            clean_host = f"{clean_host}:{clean_parts.port}"
        clean_url = urlunsplit((clean_parts.scheme, clean_host, clean_parts.path, clean_parts.query, clean_parts.fragment))

        password_enc = encrypt_secret(camera.password)
        conn = db_connect()
        try:
            conn.execute(
                """
                INSERT INTO cameras(id, name, rtsp_url, username, password_enc, roi_json, enabled, created_at, updated_at)
                VALUES(?,?,?,?,?,'[]',?,?,?)
                """,
                (camera_id, camera.name, clean_url, camera.username, password_enc, int(camera.enabled), now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        finally:
            conn.close()

        if row is None:
            raise RuntimeError("camera insert failed")

        with self.lock:
            self.cameras[camera_id] = self._row_to_runtime(row)
        return camera_id

    def remove_camera(self, camera_id: str) -> bool:
        with self.lock:
            existing = self.cameras.pop(camera_id, None)
            if existing and existing.get("cap"):
                existing["cap"].release()

        conn = db_connect()
        try:
            cur = conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_roi(self, camera_id: str, points: list[list[int]]) -> None:
        normalized = [[int(p[0]), int(p[1])] for p in points if len(p) >= 2]
        conn = db_connect()
        try:
            cur = conn.execute(
                "UPDATE cameras SET roi_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(normalized), utc_now_iso(), camera_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise KeyError(camera_id)
        finally:
            conn.close()

        with self.lock:
            if camera_id in self.cameras:
                self.cameras[camera_id]["roi_points"] = normalized

    def list_safe(self) -> list[dict[str, Any]]:
        with self.lock:
            result = []
            for camera_id, cam in self.cameras.items():
                cap: ThreadedCamera | None = cam.get("cap")
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
                        "source": masked_rtsp_url(cam["rtsp_url"], cam["username"], cam["has_password"]),
                        "status": status,
                        "roi_points": cam.get("roi_points", []),
                    }
                )
            return result

    def get_runtime_items(self) -> list[tuple[str, dict[str, Any]]]:
        with self.lock:
            return list(self.cameras.items())


camera_manager = CameraManager()
person_states: dict[tuple[str, int], PersonState] = {}
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


def incident_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    try:
        result["metadata"] = json.loads(result.pop("metadata_json", "{}") or "{}")
    except json.JSONDecodeError:
        result["metadata"] = {}
    return result


def trigger_incident(
    camera_id: str,
    camera_name: str,
    event_type: str,
    message: str,
    risk_score: float,
    frame: np.ndarray,
    metadata: dict[str, Any] | None = None,
) -> str:
    incident_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    filename = ALERT_DIR / f"incident_{camera_id}_{timestamp}.jpg"
    cv2.imwrite(str(filename), frame)

    conn = db_connect()
    try:
        conn.execute(
            """
            INSERT INTO incidents(
                id, camera_id, camera_name, event_type, message, risk_score,
                created_at, snapshot_path, clip_path, review_status, metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,'needs_review',?)
            """,
            (
                incident_id,
                camera_id,
                camera_name,
                event_type,
                message,
                float(max(0.0, min(1.0, risk_score))),
                utc_now_iso(),
                str(filename),
                None,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    threading.Thread(
        target=send_email_notification,
        args=(camera_name, event_type, message, risk_score, filename),
        daemon=True,
    ).start()
    return incident_id


def send_email_notification(camera_name: str, event_type: str, message: str, risk_score: float, image_path: Path) -> None:
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
        f"ShopAware detected an event requiring review.\n\n"
        f"Camera: {camera_name}\n"
        f"Event: {event_type}\n"
        f"Risk score: {risk_score:.0%}\n"
        f"Details: {message}\n\n"
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
        print(f"Email notification failed: {redact_exception_message(str(exc), [password])}")


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
            model_load_error = redact_exception_message(str(exc))
            model_pose = None
            model_obj = None
            raise


def video_loop() -> None:
    global latest_frame
    try:
        load_models()
    except Exception as exc:
        print(f"Model initialization failed: {redact_exception_message(str(exc))}")
        return

    no_signal = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.putText(no_signal, "NO SIGNAL", (420, 360), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 3)
    frame_count = 0

    while True:
        try:
            frames_payload: list[dict[str, str]] = []
            run_obj = frame_count % OBJECT_INFERENCE_EVERY_N_FRAMES == 0
            now = time.time()

            for camera_id, cam in camera_manager.get_runtime_items():
                cap: ThreadedCamera | None = cam.get("cap")
                if cap is None:
                    continue

                ret, frame = cap.read()
                if not ret or frame is None:
                    frame = no_signal.copy()
                    encode_frame = frame
                else:
                    encode_frame = frame
                    assert model_pose is not None
                    assert model_obj is not None

                    pose_results = model_pose.track(frame, persist=True, verbose=False, classes=[0])
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
                                                {"class_name": class_name, "confidence": float(confidence)},
                                            )
                                            cam["last_alert_time"] = now
                            else:
                                # Upstream-compatible COCO classes used only as a weak interaction signal.
                                target_classes = {24, 25, 26, 28, 39, 40, 41, 42, 43, 67, 73, 74, 75, 76, 77, 78, 79}
                                for box, cls_id, confidence in zip(boxes, classes, confidences):
                                    if int(cls_id) in target_classes:
                                        detected_objects.append(box)
                                        label = f"ITEM {model_obj.names[int(cls_id)]} {confidence:.2f}"
                                        cv2.rectangle(frame, tuple(box[:2]), tuple(box[2:]), (0, 165, 255), 2)
                                        cv2.putText(frame, label, (int(box[0]), max(20, int(box[1]) - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1)
                                cam["last_objects"] = detected_objects

                    if pose_results and pose_results[0].boxes.id is not None:
                        person_boxes = pose_results[0].boxes.xyxy.cpu().numpy().astype(int)
                        track_ids = pose_results[0].boxes.id.cpu().numpy().astype(int)
                        keypoints_all = pose_results[0].keypoints.xy.cpu().numpy() if pose_results[0].keypoints is not None else []

                        for idx, track_id in enumerate(track_ids):
                            box = person_boxes[idx]
                            keypoints = keypoints_all[idx] if len(keypoints_all) > idx else np.empty((0, 2))
                            state_key = (camera_id, int(track_id))
                            state = person_states.setdefault(state_key, PersonState(int(track_id)))
                            state.last_seen = now

                            left_has_obj = check_object_in_hand(keypoints, detected_objects, "LEFT")
                            right_has_obj = check_object_in_hand(keypoints, detected_objects, "RIGHT")
                            current_holding = left_has_obj or right_has_obj
                            holding_hand = "LEFT" if left_has_obj else "RIGHT" if right_has_obj else None

                            if current_holding:
                                state.holding_object = True
                                state.holding_hand = holding_hand
                                state.last_holding_time = now
                                cv2.putText(frame, f"ITEM INTERACTION ({holding_hand})", (int(box[0]), max(20, int(box[1]) - 55)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)

                            if state.holding_object and not current_holding:
                                age = now - state.last_holding_time
                                if age < 3.0 and state.holding_hand and check_concealment(keypoints, state.holding_hand):
                                    cv2.rectangle(frame, tuple(box[:2]), tuple(box[2:]), (0, 0, 255), 3)
                                    cv2.putText(frame, "SUSPECTED CONCEALMENT - REVIEW", (int(box[0]), max(20, int(box[1]) - 80)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
                                    if now - cam["last_alert_time"] > ALERT_COOLDOWN:
                                        trigger_incident(
                                            camera_id,
                                            cam["name"],
                                            "suspected_concealment",
                                            "An item interaction was followed by hand movement near the hip/waist region. Human review required.",
                                            0.72,
                                            frame,
                                            {"track_id": int(track_id), "hand": state.holding_hand, "heuristic": "upstream_item_disappearance_plus_hip_proximity"},
                                        )
                                        cam["last_alert_time"] = now
                                    state.holding_object = False
                                    state.holding_hand = None
                                elif age >= 3.0:
                                    state.holding_object = False
                                    state.holding_hand = None

                            roi = cam.get("roi_points", [])
                            reaching, hand = check_reaching(keypoints, roi)
                            if reaching:
                                cv2.putText(frame, "ROI INTERACTION", (int(box[0]), max(20, int(box[1]) - 30)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)

                            center_x = int((box[0] + box[2]) / 2)
                            center_y = int((box[1] + box[3]) / 2)
                            inside = len(roi) >= 3 and cv2.pointPolygonTest(np.array(roi, dtype=np.int32), (center_x, center_y), False) >= 0
                            if inside:
                                entry_times: dict[int, float] = cam["roi_entry_times"]
                                entry_times.setdefault(int(track_id), now)
                                dwell = now - entry_times[int(track_id)]
                                if dwell >= LOITERING_THRESHOLD:
                                    cv2.putText(frame, f"DWELL {dwell:.1f}s", (int(box[0]), int(box[3]) + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 2)
                            else:
                                cam["roi_entry_times"].pop(int(track_id), None)

                            if check_bending(keypoints):
                                cv2.putText(frame, "BENDING", (int(box[0]), int(box[3]) + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 1)

                    roi = cam.get("roi_points", [])
                    if roi:
                        cv2.polylines(frame, [np.array(roi, dtype=np.int32)], True, (0, 255, 255), 2)

                    encode_frame = frame

                ok, buffer = cv2.imencode(".jpg", encode_frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                if ok:
                    frames_payload.append(
                        {
                            "camera_id": camera_id,
                            "name": cam["name"],
                            "data": base64.b64encode(buffer).decode("ascii"),
                        }
                    )

            # Garbage-collect stale tracking state.
            stale_before = now - 60
            for key in [k for k, state in person_states.items() if state.last_seen < stale_before]:
                person_states.pop(key, None)

            frame_count += 1
            if frames_payload:
                with latest_frame_lock:
                    latest_frame = {"type": "multi_frame", "cameras": frames_payload}
            time.sleep(0.03)
        except Exception as exc:
            print(f"Video loop error: {redact_exception_message(str(exc))}")
            time.sleep(1)


app = FastAPI(title=APP_NAME, version="0.1.0")
app.mount("/alerts", StaticFiles(directory=str(ALERT_DIR)), name="alerts")
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
    }


@app.get("/cameras")
def list_cameras() -> list[dict[str, Any]]:
    return camera_manager.list_safe()


@app.post("/cameras", status_code=201)
def add_camera(camera: CameraInput) -> dict[str, Any]:
    try:
        camera_id = camera_manager.add_camera(camera)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=redact_exception_message(str(exc), [camera.password])) from exc

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
    limit = min(max(limit, 1), 500)
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [incident_to_dict(row) for row in rows]
    finally:
        conn.close()


@app.post("/incidents/{incident_id}/review")
def review_incident(incident_id: str, review: ReviewInput) -> dict[str, Any]:
    conn = db_connect()
    try:
        cur = conn.execute("UPDATE incidents SET review_status = ? WHERE id = ?", (review.status, incident_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Incident not found")
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        assert row is not None
        return incident_to_dict(row)
    finally:
        conn.close()


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
    thread = threading.Thread(target=video_loop, daemon=True)
    thread.start()
    yield
    for _, cam in camera_manager.get_runtime_items():
        if cam.get("cap"):
            cam["cap"].release()


app.router.lifespan_context = lifespan


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("SHOPAWARE_API_HOST", "0.0.0.0"),
        port=int(os.getenv("SHOPAWARE_API_PORT", "8000")),
    )
