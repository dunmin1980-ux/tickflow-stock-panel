#!/usr/bin/env bash
set -euo pipefail

ssh_host="${TICKFLOW_SSH_HOST:-codex-vm}"
pwa_url="${TICKFLOW_PWA_URL:-https://vm-0-9-ubuntu.tail21c236.ts.net:8443}"
tailnet_host="${TICKFLOW_TAILNET_HOST:-vm-0-9-ubuntu.tail21c236.ts.net}"

status="$(ssh "$ssh_host" 'sudo tailscale serve status')"
serve_json="$(ssh "$ssh_host" 'sudo tailscale serve status --json')"
grep -Fq "https://${tailnet_host} (tailnet only)" <<<"$status"
grep -Fq "https://${tailnet_host}:8443 (tailnet only)" <<<"$status"
if grep -Eqi 'funnel|public' <<<"$status"; then
  echo "unexpected public Tailscale exposure" >&2
  exit 1
fi
python3 -c '
import json, sys
config = json.load(sys.stdin)
host = sys.argv[1]
assert config["TCP"]["443"] == {"HTTPS": True}
assert config["TCP"]["8443"] == {"HTTPS": True}
assert config["Web"][f"{host}:443"]["Handlers"]["/"]["Proxy"] == "http://127.0.0.1:8787"
assert config["Web"][f"{host}:8443"]["Handlers"]["/"]["Proxy"] == "http://127.0.0.1:3019"
' "$tailnet_host" <<<"$serve_json"

ssh "$ssh_host" 'ss -lnt | grep -Eq "127.0.0.1:3019[[:space:]]"'
ssh "$ssh_host" '! ss -lnt | grep -Eq "(0.0.0.0|\[::\]):3019[[:space:]]"'
container_env="$(ssh "$ssh_host" 'docker inspect TickFlow_Stock_Panel --format "{{range .Config.Env}}{{println .}}{{end}}"')"
grep -Fxq 'AUTH_COOKIE_SECURE=true' <<<"$container_env"
grep -Fxq 'GOLD_WORKSPACE_ENABLED=false' <<<"$container_env"

curl -fsS --connect-timeout 10 --max-time 30 "$pwa_url/health" | grep -q '"status":"ok"'

echo PWA_PRIVATE_ACCESS_OK
