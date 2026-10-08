# Research Path P1 Implementation Plan

> Execute inline with TDD and one independent focused review.

**Goal:** Restore the existing research pages through the desktop App without changing research/accounting rules or evidence.

**Architecture:** Resolve the startup project and configured data root to physical paths. Validate derived research/runtime roots before startup side effects. Keep the backend's strict descendant symlink checks unchanged.

**Tech Stack:** Bash, Python standard library, pytest, existing FastAPI and browser UI.

**Spec:** User-approved bounded remediation of `research_paper_daily_invalid` in this task.

## Constraints And Review Focus

- No market/Provider/AI requests, Daily execution, or ledger writes.
- Project aliases and data-root aliases may resolve to their canonical roots.
- Default data stays within the physical project. An explicit `DATA_DIR` remains a user-selected allowed root; research/runtime descendants must stay within it.
- Descendant symlinks remain subject to the existing fail-closed research policy; never weaken `phase2_research_daily.py`.
- Git relocation metadata is P2, deferred. Use explicit existing Git metadata for this commit.

## Execution

- [x] RED: `tests/test_visual_startup_paths.py` produced 10 expected failures and 2 passes before implementation.
- [x] GREEN: changed startup path handling, tests, local launcher canonicalization, and concise runbook notes only; backend guards unchanged.
- [x] Related backend/launcher tests: 111 passed (14 path tests). Bash syntax, Ruff F821, 223-file no-write compile check, and `git diff --check` passed.
- [x] Desktop App LaunchServices restart passed; 48 real-browser desktop/mobile checks passed; 527 evidence files and 28 published records unchanged.
- [x] Independent focused review: NO_P0_P1_FINDINGS; regular-file-parent dry-run gap recorded as P2, not expanded into this repair.

Final delivery action: commit and push the existing branch, verify local/fork match and clean worktree, stop. Evidence is in `reports/tickflow_research_path_p1_eval.md`.
