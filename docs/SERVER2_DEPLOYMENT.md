# Deploy ShopAware v0.1.0-beta.1 on server2

Target:

- Host: `server2`
- OS: Ubuntu 22.04 LTS class host with Apache and Docker
- Public URL: `https://shopaware.innawareucp.com`
- Recommended checkout: `/opt/shopaware`
- Persistent state: `/var/lib/shopaware`
- Backup root: `/var/backups/shopaware`
- Optional Apache placeholder docroot: `/var/www/shopaware`

Do **not** store the ShopAware database, Fernet key, incident footage or training data
under `/var/www`. Apache only reverse-proxies the application.

This is a beta deployment for real-environment qualification. Keep the repository
private and do not describe AI detections as proof of theft.

## 1. DNS and firewall

Create/confirm DNS for `shopaware.innawareucp.com` pointing to the public endpoint
that already serves server2. Only normal HTTPS/HTTP needs to be exposed publicly.
The ShopAware containers bind to loopback only:

- backend: `127.0.0.1:18080`
- dashboard: `127.0.0.1:18081`

Do not open those ports in the Internet-facing firewall.

## 2. Prepare the host

From the repository checkout you can run:

```bash
sudo bash deploy/server2/prepare-host.sh
```

Or manually:

```bash
sudo mkdir -p /opt/shopaware /var/www/shopaware
sudo mkdir -p /var/lib/shopaware/{data,alerts,incidents,models}
sudo mkdir -p /var/backups/shopaware
sudo chown "$USER:$USER" /opt/shopaware
sudo chmod 0750 /var/lib/shopaware /var/lib/shopaware/{data,alerts,incidents,models}
sudo chmod 0700 /var/backups/shopaware
sudo a2enmod proxy proxy_http proxy_wstunnel headers ssl rewrite
```

Verify Docker + Compose:

```bash
docker --version
docker compose version
```

If your normal login is not allowed to use Docker, add it to the Docker group and
start a new login session, or run the Compose commands with `sudo`.

## 3. Clone the private repository

Using SSH credentials that can access the private MusicCityTelecom repository:

```bash
cd /opt
git clone git@github.com:MusicCityTelecom/shopaware-theft-detection.git shopaware
cd /opt/shopaware
git fetch --all --tags
```

For this beta, deploy the release branch/tag created for `v0.1.0-beta.1`. If the tag
is available:

```bash
git checkout v0.1.0-beta.1
```

If GitHub Actions could not publish the tag because of an account Actions billing
restriction, use the immutable release-branch commit documented in the GitHub release
notes until the tag is created.

## 4. Create the production environment file

```bash
cd /opt/shopaware
cp deploy/server2/.env.production.example .env
chmod 0600 .env
nano .env
```

Required production settings include:

```dotenv
SHOPAWARE_CORS_ORIGINS=https://shopaware.innawareucp.com
SHOPAWARE_COOKIE_SECURE=true
SHOPAWARE_ENABLE_SPECIALIZED_MODEL=false
```

Start with the standard detector/pose models:

```dotenv
SHOPAWARE_DETECTION_MODEL=yolo26n.pt
SHOPAWARE_POSE_MODEL=yolo26n-pose.pt
```

Do not enable `shoplifting.pt` unless you intentionally provide and qualify that
checkpoint. SMTP can be configured later in the authenticated Settings UI.

The default evidence quota is 10 GiB. Check available space before deciding whether
to raise it:

```bash
df -h /var/lib/shopaware
```

## 5. Build the CPU deployment

Server2 can run the CPU image for initial functional qualification:

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml build --pull
```

Then start it:

```bash
docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d
```

Watch startup:

```bash
docker compose --env-file .env -f deploy/server2/docker-compose.yml ps
docker compose --env-file .env -f deploy/server2/docker-compose.yml logs -f --tail=200 shopaware
```

On first model use, Ultralytics may download the standard YOLO26 checkpoints.
Do not interrupt that initial load unless it is clearly failing.

## 6. Create the first administrator

The application ships with no default password. Create the first admin interactively:

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml exec shopaware python -m shopaware.auth
```

Use a unique password of at least 12 characters. Do not put that password in `.env`,
Git, shell history or deployment documentation.

## 7. Verify the loopback services

```bash
curl -fsS http://127.0.0.1:18080/health/live
curl -I http://127.0.0.1:18081/
```

Expected backend liveness response:

```json
{"status":"alive"}
```

The authenticated readiness/health view should be checked after login because model
state and most API routes require a session.

## 8. TLS certificate and Apache

Obtain/confirm a certificate for:

`shopaware.innawareucp.com`

The supplied Apache template expects the standard Let's Encrypt paths:

```text
/etc/letsencrypt/live/shopaware.innawareucp.com/fullchain.pem
/etc/letsencrypt/live/shopaware.innawareucp.com/privkey.pem
```

If your certificate tooling writes different paths, update the template before enabling it.

Install the vhost:

```bash
sudo cp deploy/server2/shopaware-apache.conf /etc/apache2/sites-available/shopaware.conf
sudo a2ensite shopaware.conf
sudo apache2ctl configtest
sudo systemctl reload apache2
```

The vhost routes:

- `/ws` directly to FastAPI on `127.0.0.1:18080` for authenticated WebSocket previews;
- all other paths to Next.js on `127.0.0.1:18081`;
- Next.js `/api/...` requests internally to the backend container.

