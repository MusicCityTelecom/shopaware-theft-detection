import numpy as np
import pytest
from types import SimpleNamespace
from ultralytics.engine.results import Results
from ultralytics.trackers.basetrack import BaseTrack

from shopaware.tracking import CameraTrackingContext


@pytest.mark.parametrize('mode,count', [('image', 1), ('stream', 2)])
def test_pinned_upstream_tracker_allocation_contract(mode, count):
    from importlib.metadata import version
    from ultralytics.trackers.track import on_predict_start
    assert version('ultralytics') == '8.4.150'
    predictor = SimpleNamespace(args=SimpleNamespace(task='pose', tracker='bytetrack.yaml'),
                                device='cpu', dataset=SimpleNamespace(bs=2, mode=mode))
    on_predict_start(predictor, persist=True)
    assert len(predictor.trackers) == count
    trackers = predictor.trackers
    on_predict_start(predictor, persist=True)
    assert predictor.trackers is trackers


def result(boxes):
    boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 6)
    keypoints = np.zeros((len(boxes), 17, 3), dtype=np.float32)
    for i in range(len(boxes)):
        keypoints[i, :, :] = [20 + i, 30 + i, 0.99]
    return Results(np.zeros((300, 400, 3), dtype=np.uint8), 'frame',
                   {0: 'person'}, boxes=boxes, keypoints=keypoints)


def person(x=10, confidence=0.9):
    return [x, 10, x + 40, 110, confidence, 0]


def update(context, boxes, now=1):
    return context.update(result(boxes), now)


def test_stable_ids_and_low_confidence_association():
    context = CameraTrackingContext()
    first = update(context, [person()]).boxes.id.copy()
    for x in range(11, 20):
        np.testing.assert_array_equal(update(context, [person(x, 0.2)]).boxes.id, first)


def test_camera_history_is_identical_alone_or_interleaved():
    alone, a, b = (CameraTrackingContext() for _ in range(3))
    sequence = [[person(10)], [person(12)], [], [person(14)],
                [person(16), person(250)], [person(18), person(252)]]
    for i, boxes in enumerate(sequence):
        expected = update(alone, boxes, i)
        update(b, [person(10), person(200)], i)
        if i == 3:
            b.reset(2, (300, 400))
        actual = update(a, boxes, i)
        np.testing.assert_array_equal(actual.boxes.data, expected.boxes.data)
        np.testing.assert_array_equal(actual.keypoints.data, expected.keypoints.data)
    assert a.tracker is not b.tracker
    assert a.tracker.tracked_stracks[0] is not b.tracker.tracked_stracks[0]


def test_creation_reset_and_removal_do_not_touch_global_or_other_camera_ids():
    initial_global = BaseTrack._count
    a, b = CameraTrackingContext(), CameraTrackingContext()
    update(a, [person()])
    update(b, [person()])
    b.reset(2, (300, 400))
    update(b, [person()])
    b.close()
    update(a, [person(), person(250)])
    ids = update(a, [person(), person(251)]).boxes.id
    assert len(set(ids)) == 2
    assert set(ids) == {1, 2}
    assert BaseTrack._count == initial_global
    with pytest.raises(RuntimeError, match='closed'):
        update(b, [person()])


def test_no_cross_camera_concealment_state_and_restart_cleanup():
    a, b = CameraTrackingContext(), CameraTrackingContext()
    a.person(1, 10).holding_object = True
    assert not b.person(1, 10).holding_object
    b.reset(1, (300, 400))
    assert a.people[1].holding_object
    a.reset(2, (300, 400))
    assert not a.person(1, 11).holding_object


def test_stale_behavior_and_tracker_cleanup_even_on_empty_detections():
    a = CameraTrackingContext(track_buffer=2)
    update(a, [person()], 1)
    a.person(1, 1)
    for now in range(2, 65):
        empty = update(a, [], now)
        assert len(empty.boxes) == len(empty.keypoints) == 0
    assert not a.people
    assert not a.tracker.lost_stracks


def test_keypoints_follow_source_indices_after_filter_and_reorder():
    a = CameraTrackingContext()
    tracked = update(a, [person(200, 0.05), person(10)])
    assert len(tracked.boxes) == 1
    np.testing.assert_array_equal(tracked.keypoints.xy[0, 9], [21, 31])
    assert tracked.boxes.cls[0] == 0


def test_identical_person_in_second_camera_starts_independently():
    a, b = CameraTrackingContext(), CameraTrackingContext()
    for _ in range(10):
        update(a, [person()])
    update(b, [])
    # A mature track must not activate B's first observation immediately.
    assert len(update(b, [person()]).boxes) == 0
    assert len(update(b, [person()]).boxes) == 1
    assert a.tracker.frame_id == 10


def test_tensor_results_keep_tensor_contract_for_backend_consumers():
    import torch
    source = result([person()])
    source.update(boxes=torch.as_tensor(source.boxes.data))
    tracked = CameraTrackingContext().update(source, 1)
    assert isinstance(tracked.boxes.data, torch.Tensor)
    assert tracked.boxes.id.cpu().numpy().tolist() == [1]
