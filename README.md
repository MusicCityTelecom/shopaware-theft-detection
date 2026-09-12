# ShopAware Theft Detection

ShopAware is a web-based retail video analytics project for ingesting RTSP camera streams, running computer-vision inference, identifying theft/suspicious-event candidates, preserving incident video, and sending real-time alerts.

> **Project status:** Early development / proof-of-concept. Do not treat detections as proof of theft. Human review is required before any intervention or accusation.

## Goals

- Add/manage RTSP cameras from a browser.
- Store RTSP credentials securely instead of exposing passwords in logs or UI responses.
- Run object detection and tracking against multiple live channels.
- Keep the detector behind a provider interface so inference backends can be changed without rewriting the application.
- Build temporal theft/suspicion scoring on top of detections and tracks rather than pretending a single-frame object detector can determine intent.
- Keep a rolling pre-event buffer and save pre/post-event incident clips.
- Send email alerts immediately when an incident crosses the configured threshold.
- Add SMS/text alert providers later without changing the incident pipeline.
- Support CPU development and NVIDIA GPU production deployments.
- Provide an incident-review UI with camera, timestamp, score/reason, snapshot, clip, and disposition.

## Important detection limitation

YOLOv8 is an object detector, not a complete theft detector. Reliable retail loss-prevention analytics require temporal evidence such as person tracking, merchandise-zone interaction, hand/object motion, concealment behavior, exit behavior, and usually a custom retail dataset/model. ShopAware therefore separates:

1. **Detection/tracking** — people, bags, products/custom classes and persistent track IDs.
2. **Signal extraction** — zone entry, dwell, product interaction, concealment-like motion, object disappearance, exit, etc.
3. **Decision engine** — combines signals over time into a scored incident candidate.
4. **Human review** — confirms/dismisses the candidate.

The first milestone intentionally produces **incident candidates**, not definitive theft accusations.

## Ultralytics licensing

Ultralytics YOLO is available under AGPL-3.0 and commercial/Enterprise licensing. ShopAware is being structured so the Ultralytics implementation is an optional detector adapter. Before deploying a private, proprietary, internal-business, or commercial product using Ultralytics code/models, confirm the applicable Ultralytics license and obtain an Enterprise license if required.

## Planned architecture

```text
Browser
  |
  v
FastAPI web/API  ----> SQL database
  |                     cameras / incidents / alert config
  |
  +----> Camera supervisor ----> RTSP ingest ----> frame buffer
                                   |                 |
                                   v                 +--> incident clip writer
                              detector adapter
                                   |
                              object tracker
                                   |
                              signal engine
                                   |
                              risk/decision engine
                                   |
                    +--------------+-------------+
                    |                            |
                 incident DB                  alert bus
                    |                            |
                 clip/snapshot               SMTP email
                                              SMS later
```

## Initial technology choices

- Python 3.12+
- FastAPI + Jinja2 web UI
- SQLAlchemy
- SQLite for local development; PostgreSQL/MySQL-compatible production configuration will follow
- OpenCV/FFmpeg for RTSP ingest and media handling
- Pluggable detector interface
- Optional Ultralytics YOLOv8 detector/tracker adapter
- SMTP email alerts
- Docker / Docker Compose

## Repository layout

```text
app/
  api/            HTTP/API routes
  core/           configuration, security, logging
  db/             database/session/model layer
  detectors/      inference backend adapters
  services/       camera, incidents, alerts, recording
  theft/          temporal signal + decision logic
  templates/      server-rendered web UI
  static/         CSS/JS/assets
tests/
docs/
```

## Development milestones

### M0 — Bootstrap
- Application skeleton and configuration
- Database models
- Camera CRUD
- Encrypted camera credentials
- Health endpoint
- Docker development environment

### M1 — RTSP + inference
- RTSP connectivity test
- Camera worker lifecycle/reconnect
- YOLOv8 detector adapter
- Tracking IDs
- Live per-camera status/metrics

### M2 — Incident recording + email
- Rolling pre-event frame/video buffer
- Incident snapshot
- Pre/post-event MP4 clip
- SMTP alerts
- Incident review UI

### M3 — Retail theft signals
- Configurable merchandise/checkout/exit zones
- Dwell and interaction signals
- Pose/hand proximity signals
- Concealment candidate model/rules
- Exit-with-risk escalation
- Per-camera sensitivity and cooldowns

### M4 — Production hardening
- GPU worker scheduling
- Multi-process/multi-host workers
- Redis/message queue if required by scale
- Retention policies
- Audit trail and user roles
- TLS/reverse-proxy deployment
- Backup/restore
- SMS provider adapter
- Dataset annotation/training workflow

## Security requirements

- Never log complete RTSP URLs containing credentials.
- Encrypt camera passwords at rest.
- Redact secrets from API responses and exception messages.
- Restrict camera URLs to authorized administrators.
- Store incident media outside the public static tree and serve it through authenticated routes.
- Use least-privilege database and filesystem permissions in production.

## Legal / operational note

ShopAware should be treated as a loss-prevention decision-support tool. Detection scores are probabilistic and can be wrong. A trained human should review evidence before taking action. Deployment should comply with applicable privacy, surveillance, employment, biometric, retention, and notice laws/policies.

## Next step

The bootstrap implementation lives on a feature branch and will add the runnable FastAPI application, camera model/API, encrypted credentials, detector abstraction, incident model, email-alert service, and Docker/dev configuration.
