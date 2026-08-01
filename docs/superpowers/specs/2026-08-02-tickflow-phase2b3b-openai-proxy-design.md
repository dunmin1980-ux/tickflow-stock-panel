# TickFlow Phase 2B-3B Dedicated OpenAI Proxy Design

## 1. Status and Objective

This design starts from:

```text
branch=codex/tickflow-phase2-ai-review
base_head=2b99f7284b44cb76791ca017bb99886be6c43c40
provider_preflight=PHASE2B_CANARY_EGRESS_BLOCKED
```

The current blocker is structural: `docker/phase2-egress-proxy/proxy.py` is a
Mock-only HTTP proxy. It is part of the already accepted Phase 2B-3A failure
test chain and must not be converted into a real Provider adapter.

The approved architecture is a separate, dedicated OpenAI Responses adapter.
The target of this implementation phase is:

```text
CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL
```

This target means that the Provider identity, request/response contract,
credential boundary, TLS implementation, exact destination policy, local
image, and frozen evidence have all passed offline checks. It does not mean a
TLS handshake, OpenAI request, AI inference, or Provider capability check has
occurred.

## 2. Hard Boundaries

The implementation and final preflight must not:

- read, print, copy, hash, measure, or otherwise inspect the real Keychain
  Secret value;
- place the real Secret in an environment variable, command argument, Docker
  label, image layer, Projection, request fixture, receipt, log, report, or Git;
- open a socket to OpenAI or any other public destination;
- use `curl`, an OpenAI SDK, a browser, or a network capability probe;
- perform a real Provider attempt or AI call;
- retry, fall back to another model, or select another Provider;
- run a three-symbol batch;
- modify existing Phase 1 evidence, Phase 2 Facts, typed Claims, typed inbox,
  historical AI Markdown, historical rejected artifacts, isolation evidence,
  or Provider Relay evidence;
- write to a real Obsidian Vault, start Paper Trading, deploy to cloud, or
  enable Integrated Gold.

All external counters remain zero through this phase.

## 3. Chosen Architecture

The existing Mock stack remains unchanged:

```text
Relay -> Mock Egress Proxy -> Mock Provider
```

A separate real-Provider path is added:

```text
Host Orchestrator
  |
  | canonical request + minimal Projection single-file mounts
  v
Existing Relay Container (no Secret)
  |
  | phase2_canary_relay_proxy_net (Docker internal network)
  v
Dedicated OpenAI Proxy Container (only Secret holder)
  |
  | phase2_canary_egress_net (future execution only)
  v
POST https://api.openai.com:443/v1/responses
```

The dedicated image name is fixed:

```text
tickflow-phase2-openai-egress-proxy:canary-v1
```

No host port is published. The Relay joins only the internal Relay/Proxy
network. The dedicated Proxy is the sole component that may later join the
egress network. The final offline preflight does not create that egress network
or start the Proxy.

The future non-internal Docker network is not represented as a kernel-level
hostname firewall. Exact-destination enforcement is instead provided by the
reviewed immutable Proxy program, the approved image ID, fixed constants, and
TLS hostname verification. A kernel-level egress firewall would be a separate
hardening stage and is not implied by this design.

## 4. Component Responsibilities

### 4.1 Host Orchestrator

The Host remains the only component allowed to:

- load and validate `000403.SZ` Facts;
- generate the minimal Projection and bind its SHA-256;
- generate the strict `WorkerClaimsCandidate` JSON Schema;
- produce the canonical Relay request;
- validate the dedicated Proxy artifact approval;
- inspect local image metadata without starting a network workload;
- validate the returned Relay envelope and Candidate in the future execution
  phase;
- run the deterministic Renderer and routing gates in the future execution
  phase;
- clean temporary files, containers, and networks.

The Host does not send the Provider request directly.

### 4.2 Existing Relay

The existing Relay remains Provider-neutral and credential-free. It sends its
already validated canonical envelope to one fixed local Proxy endpoint:

```text
POST http://phase2-egress-proxy:8080/v1/typed-claims
```

