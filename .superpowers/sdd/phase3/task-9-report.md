# Phase 3 Task 9 Report

## Implemented

- Added an in-memory ETag store and typed workspace API for bootstrap, reads,
  revisions, conditional commands, and online-only report-body reads.
- Added workspace revision conflict handling without automatic write retries.
- Added same-origin SSE invalidation with bounded reconnect backoff and polling
  fallback when the event stream is unavailable.
- Routed watchlist, report, recap, and shared-preference mutations through
  versioned workspace commands when workspace sync is enabled.
- Added explicit local/cloud/offline mode, last-sync, and data-date indicators.
- Added mobile backtest-summary presentation while keeping full configuration
  desktop-oriented.
- Preserved report Markdown bodies outside offline storage and workspace caches.

## Verification

- `./node_modules/.bin/vitest run`: 14 files, 73 tests passed.
- `./node_modules/.bin/tsc -b`: passed.
- `./node_modules/.bin/vite build`: passed; PWA service worker generated with
  69 precache entries.
- `git diff --check`: passed before commit.

The repository's pnpm wrapper initially refused to proceed because the local
dependency tree had no executable links and the supply-chain policy required
an explicit esbuild decision. The existing offline pnpm store was used to
restore links. Tests and build were then run directly from the locked local
executables; no package or lockfile changes are included.

## Changed Files

- `frontend/src/lib/etagStore.ts`
- `frontend/src/lib/workspace.ts`
- `frontend/src/lib/useWorkspaceEvents.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/useSharedQueries.ts`
- `frontend/src/lib/useSharedMutations.ts`
- `frontend/src/main.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/src/pages/Backtest.tsx`
- `frontend/src/pages/Watchlist.tsx`
- `frontend/src/pages/StockAnalysis.tsx`
- `frontend/src/pages/Review.tsx`
- `frontend/src/lib/__tests__/workspaceConflict.test.ts`
- `frontend/src/lib/__tests__/useWorkspaceEvents.test.ts`
- `frontend/src/lib/__tests__/reportBodies.test.tsx`

## Caveats

- Physical iOS and Android interaction remains a final Task 10 acceptance gate.
- Real two-client conflict and SSE propagation are covered by Task 10 E2E.
- Report Markdown remains online-only and is intentionally unavailable from the
  offline read cache.
