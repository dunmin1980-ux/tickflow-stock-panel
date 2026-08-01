# TickFlow Phase 2B-3B Dedicated OpenAI Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and prove a separately approved, standard-library-only OpenAI Responses Proxy artifact so the single-symbol Canary offline validator can stop at `CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL` without reading the real Secret or opening a public connection.

**Architecture:** Keep the accepted Mock Proxy and Provider Relay evidence unchanged. Add a dedicated OpenAI adapter image, a deterministic Host-generated contract, a pure future-launcher command contract, and an independently approved non-sensitive artifact manifest; the final preflight validates source, image, TLS policy, Secret mechanism, Facts/Projection, and all frozen evidence while external request counters remain zero.

**Tech Stack:** Python 3.12, standard-library `http.client`/`ssl` inside the dedicated image, Pydantic v2 on the Host, digest-pinned distroless Python, Docker CLI through argument arrays, pytest, Ruff, SHA-256, canonical JSON.

## Global Constraints

- Approved branch: `codex/tickflow-phase2-ai-review`.
- Design baseline: `165dc46ec9c10806ddcc4ff5d2171bc24ec7a87e`.
- Fixed symbol: `000403.SZ`; fixed trade date: `2026-07-31`.
- Provider/model/endpoint: `openai`, `gpt-5.6-terra`, `POST https://api.openai.com:443/v1/responses`.
- TLS verification and hostname checking are mandatory; minimum TLS is 1.2.
- Redirects, streaming, tools, web, files, retries, alternate models, alternate Providers, and publishing are disabled.
- The Provider request top-level keys are exactly `model`, `stream`, `tools`,
  `input`, and `text`. Do not add `temperature`, `store`, or another
  unapproved field; `temperature` is deliberately omitted because the current
  Provider approval does not authorize it.
- Provider attempts, AI calls, TickFlow requests, and successful public connections remain zero.
- Real Keychain Secret content, length, prefix, suffix, and hash must never be read or recorded.
- Existing Mock Proxy, Mock Provider, Provider Relay runtime evidence, Phase 1 evidence, Phase 2 Facts, typed Claims/inbox, historical AI Markdown, historical rejected, and isolation evidence remain byte-for-byte unchanged.
- No real Obsidian write, Paper Trading, cloud deployment, Telegram, OpenClaw, or Integrated Gold action.
- No real Provider request is executed by this plan.
- Implementation paths remain inside section 14 of the approved design. The
  contract module owns its `--write`/`--check` CLI, and dedicated Proxy tests
  live in the approved contract test module; no extra helper path expands the
  authorized file boundary.
