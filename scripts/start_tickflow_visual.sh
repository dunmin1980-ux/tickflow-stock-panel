#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="127.0.0.1"
PORT="${TICKFLOW_VISUAL_PORT:-3018}"
URL="http://${HOST}:${PORT}/paper-trading"
HEALTH_URL="http://${HOST}:${PORT}/health"
DATA_DIR="${DATA_DIR:-${ROOT}/backend/data}"
RUNTIME_DIR="${DATA_DIR}/user_data/phase2_option_c_paper/visual_runtime"
PID_FILE="${RUNTIME_DIR}/server.pid"
LOG_FILE="${RUNTIME_DIR}/server.log"
STARTUP_LOCK="${RUNTIME_DIR}/.startup.lock"
STARTUP_LOCK_OWNER="${STARTUP_LOCK}/owner.pid"
PYTHON="${TICKFLOW_VISUAL_PYTHON:-${ROOT}/backend/.venv/bin/python}"
STOCKSDK_BRIDGE_DIR="${TICKFLOW_STOCKSDK_BRIDGE_DIR:-${ROOT}/backend/app/plugins/stocksdk}"
STOCKSDK_PACKAGE="${STOCKSDK_BRIDGE_DIR}/node_modules/stock-sdk/package.json"

health_ready() {
  curl --silent --show-error --fail --max-time 2 "${HEALTH_URL}" >/dev/null 2>&1
}

open_browser() {
  if [[ "${TICKFLOW_VISUAL_SKIP_BROWSER:-0}" == "1" ]]; then
    return
  fi
  if command -v open >/dev/null 2>&1; then
    open "${URL}"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${URL}" >/dev/null 2>&1
  else
    printf 'BROWSER_OPEN_UNAVAILABLE url=%s\n' "${URL}"
  fi
}

if [[ "${1:-}" == "--dry-run" ]]; then
  printf 'host=%s\n' "${HOST}"
  printf 'port=%s\n' "${PORT}"
  printf 'url=%s\n' "${URL}"
  printf 'data_dir=%s\n' "${DATA_DIR}"
  printf 'stocksdk_bridge=%s\n' "${STOCKSDK_BRIDGE_DIR}"
  printf 'gold_workspace_enabled=false\n'
  printf 'real_trading=DISABLED\n'
  exit 0
fi

mkdir -p "${RUNTIME_DIR}"
chmod 700 "${RUNTIME_DIR}"

if health_ready; then
  printf 'ALREADY_RUNNING url=%s\n' "${URL}"
  open_browser
  exit 0
fi

if [[ -d "${STARTUP_LOCK}" && ! -L "${STARTUP_LOCK}" && -f "${STARTUP_LOCK_OWNER}" && ! -L "${STARTUP_LOCK_OWNER}" ]]; then
  lock_owner="$(tr -cd '0-9' < "${STARTUP_LOCK_OWNER}")"
  if [[ -n "${lock_owner}" ]] && ! kill -0 "${lock_owner}" 2>/dev/null; then
    rm -f "${STARTUP_LOCK_OWNER}"
    rmdir "${STARTUP_LOCK}" 2>/dev/null || true
  fi
fi
if ! mkdir "${STARTUP_LOCK}" 2>/dev/null; then
  printf 'STARTUP_IN_PROGRESS url=%s\n' "${URL}" >&2
  exit 1
fi
printf '%s\n' "$$" > "${STARTUP_LOCK_OWNER}"
chmod 600 "${STARTUP_LOCK_OWNER}"
cleanup_lock() {
  rm -f "${STARTUP_LOCK_OWNER}"
  rmdir "${STARTUP_LOCK}" 2>/dev/null || true
}
trap cleanup_lock EXIT INT TERM

if health_ready; then
  printf 'ALREADY_RUNNING url=%s\n' "${URL}"
  open_browser
  exit 0
fi

server_pid=""
if [[ -f "${PID_FILE}" ]]; then
  existing_pid="$(tr -cd '0-9' < "${PID_FILE}")"
  if [[ -n "${existing_pid}" ]] && kill -0 "${existing_pid}" 2>/dev/null; then
    server_pid="${existing_pid}"
    printf 'SERVER_PROCESS_EXISTS pid=%s health=pending\n' "${existing_pid}"
  else
    rm -f "${PID_FILE}"
  fi
fi

if [[ ! -x "${PYTHON}" ]]; then
  printf 'PYTHON_RUNTIME_MISSING path=%s\n' "${PYTHON}" >&2
  exit 1
fi

if [[ ! -f "${STOCKSDK_PACKAGE}" || -L "${STOCKSDK_PACKAGE}" ]]; then
  if [[ -L "${STOCKSDK_BRIDGE_DIR}" || ! -f "${STOCKSDK_BRIDGE_DIR}/package.json" || -L "${STOCKSDK_BRIDGE_DIR}/package.json" || ! -f "${STOCKSDK_BRIDGE_DIR}/package-lock.json" || -L "${STOCKSDK_BRIDGE_DIR}/package-lock.json" ]]; then
    printf 'STOCKSDK_BRIDGE_MANIFEST_INVALID path=%s\n' "${STOCKSDK_BRIDGE_DIR}" >&2
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    printf 'STOCKSDK_NPM_MISSING\n' >&2
    exit 1
  fi
  printf 'INSTALLING_STOCKSDK_BRIDGE\n'
  (
    cd "${STOCKSDK_BRIDGE_DIR}"
    npm ci --ignore-scripts
  )
  if [[ ! -f "${STOCKSDK_PACKAGE}" || -L "${STOCKSDK_PACKAGE}" ]]; then
    printf 'STOCKSDK_BRIDGE_INSTALL_FAILED\n' >&2
    exit 1
  fi
fi

DIST_INDEX="${ROOT}/frontend/dist/index.html"
needs_build=0
if [[ ! -f "${DIST_INDEX}" ]]; then
  needs_build=1
elif find "${ROOT}/frontend/src" "${ROOT}/frontend/public" -type f -newer "${DIST_INDEX}" -print -quit 2>/dev/null | grep -q .; then
  needs_build=1
fi

if [[ "${needs_build}" == "1" ]]; then
  if ! command -v corepack >/dev/null 2>&1; then
    printf 'COREPACK_MISSING\n' >&2
    exit 1
  fi
  printf 'BUILDING_FRONTEND\n'
  (
    cd "${ROOT}/frontend"
    corepack pnpm build
  )
fi

if ! health_ready && [[ -z "${server_pid}" ]]; then
  umask 077
  (
    cd "${ROOT}/backend"
    nohup env \
      DATA_DIR="${DATA_DIR}" \
      HOST="${HOST}" \
      PORT="${PORT}" \
      GOLD_WORKSPACE_ENABLED=false \
      "${PYTHON}" -m uvicorn app.main:app --host "${HOST}" --port "${PORT}" \
      >"${LOG_FILE}" 2>&1 &
    launched_pid=$!
    temporary_pid="${PID_FILE}.tmp-${launched_pid}"
    printf '%s\n' "${launched_pid}" > "${temporary_pid}"
    chmod 600 "${temporary_pid}"
    mv "${temporary_pid}" "${PID_FILE}"
  )
fi

wait_seconds="${TICKFLOW_VISUAL_STARTUP_WAIT_SECONDS:-90}"
deadline=$((SECONDS + wait_seconds))
while (( SECONDS < deadline )); do
  if health_ready; then
    printf 'TICKFLOW_VISUAL_READY url=%s\n' "${URL}"
    open_browser
    exit 0
  fi
  sleep 1
done

printf 'STARTUP_FAILED log=%s\n' "${LOG_FILE}" >&2
exit 1
