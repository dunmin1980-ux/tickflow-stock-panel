# TickFlow Strategy Visualization V2.1

Status: `TICKFLOW_STRATEGY_VISUALIZATION_V2_1_RELEASED`

Release date: 2026-09-11 (Asia/Shanghai).
Baseline: `d7a5cc3c92322e90c4ccbe9ad3ee1bd41e5e3c20`.
Release commit: the commit introducing this marker.
Branch: `codex/tickflow-visual-workbench-v1`.

## Released

- QFQ candles/MA/BOLL, existing RAW relative volume with VOL5/VOL10, MACD and RSI6.
- Strategy-specific type/status markers on the appropriate evidence chart.
- Shared observation date and zoom; card selection, re-centering, details and
  mobile tap; display-only layer controls.
- Truthful symbol-scoped cached market/research/paper dates and separately named
  stock daily enriched-cache date.

Research and Paper engines, strategy thresholds and historical research remain
unchanged. Chan remains structure-only `NOT_AVAILABLE / N/A`. No Provider,
AI call, real trade, additional symbol or cloud deployment is part of this release.

## Verification

Production build: PASSED. Final real-browser E2E: 48 checks / 20 screenshots.
Desktop: 1440x1000. Mobile: 390x844 Chromium touch viewport.
Independent focused review: `NO_P0_P1_FINDINGS`.
Frontend full: 256 passed before final grid-only fix; final layout suite: 7 passed.
Backend focused: 92 passed. Existing P2 work: DEFER.

During final acceptance an external Daily run published 2026-09-11. It was
preserved, not created or reverted by the acceptance runner. Repeated read-only
E2E on the stable publication passed with account state unchanged. See the
evaluation for the exact historical-hash and external-update distinction.

Runbook: `TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md`.
Evaluation: `reports/tickflow_strategy_visualization_v2_1_eval.md`.
Verification: `reports/strategy_visualization_v2_1/ui/ui-verification.json`.

Local entry: `http://127.0.0.1:3018/stock-research`.
Today Workbench: `http://127.0.0.1:3018/paper-trading`.

Next action: `BEGIN_STRATEGY_VISUALIZATION_DAILY_USE`. STOP.
