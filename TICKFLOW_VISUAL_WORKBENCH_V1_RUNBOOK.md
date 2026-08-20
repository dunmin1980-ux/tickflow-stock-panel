# TickFlow Visual Workbench v1 Runbook

## Current status

`DELIVERY_SPRINT_BLOCKED_BY_P0_P1`

The browser workbench is implemented and safe for the released frozen
`000403.SZ` reference workflow. It is not a released daily product because the
current Option C contract cannot represent a new validated real-market daily
input. Do not treat a deterministic test fixture as daily research evidence.

## Start

Double-click:

```text
TickFlow Visual Workbench.command
```

Or run from the repository root:

```bash
./scripts/start_tickflow_visual.sh
```

The launcher binds only `127.0.0.1:3018`, builds the frontend when needed,
starts FastAPI, waits for `/health`, and opens:

```text
http://127.0.0.1:3018/paper-trading
```

It reuses a healthy server and never kills a process that already owns the
port. Set a different local port only when required:

```bash
TICKFLOW_VISUAL_PORT=3020 ./scripts/start_tickflow_visual.sh
```

## What works

- Account KPIs, positions, decisions, trades, PnL, drawdown, and equity curve.
- Research Signal, Paper Action, and deterministic Claims status.
- ChenQuant Daily JSON/Markdown rendering.
- Frozen reference account initialization and idempotent re-open.
- Refresh/restart recovery from immutable Option C state.
- Friendly fail-closed errors and double-click protection.
- `SIMULATION ONLY`, `can_publish=false`, and `REAL TRADING DISABLED`.

## State and inputs

The startup script uses:

```text
backend/data/user_data/phase2_option_c_paper/reference_account/
backend/data/user_data/phase2_option_c_paper/inputs/
```

A staged filename is:

```text
YYYY-MM-DD_input.json
```

It must pass the existing strict `ContinuousDayInput` contract. The Visual
layer does not fetch prices, generate Claims, alter the input, or guess a
missing next-day open.

## Current P1 blocker

The released schema currently has only:

```text
DETERMINISTIC_REFERENCE_FIXTURE
DETERMINISTIC_TEST_FIXTURE
```

The reference is fixed to `2026-07-31`; the test fixture must not be presented
as real daily evidence. A follow-up scope must add an approved validated-daily
input contract and deterministic daily Facts/Claims preparation before this
workbench can be released for actual daily use.

## Safety

- No Ark or OpenAI call.
- No broker or real-trading route.
- No cloud deployment.
- No three-symbol batch.
- No automatic publishing.
