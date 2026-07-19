"""Persistent storage for the isolated gold-monitor shadow pipeline."""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_DAYS = 30
MAX_RECORDS = 20_000
_LOCK = threading.Lock()
_REQUIRED_STRING_FIELDS = (
    "observed_at",
    "market_date",
    "symbol",
    "quote_source",
    "state",
)
_NUMERIC_FIELDS = (
    "price",
    "previous_close",
    "legacy_reference_60",
    "native_ema60",
    "P",
    "V",
    "A",
)


def validate_gold_snapshot(snapshot: dict[str, Any]) -> None:
    """Validate the shared persisted snapshot shape without domain reinterpretation."""
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    if type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    for field in _REQUIRED_STRING_FIELDS:
        if not isinstance(snapshot.get(field), str):
            raise ValueError(f"{field} must be a string")
    if snapshot["quote_source"] != "tickflow":
        raise ValueError("quote_source must be tickflow")
    if snapshot["symbol"] != "600489.SH":
        raise ValueError("symbol must be 600489.SH")
    if type(snapshot.get("quote_ts")) is not int:
        raise ValueError("quote_ts must be an integer")
    for field in _NUMERIC_FIELDS:
        value = snapshot.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be numeric")
        if not math.isfinite(value):
            raise ValueError(f"{field} must be finite")
    for field in ("price", "previous_close", "legacy_reference_60"):
        if snapshot[field] <= 0:
            raise ValueError(f"{field} must be positive")
    for field in ("candidate_signals", "new_candidate_signals"):
        value = snapshot.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{field} must be a list of strings")


class GoldShadowStorageReadError(RuntimeError):
    """Raised when existing shadow storage cannot be read reliably."""


