# Gold Stage A runbook (hardening)

## Purpose

Operate the **Gold-integrated** stock panel in Stage A observation with hard safety boundaries. This is **not** Stage B, not production Telegram ownership transfer, and not account-wide rate limiting.

## Network

- Compose publishes **only** `127.0.0.1:${PORT:-3018}:3018`.
- Do **not** bind `0.0.0.0:3018` or `[::]:3018`.
- Remote access: Tailscale, SSH tunnel, or a controlled reverse proxy — never a public Docker publish / Cloudflare public webhook for this app.

Verify:

```bash
docker compose config | grep -A2 ports
# expect: 127.0.0.1:…:3018
ss -lntp | grep 3018   # or: lsof -nP -iTCP:3018 -sTCP:LISTEN
```

## Feature flag

- Keep `GOLD_WORKSPACE_ENABLED=false` until Stage A checklist is green.
- Enabling Gold starts the 5‑minute sampler for fixed symbol `600489.SH` only.

## Single writer

- Gold data root uses `runtime.lock` (`flock`).
- Second process against the same root must fail closed at startup.
- Do not run multiple app workers as Gold writers.

## Storage safety

- Gold roots reject symlink components (root / parents / dangling).
- Writes must stay under the configured Gold root; outside-root creates = 0.

## TickFlow usage

- Gold TickFlow calls go through process-local `resolve_limit` + `sleep_between_batches` (80% safety factor).
- **Process-local Stage A throttling only** — not cross-container / account shared RPM.
- On first HTTP 429: stop current Gold pipeline; on second consecutive 429: trip circuit (`tickflow_circuit_open`).
- Do **not** run `scripts/probe_tickflow_pro.py --live` while Gold sampler is active.
- Prefer A-share Pro bulk sync **after 16:00 Asia/Shanghai** when Gold is observing.

## Forbidden

- Stage B, broker hooks, live trading, simulated trading module
- Migrating Telegram ownership / enabling Gold outbound
- Editing legacy `gold-monitor`
- Public 3018 / 5678, restoring public Cloudflare webhooks
- Putting real API keys in source, reports, or chat

## Health checks

```bash
curl -fsS http://127.0.0.1:3018/health
./scripts/verify_gold_stage_a.sh
```

## Status vocabulary

Use only:

- `STAGE_A_READY` — all P0 gates green
- `STAGE_A_BLOCKED` — any P0 gate red

Never: `LIVE_READY`, `PRODUCTION_READY`, `STAGE_B_READY`, `GLOBAL_RATE_LIMIT_READY`.