No public access to ports 18080/18081 is needed.

## 9. Verify the public deployment

```bash
cd /opt/shopaware
bash deploy/server2/verify.sh shopaware.innawareucp.com
```

Then browse to:

`https://shopaware.innawareucp.com`

Log in with the admin account created above.

## 10. Add the first RTSP camera

In **Cameras**:

1. Enter a descriptive camera name.
2. Enter the RTSP URL **without embedded credentials**.
3. Enter username and password in their separate fields.
4. Save the camera.
5. Use **Test connection**.
6. Wait for status/live preview.
7. Configure merchandise/restricted/checkout/exit/ignore zones.

Do not paste `rtsp://user:password@host/...` into tickets, screenshots or logs.
ShopAware strips URL userinfo and encrypts saved camera passwords.

## 11. First real-camera qualification

Before trusting incident alerts, stage controlled tests and record results:

- normal walking and browsing;
- reaching into merchandise zones;
- holding/returning an item;
- staged concealment-like hand-to-waist behavior;
- checkout-zone traversal;
- exit-zone traversal;
- two cameras operating simultaneously;
- camera disconnect/reconnect;
- NVR reboot or RTSP interruption;
- incident snapshot and pre/post-event clip playback;
- false alarms during ordinary activity.

Issue #3 should remain open until two real simultaneous streams establish that the
per-camera tracker behavior is correct on actual detections.

## 12. Model qualification on server2

Use a representative image containing a person:

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml exec shopaware \
  python -m tools.qualify_models --image /app/data/representative-person.jpg --device cpu --output /app/data/cpu-qualification.json
```

Copy the image into `/var/lib/shopaware/data/representative-person.jpg` first.
Review `/var/lib/shopaware/data/cpu-qualification.json` afterward.

CPU inference may be too slow for many channels. That is a measurement question;
do not infer a supported channel count from successful startup alone.

## 13. NVIDIA GPU deployment later

The repository contains `docker-compose.gpu.yml`, but GPU deployment still requires:

- supported NVIDIA GPU/driver;
- NVIDIA Container Toolkit;
- a PyTorch CUDA wheel index matching the target environment;
- `torch.cuda.is_available()` returning true inside the container;
- actual latency/VRAM/channel-count measurement.

Do not switch the server2 deployment to a CUDA image unless those prerequisites are
confirmed.

## 14. Training from a camera

Open **Training** or **Cameras → Train with this camera**.

Create separate collection sessions for:

- Training
- Validation
- Test

Capture varied clean frames, annotate all relevant visible COCO objects, explicitly
review each example and export the dataset ZIP. Follow `docs/TRAINING.md` exactly.

Training on server2 is possible but not recommended while ShopAware is actively
analyzing cameras, especially on CPU. Prefer a separate training machine. If you do
train on server2, stop the ShopAware services first or accept that training and
real-time inference will compete for CPU/RAM/GPU.

Validate an export before training:

```bash
python -m tools.train_camera --data training-data/camera/data.yaml --check-only
```

CPU example:

```bash
python -m tools.train_camera --data training-data/camera/data.yaml --device cpu --epochs 50 --batch 4
```

A trained detector is not activated automatically. Compare it against the baseline,
then explicitly set the qualified checkpoint path in Settings and restart the backend.
Keep `yolo26n-pose.pt` as the pose model.

## 15. Backups

The Fernet key and SQLite database must be backed up together.

Basic online backup:

```bash
sudo bash /opt/shopaware/deploy/server2/backup.sh
```

To include evidence media as well:

```bash
sudo INCLUDE_MEDIA=1 bash /opt/shopaware/deploy/server2/backup.sh
```

Backups are written under `/var/backups/shopaware/<UTC timestamp>/` with checksums.
Protect these backups: the database can include private training-camera images and
the key decrypts stored camera/SMTP credentials.

## 16. Update procedure

Before an update:

```bash
sudo bash /opt/shopaware/deploy/server2/backup.sh
cd /opt/shopaware
git fetch --all --tags
```

Checkout the new release tag, then rebuild:

```bash
git checkout <new-tag>
docker compose --env-file .env -f deploy/server2/docker-compose.yml build --pull
docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d
```

The database uses additive migrations. Still retain the pre-update backup and matching
Fernet key until the new release is qualified.

## 17. Rollback

If a new release fails:

```bash
cd /opt/shopaware
git checkout v0.1.0-beta.1
docker compose --env-file .env -f deploy/server2/docker-compose.yml build
docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d
```

If a migration/data problem requires database restoration, stop the backend first and
restore **both** the database and its matching `.shopaware.key` from the same backup.
Do not mix a database with an unrelated encryption key.

## 18. Useful operational commands

```bash
cd /opt/shopaware

# status
docker compose --env-file .env -f deploy/server2/docker-compose.yml ps

# backend logs
docker compose --env-file .env -f deploy/server2/docker-compose.yml logs -f --tail=200 shopaware

# dashboard logs
docker compose --env-file .env -f deploy/server2/docker-compose.yml logs -f --tail=200 dashboard

# restart backend after settings/model changes
docker compose --env-file .env -f deploy/server2/docker-compose.yml restart shopaware

# restart everything
docker compose --env-file .env -f deploy/server2/docker-compose.yml restart

# stop
docker compose --env-file .env -f deploy/server2/docker-compose.yml down
```

Persistent data remains under `/var/lib/shopaware` when the containers are rebuilt
or removed.
