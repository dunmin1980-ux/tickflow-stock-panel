# TickFlow Phase 2B Minimal Ark Canary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare a direct Host-to-Ark, strict-structured-output, one-shot Canary for `000403.SZ` without reading the real Secret or sending a real Provider request.

**Architecture:** A new isolated service reuses the existing Ark Adapter, deterministic Facts Projection, typed Claims validator, and Renderer. It sends through one fixed `httpx` HTTPS client in future live execution, while offline verification injects `httpx.MockTransport`; no Relay, Proxy, Docker network, Dispatch Gate, or TLS Probe participates.

**Tech Stack:** Python 3.13, Pydantic v2, httpx, pytest, existing Phase 2 Facts/Claims services, macOS Keychain CLI for future approved execution only.

## Global Constraints

- Provider is exactly `volcengine_ark`.
- Model is exactly `doubao-seed-2-1-turbo-260628`.
- Endpoint is exactly `https://ark.cn-beijing.volces.com/api/v3/responses`.
- Symbol is exactly `000403.SZ`; trade date is exactly `2026-07-31`.
- `text.format.type=json_schema`, `strict=true`, `stream=false`, `store=false`, and `tools=[]` are mandatory.
- `retry=0`, `maximum_attempts=1`, and `can_publish=false` are immutable.
- TLS and hostname verification remain enabled; redirects and environment proxies are disabled.
- Real Ark requests, real AI calls, Keychain Secret reads, Approval installation, Proxy/Relay changes, new Probe systems, Paper Trading, Obsidian writes, and cloud deployment are prohibited.
- Existing Proxy/Relay and all historical evidence remain byte-for-byte unchanged.

---

### Task 1: Fixed Minimal Request Contract and Execution Inputs

**Files:**
- Create: `backend/app/services/phase2_minimal_ark_request_contract.json`
- Create: `backend/app/services/phase2_minimal_ark_canary.py`
- Create: `backend/tests/test_phase2_minimal_ark_canary.py`

**Interfaces:**
- Consumes: `build_ark_responses_request(ProviderRequest) -> dict`, `build_worker_projection(...) -> dict`, `WorkerClaimsCandidate.model_json_schema(...) -> dict`.
- Produces: `MinimalArkRequestContract`, `MinimalExecutionInputs`, `load_minimal_request_contract()`, `build_minimal_execution_inputs(repo_root, request_id)`.

- [ ] **Step 1: Write failing identity and hash-binding tests**

Add tests that construct execution inputs from `reports/phase2_facts/000403SZ_facts.json` and assert the exact Provider/model/URL, Facts SHA, Projection SHA, trade date, strict JSON schema, retry, maximum attempts, and publication flag. Parametrize mutations of Provider, model, Facts SHA, Projection SHA, `strict`, retry, and maximum attempts and require `MinimalArkCanaryError`.

```python
def test_execution_inputs_bind_frozen_facts_and_strict_request() -> None:
    inputs = build_minimal_execution_inputs(REPO_ROOT, "a" * 32)
    assert inputs.facts_sha256 == EXPECTED_FACTS_SHA
    assert inputs.projection_sha256 == EXPECTED_PROJECTION_SHA
    assert inputs.request_body["text"]["format"]["type"] == "json_schema"
    assert inputs.request_body["text"]["format"]["strict"] is True
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_phase2_minimal_ark_canary.py -q`

Expected: import failure for `app.services.phase2_minimal_ark_canary`.

- [ ] **Step 3: Add the immutable JSON contract and strict loaders**

Define exact scheme, host, port, path, method, timeout values, TLS/hostname verification, redirect behavior, `trust_env=false`, structured-output fields, retry, maximum attempts, response size, and `can_publish=false`. Reject unknown fields and all mismatches through a frozen Pydantic model.

- [ ] **Step 4: Build execution inputs from frozen Facts and existing adapters**

Read Facts with `O_NOFOLLOW`, recompute its SHA, build the existing deterministic Projection, create `ProviderRequest`, then call `build_ark_responses_request`. Compute `prompt_sha256` from the canonical request `input` built with an all-zero placeholder request ID so the live request ID is not research content.

