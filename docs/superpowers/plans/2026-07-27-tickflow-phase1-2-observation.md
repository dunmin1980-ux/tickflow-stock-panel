# TickFlow Phase 1.2 Observation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the formal 2026-07-27 Phase 1.1 evidence as Phase 1.2 Day 1, then provide a strict, repeatable operator workflow for accumulating four more real trading days.

**Architecture:** Add a network-free Python observation materializer that validates compact Phase 1.1 contracts, hashes authoritative evidence, atomically writes daily records, and rebuilds the aggregate index/report. Add a thin shell runner for future days that streams the existing bounded live validator into the unchanged cloud container, then feeds only its sanitized result to the local materializer.

**Tech Stack:** Python 3.12 standard library, Bash, pytest, JSON, Markdown, SHA-256.

## Global Constraints

- Day 1 must not call TickFlow or add to the cumulative request audit.
- Use only `000403.SZ`, `600489.SH`, and `300059.SZ`.
- Preserve `OHLC_ABS_EPSILON = 1e-10` and `AMOUNT_ZERO_EPSILON = 1e-6`.
- Do not copy, modify, fill, shift, normalize, or overwrite source market evidence.
- Future live runs must be single-process, single-pipeline, serial, and SDK `retry_count=0`.
- Do not call `intraday_batch`; stop on the first HTTP 429 without retrying.
- Do not configure AI, develop Paper Trading, enable Telegram/OpenClaw/Integrated Gold, alter Gold Shadow, deploy cloud code, or schedule background jobs.
- Do not output credentials, complete Settings responses, request headers, Cookie, Session, Authorization, or API keys.
- Do not commit or deploy unless separately requested.

---

### Task 1: Offline Evidence Contract Tests

**Files:**
- Create: `backend/tests/test_phase1_observation.py`
- Create: `scripts/validate_phase1_observation.py`

**Interfaces:**
- Consumes: `Path` values for the repository root and reports root.
- Produces: `EvidenceError`, `sha256_file(path: Path) -> str`, `hash_evidence(paths: Sequence[Path], repo_root: Path) -> list[dict[str, Any]]`, and `build_reused_day(repo_root: Path, observation_date: str) -> dict[str, Any]`.

- [x] **Step 1: Write a loader and fixture builder in the test file**

```python
def _load_observer():
    path = Path(__file__).parents[2] / "scripts" / "validate_phase1_observation.py"
    spec = importlib.util.spec_from_file_location("phase1_observation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
```

The fixture builder must create compact daily, factor, minute, verification-summary,
and request-audit JSON for all three symbols without raw OHLCV rows.

- [x] **Step 2: Add failing happy-path and strict-evidence tests**

```python
def test_build_reused_day_validates_day1_without_network(tmp_path, monkeypatch):
    repo = _valid_phase11_repo(tmp_path)
    monkeypatch.setattr(socket, "create_connection", _fail_network)
    day = observer.build_reused_day(repo, "2026-07-27")
    assert day["status"] == "DAY_PASSED"
    assert day["evidence_origin"] == "phase1_1_reuse"
    assert day["live_request_reexecuted"] is False
    assert day["source_request_count"] == 14
    assert day["new_api_request_count"] == 0


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("stale_daily", "DAILY_STALE"),
        ("minute_before_close", "ONE_MINUTE_STALE"),
        ("material_ohlc", "MATERIAL_OHLC_ANOMALY"),
        ("material_amount", "MATERIAL_NEGATIVE_AMOUNT"),
        ("duplicate_timestamp", "DUPLICATE_TIMESTAMP"),
        ("lunch_bar", "LUNCH_BREAK_BAR"),
        ("factor_failed", "EX_FACTOR_CONTRACT_FAILED"),
        ("first_bucket_unexplained", "FIRST_BUCKET_UNEXPLAINED"),
        ("http_429", "HTTP_429_DETECTED"),
        ("secret_hit", "SENSITIVE_SHAPE_DETECTED"),
    ],
)
def test_build_reused_day_rejects_incomplete_evidence(tmp_path, mutation, reason):
    repo = _valid_phase11_repo(tmp_path)
    _mutate_fixture(repo, mutation)
    with pytest.raises(observer.EvidenceError, match=reason):
        observer.build_reused_day(repo, "2026-07-27")
```

- [x] **Step 3: Run the focused tests and confirm RED**

Run:

```bash
cd backend
uv run pytest -q tests/test_phase1_observation.py
```

Expected: import or missing-symbol failure because the observer is not implemented.

- [x] **Step 4: Implement immutable evidence loading and hashing**

