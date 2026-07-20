# TickFlow request-path audit (Stage A)

Audit of TickFlow SDK call sites that can consume the **same account API quota**. Paths and APIs verified against the tree under this workspace.

## Stage A throttle facts (read first)

| Fact | Meaning |
|---|---|
| Process-local only | `backend/app/tickflow/rate_limits.py` (`resolve_limit`, `sleep_between_batches`, `SAFETY_RPM_FACTOR=0.8`) paces **one Python process**. |
| Not cross-container RPM | Gold container and A-share panel do **not** share slot tables. Two processes each at 80% can aggregate toward ~160% of package RPM. |
| Same account quota | Every path below that uses the configured TickFlow Key hits the **same vendor account RPM**, regardless of process-local pacing. |
| Gold gateway | `GoldTickFlowGateway` paces every call with `resolve_limit` + `sleep_between_batches` (single-symbol quote / daily history). |
| Concurrent Gold pipelines | **Forbidden** in Stage A (no parallel Gold samplers / writers on one data root). |
| Probe + Gold sync | **Forbidden** together: do not run live `probe_tickflow_pro.py` while Gold sampler is active. |
| First-batch burst | `sleep_between_batches(index=0)` reserves a slot but does not sleep; concurrent `index=0` callers can still burst. |

See also: `docs/tickflow-pro-shared-rate-limit.md`, `docs/tickflow-sdk-http-budget.md`.

## Call-site matrix

