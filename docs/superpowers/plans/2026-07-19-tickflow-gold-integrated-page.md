# TickFlow Gold Integrated Research Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Selectively migrate the single-symbol `600489.SH` Gold Shadow research, manual legacy import, deterministic comparison, and 10-day observation gate into the normal `tickflow-stock-panel` application.

**Architecture:** Keep Gold as a bounded domain inside the existing FastAPI/React application. A dedicated TickFlow Pro gateway and five-minute sampler write isolated JSONL state; authenticated APIs expose research data, manual normalized imports, comparisons, and a non-activating observation gate to a normal `/gold` page. Gold candidates never enter the platform notification pipeline.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, APScheduler, TickFlow SDK, React 18, TypeScript, TanStack Query, Tailwind, pytest, Ruff, pnpm/Vite.

## Global Constraints

- Target baseline is `431550859f833c2dc84440125d3dc5e20a756e0a`; rebase onto the merged PR #133 before final delivery if upstream has moved.
- Migration source is exactly `481fec626221e6a6f0822ae49004e21dd53d43a7`; never merge the complete source branch.
- The only supported symbol is `600489.SH`; reject every other symbol.
- Official Gold samples use TickFlow Pro only; never fall back to stock-sdk, Tushare, or free data.
- Sampling cadence is 300 seconds during Beijing trading sessions.
- `GOLD_WORKSPACE_ENABLED` defaults to `false`.
- Gold external sending is impossible to enable in this release; no Telegram, Feishu, webhook, or generic alert dispatch.
- The observation gate requires 10 distinct complete trading days and only emits `collecting`, `failed`, or `review_eligible`.
- Passing the gate never enables notification behavior.
- No broker integration, order execution, position ownership, OpenClaw integration, financial-daily publishing, or trading advice.
- No real API key, password, token, raw legacy upload, or live API response may enter Git, fixtures, logs, or test output.
- Use fake TickFlow clients in automated tests. A real live probe is a separate, explicitly authorized operational step.
- Preserve normal Dashboard, Watchlist, Screener, Backtest, Monitor, Review, and Stock Analysis behavior.

---

## File Map

### Backend domain files

- `backend/app/services/gold_pva.py`: pure P/V/A, SJM, and candidate-signal calculations copied from the approved source revision.
- `backend/app/services/gold_shadow_store.py`: durable Gold snapshots, candidate projection, history cache, import metadata, comparison indexes, reviews, and health state.
- `backend/app/services/gold_external_guard.py`: Gold-only fail-closed notification port and durable attempt counter.
- `backend/app/services/gold_tickflow.py`: fixed-symbol TickFlow Pro quote/history gateway and process-local pacing.
- `backend/app/services/gold_shadow.py`: quote validation and snapshot orchestration without `QuoteService` coupling.
- `backend/app/services/gold_scheduler.py`: market-window guard and registration of the five-minute APScheduler job.
- `backend/app/services/gold_shadow_compare.py`: deterministic legacy/shadow parsing, matching, tolerances, and summary.
- `backend/app/services/gold_legacy_import.py`: bounded upload validation and normalized import persistence.
- `backend/app/services/gold_comparison_runs.py`: deterministic comparison run IDs, canonical supersede chain, and atomic result commits.
- `backend/app/services/gold_observation.py`: automatic metrics plus manual full-day/restart-review gate.
- `backend/app/api/gold.py`: authenticated status, query, upload, comparison, and review endpoints.

### Backend integration files

- `backend/app/config.py`: add only `gold_workspace_enabled: bool = False`.
- `backend/app/main.py`: always register the Gold router; initialize and schedule services only when enabled.
- `.env.example`: document `GOLD_WORKSPACE_ENABLED=false` without adding secrets.

### Frontend files

- `frontend/src/lib/api.ts`: Gold API contracts and request methods.
- `frontend/src/lib/queryKeys.ts`: stable Gold query keys.
- `frontend/src/lib/gold.ts`: response normalization and presentation helpers.
- `frontend/src/pages/GoldWorkspace.tsx`: normal authenticated page orchestration.
- `frontend/src/components/gold/GoldSnapshotPanel.tsx`: latest metrics, health, and candidates.
- `frontend/src/components/gold/GoldImportComparePanel.tsx`: upload and comparison workflow.
- `frontend/src/components/gold/GoldObservationPanel.tsx`: 10-day gate and manual review state.
- `frontend/src/router.tsx`: lazy `/gold` route inside the normal layout.
- `frontend/src/components/Layout.tsx`: conditionally visible “中金黄金” menu item.
- `frontend/scripts/test-gold-integrated-routing.mjs`: source-level route/shell regression.

---

### Task 1: Port The P/V/A Compatibility Calculator

**Files:**
- Create: `backend/app/services/gold_pva.py`
- Create: `backend/tests/test_gold_pva.py`
- Create: `backend/tests/fixtures/gold/production_2026-07-16.json`

**Interfaces:**
- Produces: `GoldPvaResult`, `calculate_pva(*, current_price: float, previous_close: float, completed_closes: list[float])`, `classify_state(p)`, and `build_candidate_signals(result, closes)`.
- Consumes: no platform services or IO.

- [ ] **Step 1: Restore the approved fixture and tests only**

```bash
git restore --source=481fec626221e6a6f0822ae49004e21dd53d43a7 -- \
  backend/tests/test_gold_pva.py \
  backend/tests/fixtures/gold/production_2026-07-16.json
```

- [ ] **Step 2: Run the test to verify RED**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_pva.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'app.services.gold_pva'`.

- [ ] **Step 3: Restore the approved pure implementation**

```bash
git restore --source=481fec626221e6a6f0822ae49004e21dd53d43a7 -- \
  backend/app/services/gold_pva.py
```

Do not alter thresholds or the legacy acceleration index. The exported calculation remains:

```python
def calculate_pva(
    *, current_price: float, previous_close: float, completed_closes: list[float]
) -> GoldPvaResult:
    if len(completed_closes) < 60:
        raise ValueError("history_insufficient: need 60 completed closes")
    closes = [float(value) for value in completed_closes[-60:]]
    if (
        not math.isfinite(current_price)
        or not math.isfinite(previous_close)
        or any(not math.isfinite(value) or value <= 0 for value in closes)
        or current_price <= 0
        or previous_close <= 0
    ):
        raise ValueError("invalid_price")
    reference = fmean(closes)
    legacy_history_velocity = closes[-2] - closes[-3]
    v = current_price - previous_close
    p = (current_price / reference - 1) * 100
    a = v - legacy_history_velocity
    base = GoldPvaResult(
        legacy_reference_60=round(reference, 2),
        native_ema60=round(ema(closes, 60), 2),
        p=round(p, 2),
        v=round(v, 2),
        a=round(a, 2),
        state=classify_state(p),
    )
    return replace(base, candidate_signals=build_candidate_signals(base, closes))
