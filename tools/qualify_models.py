"""Opt-in real YOLO26 qualification. Run with representative, authorized imagery."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import cv2
import psutil
import torch
from ultralytics import YOLO

from shopaware.tracking import CameraTrackingContext


def qualify(model_path: str, task: str, image, device: str, repeats: int) -> dict:
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable; no GPU qualification performed')
    model = YOLO(model_path)
    rss_before = psutil.Process().memory_info().rss
    warmup = time.perf_counter()
    for _ in range(3):
        model.predict(image, device=device, verbose=False)
    if device.startswith('cuda'):
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    warmup_seconds = time.perf_counter() - warmup
    durations, rss_samples = [], []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = model.predict(image, device=device, verbose=False)[0]
        if device.startswith('cuda'):
            torch.cuda.synchronize()
        durations.append(time.perf_counter() - start)
        rss_samples.append(psutil.Process().memory_info().rss)
    assert result is not None and result.boxes.data.shape[1] == 6
    assert str(model.names[0]).lower() == 'person'
    assert len(result.boxes) > 0, 'Representative input must produce detections'
    details = {}
    if task == 'pose':
        assert result.keypoints is not None and result.keypoints.data.shape[1] == 17
        assert result.keypoints.data.shape[2] == 3
        assert all(result.boxes.cls.cpu().numpy() == 0)
        context = CameraTrackingContext()
        tracked = context.update(result, 1)
        again = context.update(result, 2)
        assert len(tracked.boxes) and len(again.boxes)
        assert (tracked.boxes.id == again.boxes.id).all()
        details = {'keypoints': 17, 'wrists': [9,10], 'hips': [11,12], 'adapter_tracking': 'passed'}
    return dict(model=model_path, task=task, device=str(result.boxes.data.device),
                inference_count=repeats, warmup_seconds=warmup_seconds,
                mean_latency_seconds=statistics.mean(durations),
                p95_latency_seconds=sorted(durations)[min(repeats-1,int(.95*repeats))],
                measured_fps=1/statistics.mean(durations), rss_before=rss_before,
                sampled_peak_rss=max(rss_samples),
                cuda_peak_allocated=torch.cuda.max_memory_allocated() if device.startswith('cuda') else None,
                **details)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--device', default='cpu', choices=['cpu','cuda:0'])
    parser.add_argument('--detection-model', default='yolo26n.pt')
    parser.add_argument('--pose-model', default='yolo26n-pose.pt')
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--output', type=Path, default=Path('qualification.json'))
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    image = cv2.imread(str(args.image))
    if image is None:
        parser.error('Input image could not be decoded')
    report = {'cuda_available': torch.cuda.is_available(), 'models': []}
    for model, task in [(args.detection_model,'detect'), (args.pose_model,'pose')]:
        report['models'].append(qualify(model, task, image, args.device, args.repeats))
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Qualification measurements written to {args.output}')


if __name__ == '__main__':
    main()
