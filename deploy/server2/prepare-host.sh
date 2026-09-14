#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/server2/prepare-host.sh" >&2
  exit 1
fi

APP_USER="${SUDO_USER:-root}"
APP_GROUP="$(id -gn "${APP_USER}" 2>/dev/null || echo root)"

install -d -m 0755 /opt/shopaware
install -d -m 0755 /var/www/shopaware
install -d -m 0750 /var/lib/shopaware
install -d -m 0750 \
  /var/lib/shopaware/data \
  /var/lib/shopaware/alerts \
  /var/lib/shopaware/incidents \
  /var/lib/shopaware/models \
  /var/lib/shopaware/training \
  /var/lib/shopaware/runs
install -d -m 0750 /var/backups/shopaware

chown "${APP_USER}:${APP_GROUP}" /opt/shopaware

if command -v a2enmod >/dev/null 2>&1; then
  a2enmod proxy proxy_http proxy_wstunnel headers ssl rewrite >/dev/null
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Engine + Compose plugin before continuing." >&2
  exit 2
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is not available." >&2
  exit 3
fi

cat <<'EOF'
ShopAware host directories are prepared.

Next:
  1. Clone/update the repository into /opt/shopaware as your normal user.
  2. Copy deploy/server2/.env.production.example to /opt/shopaware/.env and edit it.
  3. Install the Apache vhost after the TLS certificate exists.
  4. Build/start the server2 Compose profile.
  5. Create the first admin account.

Persistent datasets/runs live in /var/lib/shopaware/training and /var/lib/shopaware/runs.
See docs/SERVER2_DEPLOYMENT.md for exact commands.
EOF
