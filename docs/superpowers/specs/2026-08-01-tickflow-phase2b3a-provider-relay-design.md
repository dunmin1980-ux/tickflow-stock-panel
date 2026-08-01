# TickFlow Phase 2B-3A Isolated Provider Relay Design

## 1. Status and Scope

This design starts from the committed Phase 2B-2 baseline:

```text
PHASE2B_ISOLATION_RUNTIME_VERIFIED
base_head=fe70395b5812feeb76b1e1a80505dd371562c79b
```

The target state is:

```text
PHASE2B_PROVIDER_RELAY_READY
```

The phase proves a complete local Mock Provider path for one fixed symbol:

```text
000403.SZ 派林生物
```

It does not authorize a real AI call, a real Provider request, a real AI key,
Codex-generated content, a three-symbol AI batch, Paper Trading, a real
Obsidian write, a cloud deployment, Telegram, OpenClaw, or Integrated Gold.

The existing artifact below is immutable and must not be republished or
rewritten:

```text
reports/phase2_isolation_runtime/runtime_evidence.json
```

## 2. Chosen Architecture

Use three dedicated, shell-less Python containers and two Docker networks:

```text
Host Orchestrator
  |
  | four ephemeral single-file mounts
  v
Relay Container
  |
  | relay_proxy_net (internal)
  v
Egress Proxy Container
  |
  | proxy_provider_net (internal)
  v
Mock Provider Container
```

Both networks are created with Docker `Internal=true`. No container publishes
a host port. The Relay joins only `relay_proxy_net`; the Mock Provider joins
only `proxy_provider_net`; the Proxy is the sole member of both networks.
Consequently, the Relay has no Docker DNS name or route to the Mock Provider.

All three images use the already approved pinned distroless Python base:

```text
gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5
```

Each image contains one standard-library Python entrypoint and no shell,
package manager, network client dependency, model SDK, or credential.

## 3. Responsibility Boundaries

### 3.1 Host Orchestrator

The Host is the only component allowed to:

- load the committed Facts file;
- build and validate the minimal Projection;
- bind Facts and Projection SHA-256 values;
- create request IDs and temporary files;
- create networks and constrained containers;
- inspect runtime contracts;
- validate the Relay response and Claims Candidate;
- execute the existing deterministic Renderer;
- route validated Markdown to the isolated mock preview;
- publish sanitized receipts and runtime evidence;
- compute preflight and postflight invariance hashes;
- remove every container, network, and temporary file.

The Host does not call a Provider directly and does not repair Provider output.

### 3.2 Relay Container

The Relay only:

- reads one strict request file and one minimal Projection file;
- confirms symbol and Projection SHA binding;
- constructs the fixed Provider JSON envelope;
- performs one HTTP `POST` to the fixed Proxy endpoint;
- reads at most 1,048,576 response bytes within two seconds;
- applies strict single-document JSON parsing;
- writes an exact Relay response envelope on success;
- writes a separate sanitized runtime receipt on success or failure.

The Relay never sees the full Facts file or a Provider credential. It does not
render Markdown, route output, modify a Candidate, remove an invalid Claim,
follow a redirect or URL in a response, invoke a tool, or place an order.

### 3.3 Egress Proxy Container

The Proxy is a fixed reverse proxy, not a general HTTP proxy. It:

- accepts only `POST /v1/typed-claims`;
- rejects every other method and path before an upstream request;
- forwards only to `phase2-mock-provider:8081/v1/typed-claims`;
- accepts no target URL, host, port, redirect policy, or credential from Relay;
- reads the temporary Secret file and injects the authorization header;
- rejects every upstream 3xx response and does not forward `Location`;
- caps request and response bodies at 1,048,576 bytes;
- uses one upstream attempt, a two-second timeout, and zero retry;
- writes only sanitized counters and status values to its receipt file;
- disables access logs and never persists a body or header.

### 3.4 Mock Provider Container

The Mock Provider:

- listens only on `proxy_provider_net` at port 8081;
- validates the injected test credential without logging or hashing it;
- validates the fixed generation controls and minimal Projection envelope;
- reads one read-only scenario file;
- returns the deterministic response for that scenario;
- records only request count, method/path validity, authorization presence,
  and response category in its receipt file;
- disables access logs and never persists the request body or credential.

## 4. Mount Contract

"No host mount" means no broad or sensitive host tree. Home, repository,
Vault, SSH, user configuration, database, Docker control endpoint, and any
unrelated directory are forbidden.

