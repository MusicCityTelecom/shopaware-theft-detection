"""Bounded, reconnecting OpenCV/FFmpeg ingest; no model work in capture threads."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class ThreadedCamera:
    def __init__(self, runtime_url: str, *, on_frame: Callable | None = None,
                 capture_factory: Callable | None = None, stale_seconds: float = 10,
                 retry_seconds: float = 1, timeout_ms: int = 5000) -> None:
        self.runtime_url = runtime_url
        self.on_frame = on_frame
        self.capture_factory = capture_factory
        self.stale_seconds = stale_seconds
        self.retry_seconds = retry_seconds
        self.timeout_ms = timeout_ms
        self.cap = None
        self.frame = None
        self.ret = False
        self.lock = threading.RLock()
        self.last_frame_at = 0.0
        self.generation = 0
        self.sequence = 0
        self.last_error = ''
        self.status = 'connecting'
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='camera-capture')
        self.thread.start()

    def _open(self):
        if self.capture_factory:
            return self.capture_factory(self.runtime_url)
        return cv2.VideoCapture(self.runtime_url, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.timeout_ms,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.timeout_ms,
        ])

    def _run(self) -> None:
        backoff = self.retry_seconds
        while not self.stop.is_set():
            cap = None
            try:
                cap = self._open()
                self.cap = cap
                if not cap.isOpened():
                    raise RuntimeError('camera connection failed')
                with self.lock:
                    self.generation += 1
                while not self.stop.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        raise RuntimeError('stream read failed')
                    now = time.time()
                    if self.on_frame:
                        self.on_frame(frame, now)
                    with self.lock:
                        self.frame = frame
                        self.ret = True
                        self.last_frame_at = now
                        self.sequence += 1
                        self.status = 'active'
                        self.last_error = ''
                    backoff = self.retry_seconds
                    self.stop.wait(0.005)
            except Exception:
                # Native/codec errors may include passwords separately from a URI.
                # Never forward their text to logs or status responses.
                with self.lock:
                    self.ret = False
                    self.frame = None
                    self.status = 'reconnecting'
                    self.last_error = 'Camera connection or frame read failed'
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        logger.warning('Camera capture release failed')
                self.cap = None
            self.stop.wait(backoff)
            backoff = min(30, backoff * 2)
        with self.lock:
            self.ret = False
            self.frame = None
            self.status = 'stopped'

    def healthy(self) -> bool:
        with self.lock:
            return self.ret and 0 <= time.time() - self.last_frame_at < self.stale_seconds

    def snapshot(self) -> tuple[bool, np.ndarray | None, int, int, float]:
        with self.lock:
            return (self.healthy(), self.frame.copy() if self.frame is not None else None,
                    self.sequence, self.generation, self.last_frame_at)

    def read(self) -> tuple[bool, np.ndarray | None]:
        ok, frame, *_ = self.snapshot()
        return ok, frame

    def release(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2 * self.timeout_ms / 1000 + 1)
        if self.thread.is_alive():
            logger.error('Camera capture did not stop within configured timeout')
