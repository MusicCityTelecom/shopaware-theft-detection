# YOLO26 Qualification Plan

ShopAware defaults to Ultralytics YOLO26 but does not treat a model-name change as sufficient qualification.

## Pinned software baseline

- `ultralytics==8.4.150`
- generic detection model: `yolo26n.pt`
- pose model: `yolo26n-pose.pt`
- optional specialized activity model: `shoplifting.pt`

The standard YOLO26 weights are not stored in this repository. Ultralytics can resolve them at runtime or an operator may supply local paths.

Official references:

- https://docs.ultralytics.com/models/yolo26/
- https://docs.ultralytics.com/tasks/pose/
- https://pypi.org/project/ultralytics/

## Pose/keypoint compatibility

ShopAware's initial concealment heuristic depends on the COCO person keypoint indices used by the pretrained YOLO26 pose model:

- left shoulder: 5
- right shoulder: 6
- left wrist: 9
- right wrist: 10
- left hip: 11
- right hip: 12

These assumptions must be checked against actual YOLO26 pose output before field deployment.

## Required model tests

### Load/inference

- [ ] `yolo26n.pt` loads on CPU.
- [ ] `yolo26n-pose.pt` loads on CPU.
- [ ] both models load on the target CUDA stack.
- [ ] one-image object inference returns `Results.boxes`.
- [ ] one-image pose inference returns `Results.boxes` and `Results.keypoints`.
- [ ] pose output includes 17 person keypoints for normal COCO-person detections.

### Tracking

- [ ] stable IDs persist across adjacent frames on one camera.
- [ ] IDs are not accidentally shared across independent cameras.
- [ ] tracker reset/reconnect behavior is defined and tested.
- [ ] stale person state is cleaned after a disconnect or track disappearance.

See issue #3 for the per-camera persistent-tracker isolation blocker.

### Theft-signal regression

Test representative sequences for:

- person reaches toward configured merchandise ROI;
- object appears near left/right wrist;
- item/object becomes unavailable after interaction;
- wrist approaches hip/waist region;
- normal item handling without concealment;
- item returned to shelf;
- shopper carries item openly;
- overlapping people;
- temporary detector dropout;
- camera occlusion and reconnect.

The application must produce candidate/review language, not definitive criminal accusations.

### Performance profiles

Benchmark at least:

- YOLO26n detection + YOLO26n-pose;
- YOLO26s detection + YOLO26s-pose;
- YOLO26m detection + YOLO26m-pose where hardware allows.

For each profile capture:

- GPU model / VRAM;
- input resolution;
- number of simultaneous streams;
- source stream FPS;
- inference FPS per camera;
- end-to-end frame latency;
- GPU utilization;
- VRAM utilization;
- CPU/RAM utilization;
- dropped/stale-frame rate.

## Deployment acceptance

A deployment profile is not considered qualified until it has:

1. reproducible package/model versions;
2. stable RTSP reconnect behavior;
3. proven per-camera tracker isolation;
4. measured sustainable stream count;
5. reviewed false-positive/false-negative samples;
6. verified incident snapshot and video evidence;
7. credential-redaction checks;
8. human-review workflow validation.
