# TickFlow Phase 2B Option C Deterministic Paper Trading Design

## Status And Authority

This design implements the user-approved Option C path. It replaces further
real-provider work with a deterministic, offline research workflow:

```text
Frozen Facts / validated Claims
-> deterministic Interpretation
-> deterministic Signal
-> deterministic Action
-> A-share T+1 Paper Trading ledger
-> PnL
-> ChenQuant Daily
```

The target state is `PHASE2B_OPTION_C_PAPER_TRADING_READY`. This is an
engineering-readiness state for a single-symbol simulation sidecar, not a
production, trading, publication, or investment-advice approval.

Option A is exhausted. Real-provider integration remains
`REAL_PROVIDER_INTEGRATION=DEFERRED`; all Ark/OpenAI runtime paths, approvals,
ledgers, and historical evidence remain frozen and are not prerequisites for
Option C.

## Scope And Boundaries

The implementation is an independent offline module. It is not wired into the
main API, UI, cloud deployment, Obsidian Vault, Telegram, OpenClaw, Integrated
Gold, brokerage, or any live-trading path. It does not reuse the existing
batch backtest engine as the Paper Trading ledger.

The only business symbol is `000403.SZ` (Pai Lin Bio) at the frozen Facts trade
date `2026-07-31`. No new TickFlow or provider request is allowed. The frozen
sample may legitimately produce only `HOLD`; the implementation must not
invent a future market bar or alter real evidence to demonstrate `BUY` or
`SELL`.

`BUY` and `SELL` closure is tested only with files explicitly marked
`DETERMINISTIC_TEST_FIXTURE`. Those fixtures are synthetic test inputs, never
historical observations or publishable research artifacts.

Every generated artifact must contain `SIMULATION ONLY` and
`can_publish=false`. No artifact may present a recommendation or authorize an
order.

## Selected Architecture

The implementation is split into five responsibilities:

- `app.services.phase2_option_c_fixture` loads the frozen Facts and Claims,
  verifies their identities, and constructs the deterministic reference
  fixture.
- `app.services.phase2_research_decision` enforces the
  `FACT -> INTERPRETATION -> SIGNAL -> ACTION` boundary and owns closed
  interpretation and signal registries.
- `app.services.phase2_paper_trading` owns the standalone account state,
  deterministic rule engine, A-share execution constraints, immutable trade
  ledger, positions, cash, PnL, equity, and drawdown.
- `app.services.phase2_chenquant_daily` renders deterministic JSON and
  Markdown summaries from validated snapshots only.
- `scripts/build_phase2_option_c_offline.py` is the offline orchestration entry
  point. It performs no network access and publishes only under
  `reports/phase2_option_c/`.

The planned test files are:

```text
backend/tests/test_phase2_option_c_fixture.py
backend/tests/test_phase2_research_decision.py
backend/tests/test_phase2_paper_trading.py
backend/tests/test_phase2_chenquant_daily.py
backend/tests/test_phase2_option_c_replay.py
```

No module imports the batch backtest engine, provider launcher, provider relay,
or web application router.

## Deterministic Reference Fixture

`reference_typed_claims.json` is a strict non-publishable envelope with
`source=DETERMINISTIC_REFERENCE_FIXTURE`. Its nested `claims_document` is the
exact validated `ClaimsDocument` and keeps the schema-owned
`source_system=tickflow-stock-panel`. A separate closed
`reference_fixture_manifest.json` binds:

- source label `DETERMINISTIC_REFERENCE_FIXTURE`;
- symbol, name, and trade date;
- exact Facts SHA-256;
- exact minimal Projection SHA-256;
- exact Typed Claims Schema SHA-256;
- exact nested canonical `ClaimsDocument` SHA-256; the delivery evidence index
  separately binds the envelope file SHA-256;
- fixed source paths relative to the repository;
- the exact Claims artifact safety state `SIMULATION ONLY`,
  `can_publish=false`, and `trading_advice=false` without changing the frozen
  Typed Claims Schema;
- the four existing vendor-pending items.

Neither envelope nor manifest claims to be an AI result. The source label stays
outside the nested strict Claims schema, and the envelope records the exact
canonical ClaimsDocument SHA-256. The host re-runs the current Typed Claims
Validator before any interpretation. A fixture identity mismatch,
invalid Claim, unexpected symbol, unexpected trade date, sensitive content,
forbidden trading claim, non-finite number, or raw/qfq mismatch blocks the
entire decision.

The frozen business fixture maps the current evidence to:

```text
interpretation=MIXED_TECHNICAL_STRUCTURE
signal=MIXED_OBSERVATION
action=HOLD
```

