# TickFlow Phase 2B Ark Provider Timeout Contract Design

## Status and objective

The consumed Ark request `2f17745f58534063bdd7eda1eb0d16f1` is immutable and remains
`FAILED_AFTER_DISPATCH / CONSUMED`. Its observed terminal stage is
`WAITING_FOR_PROVIDER_RESPONSE_HEADERS`: TCP, TLS, and request-body transmission
completed, while no response headers arrived before the previous 60-second
provider deadline.

This change varies one runtime dimension only: the Ark timeout contract. It does
not alter provider identity, model, endpoint, request payload, reasoning settings,
Facts, projection, schemas, validation, rendering, secrets, or ledger semantics.
No real provider request is permitted while implementing or validating the change.

## New timeout contract

| Boundary | Value |
|---|---:|
| Provider connect | 10 seconds |
| Provider read | 180 seconds |
| Provider total | 180 seconds |
| Relay candidate wait | 195 seconds |
| Host orchestrator | 210 seconds |
| Cleanup | 15 seconds |
| Retry | 0 |
| Maximum provider attempts | 1 |

The runtime invariant is `180 < 195 < 210`. These values are immutable runtime
artifacts and cannot be overridden by environment variables.

## Source of truth and propagation

`backend/app/services/phase2_ark_timeout_contract.json` is the Ark-only canonical
contract. The existing OpenAI runtime contract remains byte-identical at
60/75/90. Ark request policy and relay request generation consume the Ark
contract, so there is no separate hard-coded 60/75-second Ark path.

The Ark proxy receives a synchronized copy. The unchanged relay source is built
as a distinct Ark relay image with the Ark contract in an isolated build context;
the shared OpenAI relay image and its embedded contract remain unchanged.

Artifacts whose identity binds the contract are rebuilt after the source change:

- canonical Ark runtime contract and synchronized Ark copies;
- Ark proxy image and immutable image identity;
- Ark relay image built from the unchanged relay source and Ark contract;
- Ark approval candidate and approval scope evidence;
- Ark mock evidence and timeout-specific evaluation evidence.

The orchestrator or launcher source hash changes only if its source changes. A
runtime identity change does not imply an unrelated source rewrite.

## Historical isolation

The previous Ark approval candidate
`0fee5a1c509c77ae9b03107dfddf8caefa453673106cced13db4a4d6ac523ffc`
is copied byte-for-byte into a superseded evidence location before the active
candidate path is regenerated. It remains discoverable for historical ledger
validation and is never accepted for the new runtime contract.

The consumed approval scope
`898079e765f188ba48b2de143ff5fce81e1191ec7bfc6cb773352b2bed90982d`
and request evidence are hashed before and after the work. The new candidate gets
a distinct approval scope with zero historical attempts. No ledger state is
deleted, reset, or edited.

No new local approval is installed. The previously installed approval is not
treated as authorization for the regenerated candidate.

## Deterministic timeout harness

Tests and mock evidence use a controllable monotonic clock. They model:

- a fast successful response;
- a response at approximately 60 seconds;
- a response at 179 seconds;
- a response at the 180-second deadline.

The first three success paths must produce a valid candidate, valid claims, and
deterministic rendering with no retry. The deadline path must terminate as a
provider timeout without sleeping for wall-clock minutes and without a retry.
Relay and host boundaries are independently asserted at 195 and 210 seconds.

## Safety and acceptance

Validation is entirely local: focused timeout tests, Ark contract and mock tests,
runtime compatibility tests, backend full tests, compileall, Ruff F821, secret
scanning, historical evidence hash comparison, and an independent focused review.

Acceptance requires:

- all new timeout and compatibility tests pass;
- old 60/75/90 approval identity cannot authorize the new artifacts;
- the historical consumed scope and request bytes are unchanged;
- the new approval scope reports zero attempts;
- no real Ark or OpenAI call occurs;
- no secret content is read and no approval is installed;
- the independent review has no actionable findings.

The terminal state is
`PHASE2B_ARK_TIMEOUT_CONTRACT_READY_FOR_REAPPROVAL`, followed only by
`REQUEST_ARK_TIMEOUT_HASH_REAPPROVAL`.
