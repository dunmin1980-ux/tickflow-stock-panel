# Gold Stage A hardening evaluation

- **Date:** 2026-07-20 (Asia/Shanghai)
- **Branch:** `cursor/gold-stage-a-hardening-v0.1.86`
- **Base commit:** `3a171366055153a6a7329a3c094b3e4a507088d1`
- **New tip:** `75e1ede`
- **Workspace:** `tickflow-gold-stage-a-hardening-v0.1.86`
- **Final status:** `STAGE_A_READY` (local verify script; see caveats)

## 1. Baseline

| Item | Value |
|---|---|
| Original package | v0.1.86 / `3a17136` / base `4315508` (PR #133) |
| Hardening branch | `cursor/gold-stage-a-hardening-v0.1.86` |
| Python test runner | sibling venv `tickflow-stock-panel-v0.1.84/backend/.venv` |
| Environment | macOS local; docker compose config binary optional |

## 2. P0 completion

| P0 | Status | Notes |
|---|---|---|
| P0-1 localhost 3018 | **done** | `docker-compose.yml` → `127.0.0.1:${PORT:-3018}:3018` + comments |
| P0-2 symlink escape | **done** | `gold_path_security.py` + store wiring; tests cover root/parent/dangling symlink |
| P0-3 runtime lock | **done** | `gold_runtime_lock.py` flock; acquired in `main.py` lifespan |
| P0-4 429 evidence | **done** | `gold_tickflow_errors.py` classify + `RateLimitCircuit`; Gold gateway maps/records |
| P0-5 request audit | **done** | `docs/tickflow-request-path-audit.md` |
| P0-6 SDK HTTP count | **partial→documented** | Simulated transport tests + `docs/tickflow-sdk-http-budget.md`; **does not** claim real HTTP 80% RPM |

## 3. Tests

Local `./scripts/verify_gold_stage_a.sh`:

- security + rate-limit + sdk-budget suite: **30 passed**
- See `reports/gold_stage_a_verify_run.txt`

Not claimed in this eval:

- Full backend 757 suite re-run after hardening
- Frontend `pnpm test:gold`
- Live `ss`/`lsof` on a running compose stack (file-level bind verified)

## 4. Security validation

See `reports/gold_stage_a_security_validation.json`.

- Heuristic secret scan: OK (placeholders filtered)
- Outside-root writes: covered by path-security tests (assert reject)
- Compose bind: loopback in YAML
- Gold outbound / Telegram: unchanged zero-send Stage A policy (no ownership migration)

## 5. Rate-limit boundary (mandatory wording)

```text
当前是进程内 Stage A 限频
不是跨容器账户级限频
不是 Gold + A股 + legacy 的全局共享限频
```

## 6. Final status

```text
STAGE_A_READY
```

**Caveats before cloud enablement:**

1. Re-run verify on the Lighthouse host after `docker compose up` and confirm `127.0.0.1:3018` via `ss`/`lsof`.
2. Keep `GOLD_WORKSPACE_ENABLED=false` until host bind + lock + holidays are confirmed.
3. Do not run live Pro probe concurrently with Gold sampler.
4. P1 (CI / version unify to 0.1.87 / frozen locks / credential redaction) still recommended before merge to upstream.

## 7. Connection-failure note

Background agents ([Write Stage A audit docs](502952c7-03fd-4207-8266-b0c42a6af177), [Gold Stage A hardening](09ac5a42-9163-4a71-9dc6-e353dd36b1a4)) failed with connection errors; work continued in the parent session. No reset/discard was performed; WIP was preserved.