This mapping reflects the simultaneous positive MACD structure, elevated
short-period RSI, non-bullish MA ordering, incomplete market scope, and pending
vendor semantics. It does not forecast price or manufacture a trading signal.

## Layer Contracts

Each stage has a closed typed output and may only consume the immediately
preceding validated stage.

### Fact

Facts are immutable Phase 2A values with source path, source SHA-256,
calculation identity, unit, and price basis. No later stage may mutate, infer,
or silently replace a Fact.

### Interpretation

Interpretations are registered classifications derived from validated Claims.
They may describe evidence states such as `MIXED_TECHNICAL_STRUCTURE`, but may
not contain an action, order quantity, execution price, target, stop, or free
text instruction.

### Signal

Signals are closed research states: `POSITIVE_OBSERVATION`,
`MIXED_OBSERVATION`, `RISK_OBSERVATION`, or `INVALID`. A signal contains reason
references to validated Claim IDs and immutable snapshot identities. It is not
an order.

### Action

Actions are only `BUY`, `HOLD`, or `SELL`. They are produced exclusively by a
deterministic rule engine from a valid signal plus a portfolio snapshot and an
immutable Paper Trading configuration. Free text, AI output, and market prices
are never accepted as action inputs. A separate execution step resolves an
executable action against the closed deterministic Market Fixture.

The minimum deterministic action rules are:

- `MIXED_OBSERVATION` or `INVALID` -> `HOLD`;
- `POSITIVE_OBSERVATION` with no position and sufficient cash -> `BUY`;
- `RISK_OBSERVATION` with T+1-eligible shares -> `SELL`;
- any otherwise ineligible state -> `HOLD` with a closed reason code.

The positive/risk paths exist for deterministic test fixtures only in this
phase. The frozen business fixture is not modified to reach them.

## Paper Trading Account Contract

The Paper Trading engine is a new append-only ledger, independent of the
backtest engine. It stores:

- immutable account configuration;
- cash ledger;
- position lots and acquisition trade dates;
- explicit `sellable_from_trade_date` per lot;
- processed decision, order, action, and idempotency identities;
- execution records;
- total fees, sell-side cost, and allocated cost basis per trade;
- realized and unrealized PnL;
- equity and peak equity;
- drawdown;
- Facts, Claims, Signal, and rule identities.

All monetary calculations use Python `Decimal`. Prices, fees, cash, equity,
PnL, and drawdown amounts are quantized to CNY `0.01` with
`ROUND_HALF_UP`. Quantities are integers. Buy quantities must be positive
multiples of 100 shares. Sell quantities must be positive integers and may not
exceed T+1-eligible holdings. No fractional shares are accepted.

The readiness configuration is explicit simulation metadata, not a statement
of a current broker tariff:

```text
initial_cash=100000.00
lot_size=100
commission_rate=0.0003
minimum_commission=5.00
sell_fee_rate=0.0005
t_plus_one=true
execution_policy=NEXT_TRADING_DAY_OPEN
slippage_rate=0
currency=CNY
```

Commission applies to both sides with the fixed minimum. The explicit
test-only sell fee applies to sell executions only; these values are not
represented as a broker tariff. Tests bind this configuration by canonical
JSON hash.

## A-Share T+1 And Execution Timing

A decision is timestamped in `Asia/Shanghai` and can only use evidence whose
`as_of` value is not later than the decision time. An action generated from
that decision is resolved against an ordered, unique deterministic Market
Fixture calendar. The Fixture carries a content-derived identity over its
calendar, bars, prices, source, and safety state; the engine recomputes this
identity before execution and records it in each Trade. It may execute only on
the exact next available trade date, at that date's validated `open`; a missing
exact-next-date bar raises
`NO_EXECUTION_PRICE_AVAILABLE` and the engine never skips to a later bar.

The engine rejects:

- a decision date absent from the Fixture calendar;
- a missing exact next-trading-date open bar;
- a symbol, source, raw price basis, or Fixture identity mismatch;
- a future close used as an execution price;
- any price not traceable to the selected bar's `open` field;
- a sell of shares acquired on the same trade date;
- unavailable, non-finite, non-positive, or basis-ambiguous prices;
- hidden gap filling or synthetic replacement of missing market data.

Each acquired lot records the exact next Fixture trade date as
`sellable_from_trade_date`; eligibility uses that field rather than calendar-day
arithmetic. The frozen `2026-07-31` business sample produces `HOLD`, so it needs
no market Fixture. Synthetic BUY/SELL tests use explicitly labeled closed
calendars and next-trading-day open bars outside the business artifact route.

## Idempotency And Atomic Publication

