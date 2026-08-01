# TickFlow Phase 2B-1 Typed Claims Contract Design

## Status And Authority

This design implements the user-approved `DESIGN_TYPED_CLAIMS_OUTPUT_CONTRACT`
scope. The authoritative market inputs remain the three committed Phase 2A
Facts files at trade date `2026-07-31`. No TickFlow request, AI call, provider
attempt, cloud mutation, real Obsidian write, Paper Trading, or Integrated Gold
action is permitted.

The target state is `PHASE2B_TYPED_CLAIMS_CONTRACT_READY`. OS-level AI worker
isolation is design-only in this phase and must remain
`ISOLATION_RUNTIME_NOT_YET_VERIFIED`.

## Approaches Considered

1. **Pydantic discriminated claims plus semantic registries (selected).** Each
   claim type has a closed structural model, while predicate, provenance,
   calculation, unit, basis, and template compatibility are enforced by host
   registries. This produces JSON Schema and also covers constraints that JSON
   Schema alone cannot express.
2. **One generic claim dictionary plus manual validation.** This is smaller but
   leaves too many legal field combinations and recreates the permissive
   freeform problem in structured form.
3. **JSON Schema validation only.** This validates shape but cannot prove Facts
   pointer values, calculation replay, raw/qfq compatibility, or renderer
   ownership.

## Architecture

The publishable path is strictly:

```text
Phase 2A Facts JSON
-> typed Claims JSON
-> host Claims Validator
-> deterministic Renderer
-> repository preview
```

AI never owns Markdown. A future AI worker may return a candidate JSON object,
but the host reconstructs and validates every binding before rendering. Any
schema, binding, calculation, basis, unit, or template failure rejects the
whole document. There is no freeform fallback and no automatic repair.

The implementation is split by responsibility:

- `app.schemas.phase2_claims` owns closed Pydantic models and JSON Schema.
- `app.services.phase2_claims_service` owns Facts binding, predicate rules,
  calculation replay, fixture construction, normalized hashes, and routing.
- `app.services.phase2_claims_renderer` owns fixed Markdown templates only.
- `app.services.phase2_ai_worker_protocol` owns minimal projection and fake
  worker boundary checks, not OS isolation.
- CLI scripts build, validate, render, and test the offline artifacts.

## Claims Document

The top-level document contains only:

- schema version and source system;
- symbol, name, trade date, and `Asia/Shanghai` timezone;
- exact relative Facts file and SHA-256 binding;
- conservative data scopes;
- a lexicographically ordered claim list;
- the exact four vendor-pending items;
- `trading_advice=false`.

Models use strict types and `extra="forbid"`. Publishable documents have no
freeform `conclusion`, `analysis`, `reason`, `recommendation`, `outlook`,
`human_note`, `debug_note`, `validator_message`, or `human_review_note` field.
Internal diagnostics live only in validator result objects and never enter the
renderer input.

Each claim contains a unique deterministic `claim_id`, one of eight allowed
claim types, a fixed stock subject, a whitelisted predicate, a typed object,
Facts SHA-256, one or more JSON Pointers, an optional registered calculation,
`raw`/`qfq`/`none` basis, a whitelisted template, an as-of scope, and
`validation_status=VALIDATED`. The host never trusts that status; it recomputes
the complete contract.

## Allowed And Forbidden Claims

The only allowed claim types are:

1. `NUMERIC_OBSERVATION`
2. `BOOLEAN_STATE`
3. `ENUM_STATE`
4. `SAME_BASIS_COMPARISON`
5. `ORDERED_RELATION`
6. `SCOPE_NOTICE`
7. `VENDOR_PENDING_NOTICE`
8. `DATA_QUALITY_STATE`

The fourteen forbidden types from the approved specification are not members
of the schema and are also rejected by the semantic validator:
`RECOMMENDATION`, `TRADE_ACTION`, `POSITION_SIZING`, `BUY_SELL_SIGNAL`,
`TARGET_PRICE`, `STOP_LOSS`, `FORECAST`, `PREDICTION`, `CATALYST`,
`NEWS_INTERPRETATION`, `FINANCIAL_INTERPRETATION`, `INDUSTRY_RANKING`,
`SUPPORT_LEVEL`, and `RESISTANCE_LEVEL`.

Key levels remain `NOT_IMPLEMENTED` and have no predicate or renderer template.

## Predicate And Provenance Rules

Every predicate has exactly one host rule defining:

- claim type;
- legal Facts pointers and their order;
- object/value type and unit;
- expected price basis;
- calculation ID or direct identity;
- renderer template;
- display precision and output section.

Direct claims must equal the referenced Facts value. Calculated claims must
bind every input pointer and equal the replayed registry result. Pointer
resolution implements RFC 6901 escaping, rejects malformed paths, and never
reads outside the already loaded Facts object.

