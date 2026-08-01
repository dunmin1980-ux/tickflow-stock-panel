# TickFlow Phase 2B Facts-Only AI Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate and strictly validate three non-trading, facts-only AI stock-review samples and route them into an isolated Obsidian preview.

**Architecture:** A new offline service converts committed Phase 2A Facts into a prompt and exact numeric claim catalog, validates model output, creates deterministic frontmatter and routes previews. A CLI script runs Codex once per fixed symbol through an injected provider boundary and atomically publishes only a complete validated batch.

**Tech Stack:** Python 3.12, pytest, PyYAML, existing `ai_provider` Codex CLI isolation helpers, canonical JSON, SHA-256, atomic directory replacement.

## Global Constraints

- Fixed scope: `000403.SZ`, `600489.SH`, `300059.SZ` only.
- No TickFlow API request, cloud mutation, cloud deployment or Phase 1 evidence mutation.
- No application AI key configuration and no OpenAI-compatible provider call.
- Codex CLI may be called once per symbol with no retry.
- No Paper Trading, Telegram, OpenClaw, Integrated Gold or real Obsidian write.
- Every output keeps `can_publish=false`, `trading_advice=false` and `verification_status=pending`.
- Vendor pending: `intraday_batch_entitlement`, `first_30m_bucket_includes_09_30`, `volume_unit`, `amount_unit`.

---

### Task 1: Prompt And Numeric Claim Catalog

**Files:**
- Create: `backend/app/services/phase2_ai_review.py`
- Test: `backend/tests/test_phase2_ai_review.py`

**Interfaces:**
- Consumes: `validate_facts_document(Path, Mapping)` and canonical Phase 2A Facts JSON.
- Produces: `build_review_prompt(facts, facts_sha256) -> ReviewPrompt` and `build_numeric_claim_catalog(facts, facts_sha256) -> dict[str, ClaimSource]`.

- [ ] Write failing tests for the ten headings, facts-only restrictions, exact display literals, omitted unit-pending amounts and source pointers.
- [ ] Run `PYTHONPATH=. .venv/bin/pytest tests/test_phase2_ai_review.py -q` and confirm the tests fail because the module is absent.
- [ ] Implement immutable prompt/catalog dataclasses and deterministic rendering.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Review Validator And Frontmatter

**Files:**
- Modify: `backend/app/services/phase2_ai_review.py`
- Test: `backend/tests/test_phase2_ai_review.py`

**Interfaces:**
- Produces: `validate_review_body(body, facts, facts_sha256) -> ReviewValidation`, `render_review_document(...) -> str`, and `validate_review_document(...) -> ReviewValidation`.

- [ ] Add failing tests for allowed numeric claims, unsupported numbers, section-number exceptions, forbidden transaction language, fabricated scopes, secrets and malformed headings.
- [ ] Add failing tests that parse frontmatter and require exact safe values.
- [ ] Implement numeric token extraction with exact catalog matching and source-pointer evidence.
- [ ] Implement body/frontmatter validation and deterministic Markdown rendering.
- [ ] Run the focused tests and confirm all cases pass.

### Task 3: Isolated Preview Router And Batch Generator

**Files:**
- Modify: `backend/app/services/phase2_ai_review.py`
- Create: `backend/scripts/generate_phase2_ai_reviews.py`
- Create: `backend/scripts/validate_phase2_ai_review.py`
- Test: `backend/tests/test_phase2_ai_review.py`

**Interfaces:**
- Produces: `route_review_preview(status) -> Literal["inbox", "rejected"]`, a single-run generator, and an offline validator CLI.

- [ ] Add failing tests for inbox/rejected routing, absence of automatic reviewed writes, fixed symbols, exactly-once provider calls, no retry and atomic batch publication.
- [ ] Implement staging plus atomic directory replacement using `app.services.atomic_directory`.
- [ ] Implement the offline validator summary with `REVIEW_VALID`, `REVIEW_REJECTED` and `REVIEW_NEEDS_VERIFICATION` only.
- [ ] Run focused tests, compileall and Ruff F821.

### Task 4: Three One-Shot Codex CLI Samples

**Files:**
- Generate: `reports/phase2_ai_samples/*.md`
- Generate: `reports/phase2_ai_samples/generation_audit.json`
- Generate: `reports/phase2_obsidian_preview/inbox/*.md`
- Generate: `reports/phase2_obsidian_preview/validation_manifest.json`

**Interfaces:**
- Consumes: three committed Facts files and installed Codex CLI authentication.
- Produces: one provider attempt per symbol with provider, model source, duration, input Facts hash, output hash and validation status.

- [ ] Run the generator once with `--provider codex_cli`; do not retry any symbol.
- [ ] Run the offline validator and verify no unsupported numeric claims, transaction language, financial fabrication or news fabrication.
- [ ] Confirm `reports/phase2_obsidian_preview/reviewed/` is empty and no real vault path was touched.

### Task 5: Documentation, Full Verification And Delivery

**Files:**
- Create: `reports/phase2_manual_review_checklist.md`
- Create: `docs/tickflow_phase2_obsidian_runbook.md`
- Create: `reports/tickflow_phase2_ai_review_eval.md`

**Interfaces:**
- Produces: the final Phase 2 evidence package and status.

- [ ] Document the human verification checklist and isolated inbox/rejected workflow.
- [ ] Record Phase 1 merge, Base SHA, Facts traceability, provider evidence, sample results, source immutability and all frozen boundaries.
- [ ] Run focused tests, full backend pytest, compileall, Ruff F821, both offline validators and a secret scan.
- [ ] Verify Phase 1 evidence/request-audit aggregate hashes remain unchanged and TickFlow request count remains zero.
- [ ] Commit implementation and artifacts, push only `codex/tickflow-phase2-ai-review`, verify local/fork SHA match, and stop without PR, merge or deployment.