The approved Relay mounts are exactly four ephemeral regular files:

| Container path | Mode | Purpose |
|---|---|---|
| `/input/request.json` | read-only | strict Relay request |
| `/input/projection.json` | read-only | one-symbol minimal Projection |
| `/output/response.json` | read-write | exact success envelope only |
| `/output/receipt.json` | read-write | sanitized execution receipt |

The Relay has no Secret mount.

The Proxy mounts exactly:

| Container path | Mode | Purpose |
|---|---|---|
| `/run/secrets/provider_token` | read-only | ephemeral test credential |
| `/output/receipt.json` | read-write | sanitized policy counters |

The Mock Provider mounts exactly:

| Container path | Mode | Purpose |
|---|---|---|
| `/run/secrets/provider_token` | read-only | expected test credential |
| `/input/scenario.json` | read-only | fixed Mock response mode |
| `/output/receipt.json` | read-write | sanitized request counters |

Every mount source is created under one mode-0700 temporary directory. Input
and Secret files are regular, non-symlink files made read-only before Docker
creation. Output files are pre-created single files; no output directory is
mounted. Temporary paths are never written to reports.

## 5. Container Runtime Contract

Relay, Proxy, and Mock Provider all require:

```text
user=65532:65532
read_only_rootfs=true
cap_drop=ALL
no_new_privileges=true
pids_limit=16
memory_limit=128 MiB
cpu_limit=0.5
ipc=none
restart=no
published_ports=0
devices=0
privileged=false
```

The Host validates both created and exited/running inspect states. Runtime
evidence persists booleans and counts only. Container IDs, names, IP addresses,
raw inspect payloads, commands, environment values, and mount sources are not
published.

The Relay image environment contains only fixed non-sensitive runtime settings.
The Relay command contains no endpoint, Secret, Projection, or request body.

## 6. Relay Protocol

### 6.1 Host-to-Relay Request

The strict request object has exactly these fields:

```json
{
  "protocol_version": 1,
  "request_id": "32-lowercase-hex-characters",
  "symbol": "000403.SZ",
  "projection_sha256": "64-lowercase-hex-characters",
  "claims_schema_version": 1,
  "allowed_claim_types": [],
  "allowed_predicates": [],
  "response_format": "typed_claims_json"
}
```

`allowed_claim_types` and `allowed_predicates` must equal the canonical Host
allowlists. They are not caller-selected subsets or extensions.

The Projection remains a separate file and must pass the existing Projection
validator. The Relay verifies that request symbol and Projection SHA equal the
Projection before making any HTTP request.

### 6.2 Relay-to-Proxy Provider Envelope

The Relay sends this canonical JSON shape:

```json
{
  "request": {},
  "projection": {},
  "generation": {
    "temperature": 0,
    "response_format": "typed_claims_json",
    "tool_use": false,
    "web_browsing": false,
    "file_tools": false,
    "function_calling": false,
    "streaming": false,
    "retry_count": 0,
    "maximum_output_bytes": 1048576,
    "timeout_seconds": 2
  }
}
```

Only the current minimal Projection is included. Full Facts, source paths,
Home/repository/Vault paths, database information, TickFlow credentials,
GitHub credentials, Provider credentials, historical prose, and unrelated
files are absent.

### 6.3 Relay Success Output

The success file has exactly:

```json
{
  "protocol_version": 1,
  "request_id": "",
  "symbol": "000403.SZ",
  "projection_sha256": "",
  "claims_candidate": {}
}
```

Unknown top-level fields, multiple Candidates, Markdown, HTML, XML, prose,
reasoning, code fences, links, tool calls, and file references are rejected.

On failure the Relay does not write a partial response envelope. It exits with
a stable category and writes only the separate sanitized receipt. The Host
never converts the receipt into Claims or prose.

## 7. Secret Lifecycle

The test credential is generated randomly in Host memory for the runtime suite.
No fixed test value is committed. The value is written once to a temporary
read-only regular file mounted only into Proxy and Mock Provider.

The Proxy uses it solely to inject authorization. The Mock Provider compares
the received value directly with the temporary file value. Neither component
hashes, logs, echoes, or persists it. The Host scans in-memory inspect output,
captured stdout/stderr, temporary receipts, published artifacts, and the final
Git worktree for the exact value before deleting it.

Published evidence records only:

```text
secret_present=true
secret_value_hit_count=0
secret_digest_recorded=false
secret_file_cleanup=true
```

The Secret is never passed as a command argument, image layer, Projection,
Candidate, label, report field, stdout/stderr value, or inspect-visible
environment value.

