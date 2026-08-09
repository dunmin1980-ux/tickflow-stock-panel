# TickFlow Phase 2B-3B6 Runtime Approval Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the eight confirmed Canary Runtime and Approval Contract defects, then publish a newly built, loadable Candidate for a separate hash approval.

**Architecture:** Keep the existing one-shot Host Orchestrator, Relay, Proxy, immutable image, and attempt-ledger boundaries. Extend their exact contracts only where the approved readiness identity and durable evidence lifecycle require it. Every production change starts with a focused failing test; the final Candidate is rebuilt offline and is never installed or executed in this phase.

**Tech Stack:** Python 3.12, Pydantic, pytest, Docker with distroless Python images, canonical JSON, fsync plus atomic rename, macOS local approval metadata.

## Global Constraints

- Fixed symbol: `000403.SZ`; fixed trade date: `2026-07-31`.
- Provider timeout `60`, Relay timeout `75`, Host timeout `90`, cleanup timeout `15` seconds.
- Retry count `0`; maximum Provider attempts per approval `1`.
- Do not change Phase 1 contracts, Phase 2 Facts, Typed Claims, calculation registry, deterministic renderer, Docker isolation, Secret injection, model, Responses schema, `store=false`, or attempt-ledger semantics.
- Historical requests `d59766101b63450e8148541d589a90bf` and `3d3e6adbe5b74c98ad18a2efe0fd1d0c` are immutable read-only fixtures.
- Candidate `45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127` is `INVALID_FOR_RUNTIME` and must remain preserved.
- No Keychain value read, public DNS/TLS probe, real Provider request, approval installation, cloud deployment, Gold, Paper Trading, Telegram, OpenClaw, or real Obsidian write.

---

### Task 1: Runtime Identity and Local Approval Security

**Files:**
- Modify: `backend/app/services/phase2_canary_orchestrator.py`
- Modify: `backend/tests/test_phase2_canary_orchestrator.py`

**Interfaces:**
- Consumes: `runtime_contract_candidate.json` artifact identity and canonical mode-0600 approval JSON.
- Produces: `ApprovedCanaryArtifacts.readiness_contract_sha256` and a trusted local approval reader.

- [ ] Add a test using the actual `45c5...` Candidate shape and prove the old loader rejects it because `readiness_contract_sha256` is unknown.
- [ ] Add tests that reject missing, unknown, or mutated readiness hashes and accept an exact 64-hex readiness hash.
- [ ] Add tests that reject a symlinked approval, a non-0700 approval directory, a foreign-owned leaf/directory, a non-0600 leaf, and replacement during open/read.
- [ ] Run the new tests and confirm each fails for the missing contract behavior.
- [ ] Add `readiness_contract_sha256` to `ApprovedCanaryArtifacts`, its digest validator, and the exact loader identity set.
- [ ] Read the local approval through no-follow descriptor checks; verify immediate approval directory and leaf ownership, modes, and regular-file identity before parsing.
- [ ] Run focused loader tests and confirm the old `45c5...` fixture remains blocked while a newly generated Candidate shape is loadable.

### Task 2: Pre-dispatch Evidence, Timeout Layering, and Stage Ordering

**Files:**
- Modify: `backend/app/services/phase2_canary_orchestrator.py`
- Modify: `backend/app/services/phase2_canary_observability.py` only if the existing result type cannot express Host timeout source.
- Modify: `backend/tests/test_phase2_canary_orchestrator.py`
- Modify: `backend/tests/test_phase2_canary_stage_receipts.py`

**Interfaces:**
- Consumes: Proxy readiness receipt, Relay receipt, child result, monotonic event timestamps, and fixed 75/90-second deadlines.
- Produces: durable pre-dispatch evidence and one precise terminal reason/source pair.

- [ ] Add a failing test where Proxy publishes `PROXY_READY` and readiness then fails before dispatch; assert the Proxy receipt is archived, attempt count stays zero, and the original failure reason survives.
- [ ] Add failing tests for Relay's 75-second deadline, Host's 90-second deadline, child nonzero exit, process error, and `terminal_reason_source=host_timeout` only on true Host expiry.
- [ ] Add a failing test in which the latest Relay event has the greatest monotonic timestamp and must become `last_proven_stage` regardless of component iteration order.
- [ ] Archive valid pre-dispatch Proxy evidence based on durable receipt presence rather than Provider attempt count.
- [ ] Keep Relay attach under the Host 90-second budget while Relay enforces its own 75-second deadline; map timeout, nonzero, and process errors without conflation.
- [ ] Select `last_proven_stage` from all persisted events by maximum monotonic timestamp with a deterministic equal-timestamp rule.
- [ ] Run the focused lifecycle and receipt suites.

### Task 3: Proxy Unexpected-exception Terminal Receipt

**Files:**
- Modify: `docker/phase2-openai-egress-proxy/proxy.py`
- Modify: `backend/tests/test_phase2_openai_proxy_contract.py`
- Modify: `backend/tests/test_phase2_proxy_readiness.py`

