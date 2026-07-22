# Phase 3 Task 8 Report

## Starting State

- Worktree: `/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app`
- Branch: `codex/tickflow-multiclient-app`
- Required HEAD: `31e0132`
- Task 7 status at start: final security review `CLEAN`

## Implemented

- Added `ComputeRouter` with a two-target result contract and exactly one cloud attempt. The
  router catches only `LocalComputeUnavailable`; unsupported error codes cannot construct that
  exception.
- Added bounded conversion points for compute-input transport/read timeouts and typed worker
  infrastructure failures. Integrity, coverage, freshness, digest, permission, HTTP 429, and
  business exceptions remain outside the fallback catch.
- Added worker failure classification for process start/exit, native-library loading, and local
  repository initialization. Child business failures remain `BacktestWorkerError`; a snapshot
  integrity race is reconstructed as `ComputeInputIntegrityError` in the parent process.
- Added a disposable writable repository overlay for verified read-only bundles. Only the four
  Task 7 data roots are linked into the overlay; worker matrix caches and repository-generated
  files are removed with the overlay, while the verified source tree remains unchanged.
- Routed desktop `POST /api/backtest/strategy/run`, `GET /api/backtest/strategy/stream`, and
  `POST /api/screener/run` through local compute first. The local worker receives the exact
  directory returned by `prepare_compute_input()`. Cloud mode has no adapter and executes directly,
  so it cannot recursively fall back to itself.
- Re-bound the installed manifest to the requested task and canonical parameter digest immediately
  before execution, and rejected missing, non-directory, or symlinked required data roots.
- Added single-call authenticated cloud requests that preserve non-success HTTP status, including
  429. The stream path emits the remote result through the existing `done` event after an eligible
  local infrastructure failure.
- Added automatic writeback of successful strategy-backtest summaries only. The payload contains
  exactly the existing bounded summary fields, a restricted numeric stats allowlist, no trades,
  curves, report body, worker details, parameters, credentials, or arbitrary run identifiers.
- On a summary revision conflict, the current revision is refreshed and the response returns
  `confirmation_required` with the safe pending summary. Compute is not rerun and the command is
  not replayed or overwritten automatically.
- Compute logs contain only `task_id`, `target`, `elapsed_ms`, and `error_code`. Parameters,
  authentication headers, report bodies, and exception detail are not logged by the router.

## TDD Evidence

1. Initial router RED:
   - `uv run --project backend pytest -q backend/tests/test_compute_router.py`
   - Collection failed because `app.services.compute_router` did not exist.
2. Transport-boundary RED:
   - Collection failed because `prepare_local_compute_input` did not exist.
3. Worker-boundary RED:
   - Collection failed because `BacktestWorkerInfrastructureError` did not exist.
4. Read-only worker RED:
   - The worker attempted to create `.matrix_generation_stock.json` under the verified snapshot
     and failed with `PermissionError`.
5. API integration RED:
   - `17 failed, 28 passed`; failures covered strategy run, stream fallback, screener routing,
     cloud direct execution, safe summary writeback, and revision conflict behavior.
6. Security follow-up RED:
   - Unknown fallback codes and repository temp-root failures failed their boundary tests.
   - A replaced data-root symlink reached the worker, a string stat reached the summary, and a
     child snapshot race was initially misclassified as repository infrastructure.
   - An arbitrary `run_id` initially reached the synchronized summary.

## Final Verification

1. Task 8 and worker targeted:
   - Command: `uv run --project backend pytest -q backend/tests/test_compute_router.py backend/tests/backtest/test_worker_process.py`
   - Result: `58 passed in 14.02s`.
2. Related backtest, screener, desktop proxy, and workspace regression:
   - Command: `uv run --project backend pytest -q backend/tests/backtest backend/tests/test_backtest_etf.py backend/tests/test_screener_builtin_params.py backend/tests/test_screener_etf.py backend/tests/test_desktop_cloud_proxy.py backend/tests/workspace`
   - Result: `409 passed, 2 warnings in 16.79s`.
3. Full backend:
   - Command: `uv run --project backend pytest -q backend/tests`
   - Result: `1253 passed, 11 warnings in 65.93s`, exit code `0`.
4. Ruff:
   - New service, worker, and tests pass strict Ruff.
   - Modified legacy API files pass Ruff with only their pre-existing rule classes excluded.
5. Diff validation:
   - `git diff --check` returned exit code `0` with no output.

## Changed Files