The Relay cannot provide or override the Provider host, port, path, model,
authorization header, TLS policy, redirect policy, retry count, or output
schema.

### 4.3 Dedicated OpenAI Proxy

The dedicated Proxy is a Provider adapter, not a generic HTTP proxy. It:

- accepts only `POST /v1/typed-claims` from the Relay network;
- rejects every other method and path before any upstream attempt;
- validates the Relay envelope and minimal Projection against exact local
  schemas and size limits;
- constructs the OpenAI Responses request from fixed local policy plus the
  validated Projection;
- uses exactly `gpt-5.6-terra`;
- uses exactly `POST https://api.openai.com:443/v1/responses`;
- reads the credential from one read-only mounted regular file;
- creates one verified TLS connection with one request and zero retry;
- rejects redirects, non-success statuses, oversized bodies, malformed JSON,
  multiple response documents, missing output, unknown response shapes, model
  mismatch, and schema-invalid Candidates;
- returns only one canonical Candidate JSON object to the Relay;
- writes only a sanitized receipt with counters and stable categories;
- disables request/access logging and never persists a body or header.

The Proxy does not accept a URL, hostname, IP address, port, path, model,
schema, header, timeout, redirect flag, or retry flag from the Relay.

### 4.4 Dedicated Canary Launcher Contract

A new Host-side module provides pure command builders for the future one-call
Canary. It does not expose a CLI that executes the call in this phase. The
builders fix:

- one internal Relay/Proxy network;
- one future Proxy-only egress network;
- the existing Relay image and dedicated OpenAI Proxy image;
- one-symbol input mounts;
- no Relay Secret mount;
- one read-only Proxy Secret-file mount;
- no published port, privileged mode, device, Docker control mount, broad host
  mount, restart, environment credential, or command-line credential;
- non-root user, read-only rootfs, dropped capabilities, resource limits, and
  `no-new-privileges`.

Offline preflight exercises these builders with a synthetic Secret path and
validates the resulting inspect-shaped contract. It never executes the
commands. The future live execution requires a separate explicit approval and
will use the same builders without adding a bypass flag.

## 5. Provider Request Contract

The request body is built entirely inside the dedicated Proxy. It contains:

```json
{
  "model": "gpt-5.6-terra",
  "stream": false,
  "tools": [],
  "input": [],
  "text": {
    "format": {
      "type": "json_schema",
      "name": "tickflow_phase2_claims_candidate",
      "strict": true,
      "schema": {}
    }
  }
}
```

The request deliberately omits `temperature` because the current approved
Provider configuration does not authorize that field. The Proxy does not
silently choose or expose a caller-controlled temperature. Adding it requires
a later approval contract revision.

`input` contains only:

1. fixed, source-controlled instructions that prohibit trading advice,
   prediction, unsupported financial/news assertions, free text, and data not
   present in the Projection; and
2. canonical JSON for the validated minimal Projection.

The Proxy does not send full Facts, source paths, Home/repository/Vault paths,
historical prose, other symbols, database configuration, TickFlow credentials,
GitHub credentials, or user-private material.

The strict schema is generated from `WorkerClaimsCandidate`. A canonical
schema artifact is stored beside the dedicated Proxy source and copied into
the image. The Host regenerates the schema during every validation and
requires byte-for-byte equality before approving the artifact.

No free-text fallback exists. A Provider that cannot satisfy the strict schema
will fail during the separately approved live Canary rather than causing this
phase to weaken the contract.

## 6. Provider Response Contract

The dedicated Proxy accepts one bounded Responses API JSON envelope. It
extracts exactly one structured output payload through a source-controlled
parser. Synthetic fixtures cover every accepted and rejected envelope shape.

The extracted value must:

- be one JSON object and no trailing document;
- validate as `WorkerClaimsCandidate` with no unknown fields;
- match `000403.SZ`, the fixed trade date, Facts SHA, and Projection SHA;
- contain only approved Claim types, predicates, units, price bases, and Facts
  pointers;
