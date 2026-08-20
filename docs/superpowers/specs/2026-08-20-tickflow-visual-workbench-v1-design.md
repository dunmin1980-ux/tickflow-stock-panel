# TickFlow Visual Workbench v1 Design

## Status

`VISUAL_ARCHITECTURE_FROZEN`

## Objective

Expose the released single-symbol Option C Paper Trading workflow as a local,
browser-based daily research workbench. The UI is a thin operational surface;
the released engine remains the only authority for T+1, next-day-open
execution, accounting, PnL, drawdown, idempotency, and state recovery.

## Safety Boundary

- Symbol is fixed to `000403.SZ`.
- Every response and screen states `SIMULATION ONLY` and
  `REAL TRADING DISABLED`.
- No provider, broker, real trading, cloud deployment, notification, or
  publishing path is introduced.
- No market value, ledger value, signal, action, fee, or PnL is recomputed in
  the API or frontend.
- Fresh inputs must already be strict `ContinuousDayInput` artifacts. Missing
  inputs fail closed; the workbench never guesses prices or falls back to a
  same-day close.
- A new account may be initialized once from the released frozen reference
  fixture. The UI labels its effective data date and never presents it as
  current-day market data.

## Architecture

### Backend thin layer

`Phase2VisualWorkbenchService` owns no trading rules. It:

1. loads the latest state through `OptionCStateStore`;
2. validates staged daily inputs through `load_day_input`;
3. calls `run_daily_once` exactly once per accepted input;
4. aggregates immutable ledgers, equity history, Daily JSON/Markdown, Claims
   validation state, and input readiness into a browser response;
5. translates known fail-closed engine errors into stable product error codes.

The authenticated endpoints are:

- `GET /api/paper-trading/dashboard`
- `POST /api/paper-trading/run`

The mutation endpoint is synchronous and serialized by the released
cross-process state lock. Duplicate input returns the existing published day.

### Input preparation

Input resolution order:

1. strict staged file for the requested Asia/Shanghai date;
2. released frozen reference input only when the account has no published day;
3. `PAPER_INPUT_MISSING` with no state mutation.

This is a preparation and validation layer, not a market-data downloader.

### Frontend

The root route opens a purpose-built Paper Trading page containing:

- account KPIs;
- signal, action, Claims, and input readiness;
- one guarded daily-run button;
- positions, decisions, trades, equity/PnL chart;
- deterministic ChenQuant Daily Markdown;
- friendly, non-sensitive fail-closed errors.

React Query invalidates the single dashboard query after a run. Refreshes read
the same immutable state from disk.

### Startup

A local-only launcher binds `127.0.0.1`, reuses an already healthy process,
never kills unrelated processes, starts the backend serving the built frontend,
waits for health, and opens `/paper-trading`. Gold remains disabled.

## Acceptance

Backend contracts, frontend component tests, build/typecheck, Playwright UI E2E,
startup boundary tests, Option C focused regression, and an independent P0/P1
review must pass before the release marker is created.
