#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
runner="$repo_root/scripts/run_phase1_daily_validation.sh"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_contains() {
  local needle="$1"
  local file="$2"
  grep -F -- "$needle" "$file" >/dev/null 2>&1 ||
    fail "expected '$needle' in $file"
}

assert_not_contains() {
  local needle="$1"
  local file="$2"
  if grep -F -- "$needle" "$file" >/dev/null 2>&1; then
    fail "did not expect '$needle' in $file"
  fi
}

assert_ssh_count() {
  local expected="$1"
  local actual
  actual="$(grep -c '^ssh ' "$call_log" || true)"
  [[ "$actual" == "$expected" ]] ||
    fail "expected $expected ssh calls, got $actual"
}

fake_bin="$tmp_dir/bin"
test_repo="$tmp_dir/repo"
mkdir -p "$fake_bin" "$test_repo/reports/phase1_observation"
call_log="$tmp_dir/calls.log"
: >"$call_log"

cat >"$fake_bin/ssh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'ssh %s\n' "$*" >>"$CALL_LOG"
IFS= read -r first_line || true
printf 'ssh-stdin-first-line %s\n' "$first_line" >>"$CALL_LOG"
cat >/dev/null
sleep "${FAKE_SSH_SLEEP:-0}"
printf '%s\n' '{"final_status":"PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING"}'
exit "${FAKE_SSH_EXIT_CODE:-0}"
SH

cat >"$fake_bin/python3" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'python3 %s\n' "$*" >>"$CALL_LOG"
case " $* " in
  *" --preflight-live-date "*)
    if [[ "${FAKE_GATE_EXIT_CODE:-0}" != "0" ]]; then
      printf '%s\n' "${FAKE_GATE_ERROR:-GATE_REJECTED}" >&2
      exit "$FAKE_GATE_EXIT_CODE"
    fi
    printf '{"decision": "%s"}\n' "${FAKE_GATE_DECISION:-LIVE_ALLOWED}"
    ;;
  *" --record-non-trading-day "*)
    printf '%s\n' '{"day_status":"NON_TRADING_DAY","new_api_request_count":0}'
    ;;
  *" --summarize "*)
    printf '%s\n' '{"final_status":"PHASE1_OBSERVATION_IN_PROGRESS"}'
    ;;
  *" --reuse-phase11-day1 "*)
    printf '%s\n' '{"day_status":"DAY_PASSED","new_api_request_count":0}'
    ;;
  *" --materialize-live "*)
    printf '%s\n' \
      '{"day_status":"DAY_PASSED","final_status":"PHASE1_OBSERVATION_IN_PROGRESS","valid_trading_days":2,"new_api_request_count":14}'
    exit "${FAKE_MATERIALIZE_EXIT_CODE:-0}"
    ;;
  *)
    printf '%s\n' 'unexpected fake python invocation' >&2
    exit 97
    ;;
esac
SH

chmod +x "$fake_bin/ssh" "$fake_bin/python3"
fake_validator="$tmp_dir/fake-live-validator.py"
printf '%s\n' '# phase1-test-live-validator' >"$fake_validator"

base_env=(
  "PATH=$fake_bin:/usr/bin:/bin"
  "CALL_LOG=$call_log"
  "PHASE1_REPO_ROOT=$test_repo"
  "PHASE1_PYTHON=python3"
  "PHASE1_TEST_MODE=1"
  "PHASE1_TEST_VALIDATOR_PATH=$fake_validator"
  "TICKFLOW_SSH_HOST=test-host"
  "TICKFLOW_CONTAINER=TickFlow_Stock_Panel"
)

live_confirmations=(
  "PHASE1_AUTH_CONFIGURED_CONFIRMED=YES"
  "PHASE1_SESSION_VALID_CONFIRMED=YES"
  "PHASE1_TICKFLOW_KEY_CONFIGURED_CONFIRMED=YES"
  "PHASE1_LOG_SCAN_PASSED=YES"
)

# Dry-run performs only the offline gate.
: >"$call_log"
env \
  "${base_env[@]}" \
  PHASE1_OBSERVATION_NOW="2026-07-28T16:30:00+08:00" \
  bash "$runner" --date 2026-07-28 --dry-run >/dev/null
assert_ssh_count 0
assert_contains "--preflight-live-date" "$call_log"
assert_not_contains "--materialize-live" "$call_log"

# Gate failures, including before-close/future/history/duplicate, stop before SSH.
for gate_error in \
  BEFORE_SAFE_WINDOW \
  FUTURE_DATE_FORBIDDEN \
  HISTORICAL_LIVE_FORBIDDEN \
  OBSERVATION_DATE_ALREADY_PASSED \
  OBSERVATION_REQUEST_ALREADY_RECORDED
do
  : >"$call_log"
  if env \
    "${base_env[@]}" \
    FAKE_GATE_EXIT_CODE=2 \
    FAKE_GATE_ERROR="$gate_error" \
    PHASE1_OBSERVATION_NOW="2026-07-28T16:30:00+08:00" \
    bash "$runner" --date 2026-07-28 >/dev/null 2>&1
  then
    fail "runner accepted gate failure $gate_error"
  fi
  assert_ssh_count 0
done

# A non-trading day is recorded offline and never reaches SSH.
: >"$call_log"
env \
  "${base_env[@]}" \
  FAKE_GATE_DECISION=NON_TRADING_DAY \
  PHASE1_OBSERVATION_NOW="2026-08-02T16:30:00+08:00" \
  bash "$runner" --date 2026-08-02 >/dev/null