```python
SYMBOLS = ("000403.SZ", "600489.SH", "300059.SZ")
OHLC_ABS_EPSILON = 1e-10
AMOUNT_ZERO_EPSILON = 1e-6


class EvidenceError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

`build_reused_day` must read JSON fields directly, require every listed contract,
verify the Phase 1.1 nested 14-record audit contains no `intraday_batch`, and
return a compact day dictionary. Markdown presence and hash are required, but
Markdown prose is not used to supply missing JSON fields.

- [x] **Step 5: Run tests and confirm GREEN**

Run:

```bash
cd backend
uv run pytest -q tests/test_phase1_observation.py
```

Expected: all Task 1 tests pass.

### Task 2: Atomic Day Materialization And Idempotency

**Files:**
- Modify: `backend/tests/test_phase1_observation.py`
- Modify: `scripts/validate_phase1_observation.py`

**Interfaces:**
- Consumes: the compact result from `build_reused_day`.
- Produces: `materialize_day(day: Mapping[str, Any], reports_root: Path) -> dict[str, Any]` and the six required files under a date directory.

- [x] **Step 1: Add failing output-shape and no-source-mutation tests**

```python
def test_materialize_day_writes_six_compact_files_without_touching_sources(tmp_path):
    repo = _valid_phase11_repo(tmp_path)
    before = observer.hash_evidence(observer.source_evidence_paths(repo), repo)
    day = observer.build_reused_day(repo, "2026-07-27")
    result = observer.materialize_day(day, repo / "reports")
    out = repo / "reports" / "phase1_observation" / "2026-07-27"
    assert sorted(path.name for path in out.iterdir()) == [
        "000403SZ_contract.json",
        "300059SZ_contract.json",
        "600489SH_contract.json",
        "daily_summary.json",
        "minute_30m_comparison.json",
        "request_audit.json",
    ]
    assert observer.hash_evidence(observer.source_evidence_paths(repo), repo) == before
    assert result["status"] == "DAY_PASSED"
```

- [x] **Step 2: Add failing idempotency and conflict tests**

```python
def test_materialize_day_is_idempotent_and_refuses_conflicting_hashes(tmp_path):
    repo = _valid_phase11_repo(tmp_path)
    day = observer.build_reused_day(repo, "2026-07-27")
    first = observer.materialize_day(day, repo / "reports")
    second = observer.materialize_day(day, repo / "reports")
    assert second == first
    changed = copy.deepcopy(day)
    changed["source_evidence"][0]["sha256"] = "0" * 64
    with pytest.raises(observer.EvidenceConflictError):
        observer.materialize_day(changed, repo / "reports")
```

- [x] **Step 3: Run the new tests and confirm RED**

Run:

```bash
cd backend
uv run pytest -q tests/test_phase1_observation.py -k materialize
```

Expected: missing `materialize_day` or missing conflict protection.

- [x] **Step 4: Implement compact atomic output**

Write all six JSON files to
`reports/phase1_observation/.2026-07-27.tmp-<pid>`, call `fsync` on each file,
and rename the complete directory to `2026-07-27`. Existing output with the same
evidence manifest is returned unchanged. Different evidence raises
`OBSERVATION_EVIDENCE_CONFLICT` before any write.

- [x] **Step 5: Run Task 2 tests and confirm GREEN**

Run:

```bash
cd backend
uv run pytest -q tests/test_phase1_observation.py -k materialize
```

Expected: all selected tests pass.

### Task 3: Observation Index And Status/Final Report

**Files:**
- Modify: `backend/tests/test_phase1_observation.py`
- Modify: `scripts/validate_phase1_observation.py`

**Interfaces:**
- Consumes: complete date directories under `reports/phase1_observation/`.
- Produces: `rebuild_index(reports_root: Path) -> dict[str, Any]`,
  `render_observation_report(index: Mapping[str, Any]) -> str`,
  `reports/phase1_observation/observation_index.json`, and
  `reports/tickflow_phase1_observation_status.md` while in progress.

- [x] **Step 1: Add failing state-machine tests**

```python
def test_index_requires_five_real_passed_days(tmp_path):
    reports = tmp_path / "reports"
    _write_days(reports, passed=4)
    assert observer.rebuild_index(reports)["final_status"] == (
        "PHASE1_OBSERVATION_IN_PROGRESS"
    )
    _write_days(reports, passed=5)
    assert observer.rebuild_index(reports)["final_status"] == (
        "PHASE1_OBSERVATION_PASSED"
    )