class GoldShadowStore:
    """Store gold shadow data below ``data_dir/user_data/gold_shadow``."""

    def __init__(
        self,
        data_dir: Path,
        *,
        max_records: int = MAX_RECORDS,
        max_days: int = MAX_DAYS,
    ) -> None:
        self.root = Path(data_dir) / "user_data" / "gold_shadow"
        self.max_records = max_records
        self.max_days = max_days

    def append_snapshot(self, snapshot: dict[str, Any], *, now: date | datetime | None = None) -> None:
        """Append a snapshot and immediately apply the retention policy."""
        self._validate_snapshot(snapshot)
        with _LOCK:
            existing_rows = self._read_jsonl_locked("snapshots.jsonl", raise_on_failure=True)
            self._append_jsonl_locked("snapshots.jsonl", snapshot)
            self._prune_snapshots_locked(now or date.today(), [*existing_rows, snapshot])

    def commit_evaluation(
        self, snapshot: dict[str, Any], *, now: date | datetime | None = None
    ) -> dict[str, Any]:
        """Durably commit a canonical snapshot before repairing candidate projections."""
        candidate_signals = snapshot.get("candidate_signals")
        row = {**snapshot, "new_candidate_signals": []}
        self._validate_snapshot(row)
        with _LOCK:
            existing_rows = self._read_jsonl_locked("snapshots.jsonl", raise_on_failure=True)
            existing = self._find_evaluation_locked(existing_rows, row)
            if existing is not None:
                self._reconcile_candidate_projection_locked(existing_rows)
                self._prune_snapshots_locked(now or date.today(), existing_rows)
                return dict(existing)

            claimed_keys = set(self._canonical_candidate_events_locked(existing_rows))
            new_signals: list[str] = []
            seen_signals: set[str] = set()
            for signal in candidate_signals:
                key = self._candidate_key(signal, row["symbol"], row["market_date"])
                if signal not in seen_signals and key not in claimed_keys:
                    new_signals.append(signal)
                    claimed_keys.add(key)
                seen_signals.add(signal)
            row["new_candidate_signals"] = new_signals
            self._validate_snapshot(row)

            # This fsynced append is the commit point. Everything after it is a projection.
            self._append_jsonl_locked("snapshots.jsonl", row)
            canonical_rows = [*existing_rows, row]
            self._reconcile_candidate_projection_locked(canonical_rows)
            self._prune_snapshots_locked(now or date.today(), canonical_rows)
            return dict(row)

    def list_snapshots(self, limit: int = MAX_RECORDS) -> list[dict[str, Any]]:
        with _LOCK:
            rows = self._read_jsonl_locked("snapshots.jsonl", raise_on_failure=True)
        rows.sort(
            key=lambda row: (
                str(row.get("observed_at", "")),
                str(row.get("market_date", "")),
            ),
            reverse=True,
        )
        return rows[: max(0, limit)]

    def latest_snapshot(self) -> dict[str, Any] | None:
        rows = self.list_snapshots(limit=1)
        return rows[0] if rows else None

    def register_candidate(self, signal: str, symbol: str, market_date: str) -> bool:
        """Register exactly one candidate for (signal, symbol, market_date)."""
        if symbol != "600489.SH":
            raise ValueError("symbol must be 600489.SH")
        key = self._candidate_key(signal, symbol, market_date)
        with _LOCK:
            snapshots = self._read_jsonl_locked("snapshots.jsonl", raise_on_failure=True)
            self._reconcile_candidate_projection_locked(snapshots)
            state = self._read_candidate_state_locked()
            event_keys = self._read_candidate_event_keys_locked()
            state_keys = set(state)
            if key in event_keys:
                if key not in state_keys:
                    self._write_json_state_locked(
                        "candidate_state.json", {"keys": sorted(state_keys | event_keys)}
                    )
                return False
            if key in state_keys:
                return False
            self._append_jsonl_locked(
                "candidates.jsonl",
                {
                    "key": key,
                    "signal": signal,
                    "symbol": symbol,
                    "market_date": market_date,
                },
            )
            self._write_json_state_locked(
                "candidate_state.json", {"keys": sorted(state_keys | event_keys | {key})}
            )
            return True

    def list_candidates(self, limit: int | None = None) -> list[dict[str, Any]]:
        with _LOCK:
            snapshots = self._read_jsonl_locked("snapshots.jsonl", raise_on_failure=True)
            self._reconcile_candidate_projection_locked(snapshots)
            rows = self._read_jsonl_locked("candidates.jsonl", raise_on_failure=True)
        rows.reverse()
        return rows if limit is None else rows[: max(0, limit)]

    def append_comparison(self, comparison: dict[str, Any]) -> None:
        with _LOCK:
            self._append_jsonl_locked("comparisons.jsonl", comparison)

    def list_comparisons(self, limit: int = MAX_RECORDS) -> list[dict[str, Any]]:
        with _LOCK:
            rows = self._read_jsonl_locked("comparisons.jsonl", raise_on_failure=True)
        rows.reverse()
        return rows[: max(0, limit)]

    def write_health(
        self,
        *,
        last_success: str | None,
        consecutive_failures: int,
        last_error: dict[str, str] | None,
    ) -> None:
        state = {
            "last_success": last_success,
            "consecutive_failures": consecutive_failures,
            "last_error": last_error,
        }
        with _LOCK:
            self._write_json_state_locked("health.json", state)

    def read_health(self) -> dict[str, Any] | None:
        with _LOCK:
            return self._read_json_state_locked("health.json", raise_on_failure=True)

    def read_holidays(self) -> list[str]:
        """Read deployment-provided holidays without creating or modifying them."""
        path = self.root / "holidays.json"
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("gold_shadow_store holidays read failed: %s", exc)
            return []
        return value if isinstance(value, list) else []

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _append_jsonl_locked(self, filename: str, row: dict[str, Any]) -> None:
        self._ensure_root()
        path = self.root / filename
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(path, 0o600)
        with os.fdopen(descriptor, "r+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write(
                (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            )
            handle.flush()
            os.fsync(handle.fileno())
        self._fsync_root_locked()

    def _read_jsonl_locked(
        self, filename: str, *, raise_on_failure: bool = False
    ) -> list[dict[str, Any]]:
        path = self.root / filename
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        logger.warning("gold_shadow_store malformed %s line %d", filename, line_number)
                        continue
                    if not isinstance(row, dict):
                        logger.warning("gold_shadow_store malformed %s line %d", filename, line_number)
                        continue
                    rows.append(row)
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("gold_shadow_store read %s failed: %s", filename, exc)
            if raise_on_failure:
                raise GoldShadowStorageReadError(f"unable to read {filename}") from exc
        return rows

    def _prune_snapshots_locked(
        self, now: date | datetime, rows: list[dict[str, Any]] | None = None
    ) -> None:
        if rows is None:
            rows = self._read_jsonl_locked("snapshots.jsonl", raise_on_failure=True)
        cutoff = (now.date() if isinstance(now, datetime) else now) - timedelta(days=self.max_days - 1)
        kept: list[dict[str, Any]] = []
        for row in rows:
            try:
                market_date = date.fromisoformat(str(row["market_date"]))
            except (KeyError, TypeError, ValueError):
                logger.warning("gold_shadow_store malformed snapshot market_date")
                continue
            if market_date >= cutoff:
                kept.append(row)
        kept.sort(key=lambda row: str(row.get("observed_at", "")))
        if len(kept) > self.max_records:
            kept = kept[-self.max_records :]
        self._replace_jsonl_locked("snapshots.jsonl", kept)

    def _read_candidate_state_locked(self) -> list[str]:
        value = self._read_json_state_locked("candidate_state.json")
        keys = value.get("keys", []) if isinstance(value, dict) else value
        return [key for key in keys if isinstance(key, str)] if isinstance(keys, list) else []

    def _read_candidate_event_keys_locked(self) -> set[str]:
        return {
            key
            for row in self._read_jsonl_locked("candidates.jsonl")
            if isinstance(key := row.get("key"), str)
        }

    @staticmethod
    def _candidate_key(signal: str, symbol: str, market_date: str) -> str:
        return f"{signal}|{symbol}|{market_date}"

    @staticmethod
    def _evaluation_key(snapshot: dict[str, Any]) -> tuple[object, object, object]:
        return snapshot.get("symbol"), snapshot.get("market_date"), snapshot.get("quote_ts")

    def _find_evaluation_locked(
        self, rows: list[dict[str, Any]], snapshot: dict[str, Any]
    ) -> dict[str, Any] | None:
        key = self._evaluation_key(snapshot)
        return next((row for row in rows if self._evaluation_key(row) == key), None)

    def _canonical_candidate_events_locked(
        self, snapshots: list[dict[str, Any]]
    ) -> dict[str, dict[str, str]]:
        events: dict[str, dict[str, str]] = {}
        for snapshot in snapshots:
            symbol = snapshot.get("symbol")
            market_date = snapshot.get("market_date")
            signals = snapshot.get("new_candidate_signals")
            if not isinstance(symbol, str) or not isinstance(market_date, str):
                continue
            if not isinstance(signals, list):
                continue
            for signal in signals:
                if not isinstance(signal, str):
                    continue
                key = self._candidate_key(signal, symbol, market_date)
                events.setdefault(
                    key,
                    {
                        "key": key,
                        "signal": signal,
                        "symbol": symbol,
                        "market_date": market_date,
                    },
                )
        return events

    def _reconcile_candidate_projection_locked(
        self, snapshots: list[dict[str, Any]]
    ) -> None:
        canonical_events = self._canonical_candidate_events_locked(snapshots)
        projected_keys = self._read_candidate_event_keys_locked()
        for key, event in canonical_events.items():
            if key not in projected_keys:
                self._append_jsonl_locked("candidates.jsonl", event)
                projected_keys.add(key)

        state_keys = set(self._read_candidate_state_locked())
        expected_state = state_keys | projected_keys | set(canonical_events)
        if state_keys != expected_state:
            self._write_json_state_locked(
                "candidate_state.json", {"keys": sorted(expected_state)}
            )

    @staticmethod
    def _validate_snapshot(snapshot: dict[str, Any]) -> None:
        validate_gold_snapshot(snapshot)

    def _read_json_state_locked(self, filename: str, *, raise_on_failure: bool = False) -> Any:
        path = self.root / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("gold_shadow_store read %s failed: %s", filename, exc)
            if raise_on_failure:
                raise GoldShadowStorageReadError(f"unable to read {filename}") from exc
            return None

    def _write_json_state_locked(self, filename: str, value: Any) -> None:
        self._ensure_root()
        path = self.root / filename
        temporary = path.with_name(f".{path.name}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        self._fsync_root_locked()

    def _replace_jsonl_locked(self, filename: str, rows: list[dict[str, Any]]) -> None:
        self._ensure_root()
        path = self.root / filename
        temporary = path.with_name(f".{path.name}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        self._fsync_root_locked()

    def _fsync_root_locked(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
