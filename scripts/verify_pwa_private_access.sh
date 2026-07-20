#!/usr/bin/env bash
set -euo pipefail

ssh_host="${TICKFLOW_SSH_HOST:-codex-vm}"
pwa_url="${TICKFLOW_PWA_URL:-https://vm-0-9-ubuntu.tail21c236.ts.net:8443}"

status="$(ssh "$ssh_host" 'tailscale serve status')"
grep -Fq 'proxy http://127.0.0.1:8787' <<<"$status"
grep -Fq 'proxy http://127.0.0.1:3019' <<<"$status"

ssh "$ssh_host" 'ss -lnt | grep -Eq "127.0.0.1:3019[[:space:]]"'
ssh "$ssh_host" '! ss -lnt | grep -Eq "(0.0.0.0|\[::\]):3019[[:space:]]"'

curl -fsS --connect-timeout 10 --max-time 30 "$pwa_url/health" | grep -q '"status":"ok"'

echo PWA_PRIVATE_ACCESS_OK
