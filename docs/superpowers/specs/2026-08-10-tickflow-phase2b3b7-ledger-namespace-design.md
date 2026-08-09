# TickFlow Phase 2B-3B7 Approval-scoped Attempt Ledger Namespace Design

## Status

Approved design: `APPROVAL_SCOPED_LEDGER_NAMESPACE`.

This change is offline-only. It must not read the Keychain secret, construct an
Authorization header, resolve DNS, perform TLS, call OpenAI, or change either
historical Provider attempt.

## Problem

The current one-shot orchestrator stores one ledger at
`runtime-v1/attempt-ledger.json`. Both historical requests consumed their own
approved attempts, but the current launcher treats either terminal ledger as a
global lifetime ban. That conflates global runtime safety with the Provider
attempt budget.

The budget identity is instead:

1. approval candidate SHA-256;
2. symbol;
3. provider ID;
4. exact model ID;
5. endpoint alias;
6. ledger namespace version.

Global mutual exclusion and residue checks remain global.

## Scope Identity

`ledger_namespace_version` is fixed to `1`. The canonical material is UTF-8,
has no trailing newline, and places the separator on its own line:

```text
tickflow-canary-ledger-v1
|
<approval_candidate_sha256>
|
000403.SZ
|
openai
|
gpt-5.6-terra
|
openai_responses_v1
```

`approval_scope_id` is the lowercase SHA-256 hex digest of those exact bytes.
The material contains no Secret or Secret-derived value.

## Storage

The existing legacy ledger schema and paths remain byte-for-byte read-only:

```text
~/Library/Application Support/TickFlowPhase2Canary/runtime-v1/
  attempt-ledger.json
  history/<request_id>/attempt-ledger.json
```

Future attempts use:

```text
reports/phase2_provider_canary/attempts/
  <approval_scope_id>/
    <request_id>/
      ledger.json
```

Every new directory is a real directory with mode `0700`. Every ledger is a
regular file with mode `0600`, written using staging, fsync, atomic rename, and
directory fsync. Symlinks, noncanonical JSON, unexpected path names, and
duplicate request IDs fail closed.

The new ledger is a strict wrapper around the existing attempt record. It adds
`ledger_namespace_version`, `approval_scope_id`, and `exact_model_id` without
changing or backfilling the legacy ledger schema.

## Preflight

The production sequence is:

1. load the installed Runtime Approval;
2. verify the immutable Runtime Candidate;
3. compute the approval scope identity;
4. acquire the existing global exclusive lock;
5. scan legacy ledgers, scoped ledgers, historical evidence, and live residue;
6. classify the current scope;
7. generate and globally validate a new request ID;
8. create the scoped ledger before any Secret read or network dispatch.

Current-scope states at or after `NETWORK_DISPATCH_STARTED`, including
`FAILED_AFTER_DISPATCH`, `ATTEMPT_CONSUMED_UNKNOWN`, and
`CLEANUP_COMPLETED`, consume the scope. `FAILED_BEFORE_DISPATCH` is terminal
but does not consume a Provider attempt. A nonterminal prior ledger blocks
globally until its state is resolved.

Historical terminal ledgers are nonblocking only when all of these are true:

- their candidate identity resolves through an immutable current or superseded
  Runtime Candidate;
- the candidate differs from the current approval candidate;
- request IDs are globally unique;
- terminal state and attempt count agree;
- matching runtime evidence has zero recorded runtime residue, or the frozen
  historical classification explicitly records an unknown consumed attempt;
- no matching container, network, or held/unknown global lock remains.

Unparseable identity, contradictory state, unresolved approval identity,
unknown nonterminal state, duplicate request ID, or live residue returns
`GLOBAL_LEDGER_SAFETY_BLOCKED`.

## Lock And Request IDs

The lock remains at `runtime-v1/.runtime.lock`; namespace separation never
permits concurrent orchestrators. Lock identity includes the approval scope ID.
A stale nonempty lock is recoverable only for a provable terminal
`FAILED_BEFORE_DISPATCH` ledger in that same scope. Otherwise it fails closed.

Request IDs remain 32 lowercase hex characters and must be unique across:

- legacy current and history ledgers;
- all scoped ledger directories;
- historical evidence, rejection, and receipt paths.

Collision returns `REQUEST_ID_COLLISION_BLOCKED` before Secret access.

## Mock Contract

The exact local Mock E2E runs three times. Each run receives a deterministic,
Mock-only approval identity derived from the externally approved Runtime
Candidate plus its run index, producing three distinct approval scope IDs.
The production candidate is still verified once per run, while the Mock-only
identity cannot be accepted by the production approval loader.

Each run must finish with one Mock dispatch, retry zero, valid Candidate and
Claims, deterministic renderer output, and zero container, network, Secret,
and temporary residue. Historical evidence hashes must match before and after.

## Candidate Lifecycle

The installed `769f4d762594ac496bbaf14b90b8dd3cb7eae572ac9bf05f0484abfe3b86ffaf`
approval becomes `SUPERSEDED_AND_PRESERVED` before runtime source changes.
Its Candidate is copied byte-for-byte into `reports/phase2_provider_canary/superseded/`.

After TDD and Mock verification, the offline builder creates a new immutable
Runtime Candidate binding the changed orchestrator and launcher. The new
Candidate is verified but not installed. No real Provider attempt is authorized.

## Verification

The implementation must prove at least these contracts:

- old consumed scope plus new approval is available;
- consumed or unknown current scope is blocked;
- terminal history is preserved byte-for-byte;
- unknown historical nonterminal or unresolved identity blocks globally;
- scope IDs are deterministic, Secret-free, and collision resistant;
- request IDs are globally unique;
- the lock remains global;
- a post-dispatch current scope cannot be reopened;
- exact local Mock E2E passes three independent approval scopes;
- all focused and backend tests, compileall, Ruff F821, and `git diff --check`
  pass;
- real Provider attempts and AI calls remain zero.

## Completion State

The only successful terminal state for this work is:

```text
PHASE2B_CANARY_LEDGER_NAMESPACE_READY_FOR_REAPPROVAL
```

The next action is `REQUEST_LEDGER_NAMESPACE_HASH_REAPPROVAL`.
