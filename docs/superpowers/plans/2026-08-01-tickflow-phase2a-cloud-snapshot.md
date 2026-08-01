# TickFlow Phase 2A Existing Cloud Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Materialize the approved existing cloud-store snapshot without API or cloud writes, compute traceable daily indicators for the fixed three symbols, and reach `PHASE2A_FACTS_READY` only after all contracts pass.

**Architecture:** A network-free snapshot service reads an explicit data-store allowlist and emits a canonical bundle. One CLI supports remote stdout export and local atomic materialization; an independent validator checks the materialized snapshot. The existing Facts builder then consumes the validated snapshot, calls the repository's official indicator implementation, and preserves Phase 1 contract evidence as a separate domain.

**Tech Stack:** Python 3.12, Polars, pytest, canonical JSON/SHA-256, existing `app.indicators.pipeline.compute_indicators`, SSH/docker only as a read-only transport for the approved capture.

## Global Constraints

- Fixed symbols: `000403.SZ`, `600489.SH`, `300059.SZ`.
- Source type: `existing_cloud_store_snapshot`.
- `phase1_original_payload_recovered=false` and `phase1_byte_equivalent=false`.
- Cloud access is read-only; cloud mutation count and new TickFlow request count remain zero.
- Do not read or emit credentials, connection strings, hostnames, private addresses, headers, cookies, sessions, or tokens.
- Do not modify Phase 1 evidence or request audits.
- Do not call AI, configure AI keys, start Paper Trading, write Obsidian, deploy/restart cloud services, or enable notification/Gold integrations.
- Use qfq prices and raw volume with the existing indicator implementation; key levels remain frozen.

---

### Task 1: Canonical Snapshot Contract

**Files:**
- Create: `backend/app/services/phase2_snapshot.py`
- Create: `backend/tests/test_phase2_snapshot.py`

**Interfaces:**
- Produces: `build_snapshot_bundle(data_dir: Path, captured_at: str) -> dict[str, Any]`
- Produces: `validate_snapshot_bundle(bundle: Mapping[str, Any]) -> list[str]`
- Produces: `materialize_snapshot_bundle(bundle: Mapping[str, Any], output_dir: Path) -> dict[str, Any]`
- Produces: `validate_snapshot_directory(snapshot_dir: Path) -> dict[str, Any]`

- [x] **Step 1: Write failing contract tests**

Create fixture partitions and tests for fixed symbols, provider/job evidence,
251 aligned rows, ordering, duplicates, future dates, OHLCVA validity,
normalized hashes, source type, false Phase 1 equivalence, zero counters, and
secret/trading-field rejection.

