# TickFlow Phase 2B-3B Canary Provider Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, without reading the real credential or making a network request, which approved OpenAI single-symbol Canary gates are ready and fail closed unless the exact runtime Proxy artifact has been separately reviewed and pinned.

**Architecture:** A frozen Pydantic approval model encodes the only accepted provider identity and network contract. A network-free validator reads the approval file through no-follow file descriptors, checks only Keychain entry existence, exercises a synthetic credential through an ephemeral `0600` file, derives a strict Responses JSON Schema from `WorkerClaimsCandidate`, and revalidates the fixed Facts/Projection/Relay evidence. It also inspects the exact runtime Proxy source and requires structural policy conformance, a separately approved source SHA-256, an approved local image ID, and a matching source-hash image label. The CLI emits sanitized status only and has no HTTP client or provider transport.

**Tech Stack:** Python 3.11+, Pydantic v2, macOS `security` CLI existence check, standard-library filesystem and subprocess APIs, pytest, canonical JSON, SHA-256.

## Global Constraints

- Preflight base Head is `b5c7e7bf3833f63c706535da9e1be818ddb2ec4f`.
- Fixed symbol is `000403.SZ`; fixed trade date is `2026-07-31`.
- Provider is exactly `openai`; model is exactly `gpt-5.6-terra`.
- Endpoint is exactly `POST https://api.openai.com:443/v1/responses`.
- TLS verification is enabled; redirects, streaming, tools, web, files, retries, publishing, and alternate attempts are disabled.
- The real Keychain value must never be read, printed, hashed, exported, or injected in this phase.
- Provider attempts, AI calls, public network connections, and TickFlow API requests remain zero.
- No PR, merge, cloud deployment, Obsidian write, Paper Trading, Telegram, OpenClaw, or Integrated Gold action is allowed.
- Every behavior change follows RED -> GREEN -> refactor.
- The existing `docker/phase2-egress-proxy/proxy.py` is Mock-only. No real Proxy hash is approved in this task, so the terminal safe state is `PHASE2B_CANARY_EGRESS_BLOCKED`.

---

### Task 1: Closed Approval and Responses Schemas

**Files:**
- Create: `backend/app/schemas/phase2_canary_provider_config.py`
- Create: `backend/tests/test_phase2_canary_provider_config.py`

**Interfaces:**
- Produces: `CanaryProviderApproval`, `CanaryEgressPolicy`, `build_responses_request_contract(...)`, `validate_exact_egress(...)`, and stable approved constants.
- Consumes: `WorkerClaimsCandidate.model_json_schema(mode="validation")` as the only Candidate schema source.

- [x] Write tests that accept only the exact approved fields and reject missing, extra, relaxed, alternate-provider, alternate-model, alternate-endpoint, retry, attempt, symbol, TLS, redirect, tool, web, streaming, and publish mutations.
- [x] Run `PYTHONPATH=. .venv/bin/pytest tests/test_phase2_canary_provider_config.py -q` and require collection failure because the schema module does not exist.
- [x] Implement frozen strict approval and egress models with `Literal` values and `extra="forbid"`.
- [x] Add a deterministic schema normalizer that preserves the typed Candidate contract, requires every object property, rejects free-form object properties, replaces discriminated `oneOf` with compatible `anyOf`, and removes provider-unsupported annotation keys without changing the Host schema.
- [x] Build the in-memory Responses request contract with model, `stream=false`, empty tools, and `text.format={type: json_schema, name: tickflow_phase2_claims_candidate, strict: true, schema: ...}`.
- [x] Re-run the focused tests and require GREEN.

### Task 2: Secure Local Configuration and Keychain Existence Gate

**Files:**
- Create: `backend/scripts/validate_phase2_canary_provider_config.py`
- Modify: `backend/tests/test_phase2_canary_provider_config.py`

**Interfaces:**
- Produces: `read_provider_approval(path, current_uid)`, `keychain_secret_exists(...)`, sanitized `CanaryPreflightResult`, and CLI `main(...)`.
- Consumes: the exact approval model from Task 1.

