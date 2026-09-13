import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import numpy as np
import pytest
from pydantic import ValidationError

from shopaware.db import Database
from shopaware.training import AnnotationInput, Box, COCO_NAMES, SessionInput, TrainingStore
from tools.train_camera import check_dataset


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / 'training.db')
    for camera in ('a', 'b'):
        db.insert_camera(camera_id=camera, name=camera, rtsp_url='rtsp://private-host/live',
                         username='private-user', password_enc='private-secret', enabled=False)
    return TrainingStore(db)


def session(store, split='train', camera='a'):
    return store.create_session(SessionInput(camera_id=camera, name='Synthetic session', split=split))['id']


def capture(store, session_id, value=0):
    return store.capture(session_id, np.full((100, 200, 3), value, np.uint8), 100 + value)['id']


def review(store, sample_id, boxes=True):
    store.annotate(sample_id, AnnotationInput(boxes=[Box(class_id=39, x1=.1, y1=.2, x2=.5, y2=.8)] if boxes else [], reviewed=True), 'test-admin')


def exported(store, tmp_path):
    for value, split in enumerate(('train', 'val', 'test')):
        review(store, capture(store, session(store, split), value * 50))
    archive = zipfile.ZipFile(io.BytesIO(store.export('a')))
    archive.extractall(tmp_path / 'dataset')  # Only our generated, controlled test archive.
    return tmp_path / 'dataset' / 'data.yaml', archive


def test_capture_resize_duplicate_and_camera_isolation(store):
    s = session(store)
    sample = store.capture(s, np.zeros((1600, 2400, 3), np.uint8), 100)['id']
    row = store.overview('a')['samples'][0]
    assert row['width'] == 1280 and row['height'] == 853 and not row['reviewed']
    assert store.image(sample).startswith(b'\xff\xd8')
    with pytest.raises(ValueError, match='Identical'):
        store.capture(session(store, 'val'), np.zeros((1600, 2400, 3), np.uint8), 101)
    assert not store.overview('b')['samples']
    capture(store, session(store, camera='b'))
    assert len(store.overview('a')['samples']) == 1


@pytest.mark.parametrize('fields', [dict(x2=.1), dict(y2=.2), dict(x1=-.1), dict(x2=1.1), dict(class_id=80), dict(class_id=1.5), dict(x1=float('nan'))])
def test_annotation_geometry_rejects_invalid_boxes(fields):
    values = dict(class_id=39, x1=.1, y1=.2, x2=.5, y2=.8)
    values.update(fields)
    with pytest.raises(ValidationError):
        Box(**values)


def test_export_review_gating_format_and_no_credentials(store, tmp_path):
    unreviewed = capture(store, session(store), 200)
    with pytest.raises(ValueError, match='each split'):
        store.export('a')
    path, archive = exported(store, tmp_path)
    assert len(COCO_NAMES) == 80 and COCO_NAMES[39] == 'bottle'
    assert unreviewed not in ' '.join(archive.namelist())
    assert json.loads(archive.read('data.yaml'))['names'] == COCO_NAMES
    labels = [archive.read(n).decode() for n in archive.namelist() if n.startswith('labels/')]
    assert all(text == '39 0.30000000 0.50000000 0.40000000 0.60000000\n' for text in labels)
    assert not any(b'private-' in archive.read(n) for n in archive.namelist())
    report = check_dataset(path)
    assert report['format_valid'] and not report['quality_qualified']
    assert report['splits']['test'] == dict(images=1, boxes=1)


def test_reviewed_background_is_explicit_and_exportable(store, tmp_path):
    path, _ = exported(store, tmp_path)
    sample = capture(store, session(store), 150)
    review(store, sample, boxes=False)
    archive = zipfile.ZipFile(io.BytesIO(store.export('a')))
    assert archive.read(f'labels/train/{sample}.txt') == b''
    with closing(store.database.connect()) as conn:
        row = conn.execute('SELECT reviewer,reviewed_at FROM training_samples WHERE id=?', (sample,)).fetchone()
    assert row['reviewer'] == 'test-admin' and row['reviewed_at']


def test_storage_limits_are_atomic_and_deletion_reclaims_capacity(store, monkeypatch):
    import shopaware.training as training
    monkeypatch.setattr(training, 'MAX_SAMPLES', 1)
    s = session(store)
    def attempt(value):
        try: return capture(store, s, value)
        except ValueError: return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, [0, 255]))
    assert sum(r is not None for r in results) == 1
    store.delete(next(r for r in results if r))
    assert store.overview('a')['used_bytes'] == 0
    capture(store, s, 100)
    store.delete(s, session=True)
    assert not store.overview('a')['samples']


def test_byte_quota_and_session_quota(store, monkeypatch):
    import shopaware.training as training
    s = session(store)
    monkeypatch.setattr(training, 'MAX_BYTES', 1)
    with pytest.raises(ValueError, match='storage limit'):
        capture(store, s)
    monkeypatch.setattr(training, 'MAX_SESSIONS', 1)
    with pytest.raises(ValueError, match='Session limit'):
        session(store)


