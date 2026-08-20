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
- Fresh inputs are generated only by the single-symbol validated daily builder
  from serial raw/qfq history and trading-calendar reads. Missing, stale,
  inconsistent, or re-signed-but-nonderivable inputs fail closed; the
  workbench never guesses prices or falls back to a same-day close.
- Signals are derived only from same-basis qfq Typed Claims. Raw prices remain
  authoritative for valuation and next-trading-day-open execution.
- An open position encountering a material raw/qfq adjustment-factor change
  fails closed before ledger mutation because corporate-action accounting is
  outside v1.
- A new account may be initialized once from the released frozen reference
  fixture. The UI labels its effective data date and never presents it as
  current-day market data.

## Architecture

### Backend thin layer

`Phase2VisualWorkbenchService` owns no trading rules. It:

1. loads the latest state through `OptionCStateStore`;
2. prepares or reloads a strict `VALIDATED_DAILY_INPUT` bundle;
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

1. released frozen reference input only for its exact reference date;
2. an already published strict daily bundle for the requested date;
3. after `15:10 Asia/Shanghai`, one serial raw/qfq/calendar preparation for the
   current date;
4. a stable fail-closed error with no account mutation.

The builder writes a fixed artifact set atomically. Every load rechecks source
hashes and deterministically reconstructs Facts, Projection, Claims,
validation, Signal, valuation, and execution-price bindings. The legacy
`YYYY-MM-DD_input.json` compatibility path is intentionally not accepted by the
Visual workbench.

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
