"""Persist deterministic, date-scoped Gold comparison evidence."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.gold_calendar import is_beijing_session
from app.services.gold_shadow_compare import (
    LegacySample,
    ShadowSample,
    compare_samples,
    parse_shadow_rows,
)
from app.services.gold_shadow_store import GoldShadowStore

_CN_TZ = timezone(timedelta(hours=8))
_COMPARATOR_VERSION = "2"


def _run_id(
    *,
    market_date: date,
    legacy_digest: str,
    shadow_digest: str,
    comparator_version: str = "2",
) -> str:
    payload = f"{market_date.isoformat()}|{legacy_digest}|{shadow_digest}|{comparator_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _beijing_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(_CN_TZ)


def _canonicalize_legacy(rows: list[dict[str, Any]], market_date: date) -> list[LegacySample]:
    samples = [
        LegacySample(
            observed_at=_beijing_datetime(row["observed_at"]),
            price=row["price"],
            p=row["P"],
            v=row["V"],
            a=row["A"],
            state=row["state"],
            signals=tuple(row["signals"]),
            source_line=0,
        )
        for row in rows
    ]
    return _canonicalize_samples(
        [sample for sample in samples if sample.observed_at.date() == market_date]
    )


def _canonicalize_shadow(rows: list[dict[str, Any]], market_date: date) -> list[ShadowSample]:
    samples = parse_shadow_rows(rows)
    return _canonicalize_samples(
        [sample for sample in samples if sample.observed_at.date() == market_date]
    )


def _canonicalize_samples(samples: list[LegacySample] | list[ShadowSample]):
    ordered = sorted(
        samples,
        key=lambda sample: json.dumps(
            _sample_payload(sample), ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ),
    )
    return [replace(sample, source_line=index) for index, sample in enumerate(ordered, start=1)]


def _sample_payload(sample: LegacySample | ShadowSample) -> dict[str, Any]:
    return {
        "observed_at": sample.observed_at.isoformat(),
        "price": sample.price,
        "P": sample.p,
        "V": sample.v,
        "A": sample.a,
        "state": sample.state,
        "signals": list(sample.signals) if sample.signals is not None else None,
    }


def _sample_digest(samples: list[LegacySample] | list[ShadowSample]) -> str:
    payload = [_sample_payload(sample) for sample in samples]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class GoldComparisonRunner:
    """Build one canonical persisted comparison for an import and Beijing date."""

    def __init__(self, store: GoldShadowStore) -> None:
        self.store = store

    def run(self, import_id: str, market_date: date) -> dict[str, Any]:
        if not isinstance(market_date, date) or isinstance(market_date, datetime):
            raise ValueError("market_date must be a date")
        imported = self.store.get_import(import_id)
        if imported is None:
            raise ValueError("unknown import")

        legacy_evidence = _canonicalize_legacy(imported["rows"], market_date)
        shadow_evidence = _canonicalize_shadow(
            self.store.comparison_snapshots(), market_date
        )
        legacy = _canonicalize_samples(
            [sample for sample in legacy_evidence if is_beijing_session(sample.observed_at)]
        )
        shadow = _canonicalize_samples(
            [sample for sample in shadow_evidence if is_beijing_session(sample.observed_at)]
        )
        legacy_excluded = len(legacy_evidence) - len(legacy)
        shadow_excluded = len(shadow_evidence) - len(shadow)
        legacy_digest = _sample_digest(legacy_evidence)
        shadow_digest = _sample_digest(shadow_evidence)
        run_id = _run_id(
            market_date=market_date,
            legacy_digest=legacy_digest,
            shadow_digest=shadow_digest,
        )
        report = compare_samples(legacy, shadow)
        quality = {
            "legacy_excluded_out_of_session_count": legacy_excluded,
            "shadow_excluded_out_of_session_count": shadow_excluded,
        }
        summary = {**report.summary, "quality": quality}
        metadata = {
            "schema_version": 2,
            "run_id": run_id,
            "market_date": market_date.isoformat(),
            "legacy_import_id": import_id,
            "legacy_digest": legacy_digest,
            "shadow_digest": shadow_digest,
            "comparator_version": _COMPARATOR_VERSION,
            "legacy_sample_count": len(legacy),
            "shadow_sample_count": len(shadow),
            **quality,
        }
        return self.store.commit_comparison_run(metadata, [*report.rows, summary])

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.store.get_comparison_run(run_id)

    def list_runs(self, limit: int) -> list[dict[str, Any]]:
        return self.store.list_comparison_runs(limit)
