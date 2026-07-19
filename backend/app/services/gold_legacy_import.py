"""Safe manual import of normalized legacy Gold monitor samples."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from app.services.gold_legacy_schema import LEGACY_SIGNAL_VOCABULARY
from app.services.gold_shadow_compare import LegacySample, parse_legacy_text
from app.services.gold_shadow_store import GoldShadowStore, validate_gold_import_row

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_GOLD_STATES = frozenset({"恐慌", "死机", "贪婪"})
_ALLOWED_EXTENSIONS = frozenset({".log", ".txt", ".json", ".jsonl"})


class GoldImportError(ValueError):
    """Raised with a safe machine-readable code when a manual import is rejected."""


@dataclass(frozen=True)
class GoldImportResult:
    import_id: str
    sha256: str
    filename: str
    imported_at: str
    sample_count: int


def normalize_legacy_sample(sample: LegacySample) -> dict[str, object]:
    """Drop parser provenance and return the fixed persisted legacy schema."""
    numeric_values = (sample.price, sample.p, sample.v, sample.a)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in numeric_values
    ):
        raise ValueError("legacy sample metrics must be finite")
    if sample.observed_at.utcoffset() is None:
        raise ValueError("legacy sample timestamp must include a timezone")
    if sample.state not in _GOLD_STATES:
        raise ValueError("legacy sample state is invalid")
    if not all(signal in LEGACY_SIGNAL_VOCABULARY for signal in sample.signals):
        raise ValueError("legacy sample signal is invalid")
    return {
        "schema_version": 1,
        "observed_at": sample.observed_at.isoformat(),
        "price": sample.price,
        "P": sample.p,
        "V": sample.v,
        "A": sample.a,
        "state": sample.state,
        "signals": list(sample.signals),
    }


def _safe_basename(filename: str) -> str:
    basename = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    return "upload" if basename in {"", ".", ".."} else basename


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _load_json(text: str) -> object:
    try:
        return json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (json.JSONDecodeError, ValueError):
        raise GoldImportError("invalid_json") from None


def _parse_normalized_json(extension: str, text: str) -> list[dict[str, object]]:
    if extension == ".json":
        value = _load_json(text)
        if not isinstance(value, list):
            raise GoldImportError("invalid_json_structure")
        if not all(isinstance(row, dict) for row in value):
            raise GoldImportError("invalid_json_structure")
        return [dict(row) for row in value]

    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = _load_json(line)
        if not isinstance(row, dict):
            raise GoldImportError("invalid_json_structure")
        rows.append(dict(row))
    return rows


class GoldLegacyImporter:
    """Import user-selected bytes without retaining their original content."""

    def __init__(self, store: GoldShadowStore) -> None:
        self.store = store

    @staticmethod
    def _result(metadata: dict[str, object]) -> GoldImportResult:
        return GoldImportResult(
            import_id=str(metadata["import_id"]),
            sha256=str(metadata["sha256"]),
            filename=str(metadata["filename"]),
            imported_at=str(metadata["imported_at"]),
            sample_count=int(metadata["sample_count"]),
        )

    def import_bytes(self, filename: str, payload: bytes) -> GoldImportResult:
        safe_filename = _safe_basename(filename)
        extension = "." + safe_filename.rsplit(".", 1)[-1].lower()
        if "." not in safe_filename or extension not in _ALLOWED_EXTENSIONS:
            raise GoldImportError("invalid_extension")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise GoldImportError("upload_too_large")
        if payload.startswith(_ZIP_MAGICS):
            raise GoldImportError("archive_not_allowed")

        digest = hashlib.sha256(payload).hexdigest()
        try:
            existing, repair_required = self.store.resolve_import_upload(digest)
        except ValueError as exc:
            if str(exc) != "legacy_import_original_mismatch":
                raise
            raise GoldImportError("legacy_import_original_mismatch") from None
        if existing is not None:
            return self._result(existing)

        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise GoldImportError("invalid_utf8") from None
        if extension in {".log", ".txt"}:
            samples = parse_legacy_text(text)
            if not samples:
                raise GoldImportError("no_valid_samples")
            try:
                normalized = [normalize_legacy_sample(sample) for sample in samples]
            except (TypeError, ValueError):
                raise GoldImportError("invalid_sample") from None
        else:
            normalized = _parse_normalized_json(extension, text)
            if not normalized:
                raise GoldImportError("no_valid_samples")
            try:
                for row in normalized:
                    validate_gold_import_row(row)
            except (TypeError, ValueError):
                raise GoldImportError("invalid_sample") from None

        if repair_required:
            try:
                metadata = self.store.repair_legacy_import(
                    digest, safe_filename, normalized
                )
            except ValueError as exc:
                if str(exc) != "legacy_import_original_mismatch":
                    raise
                raise GoldImportError("legacy_import_original_mismatch") from None
        else:
            metadata = self.store.commit_import(digest, safe_filename, normalized)
        return self._result(metadata)