- `backend/app/services/compute_router.py` (new)
- `backend/app/api/backtest.py`
- `backend/app/api/screener.py`
- `backend/app/backtest/worker.py` (strictly necessary typed failure and read-only snapshot support)
- `backend/tests/test_compute_router.py` (new)
- `backend/tests/backtest/test_worker_process.py` (strictly necessary worker regressions)
- `.superpowers/sdd/phase3/task-8-report.md` (this report)

## Caveats

- A desktop cloud-fallback response relies on the cloud instance running this same Task 8 API so
  the cloud endpoint performs summary writeback. Mixed-version deployment must be avoided.
- Conflict responses are deliberately transient and are not persisted as an offline mutation
  queue. A client must show the refreshed revision and obtain explicit user confirmation before
  submitting the safe pending summary through the versioned workspace command.
- The local overlay is process-local and temporary. It does not copy data, publish results, call an
  external data provider, or consume additional TickFlow Pro RPM after bundle preparation.
- Existing API files retain unrelated historical Ruff debt. No broad formatting or refactor was
  performed in this task.
- The initial Task 8 commit changed no frontend or Task 7 bundle/adapter/proxy file. Neither that
  commit nor this follow-up changes broker, Telegram, OpenClaw, Gold, minute-data, or
  automatic-trading behavior.

## Security Review Follow-up

### Review Findings Addressed

1. Replaced the cache-target symlink execution model with a per-execution compute-input lease.
   The source directory is pinned with a no-follow directory descriptor. Every manifest member is
   then opened through no-follow relative descriptors, checked as a regular file, revalidated
   against its declared size and SHA-256, and copied into a private read-only directory. The worker
   receives only this lease, and the caller removes it in `finally` through the context manager.
2. Restricted read-only snapshot workers to the application-owned built-in strategy directory.
   Snapshot `strategies/custom`, `strategies/ai`, and any unlisted Python are neither copied into the
   lease nor included in the worker's import search. Unsupported custom or AI strategy IDs therefore
   return a normal failed backtest and never trigger cloud fallback.
3. Removed `worker_exit_failed` from both fallback allowlists. Only process start failure, an
   explicitly reported native-library load failure, and local repository initialization failure are
   worker-side fallback candidates. A post-start crash, OOM-style nonzero exit, no-result exit, or
   failure to terminate now raises `BacktestWorkerError` and is fail closed.
4. Completed the summary conflict UI. SSE `done` retains `summary_sync`, `pending_summary`, and
   `current_revision`. The Backtest page displays an explicit unsaved-summary alert and requires a
   click on `Confirm save summary`. That action appends only the pending sanitized summary with the
   returned revision; it never reruns compute. Offline failures and repeated 412 responses preserve
   the pending state for another explicit user decision.

### Follow-up TDD Evidence

- Lease RED: targeted test collection failed because `lease_compute_input` did not exist.
- Contract RED: conflict tests failed with missing `current_revision` in both direct and SSE results.
- Frontend RED: four tests failed because confirmation state, action, and alert UI did not exist.
- Worker security regression covers a malicious snapshot custom strategy that writes a marker at
  import time; the marker remains absent under read-only execution.
- Cache replacement regression swaps the published cache key during lease construction and proves
  that the lease contains one generation only. A same-size file tamper is rejected by SHA-256 before
  either worker or cloud is called.
- A simulated post-start native crash exits with `-9` and no result; it raises the ordinary worker
  error rather than the fallback-eligible subtype.

### Follow-up Verification

- Related backtest, screener, proxy, workspace, and Task 8 tests: `470 passed, 2 warnings`.
- Full backend: `1261 passed, 11 warnings`.
- Full frontend Vitest: `97 passed` across 22 files.
- TypeScript project build: `tsc -b` passed.
- Vite production build: passed; existing chunk-size warnings remain informational.
- Ruff: strict new service/worker/test scope passed; legacy API files passed with the same documented
  pre-existing rule exclusions.

### Follow-up Caveats

- The managed `pnpm --dir frontend` wrapper attempted an install and stopped on the environment's
  ignored-build policy. Verification therefore invoked the already-installed, lockfile-backed
  `frontend/node_modules/.bin/{vitest,tsc,vite}` binaries directly.
- The lease is a full verified copy per execution. This deliberately trades local disk I/O for an
  immutable execution view and a small security boundary; no cloud API, data-provider API, or Pro
  RPM is consumed while making the lease.
- Frontend files are now included only for the explicit revision-conflict confirmation flow. Task 10
  E2E, mock, script, and documentation files remain untouched and unstaged by this follow-up.