- Request and response fixtures are anchored to the official
  [Responses create reference](https://developers.openai.com/api/reference/resources/responses/methods/create),
  [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs),
  and [GPT-5.6 Terra model page](https://developers.openai.com/api/docs/models/gpt-5.6-terra).
- Every code task uses RED -> GREEN -> refactor and ends with a focused commit.

---

### Task 1: Deterministic OpenAI Contract Artifact

**Files:**
- Create: `backend/app/services/phase2_openai_proxy_contract.py`
- Create: `backend/tests/test_phase2_openai_proxy_contract.py`
- Generate: `docker/phase2-openai-egress-proxy/responses-contract.json`
- Read: `backend/app/schemas/phase2_claims.py`
- Read: `backend/app/schemas/phase2_canary_provider_config.py`
- Read: `backend/app/schemas/phase2_provider_relay.py`
- Read: `backend/app/services/phase2_claims_service.py`

**Interfaces:**
- Produces: `OpenAIProxyPolicy`, `strict_worker_schema() -> dict[str, Any]`,
  `build_proxy_contract() -> dict[str, Any]`,
  `canonical_contract_payload(value: Mapping[str, Any]) -> bytes`,
  `canonical_proxy_contract_bytes() -> bytes`,
  `proxy_policy_sha256() -> str`, and
  `main(argv: Sequence[str] | None = None) -> int` with CLI modes
  `--check`/`--write`.
- Consumes: `build_responses_request_contract()` for the existing strict
  `WorkerClaimsCandidate` schema, `GenerationControls`,
  `ALLOWED_CLAIM_TYPES`, and `PREDICATE_RULES`.

- [ ] **Step 1: Write failing tests for exact policy and deterministic bytes**

Add tests with exact assertions:

```python
def test_proxy_policy_is_closed_and_exact() -> None:
    policy = OpenAIProxyPolicy()
    assert policy.model_dump(mode="json") == {
        "provider_id": "openai",
        "model_id": "gpt-5.6-terra",
        "endpoint_host": "api.openai.com",
        "endpoint_port": 443,
        "endpoint_path": "/v1/responses",
        "http_method": "POST",
        "tls_verification": True,
        "minimum_tls_version": "TLSv1_2",
        "follow_redirects": False,
        "stream": False,
        "tools": [],
        "retry_count": 0,
        "maximum_attempts": 1,
        "timeout_seconds": 60,
        "maximum_bytes": 1_048_576,
        "approved_symbol": "000403.SZ",
    }


def test_contract_bytes_are_deterministic_and_schema_bound() -> None:
    first = canonical_proxy_contract_bytes()
    second = canonical_proxy_contract_bytes()
    contract = json.loads(first)
    assert first == second
    assert first.endswith(b"\n")
    assert contract["response_format"]["type"] == "json_schema"
    assert contract["response_format"]["strict"] is True
    assert contract["response_format"]["schema"] == strict_worker_schema()
    recorded = contract.pop("contract_sha256")
    assert hashlib.sha256(canonical_contract_payload(contract)).hexdigest() == recorded
    assert contract["relay_contract"] == {
        "protocol_version": 1,
        "claims_schema_version": 1,
        "response_format": "typed_claims_json",
        "approved_symbol": "000403.SZ",
        "approved_trade_date": "2026-07-31",
        "allowed_claim_types": sorted(ALLOWED_CLAIM_TYPES),
        "allowed_predicates": sorted(PREDICATE_RULES),
        "generation": GenerationControls().model_dump(mode="json"),
    }
```

Parameterize exact policy mutations for provider, model, host, port, path,
method, TLS, minimum TLS, redirect, stream, tools, retry, attempts, timeout,
maximum bytes, and symbol. Each mutation must raise `ValidationError`.

- [ ] **Step 2: Run the focused tests and require RED**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_proxy_contract.py -q
```

Expected: collection fails because `phase2_openai_proxy_contract` does not
exist.

- [ ] **Step 3: Implement the closed Host policy and canonical contract**

Use a frozen strict Pydantic model with `Literal` fields. The canonical object
has exactly:

```python
def build_proxy_contract() -> dict[str, Any]:
    policy = OpenAIProxyPolicy()
    value = {
        "contract_schema_version": 1,
        "policy": policy.model_dump(mode="json"),
        "relay_contract": {
            "protocol_version": 1,
            "claims_schema_version": 1,
            "response_format": "typed_claims_json",
            "approved_symbol": "000403.SZ",
            "approved_trade_date": "2026-07-31",
            "allowed_claim_types": sorted(ALLOWED_CLAIM_TYPES),
            "allowed_predicates": sorted(PREDICATE_RULES),
            "generation": GenerationControls().model_dump(mode="json"),
        },
        "fixed_instructions": [
            "Use only supplied projection facts and pointers.",
            "Return exactly one schema-valid candidate object.",
            "Do not provide trading advice, predictions, news, or financial assertions.",
            "Do not emit markdown, prose, URLs, tool calls, or file references.",
        ],
        "response_format": {
            "type": "json_schema",
            "name": "tickflow_phase2_claims_candidate",
            "strict": True,
            "schema": strict_worker_schema(),
        },
    }
    payload = canonical_contract_payload(value)
    value["contract_sha256"] = hashlib.sha256(payload).hexdigest()
    return value
```

Define `canonical_proxy_contract_bytes()` by serializing a copy without
`contract_sha256`, computing the digest, inserting it, and serializing once
more with `allow_nan=False`, sorted keys, two-space indentation, UTF-8, and one
terminal newline. Define a matching validator that recomputes the digest over
the digest-free object; tests must reject a self-hash calculated over different
bytes.

- [ ] **Step 4: Implement atomic generator/check CLI**

The module CLI makes `--write` and `--check` mutually exclusive, rejects both
together, defaults to `--check` when neither is supplied, resolves the
repository root from `__file__`, rejects symlinked
targets, and writes through a same-directory temporary file with `fsync` plus
`os.replace`. The same module ends with
`raise SystemExit(main())` under `if __name__ == "__main__"`:

```python
def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    expected = canonical_proxy_contract_bytes()
    target = repo_root() / "docker/phase2-openai-egress-proxy/responses-contract.json"
    if args.write:
        atomic_write(target, expected, mode=0o644)
        return 0
    return 0 if target.is_file() and not target.is_symlink() and target.read_bytes() == expected else 2
```

- [ ] **Step 5: Run GREEN, generate the artifact, and re-check it**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_proxy_contract.py -q
PYTHONPATH=. .venv/bin/python -m app.services.phase2_openai_proxy_contract --write
PYTHONPATH=. .venv/bin/python -m app.services.phase2_openai_proxy_contract --check
```

Expected: tests pass and both generator commands exit `0`.

- [ ] **Step 6: Commit Task 1**

```bash
cd "$(git rev-parse --show-toplevel)"
git add backend/app/services/phase2_openai_proxy_contract.py \
  backend/tests/test_phase2_openai_proxy_contract.py \
  docker/phase2-openai-egress-proxy/responses-contract.json
git commit -m "feat: add deterministic OpenAI proxy contract"
```

---

### Task 2: Dedicated Proxy Pure Request and Response Logic

**Files:**
- Create: `docker/phase2-openai-egress-proxy/proxy.py`
- Create: `docker/phase2-openai-egress-proxy/secret.placeholder`
- Create: `docker/phase2-openai-egress-proxy/receipt.placeholder.json`
- Modify: `backend/tests/test_phase2_openai_proxy_contract.py`
- Read: `docker/phase2-provider-relay/relay.py`
- Read: `reports/phase2_facts/000403SZ_facts.json`

**Interfaces:**
- Consumes: Relay envelope `{request, projection, generation}` and `responses-contract.json`.
- Produces: `ProxyError`,
  `load_contract(path: str = "/proxy/responses-contract.json")
  -> dict[str, Any]`,
  `validate_relay_envelope(value: Any, contract: Mapping[str, Any])
  -> dict[str, Any]`,
  `build_provider_request(envelope: Mapping[str, Any],
  contract: Mapping[str, Any]) -> dict[str, Any]`,
  `extract_candidate(response: Mapping[str, Any],
  contract: Mapping[str, Any]) -> dict[str, Any]`, and
  `validate_candidate(candidate: Any, schema: Mapping[str, Any])
  -> dict[str, Any]`.

- [ ] **Step 1: Write failing tests that import the dedicated source directly**

Use `importlib.util.spec_from_file_location` to load `proxy.py`. Build the Relay
envelope with existing `build_relay_request`, `build_provider_envelope`, and the
committed `000403.SZ` Projection.

The primary request test must assert:

```python
def test_provider_request_is_exact_and_projection_only(proxy_module, relay_envelope, contract):
    payload = proxy_module.build_provider_request(relay_envelope, contract)
    assert set(payload) == {"model", "stream", "tools", "input", "text"}
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["stream"] is False
    assert payload["tools"] == []
    assert payload["text"]["format"] == contract["response_format"]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "000403.SZ" in serialized
    assert "reports/phase2_facts" not in serialized
    assert str(REPO_ROOT) not in serialized
    assert str(Path.home()) not in serialized
    assert "temperature" not in payload
```

Add parameterized envelope mutations for unknown top-level field, wrong symbol,
wrong Projection SHA, wrong Facts SHA, wrong response format, altered allowed
claims/predicates, enabled tool/web/file/function flags, streaming, retry,
maximum bytes, timeout, non-finite number, trailing JSON, and oversized input.
Every case must raise `ProxyError("REQUEST_SCHEMA_BLOCKED")` before transport.

- [ ] **Step 2: Write failing response extraction and schema tests**

Use a full non-streaming Responses envelope shaped from the official create
reference. Because `gpt-5.6-terra` is a reasoning model, accept zero or more
closed `reasoning` output items followed by exactly one completed assistant
message; reject every other output-item type and never persist reasoning data:

```python
def response_fixture(candidate: dict) -> dict:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "completed_at": 1,
        "error": None,
        "incomplete_details": None,
        "model": "gpt-5.6-terra",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_test",
                "summary": [],
            },
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(candidate, allow_nan=False, sort_keys=True),
                        "annotations": [],
                        "logprobs": [],
                    }
                ],
            }
        ],
        "tools": [],
    }
```

Assert the fixture with and without the optional reasoning item returns one
valid Candidate unchanged. Parameterize wrong object/status/model, non-null
error or incomplete details, missing output, two messages, a tool/function/web/
file output item, malformed reasoning item, two content items, refusal content,
non-`output_text`, non-empty annotations/logprobs, empty text, markdown,
trailing JSON, list output, unknown Candidate field, wrong symbol, wrong trade
date, wrong Facts SHA, wrong Projection SHA, trading advice true, unknown Claim
type/predicate/pointer/unit/price basis, non-finite number, and schema-invalid
nested object. Every case must fail with one stable category and no repair.

The parser validates required top-level identity/status fields and every
`output` item. It may ignore documented non-payload response metadata, but it
must reject unknown output item/content shapes. A reasoning item is accepted
only with exact keys `id`, `type`, and empty `summary`; encrypted content,
reasoning text, or any extra reasoning field is rejected because none was
requested.

- [ ] **Step 3: Run focused tests and require RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_proxy_contract.py -q
```

Expected: import fails because the dedicated Proxy source does not exist.

- [ ] **Step 4: Implement bounded JSON and Relay-envelope validation**

Define immutable constants:

```python
ALLOWED_METHOD = "POST"
ALLOWED_PATH = "/v1/typed-claims"
UPSTREAM_HOST = "api.openai.com"
UPSTREAM_PORT = 443
UPSTREAM_PATH = "/v1/responses"
MODEL_ID = "gpt-5.6-terra"
FOLLOW_REDIRECTS = False
RETRY_COUNT = 0
MAXIMUM_ATTEMPTS = 1
MAXIMUM_BYTES = 1_048_576
TIMEOUT_SECONDS = 60
```

Implement a bounded `strict_object(raw, category)` with UTF-8 decoding,
`JSONDecoder.raw_decode`, `parse_constant` rejection, trailing-data rejection,
and exact object requirement. Validate every Relay request/projection/generation
field against `contract["relay_contract"]`; require a 32-lowercase-hex request
ID, a 64-lowercase-hex Facts/Projection SHA, exact Projection field sets,
`trade_date == "2026-07-31"`, `timezone == "Asia/Shanghai"`, recomputed
Projection SHA, and equality among request, Projection, and contract bindings.
Do not accept a caller-supplied Provider option.

Use two deliberately different canonical encodings: Projection SHA validation
must match the Host encoding exactly (`ensure_ascii=False`, `allow_nan=False`,
two-space indentation, sorted keys, terminal newline, with
`projection_sha256` removed); only the human-readable Provider input text uses
the compact encoding defined in Step 5. Tests compare both hashes to the
existing Host `compute_projection_sha256()` result.

- [ ] **Step 5: Implement request construction and Candidate validation**

Construct `input` as exactly two messages, each containing exactly one
`input_text` item, with no files, URLs, images, or tool content:

```python
payload = {
    "model": MODEL_ID,
    "stream": False,
    "tools": [],
    "input": [
        {
            "role": "developer",
            "content": [{"type": "input_text", "text": instructions_text}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": canonical_json_text(envelope["projection"]),
                }
            ],
        },
    ],
    "text": {"format": contract["response_format"]},
}
```

Join `fixed_instructions` with `"\n"`; serialize the Projection with sorted
keys, compact separators, `ensure_ascii=False`, and `allow_nan=False`.
Implement a closed recursive validator
for the generated schema keywords used by `WorkerClaimsCandidate`:

```text
$defs, $ref, type, const, enum, anyOf, required, properties,
additionalProperties=false, items, pattern, minLength, maxLength,
minimum, maximum, minItems, maxItems, title
```

Treat `title` only as a string annotation; it cannot alter validation. Resolve
only local `#/$defs/<name>` references, reject reference cycles and remote
references, and reject unsupported schema keywords during contract load.
Reject booleans where
the schema requires a number and reject NaN/Infinity. Compare the dedicated
validator against Pydantic in tests using all valid FakeClaimsWorker Candidates
and every invalid mutation listed in Step 2.

- [ ] **Step 6: Run GREEN and source security scans**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_proxy_contract.py -q
PYTHONPATH=. .venv/bin/python -m compileall -q ../docker/phase2-openai-egress-proxy
.venv/bin/ruff check ../docker/phase2-openai-egress-proxy/proxy.py \
  tests/test_phase2_openai_proxy_contract.py
if rg -n 'requests|urllib|http\.client\.HTTPConnection\(|os\.environ|subprocess|socket\.' \
  ../docker/phase2-openai-egress-proxy/proxy.py; then
  exit 1
fi
rg -n 'http\.client\.HTTPSConnection\(' \
  ../docker/phase2-openai-egress-proxy/proxy.py
```

Expected: tests/lint compile clean, the prohibited scan has no matches, and the
positive scan finds the deliberate `HTTPSConnection` call exactly once.

- [ ] **Step 7: Commit Task 2**

```bash
cd "$(git rev-parse --show-toplevel)"
git add docker/phase2-openai-egress-proxy/proxy.py \
  docker/phase2-openai-egress-proxy/secret.placeholder \
  docker/phase2-openai-egress-proxy/receipt.placeholder.json \
  backend/tests/test_phase2_openai_proxy_contract.py
git commit -m "feat: add dedicated OpenAI proxy contract logic"
```

---

### Task 3: TLS Transport, Handler, and Sanitized Receipt

**Files:**
- Modify: `docker/phase2-openai-egress-proxy/proxy.py`
- Modify: `backend/tests/test_phase2_openai_proxy_contract.py`

**Interfaces:**
- Produces: `create_tls_context() -> ssl.SSLContext`,
  `read_auth_file(path: str = "/run/phase2/provider-auth") -> str`,
  `perform_provider_request(body: bytes, auth_value: str,
  *, connection_factory: ConnectionFactory = default_connection_factory)
  -> tuple[int, bytes]`, `ProxyHandler`, and stable receipt categories, where
  `ConnectionFactory = Callable[[str, int, int, ssl.SSLContext],
  http.client.HTTPSConnection]`.
- Consumes: the pure request/response functions from Task 2.

- [ ] **Step 1: Write failing TLS and one-attempt tests**

Use a connection factory double that records constructor arguments, request
arguments, and call counts without opening a socket:

```python
def test_transport_uses_one_verified_https_request(
    proxy_module, valid_body, fake_connection, tls_context, monkeypatch
):
    monkeypatch.setattr(proxy_module, "create_tls_context", lambda: tls_context)
    status, raw = proxy_module.perform_provider_request(
        valid_body,
        "synthetic-value",
        connection_factory=fake_connection.factory,
    )
    assert status == 200
    assert raw == fake_connection.response_body
    assert fake_connection.created == [("api.openai.com", 443, 60)]
    assert fake_connection.contexts == [tls_context]
    assert fake_connection.requests == [
        (
            "POST",
            "/v1/responses",
            valid_body,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer synthetic-value",
            },
        )
    ]
    assert fake_connection.getresponse_count == 1
    assert fake_connection.close_count == 1
