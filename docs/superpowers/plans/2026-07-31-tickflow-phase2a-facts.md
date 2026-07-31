# TickFlow Phase 2A Deterministic Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate and validate three deterministic, provenance-complete Facts JSON documents using only retained Phase 1 evidence.

**Architecture:** A pure service module loads a fixed evidence set, builds canonical Facts dictionaries, and records provenance for every numeric leaf. Two thin CLIs build and validate artifacts; tests use synthetic evidence fixtures and the committed Phase 1 evidence without network access.

**Tech Stack:** Python 3.12, standard-library JSON/hash/path APIs, Pydantic-free deterministic dictionaries, pytest, existing Polars indicator contract references.

## Global Constraints

- Make zero TickFlow API requests and zero AI calls.
- Read but never modify Phase 1 evidence or request audits.
- Restrict symbols to `000403.SZ`, `600489.SH`, and `300059.SZ`.
- Preserve all four supplier-pending items.
- Emit no trading-action fields, secrets, NaN, or Infinity.
- Mark indicators `NOT_IMPLEMENTED` when the retained qfq series is unavailable.
- Do not deploy, configure AI, write to Obsidian, or start Paper Trading.

---

### Task 1: Define the external Facts and provenance contract

**Files:**
- Create: `backend/tests/test_phase2_facts.py`
- Create: `backend/app/services/phase2_facts.py`

**Interfaces:**
- Produces: `build_symbol_facts(repo_root: Path, symbol: str) -> dict[str, Any]`
- Produces: `validate_facts_document(repo_root: Path, facts: Mapping[str, Any]) -> list[str]`
- Produces: `canonical_json_bytes(value: Any) -> bytes`

- [ ] Write failing tests for fixed symbols, source hashes, numeric provenance,
  raw/qfq separation, unavailable indicators, vendor-pending metadata, finite
  numbers, secret shapes, and forbidden trading fields.
- [ ] Run `PYTHONPATH=. .venv/bin/pytest tests/test_phase2_facts.py -q` and confirm
  failures are caused by the missing service.
- [ ] Implement the minimum source loader, Facts builder, canonical serializer,
  and validator required by those tests.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Add deterministic build and validation CLIs

**Files:**
- Create: `backend/scripts/build_phase2_facts.py`
- Create: `backend/scripts/validate_phase2_facts.py`
- Modify: `backend/tests/test_phase2_facts.py`

**Interfaces:**
- Build CLI: `--repo-root PATH --output-dir PATH`
- Validation CLI: `--repo-root PATH --facts-dir PATH`
- Build result: canonical JSON summary with status, source-set hash, output
  hashes, and request count zero.

- [ ] Add failing CLI tests for atomic output, repeated byte-identical builds,
  source mutation rejection, no partial publication, and a zero API count.
- [ ] Run the focused tests and verify the new cases fail before implementation.
- [ ] Implement temporary-directory publication, output manifest generation, and
  independent validation CLI behavior without importing any data provider.
- [ ] Re-run the focused tests and confirm all CLI cases pass.

### Task 3: Materialize three audited Facts documents

**Files:**
- Create: `reports/phase2_facts/000403SZ_facts.json`
- Create: `reports/phase2_facts/600489SH_facts.json`
- Create: `reports/phase2_facts/300059SZ_facts.json`
- Create: `reports/phase2_facts/build_manifest.json`

**Interfaces:**
- Consumes the CLIs from Task 2.
- Produces four canonical, deterministic JSON files.

- [ ] Hash all Phase 1 observation and request-audit inputs before generation.
- [ ] Run `PYTHONPATH=. .venv/bin/python scripts/build_phase2_facts.py --repo-root .. --output-dir ../reports/phase2_facts`.
- [ ] Run the same command again and compare directory hashes byte-for-byte.
- [ ] Run `PYTHONPATH=. .venv/bin/python scripts/validate_phase2_facts.py --repo-root .. --facts-dir ../reports/phase2_facts`.
- [ ] Re-hash Phase 1 inputs and prove the pre/post manifests are identical.

### Task 4: Close out Phase 2A evidence and regression testing

**Files:**
- Create: `reports/tickflow_phase2a_facts_eval.md`

**Interfaces:**
- Records three validation outcomes, unsupported-number count, source hash
  result, idempotency result, test counts, and all frozen boundaries.

- [ ] Run the focused Phase 2 Facts tests.
- [ ] Run backend `compileall`, Ruff F821, and the complete pytest suite.
- [ ] Scan changed files for secret shapes, provider imports, AI calls,
  `intraday_batch`, and trading-action fields.
- [ ] Write the evaluation report from observed command outputs only.
- [ ] Run `git diff --check`, inspect the exact staged file list, commit, push,
  verify remote Head, and stop at `PHASE2A_FACTS_READY`.
