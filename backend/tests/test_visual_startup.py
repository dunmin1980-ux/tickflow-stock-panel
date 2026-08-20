from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "start_tickflow_visual.sh"
COMMAND = REPO_ROOT / "TickFlow Visual Workbench.command"


def test_startup_script_is_local_only_and_never_kills_ports() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "host=127.0.0.1" in result.stdout
    assert "url=http://127.0.0.1:3018/paper-trading" in result.stdout
    assert "gold_workspace_enabled=false" in result.stdout
    source = SCRIPT.read_text()
    for forbidden in ("pkill", "kill -9", "lsof -ti", "0.0.0.0"):
        assert forbidden not in source


def test_startup_reuses_healthy_server_and_opens_browser(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    opened = tmp_path / "opened.txt"
    (fake_bin / "curl").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "open").write_text(f"#!/bin/sh\nprintf '%s' \"$1\" > {opened!s}\n")
    (fake_bin / "curl").chmod(0o755)
    (fake_bin / "open").chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "ALREADY_RUNNING" in result.stdout
    assert opened.read_text() == "http://127.0.0.1:3018/paper-trading"


def test_double_click_command_delegates_without_extra_runtime_logic() -> None:
    source = COMMAND.read_text()
    assert "scripts/start_tickflow_visual.sh" in source
    assert "sudo" not in source
    assert "docker" not in source


def test_startup_recovers_only_a_stale_owned_lock(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runtime = data_dir / "user_data" / "phase2_option_c_paper" / "visual_runtime"
    lock = runtime / ".startup.lock"
    lock.mkdir(parents=True)
    (lock / "owner.pid").write_text("999999\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text("#!/bin/sh\nexit 1\n")
    (fake_bin / "curl").chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DATA_DIR": str(data_dir),
            "TICKFLOW_VISUAL_PYTHON": str(tmp_path / "missing-python"),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "PYTHON_RUNTIME_MISSING" in result.stderr
    assert "STARTUP_IN_PROGRESS" not in result.stderr
    assert not lock.exists()


def test_startup_script_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_startup_installs_locked_stocksdk_bridge_dependency_when_missing(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / "stocksdk"
    bridge.mkdir()
    (bridge / "package.json").write_text('{"name":"test"}\n')
    (bridge / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm_log = tmp_path / "npm.log"
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)
    (fake_bin / "curl").write_text("#!/bin/sh\nexit 1\n")
    (fake_bin / "curl").chmod(0o755)
    (fake_bin / "npm").write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" > {npm_log!s}\n"
        f"mkdir -p {bridge!s}/node_modules/stock-sdk\n"
        f"printf '{{}}\\n' > {bridge!s}/node_modules/stock-sdk/package.json\n"
    )
    (fake_bin / "npm").chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DATA_DIR": str(tmp_path / "data"),
            "TICKFLOW_STOCKSDK_BRIDGE_DIR": str(bridge),
            "TICKFLOW_VISUAL_PYTHON": str(fake_python),
            "TICKFLOW_VISUAL_STARTUP_WAIT_SECONDS": "1",
            "TICKFLOW_VISUAL_SKIP_BROWSER": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "INSTALLING_STOCKSDK_BRIDGE" in result.stdout
    assert npm_log.read_text().strip() == "ci --ignore-scripts"
    assert (bridge / "node_modules" / "stock-sdk" / "package.json").is_file()