```

The fake factory accepts `(host, port, timeout, context)` but records the first
three values separately from the context. Patch `ssl.create_default_context`
and assert `check_hostname=True`,
`verify_mode=ssl.CERT_REQUIRED`, and
`minimum_version >= ssl.TLSVersion.TLSv1_2`. Patch `socket.socket` to raise if
any focused test reaches it.

- [ ] **Step 2: Write failing transport/handler failure tests**

Cover wrong local method/path, absent or invalid Content-Length, oversized
request, missing auth file, auth symlink, non-regular auth file, auth file over
16 KiB, empty auth, embedded NUL/CR/LF, timeout, TLS exception, 301/307, 429,
500, wrong response Content-Type, invalid/oversized response body, model
mismatch, malformed Provider JSON, and schema-invalid output. Assert:

```python
assert attempt_count in {0, 1}
assert retry_count == 0
assert authorization_value not in serialized_receipt
assert raw_request_body not in serialized_receipt
assert raw_response_body not in serialized_receipt
```

The handler must return a stable local status and write one receipt category;
it must never follow `Location` or invoke the factory twice.

Use this exact local status map: success `200`; method `405`; path `404`;
request size `413`; request/schema `400`; auth `503`; timeout `504`; upstream
429 `429`; every other TLS/upstream/response/output failure `502`. The JSON
error response contains only `{"error": "<STABLE_CATEGORY>"}`.

- [ ] **Step 3: Run RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_proxy_contract.py -q
```

