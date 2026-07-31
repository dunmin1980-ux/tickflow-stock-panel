# TickFlow Phase 2 Merge And Baseline

## Status

```text
PHASE2_BASELINE_READY
```

Recorded on 2026-07-31 (Asia/Shanghai). This report closes the Phase 1
governance gate and records the clean Phase 2 baseline only. No Phase 2 facts,
AI review, Obsidian export, or Paper Trading implementation was started.

## Phase 1 Governance

- Repository: `dunmin1980-ux/tickflow-stock-panel`
- Pull request: [#2](https://github.com/dunmin1980-ux/tickflow-stock-panel/pull/2)
- PR state: `MERGED`
- PR Head: `3af4185b0342c9f274c2ba7b8cd18e6c2c8745dd`
- PR Base: `codex/tickflow-multiclient-app`
- Merge commit: `ce1f0b221cfd390cafc92a26ef8e16cf7dd3a753`
- Merged at: `2026-07-31T13:51:47Z`
- Validated Head in Base: `YES`
- Governance tag: `phase1-observation-passed-20260731`
- Tag target after peeling: `ce1f0b221cfd390cafc92a26ef8e16cf7dd3a753`

## Remote CI Evidence

- Workflow: `Backend Quality`
- Event: `pull_request`
- Run: [30636014137](https://github.com/dunmin1980-ux/tickflow-stock-panel/actions/runs/30636014137)
- Head SHA: `3af4185b0342c9f274c2ba7b8cd18e6c2c8745dd`
- Job: `high-risk-checks`
- Conclusion: `SUCCESS`
- Locked dependency sync: `PASSED`
- Compile backend: `PASSED`
- Ruff F821: `PASSED`
- Full backend pytest: `PASSED`

The merge decision used the real GitHub check-run. Mergeability and conflict
status were not treated as substitutes for CI.

## Phase 2 Workspace

- Base SHA: `ce1f0b221cfd390cafc92a26ef8e16cf7dd3a753`
- Branch: `codex/tickflow-phase2-ai-review`
- Worktree: `/Users/macbookpro/Documents/TradingView 量化/tickflow-phase2-ai-review`
- Worktree type: linked Git worktree
- Initial worktree status: `CLEAN`
- Phase 1 untracked files carried forward: `NO`
- Runtime code modified: `NO`

## Backend Baseline

Commands and results:

```text
UV_PYTHON=3.12 UV_HTTP_TIMEOUT=180 uv sync --locked --extra dev
PASSED

PYTHONPATH=. .venv/bin/python -m compileall -q app
PASSED

.venv/bin/ruff check app --select F821
PASSED

PYTHONPATH=. .venv/bin/pytest -q
1349 passed, 13 warnings
```

The warnings are existing deprecation or library warnings and did not fail the
baseline.

## Frontend Baseline

Commands and results:

```text
corepack pnpm install --frozen-lockfile
PASSED

corepack pnpm test -- --run
22 test files passed, 97 tests passed

corepack pnpm exec tsc -b --pretty false
PASSED

corepack pnpm build
PASSED
```

The production build emitted the existing large-chunk advisory and a Node URL
API deprecation warning. Neither was a build failure.

## Frozen Boundaries

- Cloud redeployment: `NO`
- AI: `NOT_CONFIGURED`
- Paper Trading: `NOT_STARTED`
- Integrated Gold: `DISABLED / external_send_count=0`
- Real Key: `NOT_EXPOSED`
- Phase 2 feature development: `NOT_STARTED`
- Phase 1 market evidence modified: `NO`
- Phase 1 request audit modified: `NO`

Vendor confirmations remain pending and must be retained in later Phase 2 risk
metadata:

- `intraday_batch_entitlement`
- `first_30m_bucket_includes_09_30`
- `volume_unit`
- `amount_unit`

## Stop Point

The next permitted action is Phase 2 deterministic facts and AI-review design
and implementation under a separate instruction. This baseline does not imply
production readiness, trading readiness, or Paper Trading readiness.
