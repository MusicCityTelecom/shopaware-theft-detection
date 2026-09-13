from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from shopaware.media import MediaWriter, OpenCVWriter

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
        media_writer: MediaWriter | None = None,
        max_buffer_bytes: int = 32 * 1024**2,
        max_clip_bytes: int = 96 * 1024**2,
        max_inflight: int = 4,
    ) -> None:
        if not re.fullmatch(r'[\w-]+', camera_id):
            raise ValueError('Invalid camera identifier')
        if not all(math.isfinite(v) and 0 < v <= 600 for v in (pre_seconds, post_seconds, sample_fps)):
            raise ValueError('Recording durations/FPS must be finite and in (0, 600]')
        if min(max_buffer_bytes, max_clip_bytes, max_inflight) <= 0:
            raise ValueError('Recording limits must be positive')
        self.media_writer = media_writer or OpenCVWriter()
        self.max_buffer_bytes, self.max_clip_bytes, self.max_inflight = max_buffer_bytes, max_clip_bytes, max_inflight
        self._inflight: set[str] = set()
        self._recent: deque[str] = deque(maxlen=1024)
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='evidence')
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
        self._last_sample_at = None
        self._lock = threading.RLock()

    def push(self, frame: np.ndarray, timestamp: float | None = None) -> None:
        now = float(timestamp if timestamp is not None else time.time())
        if not math.isfinite(now):
            raise ValueError('Frame timestamp must be finite')
        with self._lock:
            if self._closed or (self._last_sample_at is not None and
                               now - self._last_sample_at < self._interval - 1e-9):
                return
            ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if not ok:
                raise RuntimeError('Evidence frame encoding failed')
            self._last_sample_at = now
            sample = EncodedFrame(now, encoded.tobytes())
            self._buffer.append(sample)
            size = sum(len(f.jpeg) for f in self._buffer)
            while self._buffer and (self._buffer[0].timestamp < now - self.pre_seconds or size > self.max_buffer_bytes):
                size -= len(self._buffer.popleft().jpeg)
            for clip in list(self._active.values()):
                if now <= clip.deadline:
                    clip.frames.append(sample)
                if sum(len(f.jpeg) for f in clip.frames) > self.max_clip_bytes:
                    self._active.pop(clip.incident_id)
                    self._inflight.discard(clip.incident_id)
                    self._error(clip.incident_id, RuntimeError('Evidence clip memory limit exceeded'))
                elif now >= clip.deadline:
                    self._submit(clip)

    def expire(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            for clip in list(self._active.values()):
                if now >= clip.deadline:
                    self._submit(clip)

    def _submit(self, clip: ActiveClip) -> None:
        self._active.pop(clip.incident_id, None)
        self._executor.submit(self._finish, clip)

    def _finish(self, clip: ActiveClip) -> None:
        try:
            self._finalize(clip)
        finally:
            with self._lock:
                self._inflight.discard(clip.incident_id)
                self._recent.append(clip.incident_id)

    def active_incidents(self) -> set[str]:
        with self._lock:
            return set(self._inflight)

    def start_incident(self, incident_id: str, trigger_at: float | None = None) -> bool:
        if not re.fullmatch(r'[\w-]+', incident_id):
            raise ValueError('Invalid incident identifier')
        now = float(trigger_at if trigger_at is not None else time.time())
        if not math.isfinite(now):
            raise ValueError('Trigger timestamp must be finite')
        with self._lock:
            if self._closed or incident_id in self._inflight or incident_id in self._recent or len(self._inflight) >= self.max_inflight:
                return False
            seed = [frame for frame in self._buffer if now - self.pre_seconds <= frame.timestamp <= now]
            if sum(len(f.jpeg) for f in seed) > self.max_clip_bytes:
                return False
            self._inflight.add(incident_id)
            self._active[incident_id] = ActiveClip(
                incident_id=incident_id,
                trigger_at=now,
                deadline=now + self.post_seconds,
                frames=list(seed),
            )
            return True

    def force_finalize_all(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for clip in list(self._active.values()):
                self._submit(clip)
            self._buffer.clear()
        self._executor.shutdown(wait=True)

    def stats(self) -> dict[str, float | int]:
        with self._lock:
            approx_bytes = sum(len(frame.jpeg) for frame in self._buffer)
            return {
                "buffered_frames": len(self._buffer),
                "active_clips": len(self._active),
                "inflight_clips": len(self._inflight),
                "approx_buffer_bytes": approx_bytes,
                "sample_fps": self.sample_fps,
                "pre_seconds": self.pre_seconds,
                "post_seconds": self.post_seconds,
            }

    def _error(self, incident_id: str, exc: Exception) -> None:
        logging.getLogger(__name__).error('Incident recording failed', extra={'incident_id': incident_id})
        if self.on_error:
            try:
                self.on_error(incident_id, exc)
            except Exception:
                logging.getLogger(__name__).error('Recording error callback failed')

    def _finalize(self, clip: ActiveClip) -> None:
        path = self.output_dir / f'incident_{self.camera_id}_{clip.incident_id}.mp4'
        try:
            self.media_writer.write(path, clip.frames, self.sample_fps)
            if self.on_complete:
                self.on_complete(clip.incident_id, path)
        except Exception as exc:
            self._error(clip.incident_id, exc)
