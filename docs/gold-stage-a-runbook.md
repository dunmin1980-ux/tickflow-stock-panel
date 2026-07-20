# Gold Stage A runbook (hardening)

## Purpose

Operate the **Gold-integrated** stock panel in Stage A observation with hard safety boundaries. This is **not** Stage B, not production Telegram ownership transfer, and not account-wide rate limiting.

## Network

- Compose publishes **only** `127.0.0.1:${PORT:-3018}:3018`.
- Do **not** bind the selected host port to `0.0.0.0` or `[::]`.
- Remote access: Tailscale, SSH tunnel, or a controlled reverse proxy — never a public Docker publish / Cloudflare public webhook for this app.
- When standalone `TickFlow_Gold_Shadow` already owns host port `3018`, use
  `PORT=3019` for the Gold-disabled integrated sidecar. Container port remains `3018`.

Verify:

```bash
cp .env.example .env   # only when a local .env does not already exist
chmod 600 .env
./scripts/verify_gold_stage_a.sh
# expect: resolved Compose loopback check + 34 passed + STAGE_A_READY
ss -lntp | grep "${PORT:-3018}"   # or use lsof for the selected host port
```

The verifier requires Docker Compose, a valid local `.env`, and the current workspace `backend/.venv`. It must return `STAGE_A_BLOCKED` rather than falling back to a raw YAML grep when Compose cannot be parsed.

## Feature flag

- Keep `GOLD_WORKSPACE_ENABLED=false` until Stage A checklist is green.
- Enabling Gold starts the 5‑minute sampler for fixed symbol `600489.SH` only.

## Lighthouse sidecar commands

Current Gold-disabled deployment:

```bash
ssh codex-vm
cd ~/tickflow-stock-panel-stage-a
docker compose ps
curl -fsS http://127.0.0.1:3019/health
curl -fsS http://127.0.0.1:3019/api/gold/status
# required: enabled=false and external_send_count=0
```

Manual access from the Mac:

```bash
ssh -N -L 3019:127.0.0.1:3019 codex-vm
# then open http://127.0.0.1:3019
```

Stop/restart only the integrated sidecar:

```bash
cd ~/tickflow-stock-panel-stage-a
docker compose stop app
docker compose up -d --no-build app
```

Do not stop or recreate `TickFlow_Gold_Shadow` from this directory.

## Lighthouse build mirror workaround

In v0.1.86, `backend/uv.lock` contains direct Tsinghua wheel URLs. Docker build
arguments alone do not override those frozen URLs. If those downloads are slow,
use this build-only substitution. Hashes remain enforced, and the trap restores the
checkout before the command exits.

```bash
cd ~/tickflow-stock-panel-stage-a
git diff --quiet -- backend/uv.lock

cleanup() { git checkout -- backend/uv.lock; }
trap cleanup EXIT INT TERM

sed -i \
  -e 's#https://pypi.tuna.tsinghua.edu.cn/packages/#https://files.pythonhosted.org/packages/#g' \
  -e 's#registry = "https://pypi.tuna.tsinghua.edu.cn/simple"#registry = "https://pypi.org/simple"#g' \
  backend/uv.lock

docker compose build \
  --build-arg PYPI_INDEX=https://pypi.org/simple \
  --build-arg PYPI_FALLBACK=https://pypi.org/simple
docker compose up -d --no-build

cleanup
trap - EXIT INT TERM
git diff --quiet -- backend/uv.lock
```

This is a deployment workaround, not the P1 lock-source fix.

## Single writer

- Gold data root uses `runtime.lock` (`flock`).
- Second process against the same root must fail closed at startup.
- Do not run multiple app workers as Gold writers.

## Storage safety

- Gold roots reject symlink components (root / parents / dangling).
- Top-level state, JSONL, atomic temporary files, `imports/`, and `comparison_runs/` use descriptor-relative I/O with no-follow checks.
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
