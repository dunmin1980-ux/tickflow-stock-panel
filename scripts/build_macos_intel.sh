#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
icon="$root/packaging/icon.icns"
report_dir="$root/reports/macos_intel_acceptance"
app="$root/backend/dist/TickFlowStockPanel.app"
dmg="$root/dist/TickFlowStockPanel-intel-x86_64.dmg"
pyinstaller_version="6.16.0"
canary_receipt="$report_dir/security_canary_build.json"
canary_digest=""
build_canary_data=""

cleanup_canary_only() {
  local rc=$?
  trap - EXIT
  if [[ -n "$build_canary_data" && -d "$build_canary_data" ]]; then
    rm -rf -- "$build_canary_data" || rc=1
  fi
  exit "$rc"
}
trap cleanup_canary_only EXIT

fail() {
  printf 'MACOS_INTEL_BUILD_FAILED: %s\n' "$*" >&2
  exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "build requires macOS"
[[ "$(uname -m)" == "x86_64" ]] || fail "build requires an Intel x86_64 Mac"
for tool in corepack uv codesign hdiutil shasum; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing required tool: $tool"
done
[[ -f "$icon" ]] || fail "missing committed macOS icon: $icon"
[[ -f "$root/frontend/pnpm-lock.yaml" ]] || fail "missing frontend lockfile"
[[ -f "$root/backend/uv.lock" ]] || fail "missing backend lockfile"

umask 077
mkdir -p "$report_dir" "$root/dist"
rm -f "$canary_receipt" "$report_dir/security_canary_build.sha256"
if [[ -n "${TICKFLOW_SECRET_CANARY:-}" ]]; then
  [[ "${#TICKFLOW_SECRET_CANARY}" -ge 24 ]] || fail "secret canary must be at least 24 characters"
  case "$TICKFLOW_SECRET_CANARY" in
    *$'\n'*|*$'\r'*) fail "secret canary must be one line" ;;
  esac
  canary_digest="$(printf '%s' "$TICKFLOW_SECRET_CANARY" | shasum -a 256 | awk '{print $1}')"
  build_canary_data="$(mktemp -d "${TMPDIR:-/tmp}/tickflow-build-canary.XXXXXX")"
  install -d -m 700 "$build_canary_data/user_data"
  TICKFLOW_CANARY_VALUE="$TICKFLOW_SECRET_CANARY" python3 - "$build_canary_data" <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
canary = os.environ["TICKFLOW_CANARY_VALUE"]
(root / "user_data" / "secrets.json").write_text(json.dumps({
    "tickflow_api_key": canary,
    "tushare_token": canary,
    "ai_api_key": canary,
}), encoding="utf-8")
(root / "user_data" / "preferences.json").write_text(json.dumps({
    "feishu_webhook_url": f"https://open.feishu.cn/open-apis/bot/v2/hook/{canary}",
    "feishu_webhook_secret": canary,
    "wecom_webhook_url": f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={canary}",
}), encoding="utf-8")
for path in (root / "user_data").iterdir():
    path.chmod(0o600)
PY
  export DATA_DIR="$build_canary_data"
  export TICKFLOW_API_KEY="$TICKFLOW_SECRET_CANARY"
  export TUSHARE_TOKEN="$TICKFLOW_SECRET_CANARY"
  export AI_API_KEY="$TICKFLOW_SECRET_CANARY"
  export AUTH_PASSWORD="$TICKFLOW_SECRET_CANARY"
  export FEISHU_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/$TICKFLOW_SECRET_CANARY"
  export FEISHU_WEBHOOK_SECRET="$TICKFLOW_SECRET_CANARY"
  export WECOM_WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=$TICKFLOW_SECRET_CANARY"
fi
icon_hash_before="$(shasum -a 256 "$icon" | awk '{print $1}')"
printf 'before %s  %s\n' "$icon_hash_before" "$icon" > "$report_dir/icon_hashes.txt"