```

- [ ] **Step 4: Verify the domain tests and lint**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_pva.py -q`

Expected: all tests pass.

Run: `cd backend && uv run --extra dev ruff check app/services/gold_pva.py tests/test_gold_pva.py`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/gold_pva.py backend/tests/test_gold_pva.py backend/tests/fixtures/gold/production_2026-07-16.json
git commit -m "feat: port Gold PVA compatibility calculator"
```

### Task 2: Port And Harden Durable Gold Storage

**Files:**
- Create: `backend/app/services/gold_shadow_store.py`
- Create: `backend/tests/test_gold_shadow_store.py`
- Modify: `backend/tests/test_gold_shadow_store.py`

**Interfaces:**
- Produces: `GoldShadowStore(data_dir)`, `validate_gold_snapshot(snapshot)`, snapshot/candidate/health APIs, and atomic JSON state helpers.
- Consumes: Task 1 snapshot fields but does not import calculation code.

- [ ] **Step 1: Restore storage tests only and verify RED**

```bash
git restore --source=481fec626221e6a6f0822ae49004e21dd53d43a7 -- \
  backend/tests/test_gold_shadow_store.py
cd backend
uv run --extra dev pytest tests/test_gold_shadow_store.py -q
```

Expected: collection fails because `app.services.gold_shadow_store` is absent.

- [ ] **Step 2: Restore the approved storage implementation**

```bash
git restore --source=481fec626221e6a6f0822ae49004e21dd53d43a7 -- \
  backend/app/services/gold_shadow_store.py
```

- [ ] **Step 3: Add failing source and positivity validation tests**

Add these tests to `backend/tests/test_gold_shadow_store.py`:

```python
@pytest.mark.parametrize("field", ["price", "previous_close", "legacy_reference_60"])
def test_snapshot_rejects_nonpositive_reference_values(tmp_path, field):
    row = valid_snapshot()
    row[field] = 0
    with pytest.raises(ValueError, match=field):
        GoldShadowStore(tmp_path).append_snapshot(row)


def test_snapshot_requires_tickflow_source(tmp_path):
    row = valid_snapshot()
    row["quote_source"] = "tushare"
    with pytest.raises(ValueError, match="quote_source"):
        GoldShadowStore(tmp_path).append_snapshot(row)
```

Run: `cd backend && uv run --extra dev pytest tests/test_gold_shadow_store.py -q`

Expected: the two new cases fail because the restored validator accepts these values.

- [ ] **Step 4: Harden validation and permissions**

Update `validate_gold_snapshot` with exact checks:

```python
if snapshot["quote_source"] != "tickflow":
    raise ValueError("quote_source must be tickflow")
for field in ("price", "previous_close", "legacy_reference_60"):
    if snapshot[field] <= 0:
        raise ValueError(f"{field} must be positive")
```

Add a private root initializer and call it before every write:

```python
def _ensure_root(self) -> None:
    self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(self.root, 0o700)
```

Open newly created state files with mode `0o600` or call `os.chmod(path, 0o600)` immediately after atomic replace.

- [ ] **Step 5: Verify storage tests**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_shadow_store.py -q`

Expected: all storage tests pass, including corruption, idempotency, and candidate-projection recovery.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/gold_shadow_store.py backend/tests/test_gold_shadow_store.py
git commit -m "feat: add durable Gold research storage"
```

### Task 3: Add The Gold-Only External Send Guard

**Files:**
- Create: `backend/app/services/gold_external_guard.py`
- Create: `backend/tests/test_gold_external_guard.py`

**Interfaces:**
- Produces: `GoldExternalSendDenied`, `GoldNotificationPort`, `DisabledGoldNotifier.send(channel, payload)`, and `attempt_count(root)`.
- Consumes: a Gold storage root `Path`; never reads global notification settings.

- [ ] **Step 1: Write the failing guard tests**

```python
def test_disabled_notifier_records_and_denies(tmp_path):
    notifier = DisabledGoldNotifier(tmp_path / "gold_shadow")
    with pytest.raises(GoldExternalSendDenied):
        notifier.send("telegram", {"signal": "恐慌极端"})
    assert notifier.attempt_count() == 1


def test_guard_never_persists_payload_or_secret(tmp_path):
    notifier = DisabledGoldNotifier(tmp_path / "gold_shadow")
    with pytest.raises(GoldExternalSendDenied):
        notifier.send("webhook", {"token": "must-not-persist"})
    text = (tmp_path / "gold_shadow" / "external_send_attempts.jsonl").read_text()
    assert "must-not-persist" not in text
    assert '"channel":"webhook"' in text
```

Run: `cd backend && uv run --extra dev pytest tests/test_gold_external_guard.py -q`

Expected: collection fails because the module is absent.

- [ ] **Step 2: Implement the fail-closed port**

```python
class GoldExternalSendDenied(RuntimeError):
    pass


class GoldNotificationPort(Protocol):
    def send(self, channel: str, payload: Mapping[str, object]) -> None:
        pass


class DisabledGoldNotifier:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def send(self, channel: str, payload: Mapping[str, object]) -> None:
        del payload
        self._record_attempt(channel)
        raise GoldExternalSendDenied(f"Gold external send disabled: {channel}")

    def attempt_count(self) -> int:
        return len(self._validated_attempt_rows())
```

Use the source revision's `O_NOFOLLOW`, `0600`, `fsync`, channel regex, and malformed-state fail-closed behavior. Do not import `settings`, generic alerts, or any notification adapter.

- [ ] **Step 3: Verify and commit**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_external_guard.py -q`

Expected: all tests pass.

```bash
git add backend/app/services/gold_external_guard.py backend/tests/test_gold_external_guard.py
git commit -m "feat: enforce zero external sends for Gold"
```

### Task 4: Add The Fixed TickFlow Pro Gateway And Daily Cache

**Files:**
- Create: `backend/app/services/gold_tickflow.py`
- Create: `backend/tests/test_gold_tickflow.py`
- Modify: `backend/app/services/gold_shadow_store.py`
- Modify: `backend/tests/test_gold_shadow_store.py`

**Interfaces:**
- Produces: `GoldDataError(code)`, `GoldTickFlowGateway.get_quote() -> dict[str, object]`, and `GoldTickFlowGateway.get_completed_closes(expected_date: date) -> list[float]`.
- Consumes: `CapabilitySet`, `get_paid_realtime_client`, `resolve_limit`, `sleep_between_batches`, and `GoldShadowStore`.

- [ ] **Step 1: Write gateway contract tests with fake SDK namespaces**

