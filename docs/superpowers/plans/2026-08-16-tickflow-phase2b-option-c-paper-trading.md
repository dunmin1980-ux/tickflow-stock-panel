# TickFlow Phase 2B Option C Paper Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-symbol, deterministic, offline A-share T+1 Paper Trading sidecar from frozen `000403.SZ` Facts and validated Typed Claims through ChenQuant Daily output.

**Architecture:** A strict Option C schema separates fixture identity, interpretation, signal, action, ledger, and report objects. Existing Claims and Projection validators remain authoritative; a new standalone Decimal ledger enforces next-trading-day-open execution and T+1 without importing the batch backtest engine. A repository-local CLI publishes only deterministic `HOLD` readiness artifacts atomically under `reports/phase2_option_c/`; synthetic BUY/SELL market bars exist only in tests.

**Tech Stack:** Python 3.11, Pydantic 2 strict models, `Decimal`, pytest, existing Phase 2 Claims/Projection services, existing atomic directory publisher.

## Global Constraints

- The implementation stays outside the main API and UI.
- Do not import or reuse `app.backtest` as the Paper Trading ledger.
- Do not call TickFlow, Ark, OpenAI, a broker, or any public network endpoint.
- The only business symbol is `000403.SZ`; no three-symbol batch is allowed.
- Enforce `FACT -> INTERPRETATION -> SIGNAL -> ACTION` with closed typed objects.
- Only the deterministic rule engine may produce `BUY`, `HOLD`, or `SELL`.
- All money uses `Decimal`; quantities are integers; buys use 100-share lots.
- Shares bought on one trade date are not sellable on that date.
- A signal may execute only at the first validated trading-day open after its decision date.
- Do not use a future close or any data unavailable at the decision timestamp.
- The frozen business fixture must yield `MIXED_OBSERVATION -> HOLD`.
- Synthetic BUY/SELL inputs must be marked `DETERMINISTIC_TEST_FIXTURE` and never published under `reports/phase2_option_c/`.
- Every output states `SIMULATION ONLY`, `can_publish=false`, and `trading_advice=false`.
- Replay x3 must produce byte-identical ledger, positions, PnL, JSON, and Markdown.
- Preserve all historical Provider evidence and request audits byte-for-byte.
- Keep `REAL_PROVIDER_INTEGRATION=DEFERRED`, `ARK_MINIMAL_PATH=FROZEN`, and `OPTION_A=EXHAUSTED`.
- Stop at `PHASE2B_OPTION_C_PAPER_TRADING_READY`.

---

## File Map

### New Runtime Files

- `backend/app/schemas/phase2_option_c.py`: strict Option C models, Decimal serialization, and closed enums.
- `backend/app/services/phase2_option_c_fixture.py`: load and verify frozen Facts, Projection, Claims, schemas, and the deterministic fixture manifest.
- `backend/app/services/phase2_research_decision.py`: build the reference interpretation and signal from validated Claims.
- `backend/app/services/phase2_paper_trading.py`: standalone T+1 account, deterministic action rules, execution, fees, positions, and PnL.
- `backend/app/services/phase2_chenquant_daily.py`: deterministic daily JSON/Markdown and replay comparison.
- `backend/app/services/phase2_option_c_delivery.py`: protected-evidence hashing and atomic report-tree publication.
- `backend/scripts/build_phase2_option_c_offline.py`: offline CLI with no provider or network imports.

### New Tests

- `backend/tests/test_phase2_option_c_fixture.py`
- `backend/tests/test_phase2_research_decision.py`
- `backend/tests/test_phase2_paper_trading.py`
- `backend/tests/test_phase2_chenquant_daily.py`
- `backend/tests/test_phase2_option_c_replay.py`

### Generated Artifacts

