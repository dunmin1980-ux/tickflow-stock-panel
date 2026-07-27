#!/usr/bin/env bash
set -euo pipefail

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="${PHASE1_REPO_ROOT:-$source_root}"
python_command="${PHASE1_PYTHON:-python3}"
observer="$source_root/scripts/validate_phase1_observation.py"
validator="$source_root/backend/scripts/validate_phase1_tickflow_contracts.py"
ssh_host="${TICKFLOW_SSH_HOST:-codex-vm}"
container="${TICKFLOW_CONTAINER:-TickFlow_Stock_Panel}"
symbol_set_hash="f5f48819c7db5ecac1e09e7a0ec634a09aa5675480a9bd0f6d2cc9eea8bf8dd8"

fail() {
  printf 'PHASE1_DAILY_VALIDATION_FAILED: %s\n' "$*" >&2
  exit 2
}

if [[ -n "${PHASE1_TEST_VALIDATOR_PATH:-}" ]]; then
  [[ "${PHASE1_TEST_MODE:-0}" == "1" ]] ||
    fail "PHASE1_TEST_VALIDATOR_PATH is test-only"
  validator="$PHASE1_TEST_VALIDATOR_PATH"
fi

[[ -f "$observer" ]] || fail "observation validator missing"
[[ -f "$validator" ]] || fail "live contract validator missing"
command -v "$python_command" >/dev/null 2>&1 ||
  fail "Python command unavailable"

mode="live"
observation_date=""
dry_run=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --reuse-day1)
      [[ "$mode" == "live" && -z "$observation_date" && "$dry_run" == "0" ]] ||
        fail "--reuse-day1 cannot be combined with other options"
      mode="reuse"
      shift
      ;;
    --validate-only)
      [[ "$mode" == "live" && -z "$observation_date" && "$dry_run" == "0" ]] ||
        fail "--validate-only cannot be combined with other options"
      mode="validate"
      shift
      ;;
    --date)
      [[ "$#" -ge 2 && -z "$observation_date" ]] ||
        fail "--date requires one value"
      observation_date="$2"
      shift 2
      ;;
    --dry-run)
      [[ "$dry_run" == "0" ]] || fail "--dry-run supplied more than once"
      dry_run=1
      shift
      ;;
    *)
      fail "unsupported argument: $1"
      ;;
  esac
done

if [[ "$mode" == "reuse" ]]; then
  exec "$python_command" "$observer" \
    --reuse-phase11-day1 \
    --repo-root "$repo_root"
fi
if [[ "$mode" == "validate" ]]; then
  exec "$python_command" "$observer" \
    --summarize \
    --repo-root "$repo_root"
fi

now="${PHASE1_OBSERVATION_NOW:-}"
if [[ -n "$now" && "${PHASE1_TEST_MODE:-0}" != "1" ]]; then
  fail "PHASE1_OBSERVATION_NOW is test-only"
fi
if [[ -z "$now" ]]; then
  now="$(TZ=Asia/Shanghai date '+%Y-%m-%dT%H:%M:%S+08:00')"
fi
if [[ ! "$now" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2})T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00$ ]]; then
  fail "current time must be Asia/Shanghai ISO-8601"
fi
current_date="${BASH_REMATCH[1]}"
if [[ -z "$observation_date" ]]; then
  observation_date="$current_date"
