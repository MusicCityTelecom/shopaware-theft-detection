"""Replaceable media writer; OpenCV MP4 output still needs browser qualification."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, Sequence

import cv2
import numpy as np


class MediaWriter(Protocol):
    def write(self, path: Path, frames: Sequence[Any], fps: float) -> None: ...


class OpenCVWriter:
    def __init__(self, codec: str = 'mp4v') -> None:
        if len(codec) != 4:
            raise ValueError('Codec must be four characters')
        self.codec = codec

    def write(self, path: Path, frames: Sequence[Any], fps: float) -> None:
        if not frames:
            raise RuntimeError('incident clip contains no buffered frames')
        temporary = path.with_suffix('.partial.mp4')
        writer = None
        try:
            first = cv2.imdecode(np.frombuffer(frames[0].jpeg, np.uint8), cv2.IMREAD_COLOR)
            if first is None:
                raise RuntimeError('incident frame could not be decoded')
            height, width = first.shape[:2]
            writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*self.codec), fps, (width, height))
            if not writer.isOpened():
                raise RuntimeError('OpenCV MP4 writer could not be opened')
            index, decoded_index = 0, -1
            frame = first
            count = int(round((frames[-1].timestamp - frames[0].timestamp) * fps)) + 1
            for output_index in range(max(1, count)):
                target = frames[0].timestamp + output_index / fps
                while index + 1 < len(frames) and frames[index + 1].timestamp <= target:
                    index += 1
                if index != decoded_index:
                    frame = cv2.imdecode(np.frombuffer(frames[index].jpeg, np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        raise RuntimeError('incident frame could not be decoded')
                    if frame.shape[:2] != (height, width):
                        frame = cv2.resize(frame, (width, height))
                    decoded_index = index
                writer.write(frame)
            writer.release()
            writer = None
            capture = cv2.VideoCapture(str(temporary))
            try:
                actual = 0
                while capture.read()[0]:
                    actual += 1
                if actual != max(1, count):
                    raise RuntimeError('incident clip failed frame-count verification')
            finally:
                capture.release()
            os.replace(temporary, path)
            path.with_suffix('.json').write_text(json.dumps({
                'source_timestamps': [f.timestamp for f in frames], 'fps': fps,
                'frames_written': count, 'gap_policy': 'hold_previous_frame', 'codec': self.codec,
            }), encoding='utf-8')
        except Exception:
            if writer is not None:
                writer.release()
                writer = None
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            path.with_suffix('.json').unlink(missing_ok=True)
            raise
        finally:
            if writer is not None:
                writer.release()