- `reports/phase2_option_c/reference_typed_claims.json`
- `reports/phase2_option_c/reference_fixture_manifest.json`
- `reports/phase2_option_c/account_config.json`
- `reports/phase2_option_c/decision.json`
- `reports/phase2_option_c/paper_account.json`
- `reports/phase2_option_c/trade_ledger.json`
- `reports/phase2_option_c/chenquant_daily.json`
- `reports/phase2_option_c/chenquant_daily.md`
- `reports/phase2_option_c/replay_validation.json`
- `reports/phase2_option_c/evidence_index.json`
- `reports/tickflow_phase2b_option_c_paper_trading_eval.md`

---

### Task 1: Strict Option C Contracts And Decimal Canonicalization

**Files:**
- Create: `backend/app/schemas/phase2_option_c.py`
- Create: `backend/tests/test_phase2_paper_trading.py`

**Interfaces:**
- Produces: `money(value: Decimal | str | int) -> Decimal`
- Produces: `canonical_option_c_bytes(value: BaseModel | Mapping[str, Any]) -> bytes`
- Produces: `PaperTradingConfig`, `DeterministicMarketFixture`, execution-only `MarketBar`, `ValuationBar`, `ResearchInterpretation`, `ResearchSignal`, `PaperAction`, `PositionLot`, `TradeRecord`, and `PaperAccount`.

- [ ] **Step 1: Write failing strict-model and Decimal tests**

Add tests proving that config values are `Decimal`, JSON money is serialized as fixed two-decimal strings, quantities reject booleans/floats/negative values, BUY quantities reject non-100-share lots, timestamps require `Asia/Shanghai`, and every report-capable object fixes `simulation_only="SIMULATION ONLY"`, `can_publish=false`, and `trading_advice=false`.

```python
def test_simulation_config_is_decimal_and_canonical() -> None:
    config = PaperTradingConfig.default()
    assert config.initial_cash == Decimal("100000.00")
    assert config.t_plus_one is True
    assert config.execution_policy == "NEXT_TRADING_DAY_OPEN"


@pytest.mark.parametrize("quantity", [True, 1.5, -100, 50])
def test_buy_action_rejects_invalid_lot_quantity(quantity: object) -> None:
    with pytest.raises(ValidationError):
        PaperAction.buy_fixture(quantity=quantity)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_paper_trading.py -q
```

Expected: collection fails because `app.schemas.phase2_option_c` does not exist.

- [ ] **Step 3: Implement closed models and canonical serialization**

Use `ConfigDict(extra="forbid", strict=True)`. Define these exact literals:

```python
InterpretationCode = Literal["MIXED_TECHNICAL_STRUCTURE", "POSITIVE_TEST_STRUCTURE", "RISK_TEST_STRUCTURE"]
SignalCode = Literal["POSITIVE_OBSERVATION", "MIXED_OBSERVATION", "RISK_OBSERVATION", "INVALID"]
ActionSide = Literal["BUY", "HOLD", "SELL"]
FixtureSource = Literal["DETERMINISTIC_REFERENCE_FIXTURE", "DETERMINISTIC_TEST_FIXTURE"]
```

Implement `money()` with `Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` and reject bool, NaN, Infinity, and non-string floats at strict model boundaries. Serialize Decimal money as strings, never JSON floats. Fix the default configuration to:

