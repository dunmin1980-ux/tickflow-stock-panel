# TickFlow Phase 2B-3A Provider Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and prove a one-symbol, local-only Provider Relay path in which a non-root Relay can reach only a fixed Egress Proxy, the Proxy can reach only a Mock Provider, every invalid response fails closed, and the Host alone validates and renders typed Claims.

**Architecture:** Three digest-pinned distroless Python containers run across two Docker `internal` networks. The Relay has four ephemeral single-file mounts and no credential; the Proxy injects a temporary test credential and forwards only one method/path to the Mock Provider; the Host validates the exact response, renders deterministically, routes only into `reports/phase2_provider_relay/`, and removes all containers, networks, Secrets, and temporary files.

**Tech Stack:** Python 3.12 Host, Pydantic v2, Python standard-library container entrypoints, Docker Desktop 29.3.1, distroless Python 3 Debian 12 pinned by digest, pytest, canonical JSON, SHA-256, atomic directory publication.

## Global Constraints

- Base design commit: `7239a21330456eac72b7aec26f79be419b117f7d`.
- Fixed symbol: `000403.SZ` only.
- Real AI calls, real Provider attempts, TickFlow API requests, and real-public-network successful connections must remain zero.
- Do not configure any AI key or use Codex CLI to generate content.
- Do not touch Paper Trading, real Obsidian, cloud deployment, Telegram, OpenClaw, or Integrated Gold.
- Do not modify `reports/phase2_isolation_runtime/runtime_evidence.json`.
- Do not alter `backend/app/**` or `frontend/src/**` outside the Phase 2B-3A files named in this plan.
- Do not modify the existing Phase 2B-2 worker or its image context.
- Every behavior change follows RED -> GREEN -> refactor; no production code precedes its failing test.
- Provider retry count is exactly zero; no alternate image, endpoint, key, or relaxed network policy is allowed.
- Relay runtime user is exactly `65532:65532`; rootfs is read-only; all capabilities are dropped; no-new-privileges, PID, CPU, memory, IPC, and restart controls are mandatory.
- Relay joins only `relay_proxy_net`; Mock joins only `proxy_provider_net`; Proxy alone joins both Docker `internal` networks.
- No host port is published and no normal bridge, host network, Home, repository, Vault, SSH, user config, database, device, or Docker control mount is allowed.
- Relay mounts exactly request/projection read-only files plus response/receipt writable files. Relay has no Secret mount.
- Proxy and Mock may read only the same ephemeral test Secret file; the exact Secret must have zero hits in inspect-visible values, logs, artifacts, and final worktree, and no Secret digest may be recorded.
- Response limit is 1,048,576 bytes, timeout is two seconds, temperature is zero, JSON mode is fixed, and streaming/tools/web/files/functions are disabled.
- Existing artifacts must preserve these preflight digests:

```text
Phase 1 observation evidence: 8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5
Phase 1 request audits: dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d
Phase 2 Facts: 10c6664754b8e1391f55d9b1b8b98f1577b749f0dceafbb77288749ae5376ef7
Typed Claims tree: 39ef9b714798f193c3ba283c1035fad42372cedf4f7a17a36fc008411ed41bbf
Typed inbox: a9bfddecc5a9046badf5f7cf4485fab029508681dae8a5a5206a93d4ab2e2291
Historical AI Markdown: 9a0bc445f7580c55db630b8e3fbfac545fe71d54b36588919dd7ec79ef57feb5
Historical rejected: 6e2e0c7b311a76ccff061917f3893f97aca4ba84782c769ee004960d335f164c
Phase 2B-2 runtime evidence: 9cf174d4def6908e84522366105b41ed33a00f3be8aab9dc01454c4f75f5533c
```

---

### Task 1: Strict Relay Schemas and Pure Protocol

**Files:**
- Create: `backend/app/schemas/phase2_provider_relay.py`
- Create: `backend/app/services/phase2_provider_relay_protocol.py`
- Create: `backend/tests/test_phase2_provider_relay.py`

