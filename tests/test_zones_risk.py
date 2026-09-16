import pytest
from pydantic import ValidationError
from shopaware.zones import Zone
from shopaware.risk import RiskEngine


def test_normalized_zones_survive_resolution_change():
    zone = Zone(id='z', name='Shelf', type='merchandise', points=[(0,0),(.5,0),(.5,.5),(0,.5)])
    for width, height in [(640,480),(1920,1080)]:
        assert zone.contains((width*.25/width, height*.25/height))
        assert not zone.contains((.9,.9))


@pytest.mark.parametrize('points', [[(0,0),(1,1),(0,1),(1,0)], [(0,0),(.5,.5),(1,1)], [(-1,0),(1,0),(1,1)]])
def test_invalid_polygons(points):
    with pytest.raises(ValidationError):
        Zone(id='z', name='Bad', type='restricted', points=points)


def test_multiple_signals_deduplicate_until_quiet_and_camera_isolation():
    a, b = RiskEngine(), RiskEngine()
    assert a.observe(1, ['object_near_hand'], 1) is None
    candidate = a.observe(1, ['object_disappearance','hand_to_waist','concealment_candidate'], 2)
    assert candidate['event_type'] == 'suspected_concealment'
    assert candidate['metadata']['score_kind'] == 'heuristic'
    assert candidate['risk_score'] == .9
    for now in range(3, 90):
        assert a.observe(1, ['concealment_candidate','hand_to_waist','object_disappearance'], now) is None
    assert b.observe(1, ['hand_to_waist'], 90) is None
    assert a.observe(1, ['concealment_candidate','hand_to_waist','object_disappearance'], 160)


def test_expired_signals_do_not_accumulate_and_threshold_configurable():
    engine = RiskEngine(threshold=90, window_seconds=2)
    assert engine.observe(1, ['concealment_candidate'], 1) is None
    assert engine.observe(1, ['hand_to_waist','object_disappearance'], 4) is None
