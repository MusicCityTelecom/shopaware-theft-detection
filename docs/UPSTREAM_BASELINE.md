# ShopAware upstream baseline

ShopAware will use [`vahapogut/Theft-Detection`](https://github.com/vahapogut/Theft-Detection) as an initial implementation reference/baseline rather than starting the video-analysis pipeline from zero.

## Pinned upstream revision

- Repository: `vahapogut/Theft-Detection`
- Branch: `main`
- Reviewed commit: `fdba673494878d7c8bed7b324e071a209c158dff`
- Upstream license: MIT
- Upstream copyright: Copyright (c) 2026 Abdulvahap Öğüt

The upstream MIT license and copyright notice must be preserved for copied or substantially derived upstream code.

## Why this baseline is useful

The upstream project already contains several features that overlap directly with ShopAware's goals:

- FastAPI backend
- Next.js dashboard
- Multiple camera feeds
- RTSP-compatible OpenCV input
- Ultralytics object detection
- Ultralytics pose estimation and person tracking
- Per-camera ROI configuration
- Basic item-concealment heuristics
- Loitering and restricted-zone rules
- SQLite alert history
- Snapshot capture
- SMTP email alerts
- Telegram alert support
- Optional specialized `shoplifting.pt` model support

The upstream implementation currently uses YOLOv8 checkpoints. ShopAware will preserve the useful higher-level pipeline but migrate its default object and pose models to **Ultralytics YOLO26**, the current Ultralytics model family, before treating the imported implementation as the ShopAware baseline.

This makes the project a better experimental starting point than building all of those pieces independently while avoiding a new project being anchored to an older model generation.

## YOLO26 migration decision

ShopAware will use YOLO26 for the default generic detection and pose paths:

- Object detection baseline: `yolo26n.pt`
- Pose/keypoint baseline: `yolo26n-pose.pt`
- Larger `s`, `m`, `l`, or `x` variants may be selected by deployment profile after benchmarking.

The application must not hard-code a specific generation into the business logic. Detector and pose model paths will be configurable, allowing custom retail/shoplifting checkpoints and future model upgrades without rewriting the incident pipeline.

The upstream API usage is close to the current Ultralytics API (`YOLO(...)`, `track(..., persist=True)`, `Results.boxes`, and `Results.keypoints`), but the migration still requires explicit regression testing rather than a blind filename replacement.

Migration qualification must verify:

- object detection output/class mappings
- COCO class assumptions used by the fallback item logic
- pose keypoint ordering and confidence handling
- persistent tracking IDs per camera
- tracker selection and behavior
- concealment heuristic behavior with YOLO26 keypoints
- per-camera state isolation
- CPU inference performance
- CUDA inference performance and VRAM use
- frame latency and sustainable inference FPS with multiple RTSP streams
- exported ONNX/TensorRT behavior where used
- alert threshold recalibration after the model change

YOLO26's improved pose model is particularly relevant because ShopAware's concealment logic depends heavily on wrist, hip, and other body keypoints.

## Important limitations found during review

The upstream implementation should **not** be deployed unchanged as a production loss-prevention system.

### 1. RTSP credentials are not stored securely

Camera configuration currently stores a single camera `source` string in `cameras.json`. If credentials are embedded in the normal RTSP form (`rtsp://user:password@host/...`), they are persisted in plaintext and can also leak through logs/API responses.

ShopAware must instead provide separate fields for:

- RTSP URL/host/path
- username
- password

Passwords must be encrypted at rest. Normal logs and API responses must never contain full credentials.

### 2. Detection language is too definitive

The upstream fallback state machine can emit messages such as `THEFT CONFIRMED (Item Concealed)` when a previously detected object disappears near a person's hip. That is a heuristic, not proof of theft.

ShopAware will use review-oriented event names such as:

- `suspected_concealment`
- `high_risk_item_interaction`
- `restricted_zone_interaction`
- `loitering_candidate`

A human must review incident evidence before any accusation or intervention.

### 3. Generic COCO classes are not sufficient for retail theft detection

The fallback implementation treats selected generic object classes as "stealable" objects and associates them with wrist proximity. This is useful for prototyping but is not sufficiently accurate for liquor-store or general retail inventory.

ShopAware should support:

- custom retail/product models
- store/camera-specific classes
- dedicated shoplifting/activity models
- future temporal/action-recognition models
- configurable confidence thresholds

YOLO26 is the default generic detector/pose platform, not a claim that an off-the-shelf COCO model by itself can identify theft reliably.

### 4. No incident video clip recorder

The upstream system stores alert JPEGs but not a useful evidence clip containing activity before and after the event.

ShopAware must add a rolling per-camera buffer and save configurable pre-event/post-event video, for example:

- 10-20 seconds before trigger
- 15-30 seconds after trigger
- MP4/H.264 where practical
- event snapshot
- event metadata JSON/database record

### 5. Camera lifecycle/reconnect needs hardening

The current OpenCV threaded capture implementation is a useful prototype but needs explicit:

- reconnect/backoff behavior
- health state
- watchdog/stall detection
- stream timeout handling
- graceful worker restart
- optional FFmpeg/GStreamer backend
- per-camera inference FPS limits

### 6. No application authentication/authorization

Camera feeds, configuration and incident media must not be exposed by an unauthenticated production deployment.

ShopAware needs at minimum:

- administrator login
- password hashing
- authenticated media/API routes
- session expiry
- audit trail for configuration changes and incident review

Role-based access can follow later.

### 7. Secrets/settings handling needs replacement

SMTP and Telegram settings need a proper encrypted secret store/configuration layer rather than ad-hoc local settings files.

### 8. Storage/retention is incomplete

ShopAware needs:

- configurable incident retention
- disk usage limits
- safe deletion
- database/media consistency checks
- optional NAS/object-storage destination later

### 9. Monolithic backend

The current `backend.py` is a large single module. ShopAware should preserve working behavior while splitting it into testable services for camera ingest, inference, tracking, event scoring, incident recording, alerts, configuration and API/UI functions.

### 10. Face recognition is not required for ShopAware MVP

The upstream project includes face blacklist/VIP features. Those features are not necessary to accomplish the initial ShopAware theft-detection goal and introduce additional privacy, dependency and deployment complexity.

For the first ShopAware release, face recognition should be disabled or removed from the default path unless there is a separate, explicit requirement to restore it later.

## ShopAware adaptation target

The first working ShopAware milestone should provide:

1. Browser-based camera management.
2. Separate encrypted RTSP credentials.
3. Connection test and automatic reconnect.
4. Multiple simultaneous channels.
5. YOLO26 object detection plus YOLO26 pose/tracking as the default baseline.
6. Configurable detector/pose model paths for custom checkpoints and future upgrades.
7. Conservative suspected-concealment event scoring.
8. Configurable per-camera merchandise, checkout and exit/restricted ROIs.
9. Rolling pre/post incident video recording.
10. Snapshot and metadata capture.
11. Incident review/history UI.
12. SMTP email alerts with snapshot and incident link.
13. Alert cooldown/deduplication.
14. CPU test mode and NVIDIA CUDA support.
15. Docker deployment.
16. Authentication before production exposure.
17. Tests around event scoring, credential redaction, tracking consistency, and YOLO26 regression behavior.

## Licensing note

The upstream application source is MIT licensed. ShopAware must retain the upstream MIT notice for copied/substantially derived code.

Ultralytics YOLO26 code/models have separate Ultralytics licensing (AGPL-3.0 and Enterprise options). The final public ShopAware repository must accurately document the licenses of application code, third-party dependencies and any redistributed model weights. Do not assume the upstream repository's MIT license automatically relicenses Ultralytics code or model artifacts.
