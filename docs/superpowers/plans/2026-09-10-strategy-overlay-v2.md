# Strategy Overlay V2 Implementation Plan

Goal: put the existing research observations onto the shared main QFQ candlestick chart.
Spec: the user's approved short design and Chan contract in this task.
Architecture: extend the V1 read-only projection, then render/filter/group its output. No engine, threshold, action, state, Provider or market-request changes.
Stack: existing Python projection, React/TypeScript and ECharts.

## Tasks

- [x] RED then minimal read-only projection: QFQ OHLC, six existing evaluations with conditions/deltas, confirmed Chan events, date-bound published paper records. RED 9 expected failures plus 2 malformed-receipt regressions; final focused backend 71 passed.
- [x] RED then shared frontend: candles/MA/BOLL, one aggregate marker per date, all/single/status filters, hover/click detail and mobile detail section. RED 35 expected frontend failures; tooltip overflow and theme contrast regressions reproduced and fixed. Final frontend 211 passed; 57 focused overlay tests; TypeScript/build passed.
- [x] Focused backend, all frontend, TypeScript/build, desktop/mobile browser acceptance and independent review. 71 backend / 211 frontend / 44 browser; NO_P0_P1_FINDINGS.
- [x] Verify protected engine/input/account/history bytes and prepare release report/runbook. Commit and push are approved; final Git identity and remote equality are verified after publication in the task closeout.

## Data Contract

Keep V1 `strategy_markers` compatible. Add `overlay_markers` and `paper_records` to graphics; extend points with `open/high/low` from QFQ rows.
Overlay fields: `marker_id`, `trade_date`, `strategy_id`, `strategy_name`, `canonical_status`, `kind` (STRATEGY/CHAN_STRUCTURE), `price`, `conditions_met/total` (null for Chan), `summary`, `evidence`, `failed_conditions`, `delta_vs_previous` (null for Chan), `structure` (original Chan object or null).
Chan ID is `chan_daily_structure`; plotted date is `confirmed_at`, status NOT_AVAILABLE and progress N/A. UI label: 结构识别｜未定义策略触发合同.
`paper_records[date]`: `status` PUBLISHED/NOT_AVAILABLE, `trade_date`, `paper_action`, `research_signal`, `quantity`, `hold_explanation` (null when no published reason text), `source`, `source_sha256`. Never infer or backfill historical reasons from today's rules. Missing/corrupt publication is unavailable, not HOLD.

## Acceptance

Default display: triggered/near plus confirmed Chan events; inactive strategies stay accessible by explicit filter. Same-date markers are grouped, not stacked on identical pixels. All interactions remain read-only on both routes. Tests must prove candlestick OHLC order, exact evaluation parity, confirmation dates, no fabricated Chan trigger/progress, date-bound paper records, filter intersections, pointer/click/keyboard detail, and stale-data guards.
