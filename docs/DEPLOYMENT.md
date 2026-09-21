# Development deployment and qualification

For the CPU release, follow [the complete Server2 guide](SERVER2_DEPLOYMENT.md).
Unit tests do not establish live-camera accuracy. Do not run multiple backend processes against one
appliance database: camera ownership, recording and maintenance are single-process.

## Local setup

1. Install Python 3.12+, create a virtual environment and install
   `requirements-dev.txt`. Keep `ultralytics==8.4.150` pinned.
2. Copy `.env.example` to `.env`. For **local HTTP only**, set
   `SHOPAWARE_COOKIE_SECURE=false`. Keep it `true` behind HTTPS. Set
   `SHOPAWARE_CORS_ORIGINS` to the actual dashboard origin (scheme, host and port).
   Use the same hostname for the dashboard and WebSocket endpoint.
3. Run `python -m shopaware.auth`. This interactive CLI asks for the administrator
   name and a password of at least 12 characters; no default account is created.
4. Run `uvicorn backend:app --host 127.0.0.1 --port 8000` (one worker).
5. In `dashboard`, run `corepack pnpm install --frozen-lockfile`, then
   `corepack pnpm dev`. Node 24 and pnpm 11.19.0 are the verified toolchain.
6. Open `http://localhost:3000`, log in, add a camera with separate credentials,
   test connectivity, and configure normalized zones from the Cameras page.

Normal unit tests: `python -m pytest -q`. They do not download model checkpoints.
Frontend checks: `corepack pnpm lint` and `corepack pnpm build`.
If the host restricts Ultralytics' default settings directory, set
`YOLO_CONFIG_DIR` to a writable directory before importing the package.

The dashboard uses a same-origin `/api` proxy to the backend. Set server-side
`SHOPAWARE_BACKEND_URL` when the backend is elsewhere. For HTTPS deployments,
configure a TLS reverse proxy for both HTTP and WebSocket traffic. Public hostnames
default to same-origin `/ws`; localhost uses port 8000. An explicit build-time
`NEXT_PUBLIC_WS_URL` can override this. The Server2 guide includes Apache routing.

## Authentication and secrets

All API/documentation/media routes except `/health/live` and login require a
session. Session tokens are random, stored as SHA-256 hashes in SQLite, expire
after 12 hours and are revoked by logout. Cookies use HttpOnly and SameSite=Strict;
Secure is enabled by default. Mutating requests require a trusted Origin and a
session CSRF token (`X-CSRF-Token`), except login which requires a trusted Origin.
Login throttling is bounded and local to the process; it is not a distributed
identity service. Administrators manage all cameras; regular users can view and review cameras assigned directly or through customer groups. See [Users and customers](USERS_AND_CUSTOMERS.md).

RTSP passwords and saved SMTP passwords are Fernet-encrypted. Back up the database
and encryption key together. A wrong/missing key must not be worked around by
discarding encrypted data. An unreadable camera credential fails initialization.
Password fields are write-only. Camera URL userinfo is discarded; submit the
replacement username/password in separate fields. Credential-like URL query
parameters are rejected. Do not store credentials in model paths or logs.

Settings saved in the dashboard take effect after backend restart. They override
the corresponding environment defaults on startup; SMTP password replacement is
encrypted and omitted from read responses. Blank UI password preserves the saved
password. The authenticated settings API accepts an explicit empty string to clear it.

## Data, evidence and retention

SQLite uses schema versions (`PRAGMA user_version`) and additive transactional
migrations. Existing cameras/incidents survive migration. Sessions reference users
and zones reference cameras with foreign keys. Incident camera IDs intentionally
remain historical references after camera deletion, preserving review evidence.

Capture, evidence sampling and inference are separate. Default inference is 5 FPS,
evidence 6 FPS, with 15 seconds pre-event and 30 seconds post-event. Each recorder
allows four in-flight clips and one writer, a 32 MiB prebuffer and 96 MiB per-clip
limit. At capacity, clips can be declined or marked failed; the incident persists.
Memory bounds can truncate available prehistory on large/high-entropy images.