- contain no free-text, trading Claim, sensitive shape, external action, URL
  instruction, tool call, or file reference.

The Host remains authoritative. The Proxy's parsing does not replace the
existing Host validation chain and does not repair output.

## 7. TLS and Exact Egress Policy

The Proxy uses Python standard-library `http.client.HTTPSConnection` with an
`ssl.SSLContext` created by `ssl.create_default_context()`.

Required properties are:

```text
host=api.openai.com
port=443
path=/v1/responses
method=POST
check_hostname=true
verify_mode=CERT_REQUIRED
minimum_tls_version=TLSv1_2
follow_redirects=false
retry_count=0
maximum_attempts=1
```

The CA file is the fixed CA bundle in the digest-pinned distroless base image.
The code does not read HTTP proxy environment variables and does not support
an alternate transport. Standard-library HTTPS provides SNI and hostname
verification for the fixed host.

`TLS=READY` in the offline report means the exact pinned source and image
implement and test this contract. It does not claim that a real certificate or
handshake was observed. `egress_allowlist=PASSED` likewise means the immutable
application-level destination policy is proven offline; it does not claim a
real public connection occurred.

## 8. Size, Timeout, and Failure Closure

The dedicated Proxy uses:

```text
request_body_maximum=1,048,576 bytes
response_body_maximum=1,048,576 bytes
connect/read timeout=60 seconds
provider_attempt_count<=1
retry_count=0
```

The timeout is fixed in source and artifact approval; callers cannot alter it.

Every failure produces one stable category and no raw exception:

```text
METHOD_BLOCKED
PATH_BLOCKED
REQUEST_SIZE_BLOCKED
REQUEST_SCHEMA_BLOCKED
AUTH_BLOCKED
TLS_BLOCKED
TIMEOUT
REDIRECT_REJECTED
RATE_LIMIT_REJECTED
UPSTREAM_REJECTED
RESPONSE_SIZE_BLOCKED
RESPONSE_JSON_BLOCKED
RESPONSE_SCHEMA_BLOCKED
MODEL_IDENTITY_BLOCKED
OUTPUT_EXTRACTION_BLOCKED
```

There is no retry, redirect follow, provider fallback, model fallback, schema
relaxation, response repair, or second Candidate request.

## 9. Secret Lifecycle

The real Secret remains in macOS Keychain. The offline phase checks only
whether the approved service exists, with stdout and stderr discarded. It
never uses `security ... -w`.

Offline mechanism tests use a runtime-constructed synthetic value that is not
the real Secret. The future approved live launcher, which is out of scope for
this design phase, will be the only component allowed to read the Keychain
value into Host memory and immediately write it to a mode-0600 temporary
regular file.

The dedicated Proxy mounts exactly:

| Container path | Mode | Purpose |
|---|---|---|
| `/run/phase2/provider-auth` | read-only | temporary Provider credential |
| `/output/receipt.json` | read-write | sanitized receipt |

The Relay has no credential mount. The Secret must not appear in inspect
output, environment, labels, commands, request/response fixtures, receipts,
logs, reports, Git, or hashes. Cleanup removes the file and containing mode-0700
temporary directory. Exact synthetic-value scanning must return zero hits.

## 10. Dedicated Image and Offline Build

The dedicated image uses the existing approved base by exact digest:

```text
gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5
```

The build context contains only:

```text
Dockerfile
proxy.py
responses-contract.json
secret.placeholder
receipt.placeholder.json
```

The placeholders contain no credential. The Dockerfile sets:

```text
user=65532:65532
entrypoint=/usr/bin/python3 /proxy/proxy.py
org.tickflow.phase2.base-image-digest=<fixed digest>
org.tickflow.phase2.proxy-source-sha256=<source hash>
org.tickflow.phase2.responses-contract-sha256=<contract hash>
org.tickflow.phase2.proxy-policy-sha256=<policy hash>
```

Build prerequisites are checked locally. The build command uses the equivalent
of:

```text
--pull=false
--network=none
```

