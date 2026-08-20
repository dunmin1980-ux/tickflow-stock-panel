# TickFlow Paper Trading MVP Release

- Release status: `TICKFLOW_PAPER_TRADING_MVP_RELEASED`
- Release date: `2026-08-20`
- Validated source Git Head: `a14e068b43293a86b8e8f1ae5e04ec748d7869e1`
- Release SHA: `eeb0ed2718198bdf2f78cc969eae81a6f48006c2fe5de3ec496d3bc7c6becae2`
- Runbook: `TICKFLOW_PAPER_TRADING_MVP_RUNBOOK.md`
- Verification: `reports/phase2_option_c_mvp/release_verification.json`

## Supported Scope

Single-symbol `000403.SZ` Paper Trading with deterministic Facts and Claims validation, research decision rules, BUY/HOLD/SELL simulation, A-share T+1, next-trading-day-open execution, Decimal ledgers, positions, realized and unrealized PnL, equity, max drawdown, idempotency, state recovery, look-ahead guards, ChenQuant Daily JSON/Markdown, Daily Runner, deterministic replay, and `SIMULATION ONLY` safety controls.

## Known Limitations

- Real Provider integration: `DEFERRED`
- Real AI output: `NOT_REQUIRED_FOR_MVP`
- Real Trading: `DISABLED`
- Broker API: `NOT_CONNECTED`
- Three-symbol batch: `NOT_RELEASED`
- Main API/UI: `NOT_RELEASED`
- Cloud deployment: `NOT_RELEASED`
- Automatic publishing: `DISABLED`
- P2 technical debt: `3 / DEFERRED`