Expected: TLS/transport/handler tests fail because those interfaces are absent.

- [ ] **Step 4: Implement verified HTTPS transport**

Use only:

```python
def create_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile="/etc/ssl/certs/ca-certificates.crt")
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def default_connection_factory(host: str, port: int, timeout: int, context: ssl.SSLContext):
    return http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
```

`perform_provider_request` creates one connection, calls `request` once, reads
at most `MAXIMUM_BYTES + 1`, rejects every 3xx without reading/following
`Location`, maps 429 separately, rejects other non-2xx statuses, and closes in
`finally`. It accepts only an `application/json` response media type, allowing
an optional charset parameter. It has no loop and no retry function.

`read_auth_file` opens only the fixed path through `O_RDONLY | O_NOFOLLOW`,
requires a regular mode-0600 file no larger than 16 KiB, performs one bounded
descriptor read, decodes UTF-8, and rejects empty values plus NUL, CR, LF, or
surrounding whitespace. It never measures, hashes, logs, or returns metadata
about the value.

- [ ] **Step 5: Implement no-log handler and receipt**

Use `BaseHTTPRequestHandler` with `log_message` disabled. The receipt has exact
keys:

```text
receipt_schema_version
method_allowed
path_allowed
auth_present
tls_verification
redirect_followed
provider_attempt_count
retry_count
provider_http_status
response_category
response_size
```

Write only through an existing regular receipt file with `O_NOFOLLOW`,
`fsync`, and canonical JSON. Do not store response SHA because the report does
not need raw Provider-body identity before the approved call.

- [ ] **Step 6: Run GREEN and prove no network**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_proxy_contract.py -q
PYTHONPATH=. .venv/bin/python -m compileall -q ../docker/phase2-openai-egress-proxy
.venv/bin/ruff check ../docker/phase2-openai-egress-proxy/proxy.py \
  tests/test_phase2_openai_proxy_contract.py
```

Expected: all pass; the strict socket guard proves no real connection occurred.

- [ ] **Step 7: Commit Task 3**

```bash
cd "$(git rev-parse --show-toplevel)"
git add docker/phase2-openai-egress-proxy/proxy.py \
  backend/tests/test_phase2_openai_proxy_contract.py
git commit -m "feat: enforce OpenAI proxy TLS and failure closure"
```

---

### Task 4: Dedicated Future-Canary Launcher Contract

**Files:**
- Create: `backend/app/services/phase2_openai_canary_runner.py`
- Create: `backend/tests/test_phase2_openai_canary_runner.py`
- Read: `backend/app/services/phase2_provider_relay_runner.py`

**Interfaces:**
- Produces: `OpenAICanaryRuntimePaths`,
  `build_openai_proxy_create_command(name: str, relay_network: str,
  paths: OpenAICanaryRuntimePaths) -> list[str]`,
  `build_canary_relay_network_command(network: str) -> list[str]`,
  `build_canary_egress_network_command(network: str) -> list[str]`, and
  `validate_openai_proxy_inspect(payload: Any, *,
  paths: OpenAICanaryRuntimePaths, relay_network: str, egress_network: str,
  expected_state: Literal["created", "running", "exited"])
  -> ContainerContractEvidence`.
- Also produces:
  `build_openai_proxy_egress_attach_command(network: str,
  container: str) -> list[str]`.
- Consumes: existing Relay resource limits and image name `tickflow-phase2-openai-egress-proxy:canary-v1`.

- [ ] **Step 1: Write failing command-contract tests**

Assert the exact dedicated Proxy command includes non-root user, read-only
rootfs, all capabilities dropped, no new privileges, fixed resource limits,
no restart, one internal-network attachment, exactly two single-file mounts,
and the dedicated image. Assert it excludes the Secret value, environment
credentials, labels, host ports, privileged mode, devices, Docker socket,
Home/repository/Vault mounts, and arbitrary commands.

```python
def test_openai_proxy_command_has_only_two_mounts(tmp_path: Path) -> None:
    paths = OpenAICanaryRuntimePaths(
        auth=tmp_path / "provider-auth",
        receipt=tmp_path / "proxy-receipt.json",
    )
    command = build_openai_proxy_create_command("phase2-openai-proxy-test", "relay-net", paths)
    mounts = [command[index + 1] for index, item in enumerate(command) if item == "--mount"]
    assert len(mounts) == 2
    assert mounts[0].endswith("dst=/run/phase2/provider-auth,readonly")
    assert mounts[1].endswith("dst=/output/receipt.json")
    assert command[-1] == "tickflow-phase2-openai-egress-proxy:canary-v1"