## 8. Mock Scenarios and Failure Closure

Each scenario runs in a fresh constrained stack with one Provider attempt and
zero retry. `valid_typed_candidate` is executed twice. Required scenarios are:

```text
valid_typed_candidate
markdown_instead_of_json
extra_free_text_field
unknown_claim_type
unknown_predicate
wrong_symbol
wrong_projection_sha
raw_qfq_mismatch
unsupported_fact_pointer
trading_claim
oversized_response
timeout
http_429
http_500
invalid_json
multiple_json_documents
```

Additional scenarios close requirements not represented by that minimum list:

```text
empty_response
wrong_request_id
wrong_facts_sha
sensitive_shape
redirect_response
external_url_response
```

Only `valid_typed_candidate` may reach `VALID`. Every other scenario rejects
the whole symbol. There is no retry, field insertion, symbol/basis correction,
Claim deletion, partial acceptance, free-text fallback, or automatic repair.

The two valid runs must have identical normalized Candidate SHA-256 and
identical rendered Markdown SHA-256. Request IDs and timestamps are excluded
from those normalized values.

## 9. Real Network Policy Probes

One dedicated Relay probe container runs on `relay_proxy_net` and records only
the following values:

```text
ALLOWED
BLOCKED
NOT_REACHABLE
REDIRECT_REJECTED
```

It proves:

1. exact `POST /v1/typed-claims` reaches Proxy and Mock Provider;
2. direct Mock Provider name/port is not reachable from Relay;
3. an arbitrary test hostname cannot be reached;
4. a public test IP cannot be reached;
5. the Docker Desktop host gateway cannot be reached;
6. the local Docker control endpoint is absent and its TCP form is unreachable;
7. a wrong Proxy path is blocked without an upstream request;
8. a non-POST method is blocked without an upstream request;
9. an upstream redirect is rejected and its target is not followed;
10. a URL contained in Provider JSON is treated as data and never followed.

No true host address, resolved IP, network credential, raw error, or request
body is persisted. The expected real-public-network successful connection
count is zero.

## 10. Host Validation and Routing

For a successful Relay envelope the Host requires exact request ID, symbol,
Projection SHA, and Claims Candidate binding. It then executes the existing:

```text
validate_worker_candidate
-> ClaimsDocument adapter
-> validate_claims_document
-> render_claims_document
-> validate_rendered_document
```

The chain enforces Facts SHA and pointer binding, known Claim types and
predicates, raw/qfq separation, no trading Claim, no free-text field, and no
sensitive shape.

All Phase 2B-3A output is atomically published under:

```text
reports/phase2_provider_relay/
  runtime_evidence.json
  mock_preview/
    inbox/
    rejected/
    receipts/
```

The inbox contains only the deterministic Markdown from a valid run. Rejected
contains sanitized rejection records, never raw Provider output. Receipts
contains one sanitized receipt per run. No file is written to existing typed
inbox, Claims artifacts, historical AI samples, or historical rejected paths.

## 11. Audit Schema

Every run receipt includes:

```text
request_id
symbol
projection_sha256
facts_sha256
relay_image_digest
proxy_image_digest
mock_provider_image_digest
relay_contract_hash
proxy_policy_hash
started_at
completed_at
provider_http_status
provider_attempt_count
retry_count
response_size
response_sha256
candidate_sha256
claims_validation_status
renderer_status
route
cleanup_status
```

Timeout and pre-response failures use `null` for unavailable HTTP/hash fields
and a stable error category. Reports never contain request bodies, credentials,
headers, complete environment data, host absolute paths, raw inspect, or raw
stdout/stderr.

Top-level evidence separately records Mock attempt counts and these required
external counters:

```text
tickflow_api_request_count=0
real_ai_call_count=0
real_provider_attempt_count=0
real_public_network_success_count=0
cloud_mutation_count=0
obsidian_real_vault_write=false
external_send_count=0
integrated_gold_enabled=false
paper_trading_started=false
```

## 12. Cleanup and Crash Safety

The Host owns a fail-closed cleanup stack. For each scenario it removes Relay,
Proxy, and Mock containers, then both networks, then all temporary files.
Cleanup runs for create, start, validation, timeout, and publication failures.

Final evidence requires:

```text
relay_container_residue=0
proxy_container_residue=0
mock_provider_container_residue=0
test_network_residue=0
secret_file_residue=0
temporary_request_residue=0
temporary_response_residue=0
```

