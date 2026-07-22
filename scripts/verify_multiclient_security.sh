#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
canary="${TICKFLOW_SECRET_CANARY:-}"
ssh_host="${TICKFLOW_SSH_HOST:-codex-vm}"
tailnet_host="${TICKFLOW_TAILNET_HOST:-vm-0-9-ubuntu.tail21c236.ts.net}"
pwa_url="${TICKFLOW_PWA_URL:-https://${tailnet_host}:8443}"
remote_container="${TICKFLOW_REMOTE_CONTAINER:-TickFlow_Stock_Panel}"

frontend_dist="${TICKFLOW_FRONTEND_DIST:-$root/frontend/dist}"
desktop_app="${TICKFLOW_DESKTOP_APP:-$root/backend/dist/TickFlowStockPanel.app}"
desktop_main="$desktop_app/Contents/MacOS/TickFlowStockPanel"
installed_app="${TICKFLOW_INSTALLED_APP:-/Applications/TickFlowStockPanel.app}"
dmg="${TICKFLOW_DMG_PATH:-$root/dist/TickFlowStockPanel-intel-x86_64.dmg}"
app_support="${TICKFLOW_APP_SUPPORT_DIR:-$HOME/Library/Application Support/TickFlowStockPanel}"
workspace_cache="${TICKFLOW_WORKSPACE_CACHE_DIR:-$app_support/cache}"
desktop_log="${TICKFLOW_DESKTOP_LOG_PATH:-$app_support/desktop.log}"
launcher_logs="${TICKFLOW_LAUNCHER_LOG_DIR:-$HOME/Library/Logs/TickFlowStockPanel}"
backend_python="${TICKFLOW_BACKEND_PYTHON:-$root/backend/.venv/bin/python}"
canary_receipt="${TICKFLOW_CANARY_BUILD_RECEIPT:-$root/reports/macos_intel_acceptance/security_canary_build.json}"
artifact_digest="$root/scripts/artifact_tree_digest.py"

fail() {
  printf 'MULTICLIENT_SECURITY_FAILED: %s\n' "$*" >&2
  exit 1
}

[[ -n "$canary" ]] || fail "TICKFLOW_SECRET_CANARY is required"
[[ "${#canary}" -ge 24 ]] || fail "secret canary must be at least 24 characters"
case "$canary" in
  *$'\n'*|*$'\r'*) fail "secret canary must be one line" ;;
