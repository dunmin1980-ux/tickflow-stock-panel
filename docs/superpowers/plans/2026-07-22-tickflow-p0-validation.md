# TickFlow P0 Correctness And Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore incremental indicator history warmup, eliminate all backend undefined names, add a durable backend quality gate, and publish an evidence-backed validation report.

**Architecture:** Keep the fix narrow. `scan_enriched_parquet()` remains the single owner of enriched Parquet schema and cast compatibility; `_load_recent_history()` selects available history columns and catches only expected storage/Polars failures. Ruff F821 becomes the executable contract for import/name correctness, and GitHub Actions runs the same frozen backend checks used locally.

**Tech Stack:** Python 3.12, FastAPI, Polars, pytest, Ruff, uv, GitHub Actions, React/Vite/Playwright regression gates.

## Global Constraints

- Work only on branch `codex/tickflow-p0-validation` in the existing linked worktree.
- Do not configure or read any real TickFlow, Tushare, DeepSeek, Telegram, broker, or other credential.
- Do not deploy to the cloud, rebuild the production container, initialize authentication, or change Tailscale Serve.
- Keep `GOLD_WORKSPACE_ENABLED=false`; do not touch the independent Gold Shadow, n8n, Postgres, Telegram, OpenClaw, or broker integrations.
- Use test-first development for production code changes and preserve the existing public API.
- Do not copy or merge the historical dirty `tickflow-stock-panel-v0.1.84` worktree wholesale.
- Limit source changes to the P0/F821 root causes and the backend quality workflow.

---

### Task 1: Restore Incremental Indicator History Warmup

**Files:**
- Create: `backend/tests/test_pipeline_incremental_history.py`
- Modify: `backend/app/indicators/pipeline.py:1298-1321`

**Interfaces:**
- Consumes: `scan_enriched_parquet(source: Any, **kwargs: Any) -> pl.LazyFrame` from `backend/app/parquet.py`.
- Produces: `_load_recent_history(enriched_base: Path, symbols: list[str], days: int) -> pl.DataFrame` that returns matching recent rows, returns empty only for expected storage/Polars failures, and propagates programming errors.

- [ ] **Step 1: Write the failing regression tests**

Create `backend/tests/test_pipeline_incremental_history.py` with:

```python
"""Incremental indicator history loading regression tests."""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators import pipeline


def test_load_recent_history_reads_existing_enriched_rows(tmp_path):
    trade_date = date.today() - timedelta(days=1)
    partition = tmp_path / f"date={trade_date.isoformat()}"
    partition.mkdir()
    pl.DataFrame(
        {
            "symbol": ["000403.SZ"],
            "date": [trade_date],
            "open": [20.0],
            "high": [21.0],
            "low": [19.5],
            "close": [20.5],
            "volume": [1_000_000.0],
            "amount": [20_500_000.0],
            "raw_close": [20.5],
            "raw_high": [21.0],
            "raw_low": [19.5],
        }
    ).write_parquet(partition / "part.parquet")

    history = pipeline._load_recent_history(tmp_path, ["000403.SZ"], days=60)

    assert history.height == 1
    assert history["symbol"].to_list() == ["000403.SZ"]
    assert history["close"].to_list() == [20.5]


def test_load_recent_history_does_not_hide_programming_errors(monkeypatch, tmp_path):
    def broken_scan(*args, **kwargs):  # noqa: ARG001
        raise NameError("programming defect")

    monkeypatch.setattr(pipeline, "scan_enriched_parquet", broken_scan)

    with pytest.raises(NameError, match="programming defect"):
        pipeline._load_recent_history(tmp_path, ["000403.SZ"], days=60)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest tests/test_pipeline_incremental_history.py -q
```

Expected: 2 failed. The first receives an empty DataFrame after `_cast` raises `NameError`; the second fails because the programming error is swallowed.

- [ ] **Step 3: Implement the minimal root-cause fix**

Replace `_load_recent_history()` with:

