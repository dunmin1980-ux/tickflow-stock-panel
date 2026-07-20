## Summary
- Bind the Compose host port to `127.0.0.1` only (no public publish).
- Harden all Gold state/import/comparison I/O against root, final-file, atomic-temp and child-directory symlink escape; add cross-process `runtime.lock` (flock).
- Classify TickFlow 429s with Stage A circuit breaker; pace Gold via process-local limiter.
- Add request-path audit, SDK≠HTTP budget docs, verify script, Lighthouse bind checklist.
- Record a Gold-disabled Lighthouse sidecar gate on `127.0.0.1:3019`, while the existing standalone Shadow remains healthy on `127.0.0.1:3018`.

## Base prerequisite

Preferred stacked base: `codex/gold-integrated-page-design` at Gold-integrated
baseline `3a17136`, not `main`.

As of 2026-07-20, that base exists only in the local worktree and is absent from
both upstream and the fork. Do not open the stacked PR until the maintainer publishes
the base branch, or explicitly approves a single full Gold PR against `main`.

## Test plan
- [x] `./scripts/verify_gold_stage_a.sh` → `STAGE_A_READY` (`34 passed`)
- [x] verifier resolves exactly one app target `3018` on `127.0.0.1`
- [x] backend full suite → `787 passed`; Gold suite → `413 passed`; Ruff clean; frontend build passes
- [x] Lighthouse sidecar: `127.0.0.1:3019`, health HTTP 200, public probe blocked
- [x] Gold API: `enabled=false`, `external_send_count=0`; restart recovery verified
- [x] standalone `TickFlow_Gold_Shadow` remains healthy on `127.0.0.1:3018`
- [ ] Keep `GOLD_WORKSPACE_ENABLED=false`; do not dual-run integrated and standalone samplers

## Status vocabulary
`STAGE_A_READY` (local verifier + Gold-disabled sidecar runtime gate). Not
LIVE_READY / STAGE_B / integrated sampler verified / global account RPM.
