from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


@dataclass(slots=True)
class EncodedFrame:
    timestamp: float
    jpeg: bytes


@dataclass(slots=True)
class ActiveClip:
    incident_id: str
    trigger_at: float
    deadline: float
    frames: list[EncodedFrame] = field(default_factory=list)


class RollingClipRecorder:
    """Per-camera rolling JPEG buffer with asynchronous MP4 finalization.

    Frames are sampled and JPEG-encoded in memory rather than retaining raw BGR arrays.
    This keeps the pre-event buffer bounded enough for multi-camera development while
    still allowing ShopAware to preserve evidence that happened before the alert.

    The first implementation writes MP4 using OpenCV's `mp4v` encoder for portability.
    A later deployment pass can move encoding/muxing to FFmpeg and H.264/H.265 while
    preserving this recorder interface.
    """

    def __init__(
        self,
        camera_id: str,
        output_dir: Path,
        *,
        pre_seconds: float = 15.0,
        post_seconds: float = 30.0,
        sample_fps: float = 6.0,
        jpeg_quality: int = 72,
        on_complete: Callable[[str, Path], None] | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.pre_seconds = max(1.0, float(pre_seconds))
        self.post_seconds = max(1.0, float(post_seconds))
        self.sample_fps = max(1.0, float(sample_fps))
        self.jpeg_quality = min(95, max(35, int(jpeg_quality)))
        self.on_complete = on_complete
        self.on_error = on_error

        self._interval = 1.0 / self.sample_fps
        self._buffer: deque[EncodedFrame] = deque()
        self._active: dict[str, ActiveClip] = {}
        self._last_sample_at = 0.0
        self._lock = threading.RLock()

    def push(self, frame: np.ndarray, timestamp: float | None = None) -> None:
        now = float(timestamp if timestamp is not None else time.time())
        with self._lock:
            if self._last_sample_at and now - self._last_sample_at < self._interval:
                return
            self._last_sample_at = now

        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
        )
        if not ok:
            return

        sample = EncodedFrame(timestamp=now, jpeg=encoded.tobytes())
        completed: list[ActiveClip] = []

        with self._lock:
            self._buffer.append(sample)
            cutoff = now - self.pre_seconds
            while self._buffer and self._buffer[0].timestamp < cutoff:
                self._buffer.popleft()

            for clip in self._active.values():
                clip.frames.append(sample)
                if now >= clip.deadline:
                    completed.append(clip)

            for clip in completed:
                self._active.pop(clip.incident_id, None)

        for clip in completed:
            threading.Thread(target=self._finalize, args=(clip,), daemon=True).start()

    def start_incident(self, incident_id: str, trigger_at: float | None = None) -> bool:
        now = float(trigger_at if trigger_at is not None else time.time())
        with self._lock:
            if incident_id in self._active:
                return False
            seed = [frame for frame in self._buffer if frame.timestamp >= now - self.pre_seconds]
            self._active[incident_id] = ActiveClip(
                incident_id=incident_id,
                trigger_at=now,
                deadline=now + self.post_seconds,
                frames=list(seed),
            )
            return True

    def force_finalize_all(self) -> None:
        with self._lock:
            clips = list(self._active.values())
            self._active.clear()
        for clip in clips:
            self._finalize(clip)

    def stats(self) -> dict[str, float | int]:
        with self._lock:
            approx_bytes = sum(len(frame.jpeg) for frame in self._buffer)
            return {
                "buffered_frames": len(self._buffer),
                "active_clips": len(self._active),
                "approx_buffer_bytes": approx_bytes,
                "sample_fps": self.sample_fps,
                "pre_seconds": self.pre_seconds,
                "post_seconds": self.post_seconds,
            }

    def _finalize(self, clip: ActiveClip) -> None:
        try:
            if not clip.frames:
                raise RuntimeError("incident clip contains no buffered frames")

            decoded: list[np.ndarray] = []
            for sample in clip.frames:
                arr = np.frombuffer(sample.jpeg, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    decoded.append(frame)

            if not decoded:
                raise RuntimeError("incident clip frames could not be decoded")

            height, width = decoded[0].shape[:2]
            path = self.output_dir / f"incident_{self.camera_id}_{clip.incident_id}.mp4"
            writer = cv2.VideoWriter(
                str(path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                self.sample_fps,
                (width, height),
            )
            if not writer.isOpened():
                raise RuntimeError("OpenCV MP4 writer could not be opened")

            try:
                for frame in decoded:
                    if frame.shape[1] != width or frame.shape[0] != height:
                        frame = cv2.resize(frame, (width, height))
                    writer.write(frame)
            finally:
                writer.release()

            if not path.exists() or path.stat().st_size <= 0:
                raise RuntimeError("incident clip file was not created")

            if self.on_complete:
                self.on_complete(clip.incident_id, path)
        except Exception as exc:
            if self.on_error:
                self.on_error(clip.incident_id, exc)
