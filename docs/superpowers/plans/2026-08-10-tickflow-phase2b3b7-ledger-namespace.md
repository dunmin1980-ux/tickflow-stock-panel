# TickFlow Phase 2B-3B7 Ledger Namespace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the global lifetime attempt gate with an approval-scoped ledger namespace while preserving global runtime safety and every historical byte.

**Architecture:** Keep the legacy ledger parser and files read-only. Add a strict scope identity, a scoped ledger wrapper/store, and a fail-closed namespace scanner to the existing orchestrator; keep the global lock in local Application Support and store future attempt ledgers under the report namespace. Bind all changed runtime sources into a freshly built, uninstalled Runtime Candidate.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, POSIX `flock`, fsync/atomic rename, Docker offline builds, existing TickFlow Canary Mock topology.

## Global Constraints

- Real Provider attempts and AI calls remain `0`.
- Do not read the Keychain Secret content or perform DNS, TLS, curl, or public network requests.
- Preserve Request `d59766101b63450e8148541d589a90bf` and Request `3d3e6adbe5b74c98ad18a2efe0fd1d0c` byte-for-byte.
- `ledger_namespace_version=1`.
- Global exclusive lock remains global; maximum live orchestrators is `1`.
- Retry remains `0`; maximum Provider attempts per approval scope remains `1`.
- The new Runtime Candidate is generated and verified but not installed.

---

### Task 1: Freeze Historical State And Supersede The Installed Approval

**Files:**
- Create: `reports/phase2_provider_canary/superseded/769f4d762594ac496bbaf14b90b8dd3cb7eae572ac9bf05f0484abfe3b86ffaf.json`
- Preserve: `~/Library/Application Support/TickFlowPhase2Canary/runtime-v1/**`
- Move atomically: installed non-sensitive Runtime Approval to a superseded local filename

**Interfaces:**
- Consumes: current Candidate and installed approval SHA identities.
- Produces: immutable before-hash manifest used by all later verification.

- [ ] **Step 1: Record SHA-256 and metadata for both historical ledgers, frozen report evidence, current Candidate, and installed approval.**
- [ ] **Step 2: Copy the `769f` Candidate byte-for-byte into `superseded/` and verify filename equals content SHA-256.**
- [ ] **Step 3: Atomically rename the installed approval to a `superseded-769f...` mode-0600 file and fsync its directory.**
- [ ] **Step 4: Verify no current approval remains installed and every preserved byte hash is unchanged.**

### Task 2: Add Scope Identity And Namespace Safety Tests

**Files:**
- Modify: `backend/tests/test_phase2_canary_orchestrator.py`
- Modify: `backend/app/services/phase2_canary_orchestrator.py`

**Interfaces:**
- Produces: `ApprovalScopeIdentity`, `compute_approval_scope_id`, `ApprovalScopedAttemptLedgerStore`, and namespace preflight result types.

- [ ] **Step 1: Write failing tests for deterministic scope material, Secret absence, same/different identity behavior, and strict validation.**
- [ ] **Step 2: Run those tests and confirm failures are caused by missing scope APIs.**
- [ ] **Step 3: Implement the minimal strict identity and SHA-256 computation.**
- [ ] **Step 4: Write failing tests for legacy terminal preservation, unknown history, current-scope consumption, global request-ID collision, and byte immutability.**
- [ ] **Step 5: Run those tests and confirm the namespace scanner APIs are missing.**
- [ ] **Step 6: Implement the scoped wrapper/store and fail-closed scanner without changing legacy decode/write behavior.**
- [ ] **Step 7: Run focused tests until green and refactor only duplicated validation code.**

### Task 3: Integrate Namespace Preflight With The One-shot Orchestrator

**Files:**
- Modify: `backend/tests/test_phase2_canary_orchestrator.py`
- Modify: `backend/app/services/phase2_canary_orchestrator.py`
- Modify: `backend/scripts/run_phase2_single_symbol_canary.py`

**Interfaces:**
- Consumes: scope identity and namespace scanner from Task 2.
- Produces: scoped future ledgers at `attempts/<scope>/<request>/ledger.json`.

