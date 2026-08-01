"""Deterministic, read-only Phase 2A source snapshot handling."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_SERIES_SCHEMA_VERSION = 1
SOURCE_TYPE = "existing_cloud_store_snapshot"
SOURCE_STORE_ALIAS = "tickflow_cloud_primary"
TRADE_DATE = "2026-07-31"
FIXED_SYMBOLS = {
    "000403.SZ": "派林生物",
    "600489.SH": "中金黄金",
    "300059.SZ": "东方财富",
}
EXPECTED_DOCUMENTS = {
    f"{symbol.replace('.', '')}_{basis}_daily.json"
    for symbol in FIXED_SYMBOLS
    for basis in ("raw", "qfq")
}
REQUIRED_COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)


class SnapshotError(ValueError):
    """Raised when a source store or snapshot violates the approved contract."""


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically while rejecting non-finite numbers."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _reject_json_constant(value: str) -> None:
    raise SnapshotError(f"non-finite JSON constant: {value}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_root(data_dir: Path) -> Path:
    if data_dir.is_symlink():
        raise SnapshotError("source data directory must not be a symlink")
    try:
        root = data_dir.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SnapshotError("source data directory does not exist") from exc
    if not root.is_dir():
        raise SnapshotError("source data path is not a directory")
    return root


def _safe_source_file(root: Path, relative: str) -> Path:
    candidate = root / relative
    if candidate.is_symlink():
        raise SnapshotError(f"source file must not be a symlink: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise SnapshotError(f"invalid source file: {relative}") from exc
    if not resolved.is_file():
        raise SnapshotError(f"source path is not a regular file: {relative}")
    return resolved


def _load_json_file(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
    )
    if not isinstance(value, dict):
        raise SnapshotError(f"expected JSON object: {path.name}")
    return value


def _partition_files(root: Path, relative_root: str) -> list[Path]:
    partition_root = root / relative_root
    if partition_root.is_symlink():
        raise SnapshotError(f"partition root must not be a symlink: {relative_root}")
    try:
        resolved_root = partition_root.resolve(strict=True)
        resolved_root.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise SnapshotError(f"invalid partition root: {relative_root}") from exc
    if not resolved_root.is_dir():
        raise SnapshotError(f"partition root is not a directory: {relative_root}")

    files = sorted(resolved_root.glob("date=*/part.parquet"))
    if not files:
        raise SnapshotError(f"no Parquet partitions found: {relative_root}")
    for path in files:
        relative = path.relative_to(root).as_posix()
        _safe_source_file(root, relative)
    return files


def _file_manifest(root: Path, files: Sequence[Path]) -> dict[str, Any]:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in files
    ]
    aggregate_sha256 = _sha256_bytes(canonical_json_bytes(entries))
    return {
        "file_count": len(entries),
        "aggregate_sha256": aggregate_sha256,
        "files": entries,
    }


def _job_files(root: Path) -> list[Path]:
    job_root = root / "job_store"
    if job_root.is_symlink():
        raise SnapshotError("job store must not be a symlink")
    try:
        resolved = job_root.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise SnapshotError("job store is missing or invalid") from exc
    files = sorted(path for path in resolved.glob("*.json") if path.is_file())
    for path in files:
        _safe_source_file(root, path.relative_to(root).as_posix())
    return files


def _successful_job(root: Path) -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for path in _job_files(root):
        try:
            job = _load_json_file(path)
        except (SnapshotError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        status = str(job.get("status", "")).lower()
        log = job.get("log")
        stages = {
            entry.get("stage")
            for entry in log
            if isinstance(log, list) and isinstance(entry, Mapping)
        } if isinstance(log, list) else set()
        if status not in {"success", "succeeded"}:
            continue
        if not {"sync_daily", "compute_enriched", "done"}.issubset(stages):
            continue
        serialized = json.dumps(job, ensure_ascii=False, sort_keys=True)
        if TRADE_DATE not in serialized:
            continue
        finished_at = str(job.get("finished_at") or job.get("completed_at") or "")
        candidates.append((finished_at, path, job))
    if not candidates:
        raise SnapshotError("no successful daily and enriched source job found")
    _, path, job = max(candidates, key=lambda item: (item[0], item[1].name))
    return path, job


def _parse_captured_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError("captured_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotError("captured_at must include a timezone offset")
    return value


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _normalize_trade_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise SnapshotError(f"invalid trade date value: {value!r}") from exc


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SnapshotError(f"non-numeric source value: {field}")
    result = value.item() if hasattr(value, "item") else value
    result = int(result) if isinstance(result, int) else float(result)
    if isinstance(result, float) and not math.isfinite(result):
        raise SnapshotError(f"non-finite source value: {field}")
    return result


def _read_series(files: Sequence[Path]) -> dict[str, list[dict[str, Any]]]:
    frame = pl.read_parquet([str(path) for path in files])
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise SnapshotError(f"source columns missing: {','.join(missing)}")
    rows_by_symbol = {symbol: [] for symbol in FIXED_SYMBOLS}
    filtered = frame.select(REQUIRED_COLUMNS).filter(
        pl.col("symbol").is_in(list(FIXED_SYMBOLS))
    )
    for row in filtered.iter_rows(named=True):
        symbol = str(row["symbol"])
        trade_date = _normalize_trade_date(row["date"])
        if trade_date > TRADE_DATE:
            continue
        rows_by_symbol[symbol].append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": _number(row["open"], "open"),
                "high": _number(row["high"], "high"),
                "low": _number(row["low"], "low"),
                "close": _number(row["close"], "close"),
                "volume": _number(row["volume"], "volume"),
                "amount": _number(row["amount"], "amount"),
            }
        )
    for rows in rows_by_symbol.values():
        rows.sort(key=lambda item: item["trade_date"])
    return rows_by_symbol


def _series_document(
    symbol: str,
    adjustment: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "snapshot_series_schema_version": SNAPSHOT_SERIES_SCHEMA_VERSION,
        "source_type": SOURCE_TYPE,
        "source_store_alias": SOURCE_STORE_ALIAS,
        "phase1_original_payload_recovered": False,
        "phase1_byte_equivalent": False,
        "symbol": symbol,
        "name": FIXED_SYMBOLS[symbol],
        "adjustment": adjustment,
        "as_of_trade_date": TRADE_DATE,
        "rows": rows,
    }


def resolve_provider_selection(preferences: Mapping[str, Any]) -> dict[str, str]:
    """Mirror the application's persisted-provider default semantics."""
    daily_explicit = preferences.get("daily_data_provider")
    adjustment_explicit = preferences.get("adj_factor_provider")
    daily_present = isinstance(daily_explicit, str) and bool(daily_explicit.strip())
    adjustment_present = isinstance(adjustment_explicit, str) and bool(
        adjustment_explicit.strip()
    )
    return {
        "daily_provider": daily_explicit.strip().lower()
        if daily_present
        else "tickflow",
        "daily_provider_selection_origin": "preferences_explicit"
        if daily_present
        else "application_default_absent",
        "adjustment_factor_source": adjustment_explicit.strip().lower()
        if adjustment_present
        else "same_as_daily",
        "adjustment_factor_selection_origin": "preferences_explicit"
        if adjustment_present
        else "application_default_absent",
    }


