# ShopAware Theft Detection

ShopAware is a web-based video analytics project for ingesting RTSP camera streams, running computer-vision inference, recording review candidates and observations, preserving incident evidence, and sending real-time alerts.

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
- independently selectable Shoplifting, Vehicle break-in, LPR and Face Capture modes per camera
- persisted **per-camera settings for every analytics mode**, with beta.4-compatible defaults and live apply
- confidence-scored plate OCR snapshots and broad vehicle-color estimates
- anonymous face snapshots grouped only by a continuous per-camera track
- temporal person/vehicle interaction candidates for parking-lot review
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

The branch now adds per-camera ByteTrack isolation, local admin authentication,
protected evidence, typed normalized zones, heuristic risk scoring, bounded media
writers, provider-based SMTP, schema migrations, retention/quota management and
editable runtime settings. See [deployment/setup and exact remaining limits](docs/DEPLOYMENT.md).
Real cameras and Server2 capacity still require qualification. YOLO26 detection/pose CPU inference and tracking were checked on bundled sample imagery for this release.

For Server2, use the complete [deployment guide](docs/SERVER2_DEPLOYMENT.md), [beta.5 upgrade procedure](docs/SERVER2_BETA5_UPGRADE.md), [camera-mode guide](docs/CAMERA_MODES.md), and [camera training instructions](docs/SERVER2_TRAINING.md).

The dashboard includes **Training**, with a **Train with this camera** shortcut, and an administrator-only **Mode Settings** page for per-camera Shoplifting, Vehicle break-in, LPR and Face Capture tuning. Capture examples from a selected camera, draw object boxes, review labels and export a YOLO dataset. A separate command checks, trains and evaluates a new detector; activation remains explicit. See [the camera training guide](docs/TRAINING.md).

## Quick development start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements-dev.txt
cp .env.example .env
pytest -q
# Configure local HTTP cookie setting as documented in docs/DEPLOYMENT.md
python -m shopaware.auth
uvicorn beta5_backend:app --reload --host 0.0.0.0 --port 8000
```

In another shell:

```bash
cd dashboard
cp .env.example .env.local
corepack pnpm install --frozen-lockfile
corepack pnpm dev
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

The release images encode browser MP4 evidence with FFmpeg H.264/yuv420p and fast-start metadata. Local development falls back to OpenCV `mp4v` if FFmpeg is unavailable. Source timestamps are retained alongside each clip.

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

Shared YOLO26 pose inference now feeds an independent ByteTrack context and ID allocator per camera. Reconnect, resolution change and deletion clear that camera's temporal state. Automated tests compare isolated and interleaved tracking outputs, including keypoint alignment and lifecycle cleanup. See [tracker architecture and source investigation](docs/TRACKER_ISOLATION.md).

Issue #3 remains open for real two-camera/model and hardware qualification. Synthetic test results do not establish end-to-end deployment quality.

## Security requirements

- Never log complete RTSP URLs containing credentials.
- Keep camera passwords encrypted at rest.
- Redact secrets from API responses and exception messages.
- Keep runtime encryption keys outside Git.
- Store incident media outside frontend static assets in production.
- Admin sessions protect API, WebSocket preview and incident media. Configure HTTPS, trusted origins and secure cookies as described in the deployment guide.
- Evidence is served by incident ID through authenticated routes with media-root containment checks.

## Upstream attribution and licensing

The upstream `vahapogut/Theft-Detection` project is MIT licensed. Its required attribution is retained in `THIRD_PARTY_NOTICES.md` and `docs/UPSTREAM_BASELINE.md`.

Ultralytics software/models have separate licensing terms. Do not assume the upstream MIT license relicenses Ultralytics code or model weights. Model weights are not committed to this repository.

## Development roadmap

### Bootstrap
- [x] repository initialized
- [x] upstream baseline reviewed/pinned
