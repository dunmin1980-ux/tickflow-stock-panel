#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
icon="$root/packaging/icon.icns"
report_dir="$root/reports/macos_intel_acceptance"
app="$root/backend/dist/TickFlowStockPanel.app"
dmg="$root/dist/TickFlowStockPanel-intel-x86_64.dmg"
pyinstaller_version="6.16.0"

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
icon_hash_before="$(shasum -a 256 "$icon" | awk '{print $1}')"
printf 'before %s  %s\n' "$icon_hash_before" "$icon" > "$report_dir/icon_hashes.txt"

verify_icon_unchanged() {
  icon_hash_after="$(shasum -a 256 "$icon" | awk '{print $1}')"
  printf 'after  %s  %s\n' "$icon_hash_after" "$icon" >> "$report_dir/icon_hashes.txt"
  if [[ "$icon_hash_after" != "$icon_hash_before" ]]; then
    printf 'MACOS_INTEL_BUILD_FAILED: packaging/icon.icns changed during build\n' >&2
    exit 1
  fi
}
trap verify_icon_unchanged EXIT

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

verify_icon_unchanged
trap - EXIT
printf 'MACOS_INTEL_BUILD_OK\n'
