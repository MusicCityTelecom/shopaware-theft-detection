import threading
import time

import numpy as np

from shopaware.ingest import ThreadedCamera


def test_capture_reconnects_records_original_and_shuts_down():
    sampled = threading.Event()
    released = []
    attempts = []

    class Capture:
        def isOpened(self):
            return True

        def read(self):
            if len(attempts) == 1:
                return False, None
            return True, np.zeros((8, 8, 3), dtype=np.uint8)

        def release(self):
            released.append(self)

    def factory(url):
        attempts.append(url)
        return Capture()

    def record(frame, timestamp):
        assert not frame.any()
        assert timestamp > 0
        sampled.set()

    camera = ThreadedCamera('rtsp://synthetic', on_frame=record,
                            capture_factory=factory, retry_seconds=0.01)
    try:
        assert sampled.wait(3)
        assert camera.generation == 2
        assert camera.healthy()
        returned = camera.read()[1]
        returned[:] = 255
        assert not camera.read()[1].any()
        with camera.lock:
            camera.last_frame_at = time.time() - 20
            assert not camera.healthy()
    finally:
        camera.release()
    assert not camera.thread.is_alive()
    assert camera.status == 'stopped'
    assert len(released) == 2


def test_backoff_interruptible_and_error_never_exposes_credentials():
    attempted = threading.Event()

    def failing(url):
        attempted.set()
        raise RuntimeError('secret-password rtsp://user:secret-password@host/live')

    camera = ThreadedCamera('rtsp://user:secret-password@host/live',
                            capture_factory=failing, retry_seconds=30)
    assert attempted.wait(2)
    started = time.monotonic()
    camera.release()
    assert time.monotonic() - started < 2
    assert not camera.thread.is_alive()
    assert 'secret-password' not in camera.last_error
