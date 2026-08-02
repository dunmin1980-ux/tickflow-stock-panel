# TickFlow Phase 2B-3B1 Canary Runtime Contract Design

## Scope

This phase repairs the offline runtime contract for the single approved symbol
`000403.SZ`. It does not read the real Keychain value, open a Provider
connection, call TickFlow, render into a real Obsidian vault, or start Paper
Trading. The terminal state is
`PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL`.

## Chosen Architecture

Use one committed, versioned runtime-contract JSON document as the timeout and
single-attempt authority. The Host imports it through a strict Pydantic model;
the dedicated Canary Relay and OpenAI Proxy receive byte-identical generated
copies in their image contexts and verify its SHA-256 before serving. No timeout
is environment-configurable.

Create a dedicated `phase2-canary-relay` image instead of modifying the
previous Mock Provider Relay. This preserves the Phase 2B-3A evidence while
allowing the real Canary relay to wait 75 seconds, atomically publish a
Candidate, and emit a ready marker. The OpenAI Proxy remains the sole public
egress component and uses the same contract for 10-second connect, 60-second
read/total, zero retry, and one maximum attempt.

Create a Host-only one-shot Orchestrator. It owns the exclusive lock,
persistent attempt ledger, one-time Secret read, ephemeral filesystem,
container/network lifecycle, candidate recovery, Host validation,
deterministic rendering, evidence publication, and cleanup. Runtime behavior is
injected behind a narrow backend protocol so the complete state machine can be
fault-tested without a real Secret or network.

## Rejected Approaches

1. Reuse the old Relay with its two-second timeout. A slow Provider request can
   continue after the Relay has already failed, consuming the only attempt
   without returning a Candidate.
2. Bypass the Relay and invoke the Proxy from the Host. This breaks the approved
   isolation topology and removes a validated protocol boundary.
3. Add environment-variable timeout overrides. This makes reviewed image hashes
   insufficient to prove the effective runtime policy.
4. Keep the attempt ledger in the ephemeral run directory. A Host crash would
   erase the only proof that dispatch may have occurred.

## Canonical Timeout Contract

The immutable values are:

```text
canary_runtime_contract_version=1
provider_connect_timeout_seconds=10
provider_read_timeout_seconds=60
provider_total_timeout_seconds=60
relay_candidate_wait_timeout_seconds=75
host_orchestrator_timeout_seconds=90
cleanup_timeout_seconds=15
retry_count=0
maximum_provider_attempts=1
```

Validation requires:

```text
provider_total_timeout_seconds < relay_candidate_wait_timeout_seconds
relay_candidate_wait_timeout_seconds < host_orchestrator_timeout_seconds
provider_connect_timeout_seconds <= provider_total_timeout_seconds
provider_read_timeout_seconds <= provider_total_timeout_seconds
retry_count == 0
maximum_provider_attempts == 1
```

All deadlines use `time.monotonic()`. Cleanup has its own deadline and cannot
extend or reset the Provider deadline.

## Persistent State and Locking

The production state directory remains outside Git in the existing mode-0700
TickFlow Phase 2 Canary application-support directory. It contains a mode-0600
ledger and lock file. Both reject symlink parents and non-regular files.

The ledger binds the request ID, symbol, trade date, Facts SHA, Projection SHA,
approval-candidate SHA, Proxy image ID, Relay image ID, and timeout-contract
SHA. Updates use a temporary mode-0600 file, file `fsync`, atomic rename, and
directory `fsync`.

State transitions are:

```text
PREPARED
NETWORK_DISPATCH_STARTED
PROVIDER_RESPONSE_RECEIVED
CANDIDATE_COLLECTED
HOST_VALIDATION_COMPLETED
CLEANUP_COMPLETED
FAILED_BEFORE_DISPATCH
FAILED_AFTER_DISPATCH
ATTEMPT_CONSUMED_UNKNOWN
```

