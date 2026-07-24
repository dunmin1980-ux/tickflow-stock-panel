#!/usr/bin/env bash
set -euo pipefail

ssh_host="${TICKFLOW_SSH_HOST:-codex-vm}"
pwa_url="${TICKFLOW_PWA_URL:-https://vm-0-9-ubuntu.tail21c236.ts.net:8443}"
tailnet_host="${TICKFLOW_TAILNET_HOST:-vm-0-9-ubuntu.tail21c236.ts.net}"
container="${TICKFLOW_CONTAINER:-TickFlow_Stock_Panel}"
deployment_root="${TICKFLOW_DEPLOYMENT_ROOT:-/home/ubuntu/tickflow-stock-panel-stage-a}"
expected_image="${TICKFLOW_EXPECT_IMAGE:-sha256:f4b6652d3b57d680b366057c32149f00a5e9fd099961c0245ab30cb4a4cbed43}"
expected_revision="${TICKFLOW_EXPECT_REVISION:-494ee71481f1487c1046e1030381c1dfab03d707}"
expected_tag="${TICKFLOW_EXPECT_TAG:-tickflow-stock-panel-stage-a-app:candidate-494ee71}"
expected_user_data_sha="${TICKFLOW_EXPECT_USER_DATA_SHA:-949132c081de283f72829dadb95435bcf8d20bbe8623a483185fe9691d7706d0}"
expected_backup="${TICKFLOW_EXPECT_BACKUP:-/home/ubuntu/tickflow-backups/20260724_105311-pre-pwa-494ee71}"
expected_workbox="${TICKFLOW_EXPECT_WORKBOX:-workbox-9c191d2f.js}"
ssh_command=(ssh -o BatchMode=yes -o ConnectTimeout=10)
if [[ "${TICKFLOW_SSH_DIRECT:-1}" == "1" ]]; then
  ssh_command+=(-o ProxyCommand=none)
fi

fail() {
  printf 'CLOUD_P0_RUNTIME_FAILED: %s\n' "$*" >&2
  exit 1
}

for tool in curl python3 ssh; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing required tool: $tool"
done
[[ "$container" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail "invalid container name"

remote_output_file="$(
  mktemp "${TMPDIR:-/tmp}/tickflow-cloud-p0.XXXXXX"
)"
trap 'rm -f "$remote_output_file"' EXIT

"${ssh_command[@]}" "$ssh_host" bash -s -- \
  "$container" "$deployment_root" "$expected_image" "$expected_revision" \
  "$expected_tag" "$expected_user_data_sha" "$expected_backup" \
  "$tailnet_host" "$expected_workbox" >"$remote_output_file" <<'REMOTE'
set -euo pipefail

container="$1"
root="$2"
expected_image="$3"
expected_revision="$4"
expected_tag="$5"
expected_user_data_sha="$6"
expected_backup="$7"
tailnet_host="$8"
expected_workbox="$9"

test "$(docker inspect "$container" --format '{{.Image}}')" = "$expected_image"
test "$(
  docker inspect "$container" \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
)" = "$expected_revision"
test "$(docker inspect "$container" --format '{{.RestartCount}}')" = 0
test "$(cd "$root" && docker compose config --images)" = "$expected_tag"
test "$(docker image inspect "$expected_tag" --format '{{.Id}}')" = "$expected_image"

ss -lntH | grep -Eq '127.0.0.1:3018[[:space:]]'
ss -lntH | grep -Eq '127.0.0.1:3019[[:space:]]'
! ss -lntH | grep -Eq '(0.0.0.0|\[::\]):(3018|3019|5678)[[:space:]]'

serve_status="$(sudo tailscale serve status)"
grep -Fq "https://${tailnet_host} (tailnet only)" <<<"$serve_status"
grep -Fq "https://${tailnet_host}:8443 (tailnet only)" <<<"$serve_status"
grep -Fq 'proxy http://127.0.0.1:8787' <<<"$serve_status"
grep -Fq 'proxy http://127.0.0.1:3019' <<<"$serve_status"
! grep -Eqi 'funnel|public' <<<"$serve_status"

health="$(curl -fsS http://127.0.0.1:3019/health)"
auth="$(curl -fsS http://127.0.0.1:3019/api/auth/status)"
gold="$(curl -fsS http://127.0.0.1:3019/api/gold/status)"
HEALTH_JSON="$health" AUTH_JSON="$auth" GOLD_JSON="$gold" python3 <<'PY'
import json
import os

health = json.loads(os.environ["HEALTH_JSON"])
auth = json.loads(os.environ["AUTH_JSON"])
gold = json.loads(os.environ["GOLD_JSON"])
assert health == {"status": "ok", "version": "0.1.86", "mode": "none"}
assert auth == {"configured": False, "authenticated": False}
assert gold.get("enabled") is False
assert gold.get("external_send_count") == 0
PY

docker exec "$container" sh -c \
  'test "$AUTH_COOKIE_SECURE" = true &&
   test "$GOLD_WORKSPACE_ENABLED" = false &&
   test "$WORKSPACE_SYNC_ENABLED" = true'

for asset in \
  index.html manifest.webmanifest sw.js registerSW.js "$expected_workbox" \
  favicon.svg apple-touch-icon.png pwa-192.png pwa-512.png; do
  image_hash="$(
    docker exec "$container" sha256sum "/app/static/$asset" | awk '{print $1}'
  )"
  http_hash="$(
    curl -fsS "http://127.0.0.1:3019/$asset" | sha256sum | awk '{print $1}'
  )"
  test "$image_hash" = "$http_hash"
done