def test_camera_deletion_cascades_only_its_training_examples(store):
    capture(store, session(store))
    capture(store, session(store, camera='b'))
    store.database.delete_camera('a')
    assert not store.overview('a')['samples']
    assert len(store.overview('b')['samples']) == 1
    with closing(store.database.connect()) as conn:
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('tamper', ['duplicate', 'session_split', 'path', 'class', 'extra', 'missing', 'corrupt'])
def test_preflight_rejects_leakage_and_invalid_files(store, tmp_path, tamper):
    path, _ = exported(store, tmp_path)
    root = path.parent
    manifest = json.loads((root / 'manifest.json').read_text())
    a, b = manifest['samples'][:2]
    image = lambda s: root / 'images' / s['split'] / (s['id'] + '.jpg')
    if tamper == 'duplicate': image(a).write_bytes(image(b).read_bytes())
    if tamper == 'session_split': a['session_id'] = b['session_id']
    if tamper == 'path': a['id'] = '../outside'
    if tamper == 'class': (root / 'labels' / a['split'] / (a['id'] + '.txt')).write_text('80 .5 .5 .1 .1')
    if tamper == 'extra': (root / 'images/train/extra.jpg').write_bytes(b'extra')
    if tamper == 'missing': image(a).unlink()
    if tamper == 'corrupt': image(a).write_bytes(b'not an image')
    (root / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        check_dataset(path)


def test_export_classes_match_pinned_ultralytics_coco_mapping():
    from pathlib import Path
    import ultralytics
    import yaml
    config = Path(ultralytics.__file__).parent / 'cfg/datasets/coco8.yaml'
    names = yaml.safe_load(config.read_text(encoding='utf-8'))['names']
    assert [names[i] for i in range(80)] == COCO_NAMES


@pytest.mark.parametrize('check_only', [True, False])
def test_training_command_preflight_and_separate_test_run(store, tmp_path, monkeypatch, check_only):
    import sys
    from types import SimpleNamespace
    from tools.train_camera import main
    path, _ = exported(store, tmp_path)
    calls = []
    integration_calls = []
    callbacks = SimpleNamespace(add_integration_callbacks=lambda instance: integration_calls.append(instance))
    checkpoint = tmp_path / 'run/weights/best.pt'
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b'synthetic mocked checkpoint')

    class Model:
        task = 'detect'
        names = dict(enumerate(COCO_NAMES))
        def __init__(self, source): calls.append(('load', source))
        def train(self, **kwargs):
            callbacks.add_integration_callbacks(self)
            calls.append(('train', kwargs))
            self.trainer = SimpleNamespace(best=checkpoint)
        def val(self, **kwargs):
            callbacks.add_integration_callbacks(self)
            calls.append(('val', kwargs))
            return SimpleNamespace(results_dict={'metrics/precision(B)': .5})

    monkeypatch.setitem(sys.modules, 'ultralytics', SimpleNamespace(YOLO=Model))
    monkeypatch.setitem(sys.modules, 'ultralytics.utils', SimpleNamespace(callbacks=callbacks))
    monkeypatch.setattr(sys, 'argv', ['train_camera', '--data', str(path), '--output', str(tmp_path / 'runs')] + (['--check-only'] if check_only else []))
    main()
    assert not integration_calls
    if check_only:
        assert not calls
        assert not (checkpoint.parent.parent / 'shopaware-report.json').exists()
    else:
        assert calls[0] == ('load', 'yolo26n.pt')
        assert calls[1][1]['exist_ok'] is False
        assert calls[1][1]['data'] == str(path.resolve())
        assert calls[-1][1]['split'] == 'test'
        report = json.loads((checkpoint.parent.parent / 'shopaware-report.json').read_text())
        assert report['activated'] is False and report['quality_qualified'] is False


@pytest.mark.parametrize('fail', [False, True])
def test_local_training_suppresses_integrations_and_restores_factory(monkeypatch, fail):
    from ultralytics.utils import callbacks
    from tools.train_camera import local_training_callbacks
    calls = []
    original = lambda instance: calls.append(instance)
    monkeypatch.setattr(callbacks, 'add_integration_callbacks', original)
    try:
        with local_training_callbacks():
            callbacks.add_integration_callbacks('must-not-upload')
            assert callbacks.get_default_callbacks()['on_train_start']
            if fail:
                raise RuntimeError('Synthetic training failure')
    except RuntimeError:
        assert fail
    assert not calls and callbacks.add_integration_callbacks is original


def test_pinned_trainer_and_validator_use_guarded_integration_factory():
    import inspect
    from ultralytics.engine.trainer import BaseTrainer
    from ultralytics.engine.validator import BaseValidator
    assert 'callbacks.add_integration_callbacks(self)' in inspect.getsource(BaseTrainer)
    assert 'callbacks.add_integration_callbacks(self)' in inspect.getsource(BaseValidator)
