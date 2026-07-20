# TickFlow Private Mobile PWA Phase 1 Acceptance

## Result

Status: `CONDITIONAL_READY`

The installable PWA, responsive shell, offline read-only policy, secure-cookie backend, cloud sidecar, and tailnet-only HTTPS entry are deployed and automated tests pass. Authenticated live-device acceptance is intentionally pending because no access password existed and the user must choose it without placing it in chat or source control.

## Deployment

- URL: `https://vm-0-9-ubuntu.tail21c236.ts.net:8443/`
- Source branch: `codex/tickflow-multiclient-app`
- Cloud source at evidence capture: `7012e1c` (clean full source build)
- Runtime: Docker sidecar on `127.0.0.1:3019`
- Existing `443 -> 127.0.0.1:8787`: preserved
- New `8443 -> 127.0.0.1:3019`: tailnet only
- `AUTH_COOKIE_SECURE=true`
- `GOLD_WORKSPACE_ENABLED=false`
- Rollback image: `tickflow-stock-panel-stage-a-app:rollback-pre-pwa-20260720`
- Active image ID: `sha256:cb4068946c893fcc04b0f5093c194256dafe3b8f14be34b0036f42e7bad2b194`
- Immediate PWA rollback: `tickflow-stock-panel-stage-a-app:rollback-pwa-incremental-20260721`

## Automated Verification

| Gate | Result |
|---|---|
| Frontend Vitest | 37 passed |
| Production build | Passed; manifest and Service Worker generated |
| Playwright | 24 passed across iPhone 14, Pixel 7, and 1440x900 desktop |
| Responsive route screenshots | 18 captured |
| Secure-cookie backend test | 1 passed |
| Private access script | `PWA_PRIVATE_ACCESS_OK` |
| Service Worker API route scan | Zero `/api/` runtime-cache match |
| Frontend secret marker scan | Zero match |
| Desktop `.ico/.icns` hashes | Unchanged |

The first combined E2E command encountered an orphaned test preview process on port 4173. After terminating that process, the same complete Playwright suite passed 24/24. This was test infrastructure cleanup, not an application failure.

## Offline And Security Behavior

- Service Worker precaches only the application shell; runtime API caching is empty.
- IndexedDB snapshots use a same-origin allowlist and preserve query identity.
- Auth, settings, webhook, alert stream, live quote, analysis trigger, and report-body routes are not persistently cached.
- Offline writes fail before `fetch` and are not queued.
- A browser-session grant is required for snapshot reads; logout, 401, password change, and unauthenticated status revoke it and clear TickFlow snapshots.
- In-flight reads cannot restore access after revocation.
- HTTP 4xx/5xx and parse errors are never hidden by cached data.
- Full review Markdown bodies remain online-only with `no-store`.

## Screenshot Index

Each directory contains `watchlist.png`, `stock-analysis.png`, `review.png`, `concept-analysis.png`, `industry-analysis.png`, and `data.png`.

- `reports/pwa_acceptance/screenshots/iphone14/`
- `reports/pwa_acceptance/screenshots/pixel7/`
- `reports/pwa_acceptance/screenshots/desktop/`

## Pending Manual Gate

1. Create the first-use access password through an SSH loopback tunnel as documented in `docs/pwa-private-runbook.md`. Do not send the password in chat.
2. On a physical iPhone and Android device joined to the tailnet: log in, install to the home screen, open Watchlist/Stock Analysis/Review, verify offline-readonly display and blocked writes, then reconnect and confirm refresh.
3. A live authenticated `Set-Cookie` header must be checked for `HttpOnly`, `SameSite=lax`, and `Secure` after password setup. The backend contract test already passes, but live evidence cannot be produced before initialization.

Until these steps are complete, do not label Phase 1 as physical-device accepted and do not connect it to the main system, Telegram, OpenClaw, broker APIs, or automatic publishing.
