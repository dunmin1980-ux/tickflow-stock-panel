# TickFlow Paper Trading MVP Delivery Sprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing deterministic Option C sidecar into a recoverable, daily-use, single-symbol Paper Trading MVP without adding any real provider or trading dependency.

**Architecture:** Preserve the existing strict Option C schemas, rule engine, Claims validator, and reference HOLD path. Add a small continuous-run layer that carries one `PaperAccount` across deterministic trading days, persists append-only decision/trade history atomically, and exposes one offline CLI that produces one JSON and Markdown ChenQuant Daily per day. Synthetic BUY/HOLD/SELL lifecycle data remains isolated as `DETERMINISTIC_TEST_FIXTURE`; the frozen `000403.SZ` reference run remains unchanged.

**Tech Stack:** Python 3.11, Pydantic 2 strict models, `Decimal`, repository-local JSON/Markdown files, pytest, Ruff.

**Spec:** `/Users/macbookpro/.codex/attachments/c446e5b6-0b77-459a-9602-d4158c8c04e4/pasted-text-1.txt`

## Global Constraints

- Remain outside the main API and UI.
- Do not call TickFlow, Ark, OpenAI, a broker, or any public network endpoint.
- Do not import the batch backtest engine.
- Keep the production-facing symbol scope fixed to `000403.SZ`.
- Preserve `FACT -> INTERPRETATION -> SIGNAL -> ACTION` and generate actions only through deterministic rules.
- Use `Decimal` for all money and integer quantities; buys remain 100-share board lots.
- Enforce A-share T+1 and exact next-available-trading-day open execution.
- Reject future Facts, Projection, Signal, market data, Position, or PnL.
- The frozen reference run may remain `MIXED_OBSERVATION -> HOLD`; HOLD creates no order, trade, fill, or execution price.
- BUY/HOLD/SELL lifecycle evidence uses only `DETERMINISTIC_TEST_FIXTURE` and is never mixed with formal reference artifacts.
- Every output remains `SIMULATION ONLY`, `can_publish=false`, `trading_advice=false`, and real trading disabled.
- Fix only P0/P1 blockers; record P2 findings as technical debt without expanding scope.

---

### Task 1: Continuous Multi-Day Contracts And Lifecycle

**Files:**
- Modify: `backend/app/schemas/phase2_option_c.py`
- Create: `backend/app/services/phase2_option_c_continuous.py`
- Create: `backend/tests/test_phase2_option_c_continuous.py`

**Interfaces:**
- Produces: strict daily input, pending action, decision ledger, equity point, and continuous account state contracts.
- Produces: a pure `run_continuous_day(...)` transition that consumes prior state plus one day of deterministic inputs and returns a new state and daily result.

- [ ] Write RED tests for BUY -> HOLD -> SELL, prior-state carry-forward, append-only ledgers, T+1, next-day open, realized/unrealized PnL, full exit, and all future-data guards.
- [ ] Run the focused test and confirm expected failures are caused by missing continuous contracts.
- [ ] Implement the minimal strict models and pure transition layer using the existing Paper Trading engine.
- [ ] Run focused Option C compatibility tests and confirm GREEN.

### Task 2: Atomic State Store And Recovery

**Files:**
- Create: `backend/app/services/phase2_option_c_state.py`
- Create: `backend/tests/test_phase2_option_c_state.py`

**Interfaces:**
- Produces: `load_or_initialize_state(...)` and `publish_daily_state(...)` with regular-file, no-symlink, mode-0600, staging/fsync/atomic-rename semantics.
- Persists account, lots, trade ledger, decision ledger, equity history, input identities, and per-day report artifacts.

- [ ] Write RED tests for first initialization, same-day idempotency, interrupted write recovery, previous-state preservation, append-only history, and symlink rejection.
- [ ] Implement minimal atomic state publication without overwriting historical day directories.
- [ ] Prove repeated same-day input is a no-op and conflicting same-day identities fail closed.

### Task 3: Daily Runner And ChenQuant Daily MVP Output

**Files:**
- Modify: `backend/app/schemas/phase2_option_c.py`
- Modify: `backend/app/services/phase2_chenquant_daily.py`
- Create: `backend/app/services/phase2_option_c_daily.py`
- Create: `backend/scripts/run_phase2_option_c_daily.py`
- Create: `backend/tests/test_phase2_option_c_daily.py`

**Interfaces:**
- Produces: one offline CLI command that loads state, validates deterministic inputs, runs one day, marks to market, persists state, and writes JSON/Markdown.
- Daily output includes identities, signal/action/pending action, position, sellable quantity, cash, cost basis, market value, realized/unrealized PnL, equity, cumulative return, drawdown, trades today, risk notes, and next observation conditions.

- [ ] Write RED tests for reference HOLD, synthetic lifecycle days, duplicate-day no-op, missing execution price, crash recovery, and CLI output contract.
- [ ] Implement the minimal daily orchestrator and deterministic renderer.
- [ ] Verify no Provider, broker, network, main API, or UI imports enter the runtime path.

### Task 4: Release Replay, Runbook, Evidence, And Closeout

**Files:**
- Create: `backend/tests/test_phase2_option_c_release_replay.py`
- Create: `backend/scripts/verify_phase2_option_c_mvp.py`
- Create: `TICKFLOW_PAPER_TRADING_MVP_RUNBOOK.md`
- Create: `reports/phase2_option_c_mvp/`
- Create: `reports/tickflow_paper_trading_mvp_release_eval.md`

**Interfaces:**
- Produces: byte-identical continuous release artifacts across three clean replays and one release SHA.
- Produces: machine-readable release evidence and an executable daily-use runbook.

- [ ] Write RED tests that compare decision ledger, trade ledger, positions, cash, PnL, equity history, all Daily JSON/Markdown bytes, and final release SHA across three clean runs.
- [ ] Generate the formal single-symbol reference validation and isolated deterministic lifecycle evidence.
- [ ] Run Option C focused tests, single-symbol validation, lifecycle/T+1/look-ahead/PnL/idempotency/recovery/Daily tests, replay x3, compileall, Ruff F821, and `git diff --check`.
- [ ] Perform an independent focused P0/P1 review and fix only actionable blockers.
- [ ] Record remaining P2 technical debt without implementing it.
- [ ] Generate release report/evidence, commit, push, and confirm local/fork matched and worktree clean.
