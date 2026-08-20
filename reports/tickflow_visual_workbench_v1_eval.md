# TickFlow Visual Workbench v1 Delivery Evaluation

## Final status

```text
TICKFLOW_VISUAL_WORKBENCH_V1_RELEASED
```

Validated implementation Head:

```text
443858e47c14ab118c486c6adac80f991a1f7c81
```

## Delivered product

Visual Workbench v1 turns the released Option C single-symbol Paper Trading
engine into a local browser workflow for `000403.SZ` 派林生物. The root route opens
the workbench and displays persisted account KPIs, positions, decisions,
trades, equity/PnL, Research Signal, Paper Action, Claims validation and
ChenQuant Daily.

The `运行今日模拟盘` command performs one guarded workflow:

```text
raw/qfq daily history + A-share calendar
-> deterministic Facts / Projection / Typed Claims / Signal
-> released Option C Daily Runner
-> atomic account, ledger, PnL and ChenQuant Daily persistence
```

The page and every persisted artifact remain `SIMULATION ONLY`,
`REAL TRADING DISABLED`, `can_publish=false` and `trading_advice=false`.

## P0/P1 safety closeout

- Daily preparation is current-date only and opens after 15:10
  `Asia/Shanghai`.
- Market requests are serial and fixed to `000403.SZ`; no batch, AI, broker,
  Gold or cloud path is involved.
- raw data supplies valuation and next-trading-day-open execution; qfq data
  supplies technical indicators and signal Claims.
- Every saved bundle has a fixed artifact set and is reconstructed from raw/qfq
  evidence on load. Re-signed input, valuation, signal, projection or Claims
  tampering fails before account mutation.
- The legacy `YYYY-MM-DD_input.json` compatibility path is not accepted by the
  Visual workbench.
- An open position crossing a material raw/qfq adjustment-factor change fails
  closed. v1 does not invent dividend, split, rights or share-count accounting.
- Option C remains authoritative for A-share T+1, next-day-open execution,
  Decimal accounting, fees, idempotency, state recovery, PnL and drawdown.
- Preparation and account mutation use independent cross-process locks;
  duplicate clicks do not duplicate requests, decisions, orders or trades.

## Acceptance evidence

| Gate | Result |
|---|---|
| Dashboard and one-click Daily Run | PASSED |
| Validated daily Input Builder | PASSED |
| Account, positions, decisions and trades | PASSED |
| PnL, equity chart and signal timeline | PASSED |
| ChenQuant Daily JSON/Markdown | PASSED |
| T+1 and next-day-open | PASSED |
| Look-ahead and future-data guards | PASSED |
| Idempotency and state recovery | PASSED |
| Refresh persistence and friendly errors | PASSED |
| Double-click protection | PASSED |
| Local startup and browser open | PASSED |
| Focused backend regression | `161 passed` |
| Frontend unit tests | `104 passed` |
| TypeScript and production build | PASSED |
| Browser E2E: iPhone, Pixel, desktop | `28 passed, 2 skipped` |
| compileall / Ruff F821 / strict changed-file Ruff | PASSED |
| Node/Bash syntax, diff check, sensitive scan | PASSED |
| Independent P0/P1 review | `NO_P0_P1_FINDINGS` |

## Full backend context

The final current-tree full run completed with:

```text
2907 passed, 58 failed, 13 warnings
```

All 58 failures remain in the pre-existing frozen Ark approval/provenance, Ark
TLS/canary, Gold comparison/store and runtime-head-bound evidence tests. The
Visual, Option C, Claims, Paper Trading and startup suites have no failures.
These unrelated paths were not changed or reopened during the sprint.

## Production data smoke

The stock-sdk/Tencent bridge returned raw and qfq history for `000403.SZ` with
the same latest date and a valid A-share calendar. A controlled temporary-root
smoke generated valid Claims and a deterministic signal, published once, then
returned the existing result idempotently. It did not mutate the formal account
and did not call any Provider.

## Known limitations

- One symbol only: `000403.SZ`.
- Local Mac deployment only; no cloud release.
- Corporate actions require manual review and fail closed while a position is
  open.
- volume and amount units remain vendor-confirmation pending and do not drive
  the action.
- AI, three-symbol mode, broker integration, real trading and automatic
  publishing are not released.
- Existing Ark/Gold full-suite debt is deferred and not part of Visual v1.

## Daily entry

Run from the repository root:

```bash
./scripts/start_tickflow_visual.sh
```

The launcher binds `127.0.0.1:3018`, waits for health and opens:

```text
http://127.0.0.1:3018/paper-trading
```

Operational instructions are in `TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md`.
