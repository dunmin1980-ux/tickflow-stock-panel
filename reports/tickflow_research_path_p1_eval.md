# TickFlow Research Path P1 Closeout

Date: 2026-10-08 (Asia/Shanghai)

Status: `TICKFLOW_RESEARCH_PATH_P1_FIXED`

## Root Cause And Scope

The installed desktop launcher used an iCloud `Documents` symlink ancestor.
Its unresolved absolute `DATA_DIR` reached research readback, whose strict
ancestor-symlink check raised `research_paper_daily_invalid`. Identical data
passed when addressed by its physical path.

Only startup normalization, regression tests, and documentation changed.
`backend/app/**`, frontend sources, research/strategy thresholds, Paper Trading,
and the existing research safety check are unchanged.

- Project root: `/Users/macbookpro/Documents/文稿 - macbook的MacBook Pro (2)/TradingView 量化/tickflow-phase2-ai-review`
- Data root: project root + `/backend/data`
- Research Daily root: data root + `/user_data/phase2_option_c_paper/reference_account/days`
- Desktop entry: `/Users/macbookpro/Desktop/TickFlow.app`
- Installed launcher: `/Users/macbookpro/Library/Application Support/TickFlowLauncher/start_tickflow.command`
- Local launcher backup: same directory, `start_tickflow.command.pre-research-path-p1-20261008`
- Backup SHA-256: `4ddd06d0d2f928a653a502f1365538c7af606f4d415185637514aaf24006faa7`

The launcher now resolves project/data root aliases before startup side effects.
Containment uses `Path.resolve()` and `Path.relative_to()`, not string prefixes.
Default data stays within the physical project; an explicit `DATA_DIR` remains
a user-selected allowed root. Interior research/runtime aliases still fail closed,
including aliases escaping that data root. No backend safety exception was added.

## Verification

| Check | Result |
|---|---|
| RED before implementation | 10 expected failures, 2 passes |
| Path regression tests, including real fixture readback | 14 passed |
| Related backend/launcher suite | 111 passed |
| Bash syntax / Ruff F821 / diff whitespace | PASSED |
| Python compile check without bytecode writes | 223 files passed |
| Actual desktop App restart | PASSED, old PID 6865 replaced by PID 11889 |
| Backend health / dashboard | HTTP 200 / `visual_provider_deferred` |
| 2026-10-08 Research Daily / six strategies | READY / 6 |
| Strategy Graphics | READY, 30 dates, 190 overlay records |
| Desktop/mobile real-browser checks | 48 passed, 20 screenshots captured |
| K-line, overlay, Volume, MACD, RSI | Nonblank canvases; filters, click/tap, date linkage and refresh passed |
| New runtime log `research_paper_daily_invalid` occurrences | 0 |
| Account | cash/equity CNY 100000.00; positions 0; trades 0; PnL 0 |
| Existing published records | 28, unchanged |
| Input/account evidence files | 527 hashes unchanged |
| Provider / market / AI calls in this remediation | 0 / 0 / 0 |
| Real trades | 0; real trading remains DISABLED |
| Independent focused review | NO_P0_P1_FINDINGS |

No Daily or synchronization endpoint was executed. Browser verification allowed
only loopback GET/HEAD requests. Existing deferred-mode startup skipped provider,
depth, custom-source, and synchronization schedulers. No credentials were inspected.

Combined SHA-256 of the sorted evidence path/hash pairs, unchanged before/after:
`642f632829df394e32b96568069f79ab08ec1368b858968b3eb5878928444b93`

Evidence: [UI checks](research_path_p1/ui-verification.json),
[desktop](research_path_p1/desktop-research.png),
[mobile](research_path_p1/mobile-research.png).

## Deferred P2

- Git worktree metadata still references the pre-migration path. This does not
  affect runtime; this change uses explicit existing Git metadata, without repair.
- Independent review found that an explicitly configured missing data root below
  a regular file passes dry-run; actual startup still fails at mkdir before HTTP.
  No containment escape occurs. Defer this invalid-configuration UX improvement.
- Relocated venv console-script shebangs still reference the old path. Verification
  used the existing interpreter with `python -m pytest` and `python -m ruff`.

Baseline: `df01c40ceeedcbcddfaf282a116ec6f49602349c`.
Delivery branch: `codex/tickflow-visual-workbench-v1`; no merge or cloud deployment.