- [ ] **Step 5: Run the focused tests and commit**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_phase2_minimal_ark_canary.py -q`

Commit: `feat: add minimal Ark request contract`

---

### Task 2: One-Shot Ledger, Secret Boundary, and Direct HTTPS Transport

**Files:**
- Modify: `backend/app/services/phase2_minimal_ark_canary.py`
- Modify: `backend/tests/test_phase2_minimal_ark_canary.py`

**Interfaces:**
- Consumes: `MinimalExecutionInputs` from Task 1.
- Produces: `MinimalAttemptLedger`, `reserve_minimal_attempt(...)`, `mark_minimal_dispatch(...)`, `finalize_minimal_attempt(...)`, `read_minimal_ark_keychain_secret_once(...)`, `ArkHostTransport.send(...)`.

- [ ] **Step 1: Write failing one-shot and Secret tests**

Cover atomic `PREPARED` creation, exactly one `DISPATCH_STARTED` transition, refusal of a second dispatch, consumed HTTP error/timeout, zero retry, failure-before-dispatch not consumed, Keychain command identity, and absence of the sentinel Secret from serialized ledger/log/evidence values.

```python
def test_second_dispatch_is_rejected_before_transport(tmp_path: Path) -> None:
    ledger = reserve_minimal_attempt(tmp_path, scope_id=SCOPE, request_id="a" * 32, ...)
    mark_minimal_dispatch(ledger, now="2026-08-16T00:00:00Z")
    with pytest.raises(MinimalArkCanaryError, match="attempt_already_consumed"):
        mark_minimal_dispatch(ledger, now="2026-08-16T00:00:01Z")
```

- [ ] **Step 2: Run tests and confirm RED**

Run the focused test file and confirm missing ledger/transport behavior.

- [ ] **Step 3: Implement durable minimal ledger**

Use regular files only, mode `0600`, parent directories mode `0700`, canonical JSON, staging write, file fsync, atomic rename, and directory fsync. Store only the approved bounded fields; never store Secret, headers, body, URL query, or response body.

- [ ] **Step 4: Implement future-only Keychain reader**

Run only `security find-generic-password -w -s tickflow-phase2-canary-volcengine-ark`, reject empty/multiline values, and return the value only to the caller. Do not invoke this function from builders or Mock tests.

- [ ] **Step 5: Implement direct verified HTTPS transport**

Use `ssl.create_default_context()` with `CERT_REQUIRED`, `check_hostname=True`, minimum TLS 1.2, and an `httpx.Client` configured with `verify=context`, `follow_redirects=False`, `trust_env=False`, and fixed timeout. Stream and bound response bytes. The only Authorization header is built immediately before the POST and is never logged.

- [ ] **Step 6: Run tests and commit**

Commit: `feat: add one-shot Ark host transport`

---

### Task 3: Candidate Validation, Claims Validation, and Deterministic Rendering

**Files:**
- Modify: `backend/app/services/phase2_minimal_ark_canary.py`
- Modify: `backend/tests/test_phase2_minimal_ark_canary.py`

**Interfaces:**
- Consumes: `adapt_ark_responses_envelope(...)`, `validate_isolated_candidate(...)`.
- Produces: `MinimalCanaryResult`, `execute_minimal_ark_canary(...)`.

- [ ] **Step 1: Write failing success and terminal-failure tests**

Use `httpx.MockTransport` to assert the exact POST path, fixed body contract, and one Authorization header without recording its value. Cover success, non-200, redirect, timeout, non-JSON, invalid envelope, invalid structured output, invalid Claims, and Renderer rejection. Every post-dispatch failure must produce a terminal consumed ledger and exactly one Mock call.

- [ ] **Step 2: Run tests and confirm RED**

Expected: `execute_minimal_ark_canary` missing.

- [ ] **Step 3: Implement one linear execution pipeline**

Validate Candidate/Scope/approval and frozen inputs, reserve ledger, read Secret once, mark dispatch, send once, adapt envelope, validate typed Claims, validate deterministic Renderer, and finalize. Do not catch a failure and invoke the transport again.

- [ ] **Step 4: Require whole-Candidate rejection**

If the Ark Adapter, worker protocol, Claims validator, or Renderer reports any error, set Candidate/Claims states to rejected/blocked, preserve `can_publish=false`, and finalize without deleting or repairing Claims.

- [ ] **Step 5: Run focused plus existing Ark/Claims/Renderer tests and commit**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_minimal_ark_canary.py \
  tests/test_ark_responses_adapter.py \
  tests/test_phase2_claims.py \
  tests/test_phase2_claims_renderer.py -q
```

Commit: `feat: validate minimal Ark claims pipeline`

---

### Task 4: Minimal Launcher, Offline Builder, Candidate, Scope, and Mock E2E

**Files:**
- Create: `backend/scripts/run_phase2_minimal_ark_canary.py`
- Create: `backend/scripts/build_phase2_minimal_ark_canary_offline.py`
- Create: `backend/tests/test_phase2_minimal_ark_mock_e2e.py`
- Generate locally: `reports/phase2_provider_ark/minimal_canary/historical_evidence_manifest.json`
- Generate locally: `reports/phase2_provider_ark/minimal_canary/mock_e2e.json`
- Generate locally: `reports/phase2_provider_ark/minimal_canary/approval_candidate.json`
- Generate locally: `reports/phase2_provider_ark/minimal_canary/approval_scope.json`
- Generate locally: `reports/phase2_provider_ark/minimal_canary/verification.json`

