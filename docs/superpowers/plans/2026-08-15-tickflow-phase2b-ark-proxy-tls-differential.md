# TickFlow Phase 2B Ark Proxy TLS Differential Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add precise future Ark Proxy TLS diagnostics and determine, with internal-only parity tests, whether the historical Probe/Proxy divergence is locally reproducible.

**Architecture:** Preserve historical evidence by hash, add an Ark-only schema v3 receipt with precise sanitized TLS diagnostics while retaining historical v2 compatibility, and exercise the production TLS path against internal Docker mocks. The existing Probe and Proxy implementations share a trusted internal target whose server records actual SNI.

**Tech Stack:** Python 3.12 tests, Python 3.11 production container, stdlib `ssl`/`http.client`, Docker internal bridge networks, pytest.

## Global Constraints

- New Provider attempts: 0.
- New AI calls: 0.
- Real public network successes: 0.
- Secret content read: NO.
- Historical evidence must remain byte-identical.
- Do not alter the Ark 180/195/210 timeout contract or TLS verification policy.

---

### Task 1: Freeze Evidence and Runtime Differences

**Files:**
- Create: `reports/phase2_provider_ark/proxy_tls_differential/historical_baseline.json`
- Create: `reports/phase2_provider_ark/proxy_tls_differential/path_difference_matrix.json`

**Interfaces:**
- Consumes: the existing Probe and Canary evidence files.
- Produces: exact SHA baselines and a source/image/runtime-backed matrix.

- [x] Record exact source paths and SHA-256 values for both historical evidence sets.
- [x] Record image, base image, Python, OpenSSL, CA, user, environment, TLS call path, and network topology evidence.
- [x] Recompute the hashes before completion and fail on any mismatch.

### Task 2: Add Precise Ark TLS Classification

**Files:**
- Modify: `docker/phase2-ark-egress-proxy/proxy.py`
- Modify: `backend/app/services/phase2_canary_orchestrator.py`
- Create: `backend/tests/test_phase2_ark_proxy_tls_differential.py`

**Interfaces:**
- Produces: `classify_tls_error(error, phase=...) -> TlsErrorClassification`, precise terminal categories, and bounded Ark v3 receipt metadata.
- Preserves: exact historical Ark v2 receipt validation and all original evidence bytes.

- [x] Write tests for certificate, hostname, protocol, timeout, reset, EOF, other SSL, and connect failures.
- [x] Run the focused tests and confirm failures are caused by missing precise classification.
- [x] Implement bounded Ark v3 metadata and phase-aware TCP/TLS timeout classification.
- [x] Add orchestrator compatibility tests for historical Ark v2 and current Ark v3 receipts.
- [x] Run focused tests until green.

### Task 3: Build Internal-only Proxy TLS Parity Harness

**Files:**
- Create: `backend/scripts/run_phase2_ark_proxy_tls_parity.py`
- Create: `docker/phase2-ark-proxy-tls-mock/server.py`
- Modify: `backend/tests/test_phase2_ark_proxy_tls_differential.py`

**Interfaces:**
- Consumes: production `create_tls_context`, `default_connection_factory`, and Probe `execute_probe` paths.
- Produces: `reports/phase2_provider_ark/proxy_tls_differential/local_parity.json`.

- [x] Write tests that reject non-internal Docker networks and credential or HTTP behavior.
- [x] Confirm those tests fail before the runner exists.
- [x] Generate ephemeral CA/certificates and run seven deterministic local cases.
- [x] Run the Probe and Proxy against one trusted internal target and compare TLS version, cipher, CA identity, SNI, and hostname target.
- [x] Assert all containers and networks are removed and public success count is zero.

### Task 4: Verify and Close Out

**Files:**
- Create: `reports/phase2_provider_ark/proxy_tls_differential/verification.json`
- Create: `reports/tickflow_phase2b_ark_proxy_tls_differential_eval.md`

**Interfaces:**
- Consumes: matrix, parity output, focused tests, compatibility tests, and immutable hash baseline.
- Produces: one allowed final status and exact next action.

- [x] Run focused tests, the internal parity harness, Ark runtime compatibility tests, compileall, Ruff F821, and `git diff --check`.
- [x] Run backend full because shared `backend/app/**` receipt validation changes; classify pre-existing and expected immutable-identity failures separately.
- [x] Obtain an independent focused review with no actionable findings.
- [x] Recompute historical evidence hashes and record zero Provider/AI/public-network activity.
- [x] Preserve the diagnosis locally without a remote push because this stage prohibits public-network access.
