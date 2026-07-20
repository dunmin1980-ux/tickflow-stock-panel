# Gold Stage A hardening evaluation

- **Date:** 2026-07-20 (Asia/Shanghai)
- **Branch:** `cursor/gold-stage-a-hardening-v0.1.86`
- **Base commit:** `3a171366055153a6a7329a3c094b3e4a507088d1`
- **Hardening tip:** `75e1ede`
- **Independent review fix:** `19d4989`
- **Workspace:** `tickflow-gold-stage-a-hardening-v0.1.86`
- **Final status:** `STAGE_A_READY` (local verify script; see caveats)

## 1. Baseline

| Item | Value |
|---|---|
| Original package | v0.1.86 / `3a17136` / base `4315508` (PR #133) |
| Hardening branch | `cursor/gold-stage-a-hardening-v0.1.86` |
| Python test runner | current workspace `backend/.venv` |
| Environment | macOS local; Docker Compose resolved config required |

## 2. P0 completion

| P0 | Status | Notes |
|---|---|---|
| P0-1 localhost 3018 | **done** | `docker-compose.yml` → `127.0.0.1:${PORT:-3018}:3018` + comments |
| P0-2 symlink escape | **done after review fix** | Descriptor-relative reads/writes, randomized atomic temp files, secure `imports/` and `comparison_runs/`; tests cover root/parent/dangling/final/temp/child-directory symlinks |
| P0-3 runtime lock | **done** | `gold_runtime_lock.py` flock; acquired in `main.py` lifespan |
| P0-4 429 evidence | **done** | `gold_tickflow_errors.py` classify + `RateLimitCircuit`; Gold gateway maps/records |
| P0-5 request audit | **done** | `docs/tickflow-request-path-audit.md` |
| P0-6 SDK HTTP count | **partial→documented** | Simulated transport tests + `docs/tickflow-sdk-http-budget.md`; **does not** claim real HTTP 80% RPM |

## 3. Tests

Local `./scripts/verify_gold_stage_a.sh` at `19d4989`:

- resolved Compose loopback bind: **passed**
- security + rate-limit + sdk-budget suite: **34 passed**
- See `reports/gold_stage_a_verify_run.txt`

Broader independent regression:

- Gold suite: **413 passed**
- Full backend: **787 passed, 9 warnings**
- Gold Ruff: **passed**
- Frontend `pnpm test:gold`: **passed**
- Frontend production build: **passed** (`2687` modules)

Not claimed in this eval: live `ss`/`lsof` on a running Tencent Lighthouse stack.

## 4. Security validation

See `reports/gold_stage_a_security_validation.json`.

- Heuristic secret scan: OK (placeholders filtered)
- Outside-root reads/writes: root, final-file, atomic-temp and child-directory symlink cases covered and rejected
- Compose bind: resolved Compose JSON contains exactly one app target `3018` bound to `127.0.0.1`
- Gold outbound / Telegram: unchanged zero-send Stage A policy (no ownership migration)

## 5. Independent review correction

The original `75e1ede` implementation was not sufficient for P0-2: fixed atomic temp paths and the `imports/` / `comparison_runs/` child directories still used path-based I/O. A local reproduction overwrote an outside file through `.health.json.tmp` and wrote import data through a child-directory symlink. The original verifier also returned `STAGE_A_READY` when `docker compose config` failed because `.env` was missing.

Commit `19d4989` closes those gaps and adds regression tests. The verifier now fails closed when Docker, `.env`, Compose parsing, the resolved loopback binding, the workspace Python environment, or the security tests are unavailable.

## 6. Rate-limit boundary (mandatory wording)

```text
当前是进程内 Stage A 限频
不是跨容器账户级限频
不是 Gold + A股 + legacy 的全局共享限频
```

## 7. Final status

```text
STAGE_A_READY
```

**Caveats before cloud enablement:**

1. Re-run verify on the Lighthouse host after `docker compose up` and confirm `127.0.0.1:3018` via `ss`/`lsof`.
2. Keep `GOLD_WORKSPACE_ENABLED=false` until host bind + lock + holidays are confirmed.
3. Do not run live Pro probe concurrently with Gold sampler.
4. P1 (CI / version unify to 0.1.87 / frozen locks / credential redaction) still recommended before merge to upstream.

The local code state is ready for the separate Lighthouse runtime gate. The branch is not production-ready and the post-review commit has not been pushed.

## 8. Lighthouse next step

Host bind checklist: `docs/gold-stage-a-lighthouse-verify.md`  
Until `ss`/`lsof` confirms loopback-only 3018 on the VM, keep `compose_runtime_ss_verified=false`.

## 9. Historical connection-failure note


Background agents ([Write Stage A audit docs](502952c7-03fd-4207-8266-b0c42a6af177), [Gold Stage A hardening](09ac5a42-9163-4a71-9dc6-e353dd36b1a4)) failed with connection errors; work continued in the parent session. No reset/discard was performed; WIP was preserved.