**Interfaces:**
- Consumes: all Task 1-3 service interfaces.
- Produces: `build_minimal_candidate(...)`, `validate_minimal_candidate(...)`, `minimal_scope_for_candidate(...)`, `run_minimal_mock_e2e(...)`, and the future live CLI.

- [ ] **Step 1: Write failing Candidate/Scope and Mock E2E tests**

Assert that Candidate contains only the approved minimal identities, omits every Proxy/Relay/TLS Probe/network/Dispatch Gate field, binds the final Git Head and source hashes, and yields a deterministic available Scope with `historical_attempts=0`. Run three fixed successful Mock request IDs and require identical rendered SHA values, one call each, zero Secret hits, and zero residue.

- [ ] **Step 2: Add failure no-retry scenarios**

Mock HTTP error, timeout, malformed structured output, and invalid Claims. Assert each transport call count is one, ledger is consumed, retry remains zero, and no second request ID appears.

- [ ] **Step 3: Implement the future live launcher without executing it**

Require exact local approval file mode `0600`, approval directory mode `0700`, Candidate/Scope SHA match, clean/available Scope, and exclusive lock. Call the Keychain reader only after all gates. Print only bounded terminal metadata.

- [ ] **Step 4: Implement the offline builder**

Verify current Head, clean worktree, local/fork ref equality, frozen Facts/Projection, historical evidence hashes, and zero attempts. Run Mock E2E only, write local evidence atomically, generate Candidate and Scope, then run a self-validation pass. Do not inspect Keychain and do not instantiate production transport.

- [ ] **Step 5: Run focused tests and commit implementation**

Commit: `feat: prepare minimal Ark canary approval`

- [ ] **Step 6: Push implementation before generating Head-bound artifacts**

Run: `git push fork codex/tickflow-phase2-ai-review`

Verify local and fork refs match exactly.

- [ ] **Step 7: Run the offline builder once**

Generate local ignored evidence bound to the final pushed Head. Confirm Scope availability is `AVAILABLE` with zero attempts and no Keychain read.

---

### Task 5: Verification, Independent Review, and Closeout

**Files:**
- Generate locally: `reports/tickflow_phase2b_minimal_ark_canary_eval.md`
- Local-only exact ignores: `.git/info/exclude`

**Interfaces:**
- Consumes: generated Candidate, Scope, Mock evidence, and all focused tests.
- Produces: final `MINIMAL_ARK_CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL` evidence.

- [ ] **Step 1: Run required focused verification**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_minimal_ark_canary.py tests/test_phase2_minimal_ark_mock_e2e.py -q
PYTHONPATH=. .venv/bin/pytest tests/test_ark_responses_adapter.py tests/test_phase2_claims.py tests/test_phase2_claims_renderer.py -q
.venv/bin/python -m compileall -q app scripts/run_phase2_minimal_ark_canary.py scripts/build_phase2_minimal_ark_canary_offline.py
.venv/bin/ruff check app scripts/run_phase2_minimal_ark_canary.py scripts/build_phase2_minimal_ark_canary_offline.py --select F821
cd ..
git diff --check
```

- [ ] **Step 2: Scan for prohibited behavior and secrets**

Verify the new path contains no Proxy/Relay/Docker/TLS Probe imports, no fallback, no retry loop, no `verify=False`, no environment Secret, and no sensitive patterns in generated evidence.

- [ ] **Step 3: Verify historical bytes and attempt counts**

Recompute all hashes in the historical manifest and require exact equality. Confirm real Ark attempts and real AI calls remain zero and the new Scope has zero attempts.

- [ ] **Step 4: Run independent focused review**

Review only second-request risk, Secret leakage, identity fixation, strict schema, Facts/Projection binding, real Claims/Renderer execution, publication safety, hidden fallback, and legacy-scope reuse. Require `NO P0/P1 ACTIONABLE FINDINGS`; P2 does not block.

- [ ] **Step 5: Write closeout and verify clean state**

Write the exact final status and hashes to `reports/tickflow_phase2b_minimal_ark_canary_eval.md`, exclude generated local evidence by exact path, and require:

```text
Local / fork remote=MATCHED
Worktree=CLEAN
Real Ark attempts=0
Real AI calls=0
Secret content read=NO
```

Stop at `MINIMAL_ARK_CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL`. Do not install Approval or run the live launcher.