```python
def _load_recent_history(enriched_base: Path, symbols: list[str], days: int) -> pl.DataFrame:
    """从已有 enriched parquet 加载最近 N 天的历史数据(用于增量模式的指标计算窗口)。

    只读基础行情列, 作为指标计算的历史前缀。
    """
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days + 30)  # 多读 30 天余量

    try:
        lf = (
            scan_enriched_parquet(str(enriched_base / "**" / "*.parquet"))
            .filter(
                (pl.col("symbol").is_in(symbols))
                & (pl.col("date") >= cutoff)
            )
            .sort(["symbol", "date"])
        )
        schema_names = set(lf.collect_schema().names())
        hist_cols = [c for c in ["symbol", "date", "open", "high", "low", "close",
                                 "volume", "amount", "raw_close", "raw_high", "raw_low"]
                    if c in schema_names]
        return lf.select(hist_cols).collect()
    except (FileNotFoundError, OSError, pl.exceptions.PolarsError) as e:
        logger.warning("历史数据加载失败: %s", e)
        return pl.DataFrame()
```

- [ ] **Step 4: Run targeted tests and verify GREEN**

Run:

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest \
  tests/test_pipeline_incremental_history.py \
  tests/test_parquet_schema_compat.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/indicators/pipeline.py \
  backend/tests/test_pipeline_incremental_history.py
git commit -m "fix: restore incremental indicator history warmup"
```

### Task 2: Eliminate Backend Undefined Names

**Files:**
- Modify: `backend/app/api/kline.py:776-808`
- Modify: `backend/app/jobs/daily_pipeline.py:12-105`
- Modify: `backend/app/services/kline_sync.py:12`

**Interfaces:**
- Consumes: existing endpoint and service signatures.
- Produces: identical runtime APIs with all names resolvable under Ruff F821.

- [ ] **Step 1: Run Ruff and verify RED**

Run after Task 1:

```bash
backend/.venv/bin/ruff check backend/app --select F821
```

Expected: 3 remaining failures for `asyncio`, `_date`, and `date`.

- [ ] **Step 2: Apply the minimal import/annotation fixes**

In `sync_minute_single()` in `backend/app/api/kline.py`, add a local import before the existing service imports:

```python
    import asyncio
```

In `backend/app/jobs/daily_pipeline.py`, add the module import:

```python
from datetime import date
```

and change the annotation to:

```python
    override_start_date: date | None = None,
```

In `backend/app/services/kline_sync.py`, change the existing datetime import to:

```python
from datetime import date, datetime, timedelta
```

- [ ] **Step 3: Verify GREEN with Ruff and compileall**

Run:

```bash
backend/.venv/bin/ruff check backend/app --select F821
PYTHONPATH=backend backend/.venv/bin/python -m compileall -q backend/app
```

Expected: both commands exit 0 with no undefined-name output.

- [ ] **Step 4: Run focused module and pipeline tests**

Run:

```bash
backend/.venv/bin/pytest \
  backend/tests/test_pipeline_incremental_history.py \
  backend/tests/test_pipeline_and_monitor_fixes.py \
  backend/tests/test_parquet_schema_compat.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/app/api/kline.py \
  backend/app/jobs/daily_pipeline.py \
  backend/app/services/kline_sync.py
git commit -m "fix: resolve backend undefined names"
```

### Task 3: Add Frozen Backend Quality Gate

**Files:**
- Create: `.github/workflows/backend-quality.yml`

**Interfaces:**
- Consumes: `backend/pyproject.toml`, `backend/uv.lock`, backend source and tests.
- Produces: GitHub Actions checks for compileall, Ruff F821, and complete backend pytest on backend-related pull requests, main pushes, and manual dispatch.

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/backend-quality.yml`:

```yaml
name: Backend Quality

on:
  pull_request:
    paths:
      - "backend/**"
      - ".github/workflows/backend-quality.yml"
  push:
    branches: [main]
    paths:
      - "backend/**"
      - ".github/workflows/backend-quality.yml"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  high-risk-checks:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true

      - name: Install Python
        run: uv python install 3.12

      - name: Install backend development dependencies
        run: uv sync --frozen --extra dev

      - name: Compile backend
        run: uv run --frozen python -m compileall -q app

      - name: Reject undefined names
        run: uv run --frozen ruff check app --select F821

      - name: Run backend tests
        run: uv run --frozen pytest -q
```

