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

## Review Follow-up (base `00de851`)

### Hardened Boundaries

1. Configuration path rejection now covers path-bearing keys, absolute and relative paths,
   Windows drive-relative paths such as `C:foo.parquet`, and bare executable/data filenames such
   as `private.parquet` and `model.pkl`. Domain values including symbols, ISO dates, enums,
   timeframes, and indicator expressions remain accepted.
2. Canonical configuration now records the validated Pydantic `model_fields_set`. Omitted `start`
   retains the bounded 180-day default while explicit `start: null` retains unbounded-history
   semantics; the two forms now have different parameter digests, server cache entries, and client
   cache keys.
3. Backtest builds fail closed unless daily and enriched partition-date sets match. Explicit start
   coverage must be present. The manifest now records `coverage_start`, `coverage_end`, and sorted
   `partition_dates`; the desktop client independently reconciles those fields against archive
   members and task-required roots.
4. Client archive `fstat`, whole-ZIP SHA-256, and `ZipFile` reads now use the same open file
   description with no-follow semantics. Server source copies traverse the four whitelisted roots
   through no-follow directory descriptors, then `fstat`, hash, and copy bytes from the same source
   descriptor.
5. The ten-minute process-local master cache is bounded to four entries and 2 GiB by default, with
   deterministic oldest-entry eviction and immediate expiry cleanup. Oversized masters are served
   through a unique lease without being retained. Streaming responses clean leases in generator
   finalization and a background fallback; application shutdown closes the service and removes its
   temporary root.
6. Atomic publication retains the prior cache as a uniquely named backup until the new target has
   final read-only permissions. Replace or permission failure restores the old target. If the OS
   also rejects restoration, the code preserves the backup and keeps a recoverable target instead
   of deleting both trees.

### Follow-up TDD Evidence

1. Review RED:
   - Command: `uv run --project backend pytest -q backend/tests/test_compute_input_bundle.py`
   - Result: `18 failed, 43 passed`; failures mapped to the six review findings.
2. Task 7 GREEN:
   - Same command after implementation.
   - Result: `61 passed in 3.55s`; final repeat after publication hardening:
     `61 passed in 3.91s`.
3. Workspace and desktop regression:
   - Command: `uv run --project backend pytest -q backend/tests/test_compute_input_bundle.py backend/tests/test_desktop_lifecycle.py backend/tests/test_desktop_cloud_proxy.py backend/tests/test_desktop_client_auth.py backend/tests/test_desktop_workspace_adapter.py backend/tests/test_desktop_paths.py backend/tests/workspace`
   - Result: `355 passed, 2 warnings in 9.78s`.
4. Full backend:
   - Command: `uv run --project backend pytest -q backend/tests`
   - Result: `1143 passed, 11 warnings in 51.28s`.
   - A final managed-sandbox rerun reached `1141 passed` and only the two localhost-binding
     lifecycle cases failed with sandbox `PermissionError`; the complete lifecycle file was then
     rerun outside the network sandbox and passed `6 passed, 2 warnings in 6.41s`.
5. Ruff:
   - Strict Task 7 files excluding legacy `main.py` debt: `All checks passed!`.
   - All changed backend files with the repository's established `RUF100,RUF003` legacy ignores:
     `All checks passed!`.
6. Diff validation:
   - Command: `git diff --check`
   - Result: exit `0`, no output.

### Follow-up Caveats

- Explicit non-null backtest `start` is intentionally fail closed when that exact local partition is
  absent. Exchange-calendar-aware normalization remains a future improvement for weekend or holiday
  start dates.
- Cache limits and the build semaphore remain process-local. They do not claim cross-worker or
  cross-container coordination.
- Restoration after an underlying filesystem refuses both publish and rollback cannot guarantee the
  old directory name, but recovery bytes are preserved rather than cleaned up.
- No frontend, proxy, broker, Telegram, OpenClaw, Gold, minute-data, or external-provider behavior
  was changed in this review follow-up.

## Review Follow-up 2 (base `2907739`)

