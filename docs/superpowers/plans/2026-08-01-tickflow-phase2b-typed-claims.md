# TickFlow Phase 2B-1 Typed Claims Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a strict typed Claims contract, deterministic renderer, isolated Obsidian preview route, and fake AI worker boundary over the three immutable Phase 2A Facts files.

**Architecture:** Pydantic discriminated models close the candidate shape; host-side predicate and calculation registries rebind every value to committed Facts. Only validated Claims reach a renderer that owns all Markdown, while a separate fake-worker protocol proves the adapter boundary without claiming OS-level isolation.

**Tech Stack:** Python 3.12, Pydantic 2, canonical JSON, SHA-256, PyYAML, pytest, Ruff, repository atomic-directory helpers.

## Global Constraints

- Fixed symbols: `000403.SZ`, `600489.SH`, `300059.SZ`.
- Phase 2 Base: `ce1f0b221cfd390cafc92a26ef8e16cf7dd3a753`.
- Start Head: `4ae49f5011dba5a5b53369073559546b64192daa`.
- No TickFlow API, AI, provider, network, cloud, real Vault, Paper Trading, Telegram, OpenClaw, or Integrated Gold action.
- Do not modify `reports/phase2_facts/`, Phase 1 evidence, historical AI samples, provider audit, audit history, or existing rejected previews.
- Keep `can_publish=false`, `trading_advice=false`, and `reviewed/` empty.
- Keep the exact vendor pending list: `intraday_batch_entitlement`, `first_30m_bucket_includes_09_30`, `volume_unit`, `amount_unit`.
- Never add an arbitrary expression, formula string, freeform Markdown, or executable calculation field.
- Final isolation status is `ISOLATION_DESIGN_READY / ISOLATION_RUNTIME_NOT_YET_VERIFIED`.

---

### Task 1: Strict Claims Models And JSON Schema

**Files:**
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/schemas/phase2_claims.py`
- Create: `backend/tests/test_phase2_claims.py`

**Interfaces:**
- Produces: `ClaimsDocument`, eight discriminated claim models, `WorkerClaimsCandidate`, `claims_json_schema()`, `ALLOWED_CLAIM_TYPES`, and `FORBIDDEN_CLAIM_TYPES`.
- Consumes: only Pydantic and standard-library enums/types.

- [x] **Step 1: Write failing strict-schema tests**

Add tests that parse a minimal valid document and reject extra fields, unknown
claim types, every forbidden claim type, freeform fields, non-finite numbers,
invalid symbols, mutable trading flags, and malformed claim IDs. Assert that
the generated JSON Schema exposes exactly eight allowed claim discriminators.

```python
def test_claims_schema_forbids_freeform_and_trading_types():
    payload = minimal_claims_payload()
    payload["claims"][0]["analysis"] = "free text"
    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)

    payload = minimal_claims_payload()
    payload["claims"][0]["claim_type"] = "TRADE_ACTION"
    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)
```

- [x] **Step 2: Run tests and confirm RED**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_phase2_claims.py -q
```

Expected: collection fails because `app.schemas.phase2_claims` does not exist.

- [x] **Step 3: Implement closed Pydantic models**

Use `ConfigDict(extra="forbid", strict=True)` on every model. Define typed
objects for numbers, booleans, enums, comparisons, ordered operands, scope,
vendor pending, provenance, rendering, and claim scope. Use an annotated union
with `Field(discriminator="claim_type")`. Require
`validation_status="VALIDATED"`, exact `source_system`, timezone, and false
trading flag. Reject `NaN` and Infinity with finite-number validators.

- [x] **Step 4: Generate deterministic JSON Schema in memory**

Implement:

```python
def claims_json_schema() -> dict[str, Any]:
    return ClaimsDocument.model_json_schema(mode="validation")
```

The artifact writer will serialize this through the existing canonical JSON
function, not Pydantic's unordered display helpers.

- [x] **Step 5: Run schema tests and confirm GREEN**

Run the focused test file and require zero failures.

---

### Task 2: Facts Binding, Predicate Rules, And Calculation Registry

**Files:**
- Create: `backend/app/services/phase2_claims_service.py`
- Modify: `backend/tests/test_phase2_claims.py`
- Create: `backend/scripts/build_phase2_claims_fixture.py`
- Create: `backend/scripts/validate_phase2_claims.py`

