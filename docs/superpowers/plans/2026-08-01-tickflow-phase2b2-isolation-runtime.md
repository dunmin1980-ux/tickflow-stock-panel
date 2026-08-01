# TickFlow Phase 2B-2 AI Worker Runtime Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove with a real Docker runtime that one Fake Provider Worker is non-root, networkless, read-only, mount-minimal, shell-less, and unable to read or write outside its two approved files, then revalidate its typed Claims entirely on the host.

**Architecture:** A dedicated digest-pinned distroless Python image contains only a standard-library Fake Provider. The host creates one immutable projection and one empty output file, creates two constrained containers (candidate and probe), validates their actual Docker inspect state, revalidates the candidate through the Phase 2B-1 protocol and renderer, and persists only sanitized hashes and booleans.

**Tech Stack:** Python 3.12 host code, Python standard library worker, Docker Desktop, distroless Python, Pydantic host validation, pytest, canonical JSON, SHA-256.

## Global Constraints

- Fixed symbol: `000403.SZ` only; do not execute a real three-symbol AI batch.
- Runtime user: `65532:65532`; root is forbidden.
- Required runtime flags: `network=none`, read-only rootfs, `cap-drop=ALL`, `no-new-privileges`, `pids-limit=16`, `memory=128m`, `cpus=0.5`, `ipc=none`, restart disabled.
- Runtime mounts: exactly `/input/projection.json` read-only and `/output/candidate.json` read-write, both single files.
- No Home, repository, Obsidian, `.ssh`, `.config`, Docker socket, device, cloud, or secret mount.
- No real AI, provider, TickFlow, Telegram, OpenClaw, Gold, Paper Trading, cloud deployment, or Vault write.
- No automatic retry, alternate image fallback, or relaxed security flag.
- A mock or Python path guard cannot establish `PHASE2B_ISOLATION_RUNTIME_VERIFIED`.
- Preserve all Phase 1 evidence, request audits, Phase 2 Facts, provider audit, historical AI samples, and historical rejected artifacts byte-for-byte.

---

### Task 1: Dedicated Fake Provider Worker Image

**Files:**
- Create: `docker/phase2-ai-worker/worker.py`
- Create: `docker/phase2-ai-worker/projection.placeholder.json`
- Create: `docker/phase2-ai-worker/candidate.placeholder.json`
- Test: `backend/tests/test_phase2_isolation_runtime.py`

**Interfaces:**
- Consumes: Phase 2B-1 projection JSON shape and the fixed predicate order.
- Produces: `generate_candidate(projection: Mapping[str, Any]) -> dict[str, Any]`, `run_probe() -> dict[str, Any]`, and a shell-less image whose entrypoint accepts only `candidate` or `probe`.

- [x] **Step 1: Write failing worker contract tests**

Import `worker.py` by exact path and assert:

```python
def test_fake_provider_matches_host_candidate_for_fixed_projection():
    projection = build_test_projection("000403.SZ")
    candidate = worker.generate_candidate(projection)
    assert validate_worker_candidate(candidate, projection).status == WORKER_CANDIDATE_VALID
    assert len(candidate["claims"]) == 42

def test_worker_is_stdlib_only_and_paths_are_fixed():
    source = WORKER_PATH.read_text(encoding="utf-8")
    assert "/input/projection.json" in source
    assert "/output/candidate.json" in source
    assert not {"requests", "httpx", "openai", "anthropic"} & imported_roots(source)
```

Also assert deterministic output, exact root fields, zero freeform fields, no trading types, candidate/probe mode allowlist, and a one-megabyte input/output limit.