`NETWORK_DISPATCH_STARTED` is durably written immediately before handing
control to the runtime backend. From that state onward, an attempt is consumed.
Recovery from an interrupted nonterminal post-dispatch state always becomes
`ATTEMPT_CONSUMED_UNKNOWN`; it never retries.

The Orchestrator takes a nonblocking `flock` before generating a request ID.
Lock metadata records only PID, start time, symbol, provider, endpoint alias,
and approval hash. A second process cannot create a request ID. An unlocked but
stale lock file is accepted only when the ledger proves a terminal
pre-dispatch failure; all other stale or unknown combinations fail closed.

## Runtime Lifecycle

The production sequence is:

```text
PRECHECK
ACQUIRE_EXCLUSIVE_LOCK
VERIFY_APPROVED_ARTIFACTS
VERIFY_ATTEMPT_LEDGER
CREATE_EPHEMERAL_WORKDIR
READ_KEYCHAIN_SECRET_ONCE
WRITE_0600_SECRET_FILE
CREATE_INTERNAL_NETWORKS
START_PROXY
START_RELAY
DISPATCH_SINGLE_REQUEST
WAIT_FOR_CANDIDATE
COLLECT_RESPONSE
HOST_VALIDATE
DETERMINISTIC_RENDER
WRITE_ATOMIC_EVIDENCE
CLEANUP
RELEASE_LOCK
TERMINAL_STATE
```

The Relay network is internal. The Proxy joins that network and one dedicated
egress bridge. Neither container publishes a Host port or receives the Docker
socket. The Relay never mounts the Secret. The Proxy receives one read-only
mode-0600 Secret file and one writable receipt file.

The Relay atomically publishes `candidate.json`, then atomically publishes a
ready marker containing the request ID and Candidate SHA. The Host reads only
after the marker exists and verifies both hashes and regular-file metadata.
Partial files, multiple Candidates, missing markers, or mismatched IDs are
rejected without retry.

## Secret Boundary

Only the production Orchestrator can call `security find-generic-password -w`.
The value is held in memory only long enough to create the mode-0600 temporary
file. It is never placed in environment variables, command arguments, labels,
inspect data, logs, receipts, evidence, hashes, lengths, prefixes, or suffixes.
Tests inject a placeholder reader and assert the production reader is never
called by offline validation or fault scenarios.

## Validation and Routing

The returned Relay envelope is validated against request ID, symbol,
Projection SHA, Facts SHA, Typed Claims schema, claim/predicate allowlists,
Facts pointers, calculation registry, raw/qfq basis, units, free-text policy,
trading-claim policy, and sensitive shapes. Only then may the deterministic
renderer write into the isolated Canary inbox. Every failure writes a sanitized
rejected receipt and never enters inbox. `can_publish` remains false and manual
review remains pending only after machine success.

## Fault Injection

An injected Mock runtime backend drives all 20 required timing, crash,
candidate, cleanup, duplicate, concurrency, ledger, lock, and residue cases.
Provider delays use a fake monotonic clock, so 1/59/60/>60-second boundaries are
tested deterministically without wall-clock sleeps. Every scenario asserts
attempt count, retry count, ledger terminal state, route, cleanup, and absence
of a second dispatch.

## Artifact Candidate

The new approval candidate covers:

- canonical runtime-contract bytes and SHA;
- Host Orchestrator and launcher source SHA;
- Canary Relay source, Dockerfile, image ID, entrypoint, and runtime user;
- OpenAI Proxy source, Dockerfile, Responses contract, policy, runtime-contract
  copy, image ID, entrypoint, and runtime user;
- pinned base digest and fresh offline build provenance for both images;
- evidence-schema and runbook SHA values.

The old `c7614947...f96d` candidate remains byte-for-byte unchanged. Its local
approval is renamed to a superseded audit file only after the new candidate is
fully verified; it is never overwritten or deleted.

## Terminal Decision

Only complete offline verification yields
`PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL`. The new approval file is
not installed and no real call is made. A later turn must explicitly approve
the complete new hash set before any live authorization can be considered.
