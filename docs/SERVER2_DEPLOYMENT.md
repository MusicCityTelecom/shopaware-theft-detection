# Deploy ShopAware v0.1.0-beta.4 on Server2

This guide installs the CPU release at **https://shopaware.innawareucp.com**. Run the commands on Server2 as `installer`, using `sudo` where shown. No deployment was performed by the release review.

## Confirmed starting point

Read-only inspection on 2026-09-14 found Ubuntu 22.04.5 LTS, x86_64, 8 logical CPUs, 15 GiB RAM (about 3.3 GiB available), Docker Compose 2.40.3, Apache 2.4.52 and Certbot. `/opt/shopaware` already contains the clean beta.1 checkout and an `.env`; persistent directories exist but the data directory is empty. No ShopAware containers were present.

Port **18080 belongs to another service**. This release uses backend **127.0.0.1:18082** and dashboard **127.0.0.1:18081**. DNS already points to Server2, but there was no ShopAware Apache vhost or matching TLS certificate. Recheck these facts if deploying later.

Start with **one camera and 2 inference FPS**. The server also runs other workloads. Build the images sequentially, watch available RAM, and measure camera latency before adding channels. Successful CPU inference does not establish a supported camera count or theft-detection accuracy.

## 1. Connect and check the checkout

Connect the required VPN or enable the approved source IP first. From Windows PowerShell or another SSH client:

```bash
ssh -p 60022 installer@50.206.74.206
```

Use your configured SSH key or enter the password at the interactive prompt. Never put the password in a command, Git, or `.env`.

On Server2:

```bash
cd /opt/shopaware
git status --short --branch
git fetch origin --tags
git switch --detach v0.1.0-beta.4
cat VERSION
docker compose version
free -h
df -h /var/lib/shopaware
sudo ss -ltnp '( sport = :18081 or sport = :18082 )'
```

