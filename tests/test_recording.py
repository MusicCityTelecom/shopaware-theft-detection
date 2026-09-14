import threading

import numpy as np

from shopaware.recording import RollingClipRecorder


def test_recorder_seeds_pre_event_and_finishes_post_window(tmp_path, monkeypatch):
    completed = []
    done = threading.Event()

    recorder = RollingClipRecorder(
        "cam-1",
        tmp_path,
        pre_seconds=2.0,
        post_seconds=1.0,
        sample_fps=2.0,
    )

    def fake_finalize(clip):
        completed.append(clip)
        done.set()

    monkeypatch.setattr(recorder, "_finalize", fake_finalize)
    frame = np.zeros((64, 96, 3), dtype=np.uint8)

    recorder.push(frame, timestamp=10.0)
    recorder.push(frame, timestamp=10.5)
    recorder.push(frame, timestamp=11.0)

    assert recorder.start_incident("incident-1", trigger_at=11.0)
    recorder.push(frame, timestamp=11.5)
    recorder.push(frame, timestamp=12.0)

    assert done.wait(2.0)
    assert len(completed) == 1
    clip = completed[0]
    assert clip.incident_id == "incident-1"
    assert clip.trigger_at == 11.0
    assert len(clip.frames) >= 5
    assert recorder.stats()["active_clips"] == 0


def test_recorder_sampling_rate_limits_frames(tmp_path):
    recorder = RollingClipRecorder(
        "cam-1",
        tmp_path,
        pre_seconds=5.0,
        post_seconds=2.0,
        sample_fps=2.0,
    )
    frame = np.zeros((32, 32, 3), dtype=np.uint8)

    recorder.push(frame, timestamp=1.0)
    recorder.push(frame, timestamp=1.1)
    recorder.push(frame, timestamp=1.2)
    recorder.push(frame, timestamp=1.5)

    assert recorder.stats()["buffered_frames"] == 2
