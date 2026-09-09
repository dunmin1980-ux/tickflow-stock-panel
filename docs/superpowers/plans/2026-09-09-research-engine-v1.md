# Research Engine v1 Implementation Plan

Goal: extend the existing six-rule research panel into explainable single-symbol
daily research; keep every Paper Trading decision and accounting module unchanged.
User has authorized continuous implementation and release without intermediate approval.
Spec: user attachment `90752372-fc50-464d-90e3-a810f6bffd89/pasted-text.txt`.

## Resume Audit

- [x] Branch `codex/tickflow-visual-workbench-v1`, HEAD `de43f8b2157a2653a457773567117983fbb66d8b`.
- [x] Existing six-rule implementation is uncommitted, preserved and extended in place.
- [x] Existing tests: research panel, visual facade, daily input, frontend ResearchPanel.
- [x] No changes to protected Paper Trading, state, data-input or Provider modules.

## Contract Decisions

- Existing `phase2_research_panel.py` remains the sole six-strategy evaluator.
  Add structured numeric evidence and a canonical `StrategyResearch` contract.
  Legacy presentation fields are retained only for cached-client compatibility.
- NEAR_TRIGGER: exactly one unmet condition, at least half conditions met, and
  the unmet condition is current-day, not an unchangeable prior-day event.
- Distance is the numeric shortfall to the current indicator threshold, in its
  own unit. Do not sum prices, ratios and percentage points or project a price
  that would make moving indicators trigger. At equality strict predicates still fail.
- Closest: highest condition completion ratio, then fewer failed conditions,
  then stable strategy id; exclude triggered/unavailable rules. This is not a probability.
- Aggregation is a documented decision tree anchored on MACD/MA direction,
  Chan confirmation, setup/trigger observations and explicit conflicts. Counts
  are explanatory, never majority-vote orders. Confidence is evidence agreement,
  not predictive accuracy or a calibrated probability.
- CHAN_DAILY_STRUCTURE_V1: strict three-bar high-and-low fractals, confirmed on
  the right-hand bar date, no inclusion merging. Candidate strokes only;
  no complete strokes/segments/central zones, divergence or multitimeframe claims.
- All technical comparisons use QFQ. Relative volume uses same-source raw volume.
- Report paper_action/position from the persisted date-specific ChenQuant Daily.
  If no published daily exists, say NOT_RUN/UNKNOWN, never invent HOLD.
- Research Daily is pure JSON + deterministic Markdown in the dashboard, with
  browser downloads and an offline exporter. GET does not mutate accounting or inputs.

## Implementation And Verification

- [x] RED/green: numeric evidence, status schema, prior-day deltas, trigger distances.
  Files: `phase2_research_panel.py`, `schemas/research_engine.py`, focused tests.
- [x] RED/green: bounded Chan module and prefix-invariance tests.
  Files: `phase2_chan_structure.py`, matching tests and short contract document.
- [x] RED/green: aggregator, faithful HOLD explanation, next observations, JSON/Markdown.
  Files: `phase2_research_daily.py`, matching tests, facade read-only integration.
- [x] First-viewport summary, seven-row strategy matrix, expandable measured evidence,
  daily downloads, prior-day/structure view. Reuse current research UI and API.
- [x] Offline twelve-date replay x3; hash checks on protected files and input/account directories.
- [x] Focused Option C/Claims/Renderer/Paper compatibility; frontend tests, E2E,
  build, compileall, Ruff F821, independent review. Fix P0/P1 only.
- [ ] Runbook, compact release evidence, commit + push, match fork, clean worktree,
  local backend running, open Today Workbench; stop before Action Integration.