Release images use FFmpeg H.264/yuv420p with fast-start MP4 metadata. Browser playback,
seeking and authenticated byte ranges passed in Edge. Local development without
FFmpeg falls back to OpenCV `mp4v`; install FFmpeg for browser-compatible clips. Writers
decode incrementally, normalize resolution, hold previous frames across sampling
gaps, verify output frame count and store source timestamps in a JSON sidecar.
Missing frames are not fabricated as observed video; the sidecar records actual
capture timestamps. Shutdown joins writer work. Stalled streams finalize through
maintenance. After a process restart, unfinished rows are marked `interrupted`.

Maintenance runs retention every minute: default 30 days and 10 GiB across the
flat snapshot/clip directories. Oldest eligible incident media and rows are deleted
when expired or over quota. Pending/in-flight recordings are excluded. Unknown
files are counted but not deleted. If active/unknown media exceeds quota, status
reports it rather than deleting active evidence. Filesystem and SQLite deletion
cannot be one atomic transaction; failed deletions retain the row, and authenticated
media routes return 404 for missing media. The Server2 guide includes consistent
backups and restoration of database, matching key, configuration and evidence.

## CPU and NVIDIA Docker paths

CPU development: `docker compose up --build`. The backend image installs CPU
PyTorch wheels by default. Run admin setup with
`docker compose exec shopaware python -m shopaware.auth` and configure the cookie
setting for local HTTP. Dashboard and backend ports bind to loopback by default.

NVIDIA path: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build`.
This requests all GPUs and changes the PyTorch wheel index to `cu130`. Override
`SHOPAWARE_TORCH_INDEX_URL` for a runtime supported by the target driver and GPU;
use the [official PyTorch installer](https://pytorch.org/get-started/locally/).
The host needs Docker GPU support/NVIDIA Container Toolkit. Verify
`torch.cuda.is_available()` inside the image before benchmarking. Neither Docker
image nor GPU deployment was executed in this Windows session; Docker was unavailable.

## Required real-environment qualification

Opt-in local model check:

```sh
python -m tools.qualify_models --image representative-person.jpg --device cpu --output cpu.json
python -m tools.qualify_models --image representative-person.jpg --device cuda:0 --output gpu.json
```

This intentionally loads/downloads YOLO26n detection and pose checkpoints. It checks
COCO person class, detection results, 17 pose keypoints, wrist indices 9/10, hip
indices 11/12, and adapter tracking. It records warm-up, latency, measured FPS,
sampled process RSS and CUDA peak allocation when CUDA actually runs. CPU testing
is also available through the manual-only model-qualification workflow. Unit CI
and the ordinary PR workflow do not invoke these checkpoint tests.

Tommy's environment must still establish two independent simultaneous NVR streams,
tracker behavior on real detections, reconnect under real failures, frozen-image
detection, H.264/H.265 decoder behavior, actual incident evidence quality, SMTP
delivery, sustained retention/disk-full recovery, CPU throughput, GPU latency,
VRAM and sustainable channel count. No real camera, checkpoint, SMTP or GPU
qualification is implied by unit tests. For beta.2, actual YOLO26 detection and pose
CPU inference passed on bundled sample imagery; real cameras, SMTP, GPU and Server2
capacity remain unqualified. Issue #3 remains open for real multi-camera evidence.

Remaining software limits include pixel-scale wrist/hip heuristic distances,
uncalibrated risk weights, global rather than per-camera inference/threshold
settings, no camera credential-edit form yet, no frozen-image-content watchdog,
no durable alert outbox/retry, no PostgreSQL adapter, and JSON/base64 WebSocket preview scaling.
Typed ignore zones mask inference/preview processing but preserve original evidence.