```python
PaperTradingConfig(
    config_version=1,
    initial_cash=Decimal("100000.00"),
    lot_size=100,
    buy_lots_per_signal=1,
    sell_lots_per_signal=1,
    commission_rate=Decimal("0.0003"),
    minimum_commission=Decimal("5.00"),
    sell_fee_rate=Decimal("0.0005"),
    t_plus_one=True,
    execution_policy="NEXT_TRADING_DAY_OPEN",
    slippage_rate=Decimal("0"),
    currency="CNY",
    simulation_only="SIMULATION ONLY",
    can_publish=False,
    trading_advice=False,
)
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused command. Expected: contract and canonicalization tests pass.

- [ ] **Step 5: Commit the contract slice**

```bash
git add backend/app/schemas/phase2_option_c.py backend/tests/test_phase2_paper_trading.py
git commit -m "feat: define deterministic paper trading contracts"
```

---

### Task 2: Deterministic Reference Fixture And Existing Claims Integration

**Files:**
- Create: `backend/app/services/phase2_option_c_fixture.py`
- Create: `backend/tests/test_phase2_option_c_fixture.py`

**Interfaces:**
- Consumes: `ClaimsDocument`, `validate_claims_document()`, `render_claims_document()`, `build_worker_projection()`, and `claims_json_schema()`.
- Produces: `ReferenceFixtureBundle`.
- Produces: `load_reference_fixture(repo_root: Path) -> ReferenceFixtureBundle`.
- Produces: `build_reference_artifacts(bundle: ReferenceFixtureBundle) -> dict[str, bytes]`.

- [ ] **Step 1: Write failing fixture identity tests**

Tests must prove:

```python
bundle = load_reference_fixture(REPO_ROOT)
assert bundle.manifest.source == "DETERMINISTIC_REFERENCE_FIXTURE"
assert bundle.claims.symbol == "000403.SZ"
assert bundle.claims.trade_date == "2026-07-31"
assert bundle.validation.status == "CLAIMS_VALID"
assert bundle.validation.trading_claim_count == 0
assert bundle.validation.free_text_field_count == 0
assert bundle.validation.unsourced_claim_count == 0
assert bundle.validation.raw_qfq_mismatch_count == 0
assert bundle.projection["projection_sha256"] == (
    "0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f"
)
```

Also copy source files to a temporary repository and prove that changing one
Facts byte, Claims byte, projection value, Claims-document schema, worker
candidate schema, symbol, trade date, source label, or source evidence
`run_completed_at` blocks fixture construction.

- [ ] **Step 2: Run the fixture tests and verify RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_option_c_fixture.py -q
```

Expected: import fails because the fixture service does not exist.

- [ ] **Step 3: Implement the fail-closed fixture loader**

The loader reads only these regular repository files:

```text
reports/phase2_facts/000403SZ_facts.json
reports/phase2_claims/fixtures/000403SZ_claims.json
reports/phase2_claims/schema/phase2_claims.schema.json
reports/phase1_observation/2026-07-31/daily_summary.json
```

It rebuilds the worker projection from Facts bytes, validates the Claims
document against Facts, renders Claims twice to prove deterministic bytes, and
binds both schema identities:

```text
claims_document_schema_sha256=9a78c1ddb751f8854f58d6f2266cc18907ab4dbfb44ea104d724930ed0e45a83
worker_candidate_schema_sha256=9e80c214fc338790c17af28167f9e3916435f3e52fecbaf7925114e16cb6a19f
```

The manifest includes `evidence_available_at` from the Phase 1
`run_completed_at`, `facts_sha256`, `projection_sha256`, `claims_sha256`, both
schema hashes, renderer hash, the four vendor-pending items, and fixed safety
flags. `reference_typed_claims.json` is a strict safe envelope with
`source=DETERMINISTIC_REFERENCE_FIXTURE`; its nested `claims_document` remains
the exact canonical `ClaimsDocument`. The source label and artifact safety state
(`SIMULATION ONLY`, `can_publish=false`, and `trading_advice=false`) stay outside
the nested frozen Typed Claims Schema.

- [ ] **Step 4: Run fixture and existing Claims tests**

```bash
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_option_c_fixture.py \
  tests/test_phase2_claims.py \
  tests/test_phase2_claims_renderer.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit the fixture slice**

```bash
git add backend/app/services/phase2_option_c_fixture.py backend/tests/test_phase2_option_c_fixture.py
git commit -m "feat: add deterministic option c research fixture"
```

---

### Task 3: Research Decision Layer And Look-Ahead Guard

**Files:**
- Create: `backend/app/services/phase2_research_decision.py`
- Create: `backend/tests/test_phase2_research_decision.py`

**Interfaces:**
- Consumes: `ReferenceFixtureBundle`.
- Produces: `build_research_decision(bundle: ReferenceFixtureBundle, decision_at: datetime) -> ResearchDecision`.
- Produces: `derive_action(signal: ResearchSignal, account: PaperAccount, config: SimulationConfig) -> PaperAction`.

- [ ] **Step 1: Write failing reference decision and boundary tests**

Assert the exact business path:

```python
decision = build_research_decision(
    load_reference_fixture(REPO_ROOT),
    datetime.fromisoformat("2026-07-31T21:15:00+08:00"),
)
assert decision.interpretation.code == "MIXED_TECHNICAL_STRUCTURE"
assert decision.signal.code == "MIXED_OBSERVATION"
assert decision.action.side == "HOLD"
```

Reason references must be the exact validated Claim IDs for market scope,
RSI6, MACD DIF versus DEA, and MA order. Add tests that reject a decision before
`evidence_available_at`, a decision in another timezone, a Facts/Claims date
mismatch, a Claim `as_of` after the decision, an invalid Claims result, a
free-text action, and a fixture identity mismatch.

- [ ] **Step 2: Run the decision tests and verify RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_research_decision.py -q
```

