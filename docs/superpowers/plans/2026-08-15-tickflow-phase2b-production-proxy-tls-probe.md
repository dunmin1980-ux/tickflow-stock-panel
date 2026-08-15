# TickFlow Phase 2B Production Proxy Path TLS-only Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare an immutable, separately scoped, production-topology TLS-only Ark Probe without making a real Ark, HTTP, Provider, AI, or Secret operation.

**Architecture:** Rebuild the current production Ark Proxy image offline, run a read-only TLS-only runner inside that image, and use a dedicated host launcher that creates the production two-network attachment topology. Generate and verify a separate Candidate and Scope only after an internal double-network Mock harness and frozen evidence gates pass.

**Tech Stack:** Python 3.12 tests, Python 3.11 distroless runtime, stdlib `ssl` and `http.client`, Docker bridge networks, pytest, SHA-256 canonical JSON artifacts.

## Global Constraints

- Real Ark DNS, TCP, TLS, and HTTP operations: 0.
- Provider attempts: 0.
- AI calls: 0.
- Secret content reads: 0.
- Authorization construction: 0.
- Real public-network successes: 0.
- Retry count: 0.
- Maximum Probe attempts: 1.
- Do not change model, Prompt, reasoning, CA verification, or the 180/195/210 timeout contract.
- Do not mutate historical Probe, Canary, receipt, ledger, or differential evidence.
- Do not modify Gold or the historical launcher whitelist.

---

### Task 1: Freeze Source and Failure Baselines

**Files:**
- Create: `reports/phase2_provider_ark/proxy_tls_probe/preparation_preflight.json`
- Create: `reports/phase2_provider_ark/proxy_tls_probe/backend_failure_baseline.json`

**Interfaces:**
- Consumes: current Git Head, current Proxy and Orchestrator source, historical differential baseline, and backend full-suite output.
- Produces: immutable source and exact known-failure evidence used by Candidate preparation.

- [ ] Record Head `ad458daa57425093940480f1a090537e885ba35d` before implementation and verify the two approved working-tree source hashes.
- [ ] Recompute all 13 historical evidence hashes and fail on any mismatch.
- [ ] Run backend full with JUnit output and publish the exact failing node IDs, grouped as expected identity fail-closed, pre-existing launcher debt, and pre-existing Gold debt.
- [ ] Verify the baseline totals are `2656 passed / 57 failed / 13 warnings` and preserve the full failing-node set for post-implementation comparison.

### Task 2: Define Probe and Network Contracts with Failing Tests

**Files:**
- Create: `docker/phase2-ark-proxy-tls-probe/contracts/probe-contract.json`
- Create: `docker/phase2-ark-proxy-tls-probe/contracts/runtime-contract.json`
- Create: `docker/phase2-ark-proxy-tls-probe/contracts/readiness-contract.json`
- Create: `docker/phase2-ark-proxy-tls-probe/contracts/internal-network-contract.json`
- Create: `docker/phase2-ark-proxy-tls-probe/contracts/egress-network-contract.json`
- Create: `docker/phase2-ark-proxy-tls-probe/contracts/proxy-network-attachment-contract.json`
- Create: `docker/phase2-ark-proxy-tls-probe/contracts/egress-policy.json`
- Create: `docker/phase2-ark-proxy-tls-probe/contracts/dns-configuration-contract.json`
- Create: `docker/phase2-ark-proxy-tls-probe/contracts/receipt.schema.json`
- Create: `backend/tests/test_phase2_ark_proxy_tls_probe_approval.py`

**Interfaces:**
- Produces: closed, source-controlled contracts and a receipt schema with exact zero-activity counters.

- [ ] Write tests that require fixed target, SNI, hostname verification, TLS policy, timeout ordering, retry zero, one maximum attempt, exact double-network semantics, and the HTTP/Secret prohibition.
- [ ] Run the focused tests and confirm they fail because the contracts and implementation do not exist.
- [ ] Add canonical JSON contracts with no placeholders or optional security fields.
- [ ] Run contract tests until green.

### Task 3: Implement the TLS-only Runner and Host Launcher

**Files:**
- Create: `docker/phase2-ark-proxy-tls-probe/probe.py`
- Create: `backend/scripts/run_phase2_ark_proxy_tls_probe.py`
- Modify: `backend/tests/test_phase2_ark_proxy_tls_probe_approval.py`

