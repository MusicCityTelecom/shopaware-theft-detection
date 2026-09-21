#!/usr/bin/env bash
set -euo pipefail
case " ${RENEWED_DOMAINS:-} " in
  *" shopaware.innawareucp.com "*)
    apache2ctl configtest
    systemctl reload apache2
    ;;
esac