```python
class FakeQuotes:
    def __init__(self):
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return [{"symbol": "600489.SH", "timestamp": 1784192400000,
                 "last_price": 19.99, "prev_close": 20.12}]


class FakeKlines:
    def batch(self, symbols, **kwargs):
        assert symbols == ["600489.SH"]
        assert kwargs["period"] == "1d"
        return {"600489.SH": daily_rows(90)}


def test_gateway_uses_only_paid_tickflow_namespaces(tmp_path):
    client = SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines())
    gateway = make_gateway(tmp_path, client)
    assert gateway.get_quote()["quote_source"] == "tickflow"
    assert len(gateway.get_completed_closes(date(2026, 7, 16))) == 60
    assert client.quotes.calls == [{"symbols": ["600489.SH"], "as_dataframe": False}]
```

Also add tests for missing Key, missing `QUOTE_BATCH`/`KLINE_DAILY_BATCH`, wrong symbol, duplicate dates, non-finite closes, fewer than 60 completed sessions, and a cache with `source != tickflow`.

Run: `cd backend && uv run --extra dev pytest tests/test_gold_tickflow.py -q`

Expected: collection fails because `gold_tickflow.py` is absent.

- [ ] **Step 2: Add daily cache methods to the store**

```python
def write_daily_history(self, payload: dict[str, Any]) -> None:
    if payload.get("source") != "tickflow":
        raise ValueError("daily history source must be tickflow")
    with _LOCK:
        self._write_json_state_locked("daily_history.json", payload)


def read_daily_history(self) -> dict[str, Any] | None:
    with _LOCK:
        value = self._read_json_state_locked("daily_history.json", raise_on_failure=True)
    return value if isinstance(value, dict) else None
```

The payload must contain `schema_version`, `source`, `expected_market_date`, `fetched_at`, `sha256`, and normalized `rows` of `{date, close}`.

- [ ] **Step 3: Implement the gateway**

```python
GOLD_SYMBOL = "600489.SH"


class GoldDataError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GoldTickFlowGateway:
    def __init__(self, store, capset_provider, client_factory=get_paid_realtime_client,
                 clock=cn_now) -> None:
        self.store = store
        self.capset_provider = capset_provider
        self.client_factory = client_factory
        self.clock = clock

    def get_quote(self) -> dict[str, object]:
        client = self._paid_client(Cap.QUOTE_BATCH)
        self._pace(Cap.QUOTE_BATCH)
        rows = client.quotes.get(symbols=[GOLD_SYMBOL], as_dataframe=False) or []
        matches = [row for row in rows if row.get("symbol") == GOLD_SYMBOL]
        if len(matches) != 1:
            raise GoldDataError("quote_contract_invalid")
        return {**matches[0], "quote_source": "tickflow"}
```

`get_completed_closes` must call `client.klines.batch([GOLD_SYMBOL], period="1d", count=90, adjust="none", as_dataframe=False)`, normalize distinct dates strictly before `expected_date`, persist the TickFlow-only cache, and return exactly the latest 60 closes. `_pace(cap)` resolves the live capability limit with the existing 80% safety factor and calls `sleep_between_batches(1, limit.rpm)` so concurrent callers reserve a real slot instead of the documented `index=0` burst path.

- [ ] **Step 4: Verify and commit**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_tickflow.py tests/test_gold_shadow_store.py -q`

Expected: all tests pass without network access.

```bash
git add backend/app/services/gold_tickflow.py backend/app/services/gold_shadow_store.py \
  backend/tests/test_gold_tickflow.py backend/tests/test_gold_shadow_store.py
git commit -m "feat: add fixed TickFlow Gold data gateway"
```

### Task 5: Adapt The Gold Evaluator And Five-Minute Scheduler

**Files:**
- Create: `backend/app/services/gold_shadow.py`
- Create: `backend/app/services/gold_scheduler.py`
- Create: `backend/tests/test_gold_shadow.py`
- Create: `backend/tests/test_gold_scheduler.py`

**Interfaces:**
- Produces: `GoldEvaluation`, `GoldShadowService.evaluate_quote(quote, completed_closes=values, expected_date=day)`, `GoldSampler.run_once()`, and `register_gold_sampler(scheduler, sampler)`.
- Consumes: Tasks 1-4.

- [ ] **Step 1: Restore evaluator tests and remove repository-specific setup**

Restore the approved evaluator tests:

```bash
git restore --source=481fec626221e6a6f0822ae49004e21dd53d43a7 -- \
  backend/tests/test_gold_shadow.py
```

Replace `FakeRepo` fixtures with a fixed `completed_closes` list and instantiate:

```python
service = GoldShadowService(
    GoldShadowStore(tmp_path),
    DisabledGoldNotifier(tmp_path / "user_data" / "gold_shadow"),
    clock=lambda: datetime(2026, 7, 16, 15, 0, tzinfo=CN_TZ),
)
result = service.evaluate_quote(
    quote(observed_at="2026-07-16T15:00:00+08:00"),
    completed_closes=fixture()["completed_closes"],
    expected_date=date(2026, 7, 16),
)
```

Keep tests for symbol, source, date, future/stale timestamps, session windows, previous close, 60 closes, health, duplicate quote timestamp, and candidate dedupe. Remove source tests that assert global Shadow runtime or `QuoteService` changes.

Run: `cd backend && uv run --extra dev pytest tests/test_gold_shadow.py -q`

Expected: collection fails because the module is absent.

- [ ] **Step 2: Port and adapt the evaluator**

Start from the source file, then remove `repo`, Polars, and `sync_and_persist_daily_symbol` dependencies:

```bash
git restore --source=481fec626221e6a6f0822ae49004e21dd53d43a7 -- \
  backend/app/services/gold_shadow.py
```

The adapted public method is:

```python
def evaluate_quote(
    self,
    quote: dict[str, Any],
    *,
    completed_closes: list[float],
    expected_date: date,
) -> GoldEvaluation:
    if quote.get("symbol") != self.SYMBOL:
        return self.fail("quote_symbol_mismatch", "TickFlow quote symbol does not match Gold")
    if quote.get("quote_source") != "tickflow":
        return self.fail("quote_source_mismatch", "Gold quote source must be tickflow")
    now = self._beijing_now()
    parsed = _parse_quote_timestamp(quote.get("timestamp"))
    if parsed is None:
        return self.fail("quote_timestamp_missing", "TickFlow quote has no valid timestamp")
    quote_ts, quote_time = parsed
    if quote_time.date() != expected_date:
        return self.fail("stale_market_date", "TickFlow quote market date is stale")
    if self._configured_holidays(expected_date) is None:
        return self.fail("calendar_unconfigured", "Gold holiday calendar unavailable")
    if self._market_closed(expected_date):
        return GoldEvaluation(None, "market_closed", expected_date.isoformat())
    if quote_time > now:
        return self.fail("quote_timestamp_future", "TickFlow quote timestamp is in the future")
    if now - quote_time > QUOTE_MAX_AGE:
        return self.fail("quote_timestamp_stale", "TickFlow quote is older than 600 seconds")
    if not self._quote_timestamp_in_session(quote_time):
        return self.fail("quote_timestamp_out_of_session", "TickFlow quote is outside session")
    if len(completed_closes) != 60:
        return self.fail("history_insufficient", "Gold requires exactly 60 completed closes")
    previous_close = self._positive_previous_close(quote.get("prev_close"))
    if previous_close is None:
        return self.fail("previous_close_missing", "TickFlow quote has no positive previous close")
    try:
        result = calculate_pva(
            current_price=float(quote["last_price"]),
            previous_close=previous_close,
            completed_closes=completed_closes,
        )
        snapshot = self._build_snapshot(
            quote, quote_ts, quote_time, expected_date, result, previous_close
        )
        committed = self.store.commit_evaluation(snapshot, now=now)
        self._record_success(committed)
        return GoldEvaluation(committed, None, None)
    except (KeyError, TypeError, ValueError):
        return self.fail("evaluation_failed", "Gold evaluation failed")