esac
[[ "$ssh_host" =~ ^([A-Za-z0-9._]+@)?[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || \
  fail "invalid SSH host"
[[ "$tailnet_host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]] || \
  fail "invalid tailnet host"
[[ "$remote_container" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || \
  fail "invalid container name"
[[ "$pwa_url" == https://* ]] || fail "PWA URL must use HTTPS"

for tool in awk cat curl grep hdiutil mktemp python3 sed shasum ssh tr; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing required tool: $tool"
done

scan_target() {
  local target="$1"
  local rc
  [[ -e "$target" ]] || fail "scan target is missing: $target"
  set +e
  if [[ -d "$target" ]]; then
    LC_ALL=C grep -R -a -F -q -- "$canary" "$target"
    rc=$?
  else
    LC_ALL=C grep -a -F -q -- "$canary" "$target"
    rc=$?
  fi
  set -e
  case "$rc" in
    0) fail "secret canary found in scan target: $target" ;;
    1) printf 'scan ok: %s\n' "$target" ;;
    *) fail "grep failed while scanning: $target" ;;
  esac
}

[[ -d "$frontend_dist" ]] || fail "frontend build is missing: $frontend_dist"
[[ -d "$desktop_app" ]] || fail "desktop App is missing: $desktop_app"
[[ -x "$desktop_main" ]] || fail "desktop executable is missing: $desktop_main"
[[ -d "$installed_app" ]] || fail "installed desktop App is missing: $installed_app"
[[ -f "$dmg" ]] || fail "desktop DMG is missing: $dmg"
[[ -d "$workspace_cache" ]] || fail "workspace cache is missing: $workspace_cache"
[[ -f "$desktop_log" ]] || fail "desktop log is missing: $desktop_log"
[[ -d "$launcher_logs" ]] || fail "launcher log directory is missing: $launcher_logs"
[[ -x "$backend_python" ]] || fail "backend Python is missing: $backend_python"
sidecar_verifier="$root/scripts/verify_workspace_sync_sidecar.sh"
[[ -x "$sidecar_verifier" ]] || fail "workspace sidecar verifier is missing or not executable"
[[ -f "$artifact_digest" ]] || fail "artifact tree digest helper is missing"
[[ -f "$canary_receipt" ]] || fail "dedicated canary build receipt is missing"
[[ "$(stat -f '%Lp' "$canary_receipt")" == 600 ]] || fail "canary build receipt permissions must be 600"
actual_canary_digest="$(printf '%s' "$canary" | shasum -a 256 | awk '{print $1}')"
pwa_tree_digest="$(python3 "$artifact_digest" "$frontend_dist")"
app_tree_digest="$(python3 "$artifact_digest" "$desktop_app")"
installed_app_tree_digest="$(python3 "$artifact_digest" "$installed_app")"
dmg_digest="$(shasum -a 256 "$dmg" | awk '{print $1}')"
dmg_bytes="$(stat -f '%z' "$dmg")"
build_script_digest="$(shasum -a 256 "$root/scripts/build_macos_intel.sh" | awk '{print $1}')"
python3 - \
  "$canary_receipt" "$actual_canary_digest" "$pwa_tree_digest" \
  "$app_tree_digest" "$installed_app_tree_digest" "$dmg_digest" \
  "$dmg_bytes" "$build_script_digest" <<'PY' || fail "canary build receipt does not match current artifacts"
import json
import re
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_keys = {
    "schema_version",
    "canary_sha256",
    "pwa_tree_sha256",
    "app_tree_sha256",
    "dmg_sha256",
    "dmg_bytes",
    "build_script_sha256",
    "canary_sources",
}
expected_sources = {
    "tickflow_api_key",
    "tushare_token",
    "ai_api_key",
    "auth_password",
    "feishu_webhook_url",
    "feishu_webhook_secret",
    "wecom_webhook_url",
    "user_data/secrets.json",
    "user_data/preferences.json",
}
if not isinstance(payload, dict) or set(payload) != expected_keys:
    raise SystemExit(1)
if payload["schema_version"] != 1 or set(payload["canary_sources"]) != expected_sources:
    raise SystemExit(1)
digests = {
    "canary_sha256": sys.argv[2],
    "pwa_tree_sha256": sys.argv[3],
    "app_tree_sha256": sys.argv[4],
    "dmg_sha256": sys.argv[6],
    "build_script_sha256": sys.argv[8],
}
if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in digests.values()):
    raise SystemExit(1)
if any(payload[key] != value for key, value in digests.items()):
    raise SystemExit(1)
if sys.argv[5] != sys.argv[4] or payload["dmg_bytes"] != int(sys.argv[7]):
    raise SystemExit(1)
PY

canary_home="$(mktemp -d "${TMPDIR:-/tmp}/tickflow-canary.XXXXXX")"
canary_data="$canary_home/Library/Application Support/TickFlowStockPanel"
smoke_output="$canary_home/desktop-smoke.log"
dmg_mount="$canary_home/dmg"
dmg_attached=false
cleanup() {
  if [[ "$dmg_attached" == true ]]; then
    hdiutil detach "$dmg_mount" >/dev/null 2>&1 || true
  fi
  rm -rf "$canary_home"
}
trap cleanup EXIT

mkdir -p "$canary_data/user_data"
TICKFLOW_CANARY_VALUE="$canary" python3 - "$canary_data" <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
canary = os.environ["TICKFLOW_CANARY_VALUE"]
secrets = root / "user_data" / "secrets.json"
preferences = root / "user_data" / "preferences.json"
secrets.write_text(json.dumps({
    "tickflow_api_key": canary,
    "tushare_token": canary,
    "ai_api_key": canary,
}, ensure_ascii=False), encoding="utf-8")
preferences.write_text(json.dumps({
    "feishu_webhook_url": f"https://open.feishu.cn/open-apis/bot/v2/hook/{canary}",
    "feishu_webhook_secret": canary,
    "wecom_webhook_url": f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={canary}",
}, ensure_ascii=False), encoding="utf-8")
os.chmod(secrets, 0o600)
os.chmod(preferences, 0o600)
PY

TICKFLOW_CANARY_VALUE="$canary" python3 - "$canary_data" <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
canary = os.environ["TICKFLOW_CANARY_VALUE"]
secrets = json.loads((root / "user_data" / "secrets.json").read_text(encoding="utf-8"))
preferences = json.loads((root / "user_data" / "preferences.json").read_text(encoding="utf-8"))
assert secrets["tickflow_api_key"] == canary
assert secrets["tushare_token"] == canary
assert secrets["ai_api_key"] == canary
assert canary in preferences["feishu_webhook_url"]
assert preferences["feishu_webhook_secret"] == canary
assert canary in preferences["wecom_webhook_url"]
PY

DATA_DIR="$canary_data" \
  PYTHONPATH="$root/backend" \
  TICKFLOW_CANARY_VALUE="$canary" \
  "$backend_python" - "$canary_data/cache/workspace" <<'PY'
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.desktop_client.cache import WorkspaceCache
from app.workspace.models import ResourceName, ResourceSnapshot
from app.workspace.revision import revision_for

cache = WorkspaceCache(Path(sys.argv[1]))
safe_data = {"symbols": []}
cache.put(ResourceSnapshot(
    resource=ResourceName.WATCHLIST,
    revision=revision_for(safe_data),
    updated_at=datetime.now(UTC),
    data=safe_data,
))

unsafe_data = {
    "preferences": {
        "has_feishu_webhook": False,
        "has_feishu_credential_data": False,
        "has_wecom_webhook": False,
        "has_wecom_bot": False,
        "has_wecom_bot_credential_data": False,
        "api_key": os.environ["TICKFLOW_CANARY_VALUE"],
    }
}
try:
    cache.put(ResourceSnapshot(
        resource=ResourceName.PREFERENCES,
        revision=revision_for(unsafe_data),
        updated_at=datetime.now(UTC),
        data=unsafe_data,
    ))
except ValueError:
    pass
else:
    raise SystemExit("unsafe canary preference was accepted by WorkspaceCache")
if cache.path(ResourceName.PREFERENCES).exists():
    raise SystemExit("unsafe canary preference reached the workspace cache")
PY

env -i \
  HOME="$canary_home" \
  PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  TMPDIR="${TMPDIR:-/tmp}" \
  LANG="en_US.UTF-8" \
  DATA_DIR="$canary_data" \
  TICKFLOW_API_KEY="$canary" \
  TUSHARE_TOKEN="$canary" \
  AI_API_KEY="$canary" \
  TICKFLOW_DESKTOP_CLIENT="1" \
  TICKFLOW_DESKTOP_SMOKE="1" \
  "$desktop_main" >"$smoke_output" 2>&1 || fail "dedicated canary desktop smoke failed"

[[ -d "$canary_data/cache" ]] || fail "canary smoke did not create a cache directory"
[[ -f "$canary_data/desktop.log" ]] || fail "canary smoke did not create a desktop log"

mkdir -p "$dmg_mount"
hdiutil attach -readonly -nobrowse -mountpoint "$dmg_mount" "$dmg" >/dev/null || \
  fail "unable to mount desktop DMG for content scanning"
dmg_attached=true

for target in \
  "$frontend_dist" \
  "$desktop_app" \
  "$installed_app" \
  "$dmg" \
  "$dmg_mount" \
  "$workspace_cache" \
  "$desktop_log" \
  "$launcher_logs" \
  "$canary_data/cache" \
  "$canary_data/desktop.log" \
  "$smoke_output"; do
  scan_target "$target"
done

ssh "$ssh_host" '! ss -lntH | grep -Eq "(0.0.0.0|\[::\]):(3018|3019)[[:space:]]"' || \
  fail "3018 or 3019 is publicly bound"
ssh "$ssh_host" 'ss -lntH | grep -Eq "127.0.0.1:3019[[:space:]]"' || \
  fail "3019 is not bound to loopback"

serve_status="$(ssh "$ssh_host" 'sudo tailscale serve status')" || \
  fail "unable to read Tailscale serve status"
TAILSCALE_SERVE_STATUS="$serve_status" TICKFLOW_TAILNET_HOST="$tailnet_host" \
  python3 - <<'PY' || fail "Tailscale Serve mappings are invalid"
import os
import re

status = os.environ["TAILSCALE_SERVE_STATUS"]
host = os.environ["TICKFLOW_TAILNET_HOST"]
if re.search(r"\b(funnel|public)\b", status, re.IGNORECASE):
    raise SystemExit(1)
blocks = {
    lines[0]: "\n".join(lines[1:])
    for block in re.split(r"\n\s*\n", status.strip())
    if (lines := [line.strip() for line in block.splitlines() if line.strip()])
}
expected = {
    f"https://{host} (tailnet only)": "proxy http://127.0.0.1:8787",
    f"https://{host}:8443 (tailnet only)": "proxy http://127.0.0.1:3019",
}
for entry, target in expected.items():
    body = blocks.get(entry, "")
    if target not in body:
        raise SystemExit(1)
other_targets = {
    "proxy http://127.0.0.1:8787",
    "proxy http://127.0.0.1:3019",
}
for entry, target in expected.items():
    if any(other in blocks[entry] for other in other_targets - {target}):
        raise SystemExit(1)
PY

ssh "$ssh_host" "docker inspect '$remote_container' --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -Fxq 'AUTH_COOKIE_SECURE=true'" || \
  fail "AUTH_COOKIE_SECURE=true is not active"
ssh "$ssh_host" "docker inspect '$remote_container' --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -Fxq 'GOLD_WORKSPACE_ENABLED=false'" || \
  fail "Gold workspace must remain disabled"
ssh "$ssh_host" "docker inspect '$remote_container' --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -Fxq 'WORKSPACE_SYNC_ENABLED=true'" || \
  fail "workspace sync gate is not enabled"
ssh "$ssh_host" "docker exec '$remote_container' sh -c 'test -d /app/data/user_data && test -r /app/data/user_data && test -w /app/data/user_data'" || \
  fail "production workspace volume is not readable and writable"
production_contract="$canary_home/production-workspace-contract.txt"
ssh "$ssh_host" "image=\"\$(docker inspect '$remote_container' --format '{{.Image}}')\"; docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --volumes-from '$remote_container':ro -e DATA_DIR=/app/data -e WORKSPACE_SYNC_ENABLED=true -e GOLD_WORKSPACE_ENABLED=false \"\$image\" /app/.venv/bin/python -" \
  >"$production_contract" <<'PY' || fail "production workspace volume contract is invalid"
from app.api.workspace import _DATA_MODELS
from app.workspace.models import ResourceName
from app.workspace.registry import snapshot_resource
from app.workspace.revision import revision_for

for resource in ResourceName:
    snapshot = snapshot_resource(resource)
    _DATA_MODELS[resource].model_validate(snapshot.data)
    if snapshot.revision != revision_for(snapshot.data):
        raise SystemExit(1)
print("PRODUCTION_WORKSPACE_CONTRACT_OK")
PY
grep -Fxq 'PRODUCTION_WORKSPACE_CONTRACT_OK' "$production_contract" || \
  fail "production workspace contract did not complete"

health_response="$canary_home/pwa-health.json"
curl -fsS --connect-timeout 10 --max-time 30 \
  -o "$health_response" "$pwa_url/health" || fail "private PWA health check failed"
python3 - "$health_response" <<'PY' || fail "private PWA health response is invalid"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit(1)
PY

pwa_index="$canary_home/pwa-index.html"
pwa_manifest="$canary_home/pwa-manifest.json"
curl -fsS --connect-timeout 10 --max-time 30 \
  -o "$pwa_index" "$pwa_url/" || fail "private PWA index route failed"
grep -Fq '<div id="root"></div>' "$pwa_index" || fail "private PWA index is invalid"
curl -fsS --connect-timeout 10 --max-time 30 \
  -o "$pwa_manifest" "$pwa_url/manifest.webmanifest" || fail "private PWA manifest route failed"
python3 - "$pwa_manifest" <<'PY' || fail "private PWA manifest is invalid"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("display") != "standalone" or not payload.get("icons"):
    raise SystemExit(1)
PY

pwa_auth="$canary_home/pwa-auth.json"
production_auth="$canary_home/production-auth.json"
curl -fsS --connect-timeout 10 --max-time 30 \
  -o "$pwa_auth" "$pwa_url/api/auth/status" || fail "private PWA auth route failed"
ssh "$ssh_host" "curl -fsS http://127.0.0.1:3019/api/auth/status" \
  >"$production_auth" || fail "production loopback auth route failed"
auth_configured="$(python3 - "$pwa_auth" "$production_auth" <<'PY'
import json
import sys
from pathlib import Path

remote = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
loopback = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if remote.get("configured") is not loopback.get("configured"):
    raise SystemExit(1)
if not isinstance(loopback.get("configured"), bool):
    raise SystemExit(1)
print("true" if loopback["configured"] else "false")
PY
)" || fail "production auth states do not match"

production_reply="$canary_home/production-workspace.reply"
production_workspace="$canary_home/production-workspace.json"
ssh "$ssh_host" 'tmp="$(mktemp)"; trap '\''rm -f "$tmp"'\'' EXIT; code="$(curl -sS -o "$tmp" -w '\''%{http_code}'\'' http://127.0.0.1:3019/api/workspace/bootstrap)"; printf '\''%s\n'\'' "$code"; cat "$tmp"' \
  >"$production_reply" || \
  fail "production workspace route failed"
production_code="$(sed -n '1p' "$production_reply")"
sed '1d' "$production_reply" >"$production_workspace"
if [[ "$auth_configured" == true ]]; then
  [[ "$production_code" == 401 ]] || fail "authenticated production workspace is publicly readable"
else
  [[ "$production_code" == 200 ]] || fail "uninitialized production workspace returned HTTP $production_code"
  python3 - "$production_workspace" <<'PY' || fail "production workspace bootstrap is invalid"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = {"watchlist", "preferences", "stock_reports", "market_recaps", "backtest_summaries"}
if payload.get("schema_version") != 1 or set(payload.get("resources", {})) != required:
    raise SystemExit(1)
PY
fi

if ! {
  printf '%s\n' "$canary"
  cat "$sidecar_verifier"
} | ssh "$ssh_host" \
  "IFS= read -r TICKFLOW_SECRET_CANARY; export TICKFLOW_SECRET_CANARY; bash -s -- '$remote_container'"; then
  fail "isolated workspace sidecar contract failed"
fi

printf 'MULTICLIENT_SECURITY_OK\n'
