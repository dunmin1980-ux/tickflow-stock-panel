# TickFlow Research Engine v1

Release status: `TICKFLOW_RESEARCH_ENGINE_V1_RELEASED`

Release date: 2026-09-09, Asia/Shanghai

Validated implementation Head: `98964b31d7a1e754f39d8ebdb40e5f3f1a7cbc4c`

Release branch: `codex/tickflow-visual-workbench-v1`

The commit containing this marker records the release closeout; no runtime
source changed after the validated implementation commit.

## Delivered

- Single-symbol daily research: `000403.SZ`, QFQ, six strategy contracts.
- `CHAN_DAILY_STRUCTURE_V1`: confirmed fractals, candidate strokes, structure and levels.
- Explainable consensus, conflicts, closest measured trigger, previous-day changes.
- Separate published Paper Action with `FLAT_WAIT` / `POSITION_HOLD` meaning.
- Research Daily JSON / Markdown, browser downloads and offline exporter.
- Today Workbench overview and seven-row evidence matrix.
- Twelve frozen daily inputs replayed three times with identical outputs.

## Verification

- Backend focused and compatibility: 388 passed.
- Frontend unit: 141 passed.
- Desktop/mobile E2E: 16 checks passed across two viewports.
- TypeScript, production build, compileall, Ruff F821, whitespace: passed.
- Independent review: `NO_P0_P1_FINDINGS`.
- Original input/account files and protected Paper Trading code: unchanged.
- Replay index SHA-256: `1493de37f5dabd8f82e4ad707896f7331375be35c309a9c39e3dce825a8b92e9`.

## Boundaries

`CENTRAL_ZONE=DEFERRED`; not a complete implementation of Chan theory.
Confidence is evidence agreement, not probability. Research does not modify
the existing simulation decision, T+1, execution, accounting or PnL rules.
No new market request, AI/Provider call, real trade, three-symbol batch, cloud
deployment, or formal Obsidian publication. Eight documented P2 items remain deferred.

## Daily Use

Double-click the existing desktop `TickFlow.app`, then inspect the displayed
research date at `http://127.0.0.1:3018/paper-trading`.

- Runbook: [Visual Workbench Runbook](TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md)
- Evaluation: [Research Engine evaluation](reports/tickflow_research_engine_v1_eval.md)
- Replay: [Twelve-day evidence](reports/research_engine_v1/replay_verification.json)
- Sample: [2026-09-09 Research Daily](reports/research_engine_v1/2026-09-09/research_daily.md)

Next action: `BEGIN_RESEARCH_ENGINE_DAILY_USE`.

Do not automatically start Multi-strategy Action Integration.
