# Task 11 Report: Add Frontend Gold Contracts And Defensive Normalization

## Status

Implemented and verified.

## Implementation

- Added typed Gold API contracts and the ten approved `api.gold*` methods.
- Added six Gold React Query keys following the existing key factory convention.
- Added `normalizeGoldStatus` and `normalizeGoldGate` for defensive UI-boundary validation.
- Enabled status is accepted only for `600489.SH` TickFlow snapshots with finite metrics,
  non-negative integer send counts, and recognized Gold states and candidate signals.
- Disabled status canonicalizes to safe inert data. Observation gates accept only `collecting`,
  `failed`, or `review_eligible`; activation-like values are rejected.
- Legacy imports retain `FormData`, comparison run IDs are URI encoded, and no activation,
  trading, or notification UI contract was introduced.

## TDD Record

### RED

```bash
cd frontend && node --experimental-strip-types scripts/test-gold-contracts.mjs
```

Result: failed with `ERR_MODULE_NOT_FOUND` for `src/lib/gold.ts`, confirming the source-level
contract script exercised a missing implementation.

### GREEN

```bash
cd frontend && node --experimental-strip-types scripts/test-gold-contracts.mjs
```

Result: exit zero. The script covers disabled canonicalization, fixed-symbol/source validation,
finite and non-negative counter validation, recognized signals, and gate-state rejection.

## Verification

```bash
cd frontend && node --experimental-strip-types scripts/test-gold-contracts.mjs
```

Result: exit zero.

```bash
cd frontend && corepack pnpm build
```

Result: exit zero. TypeScript and Vite production build completed successfully.

## Self-Review

- Inspected the Task 10 routes, disabled responses, snapshot schema, comparison metadata, and
  observation-gate service output before defining frontend types.
- Confirmed all required Gold API paths, wrappers, methods, and query keys match the task brief.
- Confirmed the normalizers copy accepted values into fresh objects and fail closed for malformed
  enabled status data and unknown gate states.
- Confirmed no unrelated request helper changes and no Gold activation, trading, or notification
  controls were added.
- Ran `git diff --check`; it completed without whitespace errors.

## Concerns

No Task 11 blocking concerns. The Vite build reports the repository's existing large-chunk warning;
this task does not add a route or bundle entry and does not change chunking.

## Review Fix: Backend-Valid Zero Boundaries And Table-Driven Contracts

### RED

```bash
cd frontend && node --experimental-strip-types scripts/test-gold-contracts.mjs
```

Result: exit 1 with the added zero-boundary assertion:

```text
AssertionError [ERR_ASSERTION]: Expected values to be strictly deep-equal:
actual: [ undefined, undefined ]
expected: [ 0, 0 ]
```

This proved that both `quote_ts=0` and `native_ema60=0` were rejected by unsupported positivity
checks.

### GREEN

Removed positivity checks only for `quote_ts` and `native_ema60`; `quote_ts` remains an integer,
all numeric fields remain finite, and positivity remains required for `price`, `previous_close`,
and `legacy_reference_60`.

```bash
cd frontend && node --experimental-strip-types scripts/test-gold-contracts.mjs
```

Result: exit zero. The table-driven script rejects `NaN` and `Infinity` for every snapshot numeric
field, wrong sources, unknown states and signals, and fractional or negative send counts while
accepting both valid zero boundaries and retaining disabled/gate/activation-state coverage.

```bash
cd frontend && corepack pnpm build
```

Result: exit zero. TypeScript and Vite production build completed successfully.