def test_non_trading_day_is_not_counted_and_blocked_day_stops_observation(tmp_path):
    reports = tmp_path / "reports"
    _write_day(reports, "2026-07-28", "NON_TRADING_DAY")
    assert observer.rebuild_index(reports)["valid_trading_days"] == 0
    _write_day(reports, "2026-07-29", "DAY_BLOCKED")
    assert observer.rebuild_index(reports)["final_status"] == (
        "PHASE1_OBSERVATION_BLOCKED"
    )
```

- [x] **Step 2: Add failing aggregate metric/report tests**

Assert the index reports:

```python
assert index["metrics"] == {
    "stale_data": 0,
    "material_ohlc_anomalies": 0,
    "material_negative_amount": 0,
    "duplicate_timestamps": 0,
    "lunch_break_bars": 0,
    "thirty_minute_ohlc_mismatches": 0,
    "non_first_bucket_volume_amount_mismatches": 0,
    "first_bucket_stable_rate": 1.0,
    "ex_factor_pass_rate": 1.0,
    "http_429": 0,
    "sensitive_shape_hits": 0,
}
```

The Markdown must contain the exact current status, effective day count, all
three symbol statuses, `AI=NOT_CONFIGURED`, `Paper Trading=NOT_STARTED`,
`cloud redeploy=NO`, and `Integrated Gold=DISABLED / external_send_count=0`.

- [x] **Step 3: Run Task 3 tests and confirm RED**

Run:

```bash
cd backend
uv run pytest -q tests/test_phase1_observation.py -k "index or report"
```

- [x] **Step 4: Implement deterministic aggregation**

Sort days by `observation_date`, number only `DAY_PASSED` days, sum failure
metrics, calculate stable/pass rates from passed symbol-day observations, and
write index/report atomically. Never synthesize future dates.

- [x] **Step 5: Run Task 3 tests and confirm GREEN**

Run:

```bash
cd backend
uv run pytest -q tests/test_phase1_observation.py -k "index or report"
```

### Task 4: CLI And Future Daily Runner

**Files:**
- Modify: `backend/tests/test_phase1_observation.py`
- Create: `scripts/run_phase1_daily_validation.sh`
- Create: `scripts/tests/test_run_phase1_daily_validation.sh`
- Modify: `scripts/validate_phase1_observation.py`

**Interfaces:**
- CLI modes:
  - `--reuse-phase11-day1 --repo-root PATH`
  - `--preflight-live-date --observation-date YYYY-MM-DD --now ISO8601`
  - `--record-non-trading-day --observation-date YYYY-MM-DD --now ISO8601`
  - `--materialize-live PATH --repo-root PATH --observation-date YYYY-MM-DD
    --now ISO8601 --symbol-set-hash HASH`
  - `--summarize --repo-root PATH`
- Runner options:
  - `--date YYYY-MM-DD`
  - `--dry-run`
  - `--validate-only`
  - `--reuse-day1`
- Runner environment:
  - `TICKFLOW_SSH_HOST`
  - `TICKFLOW_CONTAINER`
  - `PHASE1_AUTH_CONFIGURED_CONFIRMED=YES`
  - `PHASE1_SESSION_VALID_CONFIRMED=YES`
  - `PHASE1_TICKFLOW_KEY_CONFIGURED_CONFIRMED=YES`
  - `PHASE1_LOG_SCAN_PASSED=YES`
  - `PHASE1_OBSERVATION_NOW` only with `PHASE1_TEST_MODE=1`.

- [x] **Step 1: Add failing CLI tests**

Use `subprocess.run` to prove Day 1 CLI exits 0, emits
`PHASE1_OBSERVATION_IN_PROGRESS`, and creates no network-facing child process.
Prove malformed live JSON exits nonzero without creating a date directory.

- [x] **Step 2: Add failing shell-runner tests**

The shell test must replace `ssh` with a fixture executable and verify:

- execution outside 16:10 to 18:00 Asia/Shanghai is rejected;
- future and historical live dates are rejected;
- official non-trading days are recorded offline;
- a completed date or successful request audit is rejected before SSH;
- missing operator assertions are rejected;
- a held global process lock is rejected and a stale lock is recoverable;
- the streamed command contains `--live`, `--session-valid-confirmed`, and
  `--log-scan-passed`;
- neither source nor command contains `intraday_batch`;
- the runner never passes a retry option;
- no SSH/API path is invoked in `--reuse-day1` mode.

- [x] **Step 3: Run CLI and shell tests and confirm RED**

Run:

```bash
cd backend
uv run pytest -q tests/test_phase1_observation.py -k cli
cd ..
bash scripts/tests/test_run_phase1_daily_validation.sh
```

- [x] **Step 4: Implement the CLI and runner**

Use atomic `mkdir` for a macOS/Linux-compatible
`reports/phase1_observation/.runtime.lock` and remove it with a shell `trap`.
Use the official 2026 SSE closure schedule for the offline date gate, require
the next real trading date, and pass the fixed symbol-set hash through every
live gate. Stream
`backend/scripts/validate_phase1_tickflow_contracts.py` over SSH into:

```text
docker exec -i <container> /app/.venv/bin/python - --live \
  --session-valid-confirmed --log-scan-passed
