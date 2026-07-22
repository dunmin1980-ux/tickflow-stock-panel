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
- No frontend, Task 7 bundle/adapter/proxy file, broker, Telegram, OpenClaw, Gold, minute-data, or
  automatic-trading behavior was changed.