```

Require `quote_source == "tickflow"`. Accept timestamps only in `09:30-11:30` or `13:00-15:00`; unlike the isolated source runtime, do not accept 15:05/15:10 finalization samples because this integrated scheduler stops at 15:00.

- [ ] **Step 3: Write scheduler tests**

```python
def test_sampler_calls_gateway_once_inside_session():
    gateway = FakeGateway()
    service = FakeService()
    sampler = GoldSampler(gateway, service, clock=at("2026-07-16T10:00:00+08:00"))
    sampler.run_once()
    assert gateway.quote_calls == 1
    assert gateway.history_calls == [date(2026, 7, 16)]
    assert service.calls == 1


@pytest.mark.parametrize("stamp", [
    "2026-07-16T09:25:00+08:00",
    "2026-07-16T11:35:00+08:00",
    "2026-07-16T12:55:00+08:00",
    "2026-07-16T15:05:00+08:00",
    "2026-07-18T10:00:00+08:00",
])
def test_sampler_never_calls_tickflow_outside_session(stamp):
    gateway = FakeGateway()
    service = FakeService()
    sampler = GoldSampler(gateway, service, clock=lambda: at(stamp))
    assert sampler.run_once() is None
    assert gateway.quote_calls == 0
    assert gateway.history_calls == []
    assert service.calls == 0
```

Also assert the registered job has ID `gold_sampler_5m`, `max_instances=1`, `coalesce=True`, and `misfire_grace_time=30`.

- [ ] **Step 4: Implement the sampler and registration**

```python
def register_gold_sampler(scheduler: AsyncIOScheduler, sampler: GoldSampler) -> None:
    scheduler.add_job(
        sampler.run_once,
        trigger=CronTrigger(day_of_week="mon-fri", minute="*/5", timezone="Asia/Shanghai"),
        id="gold_sampler_5m",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        replace_existing=True,
    )
```

`GoldSampler.run_once()` checks the exact market windows before touching the gateway. It records structured health failures through `GoldShadowService.fail(code, safe_message)` and never rethrows into the shared scheduler.

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_shadow.py tests/test_gold_scheduler.py -q`

Expected: all tests pass.

```bash
git add backend/app/services/gold_shadow.py backend/app/services/gold_scheduler.py \
  backend/tests/test_gold_shadow.py backend/tests/test_gold_scheduler.py
git commit -m "feat: schedule five-minute Gold observations"
```

### Task 6: Port Deterministic Legacy/Shadow Comparison

**Files:**
- Create: `backend/app/services/gold_shadow_compare.py`
- Create: `backend/tests/test_gold_shadow_compare.py`
- Create: `backend/tests/fixtures/gold/legacy-monitor-2026-07-16.log`
- Create: `backend/tests/fixtures/gold/shadow-snapshots-2026-07-16.jsonl`

**Interfaces:**
- Produces: `LegacySample`, `ShadowSample`, `ComparisonReport`, `parse_legacy_text(text)`, `parse_shadow_rows(rows)`, and `compare_samples(legacy_rows, shadow_rows, match_window_seconds=150)`.
- Consumes: `validate_gold_snapshot` from Task 2.

- [ ] **Step 1: Restore approved tests and fixtures, then verify RED**

```bash
git restore --source=481fec626221e6a6f0822ae49004e21dd53d43a7 -- \
  backend/tests/test_gold_shadow_compare.py \
  backend/tests/fixtures/gold/legacy-monitor-2026-07-16.log \
  backend/tests/fixtures/gold/shadow-snapshots-2026-07-16.jsonl
cd backend
uv run --extra dev pytest tests/test_gold_shadow_compare.py -q
```

Expected: collection fails because `gold_shadow_compare.py` is absent.

- [ ] **Step 2: Restore the approved comparator**

```bash
git restore --source=481fec626221e6a6f0822ae49004e21dd53d43a7 -- \
  backend/app/services/gold_shadow_compare.py
```

- [ ] **Step 3: Add text/row entry points without changing comparison semantics**

```python
def parse_legacy_text(text: str) -> list[LegacySample]:
    return _parse_legacy_lines(text.splitlines())


def parse_shadow_rows(rows: list[dict[str, Any]]) -> list[ShadowSample]:
    return _parse_validated_shadow_rows(rows)
```

Refactor the existing path functions to delegate to these pure entry points. Preserve constants exactly: `PRICE_TOLERANCE=0.01`, `P_TOLERANCE=0.10`, `V_TOLERANCE=0.02`, `A_TOLERANCE=0.02`, `MATCH_WINDOW_SECONDS=150`.

Delete these source tests because the integrated product has no standalone comparator CLI:

- `test_cli_appends_valid_jsonl_with_summary_last_and_preserves_inputs`
- `test_cli_market_date_excludes_samples_from_other_days`
- `test_cli_empty_shadow_writes_missing_rows_and_zero_availability`
- `test_cli_rejects_output_aliasing_an_input_without_modifying_it`
- `test_cli_rejects_hardlink_output_alias_without_modifying_input`
- `test_cli_returns_2_for_missing_or_all_malformed_input`

Retain all parser, matching, tolerance, malformed-row, deterministic-tie, and summary tests.

- [ ] **Step 4: Verify and commit**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_shadow_compare.py -q`

Expected: all restored and new pure-entry tests pass.

```bash
git add backend/app/services/gold_shadow_compare.py backend/tests/test_gold_shadow_compare.py \
  backend/tests/fixtures/gold/legacy-monitor-2026-07-16.log \
  backend/tests/fixtures/gold/shadow-snapshots-2026-07-16.jsonl