- [x] **Step 2: Verify red**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_snapshot.py -q
```

Expected: collection/import failure because `app.services.phase2_snapshot` does
not exist.

- [x] **Step 3: Implement the minimal snapshot service**

Read only the approved paths, select the successful same-day job, normalize the
six series, calculate source/output/business hashes, validate contracts, and
atomically publish the directory. Reject symlinks and paths outside the supplied
data directory.

- [x] **Step 4: Verify green**

Run the Task 1 test command. Expected: all snapshot tests pass.

- [x] **Step 5: Run changed-file Ruff**

```bash
.venv/bin/ruff check app/services/phase2_snapshot.py tests/test_phase2_snapshot.py
```

Expected: `All checks passed!`

### Task 2: Read-Only Export CLI And Official Snapshot

**Files:**
- Create: `backend/scripts/export_phase2_source_snapshot.py`
- Modify: `backend/tests/test_phase2_snapshot.py`
- Generate: `reports/phase2_source_snapshot/2026-07-31/manifest.json`
- Generate: `reports/phase2_source_snapshot/2026-07-31/*_raw_daily.json`
- Generate: `reports/phase2_source_snapshot/2026-07-31/*_qfq_daily.json`
- Generate: `reports/phase2_source_snapshot/2026-07-31/snapshot_validation.json`

**Interfaces:**
- CLI source mode: `--data-dir PATH --captured-at ISO --stdout-bundle`
- CLI materialize mode: `--stdin-bundle --output-dir PATH`

- [x] **Step 1: Write failing CLI and idempotency tests**

Assert stdout mode writes no source-store file, stdin mode atomically publishes,
two captures have identical normalized business/output hashes, failure preserves
the previous directory, and the CLI AST imports no HTTP/TickFlow/AI client.

- [x] **Step 2: Verify red**

Run the focused snapshot tests and observe the missing CLI failure.

- [x] **Step 3: Implement the two-mode CLI**

The source mode emits one canonical bundle. The materialize mode accepts only a
validated bundle and publishes files locally. Both print a compact sanitized
result and never print source rows during normal operation.

- [x] **Step 4: Capture twice through read-only SSH transport**

Stream the exporter into the running container, pipe its stdout into the local
materializer, then repeat into a temporary local directory. Do not create a
remote file or invoke an application API/task.

- [x] **Step 5: Compare captures and verify the official snapshot**

Run the offline directory validator. Require identical normalized business and
six output hashes, source hashes, zero request/mutation counters, and no secret
matches.

- [x] **Step 6: Commit snapshot source**

```bash
git add docs/superpowers/specs/2026-08-01-tickflow-phase2a-cloud-snapshot-design.md \
  docs/superpowers/plans/2026-08-01-tickflow-phase2a-cloud-snapshot.md \
  backend/app/services/phase2_snapshot.py \
  backend/scripts/export_phase2_source_snapshot.py \
  backend/tests/test_phase2_snapshot.py \
  reports/phase2_source_snapshot/2026-07-31
git commit -m "feat: add existing cloud snapshot source for phase2 facts"
```

### Task 3: Traceable Indicator Facts

**Files:**
- Modify: `backend/app/services/phase2_facts.py`
- Modify: `backend/scripts/build_phase2_facts.py`
- Modify: `backend/scripts/validate_phase2_facts.py`
- Modify: `backend/tests/test_phase2_facts.py`
- Regenerate: `reports/phase2_facts/*.json`

**Interfaces:**
- Facts builder consumes the validated snapshot directory under
  `reports/phase2_source_snapshot/2026-07-31`.
- `build_symbol_facts(repo_root: Path, symbol: str) -> dict[str, Any]` remains
  the public Facts interface.

- [ ] **Step 1: Replace blocked-value expectations with failing ready-state tests**

Assert daily OHLCVA, MA5/10/20/60, MACD, RSI6/14, BOLL, ATR14, volume MA/ratio,
source separation, implementation metadata, parameters, input fields, price
basis, precision, dimensionless ratio, pending units, and complete numeric
provenance.

- [ ] **Step 2: Verify red**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_facts.py -q
```

Expected: failures because current Facts intentionally contain null indicators
and `BLOCKED_SOURCE_EVIDENCE`.

- [ ] **Step 3: Implement snapshot-backed calculation**

Load canonical qfq OHLC and raw volume, call
`app.indicators.pipeline.compute_indicators` with the exact required columns,
and map the latest finite values into Facts. Record direct and deterministic
provenance; keep key levels empty and units pending.

- [ ] **Step 4: Extend independent validation**

Recompute all values from the hashed snapshot and reject wrong source type,
equivalence flags, manifest/file hashes, date sets, basis, parameters,
provenance, units, nonfinite values, nonzero request/mutation counters, and
trading fields.

- [ ] **Step 5: Verify focused Facts tests and idempotency**

Run the focused Facts suite, build twice, compare complete output hashes, and
run the independent Facts directory validator.

### Task 4: Regression, Report, And Delivery

**Files:**
- Modify: `reports/tickflow_phase2a_facts_eval.md`
- Regenerate: `reports/phase2_facts/`

**Interfaces:**
- Produces final state `PHASE2A_FACTS_READY` only if every approved gate passes.

- [ ] **Step 1: Verify Phase 1 immutability**

Recompute the established Phase 1 evidence and request-audit aggregate hashes;
require exact equality with the pre-change values.

- [ ] **Step 2: Run full verification**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_snapshot.py tests/test_phase2_facts.py -q
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python -m compileall -q app
.venv/bin/ruff check app --select F821
```

Also run changed-file Ruff, both offline validators, idempotency checks, and
targeted secret/trading scans.

- [ ] **Step 3: Update the evaluation report**

Record the independent source decision, non-equivalence, read-only proof, task
metadata, date ranges, counts, hashes, calculation metadata, pending units,
test counts, zero external actions, and final status.

- [ ] **Step 4: Commit indicator Facts**

```bash
git add backend/app/services/phase2_facts.py \
  backend/scripts/build_phase2_facts.py \
  backend/scripts/validate_phase2_facts.py \
  backend/tests/test_phase2_facts.py \
  reports/phase2_facts \
  reports/tickflow_phase2a_facts_eval.md
git commit -m "feat: calculate traceable phase2 technical indicators"
```

- [ ] **Step 5: Push and verify the Fork head**

Push `codex/tickflow-phase2-ai-review` to the writable `fork` remote. Require a
clean worktree and exact equality between local HEAD and the Fork branch HEAD.
Do not create a PR, merge, or deploy.