Each decision has a canonical identity derived from signal identity, Fixture
identity, decision time, immutable configuration, and the pre-decision account
snapshot. The separate idempotency key binds symbol, decision date, signal
identity, action side and quantity, Fixture identity, and Paper configuration
identity. `order_id` is derived from that key. Duplicate decision, order,
action, or idempotency identity is rejected before ledger mutation.

State transitions are pure: valid input state plus one event yields a new state
and an append-only event. Publication writes to a sibling staging directory,
fsyncs files, atomically renames the complete directory, and fsyncs the parent.
Failure must not leave a passed status or a partially published ledger.

Replay x3 starts from the same initial snapshot and inputs. All three runs must
produce byte-identical canonical JSON, Markdown, trade ledger, positions, cash,
PnL, equity, drawdown, and artifact hashes. Timestamps in deterministic
artifacts are supplied by the fixture, not the wall clock.

## ChenQuant Daily Output

The deterministic daily report includes:

- `SIMULATION ONLY` and `can_publish=false`;
- symbol and decision date;
- Facts, Projection, Claims, fixture, signal, rule, and account-config hashes;
- interpretation, signal, action, and closed reason references;
- pending/executed state and next-open execution rule;
- cash, eligible/ineligible shares, market value, realized/unrealized PnL,
  equity, peak equity, and drawdown;
- append-only ledger references;
- T+1, data-scope, and vendor-pending notices;
- a fixed statement that the output is not investment advice.

Markdown is rendered from the canonical validated JSON. It accepts no
free-form model text, links, HTML, or executable content. Output ordering,
formatting, Decimal serialization, and line endings are fixed.

The artifact tree is:

```text
reports/phase2_option_c/
  reference_typed_claims.json
  reference_fixture_manifest.json
  account_config.json
  decision.json
  paper_account.json
  trade_ledger.json
  chenquant_daily.json
  chenquant_daily.md
  replay_validation.json
  evidence_index.json
```

Only the business `HOLD` readiness result is published here. Synthetic
BUY/SELL fixtures stay under backend test fixtures or temporary test paths and
must not appear in the report tree.

## Failure Semantics

The workflow fails closed. Invalid or mismatched Facts/Claims produce no signal
or action. Invalid time, quantity, cash, position, T+1 eligibility, execution
bar, fee, duplicate event, hash, or publication state produces no ledger
mutation. A rejected event is recorded only in a test-local diagnostic object,
not in a passed business ledger.

No fallback may:

- call an AI provider;
- use free text as a signal or action;
- replace a missing next-open price;
- relax T+1;
- round a quantity into validity;
- change source market evidence;
- reuse the backtest engine;
- publish to the main application or a real Vault.

## Verification Strategy

Implementation follows TDD. Focused tests cover:

1. reference fixture identity, Claims validation, source labeling, and HOLD;
2. strict Fact/Interpretation/Signal/Action boundaries;
3. initial cash and empty positions;
4. deterministic BUY, HOLD, SELL, partial exit, and full exit fixtures;
5. insufficient cash and invalid quantities;
6. 100-share buy lots and integer sells;
7. commission, minimum commission, sell stamp tax, and Decimal rounding;
8. realized/unrealized PnL, equity, peak equity, and drawdown;
9. duplicate decision and execution rejection;
10. T+1 same-day sell rejection and next-day eligible sell;
11. next-trading-day open execution and rejection of close/future data;
12. invalid Claims, fixture mismatch, raw/qfq mismatch, and sensitive fields;
13. atomic publication failure behavior;
14. deterministic JSON and Markdown;
15. replay x3 byte and hash identity;
16. no runtime imports from API/UI/backtest/provider modules;
17. historical provider evidence and request audits unchanged;
18. zero network, provider, AI, broker, or real-trading calls.

Final verification runs the focused suites, Claims integration tests, all
backend tests, compileall, Ruff F821, offline build twice, replay x3, diff
checks, sensitive-pattern scans, and before/after hashes of protected evidence.
An independent focused review must report `NO P0/P1 ACTIONABLE FINDINGS`.

## Completion Gate

The final state is `PHASE2B_OPTION_C_PAPER_TRADING_READY` only when:

- the frozen reference fixture validates and yields deterministic `HOLD`;
- synthetic test fixtures close BUY/HOLD/SELL without entering report output;
- T+1 and next-trading-day-open rules pass;
- all monetary and position calculations are deterministic;
- Replay x3 is byte-identical;
- ChenQuant Daily JSON and Markdown are valid and non-publishable;
- focused and backend test suites pass;
- historical evidence is unchanged;
- real provider attempts, real AI calls, TickFlow calls, brokerage calls, and
  cloud deployments are all zero;
- independent review has no P0/P1 actionable findings.

At that state the work stops. Three-symbol execution, main-system integration,
Paper Trading operation, real Provider work, and publication require separate
approval.