```

Capture stdout in a `mktemp` file with mode `0600`, invoke
`--materialize-live`, and remove the temporary file on successful materialization.
Do not copy the validator into the container and do not modify cloud files.

- [x] **Step 5: Run Task 4 tests and confirm GREEN**

Run:

```bash
cd backend
uv run pytest -q tests/test_phase1_observation.py -k cli
cd ..
bash scripts/tests/test_run_phase1_daily_validation.sh
```

### Task 5: Materialize Formal Day 1

**Files:**
- Create: `reports/phase1_observation/2026-07-27/daily_summary.json`
- Create: `reports/phase1_observation/2026-07-27/000403SZ_contract.json`
- Create: `reports/phase1_observation/2026-07-27/600489SH_contract.json`
- Create: `reports/phase1_observation/2026-07-27/300059SZ_contract.json`
- Create: `reports/phase1_observation/2026-07-27/minute_30m_comparison.json`
- Create: `reports/phase1_observation/2026-07-27/request_audit.json`
- Create: `reports/phase1_observation/observation_index.json`
- Create: `reports/tickflow_phase1_observation_status.md`

**Interfaces:**
- Consumes: authoritative Phase 1.1 evidence in the current worktree.
- Produces: one counted observation day and an in-progress aggregate state.

- [x] **Step 1: Hash the original audit before materialization**

Run:

```bash
shasum -a 256 reports/phase1_tickflow_request_audit.json
```

Record the digest for post-run comparison.

- [x] **Step 2: Run the offline Day 1 command**

Run:

```bash
python3 scripts/validate_phase1_observation.py \
  --reuse-phase11-day1 \
  --repo-root "$PWD"
```

Expected: JSON containing `DAY_PASSED`,
`PHASE1_OBSERVATION_IN_PROGRESS`, `valid_trading_days=1`, and
`new_api_request_count=0`.

- [x] **Step 3: Verify source evidence stayed unchanged**

Run the same `shasum` command and require an identical digest. Verify the Day 1
manifest against every listed source file.

- [x] **Step 4: Verify all generated schemas**

Parse all generated JSON files, require the six-file date directory, confirm
`evidence_origin=phase1_1_reuse`, `live_request_reexecuted=false`,
`source_request_count=14`, `source_data_hashes_verified=true`, four vendor
pending items, and no copied raw bars.

### Task 6: Final Verification And Closeout

**Files:**
- Modify: `reports/tickflow_phase1_observation_status.md` only through the observer.

**Interfaces:**
- Consumes: final scripts, tests, Day 1 output, and unchanged Phase 1.1 evidence.
- Produces: fresh verification evidence for the current in-progress state.

- [x] **Step 1: Run focused tests**

```bash
cd backend
uv run pytest -q tests/test_phase1_observation.py
cd ..
bash scripts/tests/test_run_phase1_daily_validation.sh
```

- [x] **Step 2: Run static checks**

```bash
python3 -m compileall -q scripts/validate_phase1_observation.py
cd backend
uv run ruff check ../scripts/validate_phase1_observation.py \
  tests/test_phase1_observation.py --select F821
cd ..
bash -n scripts/run_phase1_daily_validation.sh
git diff --check
```

- [x] **Step 3: Run the complete backend suite**

```bash
cd backend
uv run pytest -q
```

Expected: the pre-existing suite plus the new Phase 1.2 tests passes.

- [x] **Step 4: Run sensitive-shape and boundary audit**

Scan only generated observation artifacts and scripts for credential shapes.
Confirm no API call occurred during Day 1, no cloud deployment occurred, no
Integrated Gold send occurred, and the original request audit hash is unchanged.

- [x] **Step 5: Audit all user requirements**

Require:

```text
Final status: PHASE1_OBSERVATION_IN_PROGRESS
Valid trading days: 1
Remaining actual trading days: 4
Day 1: DAY_PASSED / phase1_1_reuse / no live reexecution
429: NONE
Sensitive information: CLEAN
AI: NOT_CONFIGURED
Paper Trading: NOT_STARTED
Cloud redeploy: NO
Integrated Gold: DISABLED / external_send_count=0
```

Do not mark `PHASE1_OBSERVATION_PASSED` until four additional real trading-day
records have been generated and verified.
