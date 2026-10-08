"""Startup path contracts; all mutations stay in pytest temporary directories."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "start_tickflow_visual.sh"
PAPER = Path("user_data/phase2_option_c_paper")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path.resolve() / "project with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / "backend/data").mkdir(parents=True)
    shutil.copyfile(SCRIPT, root / "scripts/start_tickflow_visual.sh")
    return root


def dry_run(root: Path, data_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DATA_DIR"}
    if data_dir is not None:
        env["DATA_DIR"] = str(data_dir)
    return subprocess.run(
        ["bash", str(root / "scripts/start_tickflow_visual.sh"), "--dry-run"],
        cwd=root.parent, env=env, text=True, capture_output=True, check=False,
    )


@pytest.mark.parametrize("alias", [False, True], ids=["physical", "project-alias"])
def test_launch_paths_are_canonical_before_backend_receives_them(project: Path, alias: bool):
    entry = project
    if alias:
        entry = project.parent / "project alias"
        entry.symlink_to(project, target_is_directory=True)
    result = dry_run(entry)
    assert result.returncode == 0, result.stderr
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert fields["data_dir"] == str(project / "backend/data")
    assert fields["project_root"] == str(project)
    assert fields["research_daily_root"] == str(project / "backend/data" / PAPER / "reference_account/days")
    assert not (project / "backend/data" / PAPER).exists()


def test_in_root_data_alias_is_resolved(project: Path):
    alias = project / "data alias"
    alias.symlink_to(project / "backend/data", target_is_directory=True)
    result = dry_run(project, alias)
    assert result.returncode == 0, result.stderr
    assert f"data_dir={project / 'backend/data'}\n" in result.stdout


def test_default_data_cannot_escape_project_even_with_common_prefix(project: Path):
    outside = project.parent / (project.name + "-outside")
    outside.mkdir()
    data = project / "backend/data"
    data.rmdir()
    data.symlink_to(outside, target_is_directory=True)
    result = dry_run(project)
    assert result.returncode != 0
    assert "VISUAL_DATA_PATH_ESCAPE" in result.stderr
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("relative", [PAPER, PAPER / "reference_account", PAPER / "reference_account/days", PAPER / "inputs", PAPER / "visual_runtime"])
def test_research_descendants_cannot_escape_allowed_data_root(project: Path, relative: Path):
    data = project / "backend/data"
    outside = project.parent / "outside"
    outside.mkdir()
    alias = data / relative
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(outside, target_is_directory=True)
    result = dry_run(project)
    assert result.returncode != 0
    assert "VISUAL_RESEARCH_PATH_ESCAPE" in result.stderr
    assert list(outside.iterdir()) == []


def test_explicit_external_data_root_remains_a_supported_allowed_root(project: Path):
    data = project.parent / "explicit data"
    result = dry_run(project, data)
    assert result.returncode == 0, result.stderr
    assert f"data_dir={data}\n" in result.stdout
    assert not data.exists()


def test_current_physical_checkout_dry_run_is_canonical():
    result = dry_run(REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert f"project_root={REPO_ROOT}\n" in result.stdout
    assert f"data_dir={REPO_ROOT / 'backend/data'}\n" in result.stdout


@pytest.mark.parametrize("alias", [False, True], ids=["physical", "project-alias"])
def test_canonical_launch_paths_allow_real_research_readback(project: Path, alias: bool):
    from app.services.phase2_research_panel import build_research_panel
    from app.services.phase2_strategy_graphics import build_strategy_graphics
    from app.services.phase2_visual_workbench import Phase2VisualWorkbenchService
    from tests.test_phase2_visual_workbench import FakeDailyGateway, TODAY, daily_builder

    paper = project / "backend/data" / PAPER
    gateway = FakeDailyGateway()
    service = Phase2VisualWorkbenchService(
        repo_root=REPO_ROOT, state_root=paper / "reference_account", input_root=paper / "inputs",
        daily_input_builder=daily_builder(paper, gateway),
    )
    service.run(TODAY)
    entry = project
    if alias:
        entry = project.parent / "project alias"
        entry.symlink_to(project, target_is_directory=True)
    result = dry_run(entry)
    assert result.returncode == 0, result.stderr
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines())
    data = Path(fields["data_dir"]) / PAPER
    panel = build_research_panel(data / "inputs", TODAY, state_root=data / "reference_account")
    graphics = build_strategy_graphics(data / "inputs", TODAY, panel, data / "reference_account")
    assert panel["status"] == graphics["status"] == "READY"
    assert len(panel["strategies"]) == 6
    assert panel["engine"]["summary"]["paper_action"] == "HOLD"
    assert gateway.calls == 1  # Only fixture preparation; readback performs no fetch.


def test_research_daily_still_rejects_a_symlinked_day(tmp_path: Path):
    from datetime import date
    from app.services.phase2_research_daily import load_paper_context

    root = tmp_path.resolve() / "state"
    (root / "days").mkdir(parents=True)
    outside = tmp_path.resolve() / "outside"
    outside.mkdir()
    (outside / "chenquant_daily.json").write_text("{}")
    (root / "days/2026-10-08").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="research_paper_daily_invalid"):
        load_paper_context(root, date(2026, 10, 8), "0" * 64)
