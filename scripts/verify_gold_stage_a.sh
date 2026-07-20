#!/usr/bin/env bash
# Gold Stage A hardening verifier (no real API keys; fail-closed).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== HEAD ==="
git rev-parse HEAD
git status -sb

echo "=== compose bind ==="
if ! docker compose config 2>/dev/null | grep -q '127.0.0.1:.*3018:3018'; then
  # offline / no docker: fall back to file check
  if ! grep -q '127.0.0.1:${PORT:-3018}:3018' docker-compose.yml; then
    echo "FAIL: 3018 not bound to 127.0.0.1"
    echo "STAGE_A_BLOCKED"
    exit 2
  fi
  echo "OK: docker-compose.yml loopback bind (compose binary skipped)"
else
  echo "OK: docker compose config shows 127.0.0.1 bind"
fi

echo "=== security pytest ==="
PY="${PYTHON:-python3}"
if [[ -x "/Users/macbookpro/Documents/TradingView 量化/tickflow-stock-panel-v0.1.84/backend/.venv/bin/python" ]]; then
  PY="/Users/macbookpro/Documents/TradingView 量化/tickflow-stock-panel-v0.1.84/backend/.venv/bin/python"
fi
(
  cd backend
  PYTHONPATH=. "$PY" -m pytest -q \
    tests/test_gold_path_security.py \
    tests/test_gold_runtime_lock.py \
    tests/test_gold_tickflow_errors.py \
    tests/test_gold_sdk_http_budget.py \
    tests/test_rate_limit_safety_budget.py
)

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
