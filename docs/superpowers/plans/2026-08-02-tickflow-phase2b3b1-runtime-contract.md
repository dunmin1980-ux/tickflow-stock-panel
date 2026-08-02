# TickFlow Phase 2B-3B1 Runtime Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the single-symbol Canary runtime contract entirely offline and publish a new immutable approval candidate without reading the real Secret or calling a Provider.

**Architecture:** One strict committed timeout contract binds the Host Orchestrator, a dedicated Canary Relay, and the OpenAI Proxy. A persistent fsync-backed ledger and exclusive lock enforce one-attempt semantics, while an injectable runtime backend provides deterministic Mock crash and timing tests before two images are rebuilt with network disabled.

**Tech Stack:** Python 3.12, Pydantic v2, standard-library `fcntl`, `os`, `subprocess`, `http.client`, Docker, pytest.

## Global Constraints

- Start from Head `2bf7720b298c5f13f07ca3938536b16383122679` on `codex/tickflow-phase2-ai-review`.
- Fixed symbol is `000403.SZ`; fixed trade date is `2026-07-31`.
- Provider is `openai`; model is `gpt-5.6-terra`; endpoint alias is `openai_responses_v1`.
- Provider attempts, AI calls, TickFlow requests, and real public connections remain zero.
- Never read the real Keychain value in this phase.
- Preserve every historical evidence set and the old `c7614947...f96d` approval candidate.
- No PR, merge, cloud deployment, Obsidian write, Paper Trading, Telegram, OpenClaw, or Integrated Gold action.
- Every production behavior follows RED -> GREEN.

---

### Task 1: Canonical Runtime Contract

**Files:**
- Create: `backend/app/services/phase2_canary_runtime_contract.py`
- Create: `backend/app/services/phase2_canary_runtime_contract.json`
- Create: `backend/tests/test_phase2_canary_runtime_contract.py`
- Generate: `docker/phase2-openai-egress-proxy/runtime-contract.json`
- Generate: `docker/phase2-canary-relay/runtime-contract.json`

**Interfaces:**
- Produces: `CanaryRuntimeContract`, `load_runtime_contract()`, `canonical_runtime_contract_bytes()`, and `runtime_contract_sha256()`.
- Consumes: no runtime configuration or environment variables.

- [ ] Write strict tests for exact values, unknown/missing fields, timeout ordering, retry/attempt invariants, byte-identical generated copies, and environment independence.
- [ ] Run the focused file and require import/behavior failures.
- [ ] Implement the frozen strict model, no-follow bounded reader, canonical bytes, hash helper, and `--write/--check` CLI.
- [ ] Generate both Docker copies and rerun focused tests to GREEN.
- [ ] Commit the contract slice.

### Task 2: Atomic Candidate Relay

**Files:**
- Create: `docker/phase2-canary-relay/Dockerfile`
- Create: `docker/phase2-canary-relay/relay.py`
- Create: `docker/phase2-canary-relay/request.placeholder.json`
- Create: `docker/phase2-canary-relay/projection.placeholder.json`
- Create: `docker/phase2-canary-relay/output.placeholder`
- Create: `backend/tests/test_phase2_canary_relay.py`
- Modify: `docker/phase2-openai-egress-proxy/Dockerfile`
- Modify: `docker/phase2-openai-egress-proxy/proxy.py`
- Modify: `docker/phase2-openai-egress-proxy/responses-contract.json`

**Interfaces:**
- Produces: one-shot Relay exit behavior, atomic `candidate.json`, atomic `candidate.ready.json`, and sanitized `relay-receipt.json`.
- Consumes: request/projection files, the shared runtime contract, and the existing typed Relay envelope.

- [ ] Write failing tests for 75-second wait, 1/59/60/>60 boundaries with a fake clock, no retry, response bindings, partial write, marker ordering, duplicate Candidate, and sanitized receipts.
- [ ] Implement the dedicated Relay using only the standard library and no Secret mount.
- [ ] Update Proxy timeout loading to the canonical contract and enforce 10/60/60 monotonic deadlines.
- [ ] Regenerate the Responses contract so Relay generation controls bind to 75 seconds while Provider policy remains 60 seconds.
- [ ] Run Relay and Proxy suites to GREEN and commit.

### Task 3: Ledger and Exclusive Lock

**Files:**
- Create: `backend/app/services/phase2_canary_orchestrator.py`
- Create: `backend/tests/test_phase2_canary_orchestrator.py`

