#!/usr/bin/env bash
set -euo pipefail

REPO="${SHOPAWARE_REPO:-/opt/shopaware}"
STATE="${SHOPAWARE_STATE:-/var/lib/shopaware}"
BACKUP_ROOT="${SHOPAWARE_BACKUP_ROOT:-/var/backups/shopaware}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_ROOT}/${STAMP}"
TMP_DB="${STATE}/data/.shopaware-backup-${STAMP}.db"
COMPOSE=(docker compose --env-file "${REPO}/.env" -f "${REPO}/deploy/server2/docker-compose.yml")

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo so the encryption key and evidence directories can be backed up safely." >&2
  exit 1
fi

mkdir -p "${DEST}"
chmod 0700 "${DEST}"

cleanup() {
  rm -f "${TMP_DB}" || true
}
trap cleanup EXIT

# Use SQLite's online backup API inside the running backend container.
"${COMPOSE[@]}" exec -T shopaware python - <<PY
import sqlite3
src = sqlite3.connect('/app/data/shopaware.db')
dst = sqlite3.connect('/app/data/$(basename "${TMP_DB}")')
with dst:
    src.backup(dst)
dst.close()
src.close()
PY

cp --preserve=mode,timestamps "${TMP_DB}" "${DEST}/shopaware.db"
if [[ -f "${STATE}/data/.shopaware.key" ]]; then
  cp --preserve=mode,timestamps "${STATE}/data/.shopaware.key" "${DEST}/.shopaware.key"
fi

if [[ -d "${STATE}/models" ]]; then
  tar -C "${STATE}" -czf "${DEST}/models.tar.gz" models
fi

if [[ "${INCLUDE_MEDIA:-0}" == "1" ]]; then
  tar -C "${STATE}" -czf "${DEST}/evidence.tar.gz" alerts incidents
fi

(
  cd "${DEST}"
  sha256sum ./* ./.shopaware.key 2>/dev/null > SHA256SUMS || true
)

cat <<EOF
ShopAware backup created: ${DEST}

Contains the online SQLite backup, the Fernet key when present, and models.
Set INCLUDE_MEDIA=1 to include incident snapshots/clips (can be large).
Keep shopaware.db and .shopaware.key together; encrypted camera/SMTP credentials
cannot be recovered from the database without the matching key.
EOF