**Interfaces:**
- Consumes: `ClaimsDocument`, Phase 2A `validate_facts_document`, `FIXED_SYMBOLS`, `VENDOR_PENDING`, and canonical JSON.
- Produces: `ClaimsValidationResult`, `CALCULATION_REGISTRY`, `PREDICATE_RULES`, `build_claims_document(repo_root, symbol)`, `validate_claims_document(repo_root, value)`, `build_claims_fixtures(repo_root)`, and `validate_claims_directory(repo_root, claims_root)`.

- [x] **Step 1: Write failing provenance and calculation tests**

Cover exact Facts file/SHA binding, RFC 6901 pointer resolution, direct value
equality, calculation replay, claim ID uniqueness/order, symbol/name/date,
scope/vendor flags, finite values, units, templates, and sensitive patterns.
Add explicit raw-close versus qfq-MA rejection expecting
`CLAIM_REJECTED_RAW_QFQ_MISMATCH`.

```python
def test_raw_close_cannot_compare_with_qfq_ma20():
    document = build_valid_document("000403.SZ")
    document["claims"].append(raw_qfq_comparison(document))
    result = validate_claims_document(REPO_ROOT, document)
    assert result.status == "CLAIMS_INVALID"
    assert "CLAIM_REJECTED_RAW_QFQ_MISMATCH" in result.errors
```

Test all registry IDs, metadata, input counts, basis rules, epsilon, and output
types. Confirm unknown IDs and expression-like strings are rejected.

- [x] **Step 2: Run new tests and confirm RED**

Expected: imports or unimplemented interfaces fail before production code is
added.

- [x] **Step 3: Implement safe Facts loading and pointers**

Facts paths must equal `reports/phase2_facts/<SYMBOL>_facts.json`, remain under
the resolved repository root, be regular non-symlink files, and match the
document SHA. Resolve pointers only inside the loaded mapping/list and reject
invalid RFC 6901 escapes.

- [x] **Step 4: Implement the closed calculation registry**

Create executable specs for:

```text
compare_numbers_v1
ordered_relation_v1
difference_v1
percentage_difference_v1
threshold_compare_v1
normalize_scope_v1
```

Calculations accept already resolved typed values. The percentage function is
`(right-left)/abs(left)*100` and rejects zero left input. Comparison epsilon is
`1e-10`. `normalize_scope_v1` accepts only
`manual_verification_required -> unavailable`.

- [x] **Step 5: Implement predicate rules and full validation**

Each predicate fixes claim type, exact pointers, unit, basis, calculation,
template, precision, and section. Validation must collect deterministic sorted
errors, recompute normalized SHA-256, and return only `CLAIMS_VALID` or
`CLAIMS_INVALID`. Any claim failure invalidates the document.

- [x] **Step 6: Implement deterministic fixture construction**

Generate claims for raw close, raw open-to-close percentage, MA/MACD/RSI/BOLL/
ATR, minute states/counts/anomalies, adjustment factors, one MACD same-basis
comparison, one qfq MA ordering, four scope notices, and one vendor notice.
Use numeric claim ID prefixes and sort lexicographically. Do not generate key
levels or previous-close return.

- [x] **Step 7: Implement build and validation CLIs**

`build_phase2_claims_fixture.py` writes canonical schema and fixtures under an
atomic staging directory. `validate_phase2_claims.py` reads existing artifacts,
validates all three documents, checks file set and hashes, and emits a sanitized
JSON summary with zero external-action counters.

- [x] **Step 8: Run focused tests, CLIs, compileall, and Ruff**

Require the test file, fixture builder dry output, validator, compileall, and
Ruff F821 to pass before committing.

- [ ] **Step 9: Commit the Claims contract**

```bash
git add backend/app/schemas backend/app/services/phase2_claims_service.py \
  backend/scripts/build_phase2_claims_fixture.py \
  backend/scripts/validate_phase2_claims.py backend/tests/test_phase2_claims.py
git commit -m "feat: add typed phase2 claims contract"
```

---

### Task 3: Deterministic Markdown Renderer And Preview Route

