# Gold Stage A Lighthouse runtime evidence

- Checked at: 2026-07-20 21:30 Asia/Shanghai
- Host: `codex-vm`
- Source: `d3808afb82f39ffc570f3b9bd07564ddf11a8bdf`
- Deployment: `/home/ubuntu/tickflow-stock-panel-stage-a`
- Integrated container: `TickFlow_Stock_Panel`
- Existing standalone container: `TickFlow_Gold_Shadow`

## Result

| Check | Result |
|---|---|
| Integrated app health | HTTP 200, `version=0.1.86`, `mode=none` |
| Integrated bind | `127.0.0.1:3019 -> container:3018` |
| Public `3019` probe | blocked (3-second timeout) |
| SSH tunnel access | local forwarded health and root page both HTTP 200 |
| Gold feature flag | `GOLD_WORKSPACE_ENABLED=false` |
| Gold API status | `enabled=false`, `external_send_count=0` |
| Gold observation gate | blocked by `gold_workspace_disabled` |
| Gold files created | none |
| TickFlow / AI / auth secrets | empty in this sidecar |
| Manual container restart | recovered; health passed on first one-second probe |
| Existing Shadow | healthy on `127.0.0.1:3018`; start time unchanged |
| n8n / Postgres | not recreated or stopped |
| Integrated app resource sample | about `223 MiB`, `0.09%` CPU |

Five loopback `/health` samples were HTTP 200 in about 1.5-1.8 ms each.

## Boundary

This is a Gold-disabled integrated sidecar runtime gate. It proves the container can
run beside the existing Shadow without a port collision or public Docker publish.
It does not prove the integrated Gold sampler, live TickFlow calls, Stage B,
Telegram delivery, or cross-container account-level rate limiting.

The platform performed unauthenticated public extension-data GETs for built-in
industry/concept tables during startup. Those are data-ingress pulls, not Gold
notification sends. No Gold external-send path was enabled.

## Build note

The frozen lock stored direct Tsinghua wheel URLs and the VM observed severe
download slowdown. During image build only, those package URLs were replaced with
their equivalent `files.pythonhosted.org` paths. Locked hashes remained unchanged,
and `backend/uv.lock` was restored after the image was built. The deployed source
checkout has no tracked diff.

## Manual access

```bash
ssh -N -L 3019:127.0.0.1:3019 codex-vm
```

Then open `http://127.0.0.1:3019`. Keep the integrated Gold feature disabled until
the standalone Shadow observation run is intentionally migrated.