Expected: import fails because the decision service does not exist.

- [ ] **Step 3: Implement closed interpretation, signal, and action rules**

Implement a registry keyed by exact validated predicate states. The reference
rule requires:

```text
market_scope=INCOMPLETE
macd_dif_vs_dea=ABOVE
rsi6>=70
ma_value_order=DESCENDING with labels ma60,ma5,ma10,ma20
```

It yields only `MIXED_TECHNICAL_STRUCTURE -> MIXED_OBSERVATION -> HOLD`. Test
rules accept `POSITIVE_TEST_STRUCTURE` and `RISK_TEST_STRUCTURE` only when the
fixture source is `DETERMINISTIC_TEST_FIXTURE`. A positive test signal buys one
configured lot when flat and funded; a risk test signal sells at most one
configured eligible lot; every ineligible state yields HOLD with a closed
reason code. No method accepts model prose.

- [ ] **Step 4: Run decision and fixture tests**

```bash
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_research_decision.py \
  tests/test_phase2_option_c_fixture.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit the decision slice**

```bash
git add backend/app/services/phase2_research_decision.py backend/tests/test_phase2_research_decision.py
git commit -m "feat: add deterministic research decision layer"
```

---

### Task 4: Standalone A-Share T+1 Paper Trading Ledger

**Files:**
- Create: `backend/app/services/phase2_paper_trading.py`
- Modify: `backend/tests/test_phase2_paper_trading.py`

**Interfaces:**
- Produces: `new_account(config: SimulationConfig) -> PaperAccount`.
- Produces: `execute_action(account: PaperAccount, action: PaperAction, market_fixture: DeterministicMarketFixture | None) -> PaperAccount`.
- Produces: `mark_to_market(account: PaperAccount, bar: ValuationBar, *, valuation_at: datetime) -> PaperAccount`.
- Produces: `eligible_quantity(account: PaperAccount, symbol: str, trade_date: date) -> int`.

- [ ] **Step 1: Write failing cash, execution, and quantity tests**

Cover initial cash, HOLD with no Market Fixture, one-lot BUY, insufficient cash,
invalid quantity, duplicate decision/order/action/idempotency identities, and
rejection of any BUY not divisible by 100. Prove HOLD creates no order, execution
resolves the exact next Fixture trade date's `09:30`-available `open`, rejects
close fields in execution evidence, validates close availability separately, and returns
`NO_EXECUTION_PRICE_AVAILABLE` rather than skipping a missing bar.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_paper_trading.py -q
```

Expected: failures identify missing ledger behavior.

- [ ] **Step 3: Implement immutable account transitions and fee arithmetic**

Use these formulas with Decimal inputs:

```python
gross = money(execution_price * quantity)
commission = max(money(gross * config.commission_rate), config.minimum_commission)
sell_fee = money(gross * config.sell_fee_rate) if side == "SELL" else Decimal("0.00")
buy_cash_delta = -(gross + commission)
sell_cash_delta = gross - commission - sell_fee
```

Store buy cost as `gross + commission`. For a partial FIFO exit, allocate cost
as `money(lot.remaining_cost_cny * sold_quantity / lot.remaining_quantity)`;
the final exit consumes the exact remaining cost. Realized PnL is net sell cash
minus allocated cost.