verify_icon_unchanged() {
  icon_hash_after="$(shasum -a 256 "$icon" | awk '{print $1}')"
  printf 'after  %s  %s\n' "$icon_hash_after" "$icon" >> "$report_dir/icon_hashes.txt"
  if [[ "$icon_hash_after" != "$icon_hash_before" ]]; then
    printf 'MACOS_INTEL_BUILD_FAILED: packaging/icon.icns changed during build\n' >&2
    return 1
  fi
}

cleanup_build() {
  local rc=$?
  trap - EXIT
  if [[ -n "$build_canary_data" && -d "$build_canary_data" ]]; then
    rm -rf -- "$build_canary_data" || rc=1
  fi
  verify_icon_unchanged || rc=1
  exit "$rc"
}
trap cleanup_build EXIT

cd "$root/frontend"
corepack pnpm@9.10.0 install --frozen-lockfile --registry=https://registry.npmjs.org
corepack pnpm@9.10.0 build

cd "$root/backend"
uv sync --frozen --no-dev --extra desktop
python_arch="$(.venv/bin/python -c 'import platform; print(platform.machine())')"
[[ "$python_arch" == "x86_64" ]] || fail "backend Python is not x86_64: $python_arch"
uv pip install \
  --python .venv/bin/python \
  --default-index https://pypi.org/simple \
  "pyinstaller==$pyinstaller_version"
uv run --no-sync pyinstaller ../packaging/tickflow.spec --noconfirm --clean

[[ -d "$app" ]] || fail "PyInstaller did not produce $app"
codesign --force --deep --sign - "$app"

rm -f "$dmg" "$dmg.sha256"
hdiutil create \
  -volname "TickFlow Stock Panel" \
  -srcfolder "$app" \
  -ov \
  -format UDZO \
  "$dmg"
(
  cd "$root/dist"
  shasum -a 256 "$(basename "$dmg")" > "$(basename "$dmg").sha256"
)
chmod 600 "$dmg" "$dmg.sha256"

{
  printf 'platform=%s\n' "$(sw_vers -productVersion)"
  printf 'machine=%s\n' "$(uname -m)"
  printf 'python=%s\n' "$(.venv/bin/python --version 2>&1)"
  printf 'pyinstaller=%s\n' "$(.venv/bin/pyinstaller --version)"
  printf 'app=%s\n' "$app"
  printf 'dmg=%s\n' "$dmg"
} > "$report_dir/build_environment.txt"

if [[ -n "$canary_digest" ]]; then
  pwa_digest="$(python3 "$root/scripts/artifact_tree_digest.py" "$root/frontend/dist")"
  app_digest="$(python3 "$root/scripts/artifact_tree_digest.py" "$app")"
  dmg_digest="$(shasum -a 256 "$dmg" | awk '{print $1}')"
  dmg_bytes="$(stat -f '%z' "$dmg")"
  build_script_digest="$(shasum -a 256 "$root/scripts/build_macos_intel.sh" | awk '{print $1}')"
  python3 - \
    "$canary_receipt" "$canary_digest" "$pwa_digest" "$app_digest" \
    "$dmg_digest" "$dmg_bytes" "$build_script_digest" <<'PY'
import json
import os
import sys
from pathlib import Path

receipt = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "canary_sha256": sys.argv[2],
    "pwa_tree_sha256": sys.argv[3],
    "app_tree_sha256": sys.argv[4],
    "dmg_sha256": sys.argv[5],
    "dmg_bytes": int(sys.argv[6]),
    "build_script_sha256": sys.argv[7],
    "canary_sources": [
        "tickflow_api_key",
        "tushare_token",
        "ai_api_key",
        "auth_password",
        "feishu_webhook_url",
        "feishu_webhook_secret",
        "wecom_webhook_url",
        "user_data/secrets.json",
        "user_data/preferences.json",
    ],
}
receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(receipt, 0o600)
PY
fi
printf 'MACOS_INTEL_BUILD_OK\n'
