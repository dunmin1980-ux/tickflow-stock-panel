# TickFlow Visual Workbench v1 Implementation Plan

1. Add failing backend contracts for dashboard aggregation, reference
   initialization, validated daily input preparation, idempotency, recovery,
   tamper rejection, corporate-action fail-closed behavior, and API errors.
2. Implement the single-symbol raw/qfq/calendar builder plus the thin visual
   service and authenticated API router without changing Option C accounting.
3. Add failing frontend contracts for KPI rendering, safety labels, daily run,
   refresh persistence, empty ledgers, and friendly errors.
4. Implement the Paper Trading page, typed API client, query key, root route,
   desktop/mobile navigation, and ECharts equity view.
5. Add a local-only startup script and double-click command with health reuse,
   process lock, browser open, and no-kill behavior.
6. Add mocked Playwright flows for dashboard, daily run, persistence, duplicate
   click protection, and mobile layout.
7. Run focused and full verification, inspect rendered desktop/mobile screens,
   perform a separate P0/P1 review, and fix only actionable release blockers.
8. Write the runbook, release evaluation, and lightweight marker; commit, push,
   tag, and verify a clean local/fork match.