- [x] **Step 2: Run worker tests and require RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_isolation_runtime.py -q
```

Expected: collection or import failure because the worker and runtime module do not exist.

- [x] **Step 3: Implement the standard-library Fake Provider**

Implement a data-driven generator with fixed claim IDs and predicates. The worker must:

```python
INPUT_PATH = Path("/input/projection.json")
OUTPUT_PATH = Path("/output/candidate.json")
MAX_BYTES = 1_048_576
ALLOWED_MODES = {"candidate", "probe"}
```

`candidate` mode strictly parses the projection, verifies `projection_sha256`, produces all 42 Claims, writes canonical JSON only to `OUTPUT_PATH`, fsyncs it, and reports nothing sensitive to stdout/stderr.

`probe` mode performs real OS operations and records only booleans/errno categories for:

- external TCP connect;
- `/bin/sh`, `/bin/bash`, and BusyBox execution;
- forbidden host-path candidates;
- input-file overwrite;
- rootfs, worker source, `/tmp`, `/etc`, and extra `/output` writes.

- [x] **Step 4: Create the fixed mount placeholders**

Write `{}` plus a newline to `projection.placeholder.json` and a single newline
to `candidate.placeholder.json`. Tests must require both files to be regular,
smaller than 128 bytes, and free of sensitive patterns.

- [x] **Step 5: Run worker tests and require GREEN**

Run the Task 1 focused tests. Require candidate validation for the fixed projection, byte idempotency, zero network-library imports, and Dockerfile static-policy success.

- [x] **Step 6: Commit Task 1**

```bash
git add docker/phase2-ai-worker/worker.py \
  docker/phase2-ai-worker/projection.placeholder.json \
  docker/phase2-ai-worker/candidate.placeholder.json \
  backend/tests/test_phase2_isolation_runtime.py
git commit -m "feat: add isolated phase2 fake worker image"
```

---

### Task 2: Host Runtime Contract and Claims Adapter

**Files:**
- Create: `backend/app/services/phase2_isolation_runtime.py`
- Modify: `backend/tests/test_phase2_isolation_runtime.py`

**Interfaces:**
- Consumes: image reference, exact temporary projection/output paths, Docker inspect JSON, candidate bytes, and the Phase 2B-1 projection.
- Produces: `build_container_create_command(...) -> list[str]`, `validate_container_inspect(...) -> RuntimeContractEvidence`, `candidate_to_claims_document(...) -> ClaimsDocument`, and `validate_isolated_candidate(...) -> CandidateHostEvidence`.

- [x] **Step 1: Write failing command and inspect tests**

Assert the command contains every required flag exactly once, has exactly two `--mount` values, and contains none of:

```text
--privileged
--network host
/Users
/home
.ssh
.config
.obsidian
docker.sock
--env-file
```

Create fixture inspect payloads and independently mutate each required property. Each mutation must produce a stable error code such as:

```text
runtime_user_invalid
runtime_network_not_none
runtime_rootfs_not_readonly
runtime_cap_drop_invalid
runtime_mount_set_invalid
runtime_projection_mount_not_readonly
runtime_output_mount_not_writable
```

- [x] **Step 2: Write failing host adapter tests**

```python
def test_host_adapter_revalidates_candidate_and_renderer():
    projection = build_test_projection("000403.SZ")
    candidate = worker.generate_candidate(projection)
    evidence = validate_isolated_candidate(REPO_ROOT, candidate, projection)
    assert evidence.worker_status == "WORKER_CANDIDATE_VALID"
    assert evidence.claims_status == "CLAIMS_VALID"
    assert evidence.renderer_status == "RENDERED_VALID"
    assert evidence.claim_count == 42
```

Tamper Facts SHA, pointer, operand label, raw/qfq basis, value, claim ID, freeform key, and trading type. Every mutation must fail before rendering.

- [x] **Step 3: Run focused tests and require RED**

Run only the new command/inspect/adapter tests and confirm missing interfaces cause failure.

- [x] **Step 4: Implement immutable command construction and inspect validation**

Use argument lists only; never `shell=True`. Normalize expected host paths before command construction, reject symlinks/non-regular files, and return sanitized evidence without mount sources, environment values, container IDs, or command strings.

The inspect validator must check actual Docker fields, including:

```python
EXPECTED_MEMORY = 128 * 1024 * 1024
EXPECTED_NANO_CPUS = 500_000_000
EXPECTED_PIDS_LIMIT = 16
EXPECTED_MOUNTS = {
    "/input/projection.json": False,
    "/output/candidate.json": True,
}
```

- [x] **Step 5: Implement the Host Claims adapter**

Parse `WorkerClaimsCandidate`, bind it to the committed Facts file and SHA, construct a strict `ClaimsDocument`, run `validate_worker_candidate`, `validate_claims_document`, and `validate_rendered_document`, and compute only candidate/rendered SHA-256 for evidence.

- [x] **Step 6: Run Task 2 tests and require GREEN**

Require every mutated inspect field and candidate field to fail closed while the fixed candidate passes all Host validators.

- [x] **Step 7: Commit Task 2**

```bash
git add backend/app/services/phase2_isolation_runtime.py \
  backend/tests/test_phase2_isolation_runtime.py