**Interfaces:**
- Consumes: existing `WorkerClaimsCandidate`, `ALLOWED_CLAIM_TYPES`, `PREDICATE_RULES`, Projection dictionaries, and canonical JSON helpers.
- Produces: `RelayRequest`, `GenerationControls`, `ProviderEnvelope`, `RelayResponse`, `RelayExecutionReceipt`, `build_relay_request(...)`, `build_provider_envelope(...)`, `parse_single_json_object(...)`, `validate_relay_response(...)`, and stable protocol error codes.

- [x] **Step 1: Write failing strict-schema tests**

Create tests that express the public API before importing production code:

```python
def test_relay_request_is_exact_and_bound_to_projection():
    projection = fixed_projection("000403.SZ")
    request = build_relay_request(projection, request_id="a" * 32)
    assert request.model_dump(mode="json") == {
        "protocol_version": 1,
        "request_id": "a" * 32,
        "symbol": "000403.SZ",
        "projection_sha256": projection["projection_sha256"],
        "claims_schema_version": 1,
        "allowed_claim_types": sorted(ALLOWED_CLAIM_TYPES),
        "allowed_predicates": sorted(PREDICATE_RULES),
        "response_format": "typed_claims_json",
    }

def test_generation_controls_are_closed_constants():
    controls = GenerationControls()
    assert controls.temperature == 0
    assert controls.streaming is False
    assert controls.retry_count == 0
    assert controls.maximum_output_bytes == 1_048_576
    assert controls.timeout_seconds == 2
```

Parametrize request and response mutations for unknown fields, wrong protocol,
invalid request ID, wrong symbol, wrong Projection SHA, changed allowlist,
Markdown/HTML/XML/prose keys, multiple Candidates, and non-finite values.

- [x] **Step 2: Write failing strict-JSON tests**

Require one object and trailing whitespace only:

```python
@pytest.mark.parametrize("raw", [b"", b"{}{}", b"[]", b"NaN", b"{bad}"])
def test_single_json_parser_rejects_non_contract_input(raw):
    with pytest.raises(Phase2ProviderRelayError):
        parse_single_json_object(raw, maximum_bytes=1_048_576)
```

Also reject 1,048,577 bytes and accept exactly 1,048,576 only when that byte
sequence is one valid object.

- [x] **Step 3: Run Task 1 tests and require RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_provider_relay.py -q
```

Expected: collection failure because the schema and protocol modules do not
exist. Correct test import errors before proceeding; do not weaken assertions.

- [x] **Step 4: Implement strict Pydantic schemas**

Use `ConfigDict(extra="forbid", frozen=True)` on every model. Restrict symbol
to `Literal["000403.SZ"]`, response format to
`Literal["typed_claims_json"]`, and request ID to lowercase 32-character hex.
`RelayResponse.claims_candidate` is a strict `WorkerClaimsCandidate`, not an
untyped dictionary.

`RelayExecutionReceipt` contains only protocol status, stable error category,
HTTP status or null, attempt count, retry count, response byte count/hash or
null, and timestamps. It contains no body, header, endpoint, environment,
container identifier, or host path.

- [x] **Step 5: Implement pure protocol helpers**

`build_relay_request` validates the Projection using existing protocol helpers,
requires the fixed symbol, and copies only canonical allowlists.
`build_provider_envelope` embeds exactly request, minimal Projection, and fixed
generation controls. `parse_single_json_object` uses `JSONDecoder.raw_decode`,
rejects non-finite constants, verifies only whitespace remains, and checks the
byte limit before decoding.

`validate_relay_response` requires request ID, symbol, and Projection SHA to
match before returning the typed Candidate. It never changes a field.

- [x] **Step 6: Run Task 1 tests and static checks**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_provider_relay.py -q
.venv/bin/ruff check app/schemas/phase2_provider_relay.py \
  app/services/phase2_provider_relay_protocol.py \
  tests/test_phase2_provider_relay.py
.venv/bin/python -m compileall -q app
```

- [x] **Step 7: Commit Task 1**

```bash
git add backend/app/schemas/phase2_provider_relay.py \
  backend/app/services/phase2_provider_relay_protocol.py \
  backend/tests/test_phase2_provider_relay.py
git commit -m "feat: add strict phase2 provider relay protocol"
```

