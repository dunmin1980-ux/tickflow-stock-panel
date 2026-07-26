#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fixture_dir="$(mktemp -d "${TMPDIR:-/tmp}/tickflow-readiness-test.XXXXXX")"
trap 'rm -rf "$fixture_dir"' EXIT

cat >"$fixture_dir/ssh" <<'FAKE_SSH'
#!/usr/bin/env bash
set -euo pipefail

if [[ "$*" == *"/api/auth/status"* ]]; then
  printf '{"configured":true,"authenticated":false}\n'
  exit 0
fi

cat >/dev/null
today="$(date +%F)"
preference_mode="600"
factor_symbols='["000403.SZ"]'
financial_symbols='["000403.SZ"]'
if [[ "${READINESS_FIXTURE:-}" == "unsafe_preferences" ]]; then
  preference_mode="644"
  factor_symbols='["000403.SZ","600489.SH","300059.SZ"]'
  financial_symbols='["000403.SZ","600489.SH","300059.SZ"]'
fi

printf '{
  "checked_at":"%s",
  "symbols":["000403.SZ","600489.SH","300059.SZ"],
  "instruments":[
    {"symbol":"000403.SZ","name":"派林生物"},
    {"symbol":"600489.SH","name":"中金黄金"},
    {"symbol":"300059.SZ","name":"东方财富"}
  ],
  "raw_daily":[
    {"symbol":"000403.SZ","latest_date":"%s","row_count":1},
    {"symbol":"600489.SH","latest_date":"%s","row_count":1},
    {"symbol":"300059.SZ","latest_date":"%s","row_count":1}
  ],
  "enriched_daily":[
    {"symbol":"000403.SZ","latest_date":"%s","row_count":1},
    {"symbol":"600489.SH","latest_date":"%s","row_count":1},
    {"symbol":"300059.SZ","latest_date":"%s","row_count":1}
  ],
  "adjustment_factor_files":3,
  "adjustment_factor_symbols":%s,
  "financial_files":3,
  "financial_symbols":%s,
  "user_data_permissions":{
    "preferences.json":{"present":true,"mode":"%s"},
    "auth.json":{"present":true,"mode":"600"},
    "secrets.json":{"present":true,"mode":"600"}
  },
  "secret_values_read":false,
  "external_requests_made":false
}\n' \
  "$today" \
  "$today" "$today" "$today" \
  "$today" "$today" "$today" \
  "$factor_symbols" "$financial_symbols" "$preference_mode"
FAKE_SSH
chmod +x "$fixture_dir/ssh"

run_blocked_case() {
  local fixture="$1"
  local expected="$2"
  local output
  local status

  set +e
  output="$(
    PATH="$fixture_dir:$PATH" \
      READINESS_FIXTURE="$fixture" \
      TICKFLOW_SSH_HOST=fake \
      TICKFLOW_SSH_DIRECT=0 \
      "$project_root/scripts/verify_real_data_readiness.sh" 2>&1
  )"
  status=$?
  set -e

  [[ "$status" -eq 2 ]]
  grep -Fq '"status": "REAL_DATA_READINESS_BLOCKED"' <<<"$output"
  grep -Fq "$expected" <<<"$output"
}

run_blocked_case unsafe_preferences "UNSAFE_FILE_MODE:preferences.json:644"
run_blocked_case incomplete_sample "ADJUSTMENT_FACTOR_SAMPLE_INCOMPLETE"
run_blocked_case incomplete_sample "FINANCIAL_SAMPLE_INCOMPLETE"

printf 'REAL_DATA_READINESS_FIXTURES_OK\n'
