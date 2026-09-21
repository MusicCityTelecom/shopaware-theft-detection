# Upgrade the installed Server2 app to v0.1.0-beta.6

> SUPERSEDED — do not install this tag. It predates the final corrections. Use the beta.7 candidate only after its exact-commit release gate passes; see [beta.7 upgrade guide](SERVER2_BETA7_UPGRADE.md). The original notes below are historical.

This procedure upgrades an existing `shopaware.innawareucp.com` beta.4 installation to the corrected beta.6 release. **Do not deploy the earlier `v0.1.0-beta.5` prerelease tag**; it was published from an earlier branch state and is superseded by beta.6.

Beta.6 preserves `.env`, users, customer groups, camera grants, encrypted camera credentials, incidents, observations, media, training data, enabled camera modes and TLS configuration. Startup performs an additive schema-6 migration that adds persisted per-camera mode settings. Existing cameras snapshot their effective beta.4 Shoplifting tuning (including a previously customized global risk or loitering threshold) and use beta.4 behavior-equivalent values for the newly exposed mode controls.

## 1. Connect, inspect and back up

```bash
ssh -p 60022 installer@50.206.74.206
cd /opt/shopaware
git status --short --branch
git rev-parse HEAD
sudo INCLUDE_MEDIA=1 bash deploy/server2/backup.sh
sudo ls -lt /var/backups/shopaware | head
```

Record the old commit and newest successful backup directory. Stop if Git shows unreviewed local changes or if the backup/checksum step fails. Do not replace `.env` and do not delete anything under `/var/lib/shopaware`.

## 2. Select the validated release and rebuild

Only continue after GitHub shows `v0.1.0-beta.6` as a prerelease and the release workflow for its exact target SHA is green.

```bash
cd /opt/shopaware
git fetch origin --tags
git tag --list v0.1.0-beta.6
git switch --detach v0.1.0-beta.6
test "$(cat VERSION)" = "0.1.0-beta.6"
COMPOSE_PARALLEL_LIMIT=1 docker compose --env-file .env -f deploy/server2/docker-compose.yml build --pull
docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d --wait --wait-timeout 300
```

The persistent database, encryption key, evidence, models, training data and run directories are reused. The first backend startup migrates SQLite from schema 5 to schema 6 by adding `cameras.mode_settings_json`. Beta.6 then snapshots each migrated camera's effective beta.4 Shoplifting risk/loitering values into its complete per-camera settings object. Vehicle break-in, LPR and Face Capture settings begin with beta.4 behavior-equivalent values.

## 3. Verify before changing any tuning

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml ps
docker compose --env-file .env -f deploy/server2/docker-compose.yml logs --tail=200 shopaware
curl --fail http://127.0.0.1:18082/health/live
curl --fail http://127.0.0.1:18081/api/health/live
bash deploy/server2/verify.sh shopaware.innawareucp.com
docker compose --env-file .env -f deploy/server2/docker-compose.yml exec -T shopaware python -m tools.check_runtime
docker compose --env-file .env -f deploy/server2/docker-compose.yml exec -T shopaware python -c "import sqlite3; print(sqlite3.connect('/app/data/shopaware.db').execute('PRAGMA user_version').fetchone()[0])"
```

The runtime check must report version `0.1.0-beta.6`, H.264 and passed CPU inference. The final command must print `6`.

Then sign in through `https://shopaware.innawareucp.com` and confirm:

1. Existing users and customer groups are still present.
2. Existing camera assignments and enabled modes are unchanged.
3. Existing incidents and Analytics observations are accessible to the same authorized accounts.
4. **Mode Settings** appears for administrators only.
5. Selecting an existing camera shows its preserved effective beta.4 Shoplifting tuning plus beta.4-equivalent defaults for the other mode controls.
6. A regular user cannot access the Mode Settings page or API.

## 4. Tune one camera at a time

Open **Mode Settings**, choose a camera and change only the parameters needed for that camera. Saved values apply without a service restart.

Recommended initial approach on CPU-only Server2:

- Keep Shoplifting settings unchanged until representative store footage is reviewed.
- For Vehicle break-in, create a Parking zone and Ignore zones first; tune dwell/interactions only after staged owner/valet/maintenance activity is reviewed.
- For LPR, raise minimum OCR confidence if low-quality guesses are being stored; validate every plate against original video.
- For Face Capture, raise minimum quality if crops are too blurry and keep the per-track image cap small.

Monitor after each change:

```bash
docker stats --no-stream
docker compose --env-file /opt/shopaware/.env -f /opt/shopaware/deploy/server2/docker-compose.yml logs --tail=150 shopaware
df -h /var/lib/shopaware
```

## 5. Restart-persistence check

After saving one harmless test-camera setting, record the value in the UI and recreate the containers:

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d --force-recreate --wait --wait-timeout 300
```

Sign back in and confirm the camera retains the saved value. Also confirm the stream reconnects and new incidents/observations continue to use the configured settings.

## Roll back

Beta.6 schema 6 is newer than beta.4 schema 5. A code-only rollback is not valid.

Before rollback, take a rescue backup of the current beta.6 state if any new incidents/settings must be retained. Then stop the services, restore the complete pre-upgrade beta.4 backup (database and matching encryption key at minimum, plus media if needed), select the recorded beta.4 tag/commit, rebuild, start and verify using the restore procedure in `docs/SERVER2_DEPLOYMENT.md`.

Do not attempt to downgrade the schema in place by manually dropping `mode_settings_json`.
