# Phase 3 Task 7 Report

## Starting State

- Worktree: `/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app`
- Branch: `codex/tickflow-multiclient-app`
- Required HEAD: `891ff33`
- Commit subject: `feat: prepare verified cloud compute inputs`

## Implemented

- Added `ComputeInputBundleService` for `strategy_backtest` and `screener` only. Request data is validated through the existing endpoint Pydantic request models, rejects extra fields and file paths recursively, and is limited to stock daily workflows.
- Bundles read only existing local `kline_daily`, `kline_daily_enriched`, `instruments`, and `adj_factor` parquet roots. The service does not call a provider, synchronize data, or consume TickFlow Pro RPM.
- Added deterministic manifest-first ZIP output with schema 1, task, canonical parameter digest, `data_as_of`, per-file size/SHA-256, total uncompressed bytes, and a 2 GiB ceiling.
- Added a process-local ten-minute master cache with unique response leases. The API response lease is deleted by a response background task; expired cache masters are purged on subsequent builds.
- Added authenticated, feature-gated `POST /api/workspace/compute-inputs/build` with a dedicated build semaphore of one and `X-Data-As-Of` / `X-Bundle-SHA256` response headers.
- Added `CloudWorkspaceAdapter.prepare_compute_input()` and `install_compute_input()` with streaming download, archive/header/manifest/config/freshness verification, traversal/absolute path/symlink/unlisted file/duplicate/size/hash rejection, strict temporary cleanup, and `ComputeInputIntegrityError` fail-closed behavior.
- Added atomic same-filesystem publication to `Application Support/TickFlowStockPanel/compute_inputs/{cache_key}` and recursive read-only permissions. Preplanted target symlinks are rejected without following or mutating their destination.
- Added the new workspace route to the desktop proxy's exact local route registry. This adjacent change is required by the registered-route fail-closed audit; actual cloud download remains the adapter's authenticated direct call.

## TDD Evidence

1. Initial RED:
   - Command: `uv run --project backend pytest backend/tests/test_compute_input_bundle.py -q`
   - Result: collection failed because `ComputeInputIntegrityError` and the bundle service did not exist.
2. First GREEN convergence:
   - Result: `25 passed`; then expanded security cases reached `34 passed`.
3. Registered-route regression:
   - Command: `uv run --project backend pytest backend/tests/workspace backend/tests/test_desktop_workspace_adapter.py backend/tests/test_desktop_cloud_proxy.py -q`
   - RED: the new registered workspace route had no exact local desktop contract.
   - GREEN after adding the exact method/path entry: `260 passed`.

## Final Verification

1. Task 7 targeted:
   - Command: `uv run --project backend pytest backend/tests/test_compute_input_bundle.py -q`
   - Result: `35 passed`.
2. Workspace and desktop adapter/proxy regression:
   - Command: `uv run --project backend pytest backend/tests/workspace backend/tests/test_desktop_workspace_adapter.py backend/tests/test_desktop_cloud_proxy.py -q`
   - Result: `260 passed`.
3. Full backend:
   - Command: `uv run --project backend pytest -q`
   - Result: `1117 passed, 11 warnings in 51.78s`.
4. Focused Ruff:
   - Command: `uv run --project backend ruff check --ignore RUF100,RUF003 backend/app/services/compute_input_bundle.py backend/app/api/workspace.py backend/app/desktop_client/workspace_adapter.py backend/app/desktop_client/proxy.py backend/tests/test_compute_input_bundle.py`
   - Result: `All checks passed!`.
5. Diff validation:
   - Command: `git diff --check` for the Task 7 files.
   - Result: exit `0`, no output.

## Changed Files

- `backend/app/services/compute_input_bundle.py` (new)
- `backend/app/api/workspace.py`
- `backend/app/desktop_client/workspace_adapter.py`
- `backend/app/desktop_client/proxy.py` (strictly necessary registered-route entry)
- `backend/tests/test_compute_input_bundle.py` (new)
- `.superpowers/sdd/phase3/task-7-report.md` (this report)

## Remaining Caveats

- Phase 3 Task 7 supports A-share stock daily data only. ETF, minute data, extension datasets, broker access, trading, Telegram, OpenClaw, and Gold are explicitly outside this contract.
- Freshness currently uses a bounded calendar-day comparison against requested `end` / `as_of` (or the client date when absent). A future exchange-calendar authority could distinguish long market holidays more precisely.
- The ten-minute service cache intentionally may serve the same local snapshot for that TTL after a same-day parquet correction. The manifest and bundle hashes still identify the exact bytes delivered.
- Local input preparation always verifies the downloaded archive before replacing the read-only cache. Integrity failures do not invoke cloud computation; Task 8 will enforce the execution fallback boundary.
- The full-suite warnings are existing Polars, websockets/uvicorn, and `datetime.utcnow()` deprecations.
