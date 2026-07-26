## Summary

This PR repairs the incremental indicator-history warmup path, prevents
historical double adjustment, and fixes generated PWA runtime assets being
served as SPA HTML. It also adds regression and CI gates for the failure modes
found during TickFlow P0 validation.

## Defects Reproduced

- The incremental history path referenced `_cast` outside its valid scope.
- A broad exception handler could silently convert valid historical data failures into an empty warmup frame.
- Historical adjusted data could be adjusted a second time, producing incorrect indicators.
- Corrupt parquet fallback and schema/programming errors were not separated precisely enough.
- Root PWA assets such as `registerSW.js` fell through to `index.html`.
- The Workbox runtime requested by `sw.js` was not served, and a missing runtime
  asset could incorrectly receive SPA HTML instead of `404`.

## Changes

- Restore incremental indicator history warmup.
- Resolve undefined names and enforce Ruff `F821`.
- Remove stale test suppression.
- Prevent double adjustment of historical data.
- Preserve the documented corrupt-history fallback without swallowing unrelated schema/programming errors.
- Add focused regression coverage in `backend/tests/test_pipeline_incremental_history.py`.
- Serve the generated root PWA allowlist and `workbox-*.js` with the correct
  static response behavior.
- Fail closed with `404` for missing generated PWA assets while preserving SPA
  fallback for application routes.
- Add PWA static-asset regression coverage in
  `backend/tests/test_static_pwa_assets.py`.
- Add the locked backend CI workflow covering dependency sync, compileall, Ruff `F821`, and pytest.

## Verification

- Locked backend dependency sync passed.
- Local backend suite: `1296 passed, 13 warnings`.
- Focused incremental-history regression tests: `5 passed`.
- PWA static-asset regression tests: `9 passed`.
- Backend compileall and Ruff `F821` passed.
- Project pnpm `9.10.0` completed frozen installation.
- Frontend Vitest: `97 passed`; TypeScript and production build passed.
- Playwright: `25 passed, 2 skipped`.
- Cloud candidate sidecar, PWA asset chain, workspace contract, and P0 data
  contract passed before deployment.
- The post-deployment verifier returned `CLOUD_P0_RUNTIME_OK`.

Remote GitHub Actions remain a separate gate. The public API reported no PR and
no workflow run for this branch at closeout; a clean compare view is not treated
as CI success.

## Controlled Cloud Deployment

- Current deployed OCI revision: `494ee71481f1487c1046e1030381c1dfab03d707`.
- Current image ID:
  `sha256:f4b6652d3b57d680b366057c32149f00a5e9fd099961c0245ab30cb4a4cbed43`.
- Current unique local tag:
  `tickflow-stock-panel-stage-a-app:candidate-494ee71`.
- Runtime fix commit: `0c2b73d521f0f8b54e8ec8ccf4de301f02963e10`.
- Initial controlled deployment revision `aa9fc0a` differed from the runtime fix
  only by a report file.
- Governance validation then found the blocking PWA asset defect. Revisions
  `d89e088` and `494ee71` were sidecar-tested and deployed under the documented
  blocking-fix exception.
- The application still displays version `0.1.86`; the OCI revision and image identity are the authoritative patch identifiers.
- The service remains loopback-bound on `127.0.0.1:3019` and is exposed only through the existing Tailscale HTTPS route.
- Gold remains disabled and its external-send count remains zero.
- User-data checksums matched before and after deployment.
- The latest backup and rollback tag are retained, together with the prior
  rollback pair used by the isolated restore drill.

## Explicit Non-Goals

- No brokerage or live-trading integration.
- No automatic trading or simulated execution.
- No Telegram or OpenClaw integration.
- No Gold Stage B enablement.
- No public exposure of ports 3018, 3019, or 5678.
- No production password, TickFlow key, or AI key is included.

## Governance State

The cloud patch deployment does not imply that this PR or its GitHub Actions
have passed. Because GitHub CLI is not authenticated, use the prepared compare
URL to create the PR manually, then require Actions to pass for the exact final
head SHA before merge.