- [ ] **Step 4: Write failing T+1, partial/full exit, and PnL tests**

Add tests for same-day sell rejection, next-trading-day eligibility, one-lot
partial exit from 200 shares, full exit from 100 shares, FIFO cost allocation,
buy/sell fees, realized PnL, unrealized PnL, equity, peak equity, current
drawdown, and max drawdown. Include a weekend date gap to prove the engine uses
ordered supplied trading bars rather than calendar-day arithmetic.

- [ ] **Step 5: Run tests and verify RED for the new behaviors**

Expected: at least the first T+1 or PnL assertion fails before implementation.

- [ ] **Step 6: Implement T+1 lots, marking, PnL, and drawdown**

Each lot records the exact next Fixture date in `sellable_from_trade_date`; only
lots whose sellable date is not later than the execution date are eligible to
sell. `mark_to_market` accepts a traceable raw mark bar, computes position
market value, unrealized PnL, total equity, peak equity, current drawdown, and
max drawdown with Decimal money. It never changes realized PnL or trade history.

- [ ] **Step 7: Run the complete Paper Trading suite**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_paper_trading.py -q
```

Expected: all pass.

- [ ] **Step 8: Prove no backtest/provider dependency and commit**

```bash
rg -n 'app\.backtest|phase2_.*provider|httpx|requests|urllib|socket' \
  backend/app/schemas/phase2_option_c.py \
  backend/app/services/phase2_paper_trading.py
```

Expected: no matches.

```bash
git add backend/app/services/phase2_paper_trading.py backend/tests/test_phase2_paper_trading.py
git commit -m "feat: add t1 paper trading ledger"
```

---

### Task 5: ChenQuant Daily And Deterministic Replay

**Files:**
- Create: `backend/app/services/phase2_chenquant_daily.py`
- Create: `backend/tests/test_phase2_chenquant_daily.py`
- Create: `backend/tests/test_phase2_option_c_replay.py`

**Interfaces:**
- Produces: `build_chenquant_daily(bundle: ReferenceFixtureBundle, decision: ResearchDecision, account: PaperAccount) -> ChenQuantDaily`.
- Produces: `render_chenquant_daily(report: ChenQuantDaily) -> str`.
- Produces: `run_reference_simulation(repo_root: Path) -> OptionCRun`.
- Produces: `validate_replay(runs: Sequence[OptionCRun]) -> ReplayValidation`.

- [ ] **Step 1: Write failing Daily contract tests**

Assert required fields for date, symbol, raw price state, Facts summary,
validated Claim IDs, research signal, paper position, simulated trades, cost,
unrealized/realized PnL, cumulative return, equity, max drawdown, risk notices,
next observation conditions, identities, and all safety flags. Assert Markdown
contains `SIMULATION ONLY` and `不构成投资建议`, contains no buy/sell advice,
URLs, HTML, model prose, or executable text.

- [ ] **Step 2: Run Daily tests and verify RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_chenquant_daily.py -q
```

Expected: import fails because the Daily service does not exist.

- [ ] **Step 3: Implement fixed JSON and Markdown rendering**

The business report uses the frozen raw close `10.43` only as the decision-date
mark, has no execution, and records `MIXED_OBSERVATION`, `HOLD`, zero fees, zero
realized/unrealized PnL, initial cash/equity `100000.00`, and zero drawdown. The
renderer uses fixed section order and closed labels; only validated numbers and
Claim IDs enter output.

- [ ] **Step 4: Write failing Replay x3 tests**

Run `run_reference_simulation(REPO_ROOT)` three times and compare canonical
bytes for decision, account, ledger, Daily JSON, Markdown, and per-file SHA-256.
Mutating one input between runs must yield `DETERMINISTIC_REPLAY_FAILED` rather
than silently accepting the mismatch.