fi
[[ "$observation_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] ||
  fail "--date must be YYYY-MM-DD"

gate_args=(
  "$observer"
  --preflight-live-date
  --repo-root "$repo_root"
  --observation-date "$observation_date"
  --now "$now"
  --symbol-set-hash "$symbol_set_hash"
)
gate_output="$("$python_command" "${gate_args[@]}")"
if [[ "$dry_run" == "1" ]]; then
  printf '%s\n' "$gate_output"
  exit 0
fi

observation_root="$repo_root/reports/phase1_observation"
lock_dir="$observation_root/.runtime.lock"
owner_file="$lock_dir/owner.pid"
mkdir -p "$observation_root"
[[ ! -L "$observation_root" ]] || fail "observation root cannot be a symlink"

acquire_lock() {
  if [[ -L "$lock_dir" ]]; then
    fail "runtime lock cannot be a symlink"
  fi
  if [[ -e "$lock_dir" ]]; then
    [[ -d "$lock_dir" && -f "$owner_file" && ! -L "$owner_file" ]] ||
      fail "runtime lock is malformed"
    owner_pid="$(tr -cd '0-9' <"$owner_file")"
    [[ -n "$owner_pid" ]] || fail "runtime lock owner is invalid"
    if kill -0 "$owner_pid" >/dev/null 2>&1; then
      fail "another observation pipeline is running"
    fi
    rm -f "$owner_file"
    rmdir "$lock_dir" 2>/dev/null ||
      fail "stale runtime lock could not be recovered"
  fi
  mkdir "$lock_dir" 2>/dev/null ||
    fail "another observation pipeline acquired the runtime lock"
  umask 077
  printf '%s\n' "$$" >"$owner_file"
}

release_lock() {
  if [[ -d "$lock_dir" && ! -L "$lock_dir" ]]; then
    rm -f "$owner_file"
    rmdir "$lock_dir" >/dev/null 2>&1 || true
  fi
}

acquire_lock
temp_result=""
retain_result=0
cleanup() {
  release_lock
  if [[ -n "$temp_result" && "$retain_result" == "0" ]]; then
    rm -f "$temp_result"
  fi
}
trap cleanup EXIT INT TERM

# Re-evaluate after taking the process lock so no concurrent run can publish first.
gate_output="$("$python_command" "${gate_args[@]}")"
case "$gate_output" in
  *'"decision": "NON_TRADING_DAY"'*)
    "$python_command" "$observer" \
      --record-non-trading-day \
      --repo-root "$repo_root" \
      --observation-date "$observation_date" \
      --now "$now" \
      --symbol-set-hash "$symbol_set_hash"
    exit 0
    ;;
  *'"decision": "LIVE_ALLOWED"'*)
    ;;
  *)
    fail "offline gate returned an unknown decision"
    ;;
esac

[[ "${PHASE1_AUTH_CONFIGURED_CONFIRMED:-}" == "YES" ]] ||
  fail "PHASE1_AUTH_CONFIGURED_CONFIRMED=YES is required"
[[ "${PHASE1_SESSION_VALID_CONFIRMED:-}" == "YES" ]] ||
  fail "PHASE1_SESSION_VALID_CONFIRMED=YES is required"
[[ "${PHASE1_TICKFLOW_KEY_CONFIGURED_CONFIRMED:-}" == "YES" ]] ||
  fail "PHASE1_TICKFLOW_KEY_CONFIGURED_CONFIRMED=YES is required"
[[ "${PHASE1_LOG_SCAN_PASSED:-}" == "YES" ]] ||
  fail "PHASE1_LOG_SCAN_PASSED=YES is required"
[[ "$ssh_host" =~ ^[A-Za-z0-9._@-]+$ ]] || fail "invalid SSH host"
[[ "$container" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] ||
  fail "invalid container name"
command -v ssh >/dev/null 2>&1 || fail "ssh unavailable"

temp_result="$(mktemp "${TMPDIR:-/tmp}/tickflow-phase1-observation.XXXXXX")"
chmod 600 "$temp_result"

ssh_command=(ssh -o BatchMode=yes -o ConnectTimeout=10)
if [[ "${TICKFLOW_SSH_DIRECT:-1}" == "1" ]]; then
  ssh_command+=(-o ProxyCommand=none)
fi
remote_command=(
  "docker exec -i '$container' /app/.venv/bin/python - --live"
  "--session-valid-confirmed --log-scan-passed"
)

set +e
"${ssh_command[@]}" "$ssh_host" "${remote_command[*]}" \
  <"$validator" >"$temp_result"
remote_status=$?
set -e

[[ -s "$temp_result" ]] ||
  fail "live validator returned no sanitized result"

set +e
"$python_command" "$observer" \
  --materialize-live "$temp_result" \
  --observation-date "$observation_date" \
  --now "$now" \
  --symbol-set-hash "$symbol_set_hash" \
  --repo-root "$repo_root"
materialize_status=$?
set -e

if [[ "$materialize_status" -ne 0 ]]; then
  retain_result=1
  printf 'Sanitized failure evidence retained at: %s\n' "$temp_result" >&2
  exit "$materialize_status"
fi
if [[ "$remote_status" -ne 0 ]]; then
  retain_result=1
  fail "remote validator exited nonzero despite a passing local materialization"
fi

printf 'PHASE1_DAILY_VALIDATION_RECORDED: %s\n' "$observation_date"
