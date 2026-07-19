"""Safe manual import of normalized legacy Gold monitor samples."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from app.services.gold_legacy_schema import LEGACY_SIGNAL_VOCABULARY
from app.services.gold_shadow_compare import LegacySample, parse_legacy_text
from app.services.gold_shadow_store import GoldShadowStore

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_GOLD_STATES = frozenset({"恐慌", "死机", "贪婪"})


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


class GoldLegacyImporter:
    """Import user-selected bytes without retaining their original content."""

    def __init__(self, store: GoldShadowStore) -> None:
        self.store = store

    def import_bytes(self, filename: str, payload: bytes) -> GoldImportResult:
        if len(payload) > MAX_UPLOAD_BYTES:
            raise GoldImportError("upload_too_large")
        if payload.startswith(_ZIP_MAGICS):
            raise GoldImportError("archive_not_allowed")

        digest = hashlib.sha256(payload).hexdigest()
        existing = self.store.find_import_by_sha256(digest)
        if existing is not None:
            return GoldImportResult(**existing)

        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise GoldImportError("invalid_utf8") from None
        samples = parse_legacy_text(text)
        if not samples:
            raise GoldImportError("no_valid_samples")
        try:
            normalized = [normalize_legacy_sample(sample) for sample in samples]
        except (TypeError, ValueError):
            raise GoldImportError("invalid_sample") from None

        metadata = self.store.commit_import(digest, _safe_basename(filename), normalized)
        return GoldImportResult(**metadata)
