"""Bounded, secret-free backtest summaries for workspace synchronization."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.workspace.locks import resource_lock
from app.workspace.models import ResourceName

logger = logging.getLogger(__name__)

SUMMARY_FIELDS = frozenset(
    {
        "id",
        "task",
        "strategy_id",
        "parameters_digest",
        "stats",
        "started_at",
        "finished_at",
        "data_as_of",
        "engine",
        "execution_target",
    }
)
MAX_STATS_BYTES = 16 * 1024
MAX_SUMMARIES = 100
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_SECRET_KEY_PARTS = ("secret", "token", "password", "cookie", "webhook", "content", "markdown")
_FORBIDDEN_RECORD_KEYS = frozenset(
    {"trades", "trade_details", "trade_records", "transactions", "orders"}
)


def _contains_unsafe_key(value: Any) -> bool:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key).casefold()
            if (
                key in _FORBIDDEN_RECORD_KEYS
                or key == "key"
                or key.endswith("_key")
                or any(part in key for part in _SECRET_KEY_PARTS)
                or _contains_unsafe_key(nested)
            ):
                return True
    elif isinstance(value, list):
        return any(_contains_unsafe_key(item) for item in value)
    return False


def _validate_summary(summary: dict) -> dict:
    if not isinstance(summary, dict) or set(summary) != SUMMARY_FIELDS:
        raise ValueError("backtest summary must contain exactly the allowed fields")

    for key in SUMMARY_FIELDS - {"stats"}:
        value = summary[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"backtest summary {key} must be a non-empty string")
    if _DIGEST_PATTERN.fullmatch(summary["parameters_digest"]) is None:
        raise ValueError("parameters_digest must be a lowercase SHA-256 digest")

    stats = summary["stats"]
    if not isinstance(stats, dict) or _contains_unsafe_key(stats):
        raise ValueError("backtest stats contain unsupported detail or secret fields")
    try:
        encoded_stats = json.dumps(
            stats,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("backtest stats must be finite JSON") from exc
    if len(encoded_stats) > MAX_STATS_BYTES:
        raise ValueError("backtest stats exceed the synchronization size limit")
    return copy.deepcopy(summary)


class BacktestSummaryStore:
    def __init__(self, filename: str = "backtest_summaries.json") -> None:
        self.filename = filename
        self._lock = resource_lock(ResourceName.BACKTEST_SUMMARIES)

    def _path(self) -> Path:
        from app.config import settings

        path = settings.data_dir / "user_data" / self.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _read_unlocked(self) -> list[dict]:
        path = self._path()
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("%s malformed: %s", self.filename, exc)
            return []
        if not isinstance(raw, list):
            return []

        safe: list[dict] = []
        for item in raw:
            try:
                safe.append(_validate_summary(item))
            except ValueError:
                logger.warning("unsafe backtest summary ignored in %s", self.filename)
        return safe

    def list_summaries(self) -> list[dict]:
        with self._lock:
            return self._read_unlocked()

    def _atomic_write(self, summaries: list[dict]) -> None:
        path = self._path()
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(summaries, handle, indent=2, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            temp_path = None
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def append(self, summary: dict) -> dict:
        validated = _validate_summary(summary)
        with self._lock:
            summaries = self._read_unlocked()
            summaries.append(validated)
            self._atomic_write(summaries[-MAX_SUMMARIES:])
        return copy.deepcopy(validated)


_store = BacktestSummaryStore()


def list_summaries() -> list[dict]:
    return _store.list_summaries()


def append(summary: dict) -> dict:
    return _store.append(summary)
