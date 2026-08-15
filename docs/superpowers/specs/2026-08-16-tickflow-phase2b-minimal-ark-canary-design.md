# TickFlow Phase 2B Minimal Ark AI Canary Design

## Status

Approved by the user-provided Phase 2B engineering-stop directive on 2026-08-16.

Target state:

```text
MINIMAL_ARK_CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL
```

This design prepares a new offline-approved one-shot path. It never reads the real Ark Secret and never sends a real Provider request during implementation or verification.

## Engineering Stop

The existing OpenAI and Ark Proxy/Relay experiments, TLS probes, receipt transport, network-attachment contracts, build provenance, consumed scopes, request IDs, ledgers, receipts, rejected evidence, and root-cause records are frozen as:

```text
FROZEN
PRESERVED
LEGACY_EXPERIMENTAL_PATH
NO_FURTHER_EXPANSION
NOT_REQUIRED_FOR_MINIMAL_BUSINESS_VALIDATION
```

No existing file in those paths is deleted, rewritten, regenerated, or made a runtime prerequisite of the minimal path. Historical Ark requests `2f17745f58534063bdd7eda1eb0d16f1`, `93cf0ea84804444ebc9745fa34c4bb82`, and `d57b276fe9014d35bdae5a92950ca17b` remain consumed and non-reusable. TLS probes `d73e91f766854ff7a64c0e725939eb64` and `287ffe2a9f394095ab9ecb81e0cb0298` remain preserved and non-reusable. The `TLS_EOF` root cause remains `NOT_PROVABLE`.

## Minimal Architecture

The new runtime path is:

```text
TickFlow Host
  -> existing Ark Responses adapter
  -> direct verified HTTPS POST /api/v3/responses
  -> strict Responses envelope adapter
  -> existing Worker candidate validation
  -> existing typed Claims validation
  -> existing deterministic Renderer
```

The path does not instantiate or inspect Relay, Proxy, Docker networks, Dispatch Gate, or any TLS/CA/SNI probe.

### New components

1. `phase2_minimal_ark_canary.py`
   - Owns the fixed Provider identity and HTTPS transport.
   - Creates a verified `SSLContext` with hostname verification enabled.
   - Uses `httpx.Client` with `trust_env=False`, redirects disabled, fixed timeout, and no retry loop.
   - Reserves and updates one minimal durable ledger.
   - Reads the Secret only after all offline gates and ledger reservation pass.
   - Adapts the Responses envelope with the existing Ark adapter.
   - Executes `validate_isolated_candidate` and requires valid Claims and deterministic Renderer output.

2. `run_phase2_minimal_ark_canary.py`
   - Future live entry point.
   - Requires a separately installed local approval matching the Candidate and Scope.
   - Acquires one exclusive local lock.
   - Reads the Keychain Secret exactly once and keeps it in process memory only.
   - Executes no fallback and never retries.

3. `build_phase2_minimal_ark_canary_offline.py`
   - Recomputes all minimal source and data identities.
   - Runs only local Mock scenarios.
   - Produces the minimal Candidate, one-shot Scope, Mock evidence, and verification report.
   - Refuses dirty worktrees, mismatched local/fork Heads, changed historical evidence, or any existing attempt in the new Scope.

4. `phase2_minimal_ark_request_contract.json`
   - Fixes Provider, model, endpoint, path, strict schema mode, TLS behavior, timeout, retry, maximum attempts, and publication safety.

## Seven Hard Gates

### 1. Provider identity

```text
provider_id=volcengine_ark
exact_model_id=doubao-seed-2-1-turbo-260628
endpoint=https://ark.cn-beijing.volces.com/api/v3/responses
method=POST
```

Identity mismatch fails before Secret read, ledger dispatch, or network activity.

### 2. Secret safety

The live path uses Keychain service `tickflow-phase2-canary-volcengine-ark`. The value is read once after approval and one-shot gates, passed directly to the in-memory Authorization header, and never placed in environment variables, argv, logs, reports, Candidate, Scope, ledger, or temporary files.

Mock and offline preparation inject a fixed test-only sentinel through a callable. They never invoke Keychain.

### 3. One-shot semantics

The ledger is created atomically in `PREPARED` state with `attempt_count=0` and `dispatch_started=false`. Immediately before invoking the HTTP transport it is atomically changed to `DISPATCH_STARTED`, `attempt_count=1`, and `dispatch_started=true`. Any HTTP error, timeout, malformed response, Candidate rejection, Claims rejection, or Renderer failure is terminal and consumed. There is no retry branch.

