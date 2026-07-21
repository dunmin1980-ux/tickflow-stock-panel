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

## Review Follow-up

All seven Task 9 review findings were addressed in the follow-up commit:

1. SSE `onopen` now reconciles revisions before restoring online state or
   stopping the 30-second poll. A failed reconciliation keeps offline-readonly
   state, polling, and the bounded 1/2/5/10/30-second reconnect sequence.
2. Bootstrap and per-resource metadata for watchlist, backtest summaries,
   stock reports, market recaps, and shared preferences are persisted through
   `offlineDb`. Explicit projectors remove report bodies, unknown fields, and
   credential-like preference data before persistence. Report Markdown remains
   online-only and `no-store`.
3. Desktop preferences now merge the typed shared DTO over a complete safe
   runtime/default object. The partial workspace DTO is no longer asserted as
   a complete `Preferences` response.
4. Workspace mutation preflight checks both browser connectivity and
   `workspaceStatus.offlineReadonly`, so a reachable Mac gateway cannot write
   while its cloud workspace is disconnected.
5. HTTP 412 marks a per-resource conflict gate without promoting the response
   ETag to writable state. Repeated commands fail before `fetch`; only a
   successful online resource GET or bootstrap clears the gate.
6. Native `disabled` plus `aria-disabled` states now cover Screener single and
   batch watchlist writes, watchlist/screener column settings, shared data-source
   selection, shared menu order/visibility, Stock Analysis regeneration, and
   the existing Watchlist/Review report mutations.
7. Watchlist batch-add and clear counts are calculated from pre/post resource
   snapshots, including duplicate requests and already-present symbols.

### Follow-up Verification

- `./node_modules/.bin/vitest run`: 18 files, 85 tests passed.
- `./node_modules/.bin/tsc -b`: passed.
- `./node_modules/.bin/vite build`: passed; PWA service worker generated with
  69 precache entries. Vite retained the existing large-chunk warning.
- `git diff --check`: passed.
- Direct ESLint execution was attempted, but this dependency tree has no
  `frontend/node_modules/.bin/eslint` executable. No wrapper, install, or
  lockfile change was used to mask that missing tool.

### Follow-up Files

- `frontend/src/lib/etagStore.ts`
- `frontend/src/lib/workspace.ts`
- `frontend/src/lib/useWorkspaceEvents.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/components/screener/ScreenerTable.tsx`
- `frontend/src/pages/Screener.tsx`
- `frontend/src/pages/Watchlist.tsx`
- `frontend/src/pages/StockAnalysis.tsx`
- `frontend/src/pages/Review.tsx`
- `frontend/src/pages/settings/DataSourceEditor.tsx`
- `frontend/src/pages/settings/DataSources.tsx`
- `frontend/src/pages/settings/MenuSettings.tsx`
- `frontend/src/lib/__tests__/workspaceConflict.test.ts`
- `frontend/src/lib/__tests__/useWorkspaceEvents.test.ts`
- `frontend/src/lib/__tests__/workspaceOfflineCache.test.ts`
- `frontend/src/lib/__tests__/workspacePreferences.test.ts`
- `frontend/src/lib/__tests__/workspaceWatchlistCounts.test.ts`
- `frontend/src/lib/__tests__/workspaceMutationUi.test.tsx`

### Remaining Caveats

- Full app termination can revoke the browser-session offline-access grant even
  though sanitized IndexedDB snapshots remain; this is intentional session
  isolation, not a persistent-login mechanism.
- Physical-device and real two-client SSE/conflict acceptance remain Task 10.

## Review Follow-up 2

All five second-round Task 9 review findings were addressed in the follow-up
commit:

1. Every workspace 401 now revokes `offlineSessionAccess`, advances its
   generation, and clears IndexedDB snapshots. Bootstrap, resource reads, and
   revisions share this path; commands apply the same handling. A later network
   failure therefore cannot return any of the five stale workspace projections.
2. A failed `resync_required` revision pull now closes the opened stream and
   enters the existing polling plus 1/2/5/10/30-second reconnect path. A
   successful reconnect reconciles the missed window before restoring online
   state and stopping polling.
3. Desktop preferences now read `/api/client/status`, then the complete safe
   runtime response from `/api/settings/preferences`, and only then overlay the
   sanitized workspace preference projection. The desktop proxy forwards the
   complete runtime route and does not redirect it through workspace APIs.
   Realtime, minute, monitor, and Review values are refreshed from runtime after
   mutations; unknown or credential-like fields are neither merged nor cached.
4. `StockInfoBar` exposes native `disabled`, `aria-disabled`, and a descriptive
   accessible name for offline watchlist writes. `StockPreviewDialog` also
   blocks its mutation handler before the API call, covering the shared stock
   preview path.
5. Screenshot import keeps already-present candidates disabled, de-duplicates
   selected OCR matches, and displays the actual `added` count returned by the
   batch mutation instead of the submitted symbol count.

### Follow-up 2 Verification

- `./node_modules/.bin/vitest run`: 20 files, 92 tests passed.
- `./node_modules/.bin/tsc -b`: passed.
- `./node_modules/.bin/vite build`: passed; PWA service worker generated with
  69 precache entries. Vite retained the existing large-chunk warning.
- `git diff --check`: passed before commit.

### Follow-up 2 Files

- `frontend/src/lib/workspace.ts`
- `frontend/src/lib/useWorkspaceEvents.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/components/StockInfoBar.tsx`
- `frontend/src/components/StockPreviewDialog.tsx`
- `frontend/src/components/WatchlistImportDialog.tsx`
- `frontend/src/lib/__tests__/workspaceOfflineCache.test.ts`
- `frontend/src/lib/__tests__/useWorkspaceEvents.test.ts`
- `frontend/src/lib/__tests__/workspacePreferences.test.ts`
- `frontend/src/lib/__tests__/workspaceMutationUi.test.tsx`
- `frontend/src/lib/__tests__/stockPreviewWorkspaceUi.test.tsx`
- `frontend/src/lib/__tests__/watchlistImportDialog.test.tsx`

### Follow-up 2 Caveats

- The desktop same-origin complete-preferences route is intentionally proxied to
  the authenticated cloud runtime; there is no separate local-only complete
  preferences endpoint in the current desktop gateway contract.
- Physical-device recovery and real two-client SSE propagation remain Task 10
  acceptance items.

## Review Follow-up 3

Desktop preference recovery now handles the real gateway outage contract:
`/api/client/status` may return HTTP 200 with `reachable: false`. In that state
the client skips the cloud-only complete-preferences request and overlays the
sanitized workspace projection from offline storage onto the last known
complete preferences (or safe defaults). A connection failure after an
initially reachable status follows the same fallback; HTTP errors, including
401, are not swallowed and retain the revocation path.

A focused regression proves that the unreachable desktop status does not call
`/api/settings/preferences` and that cached shared preferences remain readable
in offline-readonly mode.