git commit -m "feat: port deterministic Gold comparison"
```

### Task 7: Add Safe Manual Legacy Import

**Files:**
- Create: `backend/app/services/gold_legacy_import.py`
- Create: `backend/tests/test_gold_legacy_import.py`
- Modify: `backend/app/services/gold_shadow_store.py`

**Interfaces:**
- Produces: `GoldLegacyImporter.import_bytes(filename, payload) -> GoldImportResult` and store methods `commit_import(digest, filename, rows)`, `get_import(import_id)`, and `list_imports(limit)`.
- Consumes: `parse_legacy_text` from Task 6.

- [ ] **Step 1: Write import security tests**

```python
def test_import_normalizes_and_discards_raw_text(tmp_path):
    importer = GoldLegacyImporter(GoldShadowStore(tmp_path))
    result = importer.import_bytes("monitor.log", fixture_bytes())
    assert result.sample_count > 0
    persisted = (tmp_path / "user_data/gold_shadow/imports" / f"{result.import_id}.jsonl").read_text()
    assert "[ALERT]" not in persisted
    assert set(json.loads(persisted.splitlines()[0])) == {
        "schema_version", "observed_at", "price", "P", "V", "A", "state", "signals"
    }


def test_import_rejects_oversize_before_parsing(tmp_path):
    importer = GoldLegacyImporter(GoldShadowStore(tmp_path))
    with pytest.raises(GoldImportError, match="upload_too_large"):
        importer.import_bytes("monitor.log", b"x" * (10 * 1024 * 1024 + 1))
```

Also test non-UTF-8, ZIP magic bytes, empty samples, unsafe filename normalization, duplicate SHA-256 returning the same ID, atomic failure, and error messages that do not include source lines.

Run: `cd backend && uv run --extra dev pytest tests/test_gold_legacy_import.py -q`

Expected: collection fails because the importer is absent.

- [ ] **Step 2: Implement normalized import persistence**

```python
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class GoldImportResult:
    import_id: str
    sha256: str
    filename: str
    imported_at: str
    sample_count: int


def import_bytes(self, filename: str, payload: bytes) -> GoldImportResult:
    if len(payload) > MAX_UPLOAD_BYTES:
        raise GoldImportError("upload_too_large")
    if payload.startswith(b"PK\x03\x04"):
        raise GoldImportError("archive_not_allowed")
    digest = hashlib.sha256(payload).hexdigest()
    existing = self.store.find_import_by_sha256(digest)
    if existing:
        return GoldImportResult(**existing)
    text = payload.decode("utf-8", errors="strict")
    samples = parse_legacy_text(text)
    normalized = [normalize_legacy_sample(sample) for sample in samples]
    return self.store.commit_import(digest, Path(filename).name, normalized)
```

`commit_import` writes normalized JSONL to a temporary file, fsyncs, atomically renames it to `imports/<sha256>.jsonl`, then appends metadata to `import_index.jsonl`. It never persists `payload` or `text`.

- [ ] **Step 3: Verify and commit**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_legacy_import.py tests/test_gold_shadow_store.py -q`

Expected: all tests pass.

```bash
git add backend/app/services/gold_legacy_import.py backend/app/services/gold_shadow_store.py \
  backend/tests/test_gold_legacy_import.py
git commit -m "feat: add safe manual Gold legacy import"
```

### Task 8: Persist Canonical Comparison Runs

**Files:**
- Create: `backend/app/services/gold_comparison_runs.py`
- Create: `backend/tests/test_gold_comparison_runs.py`
- Modify: `backend/app/services/gold_shadow_store.py`

**Interfaces:**
- Produces: `GoldComparisonRunner.run(import_id, market_date) -> dict`, `get_run(run_id)`, and `list_runs(limit)`.
- Consumes: Tasks 2, 6, and 7.

- [ ] **Step 1: Write deterministic run tests**

```python
def test_same_inputs_return_same_run_id(tmp_path):
    runner = make_runner(tmp_path)
    first = runner.run("import-a", date(2026, 7, 16))
    second = runner.run("import-a", date(2026, 7, 16))
    assert second["run_id"] == first["run_id"]
    assert len(runner.list_runs(10)) == 1


def test_changed_import_supersedes_same_day_run(tmp_path):
    runner = make_runner(tmp_path)
    first = runner.run("import-a", date(2026, 7, 16))
    second = runner.run("import-b", date(2026, 7, 16))
    assert second["supersedes_run_id"] == first["run_id"]
```

Also test strict target-date filtering, no legacy samples, no Shadow samples, corrupted import, interrupted temporary write, and canonical list ordering.

- [ ] **Step 2: Implement run identity and atomic commit**

```python
def _run_id(*, market_date: date, legacy_digest: str,
            shadow_digest: str, comparator_version: str = "1") -> str:
    payload = f"{market_date.isoformat()}|{legacy_digest}|{shadow_digest}|{comparator_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

`run()` loads normalized legacy samples and persisted Shadow snapshots for exactly one Beijing market date, executes `compare_samples`, writes every row plus one terminal summary to `comparison_runs/<run_id>.jsonl`, and only then appends the run metadata to `comparison_index.jsonl`.

- [ ] **Step 3: Verify and commit**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_comparison_runs.py tests/test_gold_shadow_compare.py -q`

Expected: all tests pass.

```bash
git add backend/app/services/gold_comparison_runs.py backend/app/services/gold_shadow_store.py \
  backend/tests/test_gold_comparison_runs.py
git commit -m "feat: persist canonical Gold comparison runs"
```

### Task 9: Add The Non-Activating 10-Day Observation Gate

**Files:**
- Create: `backend/app/services/gold_observation.py`
- Create: `backend/tests/test_gold_observation.py`
- Modify: `backend/app/services/gold_shadow_store.py`

**Interfaces:**
- Produces: `GoldObservationService.review_day(market_date, run_id, verified, note)`, `review_restart(verified, note)`, and `status() -> dict`.
- Consumes: comparison indexes, external send attempt count, and durable manual reviews.

- [ ] **Step 1: Write gate state tests**

```python
def test_nine_valid_days_remain_collecting(service):
    seed_valid_days(service.store, count=9)
    assert service.status()["status"] == "collecting"


def test_ten_days_and_restart_review_are_review_eligible(service):
    seed_valid_days(service.store, count=10)
    service.review_restart(verified=True, note="snapshot and dedupe restored")
    result = service.status()
    assert result["status"] == "review_eligible"
    assert "notifications_enabled" not in result


def test_external_attempt_forces_failed(service, notifier):
    seed_valid_days(service.store, count=10)
    with pytest.raises(GoldExternalSendDenied):
        notifier.send("telegram", {})
    assert service.status()["status"] == "failed"
```

Also test duplicate dates, failed tolerance, missing manual full-day review, unresolved supersede chain, missing restart review, and that manual review cannot override failed automatic checks.

- [ ] **Step 2: Implement the gate**

