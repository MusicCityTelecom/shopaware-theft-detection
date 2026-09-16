# Upgrade the installed Server2 app to v0.1.0-beta.4

This procedure upgrades the existing `shopaware.innawareucp.com` beta.3 installation. It preserves the `.env`, users, customer groups, camera grants, encrypted camera credentials, incidents, media, training data and TLS configuration. The application performs an additive schema-5 migration at startup. Existing cameras remain in Shoplifting mode.

## 1. Connect, inspect and back up

```bash
ssh -p 60022 installer@50.206.74.206
cd /opt/shopaware
git status --short --branch
git rev-parse HEAD
sudo INCLUDE_MEDIA=1 bash deploy/server2/backup.sh
sudo ls -lt /var/backups/shopaware | head
```

Record the old commit and newest successful backup directory. Stop if Git shows unreviewed local changes or the backup/checksum step fails. Do not replace `.env` and do not delete anything under `/var/lib/shopaware`.

## 2. Select the release source and rebuild

```bash
cd /opt/shopaware
git fetch origin --tags
git tag --list v0.1.0-beta.4
git switch --detach v0.1.0-beta.4
test "$(cat VERSION)" = "0.1.0-beta.4"
COMPOSE_PARALLEL_LIMIT=1 docker compose --env-file .env -f deploy/server2/docker-compose.yml build --pull
docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d --wait --wait-timeout 300
```

The backend image is rebuilt because beta.4 adds Tesseract OCR. The first backend startup migrates the database from schema 4 to 5 and adds per-camera mode configuration plus observations. The persistent database and evidence mounts are reused.

## 3. Verify before enabling new modes

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml ps
docker compose --env-file .env -f deploy/server2/docker-compose.yml logs --tail=150 shopaware
curl --fail http://127.0.0.1:18082/health/live
curl --fail http://127.0.0.1:18081/api/health/live
bash deploy/server2/verify.sh shopaware.innawareucp.com
docker compose --env-file .env -f deploy/server2/docker-compose.yml exec -T shopaware python -m tools.check_runtime
docker compose --env-file .env -f deploy/server2/docker-compose.yml exec -T shopaware python -c "import sqlite3; print(sqlite3.connect('/app/data/shopaware.db').execute('PRAGMA user_version').fetchone()[0])"
```

The runtime check should report version `0.1.0-beta.4`, H.264 and passed CPU inference. The last command must print `5`. Sign in and confirm the existing camera, customer group, user grants and incidents remain present. No Apache or certificate changes are required.

## 4. Enable modes gradually

1. Open **Cameras** and confirm each existing camera shows **Shoplifting**.
2. On the first test camera, enable one additional mode and wait for the save confirmation.
3. For Vehicle break-in, create a **Parking** zone and Ignore zones before testing.
4. Open **Analytics** for LPR/Face Capture; use **Incidents** for Shoplifting/Vehicle break-in.
5. Monitor Server2 while staging consented tests:

```bash
docker stats --no-stream
docker compose --env-file /opt/shopaware/.env -f /opt/shopaware/deploy/server2/docker-compose.yml logs --tail=100 shopaware
df -h /var/lib/shopaware
```

Keep the standard `yolo26n.pt` detector and `SHOPAWARE_ENABLE_SPECIALIZED_MODEL=false` for LPR and Vehicle break-in. See [Camera analytics modes](CAMERA_MODES.md) for limitations and acceptance checks.

## Roll back

Schema 5 is newer than beta.3 can open. A code-only rollback is not valid. Stop the services, restore the complete pre-upgrade beta.3 backup (database and matching encryption key at minimum), select the recorded beta.3 commit/tag, rebuild, start, and verify using the restore procedure in [Server2 deployment](SERVER2_DEPLOYMENT.md). Preserve the current schema-5 data in a rescue backup first if it may be needed.