assert_ssh_count 0
assert_contains "--record-non-trading-day" "$call_log"

# Missing auth, session, key, or log confirmation stops before SSH.
for missing in \
  PHASE1_AUTH_CONFIGURED_CONFIRMED \
  PHASE1_SESSION_VALID_CONFIRMED \
  PHASE1_TICKFLOW_KEY_CONFIGURED_CONFIRMED \
  PHASE1_LOG_SCAN_PASSED
do
  confirmations=("${live_confirmations[@]}")
  filtered=()
  for item in "${confirmations[@]}"; do
    [[ "$item" == "$missing="* ]] || filtered+=("$item")
  done
  : >"$call_log"
  if env \
    "${base_env[@]}" \
    "${filtered[@]}" \
    PHASE1_OBSERVATION_NOW="2026-07-28T16:30:00+08:00" \
    bash "$runner" --date 2026-07-28 >/dev/null 2>&1
  then
    fail "runner accepted missing $missing"
  fi
  assert_ssh_count 0
done

# A held global lock stops a second pipeline before SSH.
lock_dir="$test_repo/reports/phase1_observation/.runtime.lock"
mkdir "$lock_dir"
printf '%s\n' "$$" >"$lock_dir/owner.pid"
: >"$call_log"
if env \
  "${base_env[@]}" \
  "${live_confirmations[@]}" \
  PHASE1_OBSERVATION_NOW="2026-07-28T16:30:00+08:00" \
  bash "$runner" --date 2026-07-28 >/dev/null 2>&1
then
  fail "runner accepted a held runtime lock"
fi
assert_ssh_count 0
rm "$lock_dir/owner.pid"
rmdir "$lock_dir"

# A lock left by a dead process is recovered before the one allowed live run.
mkdir "$lock_dir"
printf '%s\n' "99999999" >"$lock_dir/owner.pid"
: >"$call_log"
env \
  "${base_env[@]}" \
  "${live_confirmations[@]}" \
  PHASE1_OBSERVATION_NOW="2026-07-28T16:30:00+08:00" \
  bash "$runner" --date 2026-07-28 >/dev/null
assert_ssh_count 1
[[ ! -e "$lock_dir" ]] || fail "stale runtime lock was not cleaned"

# A successful run is one SSH pipeline with the fixed contract and no retry.
: >"$call_log"
env \
  "${base_env[@]}" \
  "${live_confirmations[@]}" \
  PHASE1_OBSERVATION_NOW="2026-07-28T16:30:00+08:00" \
  bash "$runner" --date 2026-07-28 >/dev/null
assert_ssh_count 1
assert_contains "docker exec -i 'TickFlow_Stock_Panel'" "$call_log"
assert_contains "/app/.venv/bin/python - --live" "$call_log"
assert_contains "ssh-stdin-first-line # phase1-test-live-validator" "$call_log"
assert_contains "--session-valid-confirmed" "$call_log"
assert_contains "--log-scan-passed" "$call_log"
assert_contains "--materialize-live" "$call_log"
assert_contains "--observation-date 2026-07-28" "$call_log"
assert_contains \
  "--symbol-set-hash f5f48819c7db5ecac1e09e7a0ec634a09aa5675480a9bd0f6d2cc9eea8bf8dd8" \
  "$call_log"
assert_not_contains "intraday_batch" "$call_log"
assert_not_contains "retry" "$call_log"

# A 429/materialization failure is not retried.
: >"$call_log"
if env \
  "${base_env[@]}" \
  "${live_confirmations[@]}" \
  FAKE_MATERIALIZE_EXIT_CODE=2 \
  PHASE1_OBSERVATION_NOW="2026-07-28T16:30:00+08:00" \
  bash "$runner" --date 2026-07-28 >/dev/null 2>&1
then
  fail "runner accepted a blocked materialization"
fi
assert_ssh_count 1

# Two concurrent processes can produce at most one live SSH invocation.
: >"$call_log"
env \
  "${base_env[@]}" \
  "${live_confirmations[@]}" \
  FAKE_SSH_SLEEP=1 \
  PHASE1_OBSERVATION_NOW="2026-07-28T16:30:00+08:00" \
  bash "$runner" --date 2026-07-28 >/dev/null 2>&1 &
first_pid=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  grep -q '^ssh ' "$call_log" && break
  sleep 0.1
done
if env \
  "${base_env[@]}" \
  "${live_confirmations[@]}" \
  PHASE1_OBSERVATION_NOW="2026-07-28T16:30:00+08:00" \
  bash "$runner" --date 2026-07-28 >/dev/null 2>&1
then
  fail "second concurrent runner acquired the runtime lock"
fi
wait "$first_pid"
assert_ssh_count 1

# Validate-only and Day 1 reuse are offline.
: >"$call_log"
env "${base_env[@]}" bash "$runner" --validate-only >/dev/null
env "${base_env[@]}" bash "$runner" --reuse-day1 >/dev/null
assert_ssh_count 0
assert_contains "--summarize" "$call_log"
assert_contains "--reuse-phase11-day1" "$call_log"

if grep -F -- "intraday_batch" "$runner" >/dev/null 2>&1; then
  fail "runner source contains a forbidden batch capability"
fi
if grep -E -- '--force-live|--ignore-existing|--skip-gates' "$runner" >/dev/null 2>&1; then
  fail "runner exposes a forbidden bypass option"
fi

printf 'PASS: run_phase1_daily_validation boundaries\n'