If the exact base image is not already local, the phase stops with
`PHASE2B_CANARY_BUILD_INPUT_BLOCKED`. It must not pull an image or access a
registry in this offline phase.

The build receives no Secret, credential, environment file, SSH forwarding,
or broad repository context.

## 11. Independent Artifact Approval

A dedicated image cannot approve itself merely by carrying labels. After
offline implementation, tests, build, and independent review, a non-sensitive
approval candidate is generated with:

```text
approval_schema_version
image_name
image_id
base_image_digest
proxy_source_sha256
dockerfile_sha256
responses_contract_sha256
proxy_policy_sha256
launcher_source_sha256
entrypoint
runtime_user
```

The candidate is written under `reports/` and contains no local path or
credential. The process stops at:

```text
PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED
```

After the user explicitly approves those exact hashes, the approved object is
installed as:

```text
$HOME/Library/Application Support/TickFlowPhase2Canary/
  proxy-artifact-approval.json
```

The existing directory remains mode `0700`; the new regular file is mode
`0600`, owned by the current user, and has no symlink parent. This file contains
no Secret.

The final preflight requires exact agreement among:

- approval file fields;
- current source bytes;
- current Dockerfile bytes;
- regenerated Responses contract bytes;
- policy bytes;
- future launcher source bytes;
- local image ID;
- image labels;
- the source and contract bytes copied back from a never-started,
  `--network=none` temporary image container.

Any mismatch fails closed. The temporary inspection container is removed even
on error, and final residue counts must be zero.

## 12. Final Offline Preflight Sequence

The final validator executes these gates in order:

1. exact branch, allowed committed delta, and clean worktree;
2. exact Provider approval file contract and secure filesystem metadata;
3. Keychain item presence only;
4. placeholder single-file Secret injection and redaction tests;
5. current `000403.SZ` Facts, trade date, and deterministic Projection;
6. Claims Schema, Renderer, isolation evidence, and Provider Relay evidence;
7. nine protected evidence sets unchanged;
8. dedicated source, Dockerfile, contract, policy, image ID, labels, and image
   contents plus the future launcher source match the separately approved
   artifact object;
9. static and unit-tested exact HTTPS destination contract;
10. no Provider/AI/TickFlow/public-network action evidence;
11. no container, network, process, Secret, or temporary-file residue.

Only all-green evidence returns:

```text
CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL
```

The sanitized result includes:

```text
Provider=openai
Exact Model=gpt-5.6-terra
Endpoint Alias=openai_responses_v1
Provider config=VALID
Keychain Secret=PRESENT
Secret content read=NO
Temporary single-file injection=PASSED
Strict JSON Schema=READY
TLS=READY_OFFLINE
Redirect=DISABLED
Egress allowlist=PASSED_OFFLINE
Provider attempts=0
AI calls=0
Provider HTTP=NOT_RUN
Next action=REQUEST_FINAL_SINGLE_CALL_APPROVAL
```

The user-facing summary may render `TLS=READY` and `egress_allowlist=PASSED`,
but the report must explicitly preserve the offline-only qualification.

## 13. Test Strategy

### 13.1 Pure Contract Tests

Tests must cover:

- exact Provider, model, endpoint, method, symbol, and generation controls;
- strict schema byte equality with `WorkerClaimsCandidate`;
- fixed input construction from only the minimal Projection;
- rejection of arbitrary model, URL, host, port, path, method, header, schema,
  timeout, retry, tools, streaming, web, or file options;
- exact single Candidate extraction;
- wrong symbol, date, Facts SHA, Projection SHA, model, Claim, predicate,
  pointer, unit, price basis, free text, trading Claim, sensitive shape, and
  unknown field rejection.

### 13.2 Transport Tests Without Sockets

`http.client.HTTPSConnection`, `ssl.create_default_context`, and response
objects are replaced with strict test doubles. Tests prove:

