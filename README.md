# ShopAware Theft Detection

ShopAware is a web-based retail video analytics project for ingesting RTSP camera streams, running computer-vision inference, identifying suspected theft/concealment events, preserving incident evidence, and sending real-time alerts.

> **Project status:** Early development / proof-of-concept. Do not treat detections as proof of theft. Human review is required before intervention or accusation.

## Current bootstrap status

The project is being bootstrapped from concepts and implementation patterns in `vahapogut/Theft-Detection` at reviewed upstream revision `fdba673494878d7c8bed7b324e071a209c158dff`, while replacing the upstream YOLOv8 defaults with configurable **Ultralytics YOLO26** models.

Current defaults:

- Detection: `yolo26n.pt`
- Pose/keypoints: `yolo26n-pose.pt`
- Optional specialized activity model: `shoplifting.pt` when present

The backend already includes:

- FastAPI API and WebSocket live-frame transport
- multi-camera RTSP capture workers
- automatic reconnect/backoff
- encrypted RTSP password storage
- masked RTSP URLs in API responses
- YOLO26 detection and YOLO26 pose/tracking configuration
- upstream-compatible object/hand/hip concealment heuristics
- conservative `suspected_concealment` incident language
- SQLite camera/incident persistence
- incident snapshots and review status
- SMTP email alert plumbing
- Docker/Docker Compose bootstrap
- unit tests for credential redaction
- optional model-load smoke tests

Incident video clips, authentication, the adapted Next.js dashboard, richer zone types, and model benchmarking are next.

## Quick development start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements-dev.txt
cp .env.example .env
pytest -q
uvicorn backend:app --reload --host 0.0.0.0 --port 8000
```

Then open FastAPI docs at `http://127.0.0.1:8000/docs`.

The standard YOLO26 checkpoints are intentionally not committed to this repository. Ultralytics can resolve/download them at runtime, or operators can configure alternate model paths through environment variables.

## Camera API

Add an RTSP camera with credentials separated from the URL:

```json
{
  "name": "Liquor Aisle 1",
  "rtsp_url": "rtsp://10.0.0.25:554/Streaming/Channels/101",
  "username": "camera-user",
  "password": "camera-password"
}
```

Passwords are encrypted at rest. Normal camera-list responses return a masked stream representation rather than the decrypted password.

## Detection model

ShopAware does not assume that a generic object detector can prove theft. The initial event pipeline combines:

1. person pose/keypoint tracking;
2. object proximity to wrists;
3. item disappearance after interaction;
4. wrist movement near the waist/hip area;
5. configured camera ROI interactions;
6. temporal state per tracked person.

A candidate event is stored for review instead of being labeled as confirmed theft.

The first specialized-model path remains compatible with a separately supplied `shoplifting.pt`, but the long-term goal is a trained ShopAware retail/action model with measured precision/recall against representative footage.

## Planned architecture

```text
Browser / Dashboard
        |
        v
FastAPI API + WebSocket
        |
        +--> Camera manager --> RTSP workers --> rolling frame pipeline
        |                           |
        |                           +--> YOLO26 detection
        |                           +--> YOLO26 pose/tracking
        |                           +--> custom model (optional)
        |                                      |
        |                                      v
        |                              temporal risk engine
        |                                      |
        +--> SQLite incidents <---------------+
        |        |
        |        +--> snapshot / clip evidence
        |        +--> review status
        |
        +--> SMTP alert provider
                 +--> SMS provider later
```

## Security requirements

- Never log complete RTSP URLs containing credentials.
- Keep camera passwords encrypted at rest.
- Redact secrets from API responses and exception messages.
- Keep runtime encryption keys outside Git.
- Store incident media outside frontend static assets in production.
- Add application authentication before exposing ShopAware outside a trusted development network.

## Upstream attribution and licensing

The upstream `vahapogut/Theft-Detection` project is MIT licensed. Its required attribution is retained in `THIRD_PARTY_NOTICES.md` and `docs/UPSTREAM_BASELINE.md`.

Ultralytics software/models have separate licensing terms. Do not assume the upstream MIT license relicenses Ultralytics code or model weights. Model weights are not committed to this repository.

## Development roadmap

### Bootstrap
- [x] repository initialized
- [x] upstream baseline reviewed/pinned
- [x] YOLO26 selected for new default detector/pose paths
- [x] configurable model paths
- [x] encrypted RTSP password storage
- [x] reconnecting multi-camera backend baseline
- [x] snapshot incidents + review states
- [x] SMTP alert plumbing
- [x] Docker bootstrap
- [x] unit-test/CI bootstrap
- [ ] import/adapt Next.js dashboard
- [ ] live YOLO26 regression qualification

### Incident evidence
- [ ] rolling pre-event buffer
- [ ] post-event continuation
- [ ] MP4/H.264 incident clips
- [ ] retention/disk quota
- [ ] authenticated evidence routes

### Detection quality
- [ ] merchandise / restricted / checkout / exit zone types
- [ ] scored multi-signal incidents
- [ ] candidate deduplication
- [ ] per-camera thresholds
- [ ] YOLO26n/s/m benchmarking
- [ ] retail dataset/annotation workflow
- [ ] ShopAware-specific trained model

### Production hardening
- [ ] authentication and roles
- [ ] audit trail
- [ ] production reverse proxy/TLS
- [ ] GPU deployment profiles
- [ ] SMS alert provider
- [ ] backup/restore

## Safety / operational note

ShopAware is a loss-prevention decision-support system. Computer-vision detections are probabilistic and can be wrong. A trained human should review incident evidence before taking action.