```python
VALID_STATUSES = frozenset({"collecting", "failed", "review_eligible"})


def status(self) -> dict[str, Any]:
    days = self._canonical_reviewed_days()
    reasons = self._failure_reasons(days)
    if reasons:
        state = "failed"
    elif len(days) < 10 or not self._restart_verified():
        state = "collecting"
    else:
        state = "review_eligible"
    return {
        "status": state,
        "required_complete_trading_days": 10,
        "complete_trading_days": len(days),
        "external_send_count": self.notifier.attempt_count(),
        "reasons": reasons,
    }
```

Persist manual reviews in `observation_reviews.jsonl`; require a non-empty note and existing canonical comparison run. Do not add a method that enables notification.

- [ ] **Step 3: Verify and commit**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_observation.py -q`

Expected: all tests pass.

```bash
git add backend/app/services/gold_observation.py backend/app/services/gold_shadow_store.py \
  backend/tests/test_gold_observation.py
git commit -m "feat: add Gold observation eligibility gate"
```

### Task 10: Expose Authenticated Gold APIs And Wire The Lifespan

**Files:**
- Create: `backend/app/api/gold.py`
- Create: `backend/tests/test_gold_api.py`
- Create: `backend/tests/test_gold_runtime.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: the API table from the approved design and `app.state.gold_*` service references.
- Consumes: Tasks 2-9 and the existing FastAPI authentication middleware.

- [ ] **Step 1: Write disabled/enabled API tests**

```python
def test_disabled_status_is_available_without_starting_sampler(client):
    response = client.get("/api/gold/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_upload_never_returns_raw_content(enabled_client, legacy_fixture):
    response = enabled_client.post(
        "/api/gold/imports/legacy",
        files={"file": ("monitor.log", legacy_fixture, "text/plain")},
    )
    assert response.status_code == 201
    assert "raw" not in response.json()


def test_disabled_write_endpoint_fails_explicitly(client):
    response = client.post("/api/gold/comparisons", json={"import_id": "x", "market_date": "2026-07-16"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "gold_workspace_disabled"
```

Add authentication regressions for upload, comparison, and observation-review writes using the project's existing auth test helpers. Add limit validation for `1..1000`.

- [ ] **Step 2: Add the feature flag**

In `backend/app/config.py`:

```python
# Gold research workspace; fixed-symbol TickFlow Pro sampler, disabled by default.
gold_workspace_enabled: bool = False
```

In `.env.example`:

```dotenv
# Optional fixed-symbol Gold research workspace. External sends remain disabled.
GOLD_WORKSPACE_ENABLED=false
```

- [ ] **Step 3: Implement the router**

Use `APIRouter(prefix="/api/gold", tags=["gold-research"])`. Define Pydantic request models:

```python
class GoldComparisonRequest(BaseModel):
    import_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    market_date: date


class GoldDayReviewRequest(BaseModel):
    kind: Literal["day"] = "day"
    market_date: date
    run_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    complete_window_verified: bool
    note: str = Field(min_length=1, max_length=500)


class GoldRestartReviewRequest(BaseModel):
    kind: Literal["restart"] = "restart"
    verified: bool
    note: str = Field(min_length=1, max_length=500)


GoldObservationReviewRequest = Annotated[
    GoldDayReviewRequest | GoldRestartReviewRequest,
    Field(discriminator="kind"),
]
```

Read services from `request.app.state`. Implement these exact routes:

- `GET /status`, `GET /health`, `GET /snapshots`, and `GET /candidates`.
- `POST /imports/legacy` and `GET /imports`.
- `POST /comparisons`, `GET /comparisons`, and `GET /comparisons/{run_id}`.
- `GET /observation-gate` and `POST /observation-reviews` using the discriminated request union above.

`GET /status` always returns a disabled structure when services are absent; all other disabled writes return HTTP 409 with a stable structured code. Disabled list endpoints return empty rows and do not initialize storage.

- [ ] **Step 4: Wire normal application startup**

In `backend/app/main.py`, import and include `gold.router` with normal routers. After the shared scheduler is created:

```python
app.state.gold_shadow_service = None
app.state.gold_store = None
app.state.gold_legacy_importer = None
app.state.gold_comparison_runner = None
app.state.gold_observation_service = None
if settings.gold_workspace_enabled:
    gold_store = GoldShadowStore(store.data_dir)
    gold_notifier = DisabledGoldNotifier(gold_store.root)
    gold_gateway = GoldTickFlowGateway(
        gold_store,
        capset_provider=lambda: app.state.capabilities,
    )
    gold_service = GoldShadowService(gold_store, gold_notifier)
    gold_sampler = GoldSampler(gold_gateway, gold_service)
    gold_importer = GoldLegacyImporter(gold_store)
    gold_comparison_runner = GoldComparisonRunner(gold_store)
    gold_observation = GoldObservationService(gold_store, gold_notifier)
    app.state.gold_store = gold_store
    app.state.gold_shadow_service = gold_service
    app.state.gold_legacy_importer = gold_importer
    app.state.gold_comparison_runner = gold_comparison_runner
    app.state.gold_observation_service = gold_observation
    if app.state.scheduler is not None:
        register_gold_sampler(app.state.scheduler, gold_sampler)
    else:
        gold_service.fail("scheduler_unavailable", "Gold sampler scheduler is unavailable")
```

Do not change `QuoteService`, the generic monitor engine, CORS, the normal route set, or platform notification setup. If the shared scheduler is unavailable, leave the API readable and record `scheduler_unavailable` in Gold health.

- [ ] **Step 5: Verify API and runtime behavior**

Run: `cd backend && uv run --extra dev pytest tests/test_gold_api.py tests/test_gold_runtime.py -q`

Expected: all tests pass.

Run: `cd backend && uv run --extra dev pytest tests/test_gold_*.py -q`

Expected: the complete Gold backend suite passes.

- [ ] **Step 6: Commit**

```bash
git add .env.example backend/app/config.py backend/app/main.py backend/app/api/gold.py \
  backend/tests/test_gold_api.py backend/tests/test_gold_runtime.py
git commit -m "feat: expose Gold research APIs in normal runtime"
```

### Task 11: Add Frontend Gold Contracts And Defensive Normalization

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/queryKeys.ts`
- Create: `frontend/src/lib/gold.ts`
- Create: `frontend/scripts/test-gold-contracts.mjs`

**Interfaces:**
- Produces: typed `api.gold*` methods, `QK.gold*` keys, and normalization functions used by UI components.
- Consumes: Task 10 response contracts.

- [ ] **Step 1: Add a failing source-level contract script**

```javascript
import assert from 'node:assert/strict'
import { normalizeGoldStatus, normalizeGoldGate } from '../src/lib/gold.ts'