bundle_assets="$(
  docker exec -i "$container" /app/.venv/bin/python - <<'PY'
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: set[str] = set()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        candidate: str | None = None
        if tag == "script":
            candidate = values.get("src")
        elif tag == "link" and "stylesheet" in (values.get("rel") or "").split():
            candidate = values.get("href")
        if candidate:
            path = urlparse(candidate).path.lstrip("/")
            if path.startswith("assets/"):
                self.assets.add(path)


parser = AssetParser()
parser.feed(Path("/app/static/index.html").read_text())
assets = sorted(parser.assets)
assert any(path.endswith(".js") for path in assets)
assert any(path.endswith(".css") for path in assets)
print("\n".join(assets))
PY
)"

bundle_count=0
while IFS= read -r asset; do
  [[ -n "$asset" ]] || continue
  image_hash="$(
    docker exec "$container" sha256sum "/app/static/$asset" | awk '{print $1}'
  )"
  http_hash="$(
    curl -fsS "http://127.0.0.1:3019/$asset" | sha256sum | awk '{print $1}'
  )"
  test "$image_hash" = "$http_hash"
  printf 'PWA_BUNDLE_HASH|%s|%s\n' "$image_hash" "$asset"
  ((bundle_count += 1))
done <<<"$bundle_assets"
test "$bundle_count" -ge 2

register_type="$(
  curl -fsS -D - -o /dev/null http://127.0.0.1:3019/registerSW.js |
    awk -F': ' 'tolower($1)=="content-type" {gsub(/\r/,"",$2); print $2}'
)"
workbox_type="$(
  curl -fsS -D - -o /dev/null "http://127.0.0.1:3019/$expected_workbox" |
    awk -F': ' 'tolower($1)=="content-type" {gsub(/\r/,"",$2); print $2}'
)"
case "$register_type" in *javascript*) ;; *) exit 1 ;; esac
case "$workbox_type" in *javascript*) ;; *) exit 1 ;; esac
test "$(
  curl -sS -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:3019/workbox-deadbeef.js
)" = 404

test "$(sudo sha256sum "$root/data/user_data/preferences.json" | awk '{print $1}')" \
  = "$expected_user_data_sha"
test -d "$expected_backup/data"
test "$(sudo stat -c %s "$expected_backup/user-data.diff")" = 0
rollback_tag="$(cat "$HOME/.local/state/tickflow-stock-panel-stage-a/last-rollback-image")"
docker image inspect "$rollback_tag" >/dev/null
! docker logs --since 30m "$container" 2>&1 | grep -Eq 'Traceback|ERROR|CRITICAL'

printf 'CLOUD_HOST_RUNTIME_OK\n'
REMOTE
remote_output="$(cat "$remote_output_file")"
printf '%s\n' "$remote_output"

hash_url() {
  curl -fsS --connect-timeout 10 --max-time 30 "$1" |
    python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

bundle_count=0
while IFS='|' read -r marker expected_hash asset; do
  [[ "$marker" == "PWA_BUNDLE_HASH" ]] || continue
  actual_hash="$(hash_url "$pwa_url/$asset")"
  [[ "$actual_hash" == "$expected_hash" ]] || \
    fail "Tailscale bundle hash mismatch: $asset"
  ((bundle_count += 1))
done <<<"$remote_output"
[[ "$bundle_count" -ge 2 ]] || fail "main JavaScript/CSS bundles were not verified"

while IFS='|' read -r expected_hash asset; do
  [[ -n "$asset" ]] || continue
  actual_hash="$(hash_url "$pwa_url/$asset")"
  [[ "$actual_hash" == "$expected_hash" ]] || \
    fail "Tailscale asset hash mismatch: $asset"
done <<'ASSETS'
1c966058ec417e64b2f527fe9cc6541f2addf21ba6698797be305b7e4d52e4fa|index.html
2a4b725a31823cf2e0f163c09f229d68faf4bdecb89acc5a643d3014914ed9ca|manifest.webmanifest
05d8786cd8ba7e8e3174aa2913997063504997ddb479bb65f00272e255de07dc|sw.js
9742073ef7fc795e7673d98f272992843298426a0ffd8cb3507784df5143608b|registerSW.js
04b086f1b2f4215ee4b659a7bc9c76162894abdb43fa876247bc7c0bd6fd1c37|workbox-9c191d2f.js
48fc1328a4dc89ae99dd8b8136b7befbc755ab5d5d6515541d92ae7a9e88ba12|pwa-192.png
bcb9a80e46b34fc42ac84b22f7208a0c38cb205fdcb3058eca339f1af15ee9fb|pwa-512.png
ASSETS

register_type="$(
  curl -fsS -D - -o /dev/null --connect-timeout 10 --max-time 30 \
    "$pwa_url/registerSW.js" |
    awk -F': ' 'tolower($1)=="content-type" {gsub(/\r/,"",$2); print $2}'
)"
workbox_type="$(
  curl -fsS -D - -o /dev/null --connect-timeout 10 --max-time 30 \
    "$pwa_url/$expected_workbox" |
    awk -F': ' 'tolower($1)=="content-type" {gsub(/\r/,"",$2); print $2}'
)"
case "$register_type" in *javascript*) ;; *) fail "registerSW.js is not JavaScript" ;; esac
case "$workbox_type" in *javascript*) ;; *) fail "Workbox runtime is not JavaScript" ;; esac
[[ "$(
  curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 10 --max-time 30 \
    "$pwa_url/workbox-deadbeef.js"
)" == 404 ]] || fail "missing Workbox asset does not fail closed"

echo CLOUD_P0_RUNTIME_OK
