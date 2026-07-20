# Gold Stage A hardening evaluation

- **Date:** 2026-07-20 (Asia/Shanghai)
- **Branch:** `cursor/gold-stage-a-hardening-v0.1.86`
- **Base commit:** `3a171366055153a6a7329a3c094b3e4a507088d1`
- **Hardening tip:** `75e1ede`
- **Independent review fix:** `19d4989`
- **Validated source tip:** `d3808af`
- **Workspace:** `tickflow-gold-stage-a-hardening-v0.1.86`
- **Final status:** `STAGE_A_READY` (local verifier + Gold-disabled Lighthouse sidecar runtime gate)

## 1. Baseline

| Item | Value |
|---|---|
| Original package | v0.1.86 / `3a17136` / base `4315508` (PR #133) |
| Hardening branch | `cursor/gold-stage-a-hardening-v0.1.86` |
| Python test runner | current workspace `backend/.venv` |
| Environment | macOS local verification + Tencent Lighthouse `codex-vm` runtime verification |

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

Tencent Lighthouse sidecar verification at source tip `d3808af`:

- app health: HTTP 200, version `0.1.86`, mode `none`
- bind: `127.0.0.1:3019 -> container:3018`
- public `3019` probe: blocked (timeout)
- Gold status: `enabled=false`, `external_send_count=0`
- restart recovery: health returned on the first one-second probe
- existing `TickFlow_Gold_Shadow`: still healthy on `127.0.0.1:3018`; start time unchanged
- runtime footprint sample: about `223 MiB` for the integrated panel

The host port is intentionally `3019`, because the existing standalone Shadow owns
`3018`. This verifies the integrated app runtime and loopback policy only; it does
not enable or validate the integrated Gold sampler.

## 4. Security validation

See `reports/gold_stage_a_security_validation.json`.

- Heuristic secret scan: OK (placeholders filtered)
- Outside-root reads/writes: root, final-file, atomic-temp and child-directory symlink cases covered and rejected
- Compose bind: resolved Compose JSON contains exactly one app target `3018` bound to `127.0.0.1`
- Gold outbound / Telegram: unchanged zero-send Stage A policy (no ownership migration)
- Lighthouse runtime: both `3018` and `3019` are loopback-only; integrated Gold remains disabled

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

**Caveats before integrated Gold enablement:**

1. Keep `GOLD_WORKSPACE_ENABLED=false` while the standalone Shadow owns the observation run.
2. Before any migration, stop the standalone sampler first and confirm a single Gold writer.
3. Prepare and validate the trading calendar before enabling the integrated sampler.
4. Do not run live Pro probe concurrently with Gold sampler.
5. P1 (CI / version unify to 0.1.87 / frozen locks / credential redaction) still recommended before merge to upstream.

The source branch and post-review fixes are pushed to the fork. The Gold-disabled
sidecar is running on the Lighthouse host, but the branch is not Stage B or
production-ready.

## 8. Lighthouse runtime status

Host bind checklist: `docs/gold-stage-a-lighthouse-verify.md`  
Runtime evidence: `reports/gold_stage_a_lighthouse_runtime_20260720.md`

`compose_runtime_ss_verified=true` applies to the Gold-disabled integrated sidecar
on host port `3019`. `cloud_gold_sampler_verified=false` remains mandatory.

## 9. Build mirror note

The v0.1.86 `backend/uv.lock` stores direct Tsinghua wheel URLs, so changing only
Docker `PYPI_INDEX` does not redirect frozen downloads. On `codex-vm`, those direct
downloads were too slow. The deployment used a build-only URL substitution to the
equivalent `files.pythonhosted.org/packages/` paths while preserving every locked
hash, then restored `backend/uv.lock` automatically. No source change was committed.

The repeatable command is documented in `docs/gold-stage-a-runbook.md`. A future P1
should regenerate or normalize the lock source instead of relying on this deployment
workaround.

## 10. Upstream PR status

- Fork branch is pushed through evidence commit `7a0622f`.
- No upstream PR was created: local `gh` is not authenticated.
- More importantly, intended stacked base `codex/gold-integrated-page-design`
  (`3a17136`) is absent from both upstream and the fork as of this check.
- The target was not changed to `main`, because that would silently expand the PR
  from Stage A hardening to the full Gold-integrated feature set.

Next decision: publish the intended base branch upstream, or explicitly approve a
single full Gold PR against `main`.

## 11. Historical connection-failure note


Background agents ([Write Stage A audit docs](502952c7-03fd-4207-8266-b0c42a6af177), [Gold Stage A hardening](09ac5a42-9163-4a71-9dc6-e353dd36b1a4)) failed with connection errors; work continued in the parent session. No reset/discard was performed; WIP was preserved.