**Files:**
- Create: `backend/app/services/phase2_claims_renderer.py`
- Create: `backend/tests/test_phase2_claims_renderer.py`
- Create: `backend/scripts/render_phase2_claims.py`
- Generate: `reports/phase2_claims/schema/phase2_claims.schema.json`
- Generate: `reports/phase2_claims/fixtures/*.json`
- Generate: `reports/phase2_claims/rendered/*.md`
- Generate: `reports/phase2_claims/claims_index.json`
- Generate: `reports/phase2_obsidian_preview/inbox/typed_*.md`

**Interfaces:**
- Consumes: a `CLAIMS_VALID` document and `PREDICATE_RULES`.
- Produces: `render_claims_document(document, validation) -> str`, `validate_rendered_document(...)`, `route_claims_preview(...)`, and `publish_claims_bundle(repo_root, reports_root)`.

- [ ] **Step 1: Write failing renderer and route tests**

Require exact seven headings, exact safe frontmatter, no arbitrary Markdown,
HTML/comments/links/zero-width characters, HTML/Markdown escaping, stable bytes
and hash, invalid-document refusal, inbox/rejected route behavior, and empty
reviewed. Hash the three historical rejected files before publication and
assert they are unchanged afterwards.

```python
def test_renderer_is_byte_idempotent():
    document, validation = validated_fixture("000403.SZ")
    first = render_claims_document(document, validation)
    second = render_claims_document(document, validation)
    assert first == second
    assert sha256(first.encode()).hexdigest() == sha256(second.encode()).hexdigest()
```

- [ ] **Step 2: Run tests and confirm RED**

Expected: renderer import fails.

- [ ] **Step 3: Implement the fixed renderer**

Use a closed `template_id -> section/callable` registry. Render values only
from typed objects; escape dynamic labels; generate frontmatter and seven
sections in fixed order; append the fixed non-investment-advice disclaimer.
The renderer module must not import `Path`, HTTP clients, subprocess, or socket.

- [ ] **Step 4: Implement deterministic bundle publication**

Build schema, fixtures, rendered files, and index in a staging tree and publish
the `phase2_claims` directory atomically. Copy valid files to
`phase2_obsidian_preview/inbox/typed_<SYMBOL>_<NAME>.md` with durable temporary
files and `os.replace`. Never replace the existing preview tree and never touch
historical rejected files. Invalid test documents route under a `typed_` name
to rejected.

- [ ] **Step 5: Implement render CLI and idempotency check**

The CLI rerenders only from Claims fixtures, never from AI. Run it twice and
require identical fixture, rendered, index, schema, and preview hashes.

- [ ] **Step 6: Run renderer tests and offline validation**

Also rerun the legacy freeform delivery validator and require the same expected
`PHASE2_AI_OUTPUT_BLOCKED` with `errors=[]`; typed inbox files must not create a
historical duplicate-route error.

- [ ] **Step 7: Commit renderer and generated artifacts**

```bash
git add backend/app/services/phase2_claims_renderer.py \
  backend/scripts/render_phase2_claims.py \
  backend/tests/test_phase2_claims_renderer.py \
  reports/phase2_claims reports/phase2_obsidian_preview/inbox
git commit -m "feat: add deterministic claims renderer"
```

---

### Task 4: Fake AI Worker Protocol And Isolation Design

**Files:**
- Create: `backend/app/services/phase2_ai_worker_protocol.py`
- Create: `backend/tests/test_phase2_ai_worker_protocol.py`
- Create: `scripts/test_phase2_ai_isolation.sh`
- Create: `docs/tickflow_phase2_ai_isolation_design.md`

**Interfaces:**
- Produces: `build_worker_projection(facts, facts_sha256)`, `read_exact_projection(path, allowed_path)`, `validate_worker_candidate(value, projection)`, and `FakeClaimsWorker`.
- Consumes: typed candidate models and only the safe Facts fields required by predicate rules.

- [ ] **Step 1: Write failing projection and denial tests**

Assert projection contains no repository/Home/source paths, no `source_evidence`
or full `numeric_provenance`, no Secrets, and only approved values. Assert exact
projection file reads succeed while siblings, parent paths, symlinks, `/etc`,
Home, and repository paths fail before bytes are read.

- [ ] **Step 2: Write failing candidate tests**

Accept only JSON Claims candidates bound to the projection hash. Reject output
with Markdown, freeform keys, unknown/forbidden types, symbol/date mismatch,
unexpected Facts pointers, or raw/qfq mixing.