```

- [ ] **Step 2: Write failing network and inspect tests**

Assert the Relay network command contains `--internal`; the future egress
network command does not contain `--internal`. Require the only attach command
to be exactly:

```python
assert build_openai_proxy_egress_attach_command(
    "phase2-canary-egress", "phase2-openai-proxy-test"
) == [
    "docker",
    "network",
    "connect",
    "phase2-canary-egress",
    "phase2-openai-proxy-test",
]
```

Reject Relay, arbitrary-container, alias, IP, driver-option, or extra-argument
attachment variants. Validate inspect-shaped fixtures for exact image, user, entrypoint,
environment allowlist, two networks, zero published ports/devices, exact
mounts, and all hardening flags. Reject every single-field mutation.

- [ ] **Step 3: Run RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_canary_runner.py -q
```

Expected: import failure for the missing runner module.

- [ ] **Step 4: Implement pure builders and inspect validator**

Reuse fixed constants from `phase2_provider_relay_runner.py`; do not execute
subprocesses. Validate runtime names with the existing name regex. The egress
builder returns only a command array and is not called by the offline preflight.
Expose no `--force`, `--skip-gates`, `--retry`, or execution CLI.

- [ ] **Step 5: Run GREEN and commit Task 4**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_canary_runner.py -q
.venv/bin/ruff check app/services/phase2_openai_canary_runner.py \
  tests/test_phase2_openai_canary_runner.py
cd "$(git rev-parse --show-toplevel)"
git add backend/app/services/phase2_openai_canary_runner.py \
  backend/tests/test_phase2_openai_canary_runner.py
git commit -m "feat: define restricted OpenAI canary launcher"
```

---

### Task 5: Offline Image Build and Artifact Validation

**Files:**
- Create: `docker/phase2-openai-egress-proxy/Dockerfile`
- Create: `backend/app/services/phase2_openai_proxy_artifact.py`
- Create: `backend/scripts/build_phase2_openai_proxy_offline.py`
- Create: `backend/tests/test_phase2_openai_proxy_artifact.py`
- Generate: `reports/phase2_openai_proxy_artifact/approval_candidate.json`
- Generate: `reports/phase2_openai_proxy_artifact/build_evidence.json`

**Interfaces:**
- Produces: `ArtifactInputHashes`, `ImageInspectionEvidence`,
  `ImageContentEvidence`, `OpenAIProxyArtifactCandidate`,
  `build_offline_image_command(repo_root: Path,
  hashes: ArtifactInputHashes) -> list[str]`,
  `inspect_openai_proxy_image(payload: Any, history: Any,
  hashes: ArtifactInputHashes) -> ImageInspectionEvidence`,
  `verify_image_contents(repo_root: Path,
  image_id: str, *, executor: Executor) -> ImageContentEvidence`,
  `build_artifact_candidate(repo_root: Path,
  image: ImageInspectionEvidence,
  contents: ImageContentEvidence) -> OpenAIProxyArtifactCandidate`, and a
  non-network build CLI. `Executor` is a `Protocol` whose `__call__` accepts a
  `Sequence[str]` plus keyword-only `check`, `capture_output`, `text`, `shell`,
  and `timeout`, and returns `subprocess.CompletedProcess[Any]`; every call
  supplies `shell=False`, a bounded timeout, and captured output.
- Consumes: source/contract/policy/launcher bytes from Tasks 1-4 and the exact local distroless base digest.

- [ ] **Step 1: Write failing Dockerfile and build-command tests**

The Dockerfile test requires exact digest-pinned `FROM`, user
`65532:65532`, fixed entrypoint, fixed environment allowlist, and four
non-sensitive labels. It rejects an unpinned base, shell, package manager,
network client installation, Secret value, broad `COPY`, alternate entrypoint,
root user, or build-time network dependency.

The command test requires:

```python
assert command[:3] == ["docker", "build", "--pull=false"]
assert "--network=none" in command
assert command[-1] == str(dedicated_context)
assert dedicated_context == repo_root / "docker/phase2-openai-egress-proxy"
assert "--secret" not in command
assert "--ssh" not in command
```

- [ ] **Step 2: Write failing image identity/content tests**

Use executor doubles for `docker image inspect`, `docker create --network none`,
`docker cp`, and `docker rm -f`. Require exact image ID, base label,
source/contract/policy labels, user, entrypoint, clean environment, and no
sensitive layer history. Require copied `/proxy/proxy.py` and
`/proxy/responses-contract.json` bytes to match the repository exactly.

Test one-byte drift independently for source, Dockerfile, contract, policy,
launcher, image ID, each label, copied source, and copied contract. Test every
failure path invokes container removal and leaves no staging directory.

- [ ] **Step 3: Run RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_proxy_artifact.py -q
```

Expected: missing artifact module and Dockerfile failures.

- [ ] **Step 4: Implement Dockerfile and non-sensitive labels**

Use build arguments only for the four non-sensitive hashes. Require the build
context to contain exactly the five files named in the design, including the
Dockerfile; the Dockerfile uses explicit `COPY` statements for only the four
runtime files (`proxy.py`, contract, and two placeholders), never `COPY .`.
No Secret is accepted as a build argument. Preserve the approved distroless CA
bundle and set only the fixed non-sensitive Python environment.

- [ ] **Step 5: Implement secure local build and evidence functions**

