from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.workspace.storage_validation import WorkspaceStorageError, validate_storage_files


def test_missing_optional_workspace_files_are_valid(tmp_path: Path) -> None:
    validate_storage_files(tmp_path)


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("preferences.json", "{"),
        ("secrets.json", "[]"),
        ("auth.json", '{"sessions": []}'),
        ("ai_stock_reports.json", "{}"),
        ("ai_market_recaps.json", '["not-an-object"]'),
        ("backtest_summaries.json", "[{}]"),
    ],
)
def test_corrupt_or_lossy_workspace_files_fail_closed(
    tmp_path: Path,
    filename: str,
    payload: str,
) -> None:
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    (user_data / filename).write_text(payload, encoding="utf-8")

    with pytest.raises(WorkspaceStorageError, match=filename):
        validate_storage_files(tmp_path)


def test_valid_storage_files_pass_without_exposing_values(tmp_path: Path) -> None:
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    files = {
        "preferences.json": {"indices_nav_pinned": True},
        "secrets.json": {"tickflow_api_key": "local-only"},
        "auth.json": {
            "password_salt": "a" * 32,
            "password_hash": "b" * 64,
            "sessions": {},
        },
        "ai_stock_reports.json": [],
        "ai_market_recaps.json": [],
        "backtest_summaries.json": [],
    }
    for filename, payload in files.items():
        (user_data / filename).write_text(json.dumps(payload), encoding="utf-8")

    assert validate_storage_files(tmp_path) == tuple(files)
