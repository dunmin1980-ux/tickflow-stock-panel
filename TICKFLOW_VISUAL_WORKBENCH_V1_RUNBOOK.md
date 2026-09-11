# TickFlow Visual Workbench v1 Runbook

## Status

`TICKFLOW_VISUAL_WORKBENCH_V1_RELEASED`

`TICKFLOW_VISUAL_PRODUCT_CLEANUP_RELEASED`

`TICKFLOW_WORKBENCH_ACCOUNT_UX_SPLIT_RELEASED`

`TICKFLOW_STRATEGY_GRAPHICS_V1_RELEASED`

`TICKFLOW_STRATEGY_OVERLAY_V2_RELEASED`

`TICKFLOW_STRATEGY_VISUALIZATION_V2_1_RELEASED`

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

## Daily Navigation

The default route opens `/paper-trading`. The desktop primary navigation is
limited to the six daily-use areas below; Settings remains at the bottom of the
sidebar:

```text
今日工作台 -> /paper-trading
模拟盘     -> /paper-account
个股研究   -> /stock-research
市场       -> /indices
监控       -> /monitor
数据       -> /data
设置       -> /settings
```

The mobile navigation exposes the same daily areas in compact form. Historical
watchlist, screener, backtest, ladder, concept, industry, financial, Provider,
realtime and AI configuration entries are not primary navigation items. Their
underlying legacy routes are preserved where applicable.

The top status bar describes the actual runtime as `本地模式`, reports backend
and data-date status, and always shows `SIMULATION ONLY`. It does not represent
the local process as a cloud deployment.

`/paper-trading` is the Today Workbench. It answers what to do today: market
and input readiness, Research Signal, Paper Action, deterministic tasks, today's
position/PnL, ChenQuant Daily summary and the recent signal timeline. It is the
only page with the `运行今日模拟盘` button.

`/paper-account` is the Paper Account. It answers how the account has performed
over its lifetime: account KPIs, position lots and T+1 sellable quantity,
realized/unrealized PnL, equity curve, trades and decisions. It has no run
button. Both pages read the same persisted Option C dashboard and account state;
the split does not create a second ledger or calculation path.

## Capability Labels

Visual v1 labels capabilities by their actual runtime state, not by historical
subscription tiers:

```text
Paper Trading Engine=ACTIVE
AI Provider=DEFERRED
Real Trading=DISABLED
Minute K=DISABLED
Financial Data=NOT CONNECTED
Realtime Quote Provider=DEFERRED
```

Before the Data page starts its daily pipeline, Visual v1 persists minute sync
as disabled. This prevents a historical `minute_sync_enabled=true` preference
from silently reactivating minute requests behind the `DISABLED` label.

`/review` presents the persisted deterministic ChenQuant Daily output and does
not expose model generation. `/financials` is retained as a compatibility route
that reports `NOT CONNECTED`. The Data page keeps its useful local data status
and operations while hiding historical API Key and Provider configuration UX.

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
4. Wait for `今日模拟盘已完成`.
5. On `/paper-trading`, review today's status, Research Signal, Paper Action,
   tasks, position/PnL and ChenQuant Daily summary.
6. Open `/paper-account` to review account KPIs, positions, decisions, trades,
   equity curve and lifecycle PnL.

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

## Daily Research Panel

`/paper-trading` now shows six independent daily research rules for `000403.SZ`:
MACD golden cross with volume, bullish MA alignment, BOLL upper breakout,
volume/price breakout, low-volume MA20 pullback, and BOLL lower-band reclaim.
The first five reuse the builtin matrix strategies and their default parameters.
The sixth is explicitly defined as prior close at/below the lower band, current
close above the band, and current close above prior close.
The seventh row is `CHAN_DAILY_STRUCTURE_V1`: QFQ daily fractals, candidate
strokes, confirmed structure and recent structural levels. This is a bounded
deterministic engineering interpretation, not complete Chan theory. Central
zones, full strokes/segments, divergence and multitimeframe analysis are deferred.

Each rule shows actual indicator values, required thresholds, condition results,
and a comparison with the prior trading day from the same qfq snapshot. A match
is an observation, not an order. A risk flag reuses the builtin exit condition;
it is not a portfolio risk estimate. Insufficient or invalid data is shown
separately from an unmet condition. All six rules are displayed even when
none match.