const disabled = {
  enabled: false,
  symbol: null,
  latest: null,
  latest_market_date: null,
  health: {
    latest_success_at: null,
    latest_market_date: null,
    consecutive_failures: 0,
    last_error: null,
  },
  external_send_count: 0,
}
assert.equal(normalizeGoldStatus(disabled).enabled, false)
assert.equal(normalizeGoldStatus({ enabled: true, symbol: '000001.SZ' }), null)
assert.equal(normalizeGoldGate({
  status: 'review_eligible',
  required_complete_trading_days: 10,
  complete_trading_days: 10,
  external_send_count: 0,
  reasons: [],
}).status, 'review_eligible')
assert.equal(normalizeGoldGate({ status: 'notifications_enabled' }), null)
```

Run: `cd frontend && node --experimental-strip-types scripts/test-gold-contracts.mjs`

Expected: fails because `src/lib/gold.ts` is absent.

- [ ] **Step 2: Add exact frontend types and API methods**

Add `GoldShadowSnapshot`, `GoldHealth`, `GoldStatus`, `GoldImport`, `GoldComparisonSummary`, `GoldComparisonRun`, `GoldObservationGate`, and `GoldObservationReviewRequest` interfaces to `frontend/src/lib/api.ts`. Define the shared wrappers and write contracts exactly:

```typescript
export interface GoldRows<T> { rows: T[] }
export interface GoldComparisonRequest { import_id: string; market_date: string }
export type GoldObservationReviewRequest =
  | { kind: 'day'; market_date: string; run_id: string; complete_window_verified: boolean; note: string }
  | { kind: 'restart'; verified: boolean; note: string }
export type GoldGateStatus = 'collecting' | 'failed' | 'review_eligible'
```

Add methods:

```typescript
goldStatus: () => request<GoldStatus>('/api/gold/status'),
goldSnapshots: (limit = 100) => request<GoldRows<GoldShadowSnapshot>>(`/api/gold/snapshots?limit=${limit}`),
goldCandidates: (limit = 100) => request<GoldRows<GoldCandidate>>(`/api/gold/candidates?limit=${limit}`),
goldImportLegacy: (file: File) => {
  const body = new FormData()
  body.append('file', file)
  return request<GoldImport>('/api/gold/imports/legacy', { method: 'POST', body })
},
goldImports: (limit = 100) => request<GoldRows<GoldImport>>(`/api/gold/imports?limit=${limit}`),
goldRunComparison: (payload: GoldComparisonRequest) => request<GoldComparisonSummary>('/api/gold/comparisons', {
  method: 'POST', body: JSON.stringify(payload),
}),
goldComparisons: (limit = 100) => request<GoldRows<GoldComparisonSummary>>(`/api/gold/comparisons?limit=${limit}`),
goldComparison: (runId: string) => request<GoldComparisonRun>(`/api/gold/comparisons/${encodeURIComponent(runId)}`),
goldObservationGate: () => request<GoldObservationGate>('/api/gold/observation-gate'),
goldReviewObservation: (payload: GoldObservationReviewRequest) => request<GoldObservationGate>('/api/gold/observation-reviews', {
  method: 'POST', body: JSON.stringify(payload),
}),
```

Add query keys `goldStatus`, `goldSnapshots`, `goldCandidates`, `goldImports`, `goldComparisons`, and `goldObservationGate`.

- [ ] **Step 3: Implement defensive normalizers**

`normalizeGoldStatus` must reject an enabled object unless symbol is exactly `600489.SH`, source is `tickflow`, numeric values are finite, external send count is a non-negative integer, and state/signals are recognized. `normalizeGoldGate` only accepts the three approved states.

- [ ] **Step 4: Verify and commit**

Run: `cd frontend && node --experimental-strip-types scripts/test-gold-contracts.mjs`

Expected: exits zero.

Run: `cd frontend && corepack pnpm build`

Expected: TypeScript and Vite production build pass.

```bash
git add frontend/src/lib/api.ts frontend/src/lib/queryKeys.ts frontend/src/lib/gold.ts \
  frontend/scripts/test-gold-contracts.mjs
git commit -m "feat: add Gold frontend data contracts"
```

### Task 12: Build The Normal Gold Research Page

**Files:**
- Create: `frontend/src/pages/GoldWorkspace.tsx`
- Create: `frontend/src/components/gold/GoldSnapshotPanel.tsx`
- Create: `frontend/src/components/gold/GoldImportComparePanel.tsx`
- Create: `frontend/src/components/gold/GoldObservationPanel.tsx`

**Interfaces:**
- Produces: a complete `/gold` page body without its own shell.
- Consumes: Task 11 API methods, keys, and normalizers.

- [ ] **Step 1: Restore the approved source page as reference, not as the final file**

Inspect without modifying the target:

```bash
git show 481fec626221e6a6f0822ae49004e21dd53d43a7:frontend/src/pages/GoldWorkspace.tsx \
  | sed -n '1,860p'
