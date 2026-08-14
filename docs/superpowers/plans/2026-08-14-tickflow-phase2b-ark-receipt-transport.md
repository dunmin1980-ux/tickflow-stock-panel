# TickFlow Phase 2B Ark Receipt Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair and prove the local Ark TLS Probe receipt transport channel without performing any public network, Provider, AI, Secret, or HTTP operation.

**Architecture:** The existing child probe publishes a durable two-file receipt bundle into one canonicalized, mode-0700 directory bind mount. The host validates and archives that bundle before cleanup, while a `--network none` harness exercises success and every approved fault path.

**Tech Stack:** Python 3.12, Docker CLI, pytest, Ruff, SHA-256 evidence manifests.

## Global Constraints

- Historical Probe `5dd641ca9e4b4ccba1035fff4d695409` remains `UNKNOWN / PROCESS_ERROR / CONSUMED / PRESERVED`.
- Do not modify any pre-existing file under `reports/phase2_provider_ark/tls_connectivity_probe/`.
- Do not perform Ark DNS, TCP, TLS, HTTP, Provider, AI, Secret, or Authorization work.
- Every Docker test container uses `--network none`, user `65532:65532`, read-only rootfs, `cap-drop=ALL`, and `no-new-privileges`.
- The only writable mount is one empty, one-shot output directory.
- Provider attempts, AI calls, and real public network successes remain zero.
- No future public TLS Probe is authorized by this plan.

---

### Task 1: Freeze history and reproduce the host-path failure

**Files:**
- Create: `backend/tests/test_phase2_ark_receipt_transport.py`
- Generate: `reports/phase2_provider_ark/tls_receipt_transport/historical_probe_baseline.json`

**Interfaces:**
- Consumes: the seven historical files in `reports/phase2_provider_ark/tls_connectivity_probe/`.
- Produces: `verify_receipt_transport_history(repo_root: Path, baseline_path: Path) -> int` and a frozen exact file-set manifest.

- [ ] **Step 1: Write the failing macOS alias test**

```python
def test_canonical_system_temp_path_is_readable_without_allowing_inner_symlink(
    runner_module, tmp_path
):
    alias = tmp_path / "system-alias"
    real = tmp_path / "private" / "var"
    real.mkdir(parents=True)
    alias.symlink_to(real, target_is_directory=True)
    output = alias / "probe" / "output"
    output.mkdir(parents=True)
    receipt = output / "child-receipt.json"
    receipt.write_text('{"value": 1}\n')

    canonical = runner_module.canonicalize_runtime_directory(output)
    assert canonical == output.resolve(strict=True)
    assert runner_module._read_json_regular(canonical / receipt.name) == {"value": 1}

    (canonical / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="runtime_path_symlink"):
        runner_module.validate_runtime_directory(canonical)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_ark_receipt_transport.py::test_canonical_system_temp_path_is_readable_without_allowing_inner_symlink -q
```

Expected: fail because `canonicalize_runtime_directory()` and the runtime-specific symlink validator do not exist.

- [ ] **Step 3: Implement canonical runtime directory validation**

Add to `backend/scripts/run_phase2_ark_tls_connectivity_probe.py`:

```python
def canonicalize_runtime_directory(path: Path) -> Path:
    canonical = path.resolve(strict=True)
    metadata = os.lstat(canonical)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("runtime_directory_invalid")
    validate_runtime_directory(canonical)
    return canonical

def validate_runtime_directory(path: Path) -> None:
    for child in path.iterdir():
        if child.is_symlink():
            raise ValueError("runtime_path_symlink")
```

Use this only for the one-shot runtime directory. Keep strict historical path validation unchanged.

- [ ] **Step 4: Generate and verify the historical manifest**

The JSON must contain sorted relative paths, exact SHA-256 values, `file_count=7`, historical Probe ID, and `historical_status=PRESERVED`. Verify exact set membership and hashes before continuing.

- [ ] **Step 5: Re-run the focused test and verify GREEN**

Expected: the canonical path is readable and an inner symlink remains rejected.

### Task 2: Make child exit zero imply a durable receipt bundle

**Files:**
- Modify: `docker/phase2-ark-tls-connectivity-probe/probe.py`
- Modify: `backend/tests/test_phase2_ark_receipt_transport.py`

**Interfaces:**
- Produces: `ReceiptPublicationError.category`, `_durable_publish_json(path, value) -> str`, `publish_receipt_bundle(receipt) -> dict[str, Any]`, and fixed paths `/output/child-receipt.json` plus `/output/receipt.ready`.

- [ ] **Step 1: Write failing publication tests**

Cover:

```python
def valid_local_receipt(probe_module):
    recorder = probe_module.ProbeEventRecorder(
        wall_clock=lambda: "2026-08-14T00:00:00Z",
        monotonic_ns=iter(range(100)).__next__,
    )
    for name in probe_module.CHILD_SUCCESS_EVENTS:
        recorder.record(name)
    receipt = probe_module._base_receipt("a" * 32, recorder)
    receipt.update(
        terminal_status="PROBE_PASSED",
        tls_error_category="NONE",
        tls_version="LOCAL_ONLY",
        cipher_name="LOCAL_ONLY",
        san_contains_hostname=True,
        events=recorder.values(),
    )
    return receipt

@pytest.mark.parametrize(
    ("fault", "category"),
    [
        ("open", "RECEIPT_OPEN_FAILED"),
        ("write", "RECEIPT_WRITE_FAILED"),
        ("fsync", "RECEIPT_FSYNC_FAILED"),
        ("rename", "RECEIPT_RENAME_FAILED"),
        ("parent_fsync", "RECEIPT_PARENT_FSYNC_FAILED"),
        ("validation", "RECEIPT_VALIDATION_FAILED"),
    ],
)
def test_publication_fault_has_exact_category(probe_module, fault, category):
    # inject_publication_fault monkeypatches exactly one os operation and
    # restores it before the next parameterized case.
    with inject_publication_fault(probe_module, fault):
        with pytest.raises(probe_module.ReceiptPublicationError) as raised:
            probe_module.publish_receipt_bundle(valid_local_receipt(probe_module))
    assert raised.value.category == category
```

Also assert the happy path creates two regular non-symlink files, the marker hash equals the receipt bytes, both files survive reopening, and the marker records publisher `65532:65532` in the container harness.

- [ ] **Step 2: Verify RED**

Expected: missing publication API and error categories.

- [ ] **Step 3: Implement durable two-file publication**

Implement the exact sequence for the receipt and marker: temporary open, write, flush, file fsync, same-directory `os.replace`, and parent directory fsync. Validate before any write. The marker schema is fixed:

```json
{
  "publication_schema_version": 1,
  "publication_complete": true,
  "probe_id": "<32 lowercase hex>",
  "receipt_sha256": "<64 lowercase hex>",
  "publisher_uid": 65532,
  "publisher_gid": 65532,
  "directory_uid": 65532,
  "directory_gid": 65532,
  "directory_mode": "0700"
}
```

In `main()`, catch only `ReceiptPublicationError`, emit one bounded JSON category to stderr, and return nonzero. Exit 0 occurs only after `publish_receipt_bundle()` returns.

Move the fixed probe ID path from `/output/probe-id` to
`/run/tickflow/probe-id`. The host mounts that one file read-only, leaving
`/output` empty before publication and making the post-run whitelist exactly
`child-receipt.json` plus `receipt.ready`.

- [ ] **Step 4: Verify GREEN and child compatibility**

Run the new publication tests and existing `test_phase2_ark_tls_connectivity_probe.py`. Expected: all pass.

### Task 3: Archive before cleanup and fail closed on transport gaps

**Files:**
- Modify: `backend/scripts/run_phase2_ark_tls_connectivity_probe.py`
- Modify: `backend/tests/test_phase2_ark_receipt_transport.py`

**Interfaces:**
- Produces: `HostLifecycleRecorder`, `read_receipt_bundle(output_dir, probe_id, probe_module)`, `archive_child_bundle(receipt_dir, bundle, lifecycle)`, and strict `receipt_archive_completed < cleanup_started` evidence.

- [ ] **Step 1: Write the failing exit-zero implication test**

```python
def test_exit_zero_without_host_visible_receipt_fails_closed(
    runner_module, probe_module, tmp_path
):
    result = subprocess.CompletedProcess(
        ["docker", "start", "--attach", "local-fixture"], 0, "", ""
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir(mode=0o700)
    with pytest.raises(
        runner_module.ReceiptTransportError,
        match="RECEIPT_OPEN_FAILED",
    ):
        runner_module.process_container_result(
            result=result,
            output_dir=output_dir,
            probe_id="a" * 32,
            probe_module=probe_module,
        )
```

Add tests for nonzero exact child category, wrong destination, missing marker,
marker hash mismatch, malformed receipt, unexpected output file, symlink, and
receipt/marker probe-ID mismatch.

- [ ] **Step 2: Write the failing lifecycle-order test**

```python
def test_cleanup_cannot_start_before_archive(runner_module):
    lifecycle = runner_module.HostLifecycleRecorder(
        wall_clock=lambda: "2026-08-14T00:00:00Z",
        monotonic_ns=iter(range(100)).__next__,
    )
    lifecycle.record("cleanup_started")
    with pytest.raises(ValueError, match="archive_required_before_cleanup"):
        lifecycle.record("receipt_archive_completed")
```

- [ ] **Step 3: Verify RED**

Expected: the current runner ignores the start return code, has no marker, and cleans before archive.

- [ ] **Step 4: Implement the minimal host sequence**