The panel reads the existing validated daily bundle locally, checks its hashes,
and makes no new data or AI request. Price conditions use qfq; relative volume
uses the same-source raw volume divided by the prior five bars' mean volume.
Absolute volume/amount units remain vendor-pending. Older research is explicitly
date-labelled. Display values are rounded; predicates retain builtin precision.

`HOLD` now distinguishes an empty account waiting from an unchanged position and
shows the existing MACD+RSI6 evidence. Multi-strategy research does not change
the released Paper Trading rule, ledger, or ChenQuant Daily calculation.

After the normal daily run, inspect the research panel. No additional run or
API Key is needed. During development, frontend and backend changes require a
build and restart of the identified local process; the normal launcher reuses
an already healthy server.

### Research Engine v1 Daily Use

1. Double-click the existing desktop `TickFlow.app`; open Today Workbench.
2. Check the displayed research date and data status. If today's input is missing,
   use the existing daily workflow; do not interpret an older labelled snapshot
   as current research. Opening research never requests new market data.
3. Read the research consensus and **separate** Paper Action. `FLAT_WAIT` means
   no current position; `POSITION_HOLD` means an unchanged position. A missing
   published daily is `NOT_RUN`, not HOLD. Corrupt publication evidence blocks
   research readback rather than fabricating account context.
4. Inspect supporting/limiting factors, conflicts, the closest rule and its
   measured gap, yesterday's changes, and the next observation conditions.
5. Expand any of the seven matrix rows for actual values, thresholds, dated
   fractals and candidate strokes. Confirmed structure and current price location
   are separate: a previously confirmed range can coexist with a new close below it.
6. Download JSON or Markdown from the matrix toolbar. These are local research
   exports, not ChenQuant account replacements or automatic Obsidian publication.

Technical prices are QFQ throughout. Condition completion is not a probability.
Gaps retain their units and are never added across incompatible units. Strict
threshold equality remains unmet even when a displayed numeric gap is zero.
Confidence measures rule-evidence agreement, not prediction accuracy. Research
consensus follows explicit MACD/MA direction and Chan/conflict rules, not majority
voting into a trade action.

Optional offline export from the project root, using already validated inputs:

```bash
backend/.venv/bin/python scripts/export_research_daily.py --date YYYY-MM-DD
```

Each export goes to `reports/research_engine_v1/YYYY-MM-DD/research_daily.json`
and `research_daily.md`. The exporter checks three identical runs and writes
`replay_verification.json` for the selected dates. Existing account/input files
are read only. No automatic data fetch, AI request or new simulated trade occurs.
The release's twelve-date evidence covers 2026-08-25 through 2026-09-09; later
research exports do not imply those earlier results used later market data.

Release evidence: `reports/tickflow_research_engine_v1_eval.md`.
Bounded structure definition: `docs/research-chan-daily-structure-v1.md`.

## Strategy Graphics v1

- Today Workbench and `/stock-research` share the same dashboard response:
  research summary, six strategy cards, condition progress, QFQ close/MA/BOLL,
  MACD and RSI6. The stock research page is read-only and has no daily-run button.
- Check **research date** first. The cards show the current snapshot's actual
  conditions, not a fixed demo. The closest card is not necessarily near-trigger:
  it can be only 2/4 when no strategy is close enough. Progress is not a probability.
- Expand a card to compare each observed value with its original threshold.
  A historical condition cannot be satisfied by changing today's value. Missing
  evidence remains unknown, not an unmet zero.
- Charts show the last 30 available trading days. Indicators warm up on the full
  validated history first; unavailable early MA60 points stay blank. All plotted
  prices are QFQ, not raw execution or ledger prices.
- Trigger/near-trigger markers are retrospective evaluations of each dated
  prefix of the **same current QFQ snapshot**. They are not previously published
  signals, executed orders, historical snapshots, or a strategy backtest.
- MACD uses the existing DIF/DEA/histogram calculation; RSI6 includes the neutral
  50 line. Chart legends toggle series and hover reveals dated values. Missing,
  blocked and older snapshots are explicitly labelled.
- `FLAT_WAIT` = empty-account waiting; canonical `POSITION_HOLD` = unchanged
  position (the requested visual `HOLD_POSITION` meaning). No status or action
  rule was renamed, loosened, or connected to trading.
