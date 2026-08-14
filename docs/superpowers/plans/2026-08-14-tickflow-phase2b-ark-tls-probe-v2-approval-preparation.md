# TickFlow Phase 2B Ark TLS Probe v2 Approval Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a complete, deterministic, independently reviewable Ark TLS Probe v2 Candidate and unused Approval Scope without performing a real Ark network operation.

**Architecture:** Add explicit source-controlled receipt, marker, path, transport, and TLS contracts plus one offline builder. The builder validates current source bytes and frozen history, inspects the existing immutable image locally, extracts the CA bundle through a stopped `--network none` container, and atomically publishes Candidate, Scope, provenance, and preflight evidence under a new Probe-only namespace.

**Tech Stack:** Python 3.12, JSON Schema documents, Docker CLI local inspection, pytest, Ruff, Git.

## Global Constraints

- Historical Probe `5dd641ca9e4b4ccba1035fff4d695409` remains `UNKNOWN / PROCESS_ERROR / CONSUMED / PRESERVED` with retry zero.
- No Ark DNS, TCP, TLS, HTTP, Authorization, Keychain content read, Provider request, or AI call is allowed.
- The fixed container image is `sha256:a8a01a003f7afc385b4e24fd2960bcdfd3eaac40c394f39aa6249fd7b2f0f79b`.
- All local Harness containers use `--network none`.
- Probe scope type is `ark_tls_connectivity_probe_v2`, independent from all Provider and AI scopes.
- Probe attempts start at zero, retry is zero, and maximum future Probe attempts is one.
- The known full-suite fail-closed set is exactly 14 failures; any changed failure identity blocks completion.
- Completion stops at `TLS_PROBE_READY_FOR_FINAL_EXECUTION_APPROVAL`.

---

### Task 1: Protect and freeze historical local evidence

**Files:**
- Modify locally only: `.git/info/exclude`
- Read: `reports/phase2_provider_ark/tls_receipt_transport/historical_probe_baseline.json`
- Read: the four existing `93cf...` and `8e42...` untracked roots

**Interfaces:**
- Consumes: exact seven-file historical SHA baseline and the seven preserved untracked files.
- Produces: a clean normal Git status without deleting or changing preserved evidence.

- [ ] **Step 1: Record the SHA-256 of every preserved untracked historical file**

  Compare the seven observed hashes with the existing Ark historical baseline.

- [ ] **Step 2: Add exact local exclude entries**

  Add only these four paths to `.git/info/exclude`:

  ```text
  /reports/phase2_provider_ark/live_canary/evidence/93cf0ea84804444ebc9745fa34c4bb82.json
  /reports/phase2_provider_ark/live_canary/receipts/93cf0ea84804444ebc9745fa34c4bb82/
  /reports/phase2_provider_ark/live_canary/rejected/93cf0ea84804444ebc9745fa34c4bb82.json
  /reports/phase2_provider_canary/attempts/8e42a251cfa4e5b4cbdc95b3aceca9d59152ce574e65f5ba2300a0009df8a019/
  ```

- [ ] **Step 3: Verify protection and presence**

  Require normal `git status --porcelain` to be empty, `git status --ignored`
  to show all four exact roots, and all seven files to retain their hashes.

### Task 2: Define explicit Probe v2 contracts

**Files:**
- Create: `docker/phase2-ark-tls-connectivity-probe/contracts/child-receipt.schema.json`
- Create: `docker/phase2-ark-tls-connectivity-probe/contracts/ready-marker.schema.json`
- Create: `docker/phase2-ark-tls-connectivity-probe/contracts/path-validation-contract.json`
- Create: `docker/phase2-ark-tls-connectivity-probe/contracts/receipt-transport-contract.json`
- Create: `docker/phase2-ark-tls-connectivity-probe/contracts/tls-contract.json`
- Create: `backend/tests/test_phase2_ark_tls_probe_v2_approval.py`

**Interfaces:**
- Produces: five canonical JSON documents with closed property sets and fixed receipt, mount, TLS, stage, and failure semantics.

- [ ] **Step 1: Write failing contract tests**

  Tests require exact ordered events, exact error categories, `/output` paths,
  UID/GID `65532:65532`, `0700/0600` modes, the one trusted macOS alias, and the
  fixed TLS target/security settings. They reject `TLS_FAILED`, HTTP fields,
  Authorization fields, path escapes, user symlinks, and fallback behavior.

- [ ] **Step 2: Run the focused tests and verify RED**

  ```bash
  cd backend
  PYTHONPATH=. .venv/bin/pytest tests/test_phase2_ark_tls_probe_v2_approval.py -q
  ```

  Expected: failure because the five contract files do not exist.

- [ ] **Step 3: Add the minimal closed contract documents**

  Use `additionalProperties: false` for receipt and marker schemas. Keep the
  fixed stage and category arrays literal and immutable.

- [ ] **Step 4: Re-run the focused tests and verify GREEN**

  Expected: all contract tests pass.

### Task 3: Build the deterministic offline Candidate and Scope generator

**Files:**
- Create: `backend/scripts/build_phase2_ark_tls_probe_v2_offline.py`
- Modify: `backend/tests/test_phase2_ark_tls_probe_v2_approval.py`

**Interfaces:**
- Produces: `canonical_json_bytes(value)`, `validate_source_binding(...)`,
  `inspect_probe_image(...)`, `extract_ca_identity(...)`,
  `build_probe_candidate(...)`, `build_probe_scope(...)`,
  `verify_probe_artifacts(...)`, and atomic publication.
- CLI: `--build` publishes deterministic artifacts; `--verify` validates them without mutation.