---

### Task 2: Shell-less Relay Executable and Image

**Files:**
- Create: `docker/phase2-provider-relay/relay.py`
- Create: `docker/phase2-provider-relay/Dockerfile`
- Create: `docker/phase2-provider-relay/request.placeholder.json`
- Create: `docker/phase2-provider-relay/projection.placeholder.json`
- Create: `docker/phase2-provider-relay/response.placeholder.json`
- Create: `docker/phase2-provider-relay/receipt.placeholder.json`
- Modify: `backend/tests/test_phase2_provider_relay.py`

**Interfaces:**
- Consumes: `/input/request.json`, `/input/projection.json`, fixed Proxy name/port/path, and one HTTP response.
- Produces: exact `/output/response.json` on success, sanitized `/output/receipt.json` on every terminal result, and `run`/`probe` entrypoint modes.

- [x] **Step 1: Write failing Relay module tests**

Load `relay.py` by exact file path. Assert fixed constants:

```python
assert relay.PROXY_HOST == "phase2-egress-proxy"
assert relay.PROXY_PORT == 8080
assert relay.PROXY_PATH == "/v1/typed-claims"
assert relay.MAXIMUM_BYTES == 1_048_576
assert relay.TIMEOUT_SECONDS == 2
assert relay.ALLOWED_MODES == {"run", "probe"}
```

Test canonical request bytes, one outbound attempt, timeout, 429, 500,
redirect, empty body, oversized body, invalid JSON, multiple JSON documents,
wrong response binding, and no partial response file on failure. Use a local
standard-library test HTTP server for actual client behavior; do not mock a
model SDK.

- [x] **Step 2: Write failing Relay filesystem and dependency tests**

AST-inspect imports and require standard-library roots only. Verify all reads
and writes use fixed paths, `O_NOFOLLOW` when available, regular-file checks,
size limits, fsync, and no stdout/stderr body logging. Require `relay.py` to
contain no credential name or Secret path.

- [x] **Step 3: Run Relay tests and require RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_provider_relay.py -q -k 'relay_executable or relay_http'
```

Expected: missing Relay module and image context.

- [x] **Step 4: Implement Relay run mode**

Use only `http.client`, `json`, `hashlib`, `os`, `socket`, `time`, and other
standard-library modules. The Relay validates request/Projection binding before
opening a socket. It sends one canonical JSON `POST`, sets `Accept` and
`Content-Type` to JSON, sends no authorization header, disables retry, rejects
all non-2xx and all 3xx, and never follows content URLs.

On success it writes only the exact Relay response. On failure it truncates the
response placeholder to one newline and writes a receipt with a stable category
such as `HTTP_REJECTED`, `TIMEOUT`, `RESPONSE_TOO_LARGE`, `STRICT_JSON_REJECTED`,
or `BINDING_REJECTED`.

- [x] **Step 5: Implement Relay probe mode**

Probe mode performs actual bounded operations for:

```text
exact proxy method/path
direct Mock Provider name/port
arbitrary test hostname
public test IP
Docker Desktop host gateway
local Docker control file and TCP port
wrong Proxy path
non-POST Proxy method
```

It records only `ALLOWED`, `BLOCKED`, `NOT_REACHABLE`, or
`REDIRECT_REJECTED`; no address, resolved IP, exception text, or response body
is written.

- [x] **Step 6: Add a pinned minimal Dockerfile**

The first line is exactly:

```dockerfile
FROM gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5
```

Copy only `relay.py` and four placeholders, set `USER 65532:65532`, disable
bytecode, and use JSON `ENTRYPOINT`. Reject `RUN`, `ADD`, tag-only `FROM`,
`:debug`, package managers, shell paths, URLs, and sensitive environment names
in static tests.

- [x] **Step 7: Run Relay tests and require GREEN**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_provider_relay.py -q
.venv/bin/ruff check tests/test_phase2_provider_relay.py
```

- [x] **Step 8: Commit Task 2**

