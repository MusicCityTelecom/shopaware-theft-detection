"""Evidence encoders with timestamp preservation and verified output."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Protocol, Sequence

import cv2
import numpy as np


class MediaWriter(Protocol):
    def write(self, path: Path, frames: Sequence[Any], fps: float) -> None: ...


def _timeline(frames, fps):
    if not frames or not math.isfinite(fps) or not 0 < fps <= 60:
        raise ValueError('Evidence requires frames and a finite FPS between 0 and 60')
    stamps = [f.timestamp for f in frames]
    if not all(math.isfinite(t) for t in stamps) or any(b < a for a, b in zip(stamps, stamps[1:])):
        raise ValueError('Evidence timestamps must be finite and ordered')
    if stamps[-1] - stamps[0] > 1200:
        raise ValueError('Evidence timeline exceeds the clip duration limit')
    return int(round((stamps[-1] - stamps[0]) * fps)) + 1


def _decode_frames(frames, fps, count, size):
    index, decoded_index, frame = 0, -1, None
    for output_index in range(count):
        target = frames[0].timestamp + output_index / fps
        while index + 1 < len(frames) and frames[index + 1].timestamp <= target:
            index += 1
        if index != decoded_index:
            frame = cv2.imdecode(np.frombuffer(frames[index].jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise RuntimeError('Incident frame could not be decoded')
            if (frame.shape[1], frame.shape[0]) != size:
                frame = cv2.resize(frame, size)
            decoded_index = index
        yield frame


class FFmpegWriter:
    """Bounded external H.264/yuv420p encoder, with seekable browser MP4 output."""
    codec = 'h264'

    def __init__(self, executable: str | None = None, timeout: float = 120):
        self.executable = executable or os.getenv('SHOPAWARE_FFMPEG', 'ffmpeg')
        if not shutil.which(self.executable):
            raise ValueError('FFmpeg is required for H.264 evidence; install it or configure SHOPAWARE_FFMPEG')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Encoder timeout must be positive and finite')
        self.timeout = timeout

    def write(self, path: Path, frames: Sequence[Any], fps: float) -> None:
        count = _timeline(frames, fps)
        first = cv2.imdecode(np.frombuffer(frames[0].jpeg, np.uint8), cv2.IMREAD_COLOR)
        if first is None:
            raise RuntimeError('Incident frame could not be decoded')
        height, width = first.shape[:2]
        temporary = path.with_suffix('.partial.mp4')
        command = [self.executable, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                   '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size', f'{width}x{height}',
                   '-framerate', str(fps), '-i', 'pipe:0', '-an',
                   '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'libx264',
                   '-preset', 'veryfast', '-crf', '23', '-threads', '2', '-pix_fmt', 'yuv420p',
                   '-movflags', '+faststart', str(temporary)]
        process = None
        timer = None
        expired = threading.Event()
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            def stop_encoder():
                expired.set()
                try:
                    process.kill()
                except OSError:
                    pass
            timer = threading.Timer(self.timeout, stop_encoder)
            timer.daemon = True
            timer.start()
            for frame in _decode_frames(frames, fps, count, (width, height)):
                process.stdin.write(frame.tobytes())
            process.stdin.close()
            if process.wait(timeout=self.timeout) != 0 or expired.is_set():
                raise RuntimeError('FFmpeg evidence encoding failed or timed out')
            timer.cancel()
            capture = cv2.VideoCapture(str(temporary))
            try:
                actual = 0
                while capture.read()[0]:
                    actual += 1
                if actual != count:
                    raise RuntimeError('H.264 evidence failed frame-count verification')
            finally:
                capture.release()
            os.replace(temporary, path)
            path.with_suffix('.json').write_text(json.dumps({
                'source_timestamps': [f.timestamp for f in frames], 'fps': fps,
                'frames_written': count, 'gap_policy': 'hold_previous_frame', 'codec': self.codec,
                'pixel_format': 'yuv420p', 'padding': 'even_dimensions',
            }), encoding='utf-8')
        except Exception as exc:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            path.with_suffix('.json').unlink(missing_ok=True)
            raise RuntimeError('H.264 evidence encoding failed; check FFmpeg, disk space and encoder timeout') from exc
        finally:
            if timer:
                timer.cancel()
            if process is not None and process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass


def media_writer(mode: str | None = None) -> MediaWriter:
    mode = mode or os.getenv('SHOPAWARE_MEDIA_WRITER', 'auto')
    if mode not in {'auto', 'ffmpeg', 'opencv'}:
        raise ValueError('SHOPAWARE_MEDIA_WRITER must be auto, ffmpeg or opencv')
    if mode == 'ffmpeg' or (mode == 'auto' and shutil.which(os.getenv('SHOPAWARE_FFMPEG', 'ffmpeg'))):
        return FFmpegWriter()
    return OpenCVWriter()


class OpenCVWriter:
    def __init__(self, codec: str = 'mp4v') -> None:
        if codec not in {'mp4v', 'avc1', 'H264'}:
            raise ValueError('Unsupported evidence codec')
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