- Use the existing daily button only when you want to prepare a new day's inputs.
  Viewing, expanding cards, changing pages and chart interaction never fetch
  market data or contact an AI Provider. Chan uses the existing read-only
  daily structure; central zones remain deferred.

Graphics release evidence: `reports/tickflow_strategy_graphics_v1_eval.md`.

## Strategy Overlay v2

- On Today Workbench or Stock Research, the main chart now uses QFQ candles
  with the existing MA/BOLL lines. Indicators and strategy conditions retain
  their original calculations and thresholds.
- Same-day observations share one chart marker. Hover or select it to inspect
  the constituent strategies, canonical status, condition progress, met/unmet
  conditions and their changes. A date selector also exposes these details
  without precise pointer positioning, including on mobile.
- Filter by strategy and trigger status. Default visibility prioritizes
  triggered/near-trigger observations plus confirmed Chan structures. Explicitly
  select all statuses to inspect inactive strategies. Progress is not a score,
  probability or order instruction.
- Chan is anchored to its confirmation day, not retroactively to its pivot day.
  The original structure type and pivot/confirmation dates remain in the details.
  Its label is `结构识别｜未定义策略触发合同`, canonical status is `NOT_AVAILABLE`,
  and progress is `N/A`. It never represents a strategy trigger.
- Historical Paper Action is a separate, date-bound read of a verified published
  Daily. It is not inferred from retrospective chart markers. If no valid record
  exists, it is unavailable. The current MVP did not publish HOLD-reason text;
  the overlay therefore shows that reason as unavailable rather than filling it
  from today's summary or recalculating it.
- Filtering, hovering, selecting dates and refreshing do not run the Daily,
  alter ledger/history, fetch market data or contact a Provider.

Overlay release evidence: `reports/tickflow_strategy_overlay_v2_eval.md`.

## Strategy Visualization V2.1 Daily Use

V2.1 supersedes V2's count-only marker display with strategy-specific markers.
The entry points remain `/stock-research` and `/paper-trading`.

1. Read the top dates first: Shanghai system date, this symbol's cached validated
   market date, READY research date, and last completed paper date. The old
   `data_as_of` label is now **股票日线指标缓存日期**. It is not the research date.
2. Select a strategy card to focus its markers, reveal the relevant MA/BOLL
   evidence, center the current observation date and open its conditions. Cards
   keep their true canonical status, including an unmet 2/4 condition count.
3. Use **观察日期** or tap a chart marker. Cards, QFQ price, Volume, MACD, RSI6,
   indicator values and the dated published paper record share that selection.
   Zooming one chart synchronizes the visible date window of all four charts;
   selecting the card again centers the chosen date.
4. Price markers represent MA/BOLL/pullback and Chan price structure. MACD
   markers represent the MACD strategy; volume markers show the volume evidence
   of MACD, volume/price and pullback. RSI6 has no invented strategy marker.
5. Marker abbreviations are MC, MA, BU, VP, PB, BL and CH, with full names in the
   legend. Solid = triggered; hollow = near-trigger; faded = unmet; gray =
   unavailable or Chan structure. Default visibility favors active observations
   and Chan. **全部策略** restores this default; choose all statuses to inspect
   inactive conditions. Mobile uses tap and the below-chart detail section.
6. Display checkboxes control K, MA5/10/20/60, BOLL and strategies only. Default
   is K + MA5 + MA20. They never alter calculations or Paper Action.

Volume is the existing raw volume sequence used by research, with existing
`volume_ma5`/`volume_ma10` indicators warmed on the full validated history.
VOL5/VOL10 include the selected day; they are not the strategy's prior-five-bar
comparison denominator. The chart says **相对量能**, not shares or lots. Unit
authority remains pending. Compact axis labels affect display only; source
values and detailed values retain their precision.

Chan remains a confirmed structure, `NOT_AVAILABLE / N/A`, not a trigger.
Retrospective observations of the current QFQ snapshot remain separate from
published research and actual dated paper records. Missing paper records or
HOLD-reason text stay unavailable; the UI does not infer or backfill them.

Opening charts, changing dates, filters or layers performs no Daily execution,
market fetch or AI call. To inspect without changing an account, stay on
`/stock-research`. Close any concurrent Daily run before performing read-only
acceptance: a new publication can legitimately change the latest chart date.

Release evidence: `reports/tickflow_strategy_visualization_v2_1_eval.md`.

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