- [ ] **Step 2: Validate workflow syntax and required commands**

Run:

```bash
backend/.venv/bin/python - <<'PY'
from pathlib import Path
import yaml

path = Path('.github/workflows/backend-quality.yml')
payload = yaml.safe_load(path.read_text(encoding='utf-8'))
assert payload['name'] == 'Backend Quality'
text = path.read_text(encoding='utf-8')
for command in ('compileall -q app', 'ruff check app --select F821', 'pytest -q', '--frozen'):
    assert command in text
print('BACKEND_QUALITY_WORKFLOW_OK')
PY
```

Expected: `BACKEND_QUALITY_WORKFLOW_OK`.

- [ ] **Step 3: Commit Task 3**

```bash
git add .github/workflows/backend-quality.yml
git commit -m "ci: enforce backend undefined-name gate"
```

### Task 4: Full Validation And Evaluation Report

**Files:**
- Create: `reports/tickflow_p0_validation_eval.md`
- Update only if facts changed: `reports/tickflow_project_completion_ai_handoff.md`

**Interfaces:**
- Consumes: all Task 1-3 commits and current read-only cloud/runtime state.
- Produces: a durable report that separates fixed source status, rebuilt artifact status, deployed runtime status, historical evidence, and remaining manual gates.

- [ ] **Step 1: Run backend quality gates**

```bash
backend/.venv/bin/ruff check backend/app --select F821
PYTHONPATH=backend backend/.venv/bin/python -m compileall -q backend/app
backend/.venv/bin/pytest backend/tests -q
```

Expected: Ruff and compileall exit 0; all backend tests pass.

- [ ] **Step 2: Run frontend and browser regression gates**

```bash
cd frontend
pnpm test
pnpm exec tsc -b
pnpm exec playwright test
cd ..
```

Expected: Vitest, TypeScript, and Playwright complete without new failures.

- [ ] **Step 3: Run Gold Stage A regression if local Docker is available**

```bash
docker info >/dev/null 2>&1 && ./scripts/verify_gold_stage_a.sh
```

Expected when Docker is available: `STAGE_A_READY`. If Docker is unavailable, record `NOT_RUN_LOCAL_DOCKER_UNAVAILABLE`; do not start or reconfigure Docker automatically.

- [ ] **Step 4: Re-run the original real-Parquet reproduction**

Run the same one-row `000403.SZ` temporary Parquet script used in the handoff report.

Expected: `history_rows=1`, no `_cast` warning.

- [ ] **Step 5: Re-check cloud state without deployment**

Run read-only checks for cloud source HEAD, `/health`, `/api/auth/status`, selected container flags, loopback listeners, and Tailscale Serve.

Expected: cloud remains on the pre-fix source/image, `mode=none`, `configured=false`, workspace true, integrated Gold false. The report must state that source correctness is fixed locally but not deployed.

- [ ] **Step 6: Write the evaluation report**

Create `reports/tickflow_p0_validation_eval.md` with these visible sections:

1. Scope and verdict.
2. Root cause and reproduced failure.
3. Code changes and TDD evidence.
4. Static, backend, frontend, browser, Gold, and cloud verification table.
5. Artifact/deployment truth table.
6. Security and product boundaries.
7. Remaining P0/P1/manual gates.
8. Recommended next three tasks with acceptance criteria.
9. Exact evidence and commit index.

The verdict must not say production ready unless a newly built image/App is deployed and revalidated, which is outside this plan.

- [ ] **Step 7: Validate and commit the report**

```bash
git diff --check
git status --short
git add reports/tickflow_p0_validation_eval.md \
  reports/tickflow_project_completion_ai_handoff.md
git commit -m "docs: report P0 validation results"
```

Only stage `tickflow_project_completion_ai_handoff.md` if it actually changed.

- [ ] **Step 8: Final branch review and push**

Review the complete diff from `b524b94b335d51e630bdd08012c832d9522917c0` to HEAD, verify every plan requirement, then push:

```bash
git push fork HEAD:codex/tickflow-p0-validation
```

Expected: local HEAD equals `refs/heads/codex/tickflow-p0-validation` on the Fork, and the local worktree is clean.
