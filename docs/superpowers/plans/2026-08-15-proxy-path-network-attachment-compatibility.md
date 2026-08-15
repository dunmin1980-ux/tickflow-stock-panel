# Proxy Path Network Attachment Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the Production Proxy TLS Probe network attachment contract into deterministic pre-start and post-start validation so Docker Desktop 29 stopped-container metadata is accepted without weakening the post-start topology gate.

**Architecture:** The proxy container starts in a network-silent dispatch-gate process. Pre-start validation checks the exact declared network names and approved network contracts while allowing empty endpoint metadata. After start, post-start validation requires non-empty network and endpoint identifiers for exactly the approved networks. Only after that validation does the host persist `NETWORK_PROBE_STARTED` and atomically release the gate into the unchanged TLS runner.

**Tech Stack:** Python 3.12, pytest, Docker Desktop 29, canonical JSON and SHA-256 immutable artifact generation.

## Global Constraints

- Do not access real Ark DNS, TCP, TLS, HTTP, Provider, Secret, Authorization, or AI services.
- Preserve TLS implementation, CA trust, SNI, hostname verification, target, model, Prompt, reasoning, and the 180/195/210 timeout contract.
- Retry remains `0`; maximum Probe attempts remains `1`.
- The old Candidate `3658053f48e2ee7a2d6b1d2a72b6f56a803c29f9a1da7d825dbfaea5d051ff2c` and Scope `12869c84537c09c0da0267fb4216c93c1a409cc6077e588fc92b451656ca21c5` remain superseded and preserved.
- Only durable `NETWORK_PROBE_STARTED` consumes an attempt.
- Local Docker harness residue must end at zero.

---

### Task 1: Define the split attachment contract with failing tests

**Files:**
- Modify: `backend/tests/test_phase2_ark_proxy_tls_probe_approval.py`
- Modify: `docker/phase2-ark-proxy-tls-probe/contracts/proxy-network-attachment-contract.json`

**Interfaces:**
- Produces: `validate_pre_start_proxy_attachments(inspect_payload, names)` and `validate_post_start_proxy_attachments(inspect_payload, names)` behavioral contracts.

- [ ] Add tests for scenarios A-G: exact stopped-container names with empty IDs pass pre-start; missing/extra names fail; started containers require non-empty `NetworkID` and `EndpointID`; extra networks fail.
- [ ] Run the focused tests and confirm RED because the split functions do not exist.
- [ ] Update the canonical attachment contract to schema version 2 with explicit pre-start and post-start rules.

### Task 2: Add a network-silent dispatch gate and minimal launcher lifecycle

**Files:**
- Create: `docker/phase2-ark-proxy-tls-probe/dispatch_gate.py`
- Modify: `docker/phase2-ark-proxy-tls-probe/Dockerfile`
- Modify: `backend/scripts/run_phase2_ark_proxy_tls_probe.py`
- Test: `backend/tests/test_phase2_ark_proxy_tls_probe_approval.py`

**Interfaces:**
- `dispatch_gate.py --gate /output/dispatch.ready --probe-id <id>` waits for a regular gate file containing the exact probe ID, then `execv` runs the unchanged `/probe/probe.py`.
- `publish_dispatch_gate(path, probe_id)` writes mode `0644` via staging, fsync, atomic rename, and directory fsync.
- `wait_for_container_exit(names, run)` returns the exact container exit code from `docker container wait`.

- [ ] Add failing tests for gate command construction, post-start-before-dispatch command order, scenario H rollback with attempt 0, and scenario I durable dispatch consumption.
- [ ] Verify RED for the expected missing lifecycle.
- [ ] Implement exact pre-start validation, detached start, exact post-start validation, durable dispatch marking, atomic gate publication, and container wait.
- [ ] Verify focused tests GREEN without changing TLS code or request behavior.

### Task 3: Add the production-topology local attachment harness

**Files:**
- Create: `backend/scripts/run_phase2_ark_proxy_network_attachment_harness.py`
- Modify: `backend/tests/test_phase2_ark_proxy_tls_probe_approval.py`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/network_attachment_harness.json`

**Interfaces:**
- Harness creates one internal relay bridge, one non-internal egress bridge, and a gated proxy container using the immutable image.
- Each of three runs records pre-start validation, post-start validation, zero dispatch attempts, cleanup, and residue counts without releasing the gate.

- [ ] Add offline report validator tests and verify RED.
- [ ] Implement three local Docker happy paths plus cleanup and canonical report publication.
- [ ] Run the harness and require `PASSED`, three successful runs, and zero container/network/temporary residue.

### Task 4: Bind the new runtime sources and evidence into immutable generation

**Files:**
- Modify: `backend/scripts/build_phase2_ark_proxy_tls_probe_offline.py`
- Modify: `backend/tests/test_phase2_ark_proxy_tls_probe_approval.py`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/build_provenance.json`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/approval_candidate.json`
- Generate: `reports/phase2_provider_ark/proxy_tls_probe/approval_scope.json`

**Interfaces:**
- Image context binds `dispatch_gate.py` and its SHA-256 label.
- Candidate source bindings include the gate source, attachment harness source, and harness evidence.

- [ ] Add failing builder tests for complete gate and harness bindings.
- [ ] Update image context, build arguments, labels, source bindings, and evidence verification.
- [ ] Commit source changes and push the exact branch before generating final artifacts.
- [ ] Rebuild the image offline, rerun Mock E2E and attachment harness, regenerate postcheck, Candidate, Scope, and verification evidence.

### Task 5: Full verification and closeout

**Files:**
- Generate: `reports/tickflow_phase2b_proxy_path_network_attachment_compatibility.md`

**Interfaces:**
- Final status is `PROXY_PATH_TLS_PROBE_READY_FOR_FINAL_EXECUTION_APPROVAL` only when all gates prove zero real external activity and zero residue.

- [ ] Run network attachment and Proxy-path focused tests.
- [ ] Run the production topology local harness with happy path x3.
- [ ] Run Backend full as failure-set observation, compileall, Ruff F821, and `git diff --check`.
- [ ] Run an independent focused review and resolve every actionable finding.
- [ ] Verify historical evidence hashes unchanged, old Candidate/Scope preserved, new Scope attempts `0`, and availability `AVAILABLE`.
- [ ] Confirm real Probe attempts, Provider attempts, AI calls, HTTP, Authorization, Secret reads, and real public network successes all remain `0`.
- [ ] Confirm container, network, and temporary residue are `0`; publish the final closeout and stop before any real Ark Probe.