| file | function/area | SDK API | uses shared rate_limits (yes/no/partial) | concurrent? | cron? | API-triggered? | same account quota? | Stage A notes |
|---|---|---|---|---|---|---|---|---|
| `backend/app/services/gold_tickflow.py` | `GoldTickFlowGateway.get_quote` / `get_completed_closes` / `_pace` | `quotes.get(symbols=[600489.SH])`; `klines.batch([600489.SH], period=1d, …)` | **yes** (`resolve_limit` + `sleep_between_batches` on every call) | no (single-symbol sequential gateway) | yes (Gold 5‑min sampler when `GOLD_WORKSPACE_ENABLED`) | yes (health/sample paths via Gold APIs) | yes | Preferred Stage A Gold path; 429 circuit in `gold_tickflow_errors.RateLimitCircuit` (first halt pipeline, second trip). |
| `backend/app/services/kline_sync.py` | daily / adj / minute sync helpers | `klines.batch`, `klines.ex_factors`, minute batch paths via `get_client()` | **yes** (`resolve_limit`, `chunked`, `sleep_between_batches`) | no within one sync loop; can overlap other jobs if operators start parallel work | yes (`daily_pipeline` scheduler) | yes (`/api/kline/sync_*`, related endpoints) | yes | Heavy A-share consumer; prefer **after 16:00** when Gold is in session. |
| `backend/app/services/index_sync.py` | index/ETF instruments + daily/adj | `quotes.get_by_universes` (instruments); `klines.batch` / adj paths | **yes** | no (batched loops) | yes (pipeline) | yes (`/api/indices/sync_*`) | yes | Same process-local limiter as kline; still account-shared with Gold. |
| `backend/app/services/quote_service.py` | realtime poll loop; on-demand symbol pulls | `quotes.get_by_universes`; `quotes.get` via `get_paid_realtime_client()` | **no** (interval clamp only; no `resolve_limit` / `sleep_between_batches` on TickFlow pulls) | background poll thread; `ThreadPoolExecutor` used for **Feishu webhook** dispatch only (not TickFlow) | yes (in-process interval poll) | yes (SSE / watchlist refresh consumers) | yes | High steady load if full-market universes enabled; Stage A: avoid overlapping with Gold + live probe. |
| `backend/app/services/watchlist.py` | `fetch_quotes` | `quotes.get(symbols=chunk, as_dataframe=True)` | **partial** (`resolve_limit` for **batch size only**; no `sleep_between_batches`) | **FLAG:** `ThreadPoolExecutor(max_workers=1)` wraps each batch for timeout — not multi-worker fan-out, but executor pattern + API triggers can still overlap other TickFlow work | no | yes (watchlist / enriched quote paths) | yes | Flag for ops: do not treat as rate-limited pacing; keep off concurrent Gold/probe windows. |
| `backend/app/tickflow/pools.py` | universe resolve + pool constituents | `universes.list`; `quotes.get_by_universes` | **no** | no | indirect (pipeline/universe refresh) | yes (pool consumers / settings) | yes | Unpaced constituent pulls; cache where possible. |
| `backend/app/tickflow/policy.py` | capability probe (`detect_*` / live method probes) | `quotes.get` / `get_by_symbols` / `get_by_universes`; `klines.get` / `batch` / `intraday*`; `depth.get` / `batch`; `financials.metrics`; `klines.ex_factors` | **no** | no (sequential probes) | no | yes (settings “detect capabilities”) | yes | Many SDK methods per detection; do not run while Gold sampler or live Pro probe is active. |
| `backend/scripts/probe_tickflow_pro.py` | dry-run / `--live` smoke | live: `klines.batch`, `quotes.get` (golden symbols) | **partial** (imports `SAFETY_RPM_FACTOR` / `apply_safety_rpm` for report math only; **does not** call `sleep_between_batches`) | no | no | CLI only | yes | **Do not run `--live` while Gold sampler runs**; prefer after 16:00 (see phase1 probe doc). |
| `backend/app/data_providers/tickflow_provider.py` | provider adapter | `exchanges.get_instruments`; `klines.batch`; `klines.ex_factors`; `quotes.get_by_universes` / `quotes.get` | **no** | no | indirect | yes (provider registry consumers) | yes | Thin SDK wrapper; callers must supply their own pacing. |
| `backend/app/services/financial_sync.py` | `sync_*` financial tables | `financials.metrics` / `income` / `balance_sheet` / `cash_flow` / `shares` | **no** (fixed `_BATCH_SIZE` loop, no shared limiter) | background `financial-sync` thread possible | yes (`FinancialScheduler`) | yes (`/api/financials/sync/*`) | yes | Expert/financial tier; Stage A keep off Gold peak windows. |
| `backend/app/services/instrument_sync.py` | `sync_instruments` | `exchanges.get_instruments(SH/SZ/BJ, stock)` | **no** | no | yes (instruments cron in `daily_pipeline`) | yes (pipeline / related APIs) | yes | Low volume vs kline/quote; still same account. |
| `backend/app/services/depth_service.py` | sealed depth poll / finalize | `depth.batch` | **yes** (`resolve_limit` + `sleep_between_batches`; interval clamp aligned to `SAFETY_RPM_FACTOR`) | background poll thread + finalize job | yes (depth finalize cron + in-session poll) | yes (manual `run_once` / settings) | yes | Can run in parallel with quote poll inside one app process — Stage A: avoid stacking with Gold + probe. |
| `backend/app/data_providers/custom/provider.py` | custom HTTP datasets | **not TickFlow SDK** (custom source HTTP) | **partial** (reuses `chunked` + `sleep_between_batches` with **custom** `cfg.rpm`) | no | depends on caller | depends on caller | **no** (unless custom source is independently TickFlow — default is not) | Does not burn TickFlow account quota by default; still shares process-local slot table keys by rpm value. |
| `backend/app/jobs/daily_pipeline.py` | orchestrator (not a direct SDK client) | delegates to instrument/kline/index/depth jobs | n/a (indirect) | run-slot lock reduces overlap of heavy jobs | yes | yes (manual pipeline triggers) | yes (via callees) | Stage A: schedule A-share bulk after 16:00 when Gold observation is active. |

## Summary for Stage A operators

1. **Throttling is process-local.** Do not claim account-wide 80% RPM across containers.
2. **Gold** is correctly wired to shared helpers (`resolve_limit` + `sleep_between_batches`) and must remain single-pipeline.
3. **Highest risk overlaps:** `quote_service` (unpaced universes), `watchlist.fetch_quotes` (partial limiter + executor), `policy` detect, live `probe_tickflow_pro`, and large `kline_sync` / `financial_sync` batches.
4. **Ops rule:** one Gold writer (`runtime.lock`); no concurrent Gold pipelines; no live probe + Gold sync together; A-share Pro bulk preferably after **16:00 Asia/Shanghai**.
