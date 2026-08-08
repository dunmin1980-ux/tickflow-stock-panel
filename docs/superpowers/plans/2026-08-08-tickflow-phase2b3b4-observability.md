# TickFlow Phase 2B-3B4 Canary Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add precise child-exit classification and durable, sanitized Proxy/Relay stage receipts without making a Provider request.

**Architecture:** A focused Host observability module classifies process outcomes and bounds stderr. Proxy and Relay publish strict atomic receipts in separate ephemeral output directories. The Host validates and permanently archives both receipts plus child metadata before any cleanup, while leaving the v1 attempt ledger and timeout contract unchanged.

**Tech Stack:** Python 3.12, Pydantic, pytest, Docker CLI command doubles, stdlib `http.client`, atomic POSIX file operations.

## Global Constraints

- Never run the Canary launcher, OpenAI, TickFlow, DNS, TLS, curl, or wget.
- Historical Provider attempts remain 1; this implementation adds 0 attempts and 0 AI calls.
- Retry count is always 0 and no path dispatches twice.
- Preserve request `d59766101b63450e8148541d589a90bf` and all historical evidence byte-for-byte.
- Keep the runtime timeout contract at provider 60s, Relay 75s, Host 90s, cleanup 15s.
- Do not alter Facts, Typed Claims, renderer, isolation topology, Secret injection, Paper Trading, Obsidian, Gold, or cloud deployment.
- Do not install the new approval Candidate.

---

### Task 1: Child Exit Classification And Bounded Stderr

**Files:**
- Create: `backend/app/services/phase2_canary_observability.py`
- Create: `backend/tests/test_phase2_canary_child_exit_classification.py`

**Interfaces:**
- Produces: `classify_completed_child(component, result, timing) -> ChildProcessEvidence`
- Produces: `classify_child_exception(component, error, timing) -> ChildProcessEvidence`
- Produces: `sanitize_bounded_stderr(value, maximum_bytes=4096) -> BoundedStderrEvidence`

- [ ] Write tests proving positive exit, negative signal, timeout, start failure, process error, unknown exit, elapsed milliseconds, 4096-byte UTF-8 truncation, and sensitive redaction.
- [ ] Run the new file and confirm failures are caused by the missing module/API.
- [ ] Implement strict frozen evidence models and pure classification functions.
- [ ] Run the new file and confirm all tests pass.

### Task 2: Proxy Provider Stage Receipt

**Files:**
- Modify: `docker/phase2-openai-egress-proxy/proxy.py`
- Modify: `backend/tests/test_phase2_openai_proxy_contract.py`
- Create: `backend/tests/test_phase2_canary_stage_receipts.py`

**Interfaces:**
- Produces: `StageRecorder` with seven fixed Provider events.
- Changes: `perform_provider_request(..., stage_recorder=...)` records connect, TLS, write, headers, and body boundaries.
- Changes: `build_receipt(...)` emits receipt schema v2 with request binding and sanitized events.
- Changes: `write_receipt(...)` uses temporary file, fsync, atomic rename, and directory fsync.

- [ ] Add failing tests for every Provider event and failure boundary: connect, TLS, request write, headers missing, body incomplete, and success.
- [ ] Add failing tests for monotonic timestamps, exact event fields, no body/auth leakage, and atomic rename failure.
- [ ] Implement the minimal recorder and thread it through the injected mock requester contract.
- [ ] Replace in-place truncation with same-directory atomic receipt publication.
- [ ] Run Proxy and stage-receipt tests to green.

### Task 3: Relay Local Stage Receipt

**Files:**
- Modify: `docker/phase2-canary-relay/relay.py`
- Modify: `backend/tests/test_phase2_canary_relay.py`
- Modify: `backend/tests/test_phase2_canary_stage_receipts.py`

**Interfaces:**
- Produces: Relay receipt schema v2 with request ID, component, local stage events, exit code, and terminal status.
- Changes: requester accepts an injected Relay stage recorder.

