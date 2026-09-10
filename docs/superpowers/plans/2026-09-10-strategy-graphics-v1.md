# Strategy Graphics V1 Implementation Plan

**Goal:** Add understandable daily research graphics to the two existing-workbench workflows.
**Architecture:** Reuse Research Engine v1 without changing its conclusions. Add a read-only
graphics field beside the existing engine, computed from the same validated QFQ snapshot
using existing indicator and strategy functions. Share frontend charts/cards across pages.
**Tech Stack:** FastAPI/Polars, React/TypeScript, existing ECharts, pytest/Vitest/Playwright.
**Spec:** Approved attachment `e9ec1488-17f4-4484-893f-d6f1a2e1272c/pasted-text.txt`.

## Approval And Boundaries

- DESIGN_APPROVED; bounded implementation, no further design approval cycle.
- Linked worktree `tickflow-phase2-ai-review`, branch `codex/tickflow-visual-workbench-v1`.
- Initial Head `59ea865b6fb6b651989eb3521b9c72e266d03aa5`, clean.
- No Provider, new market requests, ledger writes, cloud, extra symbols, action/threshold changes.
- No second research/account state. Preserve all existing frozen evidence.
- Canonical strategy enums stay unchanged; recommended alternative UI words do not redefine rules.
- Existing POSITION_HOLD and requested HOLD_POSITION both mean the same Chinese held-position label;
  persisted engine/ledger enums remain unchanged.
- Latest available snapshot is shown with its actual date, never mislabeled as today's market.

## Read-only Interface

`research.graphics` has `schema_version=1`, `status=READY|BLOCKED`, `symbol=000403.SZ`,
`trade_date`, `timeframe=1d`, `price_basis=QFQ`, `history_basis=SAME_SNAPSHOT_PREFIX`,
`window_requested=30`, `points`, `strategy_markers`, and source/calculation provenance.

Each point: `trade_date`, `close`, `ma5`, `ma10`, `ma20`, `ma60`, `boll_upper`,
`boll_middle`, `boll_lower`, `macd_dif`, `macd_dea`, `macd_hist`, `rsi6`.
Unwarmed indicators are null, never filled. Calculate using full history before slicing.
Each marker: `trade_date`, `strategy_id`, `strategy_name`, `status=TRIGGERED|NEAR_TRIGGER`,
`price`, `conditions_met`, `conditions_total`. Recompute using only the prefix available
at that date. These are retrospective same-snapshot observations, not executed trades.

## Tasks

- [x] Resume audit, read existing route/chart/data code, verify worktree isolation.
- [x] Backend RED: chart presence, 30 dates, indicator parity, source tampering, no future bars,
  QFQ isolation, prefix marker parity and unchanged engine/account outputs.
  Files: `backend/tests/test_strategy_graphics.py`, `backend/app/services/phase2_strategy_graphics.py`,
  thin integration only in `phase2_visual_workbench.py`.
- [x] Frontend RED/green: reusable six strategy cards, condition progress, closest highlight,
  accessible price/MACD/RSI charts and empty states. Existing ECharts/theme only.
  Files: `frontend/src/components/paper-trading/StrategyGraphics.tsx`, `StrategyOverviewCards.tsx`,
  `frontend/src/lib/strategy-graphics.ts`, corresponding tests.
- [x] Page RED/green: Today embeds the shared components; new read-only `StockResearch.tsx`
  reads the same Dashboard API, with summary/cards/charts/condition matrix/Chan.
  Wire `/stock-research` and existing stock navigation; preserve legacy `/stock-analysis`.
- [x] Integration: scoped backend regression, all frontend unit tests, TypeScript/build,
  compileall/Ruff, browser desktop/mobile, chart pixels, chart data parity, readonly network guard.
- [x] Independent review, P0/P1 fixes only, unchanged evidence/code check, runbook/report/marker.

Release closure: commit/push the verified changes, confirm clean/matched fork,
leave the existing desktop launcher/backend available, open pages and stop.
Final Git identity and remote verification are reported with the delivery response.

## Verification Commands

Backend: `PYTHONPATH=. .venv/bin/pytest tests/test_strategy_graphics.py tests/test_research_engine_v1.py tests/test_phase2_research_panel.py tests/test_phase2_visual_workbench.py -q`.
Frontend: `corepack pnpm test -- --run`; `corepack pnpm build`.
Static: `python -m compileall -q app`; `ruff check app --select F821`; `git diff --check`.
Browser: `node scripts/verify-strategy-graphics.mjs` against the identified local backend.
No tests may call a real market/provider endpoint or run the real daily action.