- [ ] **Step 1: Write failing lifecycle tests proving old terminal scopes do not block, current consumed scopes do block, request-ID collisions fail before Secret access, and two concurrent scopes still share one lock.**
- [ ] **Step 2: Run the tests and confirm they fail under the single global ledger implementation.**
- [ ] **Step 3: Add explicit `attempts_root` and `historical_evidence_root` config paths.**
- [ ] **Step 4: Integrate immutable verification, scope computation, global lock, namespace scan, unique request ID, and scoped ledger creation in that order.**
- [ ] **Step 5: Update stale-lock validation to bind the scope and recover only a provable same-scope pre-dispatch terminal ledger.**
- [ ] **Step 6: Run orchestrator tests and confirm one dispatch consumes only that approval scope.**

### Task 4: Bind Candidate Lifecycle And Mock E2E

**Files:**
- Modify: `backend/app/services/phase2_canary_runtime_artifact.py`
- Modify: `backend/scripts/build_phase2_canary_runtime_offline.py`
- Modify: `backend/scripts/run_phase2_canary_local_path_mock_e2e.py`
- Modify: `backend/scripts/validate_phase2_canary_provider_config.py`
- Modify: `backend/tests/test_phase2_canary_runtime_artifact.py`
- Modify: `backend/tests/test_phase2_canary_local_path_mock_e2e.py`

**Interfaces:**
- Consumes: preserved `769f` Candidate and scoped orchestrator.
- Produces: a new uninstalled immutable Candidate and x3 Mock evidence.

- [ ] **Step 1: Write failing artifact tests requiring `769f` as the preserved prior Candidate and binding all changed runtime hashes.**
- [ ] **Step 2: Update the Candidate model/builder/verifier and exact preflight delta allowlist.**
- [ ] **Step 3: Write failing Mock tests requiring three distinct Mock-only approval scopes and global historical hash preservation.**
- [ ] **Step 4: Update the E2E harness to derive three deterministic Mock-only approval identities while verifying the real Runtime Candidate for every run.**
- [ ] **Step 5: Build images with `--network=none --pull=false --no-cache`, emit the new Candidate, and perform immutable verification.**
- [ ] **Step 6: Execute the exact local Mock x3 and verify deterministic renderer output and zero residue.**

### Task 5: Produce Offline Preflight Evidence And Reports

**Files:**
- Create: `backend/scripts/validate_phase2_canary_ledger_namespace.py`
- Create: `backend/tests/test_phase2_canary_ledger_namespace.py`
- Create: `reports/phase2_provider_canary/ledger_namespace_preflight.json`
- Create: `reports/tickflow_phase2b_canary_ledger_namespace_eval.md`

**Interfaces:**
- Consumes: real local legacy ledgers, frozen report evidence, and new uninstalled Candidate.
- Produces: non-sensitive machine evidence and final human report.

- [ ] **Step 1: Write failing tests for the offline preflight report schema and zero-external-behavior contract.**
- [ ] **Step 2: Implement the validator with selected metadata only; it must never read a Secret or call a public endpoint.**
- [ ] **Step 3: Run the validator and record historical request states, both ledger hashes, scope availability, lock state, request-ID uniqueness, and residue counts.**
- [ ] **Step 4: Generate the report with status `PHASE2B_CANARY_LEDGER_NAMESPACE_READY_FOR_REAPPROVAL`.**

### Task 6: Full Verification, Review, Commit, And Push

**Files:**
- Update only generated evidence and reports from Tasks 4-5.

**Interfaces:**
- Produces: reviewed branch head ready for hash reapproval.

- [ ] **Step 1: Run all Phase 2 focused tests and the complete backend test suite.**
- [ ] **Step 2: Run compileall, Ruff F821, `git diff --check`, sensitive-string scan, historical hash comparison, and container/network/temp residue checks.**
- [ ] **Step 3: Perform an independent focused review for retry bypass, directory bypass, false READY, stale history, lock weakening, request-ID collision, evidence mutation, and namespace collision.**
- [ ] **Step 4: Fix every actionable finding through a new failing test, then rerun verification.**
- [ ] **Step 5: Commit the implementation and push `codex/tickflow-phase2-ai-review` without creating a PR, merging, or deploying.**
- [ ] **Step 6: Record the final Head and new Candidate SHA-256; leave it uninstalled and stop.**
