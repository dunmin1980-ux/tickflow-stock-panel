# TickFlow Phase 2A Existing Cloud Snapshot Design

## Status

Approved by the user on 2026-08-01. This design replaces only the blocked daily
input portion of the existing Phase 2A Facts design. The Phase 1 observation
evidence remains immutable and continues to support minute, adjustment-factor,
rate-limit, and safety claims.

## Source Identity

The daily indicator source is an independent, already-persisted snapshot:

```text
source_type=existing_cloud_store_snapshot
source_store_alias=tickflow_cloud_primary
phase1_original_payload_recovered=false
phase1_byte_equivalent=false
```

It must never be described as the deleted Phase 1 payload or a byte-equivalent
copy. The snapshot is allowed only because the existing store contains aligned
raw/qfq daily series for the fixed three symbols through 2026-07-31, its
selected daily provider is TickFlow, its adjustment source follows the daily
provider, and the same-day sync/enrichment job completed successfully.

## Read-Only Capture

The exporter receives a local data-directory path and reads only an explicit
allowlist:

- `kline_daily/date=*/part.parquet`;
- `kline_daily_enriched/date=*/part.parquet`;
- `user_data/preferences.json` for the two provider selectors;
- `capabilities.json` for the provider tier label;
- the successful same-day `job_store/*.json` record.

It does not import TickFlow, HTTP, database-client, or AI modules. In remote use,
the script is streamed to the running container and emits one JSON bundle to
stdout. It writes no remote file and invokes no application task. A local mode
atomically materializes the bundle into
`reports/phase2_source_snapshot/2026-07-31/`.

The manifest records `cloud_access_mode=read_only`,
`cloud_mutation_count=0`, and `tickflow_api_request_count=0`. It records only a
store alias, partition identifiers, hashes, selected provider metadata, and
sanitized job metadata. Hostnames, addresses, credentials, connection strings,
request headers, and Secret files are excluded.

Provider selection follows the persisted application contract. When
`daily_data_provider` or `adj_factor_provider` is absent, the exporter records
the application's deterministic defaults (`tickflow` and `same_as_daily`) plus
`application_default_absent`; it does not misstate those defaults as explicit
user configuration.

## Snapshot Contract

Each symbol has separate canonical raw and qfq JSON documents. Rows are sorted
by trade date and contain symbol, trade date, OHLC, volume, and amount. The
validator checks:

- fixed symbol/name and exact manifest membership;
- 251 rows for this approved snapshot, strict date ordering, uniqueness, and
  latest date `2026-07-31`;
- identical raw/qfq date sets;
- finite OHLCVA, valid OHLC bounds, nonnegative volume/amount, and no future
  dates;
- selected provider `tickflow`, adjustment source `same_as_daily`, Pro
  capability label, and successful same-day sync/enrichment task;
- output hashes, source-partition aggregate hashes, canonical serialization,
  no secret/trading fields, and zero request/mutation counters.

`captured_at` is audit metadata and may differ between exports. The normalized
business hash excludes only capture-time metadata. Two read-only exports must
produce identical raw/qfq hashes and normalized business hash.

## Indicator Contract

The Facts builder uses `app.indicators.pipeline.compute_indicators` once per
symbol. Price inputs are qfq open/high/low/close; volume inputs are from the raw
snapshot. It requests the existing MA5/10/20/60, MACD DIF/DEA/HIST, RSI6/14,
BOLL upper/middle/lower, ATR14, volume MA5/10, and five-day volume-ratio columns.

Every emitted numeric value records its source file/hash or deterministic
calculation function, input fields, parameters, price basis, history window,
and `float64_unrounded` result precision. Volume calculations retain
`volume_unit=VENDOR_CONFIRMATION_PENDING`; volume ratio is explicitly
dimensionless and source-unit-unconfirmed. Amount is not used for a
unit-dependent derived indicator.

Key levels remain `NOT_IMPLEMENTED` with empty support/resistance arrays.

## Evidence Separation

Facts expose two separate evidence domains:

1. `indicator_source`: the independent cloud-store snapshot and its manifest;
2. `phase1_contract_evidence`: the existing five-day observation contracts.

The first supports daily values and technical indicators. The second supports
minute/30-minute, adjustment-factor, request-audit, and safety claims. Neither
domain is relabeled as the other.

## Failure Semantics

Any provider, job, hash, date, row, numeric, provenance, source-unit, safety, or
idempotency failure blocks publication and prevents `PHASE2A_FACTS_READY`.
Snapshot and Facts directories are staged and atomically replaced only after
validation. First publication uses one same-filesystem rename; replacement of
an existing directory uses macOS `renamex_np(RENAME_SWAP)` or Linux
`renameat2(RENAME_EXCHANGE)`, so the canonical path is never absent between two
renames. Phase 1 evidence and request audits are read-only throughout.

## Out Of Scope

AI Review, any model call, AI keys, Paper Trading, matching, Obsidian writes,
cloud deployment/restart, Telegram, OpenClaw, Integrated Gold,
`intraday_batch`, full-market scans, and key-level algorithms remain frozen.
