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
- YOLOv8 object detection
- YOLOv8 pose estimation and person tracking
- Per-camera ROI configuration
- Basic item-concealment heuristics
- Loitering and restricted-zone rules
- SQLite alert history
- Snapshot capture
- SMTP email alerts
- Telegram alert support
- Optional specialized `shoplifting.pt` model support

This makes it a better experimental starting point than building all of those pieces independently.

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

The fallback implementation treats selected generic YOLO object classes as "stealable" objects and associates them with wrist proximity. This is useful for prototyping but is not sufficiently accurate for liquor-store or general retail inventory.

ShopAware should support:

- custom retail/product models
- store/camera-specific classes
- dedicated shoplifting/activity models
- future temporal/action-recognition models
- configurable confidence thresholds

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
5. YOLOv8 pose/tracking baseline retained from upstream.
6. Conservative suspected-concealment event scoring.
7. Configurable per-camera merchandise, checkout and exit/restricted ROIs.
8. Rolling pre/post incident video recording.
9. Snapshot and metadata capture.
10. Incident review/history UI.
11. SMTP email alerts with snapshot and incident link.
12. Alert cooldown/deduplication.
13. CPU test mode and NVIDIA CUDA support.
14. Docker deployment.
15. Authentication before production exposure.
16. Tests around event scoring and credential redaction.

## Licensing note

The upstream application source is MIT licensed. ShopAware must retain the upstream MIT notice for copied/substantially derived code.

Ultralytics itself has separate licensing terms. The final public ShopAware repository must accurately document the licenses of application code, third-party dependencies and any redistributed model weights. Do not assume the upstream repository's MIT license automatically relicenses Ultralytics code or model artifacts.
