# Phase 1.2 Day 2 Boundary Schema Rematerialization Plan

> Execution mode: test-driven, offline only. No TickFlow request, cloud
> deployment, credential read, AI configuration, Paper Trading, or Gold action.

**Goal:** Align the Phase 1.1 contract producer and Phase 1.2 observation
consumer on one explicit boundary schema, then rematerialize the already
completed 2026-07-28 live evidence without a second live run.

**Source evidence:** The sanitized live result remains immutable at
`/var/folders/ln/hd9n0j9910s4htp7296nwqbw0000gn/T/tickflow-phase1-observation.3FfsSk`
with SHA-256
`443f56d446f6e09849c0a47eba76041643851b5a2a394e2c2a8f22be84d42719`.

**Implementation constraint:** The schema implementation and tests are
committed first. The resulting commit SHA is then recorded truthfully as
`schema_fix_commit` by a separate offline evidence commit. This avoids a
self-referential commit hash.

## Task 1: Freeze the original blocked evidence

**Files:**

- Update:
  `reports/phase1_observation/2026-07-28/original_blocked_evidence_manifest.json`
- Add:
  `reports/phase1_observation/2026-07-28/original_blocked_daily_summary.json`
- Add:
  `reports/phase1_observation/2026-07-28/original_blocked_request_audit.json`

1. Verify the source live result and cumulative audit hashes.
2. Preserve exact byte copies of the original blocked summary and compact
   request audit.
3. Record all hashes in the immutable-evidence manifest.
4. Do not alter the source live result or cumulative request audit.

## Task 2: Add failing producer-to-consumer tests

**Files:**

- Modify: `backend/tests/test_phase1_observation.py`

1. Load the real producer module from
   `backend/scripts/validate_phase1_tickflow_contracts.py`.
2. Construct valid market contracts, but obtain `boundaries` from the real
   producer skeleton rather than an idealized hand-built fixture.
3. Assert the producer emits every canonical boundary field and schema
   version.
4. Assert canonical producer output passes directly through the consumer.
5. Add legacy-normalization pass and strict failure cases for missing or true
   per-symbol mutation evidence, AI configured, Gold enabled or sending, and
   real-key exposure.
6. Add offline rematerialization preservation, zero-request, and idempotency
   tests.
7. Run the focused tests and confirm they fail for the expected missing
   implementation.

## Task 3: Emit the canonical producer schema

**Files:**

- Modify: `backend/scripts/validate_phase1_tickflow_contracts.py`

1. Add a single producer helper for boundary schema version 1.
2. Emit these explicit top-level fields:
   `boundary_schema_version`, `cloud_redeployed`, `real_key_exposed`,
   `raw_market_values_modified`, `timestamps_shifted`,
   `missing_minutes_filled`, `ai_configured`, `paper_trading_started`,
   `integrated_gold_enabled`, and
   `integrated_gold_external_send_count`.
3. Preserve harmless legacy fields where they remain useful, but do not make
   the consumer depend on them for new evidence.

## Task 4: Normalize legacy evidence strictly

**Files:**

- Modify: `scripts/validate_phase1_observation.py`

1. Implement `normalize_boundary_evidence_v1()`.
2. Accept canonical evidence only when every canonical field is explicitly
   present with the correct type.
3. For legacy evidence, map `ai_configured_by_phase` to `ai_configured`.
4. Derive the three mutation fields only when every fixed symbol explicitly
   supplies booleans under
   `contracts.minute_to_30m.symbols.<symbol>.local_1m_to_30m.dual_first_bucket`.
5. Map nested `raw_values_modified` to canonical
   `raw_market_values_modified`; never treat a missing value as `false`.
6. Attach normalization metadata with the source evidence hash and derived
   fields.
7. Apply the existing strict security assertions to normalized canonical
   evidence.

## Task 5: Add audited offline rematerialization

**Files:**

- Modify: `scripts/validate_phase1_observation.py`
- Modify: `backend/tests/test_phase1_observation.py`

1. Add an explicit offline rematerialization mode that accepts the frozen
   manifest and source live result.
2. Verify source, original summary, compact audit, diagnosis, index snapshot,
   and cumulative audit hashes before writing.
3. Preserve the original blocked summary and audit files.
4. Materialize Day 2 as `DAY_PASSED` with:
   `evidence_origin=phase1_2_live_reuse`,
   `live_request_reexecuted=false`, `source_live_request_count=14`,
   `new_api_request_count=0`, `original_status=DAY_BLOCKED`, and
   `resolution=OFFLINE_SCHEMA_REMATERIALIZATION`.
5. Record an audited `rematerialization` object including the schema-fix
   commit, verified source hash, and zero added requests.
6. Make repeated execution with the same frozen source a no-op; reject any
   conflicting source or preserved hash.
7. Count `phase1_2_live_reuse` as a real Phase 1.2 live observation day in the
   index.
8. Add `--observation-root` as an offline summarize-compatible CLI mode so the
   specified verification command works without network access.

## Task 6: Verify and commit the schema implementation

1. Run focused tests for the two Phase 1 contract test modules.
2. Run backend full pytest, compileall, and Ruff F821.
3. Run the observation validator and Bash syntax check.
4. Scan changed reports, docs, scripts, and tests for credential shapes.
5. Confirm the cumulative request-audit hash is unchanged and no API request
   was added.
6. Commit only schema code, consumer logic, tests, and this plan as:
   `fix: align phase1 observation boundary schema`.

## Task 7: Rematerialize Day 2 and close the stage

**Files:**

- Update:
  `reports/phase1_observation/2026-07-28/daily_summary.json`
- Update: `reports/phase1_observation/observation_index.json`
- Update: `reports/tickflow_phase1_observation_status.md`
- Add: `reports/tickflow_phase1_day2_resolution.md`
- Preserve the original blocked evidence files from Task 1.

1. Run the offline rematerializer once, recording the schema implementation
   commit SHA.
2. Re-run it to prove idempotency without changing hashes or request counts.
3. Rebuild the index and verify `valid_days=2`, `remaining_days=3`,
   `reused_observation_days=1`, and
   `phase1_2_live_observation_days=1`.
4. Confirm the phase remains `PHASE1_OBSERVATION_IN_PROGRESS`.
5. Generate the resolution report and run the full verification suite again.
6. Commit only the frozen audit trail and rematerialized reports as:
   `docs: resolve phase1 day2 from frozen evidence`.
7. Stop with
   `next_action=WAIT_FOR_NEXT_REAL_TRADING_DAY_CLOSE`.
