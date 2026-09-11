# Strategy Visualization V2.1 Implementation Plan

**Goal:** Link existing strategy evidence to price, MACD and relative volume with one observation date.
**Architecture:** Extend V2 read-only graphics, retain engines and historical records, share frontend selection between strategy cards and four ECharts canvases. No second strategy calculation.
**Spec:** User-approved Strategy Visualization V2.1 instruction, 2026-09-11.
**Tech Stack:** React, TypeScript, ECharts, Vitest, pytest, Playwright.

## Boundaries

- No Provider, AI, market requests, real trades, ledger or historical evidence writes.
- Chan remains NOT_AVAILABLE / N/A at its confirmation date.
- Historical prefix observations are not published research or paper decisions.
- Use raw volume already used by the validated research snapshot; no unconfirmed share/lot labels.
- Keep the existing worktree and branch; commit and push after verification, then stop.

## Execution

- [x] Inspect clean d7a5cc3 baseline and preserve protected file/data hashes.
- [x] RED: chart placement, volume, type/status identity, linked selection, toggles, historical semantics and top-date tests.
- [x] Minimal implementation: `phase2_strategy_graphics.py` volume projection; frontend `strategy-graphics.ts` options; shared `StrategyVisualization` selection; existing cards/details; truthful application header dates.
- [x] GREEN: focused tests and SSR layout checks; no engine edits. Added RED/GREEN for explicit card re-centering after manual zoom.
- [x] Full frontend 256 passed, focused backend 92 passed, TypeScript/build, compileall, Ruff F821, diff check. Final grid-only focused suite: 7 passed; final production build repeated after freeze.
- [x] Real local read-only E2E: desktop 1440x1000 and touch viewport 390x844; both pages, linked cards and all four dates, filters/toggles, dark/light, no overlap, account unchanged within the completed final run. 48 checks / 20 screenshots.
- [x] Independent focused review: NO_P0_P1_FINDINGS, including final shared-grid / compact-axis / 480 date-column alignment checks.
- [x] Report, screenshots, release marker, Runbook and protected hashes prepared for the release commit. External 2026-09-11 Daily update preserved and documented; prior historical bytes verified.

Publication gate: commit/push only to the existing fork branch, verify local/fork
HEAD match and clean worktree, keep the local service running, then STOP. The
commit introducing the release marker is the release identity; post-push SHA
and clean-status verification are reported in the final execution result.
