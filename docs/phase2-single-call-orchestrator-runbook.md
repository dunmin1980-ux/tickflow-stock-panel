# Phase 2 Single-Call Canary Orchestrator Runbook

## Current Gate

The runtime contract is an offline approval candidate. It is not an execution
approval. Do not run the launcher until a later instruction explicitly approves
the complete candidate SHA-256 and a separate mode-0600 local approval record
has been installed.

Fixed scope:

- symbol: `000403.SZ`
- trade date: `2026-07-31`
- provider: `openai`
- model: `gpt-5.6-terra`
- endpoint alias: `openai_responses_v1`
- maximum provider attempts: `1`
- retry count: `0`
- publication: disabled

## Runtime Contract

The committed runtime contract is the only timeout authority:

```text
provider connect: 10 seconds
provider read/total: 60 seconds
relay Candidate wait: 75 seconds
host orchestrator: 90 seconds
cleanup: 15 seconds
retry: 0
maximum attempts: 1
```

Proxy, Relay, and Host load or verify byte-identical contract bytes. Runtime
environment variables cannot override these values.

## Required Approval Record

The repository candidate is not sufficient to enable a call. A later approval
must install exactly one regular mode-0600 file at:

```text
~/Library/Application Support/TickFlowPhase2Canary/runtime-contract-approval.json
```

The record must bind the approved repository candidate SHA-256, fixed symbol,
provider, retry count, and maximum attempt count. Do not place a Provider key in
this file. The Provider key remains in macOS Keychain under the fixed Canary
service name.

## Execution

After a separate approval and only during an explicitly authorized turn, invoke
the single launcher once from the backend directory:

```bash
PYTHONPATH=. .venv/bin/python scripts/run_phase2_single_symbol_canary.py
```

Do not replace this with manual Proxy, Relay, Docker network, or HTTP commands.
The launcher is the only approved owner of the lock, ledger, Secret lifecycle,
containers, networks, Candidate collection, Host validation, rendering,
evidence publication, and cleanup.

## State Interpretation

The durable attempt budget is scoped by the approved Candidate SHA-256, symbol,
provider, exact model, endpoint alias, and ledger namespace version. New
attempts are stored under:

```text
reports/phase2_provider_canary/attempts/
  <approval_scope_id>/<request_id>/ledger.json
```

Legacy ledgers remain byte-for-byte read-only in Application Support. A
consumed terminal ledger from a different, immutable approval scope is
preserved and audited but does not consume the current scope. An unresolved,
nonterminal, contradictory, or residue-bearing historical ledger blocks every
scope.

The current scoped ledger is written before any possible external dispatch.
Once `NETWORK_DISPATCH_STARTED` is durable, that approval scope is consumed. A
crash, timeout, unknown response, or Host restart after that point never permits
an automatic retry or a second request ID in the same scope.

Terminal recovery rules:

- `FAILED_BEFORE_DISPATCH`: no Provider attempt was consumed; any later attempt
  still requires a new explicit approval.
- `FAILED_AFTER_DISPATCH`: one attempt was consumed; do not rerun.
- `ATTEMPT_CONSUMED_UNKNOWN`: dispatch may have happened; do not rerun.
- `CLEANUP_COMPLETED`: machine validation and cleanup completed; manual review
  remains pending and `can_publish` remains false.

The exclusive lock remains global at `runtime-v1/.runtime.lock`; different
approval scopes cannot run concurrently. Request IDs remain globally unique
across legacy ledgers, scoped ledgers, evidence, rejections, and receipts. A
stale lock is recoverable only when the same scope has a provable terminal
`FAILED_BEFORE_DISPATCH` ledger and no dispatch evidence. Any other existing or
unknown lock is a stop condition. Do not delete state files to force another
call.

## Evidence and Routing

Machine output is restricted to the isolated Canary route under
`reports/phase2_provider_canary/live_canary/`. A valid Candidate must pass the
ready-marker hash, request and Projection bindings, Typed Claims schema,
allowlists, Facts pointers, calculation registry, raw/qfq basis, units,
free-text policy, trading-claim policy, sensitive scan, and deterministic
renderer.

Any validation or cleanup failure routes to `rejected`. No failure may leave a
successful inbox artifact. All outputs keep `can_publish=false` and
`manual_review=PENDING`.

## Cleanup Verification

The orchestrator must report all of the following as zero before success:

```text
container_residue_count
network_residue_count
secret_file_residue_count
temporary_file_residue_count
```

The Secret is read once from Keychain, written to one mode-0600 temporary file,
mounted read-only into Proxy only, and removed during cleanup. It must never be
placed in arguments, environment variables, labels, logs, receipts, evidence,
hashes, or Relay mounts.

## Prohibited Recovery

Do not retry, change model, change endpoint, increase timeouts, delete the
ledger, remove an unknown lock, alter Candidate files, patch a rejected Claim,
or relaunch containers manually. Record the terminal category and request a new
review instead.
