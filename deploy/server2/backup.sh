#!/usr/bin/env bash
set -euo pipefail

REPO="${SHOPAWARE_REPO:-/opt/shopaware}"
STATE="/var/lib/shopaware"
BACKUP_ROOT="${SHOPAWARE_BACKUP_ROOT:-/var/backups/shopaware}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
COMPOSE=(docker compose --env-file "${REPO}/.env" -f "${REPO}/deploy/server2/docker-compose.yml")

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo so the encryption key and evidence directories can be backed up safely." >&2
  exit 1
fi

umask 077
[[ "${REPO}" = /* && "${BACKUP_ROOT}" = /* ]] || { echo "Paths must be absolute." >&2; exit 1; }
for command in docker python3 tar sha256sum flock; do command -v "$command" >/dev/null; done
for required in "${REPO}/.env" "${STATE}/data/shopaware.db" "${STATE}/data/.shopaware.key"; do
  [[ -s "$required" ]] || { echo "Missing required backup input: $required" >&2; exit 1; }
done
mkdir -p "${BACKUP_ROOT}"
exec 9>"${BACKUP_ROOT}/.backup.lock"
flock -n 9 || { echo "Another backup is running." >&2; exit 1; }
DEST="$(mktemp -d "${BACKUP_ROOT}/${STAMP}-XXXXXX")"
restart=0
cleanup() {
  local result=$?
  if [[ "$restart" == 1 ]]; then
    if ! "${COMPOSE[@]}" start shopaware; then
      echo "Backend restart failed; start it manually before resuming camera analysis." >&2
      result=1
    fi
  fi
  if [[ "$result" != 0 ]]; then
    echo "Backup failed; do not use incomplete directory ${DEST}." >&2
  fi
  exit "$result"
}
trap cleanup EXIT
running="$("${COMPOSE[@]}" ps --status running --services shopaware)"
if [[ "$running" == "shopaware" ]]; then
  restart=1
  "${COMPOSE[@]}" stop shopaware
fi

# Stop inference writers so the database, encryption key and optional evidence agree.
# The SQLite backup API also handles committed transactions still in the WAL.
python3 - "${STATE}/data/shopaware.db" "${DEST}/shopaware.db" <<'PY'
import sqlite3
import sys
from pathlib import Path
with sqlite3.connect(Path(sys.argv[1]).as_uri() + '?mode=ro', uri=True) as src:
    with sqlite3.connect(sys.argv[2]) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Database backup failed integrity check')
PY
cp "${STATE}/data/.shopaware.key" "${DEST}/.shopaware.key"
cp "${REPO}/.env" "${DEST}/.env"
files=(shopaware.db .shopaware.key .env)

for directory in models training runs; do
  if [[ -d "${STATE}/${directory}" ]]; then
    tar -C "${STATE}" -czf "${DEST}/${directory}.tar.gz" "${directory}"
    files+=("${directory}.tar.gz")
  fi
done

if [[ "${INCLUDE_MEDIA:-0}" == "1" ]]; then
  tar -C "${STATE}" -czf "${DEST}/evidence.tar.gz" alerts incidents
  files+=(evidence.tar.gz)
fi

(
  cd "${DEST}"
  sha256sum -- "${files[@]}" > SHA256SUMS
  sha256sum --check SHA256SUMS
)

cat <<EOF
ShopAware backup created: ${DEST}

Contains a consistent SQLite backup, the matching Fernet key, .env, model files,
exported training datasets and training runs/checkpoints. Set INCLUDE_MEDIA=1 to
also include incident snapshots/clips (can be large).

Camera analysis is paused during this backup. Stop independent training jobs first.
Keep shopaware.db and .shopaware.key together; encrypted camera/SMTP credentials
cannot be recovered from the database without the matching key.
EOF