**Interfaces:**
- Produces: `AttemptLedger`, `AttemptLedgerStore`, `ExclusiveCanaryLock`, `CanaryRunIdentity`, and `recover_attempt_state()`.
- Consumes: canonical JSON bytes, mode-0700 state root, and injected wall/monotonic clocks.

- [ ] Write failing tests for every ledger state, durable dispatch marker, legal transitions, crash recovery, symlink/mode rejection, and atomic update failure.
- [ ] Implement atomic mode-0600 ledger publication with file and directory fsync.
- [ ] Write failing tests for concurrent lock rejection, no second request ID, stale lock plus terminal/nonterminal ledger handling, and unknown owner failure.
- [ ] Implement nonblocking `flock`, sanitized metadata, and fail-closed stale-lock logic.
- [ ] Run focused tests and commit.

### Task 4: One-Shot Orchestrator and Fault Matrix

**Files:**
- Modify: `backend/app/services/phase2_canary_orchestrator.py`
- Create: `backend/scripts/run_phase2_single_symbol_canary.py`
- Modify: `backend/tests/test_phase2_canary_orchestrator.py`

**Interfaces:**
- Produces: `run_single_symbol_canary(...) -> CanaryRunResult`, `DockerCanaryBackend`, `read_keychain_secret_once()`, and offline `MockCanaryBackend` tests.
- Consumes: approved artifact identity, fixed Facts/Projection, ledger/lock, runtime contract, Host Claims validator, and deterministic renderer.

- [ ] Write failing lifecycle tests for precheck through terminal cleanup and prove the Secret reader is called once only after every non-secret gate.
- [ ] Write the 20 required Mock fault scenarios and assert attempt count, ledger state, route, no retry, and cleanup for each.
- [ ] Implement the minimal Orchestrator state machine and injected backend protocol.
- [ ] Implement Docker command construction with no Host ports/socket, two networks, fixed image IDs, bounded waits, and cleanup deadline.
- [ ] Implement Keychain-to-mode-0600-file lifecycle without logging/hash/length metadata.
- [ ] Implement Host Candidate validation, deterministic rendering, isolated Canary routing, and sanitized evidence.
- [ ] Run focused tests and commit.

### Task 5: Immutable Runtime Artifact Candidate

**Files:**
- Create: `backend/app/services/phase2_canary_runtime_artifact.py`
- Create: `backend/scripts/build_phase2_canary_runtime_offline.py`
- Create: `backend/tests/test_phase2_canary_runtime_artifact.py`
- Generate: `reports/phase2_provider_canary/runtime_contract_candidate.json`
- Generate: `reports/phase2_provider_canary/runtime_contract_build_evidence.json`

**Interfaces:**
- Produces: strict aggregate candidate and fresh-build evidence for both images.
- Consumes: exact source/Dockerfile/contract/runbook hashes and pinned local base image.

- [ ] Write failing tests for exact input set, no-cache/network-none/pull-false commands, image labels/content/history, RootFS prefix, and old-candidate preservation.
- [ ] Implement fresh offline builds and aggregate candidate generation.
- [ ] Build both images with `--network none --pull=false --no-cache`, inspect content/history, and verify input hashes before/after.
- [ ] Securely rename the old local approval to a superseded mode-0600 audit file without reading Provider credentials.
- [ ] Rerun artifact verification and commit.

### Task 6: Verification, Review, and Reapproval Evidence

**Files:**
- Create: `reports/tickflow_phase2b_canary_runtime_contract_eval.md`
- Create: `docs/phase2-single-call-orchestrator-runbook.md`
- Update: this plan.

**Interfaces:**
- Produces: `PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL` and the exact new hash set.
- Consumes: focused/full tests, artifact evidence, protected-evidence hashes, residue scans, and an independent code review.

- [ ] Run the two required focused suites plus Relay/artifact suites.
- [ ] Run the full backend suite, compileall, Ruff F821, runtime-contract `--check`, and artifact `--verify`.
- [ ] Run all protected-evidence, sensitive-shape, Secret-residue, container/network/process-residue, and old-candidate hash checks.
- [ ] Perform independent security review of timeout propagation, ledger transitions, lock recovery, Secret lifecycle, candidate publication, cleanup, and second-attempt paths.
- [ ] Fix all Critical/Important findings and rerun affected plus full verification.
- [ ] Write the report and runbook with Provider/AI/TickFlow/public-network counts at zero.
- [ ] Commit, push the branch, verify remote Head, and stop without installing the new approval or making a real request.