Before building, call `docker image inspect` on the exact digest-qualified base
reference. Require successful inspection and require that exact
`gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5`
value in
`RepoDigests`; do not equate the registry manifest digest with Docker's local
config `.Id`. If the immutable reference is absent or does not match, return
`PHASE2B_CANARY_BUILD_INPUT_BLOCKED`; never pull. Run subprocesses with
`shell=False`, captured output, fixed timeout, and argument arrays.

For image content verification, create one stopped container with
`--network none`, copy only the two approved files into a mode-0700 temporary
directory, compare bytes, and remove the container in `finally`. Do not start
it or attach an egress network.

Publish canonical, path-free JSON atomically. The approval candidate contains
only the exact fields specified in section 11 of the design. The separate
build evidence records status
`PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW`; status is not added to the
candidate hash contract.

- [ ] **Step 6: Run focused GREEN with executor doubles**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_openai_proxy_artifact.py -q
.venv/bin/ruff check app/services/phase2_openai_proxy_artifact.py \
  scripts/build_phase2_openai_proxy_offline.py \
  tests/test_phase2_openai_proxy_artifact.py
```

- [ ] **Step 7: Run the actual offline build only if the exact base is local**

First execute the read-only base check mode:

```bash
cd backend
PYTHONPATH=. .venv/bin/python scripts/build_phase2_openai_proxy_offline.py --check-base
```

If it exits `2`, record `PHASE2B_CANARY_BUILD_INPUT_BLOCKED` and stop the plan;
do not pull. If it exits `0`, execute:

```bash
PYTHONPATH=. .venv/bin/python scripts/build_phase2_openai_proxy_offline.py --build
PYTHONPATH=. .venv/bin/python scripts/build_phase2_openai_proxy_offline.py --verify
```

Expected: local build/verification succeeds, Provider/AI/TickFlow/public
network counters stay zero, and no stopped container remains.

- [ ] **Step 8: Commit Task 5**

```bash
cd "$(git rev-parse --show-toplevel)"
git add docker/phase2-openai-egress-proxy/Dockerfile \
  backend/app/services/phase2_openai_proxy_artifact.py \
  backend/scripts/build_phase2_openai_proxy_offline.py \
  backend/tests/test_phase2_openai_proxy_artifact.py \
  reports/phase2_openai_proxy_artifact/approval_candidate.json \
  reports/phase2_openai_proxy_artifact/build_evidence.json
git commit -m "feat: build and verify dedicated OpenAI proxy offline"
```

---

### Task 6: Secure Artifact Approval and Canary Preflight Integration

**Files:**
- Modify: `backend/scripts/validate_phase2_canary_provider_config.py:102-104,516-644,694-833,1165-1500`
- Modify: `backend/tests/test_phase2_canary_provider_config.py`
- Read: `backend/app/services/phase2_openai_proxy_artifact.py`
- Read: `backend/app/services/phase2_openai_canary_runner.py`
- Local only: `$HOME/Library/Application Support/TickFlowPhase2Canary/proxy-artifact-approval.json`

**Interfaces:**
- Produces: `ProxyArtifactApproval`,
  `read_proxy_artifact_approval(path, current_uid)`, dedicated
  artifact/image/launcher gate fields, and final
  `CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL` result.
- Consumes: Task 5 approval candidate and existing Provider/Keychain/Facts/evidence gates.

- [ ] **Step 1: Write failing secure approval-file tests**

Mirror the existing Provider approval file checks: bounded no-follow descriptor
read, regular file, directory `0700`, file `0600`, current owner, all parent
directories non-symlink, finite strict JSON, exact fields, and no sensitive
shape. Reject missing, malformed, symlinked, wrong mode/owner, extra/missing
field, alternate image, and every one-byte hash mutation.

The strict approval object is exactly the Task 5 candidate plus one field:

```python
class ProxyArtifactApproval(OpenAIProxyArtifactCandidate):
    approval_status: Literal["APPROVED"]
```

Neither model may accept local paths, credentials, Secret metadata, or an
arbitrary status string.

- [ ] **Step 2: Write failing dedicated preflight tests**

Use runner doubles and the approved dedicated artifact fixture. Require:

```python
assert result.status == "CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL"
assert result.provider == "openai"
assert result.exact_model == "gpt-5.6-terra"
assert result.keychain_secret == "PRESENT"
assert result.secret_content_read is False
assert result.secret_hash_recorded is False
assert result.temporary_single_file_injection == "PASSED"
assert result.strict_json_schema == "READY"
assert result.tls == "READY"
assert result.tls_evidence == "OFFLINE_ARTIFACT_VERIFIED"
assert result.redirect == "DISABLED"
assert result.egress_allowlist == "PASSED"
assert result.egress_evidence == "OFFLINE_APPLICATION_POLICY_VERIFIED"
assert result.provider_attempt_count == 0
assert result.ai_call_count == 0
assert result.provider_http == "NOT_RUN"
assert result.tickflow_api_request_count == 0
assert result.real_public_network_success_count == 0
```

Patch `socket.socket` to raise. Assert the Keychain runner rejects `-w` and
discards stdout/stderr. Assert the Docker runner receives only `image inspect`,
`history`, one never-started `create --network none`, bounded `cp`, cleanup
`rm -f`, and residue `ps`/`network ls` commands; it must not receive `start`,
`run`, `network create`, `network connect`, `pull`, or `login`.

- [ ] **Step 3: Write all fail-closed mutation tests**

Cover absent artifact approval, source/Dockerfile/contract/policy/launcher/image
drift, wrong/missing labels, image-content mismatch, invalid command contract,
runtime residue, placeholder exposure/residue, evidence mutation, Keychain
missing, config mutation, Git drift, and probe exceptions containing private
paths. Each returns one stable blocked category with no exception text or path.
An otherwise valid, independently reviewed candidate fixture with no local
approval must return exactly
`PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED`, preserve all action counters at
zero, and stop before any runtime create command.

- [ ] **Step 4: Run RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_canary_provider_config.py -q
```

Expected: new dedicated artifact and READY tests fail.

- [ ] **Step 5: Replace self-pinning constants with external artifact approval**

Remove `_APPROVED_RUNTIME_PROXY_SHA256` and
`_APPROVED_RUNTIME_PROXY_IMAGE_DIGEST`. The validator reads the non-sensitive
local approval object and compares it to Task 5 evidence. It inspects
`docker/phase2-openai-egress-proxy/proxy.py`, not the Mock Proxy. A label alone
cannot approve the image; source, Dockerfile, contract, policy, launcher,
image ID, labels, and copied image contents must all agree.

