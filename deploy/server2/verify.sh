#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-shopaware.innawareucp.com}"
BACKEND_PORT="${SHOPAWARE_BACKEND_PORT:-18080}"
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

failed=0
check "Backend live health" curl -fsS "http://127.0.0.1:${BACKEND_PORT}/health/live" || failed=1
check "Dashboard loopback" curl -fsS "http://127.0.0.1:${DASHBOARD_PORT}/" || failed=1
check "Public HTTPS dashboard" curl -fsS "https://${DOMAIN}/" || failed=1
check "Public proxied live health" curl -fsS "https://${DOMAIN}/api/health/live" || failed=1

printf '%-34s' "Backend readiness (internal)"
ready_code="$(curl -sS -o /tmp/shopaware-ready.$$ -w '%{http_code}' "http://127.0.0.1:${BACKEND_PORT}/health/ready" || true)"
# Readiness is authenticated by design, so 401 from an unauthenticated probe proves
# that the route is protected; use the dashboard/health view after login for model state.
if [[ "${ready_code}" == "401" || "${ready_code}" == "200" || "${ready_code}" == "503" ]]; then
  echo "reachable (${ready_code})"
else
  echo "FAIL (${ready_code})"
  failed=1
fi
rm -f /tmp/shopaware-ready.$$

if [[ ${failed} -ne 0 ]]; then
  echo "One or more ShopAware deployment checks failed." >&2
  exit 1
fi

echo "Basic reverse-proxy and service checks passed for https://${DOMAIN}/"
