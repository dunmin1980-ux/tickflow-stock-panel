# TickFlow Workbench / Account UX Split Released

```text
release_status=TICKFLOW_WORKBENCH_ACCOUNT_UX_SPLIT_RELEASED
release_date=2026-08-25
implementation_base_head=ec6c6d1280db3411f642b16ed4cced1129a606b2
today_workbench=/paper-trading
paper_account=/paper-account
shared_dashboard_api=/api/paper-trading/dashboard
paper_trading_engine=UNCHANGED
simulation_only=SIMULATION ONLY
real_trading=DISABLED
provider_calls=0
```

The Today Workbench now presents only today's deterministic research workflow
and remains the sole one-click run surface. The Paper Account presents the
persisted account lifecycle, positions, decisions, trades, equity and PnL with
no execution control. Both routes use the same released Option C state.

Runbook: `TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md`
