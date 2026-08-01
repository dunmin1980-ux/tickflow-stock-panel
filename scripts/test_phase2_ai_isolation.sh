#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_ROOT="${REPO_ROOT}/backend"

test_output="$({
  cd "${BACKEND_ROOT}"
  env \
    -u TICKFLOW_API_KEY \
    -u OPENAI_API_KEY \
    -u DEEPSEEK_API_KEY \
    -u ANTHROPIC_API_KEY \
    -u GEMINI_API_KEY \
    PYTHONPATH=. \
    .venv/bin/pytest tests/test_phase2_ai_worker_protocol.py -q
} 2>&1)"

if printf '%s\n' "${test_output}" | grep -Eiq \
  'Authorization:|Bearer [A-Za-z0-9._~+/=-]{8,}|Cookie:|tf_session=|api[_-]?key[[:space:]]*[:=]|BEGIN .*PRIVATE KEY'; then
  printf '%s\n' 'ISOLATION_HARNESS_SENSITIVE_OUTPUT_BLOCKED' >&2
  exit 2
fi

printf '%s\n' "${test_output}"
printf '%s\n' '{"ai_call_count":0,"external_send_count":0,"isolation_design":"ISOLATION_DESIGN_READY","isolation_runtime":"ISOLATION_RUNTIME_NOT_YET_VERIFIED","provider_attempt_count":0,"tickflow_api_request_count":0}'