### Closed Integrity Gaps

1. Configuration path rejection no longer depends on an extension allowlist or denylist. It
   recursively checks `params`, `overrides`, lists, and nested mappings; rejects path-bearing keys,
   any slash or backslash, POSIX/drive/file-URI forms, and bare filenames by structurally parsing a
   non-empty extension of arbitrary length. Explicit A-share symbols such as `000403.SZ` and
   `600489.SH`, ISO dates, normal strategy identifiers, enums, and condition expressions remain
   accepted. Tests cover `weights.onnx`, `config.yaml`, `script.sh`, long and Unicode extensions,
   Unicode and space-containing paths, and nested `params` / `overrides` values.
2. The default 180-calendar-day backtest window now has a deterministic conservative coverage
   contract. Daily and enriched date sets must still be identical, the first partition may trail
   `effective_start` by at most seven calendar days, and adjacent common partition dates may differ
   by at most seven calendar days. Explicit non-null `start` remains exact. The manifest records the
   policy, effective start, thresholds, calendar basis, and the lack of an authoritative exchange
   calendar; the client recomputes and enforces the same policy before publication.
3. `prepare_compute_input()` retains the descriptor returned by `mkstemp`, streams into that same
   descriptor, flushes and `fsync`s it, verifies that the temporary pathname still identifies the
   same regular-file device/inode, rewinds it, and passes the same open file description into ZIP
   hash and install validation. Failures close the descriptor and remove only the original owned
   temporary file. Symlink and regular-file pathname replacement tests fail closed without deleting
   attacker-owned replacements.
4. `ComputeInputFile.path` now has a strict Pydantic validator for a non-empty canonical relative
   POSIX path with no absolute form, traversal, backslash, NUL, duplicate separators, or dot
   segments. Malformed manifest entries are translated to `ComputeInputIntegrityError` by the
   desktop client before any extraction or publication.

### Follow-up 2 TDD Evidence

1. Review RED:
   - Command: `uv run --project backend pytest -q backend/tests/test_compute_input_bundle.py`
   - Result: `28 failed, 71 passed in 7.77s`; failures covered all four review findings.
2. Task 7 GREEN:
   - Same command after implementation and expanded path cases.
   - Result: `103 passed in 4.98s`.
3. Workspace and desktop regression, run outside the network sandbox for lifecycle port binding:
   - Command: `uv run --project backend pytest -q backend/tests/test_compute_input_bundle.py backend/tests/test_desktop_lifecycle.py backend/tests/test_desktop_cloud_proxy.py backend/tests/test_desktop_client_auth.py backend/tests/test_desktop_workspace_adapter.py backend/tests/test_desktop_paths.py backend/tests/workspace`
   - Result: `397 passed, 2 warnings in 11.03s`.
4. Full backend, run outside the network sandbox:
   - Command: `uv run --project backend pytest -q backend/tests`
   - Result: `1185 passed, 11 warnings in 53.08s`.
5. Ruff and diff validation:
   - Command: `uv run --project backend ruff check backend/app/services/compute_input_bundle.py backend/app/desktop_client/workspace_adapter.py backend/tests/test_compute_input_bundle.py`
   - Result: `All checks passed!`.
   - Command: `git diff --check`
   - Result: exit `0`, no output.

### Follow-up 2 Caveats

- The seven-day start tolerance and maximum partition gap are intentionally conservative
  calendar-day heuristics, not an authoritative A-share exchange calendar. An exceptional closure
  longer than seven calendar days will fail closed until an exchange-calendar authority is added.
- Explicit non-null `start` remains exact, so a requested weekend or market-holiday date without a
  partition is rejected rather than normalized to a nearby trading day.
- If another process replaces the temporary download pathname, TickFlow closes its original file
  descriptor but deliberately leaves the replacement untouched; this prevents cleanup from
  following or deleting an attacker-owned path.
- This follow-up changes only the Task 7 backend service, desktop adapter, tests, and this report.
  Concurrent frontend changes visible in the shared worktree are unrelated and remain unstaged.
