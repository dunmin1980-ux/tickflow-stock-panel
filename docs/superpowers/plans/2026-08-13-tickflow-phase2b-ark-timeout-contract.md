# TickFlow Phase 2B Ark Timeout Contract Implementation Plan

> Execute this plan in the existing `codex/tickflow-phase2-ai-review` worktree.
> All provider behavior is mocked. Do not launch a live canary or read secret
> content.

## Task 1: Freeze historical evidence

**Files:**
- Create: `reports/phase2_provider_ark/superseded/0fee5a1c509c77ae9b03107dfddf8caefa453673106cced13db4a4d6ac523ffc.json`
- Create: `reports/phase2_provider_ark/timeout_contract_history_baseline.json`
- Test: `backend/tests/test_ark_timeout_contract.py`

1. Hash the consumed request evidence, receipts, rejection record, ledger, and old
   approval candidate.
2. Preserve the old approval candidate byte-for-byte under `superseded/`.
3. Add a test that rejects mutation of any frozen historical file and confirms the
   consumed request remains `FAILED_AFTER_DISPATCH / CONSUMED` at
   `WAITING_FOR_PROVIDER_RESPONSE_HEADERS`.

## Task 2: Add failing timeout-contract tests

**Files:**
- Modify: `backend/tests/test_phase2_canary_runtime_contract.py`
- Create: `backend/tests/test_ark_timeout_contract.py`
- Modify: `backend/tests/test_phase2_canary_orchestrator.py`

1. Assert the canonical values are 10/180/180/195/210/15 and ordered.
2. Assert retry is zero and maximum attempts is one.
3. Model successful provider completion before 180 seconds and timeout at the
   180-second deadline using a controllable clock.
4. Assert the previous 60/75/90 candidate cannot authorize the new runtime.
5. Assert the new scope has zero attempts while the historical consumed scope is
   unchanged.
6. Run focused tests and confirm they fail for the expected old constants.

## Task 3: Implement the minimal contract propagation

**Files:**
- Create: `backend/app/services/phase2_ark_timeout_contract.json`
- Create: `backend/app/services/phase2_ark_timeout_contract.py`
- Modify: `backend/app/providers/ark_contract.py`
- Modify: `backend/app/services/phase2_canary_orchestrator.py`
- Synchronize: `docker/phase2-ark-egress-proxy/runtime-contract.json`
- Create: `backend/scripts/build_phase2_ark_runtime_images.py`

1. Add an Ark-only canonical contract and keep the OpenAI 60/75/90 contract
   byte-identical.
2. Synchronize the Ark proxy contract from the Ark source.
3. Build an Ark-specific relay image from the unchanged shared relay source and the
   Ark contract in an isolated temporary context.
4. Derive Ark provider and relay timeout policy from the Ark runtime contract
   rather than duplicate literals.
5. Preserve provider/model/endpoint/prompt/reasoning/schema/facts/renderer and
   retry behavior.
6. Re-run focused tests until green.

## Task 4: Produce deterministic mock evidence

**Files:**
- Modify: `backend/scripts/run_phase2_ark_mock_e2e.py`
- Modify: `backend/tests/test_ark_mock_e2e.py`
- Regenerate: `reports/phase2_provider_ark/mock_e2e.json`

1. Add fast, about-60-second, 179-second, and 180-second-deadline scenarios using
   virtual elapsed time.
2. Assert successful scenarios produce valid candidate/claims and deterministic
   rendering with retry zero.
3. Assert the deadline scenario times out without a retry.
4. Ensure the mock network is local-only and no public DNS/TLS call is made.

## Task 5: Rebuild immutable Ark artifacts

**Files:**
- Regenerate: `reports/phase2_provider_ark/ark_responses_contract.json`
- Regenerate: `reports/phase2_provider_ark/approval_candidate.json`
- Regenerate: `reports/phase2_provider_ark/approval_scope.json`
- Regenerate image identity evidence affected by the Ark proxy runtime contract.

1. Rebuild the Ark proxy image locally from the reviewed source.
2. Generate the new approval candidate and distinct approval scope.
3. Confirm new scope attempts are zero.
4. Confirm no new approval is installed and the old candidate remains preserved.

## Task 6: Verify and independently review

**Files:**
- Create: `reports/tickflow_phase2b_ark_timeout_contract_eval.md`
- Create or update: timeout-specific machine-readable evidence under
  `reports/phase2_provider_ark/`.

1. Run Ark focused tests and runtime compatibility tests.
2. Run backend full pytest, compileall, and Ruff F821.
3. Compare all historical hashes with the frozen baseline.
4. Scan generated artifacts and logs for credential shapes.
5. Conduct a focused independent review of the timeout-only diff and resolve all
   actionable findings without expanding scope.
6. Record zero real Ark attempts, zero real AI calls, no secret read, and no approval
   installation.

## Task 7: Commit and stop for reapproval

1. Run `git diff --check` and inspect the exact staged set.
2. Commit the timeout contract, tests, immutable generated artifacts, historical
   evidence, and evaluation report.
3. Verify the final tree is clean and record the final commit SHA.
4. Stop at `PHASE2B_ARK_TIMEOUT_CONTRACT_READY_FOR_REAPPROVAL` with next action
   `REQUEST_ARK_TIMEOUT_HASH_REAPPROVAL`.
