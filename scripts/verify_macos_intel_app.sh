#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
app="$root/backend/dist/TickFlowStockPanel.app"
main="$app/Contents/MacOS/TickFlowStockPanel"
dmg="$root/dist/TickFlowStockPanel-intel-x86_64.dmg"
checksum="$dmg.sha256"
report_dir="$root/reports/macos_intel_acceptance"

fail() {
  printf 'MACOS_INTEL_VERIFY_FAILED: %s\n' "$*" >&2
  exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "verification requires macOS"
[[ "$(uname -m)" == "x86_64" ]] || fail "verification requires an Intel x86_64 Mac"
[[ -x "$main" ]] || fail "missing frozen executable: $main"
[[ -f "$dmg" ]] || fail "missing DMG: $dmg"
[[ -f "$checksum" ]] || fail "missing SHA-256 file: $checksum"

mkdir -p "$report_dir"
: > "$report_dir/native_arch.txt"

main_archs="$(lipo -archs "$main" 2>/dev/null)" || fail "main executable is not Mach-O"
printf '%s: %s\n' "$main" "$main_archs" > "$report_dir/main_arch.txt"
case " $main_archs " in
  *" x86_64 "*) ;;
  *) fail "main executable does not contain x86_64: $main_archs" ;;
esac

native_count=0
while IFS= read -r -d '' native_file; do
  native_count=$((native_count + 1))
  native_archs="$(lipo -archs "$native_file" 2>/dev/null)" || \
    fail "native library is not a valid Mach-O file: $native_file"
  file "$native_file" >> "$report_dir/native_arch.txt"
  printf 'architectures: %s\n' "$native_archs" >> "$report_dir/native_arch.txt"
  case " $native_archs " in
    *" x86_64 "*) ;;
    *) fail "arm64-only or unsupported native library: $native_file ($native_archs)" ;;
  esac
done < <(find "$app" -type f \( -name '*.so' -o -name '*.dylib' \) -print0)
[[ "$native_count" -gt 0 ]] || fail "no native .so/.dylib files found"

codesign --verify --deep --strict --verbose=2 "$app" \
  > "$report_dir/codesign.txt" 2>&1 || fail "codesign verification failed"
hdiutil verify "$dmg" > "$report_dir/dmg_verify.txt" 2>&1 || fail "DMG verification failed"
(
  cd "$root/dist"
  shasum -a 256 -c "$(basename "$checksum")"
) > "$report_dir/sha256_check.txt" 2>&1 || fail "DMG SHA-256 verification failed"

plist="$app/Contents/Info.plist"
[[ "$(plutil -extract LSMinimumSystemVersion raw -o - "$plist")" == "10.15" ]] || \
  fail "LSMinimumSystemVersion must be 10.15"
[[ "$(plutil -extract NSAppTransportSecurity.NSAllowsArbitraryLoads raw -o - "$plist")" == "false" ]] || \
  fail "NSAllowsArbitraryLoads must be false"
[[ "$(plutil -extract NSAppTransportSecurity.NSAllowsLocalNetworking raw -o - "$plist")" == "true" ]] || \
  fail "NSAllowsLocalNetworking must be true"

smoke_home="$(mktemp -d "${TMPDIR:-/tmp}/tickflow-intel-smoke.XXXXXX")"
smoke_home="$(cd "$smoke_home" && pwd -L)"
trap 'rm -rf "$smoke_home"' EXIT
import_report="$report_dir/frozen_imports.json"
rm -f "$import_report"
(
  cd "$smoke_home"
  env -i \
    HOME="$smoke_home" \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    TMPDIR="${TMPDIR:-/tmp}" \
    LANG="en_US.UTF-8" \
    TICKFLOW_DESKTOP_CLIENT="1" \
    TICKFLOW_DESKTOP_SMOKE="1" \
    TICKFLOW_FROZEN_IMPORT_SMOKE="1" \
    TICKFLOW_FROZEN_IMPORT_REPORT="$import_report" \
    "$main"
) > "$report_dir/frozen_smoke.txt" 2>&1 || fail "frozen desktop smoke failed"

[[ -f "$import_report" ]] || fail "frozen import report was not produced"
[[ "$(plutil -extract frozen raw -o - "$import_report")" == "true" ]] || \
  fail "smoke did not execute inside a frozen application"
for module in polars pyarrow duckdb webview; do
  plutil -extract "modules.$module" raw -o - "$import_report" >/dev/null || \
    fail "frozen import failed for $module"
done

expected_data_dir="$smoke_home/Library/Application Support/TickFlowStockPanel"
actual_data_dir="$(plutil -extract data_dir raw -o - "$import_report")"
[[ "$actual_data_dir" == "$expected_data_dir" ]] || \
  fail "unexpected frozen data directory: $actual_data_dir"
[[ -d "$expected_data_dir" ]] || fail "Application Support data directory was not created"
[[ ! -e "$expected_data_dir/.desktop.lock" ]] || fail "desktop lock was not released"

sleep 1
if pgrep -f "$main" > "$report_dir/residual_processes.txt" 2>&1; then
  fail "frozen process remained after smoke"
fi
: > "$report_dir/residual_processes.txt"
if lsof -nP -a -c TickFlowStockPanel -iTCP -sTCP:LISTEN \
  > "$report_dir/residual_listeners.txt" 2>&1; then
  fail "TickFlowStockPanel retained a listening socket"
fi
: > "$report_dir/residual_listeners.txt"

for prohibited_name in .env secrets.json auth.json session.json sync-state.json; do
  if find "$app" -name "$prohibited_name" -print -quit | grep -q .; then
    fail "bundle contains prohibited private file: $prohibited_name"
  fi
done
if find "$app" -type f \( -name '*.duckdb' -o -name '*.parquet' -o -name '*.sqlite' \) \
  -print -quit | grep -q .; then
  fail "bundle contains a user data or report artifact"
fi

printf 'MACOS_INTEL_APP_OK\n'
