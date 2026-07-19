"""Persistent storage for the isolated gold-monitor shadow pipeline."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import string
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.services.gold_legacy_schema import LEGACY_SIGNAL_VOCABULARY

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
_IMPORT_ROW_FIELDS = frozenset(
    {"schema_version", "observed_at", "price", "P", "V", "A", "state", "signals"}
)
_IMPORT_NUMERIC_FIELDS = ("price", "P", "V", "A")
_IMPORT_METADATA_FIELDS = frozenset(
    {"import_id", "sha256", "normalized_sha256", "filename", "imported_at", "sample_count"}
)
_LEGACY_IMPORT_METADATA_FIELDS = _IMPORT_METADATA_FIELDS - {"normalized_sha256"}
_LEGACY_IMPORT_REIMPORT_REQUIRED = "legacy_import_requires_manual_reimport_from_original_bytes"
_LEGACY_IMPORT_ORIGINAL_MISMATCH = "legacy_import_original_mismatch"
_COMPARISON_RUN_METADATA_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "market_date",
        "legacy_import_id",
        "legacy_digest",
        "shadow_digest",
        "comparator_version",
        "legacy_sample_count",
        "shadow_sample_count",
        "supersedes_run_id",
        "row_count",
        "rows_sha256",
        "summary_sha256",
    }
)
_OBSERVATION_DAY_REVIEW_FIELDS = frozenset(
    {
        "schema_version",
        "review_type",
        "market_date",
        "run_id",
        "verified",
        "note",
        "reviewed_at",
    }
)
_OBSERVATION_RESTART_REVIEW_FIELDS = frozenset(
    {"schema_version", "review_type", "verified", "note", "reviewed_at"}
)
_GOLD_STATES = frozenset({"恐慌", "死机", "贪婪"})


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

    def commit_import(
        self, digest: str, filename: str, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Durably commit normalized samples before publishing their index metadata."""
        self._validate_import_digest(digest)
        if not rows:
            raise ValueError("import rows must not be empty")
        normalized_rows = [dict(row) for row in rows]
        for row in normalized_rows:
            self._validate_import_row(row)
        safe_filename = self._safe_basename(filename)
        normalized_digest = self._canonical_jsonl_sha256(normalized_rows)

        with _LOCK:
            index = self._read_import_index_locked()
            existing = next((row for row in index if row["sha256"] == digest), None)
            if existing is not None:
                self._read_import_rows_locked(existing)
                return dict(existing)

            self._write_import_rows_locked(digest, normalized_rows)
            metadata = {
                "import_id": digest,
                "sha256": digest,
                "normalized_sha256": normalized_digest,
                "filename": safe_filename,
                "imported_at": datetime.now(UTC).isoformat(),
                "sample_count": len(normalized_rows),
            }
            self._replace_jsonl_locked("import_index.jsonl", [*index, metadata])
            return dict(metadata)

    def repair_legacy_import(
        self, digest: str, filename: str, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Replace one legacy import only when re-uploaded raw bytes match its digest."""
        self._validate_import_digest(digest)
        if not rows:
            raise ValueError("import rows must not be empty")
        normalized_rows = [dict(row) for row in rows]
        for row in normalized_rows:
            self._validate_import_row(row)
        normalized_digest = self._canonical_jsonl_sha256(normalized_rows)

        with _LOCK:
            index = self._read_import_index_for_repair_locked()
            matching_positions = [
                position for position, row in enumerate(index) if row["sha256"] == digest
            ]
            if len(matching_positions) != 1:
                raise ValueError(_LEGACY_IMPORT_ORIGINAL_MISMATCH)
            position = matching_positions[0]
            existing = index[position]
            if "normalized_sha256" in existing:
                self._read_import_rows_locked(existing)
                return dict(existing)

            self._write_import_rows_locked(digest, normalized_rows)
            repaired = {
                **existing,
                "normalized_sha256": normalized_digest,
                "filename": self._safe_basename(filename),
                "sample_count": len(normalized_rows),
            }
            self._validate_import_metadata(repaired)
            repaired_index = [*index]
            repaired_index[position] = repaired
            self._replace_jsonl_locked("import_index.jsonl", repaired_index)
            return dict(repaired)

    def resolve_import_upload(
        self, digest: str
    ) -> tuple[dict[str, Any] | None, bool]:
        """Resolve a duplicate or legacy repair target without trusting import data."""
        self._validate_import_digest(digest)
        with _LOCK:
            index = self._read_import_index_for_repair_locked()
            matches = [row for row in index if row["sha256"] == digest]
            if len(matches) > 1:
                raise GoldShadowStorageReadError("unable to read import_index.jsonl")
            if matches:
                existing = matches[0]
                if "normalized_sha256" in existing:
                    self._read_import_rows_locked(existing)
                    return dict(existing), False
                return None, True
            if any("normalized_sha256" not in row for row in index):
                raise ValueError(_LEGACY_IMPORT_ORIGINAL_MISMATCH)
            return None, False

    def find_import_by_sha256(self, digest: str) -> dict[str, Any] | None:
        if not self._is_import_digest(digest):
            return None
        with _LOCK:
            row = next(
                (row for row in self._read_import_index_locked() if row["sha256"] == digest),
                None,
            )
            if row is not None:
                self._read_import_rows_locked(row)
        return dict(row) if row is not None else None

    def get_import(self, import_id: str) -> dict[str, Any] | None:
        if not self._is_import_digest(import_id):
            return None
        with _LOCK:
            metadata = next(
                (
                    row
                    for row in self._read_import_index_locked()
                    if row["import_id"] == import_id
                ),
                None,
            )
            if metadata is None:
                return None
            rows = self._read_import_rows_locked(metadata)
        return {**metadata, "rows": [dict(row) for row in rows]}

    def list_imports(self, limit: int = MAX_RECORDS) -> list[dict[str, Any]]:
        with _LOCK:
            rows = self._read_import_index_locked()
        rows.reverse()
        return [dict(row) for row in rows[: max(0, limit)]]

    def comparison_snapshots(self) -> list[dict[str, Any]]:
        """Return validated snapshots for a deterministic comparison run."""
        with _LOCK:
            rows = self._read_jsonl_locked(
                "snapshots.jsonl", raise_on_failure=True, reject_malformed=True
            )
            try:
                for row in rows:
                    self._validate_snapshot(row)
            except ValueError as exc:
                raise GoldShadowStorageReadError("unable to read snapshots.jsonl") from exc
        return [dict(row) for row in rows]

    def commit_comparison_run(
        self, metadata: dict[str, Any], rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Commit a run data file before atomically publishing its metadata."""
        prepared_rows = [dict(row) for row in rows]
        self._validate_comparison_run_rows(prepared_rows)
        with _LOCK:
            index = self._read_comparison_index_locked()
            existing = next((row for row in index if row["run_id"] == metadata.get("run_id")), None)
            if existing is not None:
                self._read_comparison_run_rows_locked(existing)
                return dict(existing)

            same_day_runs = [row for row in index if row["market_date"] == metadata.get("market_date")]
            committed = {
                **metadata,
                "supersedes_run_id": same_day_runs[-1]["run_id"] if same_day_runs else None,
                "row_count": len(prepared_rows),
                "rows_sha256": self._canonical_jsonl_sha256(prepared_rows),
                "summary_sha256": self._canonical_json_sha256(prepared_rows[-1]),
            }
            self._validate_comparison_run_metadata(committed)
            self._write_comparison_run_rows_locked(committed["run_id"], prepared_rows)
            self._replace_jsonl_locked("comparison_index.jsonl", [*index, committed])
            return dict(committed)

    def get_comparison_run(self, run_id: str) -> dict[str, Any] | None:
        if not self._is_import_digest(run_id):
            return None
        with _LOCK:
            metadata = next(
                (row for row in self._read_comparison_index_locked() if row["run_id"] == run_id),
                None,
            )
            if metadata is None:
                return None
            rows = self._read_comparison_run_rows_locked(metadata)
        return {**metadata, "rows": [dict(row) for row in rows]}

    def list_comparison_runs(self, limit: int = MAX_RECORDS) -> list[dict[str, Any]]:
        with _LOCK:
            rows = self._read_comparison_index_locked()
            for row in rows:
                self._read_comparison_run_rows_locked(row)
        rows.sort(key=lambda row: (row["market_date"], row["run_id"]), reverse=True)
        return [dict(row) for row in rows[: max(0, limit)]]

    def append_observation_review(self, review: dict[str, Any]) -> None:
        """Append a validated manual observation review to durable private storage."""
        self._validate_observation_review(review)
        with _LOCK:
            try:
                root_descriptor = self._open_root_descriptor(create=True)
            except OSError as exc:
                raise GoldShadowStorageReadError(
                    "unable to write observation_reviews.jsonl"
                ) from exc
            try:
                self._read_observation_reviews_at_locked(root_descriptor)
                self._append_observation_review_at_locked(root_descriptor, review)
            finally:
                os.close(root_descriptor)

    def list_observation_reviews(self) -> list[dict[str, Any]]:
        with _LOCK:
            try:
                root_descriptor = self._open_root_descriptor(create=False)
            except FileNotFoundError:
                return []
            except OSError as exc:
                raise GoldShadowStorageReadError(
                    "unable to read observation_reviews.jsonl"
                ) from exc
            try:
                rows = self._read_observation_reviews_at_locked(root_descriptor)
            finally:
                os.close(root_descriptor)
        return [dict(row) for row in rows]

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

    def write_daily_history(self, payload: dict[str, Any]) -> None:
        if payload.get("source") != "tickflow":
            raise ValueError("daily history source must be tickflow")
        with _LOCK:
            self._write_json_state_locked("daily_history.json", payload)

    def read_daily_history(self) -> dict[str, Any] | None:
        with _LOCK:
            value = self._read_json_state_locked("daily_history.json", raise_on_failure=True)
        return value if isinstance(value, dict) else None

    def read_holidays(self) -> list[str]:
        """Read and validate the required deployment-provided holiday calendar."""
        root_descriptor: int | None = None
        try:
            root_descriptor = self._open_root_descriptor(create=False)
            flags = os.O_RDONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open("holidays.json", flags, dir_fd=root_descriptor)
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                value = json.load(handle)
            if not isinstance(value, list) or not all(isinstance(day, str) for day in value):
                raise ValueError("holiday calendar must be a list of dates")
            for day in value:
                if date.fromisoformat(day).isoformat() != day:
                    raise ValueError("holiday calendar contains an invalid date")
            return list(value)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise GoldShadowStorageReadError("unable to read holidays.json") from exc
        finally:
            if root_descriptor is not None:
                os.close(root_descriptor)

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _open_root_descriptor(self, *, create: bool) -> int:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.root, flags)
        except FileNotFoundError:
            if not create:
                raise
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(self.root, flags)
        os.fchmod(descriptor, 0o700)
        return descriptor

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
        self,
        filename: str,
        *,
        raise_on_failure: bool = False,
        reject_malformed: bool = False,
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
                        if reject_malformed:
                            raise GoldShadowStorageReadError(f"unable to read {filename}") from None
                        continue
                    if not isinstance(row, dict):
                        logger.warning("gold_shadow_store malformed %s line %d", filename, line_number)
                        if reject_malformed:
                            raise GoldShadowStorageReadError(f"unable to read {filename}")
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

    def _read_import_index_locked(self) -> list[dict[str, Any]]:
        rows = self._read_jsonl_locked(
            "import_index.jsonl", raise_on_failure=True, reject_malformed=True
        )
        try:
            for row in rows:
                if set(row) == _LEGACY_IMPORT_METADATA_FIELDS:
                    raise GoldShadowStorageReadError(_LEGACY_IMPORT_REIMPORT_REQUIRED)
                self._validate_import_metadata(row)
        except ValueError as exc:
            raise GoldShadowStorageReadError("unable to read import_index.jsonl") from exc
        return rows

    def _read_import_index_for_repair_locked(self) -> list[dict[str, Any]]:
        rows = self._read_jsonl_locked(
            "import_index.jsonl", raise_on_failure=True, reject_malformed=True
        )
        try:
            for row in rows:
                if set(row) == _LEGACY_IMPORT_METADATA_FIELDS:
                    self._validate_legacy_import_metadata(row)
                else:
                    self._validate_import_metadata(row)
        except ValueError as exc:
            raise GoldShadowStorageReadError("unable to read import_index.jsonl") from exc
        return rows

    def _read_import_rows_locked(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        import_id = metadata["import_id"]
        filename = f"imports/{import_id}.jsonl"
        if not (self.root / filename).is_file():
            raise GoldShadowStorageReadError(f"unable to read import {import_id}")
        try:
            rows = self._read_jsonl_locked(filename, raise_on_failure=True, reject_malformed=True)
            for row in rows:
                self._validate_import_row(row)
            if len(rows) != metadata["sample_count"]:
                raise ValueError("import sample count mismatch")
            normalized_digest = metadata.get("normalized_sha256")
            if normalized_digest is not None and self._canonical_jsonl_sha256(rows) != normalized_digest:
                raise ValueError("import normalized digest mismatch")
        except (GoldShadowStorageReadError, ValueError) as exc:
            raise GoldShadowStorageReadError(f"unable to read import {import_id}") from exc
        return rows

    def _read_comparison_index_locked(self) -> list[dict[str, Any]]:
        rows = self._read_jsonl_locked(
            "comparison_index.jsonl", raise_on_failure=True, reject_malformed=True
        )
        try:
            for row in rows:
                self._validate_comparison_run_metadata(row)
        except ValueError as exc:
            raise GoldShadowStorageReadError("unable to read comparison_index.jsonl") from exc
        return rows

    def _read_observation_reviews_at_locked(
        self, root_descriptor: int
    ) -> list[dict[str, Any]]:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            try:
                descriptor = os.open(
                    "observation_reviews.jsonl", flags, dir_fd=root_descriptor
                )
            except FileNotFoundError:
                return []
            rows: list[dict[str, Any]] = []
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"invalid observation review at line {line_number}"
                        ) from exc
                    if not isinstance(row, dict):
                        raise ValueError(f"invalid observation review at line {line_number}")
                    rows.append(row)
            for row in rows:
                self._validate_observation_review(row)
        except (OSError, UnicodeError, ValueError) as exc:
            raise GoldShadowStorageReadError(
                "unable to read observation_reviews.jsonl"
            ) from exc
        return rows

    def _append_observation_review_at_locked(
        self, root_descriptor: int, review: dict[str, Any]
    ) -> None:
        flags = os.O_APPEND | os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(
                "observation_reviews.jsonl", flags, 0o600, dir_fd=root_descriptor
            )
            try:
                os.fchmod(descriptor, 0o600)
                if os.lseek(descriptor, 0, os.SEEK_END) > 0:
                    os.lseek(descriptor, -1, os.SEEK_END)
                    if os.read(descriptor, 1) != b"\n":
                        os.write(descriptor, b"\n")
                encoded = (
                    json.dumps(review, ensure_ascii=False, separators=(",", ":")) + "\n"
                ).encode("utf-8")
                view = memoryview(encoded)
                while view:
                    view = view[os.write(descriptor, view) :]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(root_descriptor)
        except OSError as exc:
            raise GoldShadowStorageReadError(
                "unable to write observation_reviews.jsonl"
            ) from exc

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

    @staticmethod
    def _is_import_digest(digest: object) -> bool:
        return (
            isinstance(digest, str)
            and len(digest) == 64
            and all(character in string.hexdigits for character in digest)
            and digest == digest.lower()
        )

    @classmethod
    def _validate_import_digest(cls, digest: object) -> None:
        if not cls._is_import_digest(digest):
            raise ValueError("invalid import digest")

    @staticmethod
    def _safe_basename(filename: str) -> str:
        basename = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
        return "upload" if basename in {"", ".", ".."} else basename

    @classmethod
    def _validate_import_row(cls, row: dict[str, Any]) -> None:
        if not isinstance(row, dict) or set(row) != _IMPORT_ROW_FIELDS:
            raise ValueError("normalized import fields are required")
        if type(row["schema_version"]) is not int or row["schema_version"] != 1:
            raise ValueError("import schema_version must be 1")
        observed_at = row["observed_at"]
        if not isinstance(observed_at, str):
            raise ValueError("import observed_at must be a string")
        try:
            parsed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("import observed_at is invalid") from exc
        if parsed_at.utcoffset() is None:
            raise ValueError("import observed_at must include a timezone")
        for field in _IMPORT_NUMERIC_FIELDS:
            value = row[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"import {field} must be finite")
        if row["state"] not in _GOLD_STATES:
            raise ValueError("import state is invalid")
        signals = row["signals"]
        if not isinstance(signals, list) or not all(isinstance(item, str) for item in signals):
            raise ValueError("import signals must be a list of strings")
        if not all(signal in LEGACY_SIGNAL_VOCABULARY for signal in signals):
            raise ValueError("import signal is invalid")

    @classmethod
    def _validate_import_metadata(cls, row: dict[str, Any]) -> None:
        if not isinstance(row, dict) or set(row) != _IMPORT_METADATA_FIELDS:
            raise ValueError("invalid import metadata")
        cls._validate_import_digest(row["import_id"])
        if row["sha256"] != row["import_id"]:
            raise ValueError("import digest mismatch")
        cls._validate_import_digest(row["normalized_sha256"])
        filename = row["filename"]
        if not isinstance(filename, str) or cls._safe_basename(filename) != filename:
            raise ValueError("invalid import filename")
        imported_at = row["imported_at"]
        if not isinstance(imported_at, str):
            raise ValueError("invalid import timestamp")
        try:
            parsed_at = datetime.fromisoformat(imported_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid import timestamp") from exc
        if parsed_at.utcoffset() is None:
            raise ValueError("invalid import timestamp")
        if type(row["sample_count"]) is not int or row["sample_count"] <= 0:
            raise ValueError("invalid import sample count")

    @classmethod
    def _validate_legacy_import_metadata(cls, row: dict[str, Any]) -> None:
        if not isinstance(row, dict) or set(row) != _LEGACY_IMPORT_METADATA_FIELDS:
            raise ValueError("invalid legacy import metadata")
        cls._validate_import_digest(row["import_id"])
        if row["sha256"] != row["import_id"]:
            raise ValueError("import digest mismatch")
        if not isinstance(row["filename"], str):
            raise ValueError("invalid import filename")
        imported_at = row["imported_at"]
        if not isinstance(imported_at, str):
            raise ValueError("invalid import timestamp")
        try:
            parsed_at = datetime.fromisoformat(imported_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid import timestamp") from exc
        if parsed_at.utcoffset() is None:
            raise ValueError("invalid import timestamp")
        if type(row["sample_count"]) is not int or row["sample_count"] <= 0:
            raise ValueError("invalid import sample count")

    @classmethod
    def _validate_comparison_run_metadata(cls, row: dict[str, Any]) -> None:
        if not isinstance(row, dict) or set(row) != _COMPARISON_RUN_METADATA_FIELDS:
            raise ValueError("invalid comparison run metadata")
        if type(row["schema_version"]) is not int or row["schema_version"] != 1:
            raise ValueError("invalid comparison run schema version")
        for field in ("run_id", "legacy_import_id", "legacy_digest", "shadow_digest"):
            cls._validate_import_digest(row[field])
        if row["comparator_version"] != "1":
            raise ValueError("invalid comparison comparator version")
        market_date = row["market_date"]
        if not isinstance(market_date, str):
            raise ValueError("invalid comparison market date")
        try:
            if date.fromisoformat(market_date).isoformat() != market_date:
                raise ValueError("invalid comparison market date")
        except ValueError as exc:
            raise ValueError("invalid comparison market date") from exc
        for field in ("legacy_sample_count", "shadow_sample_count"):
            if type(row[field]) is not int or row[field] < 0:
                raise ValueError("invalid comparison sample count")
        supersedes_run_id = row["supersedes_run_id"]
        if supersedes_run_id is not None:
            cls._validate_import_digest(supersedes_run_id)

        if type(row["row_count"]) is not int or row["row_count"] <= 0:
            raise ValueError("invalid comparison row count")
        cls._validate_import_digest(row["rows_sha256"])
        cls._validate_import_digest(row["summary_sha256"])

    @classmethod
    def _validate_observation_review(cls, row: dict[str, Any]) -> None:
        if not isinstance(row, dict) or row.get("review_type") not in {"day", "restart"}:
            raise ValueError("invalid observation review")
        expected_fields = (
            _OBSERVATION_DAY_REVIEW_FIELDS
            if row["review_type"] == "day"
            else _OBSERVATION_RESTART_REVIEW_FIELDS
        )
        if set(row) != expected_fields:
            raise ValueError("invalid observation review fields")
        if type(row["schema_version"]) is not int or row["schema_version"] != 1:
            raise ValueError("invalid observation review schema version")
        if type(row["verified"]) is not bool:
            raise ValueError("invalid observation review verification")
        if not isinstance(row["note"], str) or not row["note"].strip():
            raise ValueError("invalid observation review note")
        reviewed_at = row["reviewed_at"]
        if not isinstance(reviewed_at, str):
            raise ValueError("invalid observation review timestamp")
        try:
            parsed_at = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid observation review timestamp") from exc
        if parsed_at.utcoffset() is None:
            raise ValueError("invalid observation review timestamp")
        if row["review_type"] == "day":
            try:
                if date.fromisoformat(row["market_date"]).isoformat() != row["market_date"]:
                    raise ValueError("invalid observation review market date")
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid observation review market date") from exc
            cls._validate_import_digest(row["run_id"])

    @classmethod
    def _validate_comparison_run_rows(
        cls, rows: list[dict[str, Any]], metadata: dict[str, Any] | None = None
    ) -> None:
        if not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError("comparison run rows are invalid")
        summaries = [row for row in rows if row.get("type") == "summary"]
        if len(summaries) != 1 or rows[-1] is not summaries[0]:
            raise ValueError("comparison run requires one terminal summary")
        if metadata is not None and (
            metadata["row_count"] != len(rows)
            or metadata["rows_sha256"] != cls._canonical_jsonl_sha256(rows)
            or metadata["summary_sha256"] != cls._canonical_json_sha256(rows[-1])
        ):
            raise ValueError("comparison run integrity mismatch")

    def _read_comparison_run_rows_locked(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        run_id = metadata["run_id"]
        filename = f"comparison_runs/{run_id}.jsonl"
        if not (self.root / filename).is_file():
            raise GoldShadowStorageReadError(f"unable to read comparison run {run_id}")
        try:
            rows = self._read_jsonl_locked(filename, raise_on_failure=True, reject_malformed=True)
            self._validate_comparison_run_rows(rows, metadata)
        except (GoldShadowStorageReadError, ValueError) as exc:
            raise GoldShadowStorageReadError(f"unable to read comparison run {run_id}") from exc
        return rows

    @staticmethod
    def _canonical_json_bytes(row: dict[str, Any]) -> bytes:
        try:
            return json.dumps(
                row, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("canonical JSON is invalid") from exc

    @classmethod
    def _canonical_json_sha256(cls, row: dict[str, Any]) -> str:
        return hashlib.sha256(cls._canonical_json_bytes(row)).hexdigest()

    @classmethod
    def _canonical_jsonl_sha256(cls, rows: list[dict[str, Any]]) -> str:
        digest = hashlib.sha256()
        for row in rows:
            digest.update(cls._canonical_json_bytes(row))
            digest.update(b"\n")
        return digest.hexdigest()

    def _write_comparison_run_rows_locked(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        self._ensure_root()
        runs = self.root / "comparison_runs"
        runs.mkdir(mode=0o700, exist_ok=True)
        os.chmod(runs, 0o700)
        self._fsync_root_locked()
        path = runs / f"{run_id}.jsonl"
        temporary = runs / f".{run_id}.jsonl.tmp"
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        os.chmod(temporary, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(
                        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            self._fsync_directory_locked(runs)
        finally:
            temporary.unlink(missing_ok=True)

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
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        os.chmod(temporary, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(
                        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            self._fsync_root_locked()
        finally:
            temporary.unlink(missing_ok=True)

    def _write_import_rows_locked(self, digest: str, rows: list[dict[str, Any]]) -> None:
        self._ensure_root()
        imports = self.root / "imports"
        imports.mkdir(mode=0o700, exist_ok=True)
        os.chmod(imports, 0o700)
        self._fsync_root_locked()
        path = imports / f"{digest}.jsonl"
        temporary = imports / f".{digest}.jsonl.tmp"
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        os.chmod(temporary, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(
                        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            self._fsync_directory_locked(imports)
        finally:
            temporary.unlink(missing_ok=True)

    def _fsync_root_locked(self) -> None:
        self._fsync_directory_locked(self.root)

    @staticmethod
    def _fsync_directory_locked(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