```bash
git add docker/phase2-provider-relay backend/tests/test_phase2_provider_relay.py
git commit -m "feat: add isolated phase2 provider relay"
```

---

### Task 3: Fixed Egress Proxy and Deterministic Mock Provider

**Files:**
- Create: `docker/phase2-egress-proxy/proxy.py`
- Create: `docker/phase2-egress-proxy/Dockerfile`
- Create: `docker/phase2-egress-proxy/secret.placeholder`
- Create: `docker/phase2-egress-proxy/receipt.placeholder.json`
- Create: `docker/phase2-mock-provider/mock_provider.py`
- Create: `docker/phase2-mock-provider/Dockerfile`
- Create: `docker/phase2-mock-provider/secret.placeholder`
- Create: `docker/phase2-mock-provider/scenario.placeholder.json`
- Create: `docker/phase2-mock-provider/receipt.placeholder.json`
- Modify: `backend/tests/test_phase2_provider_relay.py`

**Interfaces:**
- Proxy consumes one Relay request and one temporary Secret file; it produces one upstream request and a sanitized receipt.
- Mock consumes one Provider envelope, one scenario name, and the same test Secret; it produces one deterministic response and a sanitized receipt.

- [x] **Step 1: Write failing Proxy policy tests**

Import `proxy.py` by exact path and assert policy constants are immutable:

```python
assert proxy.ALLOWED_METHOD == "POST"
assert proxy.ALLOWED_PATH == "/v1/typed-claims"
assert proxy.UPSTREAM_HOST == "phase2-mock-provider"
assert proxy.UPSTREAM_PORT == 8081
assert proxy.UPSTREAM_PATH == "/v1/typed-claims"
assert proxy.RETRY_COUNT == 0
assert proxy.FOLLOW_REDIRECTS is False
```

With a local upstream server, prove valid forwarding, wrong path rejection,
wrong method rejection, authorization injection, input/output size limits,
timeout, 429/500 pass-through as rejected statuses, upstream redirect rejection,
and one upstream request maximum. Assert no body/header is present in receipts
or captured logs.

- [x] **Step 2: Write failing Mock scenario tests**

Load the existing Phase 2B-2 worker only as the deterministic candidate factory
for tests. Parametrize all scenarios:

```python
EXPECTED = {
    "valid_typed_candidate": "VALID",
    "markdown_instead_of_json": "REJECTED",
    "extra_free_text_field": "REJECTED",
    "unknown_claim_type": "REJECTED",
    "unknown_predicate": "REJECTED",
    "wrong_symbol": "REJECTED",
    "wrong_projection_sha": "REJECTED",
    "raw_qfq_mismatch": "REJECTED",
    "unsupported_fact_pointer": "REJECTED",
    "trading_claim": "REJECTED",
    "oversized_response": "REJECTED",
    "timeout": "REJECTED",
    "http_429": "REJECTED",
    "http_500": "REJECTED",
    "invalid_json": "REJECTED",
    "multiple_json_documents": "REJECTED",
    "empty_response": "REJECTED",
    "wrong_request_id": "REJECTED",
    "wrong_facts_sha": "REJECTED",
    "sensitive_shape": "REJECTED",
    "redirect_response": "REJECTED",
    "external_url_response": "REJECTED",
}
```

Require the valid response to contain all 42 typed Claims and every mutation to
change only the field needed for that scenario.

- [x] **Step 3: Run Proxy/Mock tests and require RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_provider_relay.py -q \
  -k 'proxy or mock_provider'