**Interfaces:**
- Container runner consumes `/proxy/proxy.py`, the fixed target, and `/output`; it produces one canonical receipt.
- Host launcher consumes the installed approval, Candidate, Scope, image ID, and contracts; it produces one Probe ledger and archived receipt.

- [ ] Add failing tests proving the runner imports and calls `create_tls_context()` plus `connect_verified_tls()` and never references `read_auth_file()` or `perform_provider_request()`.
- [ ] Add failing tests for exact internal and egress network commands, attachment verification, no Secret mount, no request mount, approval gating, one-shot reservation, and retry zero.
- [ ] Implement the runner with DNS evidence, production TLS connect, precise failure classification propagation, clean close, and atomic receipt publication.
- [ ] Implement the host launcher with approval verification, exclusive lock, durable ledger, double-network lifecycle, pre-start attachment inspection, receipt validation, and fail-closed cleanup.
- [ ] Run the focused tests until green.

### Task 4: Rebuild and Bind the Immutable Proxy Image

**Files:**
- Create: `backend/scripts/build_phase2_ark_proxy_tls_probe_offline.py`
- Modify: `backend/tests/test_phase2_ark_proxy_tls_probe_approval.py`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/build_provenance.json`

**Interfaces:**
- `--build-image` consumes a fixed local base image and read-only Proxy build context; it produces a new image and Build Provenance.
- `--prepare` consumes verified image, Mock, and baseline evidence; it produces Candidate, Scope, and offline preflight.
- `--verify` recomputes the entire artifact set byte-for-byte.

- [ ] Add failing tests for `--network=none`, `--pull=false`, `--no-cache`, exact base digest, context hashes, image labels, CA bytes, and atomic publication.
- [ ] Implement the offline builder without pull, network, dynamic dependency, or source regeneration.
- [ ] Commit all source files and freeze the resulting Head before image build.
- [ ] Build the image and verify its labels, CA identity, Python/OpenSSL identity, user, architecture, and source bindings.

### Task 5: Production-topology Internal Mock Harness

**Files:**
- Create: `backend/scripts/run_phase2_ark_proxy_tls_probe_mock_e2e.py`
- Modify: `docker/phase2-ark-proxy-tls-mock/server.py`
- Modify: `backend/tests/test_phase2_ark_proxy_tls_probe_approval.py`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/mock_e2e.json`

**Interfaces:**
- Consumes: the rebuilt image, production runner, Mock server, and network contracts.
- Produces: twelve-case Mock evidence with three Happy Paths and zero residue.

- [ ] Add failing tests that require two distinct internal Mock networks, exact Proxy attachment, Mock Provider only on egress, and pre-start failure for missing or wrong attachments.
- [ ] Add failing tests for the seven TLS outcomes and three repeated Happy Paths.
- [ ] Implement the harness using the real image and runner, with all Mock networks marked internal.
- [ ] Run the harness and assert `Production topology Mock=PASSED`, `Happy Path x3=PASSED`, and all activity counters zero.

### Task 6: Candidate, Scope, Verification, and Closeout

**Files:**
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/approval_candidate.json`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/approval_scope.json`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/offline_preflight.json`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/artifact_generation.json`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/verification.json`
- Create: `reports/tickflow_phase2b_proxy_path_tls_probe_approval_prep.md`

**Interfaces:**
- Candidate binds every required immutable source, image, contract, topology, CA, target, and historical-evidence identity.
- Scope derives only from the Candidate and fixed Probe identity and starts with zero attempts and `AVAILABLE`.

- [ ] Generate Candidate and Scope after Mock evidence passes; do not install approval.
- [ ] Run Candidate, Scope, Build Provenance, TLS classification, topology, compatibility, compileall, Ruff F821, JSON, sensitive-data, diff, residue, and historical-hash checks.
- [ ] Re-run backend full or compare an equivalent complete JUnit result and fail if any unknown node appears beyond the frozen 57-item set.
- [ ] Obtain independent review with `NO ACTIONABLE FINDINGS`.
- [ ] Publish the final report with zero real Probe, Provider, AI, HTTP, Secret, and public-network activity.
- [ ] Stop at `PROXY_PATH_TLS_PROBE_READY_FOR_FINAL_EXECUTION_APPROVAL` with next action `REQUEST_FINAL_PROXY_PATH_TLS_PROBE_APPROVAL`.
