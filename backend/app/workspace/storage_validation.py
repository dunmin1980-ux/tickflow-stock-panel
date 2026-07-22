"""Fail-closed validation for persisted workspace and credential file shapes."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from app.services.backtest_summaries import _validate_summary

_OBJECT_FILES = ("preferences.json", "secrets.json", "auth.json")
_REPORT_FILES = ("ai_stock_reports.json", "ai_market_recaps.json")
_SUMMARY_FILE = "backtest_summaries.json"
_HEX = re.compile(r"[0-9a-f]+")


class WorkspaceStorageError(RuntimeError):
    """Raised when a persisted file would otherwise be silently discarded."""


def _invalid(filename: str, reason: str) -> WorkspaceStorageError:
    return WorkspaceStorageError(f"{filename}: {reason}")


def _parse_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _invalid(path.name, "invalid JSON") from exc


def _validate_auth(filename: str, payload: dict) -> None:
    sessions = payload.get("sessions", {})
    if not isinstance(sessions, dict):
        raise _invalid(filename, "sessions must be an object")
    for token, expires_at in sessions.items():
        if (
            not isinstance(token, str)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(expires_at)
        ):
            raise _invalid(filename, "session entry is invalid")

    salt = payload.get("password_salt")
    password_hash = payload.get("password_hash")
    if salt is None and password_hash is None:
        return
    if (
        not isinstance(salt, str)
        or not isinstance(password_hash, str)
        or len(salt) != 32
        or len(password_hash) != 64
        or _HEX.fullmatch(salt) is None
        or _HEX.fullmatch(password_hash) is None
    ):
        raise _invalid(filename, "password verifier is invalid")


def validate_storage_files(data_dir: Path) -> tuple[str, ...]:
    """Validate files that tolerant runtime loaders can otherwise erase logically."""
    user_data = Path(data_dir) / "user_data"
    if not user_data.exists():
        return ()
    if not user_data.is_dir():
        raise WorkspaceStorageError("user_data: expected a directory")

    validated: list[str] = []
    for filename in _OBJECT_FILES:
        path = user_data / filename
        if not path.exists():
            continue
        payload = _parse_json(path)
        if not isinstance(payload, dict):
            raise _invalid(filename, "top level must be an object")
        if filename == "auth.json":
            _validate_auth(filename, payload)
        validated.append(filename)

    for filename in _REPORT_FILES:
        path = user_data / filename
        if not path.exists():
            continue
        payload = _parse_json(path)
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise _invalid(filename, "top level must be an array of objects")
        validated.append(filename)

    summary_path = user_data / _SUMMARY_FILE
    if summary_path.exists():
        payload = _parse_json(summary_path)
        if not isinstance(payload, list):
            raise _invalid(_SUMMARY_FILE, "top level must be an array")
        try:
            for item in payload:
                _validate_summary(item)
        except (TypeError, ValueError) as exc:
            raise _invalid(_SUMMARY_FILE, "summary entry is invalid") from exc
        validated.append(_SUMMARY_FILE)

    return tuple(validated)
