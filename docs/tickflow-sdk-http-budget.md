# TickFlow SDK vs HTTP request budget (Stage A)

## Claim boundary

**Do not claim** that real HTTP RPM is stably capped at 80% until a transport-level interceptor exists.

What we *do* enforce today:

- Process-local pacing at **SDK call sites** via `resolve_limit` + `sleep_between_batches` (`SAFETY_RPM_FACTOR=0.8`).
- Gold Stage A uses **single-symbol** `quotes.get` / `klines.batch`, so SDK internal batch-split risk is low for Gold itself.
- Simulated tests document that a client-side batch limit of 100 implies: 100 symbols → 1 logical HTTP post; 101 → 2; 250 → 3 (serial).

## Why one SDK call ≠ one HTTP request

Vendor SDKs may chunk large symbol lists, retry, or parallelize under the hood. Without wrapping the HTTP transport we only observe:

| Layer | Observable today |
|---|---|
| App → SDK method | yes (call counts, Gold `_pace`) |
| SDK → HTTP | **no** (assumed via simulation only) |
| Cross-process account RPM | **no** |

## Stage A operating rules

1. One Gold pipeline / one writer lock.
2. Fixed small Gold symbol set (currently `600489.SH` only).
3. No concurrent Gold + live Pro probe.
4. Prefer after-hours for large A-share batch syncs.
5. Document first-batch burst: concurrent `index=0` paces can exceed RPM briefly.

## Tests

See `backend/tests/test_gold_sdk_http_budget.py` for simulated split counts (3 / 100 / 101 / 250 symbols) and Gold single-symbol pacing.

## Upgrade path (out of Stage A scope)

- Inject HTTP transport counter / OpenTelemetry around TickFlow client.
- Or pin SDK concurrency=1 and force app-side chunking equal to measured HTTP posts.
- Cross-container account limiter (Redis / file lease) only after Stage A evidence of 429s under honest load.