```

Use its metric labels, response hardening, loading states, and comparison presentation. Do not copy `GoldShadowLayout`, shadow-mode redirects, or external-notification assumptions.

- [ ] **Step 2: Implement the snapshot panel**

The component props are explicit:

```typescript
interface GoldSnapshotPanelProps {
  status: GoldStatus
  snapshots: GoldShadowSnapshot[]
  candidates: GoldCandidate[]
  isFetching: boolean
}
```

Render price, observed time, `quote_source`, P/V/A, `legacy_reference_60`, `native_ema60`, SJM state, candidates, latest success, consecutive failures, and last structured error. Render “Gold 外部通知已关闭” and `external_send_count` visibly.

- [ ] **Step 3: Implement the import/comparison panel**

Use a file input restricted to `.log,.txt,.json,.jsonl`; show filename, size, parsing result, import ID abbreviation, sample count, market-date selector, run button, availability, pass rates, missing Shadow count, and canonical/superseded state. Never render raw uploaded text.

- [ ] **Step 4: Implement the observation panel**

Show status, complete-day count out of 10, per-day run state, failure reasons, restart-review state, and zero-send count. The manual review form only records full-day/restart verification; it contains no notification toggle.

- [ ] **Step 5: Compose the page**

`GoldWorkspace.tsx` uses TanStack Query with:

```typescript
useQuery({ queryKey: QK.goldStatus, queryFn: api.goldStatus, refetchInterval: 30_000 })
useQuery({ queryKey: QK.goldSnapshots, queryFn: () => api.goldSnapshots(100), refetchInterval: 30_000 })
useQuery({ queryKey: QK.goldCandidates, queryFn: () => api.goldCandidates(100), refetchInterval: 30_000 })
useQuery({ queryKey: QK.goldObservationGate, queryFn: api.goldObservationGate, refetchInterval: 60_000 })
useQuery({ queryKey: QK.goldImports, queryFn: () => api.goldImports(100) })
useQuery({ queryKey: QK.goldComparisons, queryFn: () => api.goldComparisons(100) })
```

If disabled, render a compact unframed state explaining that `GOLD_WORKSPACE_ENABLED=false`. The page footer states “仅供研究，不构成投资建议”. Do not render trading actions, position controls, buy/sell language, stop-loss fields, or nested cards.

- [ ] **Step 6: Build and commit**

Run: `cd frontend && corepack pnpm build`

Expected: production build passes.

```bash
git add frontend/src/pages/GoldWorkspace.tsx frontend/src/components/gold
git commit -m "feat: add integrated Gold research workspace"
```

### Task 13: Add Normal Routing, Conditional Navigation, And Regression Gates

**Files:**
- Modify: `frontend/src/router.tsx`
- Modify: `frontend/src/components/Layout.tsx`
- Create: `frontend/scripts/test-gold-integrated-routing.mjs`
- Modify: `frontend/package.json`

**Interfaces:**
- Produces: lazy `/gold` route inside normal `Layout` and conditional “中金黄金” navigation.
- Consumes: Task 12 page.

- [ ] **Step 1: Write the failing routing regression**

```javascript
const router = read('src/router.tsx')
const layout = read('src/components/Layout.tsx')
assert.match(router, /path:\s*['"]gold['"]/)
assert.match(router, /lazy.*GoldWorkspace|GoldWorkspace.*lazy/s)
assert.doesNotMatch(router, /GoldShadowLayout/)
assert.match(layout, /中金黄金/)
assert.match(layout, /api\.goldStatus/)
assert.match(layout, /goldStatus\?\.enabled/)
```

Run: `cd frontend && node scripts/test-gold-integrated-routing.mjs`

Expected: fails before the route and menu exist.

- [ ] **Step 2: Add the lazy normal route**

```typescript
const GoldWorkspace = lazy(() => import('./pages/GoldWorkspace').then(m => ({ default: m.GoldWorkspace })))

// Inside the existing authenticated Layout children:
{ path: 'gold', element: <GoldWorkspace /> },
```

Keep the existing `Layout` Suspense boundary. Do not add shell-mode detection or authentication redirects beyond the normal router.

- [ ] **Step 3: Add conditional navigation**

Query the already-authenticated lightweight Gold status in `Layout` and add a `Coins` menu item only when enabled:

```typescript
const { data: goldStatus } = useQuery({
  queryKey: QK.goldStatus,
  queryFn: api.goldStatus,
  staleTime: 60_000,
})
const goldNav = goldStatus?.enabled
  ? [{ to: '/gold', label: '中金黄金', icon: Coins }]
  : []
type NavItem = { to: string; label: string; icon: React.ComponentType<{ className?: string }> }
const allNav: NavItem[] = Array.from(nav)
for (const item of goldNav) allNav.push(item)
for (const item of analysisNav) allNav.push(item)
```

The direct `/gold` route remains available and renders the disabled state when the backend says false.

- [ ] **Step 4: Add scripts and verify**

Add to `frontend/package.json`:

```json
"test:gold": "node --experimental-strip-types scripts/test-gold-contracts.mjs && node scripts/test-gold-integrated-routing.mjs"
```

Run: `cd frontend && corepack pnpm test:gold && corepack pnpm build`

Expected: both route scripts and the production build pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/router.tsx frontend/src/components/Layout.tsx \
  frontend/scripts/test-gold-integrated-routing.mjs frontend/package.json
git commit -m "feat: expose Gold in normal platform navigation"
```

### Task 14: Run Full Verification And Document Operations

**Files:**
- Create: `docs/gold-integrated-research-runbook.md`
- Modify: `README.md`

**Interfaces:**
- Produces: operator instructions and final verification evidence.
- Consumes: all prior tasks.

- [ ] **Step 1: Write the runbook**

Document exactly:

```dotenv
GOLD_WORKSPACE_ENABLED=true
```

Explain that the existing TickFlow Key store is reused, `holidays.json` is required under `data/user_data/gold_shadow/`, sampling is fixed to `600489.SH` every five minutes in session, raw imports are discarded, external sending is impossible, and 10 complete days only yield `review_eligible`. Include disable/rollback instructions using `GOLD_WORKSPACE_ENABLED=false` without deleting data.

- [ ] **Step 2: Run focused backend gates**

Run:

```bash
cd backend
uv run --extra dev pytest tests/test_gold_*.py -q
uv run --extra dev ruff check app/services/gold_*.py app/api/gold.py tests/test_gold_*.py
```

Expected: all Gold tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 3: Run the complete backend suite**

Run: `cd backend && uv run --extra dev pytest -q`

Expected: zero failures. Record the exact pass and warning counts in the implementation report; do not reuse counts from the source handoff.

- [ ] **Step 4: Run frontend gates**

Run:

```bash
cd frontend
corepack pnpm test:gold
corepack pnpm build
```

Expected: both commands exit zero.

- [ ] **Step 5: Build the Docker image with Gold disabled by default**

Run:

```bash
cp -n .env.example .env
docker compose config --quiet
docker compose build
```

Expected: Compose validation and image build succeed without requiring a live Key.

- [ ] **Step 6: Perform local UI visual QA**

Start the application with a temporary data directory and no real Key:

```bash
DATA_DIR=/tmp/tickflow-gold-ui GOLD_WORKSPACE_ENABLED=true ./dev.sh
```

Using browser automation, capture `/gold` at `1440x900` and `390x844`. Verify the disabled/missing-Key state is visible, the normal sidebar remains present, no text overlaps, upload controls fit, no trading action appears, and the page is not blank. Stop the dev processes after screenshots.

- [ ] **Step 7: Verify secret and scope boundaries**

Run:

```bash
git diff --check
git status --short
rg -n -i '(telegram|feishu|webhook|buy|sell|买入|卖出|止损|仓位)' \
  backend/app/services/gold_*.py backend/app/api/gold.py frontend/src/pages/GoldWorkspace.tsx \
  frontend/src/components/gold docs/gold-integrated-research-runbook.md
```

Expected: diff check is clean. Every notification match is an explicit prohibition/disabled label; every trading-language match is absent or part of a prohibition/disclaimer. No real credential value appears.

- [ ] **Step 8: Commit the operational documentation**

```bash
git add README.md docs/gold-integrated-research-runbook.md
git commit -m "docs: add integrated Gold research runbook"
```

- [ ] **Step 9: Final branch audit**

Run:

```bash
git status --short --branch
git log --oneline --decorate 431550859f833c2dc84440125d3dc5e20a756e0a..HEAD
git diff --stat 431550859f833c2dc84440125d3dc5e20a756e0a..HEAD
```

Expected: clean worktree; only the approved design, plan, Gold domain, minimal shared integration, frontend page, tests, and runbook are present. No Gold macOS App, `deploy/gold-shadow`, global Shadow mode, Telegram adapter, or unrelated source-branch change is included.
