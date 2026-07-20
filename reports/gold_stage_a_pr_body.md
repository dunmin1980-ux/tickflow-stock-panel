## Summary
- Bind Compose host port to `127.0.0.1:3018` only (no public publish).
- Harden all Gold state/import/comparison I/O against root, final-file, atomic-temp and child-directory symlink escape; add cross-process `runtime.lock` (flock).
- Classify TickFlow 429s with Stage A circuit breaker; pace Gold via process-local limiter.
- Add request-path audit, SDK≠HTTP budget docs, verify script, Lighthouse bind checklist.

## Base
Please target `codex/gold-integrated-page-design` (Gold integrated baseline `3a17136`), not `main`.

## Test plan
- [ ] `./scripts/verify_gold_stage_a.sh` → `STAGE_A_READY`
- [ ] verifier resolves Compose config to exactly one app target `3018` on `127.0.0.1`
- [ ] backend full suite → `787 passed`; Gold suite → `413 passed`; Ruff clean; frontend build passes
- [ ] On host: `ss`/`lsof` confirms loopback-only 3018 before enabling Gold
- [ ] Keep `GOLD_WORKSPACE_ENABLED=false` until bind verified; do not dual-run with standalone Gold Shadow

## Status vocabulary
`STAGE_A_READY` (local verify). Not LIVE_READY / STAGE_B / global account RPM.
