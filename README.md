# ShopAware Theft Detection

ShopAware is a web-based retail video analytics project for ingesting RTSP camera streams, running computer-vision inference, identifying suspected theft/concealment events, preserving incident evidence, and sending real-time alerts.

> **Project status:** Early development / proof-of-concept. Do not treat detections as proof of theft. Human review is required before intervention or accusation.

## Current bootstrap status

The project is being bootstrapped from concepts and implementation patterns in `vahapogut/Theft-Detection` at reviewed upstream revision `fdba673494878d7c8bed7b324e071a209c158dff`, while replacing the upstream YOLOv8 defaults with configurable **Ultralytics YOLO26** models.

Current defaults:

- Detection: `yolo26n.pt`
- Pose/keypoints: `yolo26n-pose.pt`
- Optional specialized activity model: `shoplifting.pt` when present
- Ultralytics runtime pinned in `requirements.txt`

The current bootstrap includes:

- FastAPI API and WebSocket live previews
- reconnecting multi-camera RTSP workers
- separate RTSP URL / username / encrypted password storage
- masked camera URLs in normal API responses
- configurable YOLO26 detection and pose model paths
- upstream-compatible item/hand/hip concealment signals with conservative incident language
- SQLite camera and incident persistence
- annotated incident snapshots
- rolling pre-event frame buffers
- configurable post-event recording continuation
- asynchronous MP4 evidence-clip finalization
- incident review states and browser playback
- SMTP alert plumbing
- adapted Next.js ShopAware dashboard
- Docker / Docker Compose bootstrap
- unit-test and GitHub Actions scaffolding

Authentication, richer zone types, retention/disk quotas, tracker-isolation qualification, and live YOLO26 hardware qualification remain before production use.

## Quick development start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements-dev.txt
cp .env.example .env
pytest -q
uvicorn backend:app --reload --host 0.0.0.0 --port 8000
```

In another shell:

```bash
cd dashboard
cp .env.example .env.local
npm install
npm run dev
```

Default URLs:

- API/docs: `http://127.0.0.1:8000/docs`
- Dashboard: `http://127.0.0.1:3000`

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

## Incident evidence

Each enabled camera maintains a bounded sampled pre-event buffer. When an incident candidate is created, ShopAware seeds a clip from the preceding buffer and continues collecting frames for a configurable post-event window.

Default development values:

- 15 seconds pre-event
- 30 seconds post-event
- 6 evidence frames/second

The alert snapshot is annotated with the AI trigger context. The evidence clip is buffered from the original camera frames before ShopAware draws overlays.

The first implementation writes an MP4 using OpenCV's portable `mp4v` path. FFmpeg/H.264/H.265 output and codec qualification remain deployment work.

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
        +--> Camera manager --> RTSP workers --> rolling evidence buffers
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
        |        +--> annotated snapshot
        |        +--> pre/post-event MP4
        |        +--> review status
        |
        +--> SMTP alert provider
                 +--> SMS provider later
```

## Known qualification blocker: multi-camera tracker state

The application currently keeps its own behavioral state under `(camera_id, track_id)`, but that alone does not prove that one Ultralytics `persist=True` tracker instance is internally isolated across sequential frames from different cameras.

Issue #3 tracks this explicitly. Multi-camera tracking must not be described as production-qualified until independent tracker state is proven or implemented per camera.

## Security requirements

- Never log complete RTSP URLs containing credentials.
- Keep camera passwords encrypted at rest.
- Redact secrets from API responses and exception messages.
- Keep runtime encryption keys outside Git.
- Store incident media outside frontend static assets in production.
- Add application authentication before exposing ShopAware outside a trusted development network.
- Replace the current development static-media mounts with authenticated evidence routes before production exposure.

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
- [x] adapt Next.js dashboard
- [ ] live YOLO26 regression qualification
- [ ] prove/fix per-camera tracker isolation (#3)

### Incident evidence
- [x] rolling pre-event buffer
- [x] post-event continuation
- [x] prototype MP4 incident clips
- [x] dashboard clip playback
- [ ] FFmpeg/H.264/H.265 deployment encoder
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
