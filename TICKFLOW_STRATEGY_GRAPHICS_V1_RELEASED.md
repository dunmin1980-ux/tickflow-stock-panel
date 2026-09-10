# TickFlow Strategy Graphics V1

Release status: TICKFLOW_STRATEGY_GRAPHICS_V1_RELEASED
Release date: 2026-09-10 (Asia/Shanghai)
Branch: codex/tickflow-visual-workbench-v1
Release identity: the Git commit containing this marker.
Previous release Head: 59ea865b6fb6b651989eb3521b9c72e266d03aa5

Supported: single-symbol 000403.SZ, Today Workbench and Stock Research graphics,
six existing strategies, condition progress, 30-day QFQ price/MA/BOLL, MACD, RSI6,
dated prefix observation markers, existing HOLD and read-only Chan explanation.

Validation: frontend 177 passed; backend focused 60 passed; browser 35 passed;
TypeScript/build/compileall/Ruff passed; independent review NO_P0_P1_FINDINGS.

Limits: existing Paper Trading rules unchanged; no real trading, Provider calls,
new market requests, extra symbols, cloud deployment or automatic publishing.
Retrospective chart markers are not orders or historical published decisions.

Runbook: [daily use](TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md)
Report: [evaluation](reports/tickflow_strategy_graphics_v1_eval.md)
Next action: BEGIN_GRAPHICS_DAILY_USE