At the same time, bind the Git gate to design baseline
`165dc46ec9c10806ddcc4ff5d2171bc24ec7a87e` and replace the old
additions-only set with this exact path-to-status map:

```python
_ALLOWED_PREFLIGHT_DELTA = {
    "backend/app/services/phase2_openai_proxy_contract.py": "A",
    "backend/app/services/phase2_openai_proxy_artifact.py": "A",
    "backend/app/services/phase2_openai_canary_runner.py": "A",
    "backend/scripts/build_phase2_openai_proxy_offline.py": "A",
    "backend/scripts/validate_phase2_canary_provider_config.py": "M",
    "backend/tests/test_phase2_openai_proxy_contract.py": "A",
    "backend/tests/test_phase2_openai_proxy_artifact.py": "A",
    "backend/tests/test_phase2_openai_canary_runner.py": "A",
    "backend/tests/test_phase2_canary_provider_config.py": "M",
    "docker/phase2-openai-egress-proxy/Dockerfile": "A",
    "docker/phase2-openai-egress-proxy/proxy.py": "A",
    "docker/phase2-openai-egress-proxy/responses-contract.json": "A",
    "docker/phase2-openai-egress-proxy/secret.placeholder": "A",
    "docker/phase2-openai-egress-proxy/receipt.placeholder.json": "A",
    "docs/superpowers/plans/2026-08-02-tickflow-phase2b3b-openai-proxy.md": "A",
    "reports/phase2_openai_proxy_artifact/approval_candidate.json": "A",
    "reports/phase2_openai_proxy_artifact/build_evidence.json": "A",
    "reports/tickflow_phase2b_canary_provider_preflight_eval.md": "M",
}
```

Test every allowed pair plus an extra path, deletion, rename, and wrong status.
The gate must still require the approved branch, baseline ancestry, and a clean
worktree.

- [ ] **Step 6: Bind placeholder injection to the dedicated launcher**

Exercise `build_openai_proxy_create_command` with a runtime-generated synthetic
value in one mode-0600 file. Assert the command has a read-only auth mount and
the Relay has none. Scan commands, inspect-shaped metadata, Projection,
contract, Candidate, receipt, stdout/stderr, reports, and worktree for the exact
value; remove the file/directory in `finally`.

- [ ] **Step 7: Implement offline-only READY semantics**

Set `TLS="READY"` and `egress_allowlist="PASSED"` only after the exact
approved dedicated artifact passes. Add sanitized evidence fields
`tls_evidence="OFFLINE_ARTIFACT_VERIFIED"` and
`egress_evidence="OFFLINE_APPLICATION_POLICY_VERIFIED"`. Preserve
`provider_http="NOT_RUN"` and all zero counters. The CLI exit code is `0` only
for `CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL`.

- [ ] **Step 8: Run GREEN and commit Task 6**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_canary_provider_config.py -q
.venv/bin/ruff check scripts/validate_phase2_canary_provider_config.py \
  tests/test_phase2_canary_provider_config.py
cd "$(git rev-parse --show-toplevel)"
git add backend/scripts/validate_phase2_canary_provider_config.py \
  backend/tests/test_phase2_canary_provider_config.py
git commit -m "feat: verify approved OpenAI proxy in canary preflight"
```

---

### Task 7: Full Verification and Artifact Approval Stop Point

**Files:**
- Update: `reports/tickflow_phase2b_canary_provider_preflight_eval.md`
- Update: `reports/phase2_openai_proxy_artifact/build_evidence.json`
- Verify: `reports/phase2_openai_proxy_artifact/approval_candidate.json`
- Verify: all protected evidence paths enumerated by the preflight validator

**Interfaces:**
- Produces: independently reviewed implementation evidence and exact hashes for user approval.
- Consumes: Tasks 1-6 committed state; does not install the approval file.

- [ ] **Step 1: Run focused suites**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_openai_proxy_contract.py \
  tests/test_phase2_openai_canary_runner.py \
  tests/test_phase2_openai_proxy_artifact.py \
  tests/test_phase2_canary_provider_config.py -q
```

- [ ] **Step 2: Run full backend/static verification**

```bash
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python -m compileall -q app scripts \
  ../docker/phase2-openai-egress-proxy
.venv/bin/ruff check app scripts --select F821
.venv/bin/ruff check \
  app/services/phase2_openai_proxy_contract.py \
  app/services/phase2_openai_proxy_artifact.py \
  app/services/phase2_openai_canary_runner.py \
  scripts/build_phase2_openai_proxy_offline.py \
  scripts/validate_phase2_canary_provider_config.py \
  tests/test_phase2_openai_proxy_contract.py \
  tests/test_phase2_openai_canary_runner.py \
  tests/test_phase2_openai_proxy_artifact.py \
  tests/test_phase2_canary_provider_config.py \
  ../docker/phase2-openai-egress-proxy/proxy.py
```

- [ ] **Step 3: Run offline invariance, sensitive, and residue checks**

Run
`python -m app.services.phase2_openai_proxy_contract --check` and the image
builder in `--verify`. Confirm the build evidence is still
`PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW`; do not run the final preflight or
claim approval readiness before the independent review in Step 4.

Scan only changed artifacts for real-secret shapes and the exact synthetic
test value. Known synthetic security fixtures elsewhere in the repository must
be classified rather than mislabeled as real leakage. Verify all nine protected
hash sets and request audits remain unchanged. Verify Docker container/network
and temporary file residue counts are zero.

From the repository root, run this high-confidence real-secret scan; the
focused suites separately assert zero hits for their runtime-generated exact
synthetic value:

```bash
if rg -n \
  'sk-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9_-]{20,}|OPENAI_API_KEY\s*[:=]\s*[^[:space:]]{16,}' \
  backend/app/services/phase2_openai_proxy_contract.py \
  backend/app/services/phase2_openai_proxy_artifact.py \
  backend/app/services/phase2_openai_canary_runner.py \
  backend/scripts/build_phase2_openai_proxy_offline.py \
  backend/scripts/validate_phase2_canary_provider_config.py \
  backend/tests/test_phase2_openai_proxy_contract.py \
  backend/tests/test_phase2_openai_proxy_artifact.py \
  backend/tests/test_phase2_openai_canary_runner.py \
  backend/tests/test_phase2_canary_provider_config.py \
  docker/phase2-openai-egress-proxy \
  reports/phase2_openai_proxy_artifact \
  reports/tickflow_phase2b_canary_provider_preflight_eval.md; then
  exit 1
fi
```

