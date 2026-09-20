import json
import subprocess
import sys
import time
from types import SimpleNamespace

import cv2
import imageio_ffmpeg
import numpy as np
import pytest

from shopaware.media import FFmpegWriter, media_writer
from shopaware.models import model_path


def sample(timestamp, shape=(33, 49), color=0):
    image = np.full((*shape, 3), color, np.uint8)
    ok, encoded = cv2.imencode('.jpg', image)
    assert ok
    return SimpleNamespace(timestamp=timestamp, jpeg=encoded.tobytes())


def test_h264_is_seekable_and_preserves_timing_across_gaps_and_resolution(tmp_path):
    output = tmp_path / 'evidence.mp4'
    FFmpegWriter(imageio_ffmpeg.get_ffmpeg_exe()).write(
        output, [sample(10), sample(11, (64, 64), 240), sample(12, color=120)], 2)
    raw = output.read_bytes()
    assert b'avc1' in raw
    assert raw.index(b'moov') < raw.index(b'mdat')  # start playback without downloading the whole clip
    cap = cv2.VideoCapture(str(output))
    try:
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
        assert len(frames) == 5
        assert frames[0].shape == (34, 50, 3)
        assert frames[1][10, 10].mean() < 10
        assert frames[2][10, 10].mean() > 220
    finally:
        cap.release()
    assert json.loads(output.with_suffix('.json').read_text())['codec'] == 'h264'


@pytest.mark.parametrize('timestamps,fps', [([], 2), ([1, 0], 2), ([float('nan')], 2),
                                          ([0, 1201], 2), ([0], 0), ([0], float('inf'))])
def test_bad_timeline_does_not_create_output(tmp_path, timestamps, fps):
    with pytest.raises(ValueError):
        FFmpegWriter(imageio_ffmpeg.get_ffmpeg_exe()).write(
            tmp_path / 'bad.mp4', [sample(t) for t in timestamps], fps)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('script', ['import sys; sys.exit(1)', 'import time; time.sleep(60)'])
def test_encoder_failure_and_hang_clean_up(tmp_path, monkeypatch, script):
    real_popen = subprocess.Popen
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kwargs:
                        real_popen([sys.executable, '-c', script], **kwargs))
    start = time.monotonic()
    with pytest.raises(RuntimeError, match='H.264 evidence encoding failed'):
        FFmpegWriter(imageio_ffmpeg.get_ffmpeg_exe(), timeout=0.5).write(
            tmp_path / 'bad.mp4', [sample(0, (1024, 1024))], 2)
    assert time.monotonic() - start < 10
    assert not list(tmp_path.iterdir())


def test_persistent_model_directory_preserves_explicit_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('SHOPAWARE_MODEL_DIR', str(tmp_path / 'models'))
    assert model_path('yolo26n.pt') == str(tmp_path / 'models' / 'yolo26n.pt')
    assert model_path('/app/runs/custom.pt') == '/app/runs/custom.pt'
    assert model_path('models/custom.pt') == 'models/custom.pt'


def test_explicit_ffmpeg_requires_installed_encoder(monkeypatch):
    monkeypatch.setenv('SHOPAWARE_FFMPEG', 'shopaware-nonexistent-ffmpeg')
    with pytest.raises(ValueError, match='FFmpeg is required'):
        media_writer('ffmpeg')
    assert media_writer('auto').codec == 'mp4v'