- [ ] **Step 5: Run replay tests and verify RED**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_option_c_replay.py -q
```

Expected: failures identify missing replay validation.

- [ ] **Step 6: Implement replay comparison and verify GREEN**

`validate_replay()` requires exactly three runs and exact byte equality. It
reports `DETERMINISTIC_REPLAY=PASSED`, the common artifact hashes, and
`replay_count=3`; it does not normalize mismatched bytes.

```bash
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_chenquant_daily.py \
  tests/test_phase2_option_c_replay.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit Daily and replay**

```bash
git add \
  backend/app/services/phase2_chenquant_daily.py \
  backend/tests/test_phase2_chenquant_daily.py \
  backend/tests/test_phase2_option_c_replay.py
git commit -m "feat: add deterministic chenquant daily replay"
```

---

### Task 6: Atomic Offline Delivery And Historical Evidence Guard

**Files:**
- Create: `backend/app/services/phase2_option_c_delivery.py`
- Create: `backend/scripts/build_phase2_option_c_offline.py`
- Modify: `backend/tests/test_phase2_option_c_replay.py`

**Interfaces:**
- Produces: `hash_protected_evidence(repo_root: Path) -> EvidenceSnapshot`.
- Produces: `publish_option_c_run(repo_root: Path, reports_root: Path, *, allow_test_output_root: bool = False) -> DeliveryResult`.
- CLI: `python scripts/build_phase2_option_c_offline.py --repo-root .. --reports-root ../reports`.

- [ ] **Step 1: Write failing atomic delivery and preservation tests**

Use a temporary reports root to prove that delivery creates exactly the ten
approved files, publishes no synthetic BUY/SELL fixture, rejects symlinked
roots, preserves an old destination on staging failure, leaves no staging
directory, and writes canonical bytes. Monkeypatch the existing
`atomic_publish_directory()` to fail and assert the previous output tree is
unchanged.

Hash all regular files under the protected paths before and after delivery:

```text
reports/phase1_observation/
reports/phase1_tickflow_request_audit.json
reports/phase2_facts/
reports/phase2_claims/
reports/phase2_provider_canary/
reports/phase2_provider_ark/
reports/phase2_provider_relay/
reports/phase2_isolation_runtime/
reports/phase2_obsidian_preview/
```

Assert aggregate and per-file hashes are unchanged.

- [ ] **Step 2: Run delivery tests and verify RED**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_option_c_replay.py -q
```

Expected: failures identify the missing delivery service and CLI.

- [ ] **Step 3: Implement fail-closed publication**

Build three complete in-memory runs first, validate replay, then create a
sibling staging directory. Write every file with create-exclusive mode, flush,
fsync, and mode `0600`; fsync the staging directory; call the existing atomic
directory publisher; fsync the parent. The output index records protected
evidence counts and aggregate hashes, `tickflow_api_requests=0`,
`real_provider_attempts=0`, `real_ai_calls=0`, `broker_calls=0`, and
`cloud_deployments=0`.

- [ ] **Step 4: Verify the CLI is offline by construction**

```bash
rg -n 'httpx|requests|urllib|socket|phase2_.*provider|tickflow|app\.backtest' \
  backend/app/services/phase2_option_c_delivery.py \
  backend/scripts/build_phase2_option_c_offline.py
```

Expected: only literal audit-field text may mention `tickflow`; there are no
network, provider, broker, or backtest imports/calls.

- [ ] **Step 5: Run all Option C tests and commit**

```bash
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_option_c_fixture.py \
  tests/test_phase2_research_decision.py \
  tests/test_phase2_paper_trading.py \
  tests/test_phase2_chenquant_daily.py \
  tests/test_phase2_option_c_replay.py -q
```

Expected: all pass.

```bash
git add \
  backend/app/services/phase2_option_c_delivery.py \
  backend/scripts/build_phase2_option_c_offline.py \
  backend/tests/test_phase2_option_c_replay.py
