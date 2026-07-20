#!/usr/bin/env bash
# Gold Stage A hardening verifier (no real API keys; fail-closed).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-$ROOT/backend/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  echo "FAIL: workspace Python environment unavailable: $PY"
  echo "STAGE_A_BLOCKED"
  exit 2
fi

echo "=== HEAD ==="
git rev-parse HEAD
git status -sb

echo "=== compose bind ==="
if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker is unavailable"
  echo "STAGE_A_BLOCKED"
  exit 2
fi
if ! docker compose config --quiet >/dev/null 2>&1; then
  echo "FAIL: docker compose config is invalid"
  echo "STAGE_A_BLOCKED"
  exit 2
fi
COMPOSE_JSON="$(docker compose config --format json 2>/dev/null)" || {
  echo "FAIL: docker compose JSON config is unavailable"
  echo "STAGE_A_BLOCKED"
  exit 2
}
if ! printf '%s' "$COMPOSE_JSON" | "$PY" -c '
import json, sys
config = json.load(sys.stdin)
ports = config.get("services", {}).get("app", {}).get("ports", [])
valid = [
    port for port in ports
    if port.get("target") == 3018 and port.get("host_ip") == "127.0.0.1"
]
raise SystemExit(0 if len(valid) == 1 else 1)
'; then
  unset COMPOSE_JSON
  echo "FAIL: resolved app port 3018 is not bound exactly once to 127.0.0.1"
  echo "STAGE_A_BLOCKED"
  exit 2
fi
unset COMPOSE_JSON
echo "OK: resolved compose config binds app target 3018 to 127.0.0.1"

echo "=== security pytest ==="
if ! (
  cd backend
  PYTHONPATH=. "$PY" -m pytest -q \
    tests/test_gold_path_security.py \
    tests/test_gold_runtime_lock.py \
    tests/test_gold_tickflow_errors.py \
    tests/test_gold_sdk_http_budget.py \
    tests/test_rate_limit_safety_budget.py
); then
  echo "FAIL: Stage A security pytest failed"
  echo "STAGE_A_BLOCKED"
  exit 5
fi

echo "=== secret scan (heuristic) ==="
# Ignore docs/examples that only show empty or placeholder KEY=... patterns.
HITS="$(rg -n "TICKFLOW_API_KEY=[^[:space:]\"']+|TELEGRAM_BOT_TOKEN=[^[:space:]\"']+|BEGIN (RSA |OPENSSH )?PRIVATE KEY|Authorization: Bearer [A-Za-z0-9._-]{12,}" \
  . -g '!*.lock' -g '!reports/**' -g '!.git/**' -g '!**/node_modules/**' -g '!**/.venv/**' -g '!scripts/verify_gold_stage_a.sh' \
  || true)"
HITS="$(printf '%s\n' "$HITS" | rg -v 'TICKFLOW_API_KEY=\.\.\.|TICKFLOW_API_KEY=$|TELEGRAM_BOT_TOKEN=$|example|placeholder|YOUR_|你的|redact|\*\*\*|留空' || true)"
if [[ -n "${HITS// }" ]]; then
  printf '%s\n' "$HITS"
  echo "FAIL: possible secret material"
  echo "STAGE_A_BLOCKED"
  exit 3
fi
echo "OK: no obvious live secrets in tree"

echo "=== docs present ==="
for f in \
  docs/tickflow-request-path-audit.md \
  docs/gold-stage-a-runbook.md \
  docs/tickflow-sdk-http-budget.md
do
  [[ -f "$f" ]] || { echo "FAIL missing $f"; echo "STAGE_A_BLOCKED"; exit 4; }
done
echo "OK: Stage A docs present"

# Conservative: READY only when all above passed in this script.
echo "STAGE_A_READY"
exit 0
