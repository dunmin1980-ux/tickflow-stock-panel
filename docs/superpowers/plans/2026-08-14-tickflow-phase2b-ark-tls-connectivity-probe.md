# TickFlow Phase 2B Ark TLS Connectivity Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, validate, and execute exactly one Secret-free and HTTP-free Ark TLS connectivity probe.

**Architecture:** A host one-shot runner launches the current immutable Ark Proxy image with a single read-only probe source and isolated output mount. The child performs one DNS lookup, one TCP connection, one verified TLS handshake, and a TLS close; the host owns the independent attempt ledger, final receipt publication, and cleanup.

**Tech Stack:** Python 3.12, `socket`, `ssl`, Docker CLI, pytest, Ruff.

## Global Constraints

- Historical request `93cf0ea84804444ebc9745fa34c4bb82` remains `FAILED_AFTER_DISPATCH / CONSUMED / PRESERVED`.
- Fixed target is `ark.cn-beijing.volces.com:443`; SNI and hostname target are identical.
- CA file is `/etc/ssl/certs/ca-certificates.crt`; TLS minimum is `TLSv1_2`; verification is mandatory.
- Provider attempts and AI calls remain zero; no Secret or Authorization is read or constructed.
- No HTTP method, path, header, or body is sent.
- Maximum probe attempts is one and retry count is zero.
- The live probe cannot run before all local tests and independent review pass.

---

### Task 1: Freeze history and define the probe receipt contract

**Files:**
- Create: `backend/tests/test_phase2_ark_tls_connectivity_probe.py`
- Create: `backend/app/services/phase2_ark_tls_connectivity_probe.py`

**Interfaces:**
- Produces: `ProbePolicy`, `ProbeEventRecorder`, `run_tls_probe()`, `validate_probe_receipt()`.

- [ ] **Step 1: Write failing receipt and classifier tests**

  Cover exact fields, event order, bounded verification metadata, no sensitive or
  HTTP fields, one attempt, zero retry, and every approved exception category.

- [ ] **Step 2: Run the focused test and verify RED**

  Run: `PYTHONPATH=. .venv/bin/pytest tests/test_phase2_ark_tls_connectivity_probe.py -q`

  Expected: import failure because the service does not exist.

- [ ] **Step 3: Implement the minimal pure probe service**

  Use a frozen production policy, one `getaddrinfo()` call, one selected address,
  one TCP connect, one `SSLContext.wrap_socket()`, no application write, and
  `unwrap()` for TLS close. Persist no IP, certificate bytes, or traceback.

- [ ] **Step 4: Run the focused test and verify GREEN**

  Expected: all pure service tests pass.

### Task 2: Add the restricted Docker child and host one-shot runner

**Files:**
- Create: `docker/phase2-ark-tls-connectivity-probe/probe.py`
- Create: `backend/scripts/run_phase2_ark_tls_connectivity_probe.py`
- Modify: `backend/tests/test_phase2_ark_tls_connectivity_probe.py`

**Interfaces:**
- Child output: canonical JSON `child-receipt.json` in one mounted output directory.
- Host output: `reports/phase2_provider_ark/tls_connectivity_probe/<probe_id>/receipt.json`.
- Host ledger: `reports/phase2_provider_ark/tls_connectivity_probe/execution-ledger.json`.

- [ ] **Step 1: Add failing orchestrator and Docker-command tests**

  Assert non-root user, read-only rootfs, dropped capabilities, no-new-privileges,
  restart disabled, no broad/Secret/Docker mounts, fixed image, one attempt, and
  refusal when the execution ledger already exists.

- [ ] **Step 2: Run focused tests and verify RED**

  Expected: missing child and host runner behavior.

- [ ] **Step 3: Implement atomic ledger, restricted command, receipt validation, and cleanup**

  Mark dispatch before `docker start`, treat any uncertain dispatch as consumed,
  remove the named container and network, verify zero residue, add the cleanup
  event, and atomically publish the final receipt.

- [ ] **Step 4: Run focused tests and verify GREEN**

  Expected: all host and child contract tests pass without public network access.

### Task 3: Build the Docker internal-only six-scenario harness

**Files:**
- Create: `backend/scripts/run_phase2_ark_tls_probe_mock_e2e.py`
- Create: `backend/tests/test_phase2_ark_tls_probe_mock_e2e.py`
- Generate: `reports/phase2_provider_ark/tls_connectivity_probe/mock_e2e.json`

**Interfaces:**
- Produces six scenario receipts and one aggregate result with `real_public_network_success_count=0`.

- [ ] **Step 1: Add a failing aggregate-contract test**

  Require trusted success plus exact unknown-CA, hostname, refused, DNS, and
  handshake-timeout classifications, all on Docker internal networks.

- [ ] **Step 2: Verify RED**

  Expected: mock runner or aggregate evidence is absent.

- [ ] **Step 3: Implement and run the local harness**

  Generate ephemeral certificates, use internal networks and synthetic servers,
  mount only test files, and clean all containers, networks, and temporary files.

- [ ] **Step 4: Verify GREEN and zero residue**

  Expected: six scenarios pass, no public success, and all residue counts are zero.

### Task 4: Run preflight validation and independent review

**Files:**
- Create: `reports/phase2_provider_ark/tls_connectivity_probe/preflight.json`

- [ ] **Step 1: Run focused tests, compileall, Ruff F821, and `git diff --check`**
- [ ] **Step 2: Verify the frozen historical SHA-256 manifest is unchanged**
- [ ] **Step 3: Scan new source and evidence for Secret, Authorization, and HTTP request shapes**
- [ ] **Step 4: Obtain independent focused review with `NO ACTIONABLE FINDINGS`**

### Task 5: Execute the unique live TLS probe and close out

**Files:**
- Generate: `reports/phase2_provider_ark/tls_connectivity_probe/execution-ledger.json`
- Generate: `reports/phase2_provider_ark/tls_connectivity_probe/<probe_id>/receipt.json`

- [ ] **Step 1: Re-run the complete offline preflight**
- [ ] **Step 2: Execute `run_phase2_ark_tls_connectivity_probe.py` exactly once**
- [ ] **Step 3: Validate the final receipt and terminal state without retry**
- [ ] **Step 4: Verify container, network, temporary, and historical evidence residue**
- [ ] **Step 5: Stop with the approved next action; never launch an Ark Responses request**