git commit -m "feat: publish option c paper trading evidence"
```

---

### Task 7: Generate Evidence, Full Verification, Independent Review, And Closeout

**Files:**
- Generate: `reports/phase2_option_c/`
- Create: `reports/tickflow_phase2b_option_c_paper_trading_eval.md`

**Interfaces:**
- Consumes all previous tasks.
- Produces the final `PHASE2B_OPTION_C_PAPER_TRADING_READY` evidence and report.

- [ ] **Step 1: Record protected hashes and run the offline builder**

```bash
cd backend
PYTHONPATH=. .venv/bin/python scripts/build_phase2_option_c_offline.py \
  --repo-root .. \
  --reports-root ../reports
```

Expected: `PHASE2B_OPTION_C_PAPER_TRADING_READY`, three replays, no network or
Provider activity, and only the approved Option C report tree changes.

- [ ] **Step 2: Run the focused and integration suites**

```bash
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_option_c_fixture.py \
  tests/test_phase2_research_decision.py \
  tests/test_phase2_paper_trading.py \
  tests/test_phase2_chenquant_daily.py \
  tests/test_phase2_option_c_replay.py \
  tests/test_phase2_claims.py \
  tests/test_phase2_claims_renderer.py -q
```

Expected: all pass.

- [ ] **Step 3: Run backend regression and static checks**

```bash
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/python -m compileall -q app
.venv/bin/ruff check app --select F821
```

Expected: all pass.

- [ ] **Step 4: Rebuild and prove repository artifact idempotency**

Hash `reports/phase2_option_c/`, rerun the builder once, hash it again, and
require every file hash to match. Confirm protected evidence hashes remain
unchanged and no synthetic test fixture appears in reports.

- [ ] **Step 5: Run sensitive, trade-path, and diff checks**

```bash
rg -n 'Authorization:|Bearer |api[_-]?key\s*[:=]|Cookie:|BEGIN .*PRIVATE KEY' \
  reports/phase2_option_c \
  backend/app/schemas/phase2_option_c.py \
  backend/app/services/phase2_option_c_*.py \
  backend/app/services/phase2_research_decision.py \
  backend/app/services/phase2_paper_trading.py \
  backend/app/services/phase2_chenquant_daily.py \
  backend/scripts/build_phase2_option_c_offline.py

rg -n 'broker|place_order|submit_order|real[_-]?trade|app\.backtest' \
  backend/app/schemas/phase2_option_c.py \
  backend/app/services/phase2_option_c_*.py \
  backend/app/services/phase2_research_decision.py \
  backend/app/services/phase2_paper_trading.py \
  backend/app/services/phase2_chenquant_daily.py

git diff --check
```

Expected: no secret hit, no real order/backtest dependency, and no whitespace error.

- [ ] **Step 6: Perform independent focused review**

Review only the Option C diff for look-ahead bias, future leakage, Decimal/PnL
arithmetic, position accounting, duplicate execution, T+1, Claims-to-Action
separation, deterministic replay, absence of a real-order path, and Provider
independence. Required disposition: `NO P0/P1 ACTIONABLE FINDINGS`. Fix any
finding using a new failing test before implementation.

- [ ] **Step 7: Write the closeout report**

The report must explicitly record:

```text
Option A=EXHAUSTED
Option C=ACTIVE
Real Provider integration=DEFERRED / FROZEN
Paper Trading Engine=READY
Reference Fixture=VALID
Claims integration=PASSED
Research Decision Layer=PASSED
PnL=PASSED
Look-ahead guard=PASSED
Replay x3=PASSED
ChenQuant Daily=PASSED
Real Ark attempts=0
Real AI calls=0
Real trading=DISABLED
Historical evidence=UNCHANGED
Independent review=NO_P0_P1_FINDINGS
next_action=RUN_SINGLE_SYMBOL_PAPER_TRADING_VALIDATION
```

- [ ] **Step 8: Commit, push, and stop**

```bash
git add reports/phase2_option_c reports/tickflow_phase2b_option_c_paper_trading_eval.md
git commit -m "docs: record option c paper trading readiness"
git push fork codex/tickflow-phase2-ai-review
```

Verify local and fork heads match, the worktree is clean, and the final state is
`PHASE2B_OPTION_C_PAPER_TRADING_READY`. Do not start a three-symbol run, real
Provider request, main-system integration, or live Paper Trading operation.