Capture the `docker start --attach` result. Resolve the runtime root to its
canonical path. Require output mode `0700` and exact file whitelist. Validate
and archive the child bundle before the cleanup `finally` block. Persist a
host transport evidence JSON before cleanup, then finalize cleanup status after
resource checks. Never replace a transport error with an empty success-capable
receipt.

- [ ] **Step 5: Verify GREEN and existing runner regression**

Run both receipt transport tests and existing TLS Probe tests. Expected: all pass.

### Task 4: Build the no-network fault harness and run it three times

**Files:**
- Create: `docker/phase2-ark-tls-connectivity-probe/receipt_transport_fixture.py`
- Create: `backend/scripts/run_phase2_ark_receipt_transport_harness.py`
- Create: `backend/tests/test_phase2_ark_receipt_transport_harness.py`
- Generate: `reports/phase2_provider_ark/tls_receipt_transport/local_harness.json`

**Interfaces:**
- Fixture modes: `happy`, `permission_denied`, `wrong_destination`, `rename_fail`, `missing_ready`, `malformed`.
- Harness output: one aggregate canonical JSON report with scenarios A-I and zero-public-operation counters.

- [ ] **Step 1: Write the failing aggregate-contract test**

Assert:

```python
assert report["status"] == "PASSED"
assert report["happy_path_runs"] == 3
assert report["happy_path_all_exit_zero"] is True
assert report["happy_path_all_archived_before_cleanup"] is True
assert report["fault_injection_all_fail_closed"] is True
assert report["real_public_network_success_count"] == 0
assert report["provider_attempt_count"] == 0
assert report["ai_call_count"] == 0
assert report["secret_content_read_count"] == 0
assert report["container_residue_count"] == 0
assert report["network_residue_count"] == 0
assert report["temporary_residue_count"] == 0
```

- [ ] **Step 2: Verify RED**

Expected: fixture, harness, and aggregate evidence do not exist.

- [ ] **Step 3: Implement the fixture and harness**

Every container uses `--network none`. The harness creates a fresh canonical
mode-0700 output directory per scenario, records host and container metadata,
validates exact categories, archives before cleanup, and removes all runtime
objects. The cleanup-before-archive scenario must be rejected by the lifecycle
contract rather than treated as a successful run.

- [ ] **Step 4: Run the harness and verify GREEN**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/python scripts/run_phase2_ark_receipt_transport_harness.py
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_ark_receipt_transport_harness.py -q
```

Expected: all nine requirements pass and every residue counter is zero.

### Task 5: Verify, review, and close out

**Files:**
- Generate: `reports/phase2_provider_ark/tls_receipt_transport/receipt_transport_eval.md`
- Generate: `reports/phase2_provider_ark/tls_receipt_transport/verification.json`

**Interfaces:**
- Final status: `PHASE2B_ARK_TLS_PROBE_RECEIPT_CHANNEL_READY` or a fail-closed blocker.

- [ ] **Step 1: Run complete local verification**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_ark_receipt_transport.py \
  tests/test_phase2_ark_receipt_transport_harness.py \
  tests/test_phase2_ark_tls_connectivity_probe.py -q
.venv/bin/python -m compileall -q \
  scripts/run_phase2_ark_tls_connectivity_probe.py \
  scripts/run_phase2_ark_receipt_transport_harness.py \
  ../docker/phase2-ark-tls-connectivity-probe/probe.py \
  ../docker/phase2-ark-tls-connectivity-probe/receipt_transport_fixture.py
.venv/bin/ruff check \
  scripts/run_phase2_ark_tls_connectivity_probe.py \
  scripts/run_phase2_ark_receipt_transport_harness.py \
  ../docker/phase2-ark-tls-connectivity-probe/probe.py \
  ../docker/phase2-ark-tls-connectivity-probe/receipt_transport_fixture.py \
  --select F821
cd ..
git diff --check
```

- [ ] **Step 2: Verify historical bytes and prohibited operations**

Recompute the exact seven-file historical manifest, compare every hash and the
file set, scan new sources/evidence for Secret, Authorization, HTTP request,
Ark endpoint execution, and confirm no Probe ledger or historical status was
changed.

- [ ] **Step 3: Obtain independent focused review**

Review exit-zero false-success, canonical path handling, mount mode and UID/GID,
atomic rename/fsync, marker validation, archive-before-cleanup, symlinks,
failure categories, historical isolation, and residue. Required result:
`NO ACTIONABLE FINDINGS`.

- [ ] **Step 4: Commit only approved receipt-channel files**

Do not stage any pre-existing untracked historical Canary evidence. Suggested
commit:

```text
fix: close Ark receipt transport channel
```

- [ ] **Step 5: Stop at the approved boundary**

Report `PHASE2B_ARK_TLS_PROBE_RECEIPT_CHANNEL_READY` only if every local proof
passes. Set next action to `REQUEST_NEW_TLS_PROBE_APPROVAL`. Do not perform a
public TLS Probe.
