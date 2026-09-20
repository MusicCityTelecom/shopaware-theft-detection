#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-shopaware.innawareucp.com}"
BACKEND_PORT="${SHOPAWARE_BACKEND_PORT:-18082}"
DASHBOARD_PORT="${SHOPAWARE_DASHBOARD_PORT:-18081}"

check() {
  local label="$1"; shift
  printf '%-34s' "${label}"
  if "$@" >/dev/null 2>&1; then
    echo "OK"
  else
    echo "FAIL"
    return 1
  fi
}

protected() {
  local code
  code="$(curl --connect-timeout 5 --max-time 15 -sS -o /dev/null -w '%{http_code}' "$1")"
  [[ "$code" == 401 ]]
}

failed=0
check "Backend live health" curl --max-time 15 -fsS "http://127.0.0.1:${BACKEND_PORT}/health/live" || failed=1
check "Dashboard loopback" curl --max-time 15 -fsS "http://127.0.0.1:${DASHBOARD_PORT}/" || failed=1
check "Public HTTPS dashboard" curl --max-time 15 -fsS "https://${DOMAIN}/" || failed=1
check "Public proxied live health" curl --max-time 15 -fsS "https://${DOMAIN}/api/health/live" || failed=1

check "Readiness requires login" protected "http://127.0.0.1:${BACKEND_PORT}/health/ready" || failed=1
check "Public settings require login" protected "https://${DOMAIN}/api/settings" || failed=1
check "Public cameras require login" protected "https://${DOMAIN}/api/cameras" || failed=1

if [[ ${failed} -ne 0 ]]; then
  echo "One or more ShopAware deployment checks failed." >&2
  exit 1
fi

echo "Basic reverse-proxy and service checks passed for https://${DOMAIN}/"