- [ ] **Step 1: Add failing builder tests**

  Cover deterministic bytes, missing/extra bindings, symlink rejection, image
  label mismatch, base digest mismatch, CA missing/empty, non-`--network none`
  Docker command rejection, source-head binding, isolated scope derivation,
  attempts zero, unavailable ledger detection, and atomic write failure.

- [ ] **Step 2: Verify RED**

  Run the same focused test command. Expected: import failure for the builder.

- [ ] **Step 3: Implement the smallest offline builder**

  The builder must:

  ```python
  candidate_sha256 = sha256(canonical_json_bytes(candidate)).hexdigest()
  scope_id = sha256(canonical_json_bytes({
      "scope_type": "ark_tls_connectivity_probe_v2",
      "approval_candidate_sha256": candidate_sha256,
      "target_host": "ark.cn-beijing.volces.com",
      "target_port": 443,
  })).hexdigest()
  ```

  It must use `docker image inspect`, then `docker create --network none`,
  `docker cp`, and `docker rm` to identify the CA bundle. It must never call
  `docker start`, `docker run`, DNS, socket, SSL, curl, or OpenSSL.

- [ ] **Step 4: Re-run focused tests and verify GREEN**

  Expected: deterministic builder and validation tests all pass.

- [ ] **Step 5: Commit source and contracts**

  ```bash
  git add backend/scripts/build_phase2_ark_tls_probe_v2_offline.py \
    backend/tests/test_phase2_ark_tls_probe_v2_approval.py \
    docker/phase2-ark-tls-connectivity-probe/contracts
  git commit -m "feat: add Ark TLS Probe v2 approval builder"
  ```

  Record this commit as `source_git_head` for all generated identities.

### Task 4: Refresh local receipt evidence without public networking

**Files:**
- Regenerate: `reports/phase2_provider_ark/tls_receipt_transport/local_harness.json`
- Regenerate: `reports/phase2_provider_ark/tls_receipt_transport/harness_archives/`

**Interfaces:**
- Consumes: current receipt runner, child, fixture, and historical baseline.
- Produces: nine A-I scenario results, including three independent happy paths.

- [ ] **Step 1: Record the frozen seven historical hashes**
- [ ] **Step 2: Run `backend/scripts/run_phase2_ark_receipt_transport_harness.py` once**
- [ ] **Step 3: Require scenario count 9, happy paths 3, every fault fail-closed, and all network modes `none`**
- [ ] **Step 4: Verify historical hashes unchanged and residue `0/0/0`**

### Task 5: Generate and validate the Probe v2 artifacts

**Files:**
- Generate: `reports/phase2_provider_ark/tls_probe_v2/approval_candidate.json`
- Generate: `reports/phase2_provider_ark/tls_probe_v2/approval_scope.json`
- Generate: `reports/phase2_provider_ark/tls_probe_v2/build_provenance.json`
- Generate: `reports/phase2_provider_ark/tls_probe_v2/offline_preflight.json`

**Interfaces:**
- Consumes: source commit, immutable image, CA bundle, five contracts, frozen history, and refreshed local evidence.
- Produces: complete Candidate and `AVAILABLE` Scope with attempts zero.

- [ ] **Step 1: Run the builder in `--build` mode**
- [ ] **Step 2: Run the builder twice in `--verify` mode and require identical Candidate and Scope bytes**
- [ ] **Step 3: Require all mandatory artifact and source binding keys exactly once**
- [ ] **Step 4: Require no approval installation and no attempt directory for the new scope**

### Task 6: Run the complete offline verification matrix

**Files:**
- Generate: `reports/phase2_provider_ark/tls_probe_v2/verification.json`
- Generate: `reports/phase2_provider_ark/tls_probe_v2/approval_preparation_eval.md`

**Interfaces:**
- Produces: final machine evidence and human closeout report.

- [ ] **Step 1: Run focused tests, Probe compatibility tests, compileall, Ruff F821, and `git diff --check`**
- [ ] **Step 2: Run the backend full suite and capture exact failed node IDs**
- [ ] **Step 3: Require exactly the known 14 failure identities and zero receipt-transport failures**
- [ ] **Step 4: Scan new source/evidence for Secret, Authorization, HTTP request, DNS, socket, TLS, curl, and OpenSSL execution paths**
- [ ] **Step 5: Verify Docker API and residue counts `0/0/0`**
- [ ] **Step 6: Recheck every historical and source SHA-256 binding**

### Task 7: Independent review, evidence commit, push, and final gate

**Files:**
- Modify only if needed: files found by the focused review
- Finalize: `reports/phase2_provider_ark/tls_probe_v2/verification.json`
- Finalize: `reports/phase2_provider_ark/tls_probe_v2/approval_preparation_eval.md`

**Interfaces:**
- Produces: reviewed, committed, remote-matched preparation artifacts.

- [ ] **Step 1: Request an independent focused review**

  Review exit-zero durability, source binding completeness, image/build
  provenance, CA identity, TLS contract, scope isolation, attempt availability,
  historical immutability, forbidden network paths, and cleanup residue.

- [ ] **Step 2: Resolve every actionable finding and rerun affected gates**
- [ ] **Step 3: Require final review result `NO ACTIONABLE FINDINGS`**
- [ ] **Step 4: Commit only v2 artifacts and refreshed local Harness evidence**

  ```bash
  git commit -m "docs: prepare Ark TLS Probe v2 approval"
  ```

- [ ] **Step 5: Push `codex/tickflow-phase2-ai-review` to fork**
- [ ] **Step 6: Fetch fork and require exact local/remote HEAD equality plus clean status**
- [ ] **Step 7: Stop at `TLS_PROBE_READY_FOR_FINAL_EXECUTION_APPROVAL`**

  Do not install an approval and do not execute the real TLS Probe.
