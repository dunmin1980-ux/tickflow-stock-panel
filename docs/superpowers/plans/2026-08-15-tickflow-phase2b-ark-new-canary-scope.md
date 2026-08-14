# TickFlow Phase 2B Ark New Canary Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a new, isolated Ark AI Canary Candidate and Scope bound to the successful historical TLS Probe without any live Provider activity.

**Architecture:** Extend the Ark Candidate with a versioned immutable binding contract while preserving schema-v1 historical parsing. A dedicated offline builder validates the local TLS receipt, preserves current artifacts, publishes the version-2 Candidate and Scope atomically, and records only non-sensitive preflight evidence.

**Tech Stack:** Python 3.12, Pydantic contracts, pytest, Docker image inspection, Git content hashes.

## Global Constraints

- No DNS, TCP, TLS, HTTP, Provider, or AI request.
- Do not read Keychain Secret content; presence check only.
- Provider attempts and AI calls remain `0`.
- Retry remains `0`; maximum Provider attempts remains `1`.
- Preserve every historical request, probe, ledger, receipt, and rejected file byte-for-byte.
- Do not install an Approval, run a Canary, configure Paper Trading, write to the real Obsidian Vault, deploy cloud code, or enable Integrated Gold.

---

### Task 1: Versioned Candidate and TLS binding contract

**Files:**
- Modify: `backend/app/providers/ark_contract.py`
- Modify: `backend/app/services/phase2_canary_orchestrator.py`
- Test: `backend/tests/test_ark_approval_scope.py`
- Test: `backend/tests/test_ark_contract_artifact.py`

- [ ] Add failing tests for schema-v2 required fields, exact per-file bindings, TLS Probe identity, Candidate/Scope source-Head equality, and schema-v1 historical compatibility.
- [ ] Run the focused tests and confirm they fail because schema-v2 support is absent.
- [ ] Add the minimal schema-v2 builder and validator while leaving schema-v1 historical parsing unchanged.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Offline preparation builder

**Files:**
- Create: `backend/scripts/build_phase2_ark_new_canary_scope_offline.py`
- Create: `backend/tests/test_phase2_ark_new_canary_scope.py`

- [ ] Add failing tests for TLS receipt validation, immutable historical hashes, superseded preservation, atomic publication, zero-attempt Scope isolation, and secret-presence-only checks.
- [ ] Run the new test file and confirm the expected failures.
- [ ] Implement the offline-only builder with no network-capable operation.
- [ ] Run the new test file and confirm it passes.

### Task 3: Freeze source identity

**Files:**
- Create: `reports/phase2_provider_ark/tls_probe_v2/success_evidence_binding.json`
- Create: content-addressed files under `reports/phase2_provider_ark/superseded/`

- [ ] Generate the TLS binding from the preserved local receipt without modifying the receipt.
- [ ] Verify historical hashes before and after generation.
- [ ] Run focused tests, compileall, Ruff F821, and `git diff --check`.
- [ ] Commit and push the source changes; record that commit as the Candidate source Head.

### Task 4: Generate and verify Candidate and Scope

**Files:**
- Modify: `reports/phase2_provider_ark/approval_candidate.json`
- Modify: `reports/phase2_provider_ark/approval_scope_preflight.json`
- Modify: `reports/phase2_provider_ark/artifact_generation.json`
- Create: `reports/phase2_provider_ark/new_canary_scope/offline_preflight.json`
- Create: `reports/phase2_provider_ark/new_canary_scope/verification.json`
- Create: `reports/phase2_provider_ark/new_canary_scope/approval_preparation_eval.md`

- [ ] Run the builder once in write mode with the committed source Head.
- [ ] Verify Candidate self-hash, complete bindings, TLS evidence, generation manifest, and new Scope availability.
- [ ] Check Keychain presence without reading content and confirm container/network/temporary residue is zero.
- [ ] Run Ark focused tests, compileall, Ruff F821, and backend full because shared runtime parsing changed.

### Task 5: Independent review and evidence commit

**Files:**
- Modify: `reports/phase2_provider_ark/new_canary_scope/verification.json`
- Modify: `reports/phase2_provider_ark/new_canary_scope/approval_preparation_eval.md`

- [ ] Review Scope isolation, consumed Scope non-reuse, retry/attempt limits, false-ready paths, Head consistency, Secret boundaries, and Provider-path uniqueness.
- [ ] Record `NO_ACTIONABLE_FINDINGS` only if every review gate passes.
- [ ] Commit and push offline evidence.
- [ ] Confirm local/fork Head match and a clean worktree, then stop at `CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL`.