Any cleanup failure blocks `PHASE2B_PROVIDER_RELAY_READY`. Images may remain
cached; evidence records content-addressed image IDs and Dockerfile SHA-256.

Reports are written to a staging directory under `reports`, fsynced, and
atomically published. An interrupted run cannot leave a valid inbox artifact or
success state.

## 13. Test Strategy

### 13.1 Pure Contract Tests

Tests cover strict request/response/receipt schemas, exact allowlists, Projection
and Facts SHA binding, symbol binding, strict single JSON parsing, size and
timeout limits, zero retry, every Mock mutation, Candidate rejection, Renderer
determinism, routing, atomic publication, Secret redaction, and cleanup errors.

### 13.2 Runtime Contract Tests

Injected Docker executor tests validate exact commands and inspect contracts
without establishing final runtime status. They mutate every security flag,
mount, network, image environment, status, and cleanup result and require
stable failure codes.

### 13.3 Real Docker E2E

The runtime script builds the three pinned images locally, executes the full
scenario matrix and real network probes, verifies inspect and network state,
publishes sanitized evidence, and confirms zero residue. Mock execution is the
only Provider traffic; all Provider traffic stays inside the two local internal
networks.

### 13.4 Regression and Invariance

Required commands are:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_provider_relay.py \
  tests/test_phase2_provider_relay_runtime.py -q
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python -m compileall -q app scripts
.venv/bin/ruff check app scripts --select F821
cd ..
bash scripts/test_phase2_provider_relay_runtime.sh
```

Preflight and postflight hashes must prove unchanged:

- Phase 1 observation evidence;
- Phase 1 request audits;
- Phase 2 Facts;
- typed Claims Contract;
- existing typed inbox;
- historical AI prose;
- historical rejected artifacts;
- Phase 2B-2 isolation runtime evidence.

## 14. Deliverables and Status

Implementation files follow the approved boundaries:

```text
backend/app/schemas/phase2_provider_relay.py
backend/app/services/phase2_provider_relay_protocol.py
backend/app/services/phase2_provider_relay_runner.py
backend/scripts/run_phase2_mock_provider_relay.py
backend/scripts/validate_phase2_provider_relay.py
backend/tests/test_phase2_provider_relay.py
backend/tests/test_phase2_provider_relay_runtime.py
scripts/test_phase2_provider_relay_runtime.sh
docker/phase2-provider-relay/
docker/phase2-egress-proxy/
docker/phase2-mock-provider/
reports/phase2_provider_relay/
reports/tickflow_phase2b_provider_relay_eval.md
```

All passing requirements produce only:

```text
PHASE2B_PROVIDER_RELAY_READY
```

Arbitrary Relay egress produces `PHASE2B_PROVIDER_EGRESS_BLOCKED`. Secret
boundary failure produces `PHASE2B_PROVIDER_SECRET_BLOCKED`. Mock pipeline
failure produces `PHASE2B_PROVIDER_RELAY_E2E_BLOCKED`. Any protected hash
change produces `PHASE2B_EVIDENCE_MUTATED`.

If all Mock requirements pass, the final report may answer that the system is
eligible to request a separately approved one-symbol real Provider canary. It
must not execute or imply approval for that canary.

The final report explicitly answers:

1. whether Relay ran in a real isolated container;
2. whether Relay was non-root;
3. whether Relay had only the four approved ephemeral files and no sensitive
   or broad host mount;
4. whether the Secret was absent from inspect-visible values, logs, and
   artifacts;
5. whether Relay could reach only Proxy;
6. whether Proxy could reach only the fixed Mock Provider destination;
7. whether direct egress was blocked;
8. whether redirects were blocked;
9. whether the valid Mock Candidate passed;
10. whether every invalid mode failed closed;
11. whether Host Claims validation passed;
12. whether Candidate and Renderer output were deterministic;
13. whether any container, network, Secret, or temporary file remained;
14. whether the evidence is strong enough to request separate approval for one
    real single-symbol Provider canary.

## 15. Delivery Governance

Implementation is split into independently reviewable commits for protocol,
runtime/network proof, and controlled-canary documentation. The branch is
pushed only to repository `dunmin1980-ux/tickflow-stock-panel` on:

```text
codex/tickflow-phase2-ai-review
```

In this worktree that repository is the `fork` remote; `origin` points to the
read-only upstream project and is not the delivery target. Final verification
requires the local and `fork` branch SHA to match and the worktree to be clean.
No PR is created, no merge is performed, and no cloud deployment is changed.