### 4. Structured Output

The existing Ark request adapter must produce:

```text
text.format.type=json_schema
text.format.strict=true
stream=false
store=false
tools=[]
```

`json_object`, free text, Markdown, prompt-only JSON, ChatCompletions, model fallback, and endpoint fallback are rejected.

### 5. Facts identity

The runtime binds exactly:

```text
symbol=000403.SZ
trade_date=2026-07-31
facts_sha256=adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956
projection_sha256=0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f
```

Facts and Projection are read as regular non-symlink files and recomputed before any Secret read or dispatch.

### 6. Typed Claims validation

The existing Ark envelope adapter and host validator enforce the Candidate schema, request ID bindings, Facts pointers, Calculation Registry, raw/qfq basis, units, supported predicates, free-text prohibition, and trading-claim prohibition. Invalid output is rejected as a whole; claims are never deleted or repaired.

### 7. Publication safety

The Candidate fixes `can_publish=false`. A successful real Canary would remain `manual_review=PENDING`. It cannot trigger Paper Trading, Obsidian writes, a three-symbol batch, cloud deployment, Telegram, OpenClaw, or Integrated Gold.

## Request and Prompt Identity

The research content remains the existing Ark Adapter request contract. The prompt identity is the SHA-256 of the canonical `input` payload generated from the frozen Projection with a fixed all-zero placeholder request ID. The live request ID is transport metadata and does not alter the frozen research instructions. The request-contract identity binds the full fixed HTTPS and structured-output policy.

No research wording, Facts, Projection, schema, model, reasoning setting, or timeout is changed by this phase.

## Candidate and Scope

The minimal Candidate binds only execution-critical identities:

- current Git Head;
- Provider, exact model, endpoint, symbol, and trade date;
- Facts, Projection, prompt, typed Claims schema;
- existing Ark Adapter, Claims Validator, and Renderer sources;
- minimal service, launcher, and request contract;
- `maximum_attempts=1`, `retry=0`, and `can_publish=false`;
- Mock E2E evidence and frozen historical-evidence manifest.

It does not bind Proxy or Relay images, Docker network contracts, TLS Probe Candidates, network attachments, Dispatch Gate, or legacy Approval objects.

The Scope ID is a canonical SHA-256 over the Candidate SHA, scope type, Provider, model, symbol, and trade date. Its initial state is `AVAILABLE` with zero historical attempts. Runtime attempts live under a new minimal-only root and cannot reuse any legacy scope or request ID.

## Error Handling

Failures before `dispatch_started=true` do not consume the attempt. Once the transport call begins, every outcome consumes it permanently. The ledger records only bounded non-sensitive fields: request ID, Candidate SHA, identity fields, attempt count, dispatch flag, terminal state, optional HTTP status, Candidate/Claims validation states, and timestamps.

HTTP redirects are not followed and are treated as terminal HTTP errors. Responses larger than the fixed limit, non-JSON envelopes, schema mismatches, invalid Claims, timeouts, TLS failures, and Renderer failures are terminal with no retry.

## Offline Mock E2E

Mock transport is injected at the Host HTTPS boundary. It receives the exact URL, method, headers, and JSON body but opens no public socket. The success response uses the frozen `000403.SZ` fixture and passes through the real Ark envelope adapter, Candidate protocol, Claims Validator, and Renderer.

Required runs:

- Happy Path x3, all deterministic and identical;
- invalid structured output;
- HTTP error;
- timeout;
- invalid Claims;
- identity, hash, strict-schema, retry, and maximum-attempt gate failures.

Every failure consumes at most one Mock attempt and never issues a second call. Mock evidence records no Authorization value and must report zero secret-pattern hits and zero temporary residue.

## Verification

Run only:

- Minimal Canary focused tests;
- Ark Adapter tests;
- Claims Validator tests;
- Renderer tests;
- Mock E2E x3;
- `compileall`;
- Ruff F821;
- `git diff --check`;
- independent focused review for P0/P1 business risks.

No shared `backend/app/**` behavior outside new minimal modules is changed, so historical backend failures do not gate this phase. Historical evidence hashes are captured before implementation and verified unchanged at closeout.

## Stop Condition

When offline gates pass, the Candidate is valid, the Scope is available with zero attempts, local and fork Heads match, and the worktree is clean, set:

```text
MINIMAL_ARK_CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL
```

Then stop. Do not install approval, read the real Secret, or execute a real Ark request.