`VERSION` must print `0.1.0-beta.4`; both ports must be free. If Git reports local changes, preserve and review them before switching; do not reset the checkout. If the checkout is absent on a replacement host, clone `git@github.com:MusicCityTelecom/shopaware-theft-detection.git` into `/opt/shopaware` using an account/key with repository access. Docker is already installed on Server2; replacement hosts can follow [Docker's Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/).

## 2. Prepare directories and configuration

```bash
cd /opt/shopaware
sudo bash deploy/server2/prepare-host.sh
if [ ! -f .env ]; then
  cp deploy/server2/.env.production.example .env
fi
chmod 0600 .env
nano .env
```

Preserve the existing `.env`; compare it with the new example and set these values:

```dotenv
SHOPAWARE_BACKEND_PORT=18082
SHOPAWARE_DASHBOARD_PORT=18081
SHOPAWARE_CORS_ORIGINS=https://shopaware.innawareucp.com
SHOPAWARE_COOKIE_SECURE=true
SHOPAWARE_DETECTION_MODEL=yolo26n.pt
SHOPAWARE_POSE_MODEL=yolo26n-pose.pt
SHOPAWARE_ENABLE_SPECIALIZED_MODEL=false
SHOPAWARE_INFERENCE_FPS=2
SHOPAWARE_RETENTION_DAYS=30
SHOPAWARE_MAX_STORAGE_BYTES=10737418240
```

The Compose profile forces persistent database, key, evidence and model paths, plus FFmpeg H.264 recording. Keep the default CPU PyTorch index. There is no NVIDIA setup step. SMTP can be entered later in Settings.

The persistent directories are:

| Host path | Container path | Contents |
| --- | --- | --- |
| `/var/lib/shopaware/data` | `/app/data` | SQLite database, encryption key, Ultralytics settings |
| `/var/lib/shopaware/alerts` | `/app/alerts` | Incident, plate and face snapshots |
| `/var/lib/shopaware/incidents` | `/app/incidents` | MP4 clips and timestamp sidecars |
| `/var/lib/shopaware/models` | `/app/models` | Downloaded and qualified checkpoints |
| `/var/lib/shopaware/training` | `/app/training-data` | Exported datasets |
| `/var/lib/shopaware/runs` | `/app/runs` | Training outputs |

Do not put these directories under Apache's document root. The 10 GiB evidence quota does not limit training datasets, model weights or backups.

## 3. Build and start

```bash
cd /opt/shopaware
COMPOSE_PARALLEL_LIMIT=1 docker compose --env-file .env -f deploy/server2/docker-compose.yml build --pull
docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d --wait --wait-timeout 300
docker compose --env-file .env -f deploy/server2/docker-compose.yml ps
docker compose --env-file .env -f deploy/server2/docker-compose.yml logs --tail=100 shopaware
curl --fail http://127.0.0.1:18082/health/live
curl --fail http://127.0.0.1:18081/api/health/live
```

The first build downloads packages, including Tesseract OCR; the first backend start downloads standard YOLO26 checkpoints into `/var/lib/shopaware/models`. Model initialization retries after transient download failures. Liveness only proves the process is serving requests; it does not prove the models are ready. One backend process owns the cameras; do not scale its workers or replicas.

Run the packaged installation check after startup:

```bash
docker compose --env-file .env -f deploy/server2/docker-compose.yml exec -T shopaware python -m tools.check_runtime
```

It checks database integrity, key readability, actual H.264 encoding, and real detection/pose CPU inference on Ultralytics' bundled sample image. It creates no accounts, cameras or incidents. Expect version `0.1.0-beta.4`, codec `h264`, and `cpu_inference: passed`. This check can briefly compete with live inference; run it before adding cameras.

## 4. Create your administrator

```bash
docker compose --env-file .env -f deploy/server2/docker-compose.yml exec shopaware python -m shopaware.auth
```

Enter a username and unique password of at least 12 characters at the prompts. There is no default account. Run this once for each new administrator; repeating the same username fails rather than replacing its password.

## 5. Issue the domain certificate

Server2 already runs Apache for other sites. Only install the ShopAware vhost; do not disable other sites or stop Apache.

Check that another administrator has not added this domain since inspection:

```bash
sudo apache2ctl -S
getent ahostsv4 shopaware.innawareucp.com
```

If a ShopAware vhost now exists, update that domain's configuration instead of creating a duplicate. Otherwise bootstrap HTTP certificate validation:

```bash
cd /opt/shopaware
sudo cp deploy/server2/shopaware-http.conf /etc/apache2/sites-available/shopaware.conf
sudo a2ensite shopaware.conf
sudo apache2ctl configtest && sudo systemctl reload apache2
sudo install -d /var/www/shopaware/.well-known/acme-challenge
printf 'shopaware-acme-check\n' | sudo tee /var/www/shopaware/.well-known/acme-challenge/shopaware-check >/dev/null
curl --fail http://shopaware.innawareucp.com/.well-known/acme-challenge/shopaware-check
sudo certbot certonly --webroot -w /var/www/shopaware -d shopaware.innawareucp.com
```

The probe must return `shopaware-acme-check`. Let Certbot prompt for the certificate contact and terms. Public port 80 must reach this Apache vhost for webroot validation. If access rules block the certificate authority, configure DNS validation through your DNS provider before continuing; do not disable TLS verification. [Certbot's webroot documentation](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot) explains this validation method.

Then install HTTPS routing:

```bash
sudo cp deploy/server2/shopaware-apache.conf /etc/apache2/sites-available/shopaware.conf
sudo apache2ctl configtest && sudo systemctl reload apache2
sudo install -m 0755 deploy/server2/renew-apache.sh /etc/letsencrypt/renewal-hooks/deploy/shopaware-apache
sudo certbot renew --cert-name shopaware.innawareucp.com --dry-run
systemctl list-timers --all | grep -i certbot
```

Confirm a Certbot renewal timer is present. The supplied deploy hook checks Apache configuration and reloads it after renewal. If Certbot reports different certificate paths, update `shopaware.conf` to its actual paths before reloading. The template keeps the HTTP ACME challenge reachable for renewals.

Apache sends `/ws` to backend port 18082 and other requests to dashboard port 18081. Keep both ports on loopback; expose only the intended HTTP/HTTPS entry point. If you deliberately change ports in `.env`, update the Apache template and verifier environment to match.

## 6. Verify and add your first camera

```bash
cd /opt/shopaware
bash deploy/server2/verify.sh shopaware.innawareucp.com
```

This requires valid TLS, working HTTP proxies, and **401** from protected unauthenticated routes. A 200 or 503 from unauthenticated readiness is a failure, not a successful auth check.

Open **https://shopaware.innawareucp.com**, sign in, and:

1. Confirm Health shows models ready and recording codec `h264`.
2. Add one camera under Cameras. Enter the RTSP URL without credentials; use the separate username/password fields. Start with a low-resolution substream.
3. Test connection, enable the camera, and verify live WebSocket preview.
4. Select one or more modes on the camera. Existing cameras start with Shoplifting only. Draw merchandise, checkout, exit, restricted, parking and ignore zones as appropriate.
5. Stage normal browsing and consented example actions. Inspect candidate incidents, original pre/post-event video, seeking/playback and review status.
6. Configure SMTP if wanted, trigger an authorized test, and confirm actual delivery.
7. Disconnect/reconnect the camera, restart the backend, and confirm settings, camera credentials and evidence persist.

Add a second stream only after checking CPU, RAM, frame age and event latency. Real two-camera tracker qualification remains tracked in issue #3. Risk scores are heuristic and require human review.

See [Camera analytics modes](CAMERA_MODES.md) for mode-specific setup, privacy boundaries, current make/model limitation, and the requirement to validate each camera angle. If upgrading an existing Server2 beta.3 installation, follow the shorter [beta.4 upgrade procedure](SERVER2_BETA4_UPGRADE.md).

## 7. Train using a selected camera

Choose **Cameras → Train with this camera**, or open **Training** and select the camera. Capture separate training, validation and test sessions, draw object boxes, explicitly review each example, and download the reviewed ZIP.

Follow [Server2 training instructions](SERVER2_TRAINING.md) for transferring the ZIP, validating it, stopping live analysis, running CPU training, comparing the held-out evaluation and explicitly activating a checkpoint. This trains an object detector; it does not automatically learn theft intent. A custom detector is not activated automatically and currently applies globally when selected.

## 8. Back up and verify

Stop independent training jobs first. The backup pauses the backend while taking a consistent database/key/configuration snapshot and resumes it afterward. Large evidence archives extend this analysis downtime.

```bash
sudo INCLUDE_MEDIA=1 bash /opt/shopaware/deploy/server2/backup.sh
sudo ls -lt /var/backups/shopaware
```

Backups contain database, matching encryption key, `.env`, model files, exported datasets, training outputs and (with `INCLUDE_MEDIA=1`) evidence. Each unique backup directory contains `SHA256SUMS`; the script verifies it and exits nonzero on failure. Copy successful backups to protected off-server storage. An incomplete directory from a failed run is not a valid restore point.

## 9. Update and rollback

Before updating, take a complete backup as above and record `git rev-parse HEAD`. Then:

```bash
cd /opt/shopaware
git fetch origin --tags
git switch --detach <new-release-tag>
COMPOSE_PARALLEL_LIMIT=1 docker compose --env-file .env -f deploy/server2/docker-compose.yml build --pull
docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d --wait --wait-timeout 300
bash deploy/server2/verify.sh shopaware.innawareucp.com
```

To revert code, switch to the previously recorded release/commit, rebuild, and recreate the services. Keep persistent directories intact. Do not downgrade across an incompatible database schema without restoring the pre-update database and matching key.

To restore data, open a root shell and set `RESTORE_DIR` to one successful backup. This procedure first preserves the current state in another directory and removes no existing evidence:

```bash
sudo -i
set -euo pipefail
RESTORE_DIR=/var/backups/shopaware/REPLACE_WITH_BACKUP_DIRECTORY
cd "$RESTORE_DIR"
sha256sum --check SHA256SUMS
# STOP if checksum verification fails.
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml stop shopaware dashboard
RESCUE_DIR=$(mktemp -d /var/backups/shopaware/pre-restore-XXXXXX)
cp -a /var/lib/shopaware/data "$RESCUE_DIR/"
cp -a /opt/shopaware/.env "$RESCUE_DIR/"
for name in shopaware.db shopaware.db-wal shopaware.db-shm .shopaware.key; do
  if [ -e "/var/lib/shopaware/data/$name" ]; then
    mv "/var/lib/shopaware/data/$name" "$RESCUE_DIR/$name"
  fi
done
install -m 0600 "$RESTORE_DIR/shopaware.db" /var/lib/shopaware/data/shopaware.db
install -m 0600 "$RESTORE_DIR/.shopaware.key" /var/lib/shopaware/data/.shopaware.key
install -m 0600 "$RESTORE_DIR/.env" /opt/shopaware/.env
for archive in models training runs evidence; do
  if [ -f "$RESTORE_DIR/$archive.tar.gz" ]; then
    if [ "$archive" = evidence ]; then
      directories="alerts incidents"
    else
      directories="$archive"
    fi
    for directory in $directories; do
      if [ -d "/var/lib/shopaware/$directory" ]; then
        mv "/var/lib/shopaware/$directory" "$RESCUE_DIR/$directory"
      fi
    done
    tar -xzf "$RESTORE_DIR/$archive.tar.gz" -C /var/lib/shopaware
  fi
done
chown installer:installer /opt/shopaware/.env
exit
```

Extract only archives from your own verified backups. The commands move directories being restored into `RESCUE_DIR`, preserving their newer contents. Restoring without `evidence.tar.gz` can leave incident rows whose files are unavailable. Restoring `.env` restores configuration too; verify domain and port settings still match Apache. Then select the matching code version, rebuild if needed, and run `up -d --wait` and the verification checks again.

## Troubleshooting

| Symptom | Check/action |
| --- | --- |
| SSH timeout | VPN/source-IP access and port 60022; no password is sent until connection succeeds. |
| Address already in use | `sudo ss -ltnp`; leave the existing port-18080 service alone. |
| Certificate name mismatch | Complete the ShopAware-specific vhost/certificate steps; do not bypass verification. |
| 502 | Compose status and backend/dashboard logs; confirm the Apache ports match. |
| Login fails/403 | Correct HTTPS origin in `.env`, secure cookie enabled, and actual browser hostname. Saved Settings can override environment defaults after restart. |
| Models stay loading | Outbound checkpoint download, free disk, model paths and backend logs; initialization retries every 30 seconds. |
| Preview absent | Browser WebSocket `/ws` upgrade, Apache proxy modules, session cookie, enabled camera and fresh RTSP frames. |
| High CPU or delayed alerts | Use fewer cameras, lower-resolution substreams and lower inference FPS. CPU training should run while analysis is stopped. |
| Clip unavailable | Incident media status, disk/quota, writer errors and H.264 encoder; interrupted clips are not presented as complete. |
| Restart loses cameras | Confirm this Server2 Compose profile and its persistent DB/key mounts, not an older ad-hoc container. |

Useful commands:

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml logs -f --tail=100 shopaware dashboard
docker stats --no-stream shopaware-backend shopaware-dashboard
docker compose --env-file .env -f deploy/server2/docker-compose.yml restart shopaware
sudo apache2ctl configtest
sudo tail -n 50 /var/log/apache2/shopaware-ssl-error.log
```

If migrating an older root-level Compose deployment, inspect and copy its actual database and matching key out of the old container **before** removing/recreating it. Older `.env` relative paths could override persistent Docker defaults. The inspected Server2 installation had no existing ShopAware database to migrate.
