# Task 10 Report: Expose Authenticated Gold APIs And Wire The Lifespan

## Status

Implemented and verified.

## Implementation

- Added the always-registered `/api/gold` router with the eleven approved routes and exact
  comparison/review request models.
- Disabled status and health remain readable, disabled lists return empty `rows` without creating
  Gold storage, and disabled writes return `409` with `gold_workspace_disabled` before body
  validation.
- Reused the existing application authentication middleware for upload, comparison, and
  observation-review writes.
- Added `1..1000` validation to every Gold list endpoint and explicit response projection for
  legacy uploads so raw content cannot be returned.
- Added `GOLD_WORKSPACE_ENABLED=false` and initialized all five `app.state.gold_*` service
  references only when the flag is enabled.
- Registered `gold_sampler_5m` on the shared scheduler. When the scheduler is unavailable, the
  Gold API remains readable and health records `scheduler_unavailable`.

## TDD Record

### RED

```bash
cd backend && uv run --extra dev pytest tests/test_gold_api.py tests/test_gold_runtime.py -q
```

The initial run failed because every Gold route returned `404`, `Settings` had no
`gold_workspace_enabled` field, and `app.main` had no `_initialize_gold_runtime` function. After
correcting a test-only Pydantic monkeypatch setup, the focused runtime rerun produced four expected
feature-missing failures.

### GREEN

```bash
cd backend && uv run --extra dev pytest tests/test_gold_api.py tests/test_gold_runtime.py -q
```

Result: `27 passed in 2.77s`.

## Verification

```bash
cd backend && uv run --extra dev pytest tests/test_gold_*.py -q
```

Result: `294 passed in 17.00s`.

```bash
cd backend && uv run --extra dev ruff check \
  app/api/gold.py tests/test_gold_api.py tests/test_gold_runtime.py
```

Result: `All checks passed!`.

```bash
cd backend && uv run --extra dev python -m compileall -q \
  app/api/gold.py app/config.py app/main.py
```

Result: exit zero.

## Self-Review

- Confirmed the router is registered regardless of the feature flag and all exact route/method
  pairs are present.
- Confirmed disabled API calls neither construct `GoldShadowStore` nor create
  `user_data/gold_shadow`.
- Confirmed all write workflows are covered by existing-cookie authentication regressions and no
  Gold-specific authentication bypass was added.
- Confirmed runtime wiring uses only `DisabledGoldNotifier`, the fixed-symbol TickFlow gateway,
  the shared scheduler, and the five specified `app.state` references.
- Confirmed tests use fake API dependencies or local service objects, with no live TickFlow,
  external-send, broker, OpenClaw, publishing, or platform notification calls.
- Confirmed no QuoteService, generic monitor, CORS, normal non-Gold router, or platform notification
  behavior changed.

## Concerns

No Task 10 blocking concerns. Broad Ruff checks of the two pre-existing modified modules still
report the same baseline lint debt: 23 findings in `app/main.py` and 3 in `app/config.py`. The
Task 10 router and tests are clean, and the baseline/current broad finding counts are unchanged.

## Review Fix: Scheduler Registration Isolation

### RED

```bash
cd backend && uv run --extra dev pytest tests/test_gold_runtime.py \
  -k "scheduler_registration_failure or unrelated_gold_initialization_failure" -q
```

Result: `1 failed, 1 passed, 4 deselected in 3.97s`. A non-`None` shared scheduler whose
`add_job` raised `RuntimeError("scheduler-registration-secret")` escaped through TestClient
lifespan startup before `yield`, so neither the ordinary probe API nor the Gold API became
reachable. The companion test confirmed unrelated Gold store-construction failures already
propagated.

### GREEN

The registration call is now the only added exception boundary. It records
`scheduler_unavailable` with the fixed message `Gold sampler scheduler is unavailable`, logs no
exception details, and leaves all initialized Gold read services available.

```bash
cd backend && uv run --extra dev pytest tests/test_gold_runtime.py \
  -k "scheduler_registration_failure or unrelated_gold_initialization_failure" -q
```

Result: `2 passed, 4 deselected in 1.95s`. The lifespan completed, the ordinary probe and Gold
status routes both returned `200`, and neither API output nor persisted health contained the
injected scheduler exception text.

### Final Verification

```bash
cd backend && uv run --extra dev pytest tests/test_gold_api.py tests/test_gold_runtime.py -q
```

Result: `29 passed in 3.24s`.

```bash
cd backend && uv run --extra dev pytest tests/test_gold_*.py -q
```

Result: `296 passed in 17.53s`.

```bash
cd backend && uv run --extra dev ruff check \
  app/api/gold.py tests/test_gold_api.py tests/test_gold_runtime.py
```

Result: `All checks passed!`.

```bash
cd backend && uv run --extra dev python -m compileall -q \
  app/api/gold.py app/config.py app/main.py
```

Result: exit zero.
