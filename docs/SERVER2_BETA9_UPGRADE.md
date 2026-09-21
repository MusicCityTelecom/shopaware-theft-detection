# Upgrade Server2 to v0.1.0-beta.9

Beta.9 preserves per-camera mode settings and schema 6. Upgrades from beta.4
perform the previously qualified additive schema-5-to-6 migration. Do not use
the superseded beta.5 or beta.6 tags. The qualified beta.7 and beta.8 tags remain valid prior releases.

This procedure is for a planned maintenance window. Publication and CI are not
proof of real-camera detection accuracy or sustained Server2 capacity.

## Back up and verify the release

Connect to Server2 and record the current commit and successful backup directory:

```bash
ssh -p 60022 installer@50.206.74.206
cd /opt/shopaware
git status --short --branch
git rev-parse HEAD
sudo INCLUDE_MEDIA=1 bash deploy/server2/backup.sh
sudo ls -lt /var/backups/shopaware | head
```

Stop for local source changes or backup failures. Preserve `.env` and all data
under `/var/lib/shopaware`. Use the full commit SHA from the successful beta.9
**push** qualification run, not a PR merge run. The subshell below stops on any
failed check and does not rebuild or restart services after a mismatch.

```bash
(
  set -euo pipefail
  cd /opt/shopaware
  test -z "$(git status --porcelain)"
  QUALIFIED_SHA=REPLACE_WITH_VERIFIED_CI_SHA
  [[ "$QUALIFIED_SHA" =~ ^[0-9a-f]{40}$ ]]
  git fetch origin --tags
  test "$(git rev-parse 'v0.1.0-beta.9^{commit}')" = "$QUALIFIED_SHA"
  git switch --detach v0.1.0-beta.9
  test "$(cat VERSION)" = "0.1.0-beta.9"
  COMPOSE_PARALLEL_LIMIT=1 docker compose --env-file .env -f deploy/server2/docker-compose.yml build --pull
  docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d --wait --wait-timeout 300
)
```

## Verify the installation

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml ps
bash deploy/server2/verify.sh shopaware.innawareucp.com
docker compose --env-file .env -f deploy/server2/docker-compose.yml exec -T shopaware python -m tools.check_runtime
```

The runtime check must report version `0.1.0-beta.9`, schema `6`, H.264 and passed
CPU inference. Sign in and verify existing users, customer groups, camera ACLs,
enabled modes, incidents, observations and training remain available.

Confirm each camera retains its saved mode settings and that regular users
remain unable to edit them. For acceptance, review representative footage with
the configured long cooldowns and scoring windows: LPR must suppress the same
plate for the full saved interval, Face Capture must honor the image cap on one
continuous track, and Shoplifting must use that camera's dwell threshold.

Review representative camera footage and monitor CPU, storage and event latency
before accepting the upgrade for ongoing operation.

## Rollback

Keep a rescue backup of new incidents and settings before rollback. Follow the
restore procedure in [Server2 deployment](SERVER2_DEPLOYMENT.md), using the
recorded prior commit and matching database/key backup. Returning to beta.4
requires its schema-5 backup; never drop columns to downgrade in place.
