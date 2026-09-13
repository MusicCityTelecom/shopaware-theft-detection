"""Validate a ShopAware export, then explicitly fine-tune YOLO26 outside the server."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import uuid

import cv2
import numpy as np

from shopaware.training import COCO_NAMES


@contextmanager
def local_training_callbacks():
    """Suppress optional logger/upload callbacks in the standalone training process.

    Pinned Ultralytics 8.4.150 registers trainer/validator integrations here.
    Logger settings alone do not disable Platform checkpoint uploads. Preserve
    core callbacks and restore the factory without modifying saved settings or
    account credentials. This helper is for the separate, single training CLI.
    """
    from ultralytics.utils import callbacks
    original = callbacks.add_integration_callbacks
    callbacks.add_integration_callbacks = lambda instance: None
    try:
        yield
    finally:
        callbacks.add_integration_callbacks = original


def check_dataset(data_file: Path) -> dict:
    # ShopAware writes JSON-compatible YAML. Do not execute YAML/download hooks.
    root = data_file.resolve().parent
    data = json.loads(data_file.read_text(encoding='utf-8'))
    expected = dict(train='images/train', val='images/val', test='images/test', names=COCO_NAMES)
    if data != expected:
        raise ValueError('Use an unmodified ShopAware data.yaml export with the original 80 COCO classes.')
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format_version') != 1 or manifest.get('task') != 'detect':
        raise ValueError('Unsupported ShopAware dataset manifest')
    counts = {split: dict(images=0, boxes=0) for split in ('train', 'val', 'test')}
    sessions, seen_images, seen_ids, expected_files = {}, set(), set(), set()
    for sample in manifest['samples']:
        sample_id, session_id, split = sample['id'], sample['session_id'], sample['split']
        if split not in counts or any(len(v) != 32 or any(c not in '0123456789abcdef' for c in v) for v in (sample_id, session_id)):
            raise ValueError('Invalid manifest identifiers or split')
        if sample_id in seen_ids or sessions.setdefault(session_id, split) != split:
            raise ValueError('Duplicate example or collection session appears across splits')
        seen_ids.add(sample_id)
        image = root / 'images' / split / f'{sample_id}.jpg'
        label = root / 'labels' / split / f'{sample_id}.txt'
        for path in (image, label):
            if not path.resolve().is_relative_to(root) or not path.is_file():
                raise ValueError('Missing dataset file or path outside the dataset')
            expected_files.add(path.relative_to(root).as_posix())
        image_bytes = image.read_bytes()
        pixels = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if pixels is None or min(pixels.shape[:2]) < 2:
            raise ValueError('Unreadable or invalid training image')
        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest in seen_images:
            raise ValueError('Duplicate images found; recollect independent scenes for each split')
        seen_images.add(digest)
        for line in label.read_text(encoding='utf-8').splitlines():
            parts = line.split()
            if len(parts) != 5:
                raise ValueError('Each label needs class, center x/y, width and height')
            class_id = int(parts[0])
            x, y, width, height = map(float, parts[1:])
            if not 0 <= class_id < 80 or not all(math.isfinite(v) for v in (x, y, width, height)):
                raise ValueError('Invalid class or non-finite coordinate')
            if width <= 0 or height <= 0 or min(x-width/2, y-height/2) < -1e-7 or max(x+width/2, y+height/2) > 1+1e-7:
                raise ValueError('Box outside the image or without positive area')
            counts[split]['boxes'] += 1
        counts[split]['images'] += 1
    actual_files = {p.relative_to(root).as_posix() for folder in ('images', 'labels') for p in (root / folder).rglob('*') if p.is_file() and p.suffix != '.cache'}
    if actual_files != expected_files:
        raise ValueError('Dataset contains files not recorded in the manifest. Export a clean dataset.')
    if any(not c['images'] or not c['boxes'] for c in counts.values()):
        raise ValueError('Each split needs reviewed images and at least one labeled object')
    return dict(splits=counts, collection_sessions=len(sessions), format_valid=True,
                quality_qualified=False, note='Format checks do not establish dataset sufficiency or label quality.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True, help='Extracted ShopAware data.yaml')
    parser.add_argument('--check-only', action='store_true', help='Validate files without downloading or training a model')
    parser.add_argument('--device', default='cpu', choices=['cpu', '0'], help='cpu or NVIDIA GPU index 0')
    parser.add_argument('--epochs', default=50, type=int)
    parser.add_argument('--batch', default=4, type=int)
    parser.add_argument('--output', type=Path, default=Path('runs/shopaware'))
    args = parser.parse_args()
    if not 1 <= args.epochs <= 1000 or not 1 <= args.batch <= 64:
        parser.error('epochs must be 1–1000 and batch must be 1–64')
    try:
        report = check_dataset(args.data)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(f'Dataset preflight failed: {exc}')
    print(json.dumps(report, indent=2))
    if args.check_only:
        return
    with local_training_callbacks():
        run_training(args, report)


def run_training(args, report):
    from ultralytics import YOLO
    name = f'camera-{uuid.uuid4().hex[:12]}'
    model = YOLO('yolo26n.pt')
    model.train(data=str(args.data.resolve()), epochs=args.epochs, batch=args.batch, device=args.device,
                project=str(args.output.resolve()), name=name, exist_ok=False, imgsz=640,
                workers=0, seed=0, deterministic=True, plots=True)
    checkpoint = Path(model.trainer.best)
    trained = YOLO(str(checkpoint))
    if trained.task != 'detect' or [trained.names[i] for i in range(len(trained.names))] != COCO_NAMES:
        raise RuntimeError('Trained checkpoint does not preserve the ShopAware detection class contract')
    test = trained.val(data=str(args.data.resolve()), split='test', device=args.device, batch=args.batch,
                       project=str(args.output.resolve()), name=f'{name}-test', plots=True)
    report.update(checkpoint=str(checkpoint.resolve()), test_metrics={k: float(v) for k, v in test.results_dict.items()},
                  activated=False, device=args.device)
    report_file = checkpoint.parent.parent / 'shopaware-report.json'
    report_file.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Training finished. Review {report_file}. Model has not been activated.')


if __name__ == '__main__':
    main()
