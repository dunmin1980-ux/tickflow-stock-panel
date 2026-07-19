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