**Interfaces:**
- Consumes: a bound request ID, current stage receipt, and an unexpected `Exception` after local request acceptance.
- Produces: sanitized durable terminal receipt without exception text or Secret metadata.

- [ ] Add a failing test that injects `RuntimeError` after the request is bound and verifies a terminal receipt replaces `PROXY_READY`.
- [ ] Assert HTTP `502`, terminal status `UPSTREAM_REJECTED`, correct attempt count for the reached stage, and zero exception-text leakage.
- [ ] Add one sanitized `Exception` path after request binding; leave `BaseException` uncaught and retain the existing typed `ProxyError` mapping.
- [ ] Run Proxy contract and readiness tests.

### Task 4: Exact Preflight Allowlist and Approval-parity Mock Evidence

**Files:**
- Modify: `backend/scripts/validate_phase2_canary_provider_config.py`
- Modify: `backend/scripts/run_phase2_canary_local_path_mock_e2e.py`
- Modify: `backend/tests/test_phase2_canary_provider_config.py`
- Modify: `backend/tests/test_phase2_canary_local_path_mock_e2e.py`
- Generate: `reports/phase2_provider_canary/local_path_mock_e2e.json`
- Generate: `reports/phase2_provider_canary/local_path_pre_fix_replay.json`

**Interfaces:**
- Consumes: externally supplied expected Candidate SHA, exact path-to-hash manifests, superseded pre-fix artifact identity, and controlled Mock topology substitutions.
- Produces: hash-pinned offline preflight evidence, durable pre-fix reproduction, and three post-fix runs with approval-loader parity.

- [ ] Add a failing test that the offline preflight accepts only the exact new Local Path files when every expected SHA matches and rejects unknown or mutated files.
- [ ] Add a failing test that the Mock runner rejects a Candidate not equal to an externally supplied expected SHA.
- [ ] Add failing tests that require an explicit topology-delta manifest and immutable pre-fix replay evidence.
- [ ] Replace the obsolete exact allowlists with explicit path-to-hash validation; do not use directory globs.
- [ ] Require `--expected-candidate-sha256` for the Mock runner and pass it through the real runtime approval loader contract.
- [ ] Record only declared test deltas: local Mock Provider endpoint, internal-only Mock egress, and test CA injection. Preserve all other image, mount, Relay, request, Projection, and runtime identities.
- [ ] Reproduce and persist the pre-fix Proxy-readiness failure using superseded immutable artifact identities without public access.
- [ ] Run three fixed Mock E2E executions and assert approval loading, seven stages, Candidate, Claims, renderer determinism, retry zero, historical immutability, and zero residue.

### Task 5: Immutable Rebuild and Independent Review

**Files:**
- Modify: `backend/app/services/phase2_canary_runtime_artifact.py` only for the exact identity contract.
- Modify: `backend/app/services/phase2_openai_proxy_artifact.py` only for the exact identity contract.
- Generate: `reports/phase2_provider_canary/runtime_contract_candidate.json`
- Generate: `reports/phase2_provider_canary/runtime_contract_build_evidence.json`
- Update: `reports/tickflow_phase2b_canary_local_path_eval.md`

**Interfaces:**
- Consumes: all fixed source, Dockerfile, contract, Facts, Projection, and superseded Candidate hashes.
- Produces: a new immutable Candidate SHA and complete offline evidence.

- [ ] Run all related focused tests and confirm every red/green contract is covered.
- [ ] Run backend full pytest, compileall over `app`, `scripts`, and Proxy sources, Ruff F821, and `git diff --check`.
- [ ] Build Proxy and Relay with `--network=none --pull=false --no-cache`; verify image IDs, contents, history, runtime users, and all source hashes.
- [ ] Verify the new Candidate is loadable using a temporary canonical approval and that `45c5...` remains rejected and preserved.
- [ ] Dispatch an independent read-only review covering all eight confirmed issues and fix every actionable finding before READY.
- [ ] Re-run focused, full, static, immutable, sensitive-shape, historical-hash, and residue verification after review.

### Task 6: Git Governance and Reapproval Stop

**Files:**
- Commit only the files named by Tasks 1-5 and their exact tests/evidence.

**Interfaces:**
- Consumes: green verification, no actionable review findings, and a new Candidate SHA.
- Produces: clean local and fork-remote branch ready for a separate hash approval.

- [ ] Confirm historical evidence hashes are unchanged and Provider/AI/public-network counters remain zero.
- [ ] Confirm no container, network, process, lock, Secret-file, request, response, or staging residue remains.
- [ ] Commit the approved scope and push `codex/tickflow-phase2-ai-review` to `fork`.
- [ ] Confirm local Head equals fork remote Head and worktree is clean.
- [ ] Report `PHASE2B_CANARY_LOCAL_PATH_READY_FOR_REAPPROVAL` with the full new immutable hash set.
- [ ] Stop without installing an approval or launching a Provider request.
