# TickFlow Visual Workbench v1 Runbook

## Status

`TICKFLOW_VISUAL_WORKBENCH_V1_RELEASED`

This is a local, single-symbol A-share research and Paper Trading workbench for
`000403.SZ` 派林生物. It is always `SIMULATION ONLY`; real trading and broker
connectivity are disabled.

## Start

Double-click:

```text
TickFlow Visual Workbench.command
```

Or run from the repository root:

```bash
./scripts/start_tickflow_visual.sh
```

The launcher installs the locked stock-sdk bridge dependency when needed,
builds the frontend when sources changed, binds only `127.0.0.1:3018`, waits
for health, and opens:

```text
http://127.0.0.1:3018/paper-trading
```

It reuses a healthy server and never kills an unrelated process. To use a
different local port:

```bash
TICKFLOW_VISUAL_PORT=3020 ./scripts/start_tickflow_visual.sh
```

## Runtime Status

Visual v1 uses the released deterministic Option C engine:

```text
Paper Trading Engine=ACTIVE
AI Provider=DEFERRED
Real Trading=DISABLED
```

The Settings page displays these boundaries and does not expose the historical
TickFlow or AI API Key forms. Those legacy forms write to a local
`backend/data/user_data/secrets.json`, but Visual v1 does not read that file for
Paper Trading and does not require an Ark, OpenAI, or other model credential.

The Visual launcher sets `VISUAL_WORKBENCH_PROVIDER_DEFERRED=true`. In this
mode the backend uses an empty capability set and does not start Provider
capability probes, market synchronization schedules, external data pulls,
notification transports, or financial synchronization.

Do not configure a real Provider for daily Visual v1 use.

## Backend Availability

The launcher must keep `127.0.0.1:3018` healthy. If the PWA shell remains
visible after the backend stops, the application enters read-only mode and
shows:

```text
TickFlow 后端未运行，当前为只读模式
```

Run `./scripts/start_tickflow_visual.sh` again. The launcher reuses a healthy
process or starts the local backend, waits for `/health`, and then opens the
Paper Trading page. Do not attempt to repair this state by entering an API Key.

## Daily SOP

1. Start the workbench after the A-share close. The preparation gate opens at
   `15:10 Asia/Shanghai`.
2. Confirm the page shows the intended date and `SIMULATION ONLY / REAL TRADING
   DISABLED`.
3. Click `运行今日模拟盘` once.
4. Wait for `本次已写入日结`.
5. Review Research Signal, Paper Action, Claims date, account KPIs, positions,
   decisions, trades, equity/PnL, and ChenQuant Daily.

Before 15:10 the button is disabled. A stale market date, unavailable source,
invalid raw/qfq relationship, incomplete calendar, duplicate run, or tampered
artifact fails closed without changing the account.

If an open position crosses a material raw/qfq adjustment-factor change, the
run stops with an enterprise-action review message. v1 does not guess dividend,
split, rights, or share-count adjustments, so it cannot publish a false PnL.

## What One Click Does

For the fixed symbol only, the backend serially obtains:

1. raw daily history;
2. qfq daily history;
3. the A-share trading calendar.

It writes a strict `VALIDATED_DAILY_INPUT` bundle, computes deterministic
indicators, Facts, Projection, Typed Claims and Research Signal, then passes the
canonical input to the released Option C runner. It does not call an AI model.

Signals use same-basis qfq Typed Claims. Raw prices are used for valuation and
next-trading-day-open execution. Volume and amount units remain explicitly
marked `VENDOR_CONFIRMATION_PENDING` and are not used to create the action.

## Account Semantics

- A BUY or SELL decision is queued on the decision date.
- Execution requires the next available trading day's raw opening price.
- A-share T+1 remains enabled; same-day sell is rejected.
- BUY quantity uses 100-share lots.
- Cash, fees, cost basis and PnL use Decimal accounting.
- HOLD is a valid result and creates no order or trade.
- Repeating an already published input is idempotent.

## State and Output

Runtime data is Git-ignored under:

```text
backend/data/user_data/phase2_option_c_paper/
├── inputs/YYYY-MM-DD/
│   ├── raw_daily.json
│   ├── qfq_daily.json
│   ├── facts.json
│   ├── projection.json
│   ├── claims.json
│   ├── claims_validation.json
│   ├── input.json
│   └── manifest.json
└── reference_account/
    ├── current_state.json
    └── days/YYYY-MM-DD/
        ├── chenquant_daily.json
        ├── chenquant_daily.md
        ├── decision_ledger.json
        ├── trade_ledger.json
        ├── position_lots.json
        └── equity_history.json
```

The browser reads these persisted Option C artifacts through the authenticated
thin API. Refreshing or restarting does not recompute ledger values in the UI.

## Safety Boundary

- Symbol: `000403.SZ` only.
- AI Provider calls: `0`.
- Broker and real trading: disabled.
- Three-symbol batch: not released.
- Cloud deployment: not performed.
- Automatic publishing: disabled.
- `can_publish=false` and `trading_advice=false` remain fixed.

Do not delete or edit `current_state.json`, day directories, or validated input
bundles to change an outcome. Archive a disposable test account instead of
rewriting history.