```

- [x] **Step 4: Implement the fixed Proxy**

Use `HTTPServer` with `log_message` overridden to no-op. Read the Secret with
`O_NOFOLLOW`, inject it only into the upstream authorization header, and never
echo it. Refuse unapproved method/path before opening upstream. Do not accept a
target URL from request headers or body. Reject 3xx without exposing Location.

Write a receipt containing only method/path policy booleans, upstream attempt
count, response category, response size, and Secret-present boolean.

- [x] **Step 5: Implement the Mock Provider**

Use one standard-library HTTP server with disabled logs. Validate method/path,
authorization, exact Provider envelope, fixed generation controls, and scenario
file. Production image imports `/app/candidate_factory.py`, copied from the
unchanged `docker/phase2-ai-worker/worker.py` at build time. The Mock mutates a
deep copy of the valid Candidate per scenario and never writes request content.

The timeout scenario sleeps longer than two seconds. Redirect returns a 302
whose target is never followed. External URL returns JSON containing an
unapproved URL field so strict response validation rejects it without a second
request.

- [x] **Step 6: Add pinned Dockerfiles and static policy tests**

Both Dockerfiles use the same complete distroless digest and non-root entrypoint.
Mock builds with `docker` as context so its Dockerfile may copy only:

```text
phase2-ai-worker/worker.py -> /app/candidate_factory.py
phase2-mock-provider/mock_provider.py
phase2-mock-provider/*.placeholder*
```

Static tests reject any other repository copy, `RUN`, shell, package manager,
credential, or network download.

- [x] **Step 7: Run all pure protocol/component tests**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_provider_relay.py -q
.venv/bin/ruff check app/schemas/phase2_provider_relay.py \
  app/services/phase2_provider_relay_protocol.py \
  tests/test_phase2_provider_relay.py
```

- [x] **Step 8: Commit Task 3**

```bash
git add docker/phase2-egress-proxy docker/phase2-mock-provider \
  backend/tests/test_phase2_provider_relay.py
git commit -m "feat: add fixed phase2 mock egress path"
```

---

### Task 4: Host Docker and Network Contracts

**Files:**
- Create: `backend/app/services/phase2_provider_relay_runner.py`
- Create: `backend/tests/test_phase2_provider_relay_runtime.py`

**Interfaces:**
- Consumes: exact image names, temporary single-file paths, Docker inspect/network inspect JSON, and an injected command executor.
- Produces: `RelayRuntimePaths`, `ProxyRuntimePaths`, `MockRuntimePaths`, command builders, inspect validators, network topology validators, sanitized contract evidence, and stable runtime error codes.

- [x] **Step 1: Write failing command construction tests**

Require exact argument arrays for two internal networks and three containers.
Relay command must have exactly four mounts and one network; Proxy two mounts
and two networks after an explicit `docker network connect`; Mock three mounts
and one network. Every container command includes:

```text
--read-only
--user 65532:65532
--cap-drop ALL
--security-opt no-new-privileges
--pids-limit 16
--memory 128m
--cpus 0.5
--ipc none
--restart no
```

Reject command strings containing privileged/host networking, published ports,
devices, env/env-file, Home, repository, Vault, SSH, user config, database, or
Docker control mounts.

- [x] **Step 2: Write failing inspect and topology mutation tests**

Start from valid sanitized fixtures, then independently mutate user, rootfs,
capabilities, security options, resources, restart policy, mount count/mode,
network membership, `Internal`, published ports, devices, image environment,
and state/exit code. Require a stable failure code for every mutation.

The network validator requires this exact membership matrix:

```text
relay_proxy_net: Relay + Proxy
proxy_provider_net: Proxy + Mock
```

- [x] **Step 3: Write failing Secret boundary tests**

Create a random canary at test runtime. Assert it is absent from commands,
inspect payloads, receipts, reports, logs, and serialized evidence. Require
Relay mounts not to include the Secret path while Proxy and Mock each have one
read-only Secret mount. Assert evidence has no Secret hash field.

- [x] **Step 4: Run Task 4 tests and require RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_provider_relay_runtime.py -q
```

- [x] **Step 5: Implement immutable path and command builders**

Resolve every path only after rejecting symlinks and non-regular files. Build
argument lists only and call no shell. Container/network names are random,
bounded, and never persisted. Image references are fixed constants; the API
accepts no image, endpoint, network, mount, user, or security override.

- [x] **Step 6: Implement inspect and network validators**

Validate actual Docker fields and return dataclasses whose `to_dict()` methods
contain only booleans, counts, image digests, and stable errors. Do not include
container IDs/names, IPs, commands, environments, mount sources, or raw payloads.

- [x] **Step 7: Run Task 4 tests and static checks**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_provider_relay_runtime.py -q
.venv/bin/ruff check app/services/phase2_provider_relay_runner.py \
  tests/test_phase2_provider_relay_runtime.py
.venv/bin/python -m compileall -q app
```

- [x] **Step 8: Commit Task 4**

```bash
git add backend/app/services/phase2_provider_relay_runner.py \
  backend/tests/test_phase2_provider_relay_runtime.py
git commit -m "feat: add provider relay runtime contracts"
```

---

### Task 5: No-retry Orchestrator, Host Validation, and Atomic Routing

**Files:**
- Modify: `backend/app/services/phase2_provider_relay_runner.py`
- Create: `backend/scripts/run_phase2_mock_provider_relay.py`
- Create: `backend/scripts/validate_phase2_provider_relay.py`
- Modify: `backend/tests/test_phase2_provider_relay_runtime.py`

**Interfaces:**
- Consumes: repository/reports roots, fixed Facts/Projection, fixed images, Docker executor, and current UTC clock.
- Produces: `run_mock_provider_relay(repo_root, reports_root) -> ProviderRelayResult`, atomic `reports/phase2_provider_relay/`, sanitized scenario receipts, one valid inbox Markdown, rejection records, and runtime evidence.

- [x] **Step 1: Write failing lifecycle tests with a fake Docker executor**

Cover this order per scenario:

```text
create two internal networks
create/start Mock on provider network
create/start Proxy on provider network
connect Proxy to relay network
create/start Relay on relay network
inspect containers and networks
read fixed output files
remove Relay, Proxy, Mock
remove both networks
delete temporary tree
```

Assert one Relay start, one Proxy upstream attempt, one Mock request, and zero
retry. Fail create/start/inspect/output/validation/cleanup one boundary at a
time and require blocked status plus cleanup of every successfully created
object.

- [x] **Step 2: Write failing full Mock matrix tests**

Use the fake executor to produce two valid runs and every invalid scenario.
Require only valid runs to pass Host Candidate/Claims/Renderer validation.
Require normalized Candidate and rendered hashes to match across valid runs.
Every invalid run must route one sanitized record to rejected and no Markdown
to inbox.

- [x] **Step 3: Write failing network-probe and Secret lifecycle tests**

Require all ten network policy results, exact enum values, zero real-public
successes, no redirect/content-URL follow, no Secret hit or digest, and zero
container/network/file residue. Verify the generated Secret is random per suite
and absent after return.

- [x] **Step 4: Write failing atomic publication and invariance tests**

Require staging + fsync + atomic directory exchange. Simulate publication
failure and prove prior output remains intact. Verify the runner never writes
outside `reports/phase2_provider_relay/` and does not change any protected hash.

- [x] **Step 5: Run Task 5 tests and require RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_provider_relay_runtime.py -q
```

- [x] **Step 6: Implement one-scenario lifecycle and fail-closed cleanup**

Use `subprocess.run(shell=False, check=False, capture_output=True, text=True,
timeout=...)` behind an injected executor. There is no loop that retries a
Provider attempt. Cleanup tracks created resources and removes each once in
reverse dependency order. Cleanup errors always block success.

- [x] **Step 7: Implement Host validation and deterministic routing**

For a successful Relay envelope call the existing `validate_isolated_candidate`
with the original Projection, then the existing Claims/Renderer chain. Compute
hashes from canonical Candidate bytes and UTF-8 rendered bytes. Publish one
canonical inbox Markdown only after both valid runs match.

Rejected records contain scenario, stable error codes, HTTP category, response
size/hash when available, and route only. They never contain raw responses or
free text from Provider.

- [x] **Step 8: Implement sanitized audit and atomic publication**

Record every required audit field and zero external counters. Generate the test
Secret with `secrets.token_urlsafe` in memory, write it to one temporary file,
scan exact bytes before deletion, and never compute its digest. Publish with the
existing atomic directory helper.

- [x] **Step 9: Implement fixed CLIs**

`run_phase2_mock_provider_relay.py` accepts only `--repo-root` and
`--reports-root`; it exposes no endpoint/image/network/Secret/force/retry
override. `validate_phase2_provider_relay.py` is offline and validates file set,
schemas, hashes, routes, counters, and absence of sensitive shapes.

- [x] **Step 10: Run Task 5 tests and require GREEN**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_provider_relay.py \
  tests/test_phase2_provider_relay_runtime.py -q
.venv/bin/ruff check app/schemas/phase2_provider_relay.py \
  app/services/phase2_provider_relay_protocol.py \
  app/services/phase2_provider_relay_runner.py \
  scripts/run_phase2_mock_provider_relay.py \
  scripts/validate_phase2_provider_relay.py \
  tests/test_phase2_provider_relay.py \
  tests/test_phase2_provider_relay_runtime.py
```

- [x] **Step 11: Commit Task 5**

```bash
git add backend/app/services/phase2_provider_relay_runner.py \
  backend/scripts/run_phase2_mock_provider_relay.py \
  backend/scripts/validate_phase2_provider_relay.py \
  backend/tests/test_phase2_provider_relay_runtime.py
git commit -m "feat: orchestrate restricted provider relay"
```

---

### Task 6: Real Docker Runtime and Egress Proof

**Files:**
- Create: `scripts/test_phase2_provider_relay_runtime.sh`
- Modify: `backend/tests/test_phase2_provider_relay.py`
- Modify: `backend/tests/test_phase2_provider_relay_runtime.py`
- Generate: `reports/phase2_provider_relay/runtime_evidence.json`
- Generate: `reports/phase2_provider_relay/mock_preview/inbox/`
- Generate: `reports/phase2_provider_relay/mock_preview/rejected/`
- Generate: `reports/phase2_provider_relay/mock_preview/receipts/`

**Interfaces:**
- Consumes: running Docker Desktop, pinned local base image, committed component contexts, and no credentials.
- Produces: one real local Mock E2E evidence tree and no residual runtime objects.

- [x] **Step 1: Write failing runtime script/static-policy tests**

Require the shell script to use `set -euo pipefail`, exact fixed image tags,
`--network none --pull=false` builds, no credentials, no `eval`, no dynamic
endpoint, no force/retry option, and one invocation of the Host run CLI. Require
all three Dockerfiles to pass digest/user/entrypoint/import/copy policy tests.

- [x] **Step 2: Run runtime-policy tests and require RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_provider_relay.py \
  tests/test_phase2_provider_relay_runtime.py -q -k 'dockerfile or runtime_script'
```

- [x] **Step 3: Implement the fixed runtime shell script**

The script checks Docker availability, builds:

```text
tickflow-phase2-provider-relay:runtime-v1
tickflow-phase2-egress-proxy:runtime-v1
tickflow-phase2-mock-provider:runtime-v1
```

Relay and Proxy use their own context directories. Mock uses `docker` as build
context with `docker/phase2-mock-provider/Dockerfile`. All builds are offline
from the already present pinned base. The script then invokes the Host CLI once
and runs the offline validator once.

- [x] **Step 4: Run focused tests before Docker**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_provider_relay.py \
  tests/test_phase2_provider_relay_runtime.py -q
cd ..
bash -n scripts/test_phase2_provider_relay_runtime.sh
```

- [x] **Step 5: Execute the real Docker runtime script once**

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-phase2-ai-review"
bash scripts/test_phase2_provider_relay_runtime.sh
```

Do not retry a failed Provider scenario, switch images, change a network, or
relax a runtime flag. Diagnose any implementation failure with a new RED test
before changing production code. A true egress or Secret boundary failure ends
with the corresponding blocked status.

- [x] **Step 6: Verify real runtime residue and evidence**

Require zero matching containers and networks, no Secret/request/response temp
files, no staging directories, no raw inspect/log/command files, and no exact
test Secret hit. Verify image IDs and Dockerfile hashes are content-addressed
and evidence contains all 22 scenarios plus the second valid run and ten network
policy results.

- [x] **Step 7: Commit runtime proof**

```bash
git add scripts/test_phase2_provider_relay_runtime.sh \
  backend/tests/test_phase2_provider_relay.py \
  backend/tests/test_phase2_provider_relay_runtime.py \
  reports/phase2_provider_relay
git commit -m "test: verify provider relay egress and secret boundaries"
```

---

### Task 7: Full Regression, Invariance, Report, and Branch Delivery

**Files:**
- Create: `reports/tickflow_phase2b_provider_relay_eval.md`
- Create: `docs/phase2-provider-canary-runbook.md`
- Modify: this plan only to mark completed steps.

**Interfaces:**
- Consumes: committed runtime evidence, all protected preflight digests, test outputs, image/contract hashes, and residue scans.
- Produces: final report, controlled-canary runbook with no real credential or execution command, final status, commits, and verified fork branch SHA.

- [x] **Step 1: Run the required focused suite**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_provider_relay.py \
  tests/test_phase2_provider_relay_runtime.py -q
```

- [x] **Step 2: Run backend full and static validation**

```bash
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python -m compileall -q app scripts
.venv/bin/ruff check app scripts --select F821
.venv/bin/ruff check app/schemas/phase2_provider_relay.py \
  app/services/phase2_provider_relay_protocol.py \
  app/services/phase2_provider_relay_runner.py \
  scripts/run_phase2_mock_provider_relay.py \
  scripts/validate_phase2_provider_relay.py \
  tests/test_phase2_provider_relay.py \
  tests/test_phase2_provider_relay_runtime.py
```

- [x] **Step 3: Revalidate Claims and Relay artifacts offline**

Run typed Claims validation and require `CLAIMS_VALID`. Run the historical
freeform validator and require the expected fail-closed
`PHASE2_AI_OUTPUT_BLOCKED`. Run `validate_phase2_provider_relay.py` and require
`PHASE2B_PROVIDER_RELAY_READY` with every invalid scenario rejected.

- [x] **Step 4: Recompute all protected digests**

Use the exact sorted `shasum -a 256` aggregation from preflight. Every digest in
Global Constraints must match byte-for-byte. A mismatch stops as
`PHASE2B_EVIDENCE_MUTATED`; do not repair or regenerate the protected tree.

- [x] **Step 5: Run sensitive and residue scans**

Scan code and new reports for credential shapes, absolute Home/Vault paths,
raw headers, inspect, stdout/stderr, commands, IDs/IPs, Secret hashes, staging,
tmp/partial/bak files, and transaction markers. Confirm Docker has no matching
containers/networks and the real-public-network success count is zero.

- [x] **Step 6: Write the evaluation report**

Answer all 14 questions in the approved design. Include exact Docker/image
digests, runtime controls, topology booleans, Secret lifecycle, valid/invalid
matrix, deterministic hashes, Host validation, cleanup, tests, invariance, zero
external actions, and limitations. State that a real single-symbol canary still
requires separate approval and was not run.

- [x] **Step 7: Write the controlled-canary runbook**

Document only future approval gates, required TLS/credential controls, one-symbol
scope, abort criteria, human verification, cleanup, and evidence requirements.
Do not include a real endpoint, model, key, executable live command, or imply
current approval.

- [x] **Step 8: Commit report, runbook, and completed plan**

```bash
git add reports/tickflow_phase2b_provider_relay_eval.md \
  docs/phase2-provider-canary-runbook.md \
  docs/superpowers/plans/2026-08-01-tickflow-phase2b3a-provider-relay.md
git commit -m "docs: add controlled AI canary runbook"
```

- [x] **Step 9: Push only the fork branch**

```bash
git status --short
git log -8 --oneline
git push fork codex/tickflow-phase2-ai-review
git rev-parse HEAD
git ls-remote fork refs/heads/codex/tickflow-phase2-ai-review
```

Require local and remote SHA equality and a clean worktree. Do not create a PR,
merge, deploy, or remove the worktree.

- [x] **Step 10: Stop at the approved state**

The only success state is:

```text
PHASE2B_PROVIDER_RELAY_READY
```

Stop immediately. Do not configure AI, call a real Provider, run a three-symbol
batch, write to a real Vault, start Paper Trading, or enable any integration.
