# TickFlow P0 Commit Drift Audit

Audit date: 2026-07-24 (Asia/Shanghai)

```yaml
runtime_fix_commit: 0c2b73d521f0f8b54e8ec8ccf4de301f02963e10
deployed_revision: aa9fc0a84b8e72091f8e26c86983c66819befd30
branch_revision: 12c99db6da323d8149b578358899cff5c8e53f55
pwa_fix_commit: 494ee71481f1487c1046e1030381c1dfab03d707
current_deployed_revision: 494ee71481f1487c1046e1030381c1dfab03d707
current_branch_runtime_revision: 494ee71481f1487c1046e1030381c1dfab03d707
```

## 1. `0c2b73d` to `aa9fc0a`

Commits:

```text
389eaf8 docs: add P0 validation evaluation report
aa9fc0a docs: record remote CI trigger state
```

Changed files:

```text
A reports/tickflow_p0_validation_eval.md
```

Diff stat: one report, 307 insertions.

Conclusion:

- `aa9fc0a` adds only the P0 validation report.
- It does not add or alter backend, frontend, container, workflow, or other runtime code relative to `0c2b73d`.
- The cloud image labelled with OCI revision `aa9fc0a` therefore contains the same application runtime tree as runtime fix commit `0c2b73d`, plus non-runtime report content in its build context.

## 2. `aa9fc0a` to `12c99db`

Commit:

```text
12c99db docs: record P0 cloud patch deployment
```

Changed files:

```text
A reports/tickflow_p0_cloud_upgrade_20260723.md
M reports/tickflow_p0_validation_eval.md
```

Diff stat: two report files, 152 insertions.

Conclusion:

- `12c99db` adds or updates deployment evidence only.
- It contains no cloud-undeployed runtime code relative to `aa9fc0a`.
- At the original preflight, the branch HEAD and deployed OCI revision differed
  in Git identity but not in application runtime content.

## 3. `12c99db` to `494ee71`

Commits:

```text
d89e088 fix: serve generated root PWA assets
494ee71 fix: serve Workbox runtime asset
```

Changed files:

```text
M backend/app/main.py
A backend/tests/test_static_pwa_assets.py
```

Diff stat: two files, 98 insertions and 8 deletions.

Conclusion:

- This range does contain runtime code and regression tests.
- It fixes a blocking PWA delivery defect found during governance validation:
  generated root assets such as `registerSW.js` and the Workbox runtime were
  falling through to the SPA HTML response.
- The final implementation serves only an explicit set of generated root PWA
  assets plus `workbox-*.js`, returns `404` for missing PWA runtime assets, and
  retains SPA fallback for application routes.
- Revision `494ee71` was built as a distinct candidate image, validated in a
  sidecar, and then deployed through the documented rollback-protected
  exception for blocking correctness defects.
- The current cloud runtime and the branch runtime revision are therefore
  aligned at `494ee71481f1487c1046e1030381c1dfab03d707`.

## 4. Review and Deployment Identity

The PR should review the complete head of `codex/tickflow-p0-validation` against `codex/tickflow-multiclient-app`. At preflight that exact head is:

```text
12c99db6da323d8149b578358899cff5c8e53f55
```

The runtime head at closeout is:

```text
494ee71481f1487c1046e1030381c1dfab03d707
```

The governance closeout files created after this audit will advance the branch
head with documentation and verification scripts only. The eventual PR must
review the final pushed branch head, while cloud runtime claims remain pinned to
`494ee71`.

Risk classification:

- Runtime drift at closeout: none. Both indicator-history P0 fixes and the PWA
  asset fixes through `494ee71` are present in the deployed image.
- Documentation/script drift: expected. The final governance commit is not an
  application deployment and must not be described as one.
- Deployment identity risk: low after verifying the OCI revision, image ID, and
  unique local tag. The API/UI version remains `0.1.86` and must not be used as
  the sole deployment identity.
- Current PR head contains cloud-undeployed runtime code: no, as of runtime
  revision `494ee71`.
- PR review target: the final pushed head of
  `codex/tickflow-p0-validation`; reviewers must include all runtime commits
  through `494ee71`.
