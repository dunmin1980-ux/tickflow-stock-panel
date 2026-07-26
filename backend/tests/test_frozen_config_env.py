from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _frozen_settings(tmp_path: Path, *, explicit_data_dir: Path | None = None) -> dict:
    cwd = tmp_path / "cwd"
    home = tmp_path / "home"
    resources = tmp_path / "resources"
    cwd.mkdir()
    home.mkdir()
    resources.mkdir()
    api_key_name = "TICKFLOW_API_KEY"
    (cwd / ".env").write_text(
        f"DATA_DIR=./poisoned-data\n{api_key_name}=poisoned-key\n",
        encoding="utf-8",
    )
    backend = Path(__file__).parents[1]
    env = os.environ.copy()
    env.update({"HOME": str(home), "PYTHONPATH": str(backend)})
    env.pop("DATA_DIR", None)
    env.pop("TICKFLOW_API_KEY", None)
    if explicit_data_dir is not None:
        env["DATA_DIR"] = str(explicit_data_dir)
        env["TICKFLOW_API_KEY"] = "explicit-key"
    code = f"""
import json
import sys
sys.frozen = True
sys._MEIPASS = {str(resources)!r}
from app.config import settings
print(json.dumps({{"data_dir": str(settings.data_dir), "key": settings.tickflow_api_key}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_frozen_settings_ignore_cwd_dotenv(tmp_path: Path) -> None:
    result = _frozen_settings(tmp_path)

    if sys.platform == "darwin":
        expected_data_dir = (
            tmp_path / "home" / "Library" / "Application Support" / "TickFlowStockPanel"
        )
    else:
        expected_data_dir = Path(sys.executable).resolve().parent / "data"

    assert result["data_dir"] == str(expected_data_dir)
    assert result["key"] == ""


def test_frozen_settings_still_accept_explicit_environment(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit-data"

    result = _frozen_settings(tmp_path, explicit_data_dir=explicit)

    assert result == {"data_dir": str(explicit), "key": "explicit-key"}