- [x] Write tests for missing files, file/parent symlinks, non-regular files, wrong owner, directory mode other than `0700`, file mode other than `0600`, malformed JSON, unknown fields, and all prohibited credential shapes.
- [x] Write a runner double that accepts only `security find-generic-password -a <account> -s tickflow-phase2-canary-openai`, fails if `-w` appears, and returns presence as a boolean without output.
- [x] Run the focused tests and require failures caused by missing reader and Keychain gate functions.
- [x] Implement no-follow descriptor reads with bounded size, inode recheck, exact owner/mode checks, sanitized stable error codes, and a Keychain existence-only subprocess invocation with stdout/stderr discarded.
- [x] Re-run the focused tests and require GREEN.

### Task 3: Placeholder Secret Boundary and Offline Evidence Gate

**Files:**
- Modify: `backend/scripts/validate_phase2_canary_provider_config.py`
- Modify: `backend/tests/test_phase2_canary_provider_config.py`

**Interfaces:**
- Produces: `exercise_placeholder_secret_injection(...)`, `validate_offline_canary_inputs(...)`, protected-evidence digests, and zero-request counters.
- Consumes: `build_worker_projection`, `validate_facts_document`, `validate_provider_relay_artifacts`, the exact Responses contract, and current repository evidence.

- [x] Write tests that require an ephemeral `0700` directory and `0600` regular Secret file, Proxy read-only single-file mount metadata, no Relay Secret mount, zero value/hash exposure across commands, inspect-shaped data, Projection, request, Candidate, receipt, stdout, and stderr, and zero residue after cleanup.
- [x] Write tests for strict allowlist acceptance and rejection of wildcard hosts, alternate paths/ports/methods, HTTP, redirects, host/Docker targets, public IPs, and returned URLs.
- [x] Write tests that revalidate `000403.SZ`, trade date `2026-07-31`, deterministic Projection bytes/hash, Provider Relay READY, empty Canary output, zero runtime residue, and all protected evidence digests.
- [x] Run the focused tests and require RED for missing injection and offline evidence functions.
- [x] Implement the minimum filesystem-only mechanism and repository checks. Do not import an HTTP client, open sockets, invoke Docker mutation commands, or read Keychain values.
- [x] Bind the Git gate to the frozen base, exact branch, clean worktree, all untracked files, and an added-file allowlist.
- [x] Inspect the actual runtime Proxy and require independently approved source and image hashes plus a matching source-hash image label in addition to structural HTTPS policy checks.
- [x] Return only stable error codes when local probes fail; do not serialize exception details or local paths.
- [x] Re-run the focused tests and require GREEN.

### Task 4: Verification, Sanitized Evidence, and Branch Archive

**Files:**
- Generate: `reports/tickflow_phase2b_canary_provider_preflight_eval.md`
- Modify: `docs/superpowers/plans/2026-08-01-tickflow-phase2b3b-canary-provider-preflight.md`

**Interfaces:**
- Consumes: sanitized validator result, test outputs, repository status, protected hashes, and residue scans.
- Produces: a sanitized ready or blocked preflight result; it never executes the Canary. With the current Mock-only runtime Proxy and no approved real-adapter hash, the expected result is `PHASE2B_CANARY_EGRESS_BLOCKED`.

- [x] Run focused pytest, backend full pytest, `compileall app scripts`, and Ruff F821.
- [x] Execute the validator against the local approval file and Keychain existence gate; print no configuration body and no credential metadata beyond presence.
- [x] Recompute protected evidence hashes, verify no sensitive shape or placeholder residue, and confirm Provider/AI/TickFlow/public-network counters remain zero.
- [x] Write a sanitized report containing only approved public identity, boolean gates, stable hashes, zero counters, limitations, and the next action `IMPLEMENT_AND_AUDIT_REAL_OPENAI_HTTPS_PROXY_OFFLINE` when the actual Proxy remains Mock-only.
- [x] Run `git diff --check`, inspect the exact diff, commit only code/tests/plan/report, and push `codex/tickflow-phase2-ai-review` to `fork`.
- [x] Stop before any real Provider request.