git commit -m "feat: add phase2 isolation runtime contract"
```

---

### Task 3: Docker Orchestrator and Sanitized Evidence

**Files:**
- Modify: `backend/app/services/phase2_isolation_runtime.py`
- Create: `backend/scripts/validate_phase2_isolation_runtime.py`
- Modify: `backend/tests/test_phase2_isolation_runtime.py`

**Interfaces:**
- Consumes: repository root, reports root, pinned image context, Docker CLI responses.
- Produces: `run_runtime_validation(repo_root: Path, reports_root: Path) -> RuntimeValidationResult` and a CLI that exits 0 only for `PHASE2B_ISOLATION_RUNTIME_VERIFIED`.

- [x] **Step 1: Write failing orchestration tests with a fake Docker executor**

Cover this exact lifecycle:

```text
docker version
docker image inspect
docker create
docker inspect
docker start --attach
docker inspect
docker rm -f
```

Run it once for `candidate` and once for `probe`. Assert no retries, cleanup in `finally`, a cleanup failure blocks success, and candidate/projection temp files are removed even on validation failure.

- [x] **Step 2: Write failing evidence-schema tests**

The evidence JSON must include only fixed status, image digests, contract booleans, probe booleans, Host validator counts/hashes, zero external counters, and cleanup status. Recursively reject keys or values containing:

```text
source
container_id
command
environment
Authorization
Cookie
Session
api_key
/Users/
docker.sock
```

- [x] **Step 3: Run orchestration tests and require RED**

Confirm the missing orchestrator and evidence types are the reason for failure.

- [x] **Step 4: Implement the no-retry Docker lifecycle**

Use `subprocess.run(..., shell=False, check=False, capture_output=True, text=True)` through a small injected executor. Never print raw stderr/stdout into reports. Hash unexpected output before returning a sanitized error code.

Create temporary files with no symlinks, set the projection read-only, make only the empty output file writable to UID 65532, and rehash the projection before and after each container.

- [x] **Step 5: Implement atomic evidence publication**

Write a staging directory under `reports`, fsync files, and atomically publish:

```text
reports/phase2_isolation_runtime/runtime_evidence.json
```

The result must record:

```text
tickflow_api_request_count=0
ai_call_count=0
provider_attempt_count=0
external_send_count=0
cloud_mutation_count=0
obsidian_real_vault_write=false
paper_trading_started=false
integrated_gold_enabled=false
```

- [x] **Step 6: Implement the CLI and run Task 3 tests**

The CLI takes only `--repo-root` and `--reports-root`. It must not accept `--force`, credentials, alternate mounts, alternate network modes, or image overrides.

- [x] **Step 7: Commit Task 3**

```bash
git add backend/app/services/phase2_isolation_runtime.py \
  backend/scripts/validate_phase2_isolation_runtime.py \
  backend/tests/test_phase2_isolation_runtime.py
git commit -m "feat: orchestrate phase2 isolation validation"
```

---

### Task 4: Real Docker Runtime Proof

**Files:**
- Create: `docker/phase2-ai-worker/Dockerfile`
- Modify: `backend/tests/test_phase2_isolation_runtime.py`
- Generate: `reports/phase2_isolation_runtime/runtime_evidence.json`

**Interfaces:**
- Consumes: a running Docker Desktop daemon and the committed worker build context.
- Produces: actual candidate/probe container evidence; no mock result may satisfy this task.

- [x] **Step 1: Start Docker Desktop and verify the daemon**

```bash
open -a Docker
docker version
```

Wait with bounded polling. If the daemon does not become ready, report the blocker; do not install another runtime or change Docker settings.

- [x] **Step 2: Resolve the distroless digest and write the Dockerfile**

Pull only `gcr.io/distroless/python3-debian12:nonroot`, then run:

```bash
docker image inspect \
  gcr.io/distroless/python3-debian12:nonroot \
  --format '{{index .RepoDigests 0}}'
