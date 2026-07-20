# Phase 3 Tasks 1-4 Security And Compatibility Fix Report

Date: 2026-07-21

## Status

Implemented all five findings from `tasks-1-4-security-fix-brief.md` from base
`cc59e4064dbfc669e63d4b61f7d4be2ef4b83da9`.

- Restored the gate-off operational preference response through a recursive safe
  projection while keeping the workspace preference DTO on its strict whitelist.
- Added frontend exact-ID report body reads for stock analysis and market recap;
  list state remains metadata-only and body routes are excluded from offline
  persistence.
- Validated the real `ColumnConfig` schema, including the allowed business
  `source.key` field, and preserved object arrays through workspace bootstrap and
  commands.
- Added plugin uninstall and custom data-source deletion to the enabled workspace
  legacy mutation gate before endpoint side effects.
- Made realtime-monitor legacy gating field-aware: local-only fields remain writable,
  while shared or mixed payloads fail before preference writes.

No cloud runtime, Gold, Telegram, OpenClaw, broker, trading, or deployment files were
changed.

## Files Changed

- `backend/app/api/settings.py`
- `backend/app/api/workspace.py`
- `backend/app/services/preferences.py`
- `backend/tests/workspace/test_api.py`
- `backend/tests/workspace/test_commands.py`
- `frontend/src/components/monitor/RuleEditor.tsx`
- `frontend/src/lib/__tests__/notificationProjection.test.ts`
- `frontend/src/lib/__tests__/offlinePolicy.test.ts`
- `frontend/src/lib/__tests__/reportBodies.test.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/stockAnalysisStore.ts`
- `frontend/src/pages/Review.tsx`
- `frontend/src/pages/settings/Monitoring.tsx`
- `.superpowers/sdd/phase3/tasks-1-4-security-fix-report.md`

## Test-First Evidence

- `uv run --project backend pytest backend/tests/workspace/test_commands.py backend/tests/workspace/test_api.py -q`
  - Baseline: 106 passed.
  - RED after regression tests: 115 passed, 8 failed for the intended missing fixes.
- `node node_modules/vitest/vitest.mjs run src/lib/__tests__/reportBodies.test.tsx src/lib/__tests__/notificationProjection.test.ts src/lib/__tests__/offlinePolicy.test.ts`
  - RED: 22 passed, 7 failed for missing exact-ID methods/body fetches and raw
    credential consumers.
- Corrected `ColumnConfig.source.key` RED run:
  - 4 intended failures proving the prior generic `key` rule rejected/dropped the
    real nested business field.

## Final Verification

- `uv run --project backend pytest backend/tests/workspace/test_commands.py backend/tests/workspace/test_api.py -q`
  - 129 passed.
- `node node_modules/vitest/vitest.mjs run src/lib/__tests__/reportBodies.test.tsx src/lib/__tests__/notificationProjection.test.ts src/lib/__tests__/offlinePolicy.test.ts`
  - 29 passed across 3 files.
- `uv run --project backend pytest backend/tests/workspace -q`
  - 163 passed.
- `uv run --project backend pytest backend/tests -q`
  - 984 passed, 11 existing deprecation warnings.
- `node node_modules/vitest/vitest.mjs run`
  - 57 passed across 10 files.
- `node node_modules/typescript/bin/tsc -b`
  - Passed with no output.
- `node node_modules/vite/bin/vite.js build`
  - Passed; PWA generated. Existing large-chunk warning remains.
- `uv run --project backend ruff check --ignore RUF001,RUF002,RUF003,RUF100,I001 backend/app/api/settings.py backend/app/api/workspace.py backend/app/services/preferences.py backend/tests/workspace/test_api.py backend/tests/workspace/test_commands.py`
  - All checks passed.
- `uv run --project backend ruff check --statistics backend/app/api/settings.py backend/app/api/workspace.py backend/app/services/preferences.py backend/tests/workspace/test_api.py backend/tests/workspace/test_commands.py`
  - Reports 51 pre-existing style findings: ambiguous full-width punctuation,
    unused `noqa` directives, and import ordering in legacy files.
- `git diff --check`
  - Passed with no output.

## Commit

- Message: `fix: harden workspace compatibility boundaries`
- Delivery commit: the commit containing this report; exact hash is returned in the
  task response.

## Concerns

- The managed `pnpm test` entrypoint refused to run because its supply-chain policy
  rejects `source-map@0.8.0` under the minimum release age. The existing pinned local
  Vitest, TypeScript, and Vite binaries were used directly; no lockfile or dependency
  policy was changed.
- Default Ruff remains non-zero on pre-existing style findings listed above. The
  focused functional/static rule set passes.
- The production build retains the existing Rollup chunk-size warning.
