#!/usr/bin/env bash
set -euo pipefail

production_container="${1:-TickFlow_Stock_Panel}"
canary="${TICKFLOW_SECRET_CANARY:-}"

fail() {
  printf 'WORKSPACE_SIDECAR_FAILED: %s\n' "$*" >&2
  exit 1
}

[[ -n "$canary" ]] || fail "TICKFLOW_SECRET_CANARY is required"
[[ "${#canary}" -ge 24 ]] || fail "secret canary must be at least 24 characters"
case "$canary" in
  *$'\n'*|*$'\r'*) fail "secret canary must be one line" ;;
esac
[[ "$production_container" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || \
  fail "invalid production container name"
for tool in curl docker grep head mktemp python3 sed seq tr; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing required tool: $tool"
done

image_id="$(docker inspect "$production_container" --format '{{.Image}}')" || \
  fail "unable to inspect production container"
[[ -n "$image_id" ]] || fail "production image id is empty"

work="$(mktemp -d /tmp/tickflow-workspace-sidecar.XXXXXX)"
data_dir="$work/data"
responses="$work/responses"
cookie_jar="$work/session.cookies"
sidecar="TickFlow_Workspace_Acceptance_$$"
sse_pid=""
sidecar_started=false
host_uid="$(id -u)"
host_gid="$(id -g)"
[[ "$host_uid" =~ ^[0-9]+$ && "$host_gid" =~ ^[0-9]+$ ]] || fail "invalid host uid/gid"

cleanup() {
  set +e
  if [[ -n "$sse_pid" ]]; then
    kill "$sse_pid" >/dev/null 2>&1 || true
    wait "$sse_pid" >/dev/null 2>&1 || true
  fi
  if [[ "$sidecar_started" == true ]]; then
    docker rm -f "$sidecar" >/dev/null 2>&1 || true
  fi
  rm -rf "$work"
}
trap cleanup EXIT

mkdir -p "$data_dir/user_data" "$responses"
chmod 700 "$work" "$data_dir" "$data_dir/user_data" "$responses"
TICKFLOW_CANARY_VALUE="$canary" python3 - "$data_dir/user_data" <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
canary = os.environ["TICKFLOW_CANARY_VALUE"]
(root / "secrets.json").write_text(json.dumps({
    "tickflow_api_key": canary,
    "tushare_token": canary,
    "ai_api_key": canary,
}), encoding="utf-8")
(root / "preferences.json").write_text(json.dumps({
    "feishu_webhook_url": f"https://open.feishu.cn/open-apis/bot/v2/hook/{canary}",
    "feishu_webhook_secret": canary,
    "wecom_webhook_url": f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={canary}",
}), encoding="utf-8")
for path in root.iterdir():
    path.chmod(0o600)
PY

docker run -d \
  --name "$sidecar" \
  --restart no \
  --user "$host_uid:$host_gid" \
  -p 127.0.0.1::3018 \
  -v "$data_dir:/app/data" \
  -e HOME=/tmp \
  -e DATA_DIR=/app/data \
  -e AUTH_COOKIE_SECURE=false \
  -e GOLD_WORKSPACE_ENABLED=false \
  -e WORKSPACE_SYNC_ENABLED=true \
  "$image_id" >"$work/container-id"
sidecar_started=true

binding="$(docker port "$sidecar" 3018/tcp)" || fail "sidecar port is unavailable"
[[ "$binding" =~ ^127\.0\.0\.1:([0-9]+)$ ]] || fail "sidecar is not loopback-only"
port="${BASH_REMATCH[1]}"
base="http://127.0.0.1:$port"

ready=false
for _ in $(seq 1 60); do
  if curl -fsS --connect-timeout 1 --max-time 2 "$base/health" \
    >"$responses/health.json" 2>/dev/null; then
    ready=true
    break
  fi
  sleep 0.5
done
[[ "$ready" == true ]] || fail "sidecar did not become healthy"

password="TF-accept-$(python3 -c 'import secrets; print(secrets.token_hex(18))')"
printf '{"password":"%s"}' "$password" >"$work/password.json"

request_code() {
  local output="$1"
  shift
  curl -sS --connect-timeout 3 --max-time 20 \
    -o "$output" -w '%{http_code}' "$@"
}

require_json() {
  local path="$1"
  [[ -s "$path" ]] || fail "JSON response is empty: $(basename "$path")"
  python3 -m json.tool "$path" >/dev/null 2>&1 || \
    fail "JSON response is invalid: $(basename "$path")"
}

code="$(request_code "$responses/setup.json" \
  -H 'Content-Type: application/json' --data-binary "@$work/password.json" \
  "$base/api/auth/setup")"
[[ "$code" == 200 ]] || fail "sidecar auth setup returned HTTP $code"
require_json "$responses/setup.json"
code="$(request_code "$responses/unauthenticated-bootstrap.json" \
  "$base/api/workspace/bootstrap")"
[[ "$code" == 401 ]] || fail "unauthenticated workspace returned HTTP $code instead of 401"
require_json "$responses/unauthenticated-bootstrap.json"
code="$(request_code "$responses/unauthenticated-events.json" \
  "$base/api/workspace/events")"
[[ "$code" == 401 ]] || fail "unauthenticated workspace SSE returned HTTP $code instead of 401"
require_json "$responses/unauthenticated-events.json"
code="$(request_code "$responses/login.json" \
  -c "$cookie_jar" -H 'Content-Type: application/json' \
  --data-binary "@$work/password.json" "$base/api/auth/login")"
[[ "$code" == 200 ]] || fail "sidecar login returned HTTP $code"
require_json "$responses/login.json"
chmod 600 "$cookie_jar"

for endpoint in \
  workspace/bootstrap \
  settings \
  settings/preferences \
  gold/status; do
  target="$responses/${endpoint//\//-}.json"
  code="$(request_code "$target" -b "$cookie_jar" "$base/api/$endpoint")"
  [[ "$code" == 200 ]] || fail "$endpoint returned HTTP $code"
  require_json "$target"
done

python3 - "$responses/workspace-bootstrap.json" "$responses/gold-status.json" <<'PY'
import json
import sys
from pathlib import Path

bootstrap = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gold = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
required = {"watchlist", "preferences", "stock_reports", "market_recaps", "backtest_summaries"}
if bootstrap.get("schema_version") != 1 or set(bootstrap.get("resources", {})) != required:
    raise SystemExit("workspace bootstrap contract is invalid")
if gold.get("enabled") is not False or gold.get("external_send_count") != 0:
    raise SystemExit("Gold zero-send boundary is invalid")
PY

resource_headers="$responses/watchlist.headers"
code="$(request_code "$responses/watchlist-before.json" \
  -D "$resource_headers" -b "$cookie_jar" \
  "$base/api/workspace/resources/watchlist")"
[[ "$code" == 200 ]] || fail "watchlist resource returned HTTP $code"
etag="$(sed -n 's/^[Ee][Tt][Aa][Gg]:[[:space:]]*//p' "$resource_headers" | tr -d '\r' | head -n 1)"
[[ "$etag" =~ ^\"[0-9a-f]{64}\"$ ]] || fail "watchlist ETag is invalid"

curl -sS -N --connect-timeout 3 --max-time 20 \
  -b "$cookie_jar" "$base/api/workspace/events" \
  >"$responses/events.sse" 2>"$responses/events.stderr" &
sse_pid=$!
sleep 1

printf '%s' '{"operation":"add","payload":{"symbol":"000403.SZ"}}' \
  >"$work/add-command.json"
code="$(request_code "$responses/missing-precondition.json" \
  -b "$cookie_jar" -H 'Content-Type: application/json' \
  --data-binary "@$work/add-command.json" \
  "$base/api/workspace/resources/watchlist/commands")"
[[ "$code" == 428 ]] || fail "missing If-Match returned HTTP $code instead of 428"

printf '%s' '{"symbol":"000403.SZ"}' >"$work/legacy-write.json"
code="$(request_code "$responses/legacy-write.json" \
  -b "$cookie_jar" -H 'Content-Type: application/json' \
  --data-binary "@$work/legacy-write.json" "$base/api/watchlist")"
[[ "$code" == 409 ]] || fail "legacy workspace write returned HTTP $code instead of 409"

success_headers="$responses/success.headers"
code="$(request_code "$responses/success.json" \
  -D "$success_headers" -b "$cookie_jar" \
  -H 'Content-Type: application/json' -H "If-Match: $etag" \
  --data-binary "@$work/add-command.json" \
  "$base/api/workspace/resources/watchlist/commands")"
[[ "$code" == 200 ]] || fail "conditional workspace write returned HTTP $code"

code="$(request_code "$responses/stale.json" \
  -b "$cookie_jar" -H 'Content-Type: application/json' -H "If-Match: $etag" \
  --data-binary "@$work/add-command.json" \
  "$base/api/workspace/resources/watchlist/commands")"
[[ "$code" == 412 ]] || fail "stale workspace write returned HTTP $code instead of 412"

new_etag="$(sed -n 's/^[Ee][Tt][Aa][Gg]:[[:space:]]*//p' "$success_headers" | tr -d '\r' | head -n 1)"
[[ "$new_etag" =~ ^\"[0-9a-f]{64}\"$ ]] || fail "updated watchlist ETag is invalid"
TICKFLOW_CANARY_VALUE="$canary" python3 - "$work/invalid-command.json" <<'PY'
import json
import os
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "operation": "add",
    "payload": {"symbol": "600489.SH", "api_key": os.environ["TICKFLOW_CANARY_VALUE"]},
}), encoding="utf-8")
PY
code="$(request_code "$responses/invalid-command.json" \
  -b "$cookie_jar" -H 'Content-Type: application/json' -H "If-Match: $new_etag" \
  --data-binary "@$work/invalid-command.json" \
  "$base/api/workspace/resources/watchlist/commands")"
[[ "$code" == 422 ]] || fail "invalid canary command returned HTTP $code instead of 422"

sse_seen=false
for _ in $(seq 1 40); do
  if grep -Fq 'event: resource_changed' "$responses/events.sse" && \
     grep -Fq '"resource":"watchlist"' "$responses/events.sse"; then
    sse_seen=true
    break
  fi
  sleep 0.25
done
[[ "$sse_seen" == true ]] || fail "workspace SSE did not publish the watchlist revision"
kill "$sse_pid" >/dev/null 2>&1 || true
wait "$sse_pid" >/dev/null 2>&1 || true
sse_pid=""

code="$(request_code "$responses/watchlist-after.json" \
  -b "$cookie_jar" "$base/api/workspace/resources/watchlist")"
[[ "$code" == 200 ]] || fail "updated watchlist resource returned HTTP $code"
python3 - "$responses/watchlist-after.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
symbols = [row.get("symbol") for row in payload.get("data", {}).get("symbols", [])]
if symbols != ["000403.SZ"]:
    raise SystemExit("isolated watchlist result is invalid")
PY

docker logs "$sidecar" >"$responses/container.log" 2>&1 || fail "unable to read sidecar logs"
set +e
LC_ALL=C grep -R -a -F -q -- "$canary" "$responses"
scan_rc=$?
set -e
case "$scan_rc" in
  0) fail "secret canary reached an API response or container log" ;;
  1) ;;
  *) fail "canary response/log scan failed" ;;
esac

printf 'WORKSPACE_SIDECAR_CONTRACT_OK\n'