- `HTTPConnection` is never used;
- only `api.openai.com:443` and `/v1/responses` are referenced;
- hostname verification and `CERT_REQUIRED` are retained;
- minimum TLS version is 1.2 or higher;
- exactly one request occurs;
- redirects are not followed;
- timeout, 429, 5xx, invalid JSON, multiple output documents, empty output,
  oversized output, model mismatch, and malformed output fail once with no
  retry;
- no socket constructor is reached in offline preflight tests.

### 13.3 Secret Tests

Tests require:

- Keychain presence checks never include `-w`;
- no secret value, length, prefix, suffix, or hash is emitted;
- synthetic Secret appears only in the ephemeral file;
- Relay mounts no Secret;
- Proxy mount is one read-only file;
- commands, inspect-shaped fixtures, environment, labels, Projection, request,
  Candidate, receipt, stdout/stderr, report, and worktree contain zero exact
  synthetic-value hits;
- cleanup leaves no file or directory residue.

### 13.4 Artifact Tests

Tests require failure for:

- absent local base image;
- any build command that can pull or use a network;
- broad build context;
- unexpected image user, entrypoint, environment, layer input, label, source,
  contract, policy, base digest, or image ID;
- symlinked approval/config/source files;
- one-byte source, Dockerfile, contract, policy, label, or copied-image-content
  drift;
- missing or unapproved artifact manifest;
- incomplete temporary-container cleanup.

### 13.5 Regression and Invariance

Run focused suites, full backend tests, compileall, Ruff F821, diff checks, and
sensitive-shape scans. Recompute all protected hashes before and after. No
existing Mock Provider test or evidence file may be rewritten to make the new
adapter pass.

## 14. Planned File Boundaries

The implementation plan may create or modify only these focused areas:

```text
docker/phase2-openai-egress-proxy/
backend/app/services/phase2_openai_proxy_contract.py
backend/app/services/phase2_openai_proxy_artifact.py
backend/app/services/phase2_openai_canary_runner.py
backend/scripts/build_phase2_openai_proxy_offline.py
backend/scripts/validate_phase2_canary_provider_config.py
backend/tests/test_phase2_openai_proxy_contract.py
backend/tests/test_phase2_openai_proxy_artifact.py
backend/tests/test_phase2_openai_canary_runner.py
backend/tests/test_phase2_canary_provider_config.py
docs/superpowers/plans/
reports/phase2_openai_proxy_artifact/
reports/tickflow_phase2b_canary_provider_preflight_eval.md
```

The existing Mock Proxy, Mock Provider, Phase 2B-3A runtime evidence, and
application frontend/backend runtime APIs remain unchanged.

If implementation reveals that an existing runtime file must change, work
stops for a design amendment rather than expanding scope silently.

## 15. State Machine and Stop Points

```text
PHASE2B_CANARY_EGRESS_BLOCKED
  -> offline implementation and tests
PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW
  -> independent code and artifact review
PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED
  -> explicit user approval of exact non-sensitive hashes
CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL
  -> stop; no real Provider request
```

Any safety, build, artifact, evidence, cleanup, or regression failure remains
blocked with a stable sanitized category. No failure automatically advances to
the next state.

## 16. Acceptance Criteria

The design is complete only when implementation evidence proves:

1. existing Mock behavior and frozen evidence are unchanged;
2. dedicated Proxy source and image are separate and exact;
3. strict Responses request and Candidate contracts are deterministic;
4. application-level destination is fixed to the approved OpenAI endpoint;
5. TLS verification, hostname checking, and redirect rejection are enforced in
   the exact approved image;
6. real Secret content remains unread during preflight;
7. synthetic credential injection and cleanup pass with zero exposure;
8. source, Dockerfile, contract, policy, labels, copied image contents, image
   ID, launcher source, and external approval agree;
9. protected evidence and request audits are unchanged;
10. Provider attempts, AI calls, TickFlow requests, and real public-network
    successes remain zero;
11. focused and full regression suites pass;
12. no runtime or temporary residue remains;
13. the validator returns `CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL` and stops.

No claim about real Provider availability, model entitlement, response quality,
TLS handshake success, or API compatibility is made until a separately
approved single live Canary executes.
