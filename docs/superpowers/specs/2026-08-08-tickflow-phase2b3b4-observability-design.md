# TickFlow Phase 2B-3B4 Canary Observability Design

## Status And Scope

This design implements `ADD_MINIMAL_STAGE_RECEIPTS` after the consumed Canary
request `d59766101b63450e8148541d589a90bf` ended as
`ATTEMPT_CONSUMED_UNKNOWN`. The historical request, ledger, runtime evidence,
rejection receipt, and diagnosis remain immutable.

The implementation is offline-only. It must not start the Canary launcher,
open a public socket, call OpenAI or TickFlow, install a new approval, or alter
the 60/75/90 second timeout contract. All fault replay uses injected process
results, local files, and mock connection objects.

## Architecture

### Host Child Evidence

Create `phase2_canary_observability.py` as the small, dependency-free Host
boundary for child-process evidence. It classifies a completed child or a
process exception into one of:

- `CHILD_TIMEOUT`
- `CHILD_NONZERO_EXIT`
- `CHILD_PROCESS_ERROR`
- `CHILD_SIGNALLED`
- `CHILD_START_FAILED`
- `CHILD_EXIT_UNKNOWN`

The classifier maps those states to component-specific terminal reasons such
as `RELAY_NONZERO_EXIT`, `RELAY_TIMEOUT`, `PROXY_NONZERO_EXIT`, and
`PROXY_TIMEOUT`. A negative return code is a signal, a positive return code is
a nonzero exit, and only `subprocess.TimeoutExpired` or an elapsed deadline may
be called a timeout.

Each child metadata record contains component, start/end wall timestamps,
start/end monotonic nanoseconds, elapsed milliseconds, exit code, signal,
terminal reason, and bounded stderr evidence. Stderr is limited to 4096 UTF-8
bytes. A sensitive-pattern hit stores no excerpt and no digest, only
`stderr_redacted=true`. A clean excerpt may store its SHA-256.

### Component Receipts

The Proxy receives a dedicated writable directory mounted at `/output` and
publishes `/output/proxy-receipt.json` through a same-directory temporary file,
file fsync, atomic rename, and directory fsync. This replaces the current
single-file bind mount, which cannot support atomic rename.

The Proxy receipt keeps the existing sanitized request outcome and adds:

- request ID and `component=proxy`
- a process-started event
- the seven approved Provider events
- Provider HTTP status, response byte count, and terminal status

Every event uses exactly `event`, `occurred`, `wall_time`, `monotonic_ns`,
`http_status`, and `byte_count`. The seven Provider events are:

1. `provider_connect_started`
2. `provider_connect_completed`
3. `tls_completed`
4. `request_write_started`
5. `request_write_completed`
6. `response_headers_received`
7. `response_body_completed`

The Relay receipt remains in its existing output directory but is extended
with request ID, `component=relay`, exit code, terminal status, and these local
events: Relay start, Proxy connect start/completion, request submission,
response wait, response receipt, Candidate write start/completion, and ready
marker publication. Relay events never count as Provider-network evidence.

### Archive Before Cleanup

`CanaryRuntimeContext` gains separate ephemeral Proxy output and a permanent
receipt archive directory under:

`reports/phase2_provider_canary/receipts/<request_id>/`

The backend protocol gains `archive_evidence()`. On both success and error,
the Host executes this order:

1. Detect and classify child outcome.
2. Capture bounded child metadata.
3. Validate and archive Relay receipt, Proxy receipt, and child metadata.
4. Fsync each file and atomically publish it, then fsync the directory.
5. Update the attempt ledger terminal state.
6. Remove containers, networks, Secret file, and ephemeral work directory.
7. Release the lock.

A missing receipt is recorded as `RECEIPT_MISSING`; malformed input is recorded
as `RECEIPT_MALFORMED`. Neither is reclassified as a timeout. Receipt bodies
are archived only after strict field validation; malformed or sensitive input
is never copied into permanent evidence.

The existing ledger schema remains unchanged so the consumed v1 ledger stays
canonical and readable. Precise terminal reason, last proven stage, and receipt
archive status are persisted in runtime evidence and the permanent receipt
bundle instead. `ATTEMPT_CONSUMED_UNKNOWN` remains available only when the Host
cannot prove a child outcome or archive status.

## Failure Semantics

Known child results use precise categories and end in `FAILED_AFTER_DISPATCH`,
not `ATTEMPT_CONSUMED_UNKNOWN`. Provider-stage receipts refine downstream
categories to connect failure, TLS failure, request-write failure, missing
headers, incomplete body, Candidate missing, or Candidate invalid. Cleanup
failure cannot erase a previously archived receipt bundle.

Crash-before-archive replay must produce no false receipt claim. Crash after
archive but before cleanup must retain the durable bundle. Retry count remains
zero in all paths.

## Historical Evidence

The consumed request remains classified as `DISPATCH_LEDGER_ONLY`. New code
must not synthesize DNS, TCP, TLS, HTTP, response, or Candidate events for the
old run. Tests pin the existing ledger/evidence/report hashes and verify that
the replay remains `ROOT_CAUSE_NOT_PROVABLE_WITH_CURRENT_EVIDENCE`.

The current approved runtime Candidate
`e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b`
is copied into a superseded evidence path and remains unchanged. The new
Candidate is generated only after tests and an offline image build. It is not
installed and does not authorize a Provider request.

## Verification

Focused tests cover child classification, bounded stderr, both receipt schemas,
all seven Provider events, atomic publication, archive-before-cleanup ordering,
missing/malformed receipts, two crash boundaries, historical immutability, and
retry zero. Existing Canary, Proxy, Relay, artifact, and full backend tests must
remain green. Compileall and Ruff F821 run over `app`, `scripts`, and the two
Docker Python sources. No test may use a real public network.

