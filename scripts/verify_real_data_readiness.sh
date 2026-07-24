#!/usr/bin/env bash
set -euo pipefail

ssh_host="${TICKFLOW_SSH_HOST:-codex-vm}"
container="${TICKFLOW_CONTAINER:-TickFlow_Stock_Panel}"
ssh_command=(ssh -o BatchMode=yes -o ConnectTimeout=10)
if [[ "${TICKFLOW_SSH_DIRECT:-1}" == "1" ]]; then
  ssh_command+=(-o ProxyCommand=none)
fi

fail() {
  printf 'REAL_DATA_READINESS_FAILED: %s\n' "$*" >&2
  exit 1
}

for tool in ssh python3; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing required tool: $tool"
done
[[ "$container" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail "invalid container name"

auth_json="$(
  "${ssh_command[@]}" "$ssh_host" \
    "curl -fsS http://127.0.0.1:3019/api/auth/status"
)" || fail "unable to read the safe authentication status endpoint"

data_json="$(
  "${ssh_command[@]}" "$ssh_host" \
    "docker exec -i '$container' /app/.venv/bin/python -" <<'PY'
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import duckdb

data_dir = Path("/app/data")
symbols = ("000403.SZ", "600489.SH", "300059.SZ")


def _table_rows(pattern: str) -> list[dict[str, object]]:
    parquet_glob = str(data_dir / pattern)
    if not list(data_dir.glob(pattern)):
        return []
    placeholders = ", ".join(repr(symbol) for symbol in symbols)
    rows = duckdb.sql(
        "SELECT symbol, CAST(max(date) AS VARCHAR) AS latest_date, "
        "count(*) AS row_count "
        f"FROM read_parquet('{parquet_glob}', union_by_name=true) "
        f"WHERE symbol IN ({placeholders}) "
        "GROUP BY symbol ORDER BY symbol"
    ).fetchall()
    return [
        {"symbol": symbol, "latest_date": latest_date, "row_count": row_count}
        for symbol, latest_date, row_count in rows
    ]


instrument_glob = str(data_dir / "instruments" / "*.parquet")
instrument_files = list((data_dir / "instruments").glob("*.parquet"))
instruments: list[dict[str, str]] = []
if instrument_files:
    placeholders = ", ".join(repr(symbol) for symbol in symbols)
    rows = duckdb.sql(
        "SELECT symbol, name "
        f"FROM read_parquet('{instrument_glob}', union_by_name=true) "
        f"WHERE symbol IN ({placeholders}) ORDER BY symbol"
    ).fetchall()
    instruments = [{"symbol": symbol, "name": name} for symbol, name in rows]

user_data = data_dir / "user_data"
permission_state = {}
for name in ("preferences.json", "auth.json", "secrets.json"):
    path = user_data / name
    permission_state[name] = {
        "present": path.is_file(),
        "mode": f"{path.stat().st_mode & 0o777:03o}" if path.is_file() else None,
    }

print(json.dumps({
    "checked_at": date.today().isoformat(),
    "symbols": list(symbols),
    "instruments": instruments,
    "raw_daily": _table_rows("kline_daily/**/*.parquet"),
    "enriched_daily": _table_rows("kline_daily_enriched/**/*.parquet"),
    "adjustment_factor_files": len(list((data_dir / "adj_factor").rglob("*.parquet"))),
    "financial_files": len(list((data_dir / "financials").rglob("*.parquet"))),
    "user_data_permissions": permission_state,
    "secret_values_read": False,
    "external_requests_made": False,
}, ensure_ascii=False, sort_keys=True))
PY
)" || fail "unable to inspect mounted market data without network access"

AUTH_JSON="$auth_json" DATA_JSON="$data_json" python3 <<'PY'
from __future__ import annotations

import json
import os
from datetime import date

auth = json.loads(os.environ["AUTH_JSON"])
data = json.loads(os.environ["DATA_JSON"])
expected_symbols = set(data["symbols"])
blockers: list[str] = []

if auth.get("configured") is not True:
    blockers.append("AUTH_USER_ACTION_REQUIRED")

instrument_symbols = {row["symbol"] for row in data["instruments"]}
if instrument_symbols != expected_symbols:
    blockers.append("INSTRUMENT_SAMPLE_INCOMPLETE")

table_maps = {}
for table_name in ("raw_daily", "enriched_daily"):
    rows = {row["symbol"]: row for row in data[table_name]}
    table_maps[table_name] = rows
    if set(rows) != expected_symbols:
        blockers.append(f"{table_name.upper()}_SAMPLE_INCOMPLETE")

if not any(item.endswith("SAMPLE_INCOMPLETE") for item in blockers):
    for symbol in sorted(expected_symbols):
        raw_date = table_maps["raw_daily"][symbol]["latest_date"]
        enriched_date = table_maps["enriched_daily"][symbol]["latest_date"]
        if raw_date != enriched_date:
            blockers.append(f"RAW_ENRICHED_DATE_MISMATCH:{symbol}")
        age_days = (date.today() - date.fromisoformat(raw_date)).days
        if age_days > 7:
            blockers.append(f"DAILY_DATA_STALE:{symbol}:{age_days}d")

if data["adjustment_factor_files"] == 0:
    blockers.append("ADJUSTMENT_FACTOR_DATA_UNAVAILABLE")
if data["financial_files"] == 0:
    blockers.append("FINANCIAL_DATA_UNAVAILABLE")

for name in ("auth.json", "secrets.json"):
    state = data["user_data_permissions"][name]
    if state["present"] and state["mode"] != "600":
        blockers.append(f"UNSAFE_FILE_MODE:{name}:{state['mode']}")

result = {
    "status": "REAL_DATA_READINESS_OK" if not blockers else "REAL_DATA_READINESS_BLOCKED",
    "blockers": blockers,
    "auth": {
        "configured": bool(auth.get("configured")),
        "authenticated": bool(auth.get("authenticated")),
    },
    "data": data,
    "key_presence": "not_read",
    "ai_configuration": "USER_ACTION_REQUIRED",
    "latest_trade_date_requires_manual_calendar_check": True,
}
print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
if blockers:
    raise SystemExit(2)
PY
