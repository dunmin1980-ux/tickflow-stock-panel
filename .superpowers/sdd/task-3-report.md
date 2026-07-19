# Task 3 Report: Add The Gold-Only External Send Guard

## Status

Implemented and committed in the final task commit reported by git.

## Implementation

- Added `GoldExternalSendDenied`, `GoldNotificationPort`, `DisabledGoldNotifier`, and module-level `attempt_count(root)` in `backend/app/services/gold_external_guard.py`.
- `DisabledGoldNotifier.send(channel, payload)` validates the channel, discards the payload, durably records only `schema_version`, UTC `attempted_at`, and `channel`, then always raises `GoldExternalSendDenied`.
- The guard accepts a Gold storage root directly and never imports global settings, generic alerts, notification adapters, broker integrations, OpenClaw, or publishing code.
- Attempt storage uses the source revision's lowercase channel regex, `O_NOFOLLOW`, `0600` files, private `0700` roots, file and directory `fsync`, and validated durable reads.
- Malformed attempt rows, invalid channels, and symlinked attempt files fail closed rather than being counted as valid state.

## TDD Evidence

### RED

Added `backend/tests/test_gold_external_guard.py` first, then ran:

```bash
cd backend && uv run --extra dev pytest tests/test_gold_external_guard.py -q
```

Result: collection failed with `ModuleNotFoundError: No module named 'app.services.gold_external_guard'`.

### GREEN

After implementing the guard:

```bash
cd backend && uv run --extra dev pytest tests/test_gold_external_guard.py -q
```

Result: `11 passed in 0.07s`.

## Verification

```bash
cd backend && uv run --extra dev ruff check app/services/gold_external_guard.py tests/test_gold_external_guard.py
```

Result: `All checks passed!`

```bash
cd backend && uv run --extra dev pytest -q
```

Result: `443 passed, 9 warnings in 59.15s`.

The warnings are existing dependency deprecations unrelated to this task.

```bash
git diff --check
```

Result: clean.

## Self-Review

- Confirmed every production send path in this module raises after recording; no external adapter is called.
- Confirmed payloads and nested secret values are never serialized or logged.
- Confirmed the public root-based API does not read global notification settings.
- Confirmed malformed state raises instead of returning a potentially understated count.
- Confirmed symlink refusal and permission/fsync behavior are covered by focused tests.
- Confirmed the required interface name `GoldExternalSendDenied` is retained; its lint exception is narrowly scoped.

## Concerns

No blocking concerns. Full-suite output contains 9 existing deprecation warnings; no new warnings were introduced by this task.

## Task 3 Review Fixes

### RED

Added regression tests for dangling attempt-file symlinks and symlinked configured roots, then ran:

```bash
cd backend && uv run --extra dev pytest tests/test_gold_external_guard.py -q
```

Result: `2 failed, 11 passed in 0.17s`.

The dangling-file test showed `attempt_count()` incorrectly returned zero. The configured-root test showed `attempt_count()` incorrectly returned zero and exposed that `send()` could write through the root symlink.

### GREEN

Added lstat-based symlink rejection for the configured root before and after directory creation, and for attempt-file entries before the missing-file fast path. Then ran:

```bash
cd backend && uv run --extra dev pytest tests/test_gold_external_guard.py -q
```

Result: `13 passed in 0.07s`.

```bash
cd backend && uv run --extra dev ruff check app/services/gold_external_guard.py tests/test_gold_external_guard.py
```

Result: `All checks passed!`

### Files Changed

- `backend/app/services/gold_external_guard.py`: reject symlinked configured roots and dangling or live symlinked attempt files with `lstat` before any count or write operation.
- `backend/tests/test_gold_external_guard.py`: add RED/GREEN coverage for both `send()` and `attempt_count()` symlink cases, including the no-outside-write assertion.
- `.superpowers/sdd/task-3-report.md`: append this review-fix evidence.

## Task 3 Re-Review TOCTOU Fix

### RED

Added `test_root_swap_before_attempt_open_cannot_redirect_send` with a deterministic `os.open` hook that swaps the configured root path immediately before the attempt-file write-open, then ran:

```bash
cd backend && uv run --extra dev pytest tests/test_gold_external_guard.py -q
```

Result: `1 failed, 13 passed in 0.35s`.

The failure showed the prior path-based implementation created `outside/external_send_attempts.jsonl` after the root was swapped.

### GREEN

Changed root handling to open the Gold directory with `O_DIRECTORY | O_NOFOLLOW`, use `fchmod` on the trusted directory descriptor, open/read `external_send_attempts.jsonl` relative to that descriptor with `O_NOFOLLOW`, and fsync both the file and trusted directory descriptor. Then ran:

```bash
cd backend && uv run --extra dev pytest tests/test_gold_external_guard.py -q
```

Result: `14 passed in 0.10s`.

```bash
cd backend && uv run --extra dev ruff check app/services/gold_external_guard.py tests/test_gold_external_guard.py
```

Result: `All checks passed!`

### Files Changed

- `backend/app/services/gold_external_guard.py`: replace path-based root/file operations with descriptor-relative, no-follow opens and trusted-descriptor fsyncs.
- `backend/tests/test_gold_external_guard.py`: add deterministic root-swap TOCTOU regression coverage.
- `.superpowers/sdd/task-3-report.md`: append this re-review evidence.