- [ ] Add failing tests for Relay start, Proxy connect, request submitted, response wait/receive, Candidate write, ready marker, and error exit code 2.
- [ ] Prove Relay events do not claim any Provider stage.
- [ ] Implement stage recording around the existing requester and Candidate publisher.
- [ ] Keep receipt publication atomic and run Relay/stage tests to green.

### Task 4: Host Archive Before Cleanup

**Files:**
- Modify: `backend/app/services/phase2_canary_orchestrator.py`
- Modify: `backend/tests/test_phase2_canary_orchestrator.py`
- Modify: `backend/tests/test_phase2_canary_stage_receipts.py`

**Interfaces:**
- Adds: `ArchiveResult` and `CanaryBackend.archive_evidence(context, deadline)`.
- Changes: `CanaryRuntimeContext` has `proxy_output_dir` and `receipt_archive_dir`.
- Changes: `DockerCanaryBackend` stores child evidence and archives strict receipt bundles.

- [ ] Add failing tests proving Relay nonzero is `RELAY_NONZERO_EXIT`, real timeout is `RELAY_TIMEOUT`, signal is `RELAY_SIGNALLED`, and Proxy nonzero is `PROXY_NONZERO_EXIT`.
- [ ] Add failing call-order tests for archive before ledger terminal transition and before cleanup.
- [ ] Add failing tests for missing/malformed receipts, crash before archive, crash after archive, and cleanup failure preserving archived files.
- [ ] Implement separate Proxy output mount and precise `_run` error mapping.
- [ ] Implement strict receipt validation and atomic permanent archive publication.
- [ ] Reorder success and error finalization to archive, ledger, cleanup, Secret removal, workdir removal, lock release.
- [ ] Run all three required focused test files to green.

### Task 5: Historical Replay And Runtime Artifact Supersession

**Files:**
- Modify: `backend/tests/test_phase2_canary_orchestrator.py`
- Modify: `backend/app/services/phase2_canary_runtime_artifact.py`
- Modify: `backend/tests/test_phase2_canary_runtime_artifact.py`
- Modify: `docs/phase2-single-call-orchestrator-runbook.md`
- Create: `reports/phase2_provider_canary/superseded/e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b.json`

**Interfaces:**
- Preserves: old Candidate SHA `e77596da...61ade2b` as the superseded runtime approval input.
- Proves: historical request replay stays `DISPATCH_LEDGER_ONLY` and no missing stages are synthesized.

- [ ] Pin hashes for the historical ledger-derived diagnosis, runtime evidence, rejection receipt, and consumed report.
- [ ] Add a replay test that asserts the last proven stage remains `RELAY_RUNNING` and network stages remain unknown.
- [ ] Copy the e775 Candidate byte-for-byte into the superseded directory and assert its hash.
- [ ] Update runtime artifact supersession binding and run artifact tests.

### Task 6: Complete Offline Verification And New Candidate

**Files:**
- Modify: `reports/phase2_provider_canary/runtime_contract_candidate.json`
- Modify: `reports/phase2_provider_canary/runtime_contract_build_evidence.json`
- Create: `reports/phase2_provider_canary/observability_candidate.json`
- Create: `reports/tickflow_phase2b_canary_observability_eval.md`

**Interfaces:**
- Produces: a new immutable approval Candidate SHA without installing it.
- Produces: machine evidence with Provider attempts 0, AI calls 0, Provider HTTP `NOT_RUN_THIS_ROUND`, and historical evidence `UNCHANGED`.

- [ ] Run the required focused tests and the full backend suite.
- [ ] Run compileall and Ruff F821 over app, scripts, Proxy, and Relay sources.
- [ ] Verify historical evidence hashes before the offline build.
- [ ] Start Docker Desktop only if required for the approved offline `--network=none --pull=false` image build; do not start any runtime container.
- [ ] Build and verify immutable Proxy/Relay images offline, then remove any build-only containers/networks and verify residue zero.
- [ ] Generate the observability Candidate and report, with the e775 approval marked `SUPERSEDED_AND_PRESERVED`.
- [ ] Re-run all tests, hash checks, sensitive scans, `git diff --check`, and residue checks.
- [ ] Commit scoped implementation and evidence; do not install approval or run Canary.

