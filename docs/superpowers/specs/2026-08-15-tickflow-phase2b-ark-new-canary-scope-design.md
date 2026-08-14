# TickFlow Phase 2B Ark New Canary Scope Design

## Goal

Prepare one new `000403.SZ` Ark AI Canary Approval Candidate and Approval Scope from the already verified TLS Probe without making any network or Provider request.

## Fixed Runtime Identity

- Provider: `volcengine_ark`
- Model: `doubao-seed-2-1-turbo-260628`
- Endpoint alias: `ark_responses_cn_beijing_v1`
- Endpoint: `https://ark.cn-beijing.volces.com/api/v3/responses`
- Symbol: `000403.SZ`
- Trade date: `2026-07-31`
- Response mode: strict `json_schema`
- Retry: `0`
- Maximum Provider attempts: `1`
- Publish: `false`
- Timeouts: Provider `180s`, relay `195s`, host `210s`

## Artifact Model

The new Candidate uses schema version 2. It retains the existing runtime identity and adds:

- the source Git Head used to generate the Candidate;
- complete per-file source bindings for runtime, policy, schema, Facts, and generation inputs;
- an immutable successful TLS Probe binding containing the Probe ID, consumed Scope ID, result, and exact receipt SHA-256;
- the inherited runtime build provenance and the prior validated artifact-generation manifest as immutable inputs.

The current generation manifest remains outside the Candidate and binds the newly published Candidate and Scope. This avoids an impossible self-referential hash while preserving both input lineage and output publication integrity.

## TLS Evidence

The successful Probe `d73e91f766854ff7a64c0e725939eb64` is not rerun. A new non-sensitive binding record is derived from its preserved local receipt and ledger. It records only public connectivity results, immutable hashes, zero Provider/AI/HTTP activity, and the permanent `CONSUMED / PRESERVED / NON_REUSABLE` state.

The Candidate binds that record and the exact receipt digest. The runtime validator verifies the committed binding; the offline preparation additionally verifies the local preserved receipt bytes when present.

## Scope Isolation

The Approval Scope is derived from the new Candidate hash through the existing approval-scoped ledger namespace. It must have:

- `historical_attempts=0`;
- `attempt_availability=AVAILABLE`;
- no attempt directory;
- no overlap with historical Ark, OpenAI, or TLS Probe Scopes.

All historical requests, probes, ledgers, receipts, and rejected evidence remain byte-identical.

## Publication Flow

1. Preserve the current Ark Candidate, Scope, and generation manifest under content-addressed superseded paths.
2. Create and verify the successful TLS evidence binding.
3. Commit the source and binding changes. This commit becomes `current_git_head` in the new Candidate.
4. Generate Candidate, Scope, offline preflight, verification, and the new generation manifest atomically.
5. Run focused tests, static checks, residue checks, secret-presence-only gate, and an independent review.
6. Commit and push only offline evidence. No approval is installed and no live launcher is started.

## Failure Policy

Any hash mismatch, historical mutation, non-zero Scope attempt, residue, missing Keychain item, source/remote mismatch, or validator failure produces a blocked result. No automatic repair, retry, TLS Probe, Provider request, or AI call is allowed.
