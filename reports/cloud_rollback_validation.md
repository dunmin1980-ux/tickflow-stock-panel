# TickFlow Cloud Rollback Validation

Validation date: 2026-07-24 (Asia/Shanghai)

Result:

```text
ROLLBACK_ASSETS_PRESENT
PRIOR_PAIR_RESTORE_DRILL_EXECUTED
PRIOR_PAIR_RESTORE_DRILL_OK
LATEST_PAIR_RESTORE_DRILL_NOT_EXECUTED
```

## Latest Post-PWA Rollback Pair

| Item | Evidence |
|---|---|
| Data backup | `/home/ubuntu/tickflow-backups/20260724_105311-pre-pwa-494ee71` |
| Backup user-data diff | zero bytes |
| Rollback image tag | `tickflow-stock-panel-stage-a-app:rollback-20260724_105311-pre-494ee71` |
| Rollback image ID | `sha256:4f3f240cec7a911364ce2c0a84e89a42a1206e93bd74496792045775bfdd1003` |
| Rollback state file | points to the latest rollback tag |
| Current preferences SHA-256 | `949132c081de283f72829dadb95435bcf8d20bbe8623a483185fe9691d7706d0` |

This pair was created immediately before deploying `494ee71`. The rollback
image is the previously running `aa9fc0a` application image. It was not started
again in a second restore drill because it had just completed live and sidecar
runtime checks; the earlier retained pair was used for the destructive-cleanup
isolation drill described below.

## Retained Pair Used by the Isolated Drill

| Item | Evidence |
|---|---|
| Data backup | `/home/ubuntu/tickflow-backups/20260723_232548-p0-aa9fc0a` |
| Rollback image tag | `tickflow-stock-panel-stage-a-app:rollback-20260723_232548-pre-aa9fc0a` |
| Rollback image ID | `sha256:02d556047ec97314eeb10f8ae8f7e902b59efc16d1c93387d4b5d7933f5f56a5` |
| Backup user-data diff | zero bytes |
| Backup/current preferences SHA-256 | `949132c081de283f72829dadb95435bcf8d20bbe8623a483185fe9691d7706d0` |

The backup contains deployment configuration and user data and must continue to be handled as sensitive, even though no secret value was printed during validation.

## Isolated Restore Drill

The drill:

1. copied the backup into an independent temporary restore directory;
2. mounted only that copy into the rollback image;
3. started `TickFlow_P0_Restore_Drill` on temporary loopback `127.0.0.1:3021`;
4. forced `GOLD_WORKSPACE_ENABLED=false`;
5. did not join the temporary container to Tailscale Serve;
6. used no real password, TickFlow key, or AI key;
7. queried health, authentication status, workspace bootstrap, and Gold status;
8. destroyed the temporary container and restore directory.

## Results

| Check | Result |
|---|---|
| Health | `status=ok`, `version=0.1.86`, `mode=none` |
| Authentication | uninitialized, as expected from the backup |
| Workspace schema | `1` |
| Workspace resources | watchlist, preferences, stock reports, market recaps, backtest summaries |
| Watchlist count | `0` |
| Preference object | present |
| Stock report count | `0` |
| Market recap count | `0` |
| Backtest summary count | `0` |
| Gold | disabled |
| Gold external-send count | `0` |
| Temporary binding | `127.0.0.1:3021` |
| Temporary restart count | `0` |
| Restored preferences SHA-256 | matched the backup |
| Cleanup | temporary container and restore directory absent after the drill |

## Boundary

The drill proves that the retained pre-patch image and its data backup can start
together and expose the expected health/workspace contract in isolation. The
latest post-PWA rollback pair is present and its data diff is zero, but that
exact pair was not separately started in a second drill. Neither result proves
recovery of future credentials, newly created reports, or data written after
the applicable backup timestamp.

The rollback image and backup must remain retained through the post-upgrade observation window.