- [ ] **Step 4: Perform independent code review and fix every finding**

The review scope is exact endpoint enforcement, TLS context, redirect and retry
closure, response parser, schema validator, Secret lifecycle, image/source
binding, Docker command restrictions, external approval trust, exception
sanitization, and false-READY paths. Re-run every affected focused suite after
fixes and rerun full pytest if production code changes. If any covered source,
Dockerfile, contract, policy, launcher, label, or image byte changes, rebuild
offline and regenerate both artifact evidence files before presenting hashes.
Use `superpowers:requesting-code-review` for this gate; the reviewer reports
findings, while the implementing worker applies any required fixes.

- [ ] **Step 5: Write sanitized approval-required report**

After all review findings and reruns are closed, atomically update
`build_evidence.json` from
`PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW` to
`PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED`, adding only
`independent_review="PASSED"` and the committed candidate SHA-256. Do not
change `approval_candidate.json` at this transition.

Now run the preflight without an installed artifact approval. It must stop at
`PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED`, keep all external counters at zero,
and execute no runtime create command.

Record explicit source, Dockerfile, contract, policy, launcher, base, and image
hashes from `approval_candidate.json`; record no local paths or Secret metadata
beyond presence. State:

```text
status=PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED
provider_attempt_count=0
ai_call_count=0
provider_http=NOT_RUN
tickflow_api_request_count=0
real_public_network_success_count=0
next_action=APPROVE_EXACT_PROXY_ARTIFACT_HASHES
```

- [ ] **Step 6: Commit evidence and stop for explicit approval**

```bash
cd "$(git rev-parse --show-toplevel)"
git add reports/phase2_openai_proxy_artifact \
  reports/tickflow_phase2b_canary_provider_preflight_eval.md
git commit -m "chore: record OpenAI proxy artifact for approval"
```

Do not install `proxy-artifact-approval.json` and do not continue Task 8 until
the user explicitly approves the exact candidate hashes.

---

### Task 8: Install Approved Artifact and Reach Final Offline READY

**Files:**
- Local only: `$HOME/Library/Application Support/TickFlowPhase2Canary/proxy-artifact-approval.json`
- Update: `reports/tickflow_phase2b_canary_provider_preflight_eval.md`
- Update: `docs/superpowers/plans/2026-08-02-tickflow-phase2b3b-openai-proxy.md`

**Interfaces:**
- Produces: final `CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL` evidence and pushed branch.
- Consumes: user's exact approval of Task 7 hashes.

- [ ] **Step 1: Confirm approval matches the current candidate exactly**

Compare the user-approved image ID and all SHA-256 values to the committed
candidate. Require the commit that last changed the candidate to remain an
ancestor of `HEAD`, and require every implementation/artifact byte covered by
the candidate to remain unchanged. If any approved value or covered byte
differs, return
`PHASE2B_PROXY_ARTIFACT_APPROVAL_MISMATCH` and stop.

- [ ] **Step 2: Install the non-sensitive approval file securely**

Write only the committed candidate object plus
`approval_status="APPROVED"` through a mode-0600 temporary regular file in the
existing mode-0700 directory, `fsync` file and directory, and atomically
rename. Reject symlink parents. Do not read or modify the Provider approval
file or Keychain item.

- [ ] **Step 3: Execute final offline preflight once**

```bash
cd "$(git rev-parse --show-toplevel)/backend"
PYTHONPATH=. .venv/bin/python scripts/validate_phase2_canary_provider_config.py
```

Expected exit `0` and exact status:

```text
CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL
```

The output must show Provider/model/endpoint valid, Keychain `PRESENT`, Secret
content/hash `NO`, placeholder injection passed, strict schema ready, TLS ready
offline, redirects disabled, egress policy passed offline, all protected
evidence unchanged, zero residue, zero Provider/AI/TickFlow/public-network
actions, and `provider_http=NOT_RUN`.

- [ ] **Step 4: Re-run final focused/static checks**

```bash
cd "$(git rev-parse --show-toplevel)/backend"
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_openai_proxy_contract.py \
  tests/test_phase2_openai_canary_runner.py \
  tests/test_phase2_openai_proxy_artifact.py \
  tests/test_phase2_canary_provider_config.py -q
PYTHONPATH=. .venv/bin/python -m compileall -q app scripts \
  ../docker/phase2-openai-egress-proxy
.venv/bin/ruff check app scripts --select F821
cd "$(git rev-parse --show-toplevel)"
git diff --check
```

Expected: all checks pass. The Step 3 preflight has already rerun the exact
synthetic-value scan, approval-file sensitive-shape scan, worktree scan, and
residue scan after installing the non-sensitive approval file.

- [ ] **Step 5: Update final report and plan state**

The report must use the user-required public summary:

```text
Provider=openai
Exact Model=gpt-5.6-terra
Endpoint Alias=openai_responses_v1
Provider config=VALID
Keychain Secret=PRESENT
Secret content read=NO
Temporary single-file injection=PASSED
Strict JSON Schema=READY
TLS=READY
Redirect=DISABLED
Egress allowlist=PASSED
Provider attempts=0
AI calls=0
Provider HTTP=NOT_RUN
Next action=REQUEST_FINAL_SINGLE_CALL_APPROVAL
```

The detailed report must state that TLS and egress readiness are offline
artifact evidence, not a live handshake or connection.

- [ ] **Step 6: Commit, push, and stop**

```bash
cd "$(git rev-parse --show-toplevel)"
git add reports/tickflow_phase2b_canary_provider_preflight_eval.md \
  docs/superpowers/plans/2026-08-02-tickflow-phase2b3b-openai-proxy.md
git commit -m "chore: close OpenAI canary offline preflight"
git push fork codex/tickflow-phase2-ai-review
```

Verify the remote Head equals local Head and the worktree is clean. Do not
create a PR, merge, deploy, construct a real authorization header, start the
future runtime, or execute a Provider request. Stop at
`CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL`.