def _business_hash(manifest: Mapping[str, Any], documents: Mapping[str, Any]) -> str:
    normalized_manifest = dict(manifest)
    normalized_manifest.pop("captured_at", None)
    normalized_manifest.pop("normalized_business_sha256", None)
    return _sha256_bytes(
        canonical_json_bytes(
            {"manifest": normalized_manifest, "documents": dict(documents)}
        )
    )


def hash_source_store(data_dir: Path) -> str:
    """Hash only the source files that the approved snapshot reader may access."""
    root = _safe_root(data_dir)
    preferences = _safe_source_file(root, "user_data/preferences.json")
    capabilities = _safe_source_file(root, "capabilities.json")
    raw_files = _partition_files(root, "kline_daily")
    qfq_files = _partition_files(root, "kline_daily_enriched")
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in sorted(
            [preferences, capabilities, *_job_files(root), *raw_files, *qfq_files],
            key=lambda item: item.relative_to(root).as_posix(),
        )
    ]
    return _sha256_bytes(canonical_json_bytes(entries))


def build_snapshot_bundle(data_dir: Path, captured_at: str) -> dict[str, Any]:
    """Build a deterministic snapshot bundle without mutating the source store."""
    root = _safe_root(data_dir)
    captured_at = _parse_captured_at(captured_at)
    source_hash_before = hash_source_store(root)

    preferences_path = _safe_source_file(root, "user_data/preferences.json")
    capabilities_path = _safe_source_file(root, "capabilities.json")
    preferences = _load_json_file(preferences_path)
    capabilities = _load_json_file(capabilities_path)
    job_path, job = _successful_job(root)
    raw_files = _partition_files(root, "kline_daily")
    qfq_files = _partition_files(root, "kline_daily_enriched")
    raw_rows = _read_series(raw_files)
    qfq_rows = _read_series(qfq_files)

    documents: dict[str, Any] = {}
    symbol_manifest: list[dict[str, Any]] = []
    for symbol, name in FIXED_SYMBOLS.items():
        raw_document = _series_document(symbol, "none", raw_rows[symbol])
        qfq_document = _series_document(symbol, "qfq", qfq_rows[symbol])
        raw_name = f"{symbol.replace('.', '')}_raw_daily.json"
        qfq_name = f"{symbol.replace('.', '')}_qfq_daily.json"
        documents[raw_name] = raw_document
        documents[qfq_name] = qfq_document
        symbol_manifest.append(
            {
                "symbol": symbol,
                "name": name,
                "raw": {
                    "row_count": len(raw_rows[symbol]),
                    "first_trade_date": raw_rows[symbol][0]["trade_date"]
                    if raw_rows[symbol]
                    else None,
                    "last_trade_date": raw_rows[symbol][-1]["trade_date"]
                    if raw_rows[symbol]
                    else None,
                    "normalized_sha256": _sha256_bytes(
                        canonical_json_bytes(raw_rows[symbol])
                    ),
                    "output_sha256": _sha256_bytes(
                        canonical_json_bytes(raw_document)
                    ),
                },
                "qfq": {
                    "row_count": len(qfq_rows[symbol]),
                    "first_trade_date": qfq_rows[symbol][0]["trade_date"]
                    if qfq_rows[symbol]
                    else None,
                    "last_trade_date": qfq_rows[symbol][-1]["trade_date"]
                    if qfq_rows[symbol]
                    else None,
                    "normalized_sha256": _sha256_bytes(
                        canonical_json_bytes(qfq_rows[symbol])
                    ),
                    "output_sha256": _sha256_bytes(
                        canonical_json_bytes(qfq_document)
                    ),
                },
            }
        )

    manifest: dict[str, Any] = {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source_type": SOURCE_TYPE,
        "source_store_alias": SOURCE_STORE_ALIAS,
        "captured_at": captured_at,
        "as_of_trade_date": TRADE_DATE,
        "cloud_access_mode": "read_only",
        "tickflow_api_request_count": 0,
        "cloud_mutation_count": 0,
        "phase1_relation": {
            "phase1_status": "PHASE1_OBSERVATION_PASSED",
            "phase1_original_payload_recovered": False,
            "phase1_byte_equivalent": False,
            "usage": "independent_phase2_indicator_source",
        },
        "provider_evidence": {
            **resolve_provider_selection(preferences),
            "capability_label": capabilities.get("label"),
            "sync_task_status": "success",
            "enriched_task_status": "success",
            "sync_task_id": str(job.get("id", job_path.stem)),
            "sync_started_at": job.get("started_at"),
            "sync_completed_at": job.get("finished_at") or job.get("completed_at"),
            "preferences_sha256": _sha256_file(preferences_path),
            "capabilities_sha256": _sha256_file(capabilities_path),
            "job_sha256": _sha256_file(job_path),
        },
        "source_partitions": {
            "raw": _file_manifest(root, raw_files),
            "qfq": _file_manifest(root, qfq_files),
        },
        "symbols": symbol_manifest,
    }
    manifest["normalized_business_sha256"] = _business_hash(manifest, documents)

    bundle = {"manifest": manifest, "documents": documents}
    errors = validate_snapshot_bundle(bundle)
    if errors:
        raise SnapshotError("snapshot bundle validation failed: " + ", ".join(errors))
    if hash_source_store(root) != source_hash_before:
        raise SnapshotError("source store changed during read-only snapshot capture")
    return bundle


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _walk_values(value: Any, pointer: str = ""):
    yield pointer or "/", value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk_values(
                child, f"{pointer}/{_pointer_escape(str(key))}"
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_values(child, f"{pointer}/{index}")


def _scan_unsafe_fields(bundle: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    secret_keys = {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "session",
        "secret",
        "token",
    }
    trading_keys = {
        "buy",
        "buy_price",
        "sell",
        "sell_price",
        "position",
        "add_position",
        "reduce_position",
        "stop_loss",
        "take_profit",
        "target_price",
        "trading_advice",
        "operation_advice",
        "买入",
        "卖出",
        "加仓",
        "减仓",
        "仓位",
        "止损",
        "止盈",
        "目标价",
        "操作建议",
    }
    forbidden_text = (
        "买入",
        "卖出",
        "加仓",
        "减仓",
        "止损位",
        "止盈位",
        "目标价",
        "操作建议",
    )
    for pointer, value in _walk_values(bundle):
        if pointer == "/":
            continue
        key = pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
        normalized = key.lower().replace("-", "_")
        if normalized in secret_keys:
            errors.append(f"secret_field:{pointer}")
        if normalized in trading_keys:
            errors.append(f"forbidden_trading_field:{pointer}")
        if isinstance(value, str) and any(term in value for term in forbidden_text):
            errors.append(f"forbidden_trading_text:{pointer}")
    return errors


def _manifest_symbol_entry(
    manifest: Mapping[str, Any], symbol: str
) -> Mapping[str, Any] | None:
    entries = manifest.get("symbols")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, Mapping) and entry.get("symbol") == symbol:
            return entry
    return None


def validate_snapshot_bundle(bundle: Mapping[str, Any]) -> list[str]:
    """Validate a snapshot bundle without requiring access to the source store."""
    if not isinstance(bundle, Mapping):
        return ["snapshot_bundle_not_object"]
    errors = _scan_unsafe_fields(bundle)
    manifest = bundle.get("manifest")
    documents = bundle.get("documents")
    if not isinstance(manifest, Mapping):
        return sorted(set([*errors, "manifest_invalid"]))
    if not isinstance(documents, Mapping):
        return sorted(set([*errors, "documents_invalid"]))

    if manifest.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION:
        errors.append("snapshot_schema_version_invalid")
    if manifest.get("source_type") != SOURCE_TYPE:
        errors.append("source_type_invalid")
    if manifest.get("source_store_alias") != SOURCE_STORE_ALIAS:
        errors.append("source_store_alias_invalid")
    if manifest.get("as_of_trade_date") != TRADE_DATE:
        errors.append("as_of_trade_date_invalid")
    if manifest.get("cloud_access_mode") != "read_only":
        errors.append("cloud_access_mode_invalid")
    if manifest.get("cloud_mutation_count") != 0:
        errors.append("cloud_mutation_count_nonzero")
    if manifest.get("tickflow_api_request_count") != 0:
        errors.append("tickflow_api_request_count_nonzero")
    try:
        _parse_captured_at(str(manifest.get("captured_at")))
    except SnapshotError:
        errors.append("captured_at_invalid")

    relation = manifest.get("phase1_relation")
    if not isinstance(relation, Mapping):
        errors.append("phase1_relation_invalid")
    else:
        if relation.get("phase1_status") != "PHASE1_OBSERVATION_PASSED":
            errors.append("phase1_status_invalid")
        if relation.get("phase1_original_payload_recovered") is not False:
            errors.append("phase1_original_payload_recovered_must_be_false")
        if relation.get("phase1_byte_equivalent") is not False:
            errors.append("phase1_byte_equivalent_must_be_false")
        if relation.get("usage") != "independent_phase2_indicator_source":
            errors.append("phase1_usage_invalid")

    provider = manifest.get("provider_evidence")
    if not isinstance(provider, Mapping):
        errors.append("provider_evidence_invalid")
    else:
        if provider.get("daily_provider") != "tickflow":
            errors.append("daily_provider_invalid")
        if provider.get("adjustment_factor_source") != "same_as_daily":
            errors.append("adjustment_factor_source_invalid")
        for field in (
            "daily_provider_selection_origin",
            "adjustment_factor_selection_origin",
        ):
            if provider.get(field) not in {
                "preferences_explicit",
                "application_default_absent",
            }:
                errors.append(f"provider_selection_origin_invalid:{field}")
        if provider.get("capability_label") != "Pro":
            errors.append("capability_label_invalid")
        if provider.get("sync_task_status") != "success":
            errors.append("sync_task_status_invalid")
        if provider.get("enriched_task_status") != "success":
            errors.append("enriched_task_status_invalid")
        for field in ("preferences_sha256", "capabilities_sha256", "job_sha256"):
            value = provider.get(field)
            if not isinstance(value, str) or len(value) != 64:
                errors.append(f"provider_hash_invalid:{field}")
        started_at = _parse_timestamp(provider.get("sync_started_at"))
        completed_at = _parse_timestamp(provider.get("sync_completed_at"))
        if started_at is None:
            errors.append("sync_started_at_invalid")
        if completed_at is None:
            errors.append("sync_completed_at_invalid")
        if started_at is not None and completed_at is not None:
            if completed_at < started_at:
                errors.append("sync_task_time_order_invalid")
            shanghai_date = completed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
            if shanghai_date.isoformat() != TRADE_DATE:
                errors.append("sync_task_trade_date_invalid")

    source_partitions = manifest.get("source_partitions")
    if not isinstance(source_partitions, Mapping):
        errors.append("source_partitions_invalid")
    else:
        for basis, prefix in (
            ("raw", "kline_daily/date="),
            ("qfq", "kline_daily_enriched/date="),
        ):
            partition = source_partitions.get(basis)
            if not isinstance(partition, Mapping):
                errors.append(f"source_partition_invalid:{basis}")
                continue
            files = partition.get("files")
            if not isinstance(files, list) or not all(
                isinstance(item, Mapping) for item in files
            ):
                errors.append(f"source_partition_files_invalid:{basis}")
                continue
            paths = [item.get("path") for item in files]
            if paths != sorted(set(paths)) or not all(
                isinstance(path, str) and path.startswith(prefix) for path in paths
            ):
                errors.append(f"source_partition_paths_invalid:{basis}")
            for index, item in enumerate(files):
                size = item.get("size")
                digest = item.get("sha256")
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    errors.append(f"source_partition_size_invalid:{basis}:{index}")
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    errors.append(f"source_partition_hash_invalid:{basis}:{index}")
            if partition.get("file_count") != len(files):
                errors.append(f"source_partition_file_count_mismatch:{basis}")
            try:
                aggregate = _sha256_bytes(canonical_json_bytes(files))
            except (TypeError, ValueError):
                errors.append(f"source_partition_hash_uncomputable:{basis}")
            else:
                if partition.get("aggregate_sha256") != aggregate:
                    errors.append(
                        f"source_partition_aggregate_hash_mismatch:{basis}"
                    )

    if set(documents) != EXPECTED_DOCUMENTS:
        errors.append("document_set_invalid")

    expected_symbol_entries = list(FIXED_SYMBOLS)
    entries = manifest.get("symbols")
    if not isinstance(entries, list):
        errors.append("symbol_manifest_invalid")
    else:
        symbols = [entry.get("symbol") for entry in entries if isinstance(entry, Mapping)]
        if symbols != expected_symbol_entries:
            errors.append("symbol_manifest_invalid")

    for symbol, expected_name in FIXED_SYMBOLS.items():
        raw_dates: list[str] | None = None
        qfq_dates: list[str] | None = None
        for basis, adjustment in (("raw", "none"), ("qfq", "qfq")):
            filename = f"{symbol.replace('.', '')}_{basis}_daily.json"
            document = documents.get(filename)
            if not isinstance(document, Mapping):
                continue
            if document.get("snapshot_series_schema_version") != SNAPSHOT_SERIES_SCHEMA_VERSION:
                errors.append(f"series_schema_version_invalid:{filename}")
            if document.get("source_type") != SOURCE_TYPE:
                errors.append(f"document_source_type_invalid:{filename}")
            if document.get("source_store_alias") != SOURCE_STORE_ALIAS:
                errors.append(f"document_source_store_alias_invalid:{filename}")
            if document.get("phase1_original_payload_recovered") is not False:
                errors.append(f"document_phase1_original_invalid:{filename}")
            if document.get("phase1_byte_equivalent") is not False:
                errors.append(f"document_phase1_byte_equivalent_invalid:{filename}")
            if document.get("symbol") != symbol:
                errors.append(f"document_symbol_invalid:{filename}")
            if document.get("name") != expected_name:
                errors.append(f"document_name_invalid:{filename}")
            if document.get("adjustment") != adjustment:
                errors.append(f"document_adjustment_invalid:{filename}")
            if document.get("as_of_trade_date") != TRADE_DATE:
                errors.append(f"document_as_of_trade_date_invalid:{filename}")

            rows = document.get("rows")
            if not isinstance(rows, list):
                errors.append(f"rows_invalid:{filename}")
                continue
            if len(rows) != 251:
                errors.append(f"row_count_invalid:{filename}")
            dates: list[str] = []
            for index, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    errors.append(f"row_invalid:{filename}:{index}")
                    continue
                if row.get("symbol") != symbol:
                    errors.append(f"row_symbol_invalid:{filename}:{index}")
                trade_date = row.get("trade_date")
                if not isinstance(trade_date, str):
                    errors.append(f"trade_date_invalid:{filename}:{index}")
                else:
                    try:
                        parsed_date = date.fromisoformat(trade_date)
                    except ValueError:
                        errors.append(f"trade_date_invalid:{filename}:{index}")
                    else:
                        dates.append(trade_date)
                        if parsed_date > date.fromisoformat(TRADE_DATE):
                            errors.append(f"future_trade_date:{filename}")
                numeric: dict[str, int | float] = {}
                for field in ("open", "high", "low", "close", "volume", "amount"):
                    value = row.get(field)
                    pointer = f"/rows/{index}/{field}"
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        errors.append(f"numeric_value_invalid:{filename}:{pointer}")
                    elif isinstance(value, float) and not math.isfinite(value):
                        errors.append(f"non_finite_number:{filename}:{pointer}")
                    else:
                        numeric[field] = value
                if set(numeric) == {"open", "high", "low", "close", "volume", "amount"}:
                    if numeric["high"] < max(
                        numeric["open"], numeric["close"], numeric["low"]
                    ):
                        errors.append(f"high_logic_invalid:{filename}:{index}")
                    if numeric["low"] > min(
                        numeric["open"], numeric["close"], numeric["high"]
                    ):
                        errors.append(f"low_logic_invalid:{filename}:{index}")
                    if numeric["volume"] < 0:
                        errors.append(f"negative_volume:{filename}:{index}")
                    if numeric["amount"] < 0:
                        errors.append(f"negative_amount:{filename}:{index}")
            if dates != sorted(set(dates)):
                errors.append(f"trade_dates_not_unique:{filename}")
            if dates and dates[-1] != TRADE_DATE:
                errors.append(f"latest_trade_date_invalid:{filename}")

            symbol_entry = _manifest_symbol_entry(manifest, symbol)
            series_entry = symbol_entry.get(basis) if isinstance(symbol_entry, Mapping) else None
            if not isinstance(series_entry, Mapping):
                errors.append(f"series_manifest_invalid:{filename}")
            else:
                if series_entry.get("row_count") != len(rows):
                    errors.append(f"series_row_count_mismatch:{filename}")
                if dates:
                    if series_entry.get("first_trade_date") != dates[0]:
                        errors.append(f"series_first_date_mismatch:{filename}")
                    if series_entry.get("last_trade_date") != dates[-1]:
                        errors.append(f"series_last_date_mismatch:{filename}")
                try:
                    rows_hash = _sha256_bytes(canonical_json_bytes(rows))
                    output_hash = _sha256_bytes(canonical_json_bytes(document))
                except (TypeError, ValueError):
                    errors.append(f"canonical_serialization_error:{filename}")
                else:
                    if series_entry.get("normalized_sha256") != rows_hash:
                        errors.append(f"normalized_hash_mismatch:{filename}")
                    if series_entry.get("output_sha256") != output_hash:
                        errors.append(f"output_hash_mismatch:{filename}")
            if basis == "raw":
                raw_dates = dates
            else:
                qfq_dates = dates
        if raw_dates is not None and qfq_dates is not None and raw_dates != qfq_dates:
            errors.append(f"raw_qfq_date_set_mismatch:{symbol}")

    try:
        expected_business_hash = _business_hash(manifest, documents)
    except (TypeError, ValueError):
        errors.append("normalized_business_hash_uncomputable")
    else:
        if manifest.get("normalized_business_sha256") != expected_business_hash:
            errors.append("normalized_business_hash_mismatch")

    return sorted(set(errors))


def _validation_result(bundle: Mapping[str, Any]) -> dict[str, Any]:
    errors = validate_snapshot_bundle(bundle)
    manifest = bundle.get("manifest") if isinstance(bundle, Mapping) else None
    documents = bundle.get("documents") if isinstance(bundle, Mapping) else None
    non_finite = sum(
        1
        for _, value in _walk_values(bundle)
        if isinstance(value, float) and not math.isfinite(value)
    )
    unsafe = _scan_unsafe_fields(bundle) if isinstance(bundle, Mapping) else []
    secret_hits = sum(error.startswith("secret_field:") for error in unsafe)
    trading_hits = sum(
        error.startswith(("forbidden_trading_field:", "forbidden_trading_text:"))
        for error in unsafe
    )
    raw_valid = 0
    qfq_valid = 0
    if isinstance(documents, Mapping):
        for filename in EXPECTED_DOCUMENTS:
            if filename not in documents:
                continue
            prefix = f"{filename}:"
            if not any(prefix in error for error in errors):
                if "_raw_" in filename:
                    raw_valid += 1
                else:
                    qfq_valid += 1
    manifest_hash = None
    business_hash = None
    if isinstance(manifest, Mapping):
        with suppress(TypeError, ValueError):
            manifest_hash = _sha256_bytes(canonical_json_bytes(manifest))
        value = manifest.get("normalized_business_sha256")
        business_hash = value if isinstance(value, str) else None
    return {
        "status": "SNAPSHOT_VALID" if not errors else "SNAPSHOT_INVALID",
        "errors": errors,
        "metrics": {
            "symbol_count": len(FIXED_SYMBOLS),
            "raw_series_valid": raw_valid,
            "qfq_series_valid": qfq_valid,
            "non_finite_numbers": non_finite,
            "secret_hits": secret_hits,
            "trading_field_hits": trading_hits,
            "cloud_mutation_count": manifest.get("cloud_mutation_count")
            if isinstance(manifest, Mapping)
            else None,
            "tickflow_api_request_count": manifest.get("tickflow_api_request_count")
            if isinstance(manifest, Mapping)
            else None,
        },
        "manifest_sha256": manifest_hash,
        "normalized_business_sha256": business_hash,
    }


def _write_fsynced(path: Path, value: Any) -> None:
    with path.open("wb") as handle:
        handle.write(canonical_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def materialize_snapshot_bundle(
    bundle: Mapping[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Atomically publish a validated bundle into a local evidence directory."""
    errors = validate_snapshot_bundle(bundle)
    if errors:
        raise SnapshotError("refusing to materialize invalid snapshot: " + ", ".join(errors))
    if output_dir.is_symlink():
        raise SnapshotError("snapshot output directory must not be a symlink")
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise SnapshotError("snapshot output parent must not be a symlink")
    parent = parent.resolve(strict=True)
    output_dir = parent / output_dir.name

    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=parent))
    backup = parent / f".{output_dir.name}.backup-{os.getpid()}"
    validation = _validation_result(bundle)
    try:
        _write_fsynced(staging / "manifest.json", bundle["manifest"])
        for filename, document in sorted(bundle["documents"].items()):
            _write_fsynced(staging / filename, document)
        _write_fsynced(staging / "snapshot_validation.json", validation)
        _fsync_directory(staging)

        if backup.exists():
            shutil.rmtree(backup)
        if output_dir.exists():
            if not output_dir.is_dir():
                raise SnapshotError("snapshot output exists and is not a directory")
            output_dir.rename(backup)
        try:
            staging.rename(output_dir)
            _fsync_directory(parent)
        except Exception:
            if backup.exists() and not output_dir.exists():
                backup.rename(output_dir)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return validation
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _repeat_record_errors(
    snapshot_dir: Path,
    manifest: Mapping[str, Any],
    repeat_export: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    if repeat_export.get("status") != "IDEMPOTENT":
        errors.append("repeat_export_status_invalid")
    for field in (
        "normalized_business_hash_match",
        "document_hashes_match",
        "source_partition_hashes_match",
        "capture_timestamps_differ",
    ):
        if repeat_export.get(field) is not True:
            errors.append(f"repeat_export_flag_invalid:{field}")
    if repeat_export.get("normalized_business_sha256") != manifest.get(
        "normalized_business_sha256"
    ):
        errors.append("repeat_export_business_hash_invalid")
    expected_documents = {
        filename: _sha256_file(snapshot_dir / filename)
        for filename in sorted(EXPECTED_DOCUMENTS)
    }
    if repeat_export.get("document_sha256") != expected_documents:
        errors.append("repeat_export_document_hashes_invalid")
    partitions = manifest.get("source_partitions")
    expected_partitions = {
        basis: partitions[basis]["aggregate_sha256"]
        for basis in ("raw", "qfq")
    } if isinstance(partitions, Mapping) else {}
    if repeat_export.get("source_partition_sha256") != expected_partitions:
        errors.append("repeat_export_source_hashes_invalid")
    return errors


def record_repeat_export_validation(
    primary_dir: Path, repeat_dir: Path
) -> dict[str, Any]:
    """Compare two snapshots and atomically record an idempotency result."""
    primary_validation = validate_snapshot_directory(primary_dir)
    repeat_validation = validate_snapshot_directory(repeat_dir)
    if primary_validation.get("status") != "SNAPSHOT_VALID":
        raise SnapshotError("primary snapshot is invalid")
    if repeat_validation.get("status") != "SNAPSHOT_VALID":
        raise SnapshotError("repeat snapshot is invalid")

    primary_manifest = _load_json_file(primary_dir / "manifest.json")
    repeat_manifest = _load_json_file(repeat_dir / "manifest.json")
    primary_documents = {
        filename: _sha256_file(primary_dir / filename)
        for filename in sorted(EXPECTED_DOCUMENTS)
    }
    repeat_documents = {
        filename: _sha256_file(repeat_dir / filename)
        for filename in sorted(EXPECTED_DOCUMENTS)
    }
    primary_partitions = {
        basis: primary_manifest["source_partitions"][basis]["aggregate_sha256"]
        for basis in ("raw", "qfq")
    }
    repeat_partitions = {
        basis: repeat_manifest["source_partitions"][basis]["aggregate_sha256"]
        for basis in ("raw", "qfq")
    }
    business_match = primary_manifest.get("normalized_business_sha256") == (
        repeat_manifest.get("normalized_business_sha256")
    )
    document_match = primary_documents == repeat_documents
    partition_match = primary_partitions == repeat_partitions
    timestamps_differ = primary_manifest.get("captured_at") != repeat_manifest.get(
        "captured_at"
    )
    comparison = {
        "status": "IDEMPOTENT"
        if all((business_match, document_match, partition_match, timestamps_differ))
        else "NOT_IDEMPOTENT",
        "normalized_business_hash_match": business_match,
        "document_hashes_match": document_match,
        "source_partition_hashes_match": partition_match,
        "capture_timestamps_differ": timestamps_differ,
        "normalized_business_sha256": primary_manifest.get(
            "normalized_business_sha256"
        ),
        "document_sha256": primary_documents,
        "source_partition_sha256": primary_partitions,
    }
    if comparison["status"] != "IDEMPOTENT":
        raise SnapshotError("repeat snapshot business content is not idempotent")

    validation_path = primary_dir / "snapshot_validation.json"
    recorded = _load_json_file(validation_path)
    recorded["repeat_export"] = comparison
    temporary = primary_dir / ".snapshot_validation.json.tmp"
    try:
        _write_fsynced(temporary, recorded)
        os.replace(temporary, validation_path)
        _fsync_directory(primary_dir)
    finally:
        temporary.unlink(missing_ok=True)
    return comparison


def validate_snapshot_directory(snapshot_dir: Path) -> dict[str, Any]:
    """Validate a materialized snapshot and its recorded validation evidence."""
    if snapshot_dir.is_symlink() or not snapshot_dir.is_dir():
        return {
            "status": "SNAPSHOT_INVALID",
            "errors": ["snapshot_directory_invalid"],
            "metrics": {},
            "manifest_sha256": None,
            "normalized_business_sha256": None,
        }
    expected_files = {"manifest.json", "snapshot_validation.json", *EXPECTED_DOCUMENTS}
    actual_files = {path.name for path in snapshot_dir.iterdir()}
    if actual_files != expected_files:
        return {
            "status": "SNAPSHOT_INVALID",
            "errors": ["snapshot_file_set_invalid"],
            "metrics": {},
            "manifest_sha256": None,
            "normalized_business_sha256": None,
        }
    try:
        loaded: dict[str, Any] = {}
        for filename in sorted(expected_files):
            path = snapshot_dir / filename
            if path.is_symlink() or not path.is_file():
                raise SnapshotError(f"snapshot file invalid: {filename}")
            loaded[filename] = _load_json_file(path)
        bundle = {
            "manifest": loaded["manifest.json"],
            "documents": {
                filename: loaded[filename] for filename in sorted(EXPECTED_DOCUMENTS)
            },
        }
        result = _validation_result(bundle)
        recorded = dict(loaded["snapshot_validation.json"])
        repeat_export = recorded.pop("repeat_export", None)
        if recorded != result:
            result = dict(result)
            result["status"] = "SNAPSHOT_INVALID"
            result["errors"] = sorted(
                set([*result["errors"], "recorded_validation_mismatch"])
            )
        if repeat_export is not None:
            if not isinstance(repeat_export, Mapping):
                repeat_errors = ["repeat_export_record_invalid"]
            else:
                repeat_errors = _repeat_record_errors(
                    snapshot_dir, bundle["manifest"], repeat_export
                )
            result = dict(result)
            result["repeat_export"] = repeat_export
            if repeat_errors:
                result["status"] = "SNAPSHOT_INVALID"
                result["errors"] = sorted(set([*result["errors"], *repeat_errors]))
        return result
    except (SnapshotError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return {
            "status": "SNAPSHOT_INVALID",
            "errors": [f"snapshot_read_error:{type(exc).__name__}"],
            "metrics": {},
            "manifest_sha256": None,
            "normalized_business_sha256": None,
        }


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export or materialize a deterministic Phase 2A snapshot"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stdout-bundle", action="store_true")
    mode.add_argument("--stdin-bundle", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--captured-at")
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the network-free snapshot transport CLI."""
    parser = _cli_parser()
    args = parser.parse_args(argv)
    try:
        if args.stdout_bundle:
            if args.data_dir is None or args.captured_at is None or args.output_dir:
                parser.error(
                    "stdout mode requires --data-dir and --captured-at only"
                )
            bundle = build_snapshot_bundle(args.data_dir, args.captured_at)
            sys.stdout.buffer.write(canonical_json_bytes(bundle))
            sys.stdout.buffer.flush()
            manifest = bundle["manifest"]
            print(
                json.dumps(
                    {
                        "status": "SNAPSHOT_EXPORTED",
                        "normalized_business_sha256": manifest[
                            "normalized_business_sha256"
                        ],
                        "cloud_mutation_count": 0,
                        "tickflow_api_request_count": 0,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 0

        if args.output_dir is None or args.data_dir or args.captured_at:
            parser.error("stdin mode requires --output-dir only")
        bundle = json.loads(sys.stdin.read(), parse_constant=_reject_json_constant)
        if not isinstance(bundle, Mapping):
            raise SnapshotError("stdin bundle must be a JSON object")
        validation = materialize_snapshot_bundle(bundle, args.output_dir)
        manifest = bundle["manifest"]
        print(
            json.dumps(
                {
                    "status": validation["status"],
                    "manifest_sha256": validation["manifest_sha256"],
                    "normalized_business_sha256": validation[
                        "normalized_business_sha256"
                    ],
                    "cloud_mutation_count": manifest["cloud_mutation_count"],
                    "tickflow_api_request_count": manifest[
                        "tickflow_api_request_count"
                    ],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    except (SnapshotError, json.JSONDecodeError, OSError) as exc:
        print(
            json.dumps(
                {"status": "SNAPSHOT_ERROR", "error_type": type(exc).__name__},
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