```

Copy the returned complete `gcr.io/distroless/python3-debian12@sha256:...`
reference verbatim into `FROM`. Add three `COPY --chown=65532:65532`
instructions for the worker and placeholders, followed by:

```dockerfile
USER 65532:65532
WORKDIR /worker
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
ENTRYPOINT ["/usr/bin/python3", "/worker/worker.py"]
CMD ["candidate"]
```

Add and run a static policy test that rejects `:debug`, tag-only `FROM`,
shell installation, package-manager commands, `RUN`, `ADD`, secrets, and URLs
outside the pinned `FROM` line.

- [x] **Step 3: Build the dedicated image**

```bash
docker build --network none --pull=false \
  --tag tickflow-phase2-fake-worker:runtime-v1 \
  docker/phase2-ai-worker
```

Inspect the built image. Require `User=65532:65532`, the fixed entrypoint, no secret env, and a content-addressed image ID.

- [x] **Step 4: Run the real validation CLI once**

```bash
cd backend
PYTHONPATH=. .venv/bin/python \
  scripts/validate_phase2_isolation_runtime.py \
  --repo-root .. \
  --reports-root ../reports
```

No automatic retry is allowed. Require one candidate container and one probe container, exact inspect contracts, all OS-negative probes blocked, all Host validators valid, and cleanup complete.

- [x] **Step 5: Inspect residual runtime state**

Verify no validation container remains and the report tree has no staging, temporary, candidate, projection, raw inspect, or command-log files. The dedicated local image may remain cached.

- [x] **Step 6: Commit the pinned digest and evidence**

```bash
git add docker/phase2-ai-worker/Dockerfile \
  reports/phase2_isolation_runtime/runtime_evidence.json
git commit -m "test: verify phase2 worker runtime isolation"
```

---

### Task 5: Regression, Invariance, and Final Report

**Files:**
- Create: `reports/tickflow_phase2b_isolation_runtime_eval.md`
- Modify: this implementation plan only to mark completed steps.

**Interfaces:**
- Consumes: committed runtime evidence and immutable preflight hashes.
- Produces: final status `PHASE2B_ISOLATION_RUNTIME_VERIFIED` or the honest blocked status.

- [x] **Step 1: Run focused tests**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_isolation_runtime.py \
  tests/test_phase2_ai_worker_protocol.py \
  tests/test_phase2_claims.py \
  tests/test_phase2_claims_renderer.py -q
```

- [x] **Step 2: Run backend full and static tests**

```bash
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/python -m compileall -q app scripts
.venv/bin/ruff check app scripts --select F821
```

- [x] **Step 3: Revalidate Phase 2B-1 artifacts**

Run `validate_phase2_claims.py` and the legacy freeform validator. Require `CLAIMS_VALID` for typed Claims and the expected fail-closed `PHASE2_AI_OUTPUT_BLOCKED` for historical freeform output.

- [x] **Step 4: Recompute immutable hashes**

Require exact equality with:

```text
Phase 1 evidence: 8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5
Phase 1 request audits: dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d
Phase 2 Facts: 10c6664754b8e1391f55d9b1b8b98f1577b749f0dceafbb77288749ae5376ef7
Provider audit: 7996c13681e248b2a9932cdd7428949f1317719589234a7087b8ab019e4274f9
```

Also require all historical AI sample and rejected-preview hashes from the Phase 2B-1 report to remain unchanged.

- [x] **Step 5: Run sensitive and runtime-residue scans**

Require no credentials, absolute Home/Vault paths, raw inspect, container ID, command string, candidate/projection copy, transaction marker, staging path, or temporary output in committed reports.

- [x] **Step 6: Write the final report**

Record the real Docker version, pinned base digest, built image ID, exact runtime controls, mount count, probe results, Host contract results, tests, hashes, zero external actions, and cleanup result. Do not claim more than the evidence proves.

- [x] **Step 7: Commit the report and completed plan**

```bash
git add reports/tickflow_phase2b_isolation_runtime_eval.md \
  docs/superpowers/plans/2026-08-01-tickflow-phase2b2-isolation-runtime.md
git commit -m "docs: close phase2 isolation runtime validation"
```

- [x] **Step 8: Push and verify the branch**

Push only `codex/tickflow-phase2-ai-review` to `fork`, compare local `HEAD` with `git ls-remote`, and require a clean worktree. Do not create a PR, merge, or deploy.