- [ ] **Step 3: Run tests and confirm RED**

Expected: protocol module is absent.

- [ ] **Step 4: Implement minimal projection and fake worker**

Projection construction is pure. Exact-path reading uses no-follow open plus
`fstat` on the same descriptor. The fake worker is deterministic and has no
network/provider dependency. Validation returns sanitized errors and performs
zero external attempts.

- [ ] **Step 5: Add shell harness and isolation document**

The shell script runs only the fake protocol tests, rejects Secret-bearing
environment output, and never calls the real AI or TickFlow. The design must
list forbidden mounts, short-lived Secret handling, log redaction, teardown,
and why Python guards do not prove macOS OS-level isolation.

- [ ] **Step 6: Run protocol tests and shell harness**

Require `ISOLATION_DESIGN_READY` and
`ISOLATION_RUNTIME_NOT_YET_VERIFIED` in sanitized output.

- [ ] **Step 7: Commit protocol and isolation evidence**

```bash
git add backend/app/services/phase2_ai_worker_protocol.py \
  backend/tests/test_phase2_ai_worker_protocol.py \
  scripts/test_phase2_ai_isolation.sh docs/tickflow_phase2_ai_isolation_design.md
git commit -m "test: add claims validation and AI isolation protocol"
```

---

### Task 5: Final Report, Immutability, Regression, And Push

**Files:**
- Create: `reports/tickflow_phase2b_typed_claims_eval.md`
- Modify: this implementation plan only to mark completed steps.

**Interfaces:**
- Produces final state `PHASE2B_TYPED_CLAIMS_CONTRACT_READY` and a pushed branch with a matching remote SHA.

- [ ] **Step 1: Run the required focused suite**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_phase2_claims.py \
  tests/test_phase2_claims_renderer.py \
  tests/test_phase2_ai_worker_protocol.py -q
```

- [ ] **Step 2: Run full backend and static verification**

```bash
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python -m compileall -q app scripts
.venv/bin/ruff check app scripts --select F821
```

- [ ] **Step 3: Run both Claims CLIs, renderer twice, legacy validator, and isolation harness**

Require three valid fixtures, three deterministic Markdown files, zero invalid
claims, three typed inbox files, zero typed rejected files, preserved legacy
rejected files, and no reviewed files.

- [ ] **Step 4: Recompute all immutable hashes**

Require exact equality with the preflight values:

```text
Phase 1 evidence: 8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5
Phase 1 request audits: dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d
Phase 2 Facts tree: 10c6664754b8e1391f55d9b1b8b98f1577b749f0dceafbb77288749ae5376ef7
Provider audit: 7996c13681e248b2a9932cdd7428949f1317719589234a7087b8ab019e4274f9
```

Also compare every historical AI sample and rejected-preview file hash listed in
the design-phase command output. Verify historical `ai_body_sha256` fields are
unchanged and new external-action counters are all zero.

- [ ] **Step 5: Run sensitive, trading, forbidden-field, path, and temp scans**

Scan new artifacts and implementation files without treating deliberate test
fixtures as production leaks. Ensure no transaction marker, staging directory,
Secret, Authorization, Cookie, Session, private key, trading claim, or real
Vault path exists in production artifacts.

- [ ] **Step 6: Write the final evaluation report**

Answer all 17 required questions, record exact allowed/forbidden counts, test
counts, hashes, routes, zero external actions, and the honest isolation caveat.
Set the next action to `DESIGN_ISOLATED_AI_CLAIMS_BATCH` only when every Claims
and renderer gate passes.

- [ ] **Step 7: Final verification and report commit**

Run `git diff --check`, inspect status/name-status/stat, and commit only the
report and completed plan:

```bash
git add reports/tickflow_phase2b_typed_claims_eval.md \
  docs/superpowers/plans/2026-08-01-tickflow-phase2b-typed-claims.md
git commit -m "docs: close typed claims contract validation"
```

- [ ] **Step 8: Push and verify exact remote Head**

Push only `codex/tickflow-phase2-ai-review` to the writable fork remote, then
compare `git rev-parse HEAD` with `git ls-remote`. Require a clean worktree.
Do not create a PR, merge, or deploy.