The first fixtures include raw close; a raw open-to-close percentage; MA,
MACD, RSI, BOLL, and ATR values; minute contract states and bar counts; anomaly
counts; adjustment-factor state; same-basis MACD comparison; qfq MA ordering;
scope notices; vendor pending; and the fixed disclaimer. Phase 2A Facts do not
contain a previous-close pointer, so this phase does not mislabel an
open-to-close calculation as conventional previous-close return.

Current Facts store `industry_scope=manual_verification_required`, while the
approved publication scope is `unavailable`. The fixed
`normalize_scope_v1` registry operation conservatively maps only that exact
input to `unavailable`; no arbitrary scope conversion is allowed.

## Calculation Registry

The closed registry contains versioned executable host functions and metadata:

- `compare_numbers_v1`
- `ordered_relation_v1`
- `difference_v1`
- `percentage_difference_v1`
- `threshold_compare_v1`
- `normalize_scope_v1`

Each entry fixes input count, accepted value types and units, basis rules,
epsilon, and output type. No claim may contain an expression, formula string,
module name, executable snippet, or unregistered calculation ID.

`percentage_difference_v1` is `(right - left) / abs(left) * 100` and rejects a
zero denominator. Numeric equality and comparison use a registry-owned
absolute epsilon of `1e-10`; display rounding never changes the stored claim
value.

## Raw And Qfq Separation

Facts provenance determines each operand basis. Direct raw prices bind raw;
indicator prices bind qfq; dimensionless and state values bind none. A
comparison is legal only when both operands share a basis or one side is none
under an explicitly registered rule. No rule permits raw price against qfq
price. Failure is reported as `CLAIM_REJECTED_RAW_QFQ_MISMATCH`.

The renderer receives a validated claim object and cannot hide or reinterpret
basis. It prints basis labels from the validated enum.

## Deterministic Renderer

The renderer owns all prose. It accepts no path, URL, Markdown fragment, or
freeform text and performs no file or network access. Template IDs are closed
and mapped to one of exactly seven sections:

1. 数据范围
2. 日线事实
3. 技术指标事实
4. 分钟数据质量
5. 数据限制
6. 供应商待确认
7. 免责声明

Frontmatter is generated in fixed order with
`type=stock-typed-claims-review`, `verification_status=validated`,
`can_publish=false`, `trading_advice=false`, and
`rendering_mode=deterministic`. Dynamic labels are HTML-escaped and Markdown
metacharacters are escaped. The output contains no links, HTML, comments,
extra headings, or zero-width characters. Canonical Claims input produces
byte-identical Markdown and SHA-256.

## Artifacts And Preview Routing

The complete deterministic artifact tree is published under
`reports/phase2_claims/`:

```text
schema/phase2_claims.schema.json
fixtures/<SYMBOL>_claims.json
rendered/<SYMBOL>_<NAME>.md
claims_index.json
```

Valid rendered documents are copied to
`reports/phase2_obsidian_preview/inbox/typed_<SYMBOL>_<NAME>.md`. The `typed_`
prefix prevents collision with the immutable historical freeform files in
`rejected/`. Invalid test artifacts route only to `rejected/`. The existing
three rejected files, historical AI bodies, provider audit, and audit history
are never rewritten or deleted. `reviewed/` remains empty and no real Vault
path is accepted.

## AI Worker Isolation Protocol

The future boundary is:

```text
Host orchestrator
-> minimal projection file
-> isolated AI worker
-> Claims candidate JSON
-> host validator
-> deterministic renderer
```

The projection excludes source file paths, repository paths, Home paths,
numeric provenance internals, Secrets, and unrelated Facts fields. A fake
worker harness verifies exact projection-path access, symlink rejection,
candidate-only JSON output, and rejection of Markdown, freeform fields,
unknown claims, and raw/qfq mixing.

These checks validate the adapter contract only. macOS does not provide proof
that a future model process cannot bypass Python-level guards, so the result is
`ISOLATION_DESIGN_READY` plus `ISOLATION_RUNTIME_NOT_YET_VERIFIED`. A future
real batch requires an external container or OS sandbox with a read-only single
projection mount, one temporary output mount, no Home/repository/Vault/SSH/
config/Docker-socket mount, short-lived Secret injection, scrubbed logs, and
temporary-directory deletion on exit.

## Failure, Safety, And Verification

Document status is only `CLAIMS_VALID` or `CLAIMS_INVALID`. One invalid claim
invalidates the full document. Rendering refuses invalid documents. Preview
routing is fail closed and never writes `reviewed`.

Tests cover strict schema shape, unique and ordered IDs, Facts hashes and
pointers, direct values, calculations, units, finite numbers, forbidden types
and fields, basis mismatch, deterministic escaping/rendering/hashes, scope,
vendor pending, both preview routes, historical preservation, minimal worker
projection, path denial, and sensitive patterns. Final verification runs the
focused suites, all backend tests, compileall, Ruff F821, offline CLIs,
idempotent rebuild/render, and exact before/after hashes for Phase 1, Phase 2A
Facts, historical AI bodies, provider audit, and rejected samples.
