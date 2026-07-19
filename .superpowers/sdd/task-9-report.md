# Task 9 Report: Add The Non-Activating 10-Day Observation Gate

## Status

Implemented and verified.

## Implementation

- Added `GoldObservationService.review_day(...)`, `review_restart(...)`, and `status()`.
- Counts distinct dates only when the integrity-validated canonical run passes `stage_a.thresholds_pass` and the latest day review is verified, references that run, and has a non-empty note.
- Resolves each same-day supersede graph to one complete linear chain; missing links, forks, cycles, duplicate run IDs, and malformed comparison evidence fail closed.
- Requires ten complete reviewed days plus the latest verified restart review to return `review_eligible`.
- Treats any durable external-send attempt, failed automatic tolerance, or corrupted attempt/comparison/review state as `failed`.
- Added strict append-only `observation_reviews.jsonl` storage with schema validation, fsync-backed writes, `0700` root permissions, and `0600` file permissions.
- Exposes no notification activation method or `notifications_enabled` response field.

## TDD Record

### RED

```bash
cd backend && uv run --extra dev pytest tests/test_gold_observation.py -q
```

Result: collection failed with `ModuleNotFoundError: No module named 'app.services.gold_observation'` before the service existed.

### GREEN

```bash
cd backend && uv run --extra dev pytest tests/test_gold_observation.py -q
```

Result: `21 passed in 0.54s`.

## Verification

```bash
cd backend && uv run --extra dev pytest tests/test_gold_observation.py \
  tests/test_gold_shadow_store.py tests/test_gold_comparison_runs.py \
  tests/test_gold_shadow_compare.py tests/test_gold_external_guard.py -q
```

Result: `146 passed in 1.24s`.

```bash
cd backend && uv run --extra dev ruff check \
  app/services/gold_observation.py app/services/gold_shadow_store.py \
  tests/test_gold_observation.py
```

Result: `All checks passed!`.

## Self-Review

- Confirmed the result status can only be `collecting`, `failed`, or `review_eligible`.
- Confirmed nine days, duplicate reviews, missing day reviews, unverified reviews, superseded reviews, and missing restart review cannot satisfy the gate.
- Confirmed failed automatic tolerances cannot be overridden by manual reviews, while a later passing canonical run replaces a superseded failed run and requires its own review.
- Confirmed malformed review, comparison, run-integrity, and external-attempt state cannot produce eligibility.
- Confirmed the implementation does not import or call trading, publishing, Telegram, Feishu, or generic notification adapters.
- Confirmed passing the gate only reports review eligibility and never enables notifications.

## Concerns

No Task 9 blocking concerns. A repository-wide `uv run --extra dev ruff check .` reports
1,213 pre-existing violations in unrelated backend files; the Task 9 targeted Ruff command is clean.

## Blocking Review Fixes

### Trading-Day Calendar RED

```bash
cd backend && uv run --extra dev pytest tests/test_gold_observation.py \
  -k "weekend or configured_holiday or unavailable_or_corrupt_calendar or symlinked_calendar" -q
```

Result: `7 failed, 21 deselected in 0.19s`. Weekend and configured-holiday reviews were
accepted, and missing, malformed, invalid, and symlinked calendars left the gate collecting.

### Trading-Day Calendar GREEN

The first implementation run exposed an incorrect condition ordering with
`2 failed, 5 passed, 21 deselected in 0.17s`: excluded non-trading dates were mislabeled as
automatic tolerance failures. After separating calendar exclusion from tolerance failure, the
same focused command passed with `7 passed, 21 deselected in 0.10s`.

The final suite also covers persisted weekend/holiday review records, deterministic unreadable
calendar failure, and the stable `trading_calendar_unavailable` reason.

### Review Storage RED

```bash
cd backend && uv run --extra dev pytest tests/test_gold_observation.py \
  -k "review_file or review_root or root_swap_before_review" -q
```

Result: `5 failed, 28 deselected in 0.18s`. The path-based store followed live and dangling
review-file/root symlinks, redirected an append outside during a root swap, and consumed outside
review content during a read swap.

### Review Storage GREEN

The same focused command passed with `5 passed, 28 deselected in 0.08s` after changing only
observation review persistence to hold a no-follow root directory descriptor and use `dir_fd`
relative no-follow file opens with `0600`/`0700` modes and file plus directory fsync.

### Final Review-Fix Verification

```bash
cd backend && uv run --extra dev pytest tests/test_gold_observation.py -q
```

Result: `35 passed in 0.68s`.

```bash
cd backend && uv run --extra dev pytest tests/test_gold_observation.py \
  tests/test_gold_shadow_store.py tests/test_gold_comparison_runs.py \
  tests/test_gold_shadow_compare.py tests/test_gold_external_guard.py \
  tests/test_gold_shadow.py -q
```

Result: `189 passed in 1.47s`.

```bash
cd backend && uv run --extra dev ruff check \
  app/services/gold_observation.py app/services/gold_shadow_store.py \
  tests/test_gold_observation.py
```

Result: `All checks passed!`.
