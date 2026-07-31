# TickFlow Phase 2A Deterministic Facts Design

## Status

Approved by the user on 2026-07-31. This document narrows the broader Phase 2
brief to the deterministic Facts layer only.

## Goal

Build three byte-stable, machine-validated stock Facts documents from retained
Phase 1 evidence without making any TickFlow or AI request. Every emitted
numeric value must identify its source JSON path or deterministic derivation.

## Source Boundary

The builder reads only committed evidence in the Phase 2 worktree:

- `reports/phase1_observation/observation_index.json`
- `reports/phase1_observation/2026-07-31/daily_summary.json`
- `reports/phase1_observation/2026-07-31/<SYMBOL>_contract.json`
- `reports/phase1_observation/2026-07-31/minute_30m_comparison.json`
- `reports/phase1_observation/2026-07-31/request_audit.json`
- `reports/phase1_tickflow_request_audit.json`
- `reports/tickflow_phase1_observation_final.md`

Each source file is hashed with SHA-256 before use. The builder rejects symlinks,
paths outside the repository, a non-passing Phase 1 status, a symbol outside the
fixed three-symbol set, or a trade date other than `2026-07-31`.

The retained Phase 1 evidence contains contract summaries but not the original
raw/qfq daily OHLC series. Therefore daily OHLCV values and MA, MACD, RSI, BOLL,
ATR, and volume-derived indicator values cannot be reconstructed. They are
emitted as `null` with status `NOT_IMPLEMENTED`; the reason and required input
are explicit. Old application caches, untracked artifacts, and network data are
not substitutes for the missing verified series.

## Facts Contract

Each `reports/phase2_facts/<SYMBOL>_facts.json` contains:

- schema identity, symbol, name, trade date, timezone, and freshness;
- a SHA-256 manifest of every source file used;
- raw/qfq price-basis declarations;
- retained daily, adjustment-factor, 1-minute, and 30-minute contract facts;
- explicit unavailable daily values and `NOT_IMPLEMENTED` indicators;
- incomplete market scope and unavailable financial/news scope;
- all four supplier-pending items;
- `trading: false`;
- a `numeric_provenance` map for every numeric JSON leaf.

Direct evidence provenance records a source file, its SHA-256, and a JSON path.
Derived provenance records the deterministic function and all input paths.
Schema constants record a named constant. Null values are not numeric claims.

## Validation

The validator independently reloads the Facts file and source evidence. It
checks:

1. exact schema and fixed symbol set;
2. source path confinement, regular-file status, and SHA-256 equality;
3. Phase 1 pass status and `2026-07-31` freshness;
4. raw/qfq separation and absence of values when retained inputs are missing;
5. complete numeric provenance with source-path value equality;
6. finite JSON numbers only;
7. unavailable financial/news scope and complete vendor-pending metadata;
8. absence of secret shapes and trading-action fields;
9. canonical JSON and byte-for-byte regeneration stability.

The directory also contains a deterministic build manifest with output hashes,
source-set hash, `new_tickflow_api_request_count: 0`, and idempotency results.

## Failure Semantics

The CLI exits nonzero and does not publish a partial result when source evidence
or validation fails. Output is built in a temporary sibling directory and then
atomically replaces only the Phase 2 Facts directory. Phase 1 evidence is always
read-only and its pre/post hash manifest must match.

## Out Of Scope

AI generation, AI keys, Obsidian writes, Paper Trading, cloud deployment,
Telegram, OpenClaw, Integrated Gold, full-market scans, and `intraday_batch` are
not part of Phase 2A.
