import json
import threading

import cv2
import numpy as np
import pytest

from shopaware.media import OpenCVWriter
from shopaware.recording import RollingClipRecorder


def frame(size=32):
    return np.zeros((size, size, 3), np.uint8)


class Writer:
    def __init__(self):
        self.clips = []

    def write(self, path, frames, fps):
        self.clips.append((path, list(frames), fps))
        path.write_bytes(b'test writer')


def test_pre_window_byte_bound_and_post_deadline(tmp_path):
    writer = Writer()
    r = RollingClipRecorder('cam', tmp_path, pre_seconds=2, post_seconds=1,
                            sample_fps=2, max_buffer_bytes=4000, media_writer=writer)
    for i in range(20):
        r.push(frame(), i / 2)
    assert r.stats()['approx_buffer_bytes'] <= 4000
    assert r.start_incident('one', 9.5)
    r.push(frame(), 10)
    r.push(frame(), 10.5)
    r.push(frame(), 20)
    r.force_finalize_all()
    timestamps = [f.timestamp for f in writer.clips[0][1]]
    assert min(timestamps) >= 7.5
    assert max(timestamps) == 10.5
    assert not r.active_incidents()
    assert not r.start_incident('after-close')


def test_duplicate_and_concurrent_recordings_have_bounded_queue(tmp_path):
    writer = Writer()
    r = RollingClipRecorder('cam', tmp_path, max_inflight=2, media_writer=writer)
    r.push(frame(), 10)
    assert r.start_incident('one', 10)
    assert not r.start_incident('one', 10)
    assert r.start_incident('two', 10)
    assert not r.start_incident('three', 10)
    r.force_finalize_all()
    assert len(writer.clips) == 2
    assert not r.active_incidents()


def test_stalled_stream_finalizes_on_maintenance(tmp_path):
    writer = Writer()
    r = RollingClipRecorder('cam', tmp_path, post_seconds=1, media_writer=writer)
    r.push(frame(), 10)
    r.start_incident('one', 10)
    r.expire(11)
    r.force_finalize_all()
    assert len(writer.clips) == 1


def test_writer_failure_callback_and_callback_failure_do_not_leak_capacity(tmp_path):
    errors = []

    class FailingWriter:
        def write(self, *args):
            raise OSError('disk full')

    def failure(incident, exc):
        errors.append(str(exc))
        raise RuntimeError('callback failed')

    r = RollingClipRecorder('cam', tmp_path, media_writer=FailingWriter(), on_error=failure)
    r.push(frame(), 10)
    r.start_incident('one', 10)
    r.force_finalize_all()
    assert errors == ['disk full']
    assert not r.active_incidents()


def test_real_mp4_resolution_change_and_timestamp_cadence(tmp_path):
    completed = []
    errors = []
    r = RollingClipRecorder('cam', tmp_path, sample_fps=2, pre_seconds=2,
                            post_seconds=1, on_complete=lambda i, p: completed.append(p),
                            on_error=lambda i, e: errors.append(e))
    r.push(frame(32), 0)
    r.push(frame(48), 1)
    r.start_incident('one', 1)
    r.push(frame(64), 2)
    r.force_finalize_all()
    assert not errors
    assert len(completed) == 1
    cap = cv2.VideoCapture(str(completed[0]))
    try:
        assert cap.get(cv2.CAP_PROP_FRAME_COUNT) == 5
        assert cap.get(cv2.CAP_PROP_FRAME_WIDTH) == 32
    finally:
        cap.release()
    assert json.loads(completed[0].with_suffix('.json').read_text())['source_timestamps'] == [0, 1, 2]


@pytest.mark.parametrize('name', ['../escape', 'one/two', 'one\\two'])
def test_identifiers_cannot_escape_media_directory(tmp_path, name):
    with pytest.raises(ValueError):
        RollingClipRecorder(name, tmp_path)


def test_invalid_writer_has_no_partial_media(tmp_path):
    errors = []
    r = RollingClipRecorder('cam', tmp_path, media_writer=OpenCVWriter('zzzz'),
                            on_error=lambda i, e: errors.append(e))
    r.push(frame(), 10)
    r.start_incident('one', 10)
    r.force_finalize_all()
    assert errors
    assert not list(tmp_path.glob('*.mp4'))
