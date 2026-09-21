"""Independent camera tracking on shared YOLO pose predictions.

Adapter for ultralytics==8.4.150. Do not register Ultralytics tracking callbacks
on the shared predictor. Association pools and ID allocation belong here.
"""
from __future__ import annotations

import itertools
import threading
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import numpy as np
import torch
from ultralytics.trackers.byte_tracker import BYTETracker, STrack


class CameraByteTracker(BYTETracker):
    """ByteTrack with a private ID allocator instead of BaseTrack._count."""

    def __init__(self, track_buffer: int = 30) -> None:
        ids = itertools.count(1)

        class CameraTrack(STrack):
            @staticmethod
            def next_id() -> int:
                return next(ids)

        self.track_class = CameraTrack
        super().__init__(SimpleNamespace(
            track_high_thresh=0.25, track_low_thresh=0.1,
            new_track_thresh=0.25, track_buffer=track_buffer,
            match_thresh=0.8, fuse_score=True,
        ))

    @staticmethod
    def reset_id() -> None:
        # Upstream construction/reset otherwise mutates every camera's counter.
        # IDs stay monotonic inside this context; a replacement starts at one.
        return None


@dataclass
class PersonState:
    track_id: int
    holding_object: bool = False
    holding_hand: str | None = None
    last_holding_time: float = 0.0
    last_seen: float = 0.0
    zone_entries: dict[str, float] = field(default_factory=dict)


@dataclass
class CameraTrackingContext:
    """One stream generation, protected against removal during inference."""

    track_buffer: int = 30
    lock: Any = field(default_factory=threading.RLock)
    people: dict[int, PersonState] = field(default_factory=dict)
    tracker: CameraByteTracker | None = None
    generation: int = -1
    resolution: tuple[int, int] | None = None
    closed: bool = False
    # ByteTrack IDs and ingest generations restart; persisted groups need a
    # unique boundary for each uninterrupted tracker lifetime.
    track_scope: str = field(default_factory=lambda: uuid.uuid4().hex)

    def reset(self, generation: int, resolution: tuple[int, int] | None) -> None:
        with self.lock:
            self.tracker = None
            self.people.clear()
            self.track_scope = uuid.uuid4().hex
            self.generation = generation
            self.resolution = resolution

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.tracker = None
            self.people.clear()

    def update(self, result: Any, now: float) -> Any:
        """Preserve detection-to-keypoint indexing, including empty results."""
        with self.lock:
            if self.closed:
                raise RuntimeError("camera tracking context is closed")
            if self.tracker is None:
                self.tracker = CameraByteTracker(self.track_buffer)
            tracks = self.tracker.update(result.boxes.cpu().numpy(), result.orig_img)
            if len(tracks):
                selected = result[tracks[:, -1].astype(int)]
                boxes = tracks[:, :-1]
            else:
                selected = result[np.empty(0, dtype=int)]
                boxes = np.empty((0, 7), dtype=np.float32)
            if isinstance(result.boxes.data, torch.Tensor):
                boxes = torch.as_tensor(boxes, device=result.boxes.data.device)
            selected.update(boxes=boxes)
            for track_id in list(self.people):
                if now - self.people[track_id].last_seen > 60:
                    del self.people[track_id]
            return selected

    def person(self, track_id: int, now: float) -> PersonState:
        state = self.people.setdefault(track_id, PersonState(track_id))
        state.last_seen = now
        return state
