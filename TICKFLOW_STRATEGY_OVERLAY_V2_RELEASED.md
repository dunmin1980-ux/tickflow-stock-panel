# TickFlow Strategy Overlay V2

Release status: TICKFLOW_STRATEGY_OVERLAY_V2_RELEASED
Release date: 2026-09-10 (Asia/Shanghai)
Branch: codex/tickflow-visual-workbench-v1
Release identity: the Git commit containing this marker.
Previous release Head: 177205f8e2edfe6289dad37e5096054fd5d06d32

Supported: QFQ candles + existing MA/BOLL, six-strategy observations and confirmed
Chan events on the shared main chart, same-day grouping, filters, hover/click/tap
details, date-bound paper records, last-30-day responsive viewport.

Validation: frontend 211 passed; backend focused 71 passed; browser 44 passed;
TypeScript/build/compileall/Ruff passed; independent review NO_P0_P1_FINDINGS.

Limits: research/paper engines, rules, inputs, ledger and historical evidence
unchanged. Provider/AI/market calls and real trades: 0. No cloud deployment.
Chan trigger contract is NOT_AVAILABLE, progress N/A. Missing historical HOLD
reasons remain unavailable; retrospective markers never create actions.

Runbook: [daily use](TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md)
Report: [evaluation](reports/tickflow_strategy_overlay_v2_eval.md)
Next action: BEGIN_OVERLAY_DAILY_USE
