"""Compare legacy gold-monitor logs with TickFlow shadow snapshots."""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.services.gold_legacy_schema import LEGACY_SIGNAL_VOCABULARY
from app.services.gold_shadow_store import validate_gold_snapshot

logger = logging.getLogger(__name__)

PRICE_TOLERANCE = 0.01
P_TOLERANCE = 0.10
V_TOLERANCE = 0.02
A_TOLERANCE = 0.02
MATCH_WINDOW_SECONDS = 150
PVA_SIGNALS = frozenset(LEGACY_SIGNAL_VOCABULARY[:-1])

_CN_TZ = timezone(timedelta(hours=8))
_UTC = UTC
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=_UTC)
_PVA_SIGNAL_ORDER = LEGACY_SIGNAL_VOCABULARY[:-1]
_GOLD_STATES = frozenset({"恐慌", "死机", "贪婪"})
_POSITIVE_SNAPSHOT_FIELDS = (
    "price",
    "previous_close",
    "legacy_reference_60",
    "native_ema60",
)
_TIMESTAMP_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"
    r"(?:[,.]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+|nan|inf(?:inity)?)"
_FIELD_PATTERNS = {
    "price": re.compile(rf"(?:price|价格|现价)\s*[:=\uff1a]\s*(?P<value>{_NUMBER})", re.I),
    "p": re.compile(rf"(?<![A-Za-z])P\s*[:=\uff1a]\s*(?P<value>{_NUMBER})", re.I),
    "v": re.compile(rf"(?<![A-Za-z])V\s*[:=\uff1a]\s*(?P<value>{_NUMBER})", re.I),
    "a": re.compile(rf"(?<![A-Za-z])A\s*[:=\uff1a]\s*(?P<value>{_NUMBER})", re.I),
}
_STATE_RE = re.compile(
    r"(?:SJM|state|状态)\s*[:=\uff1a]\s*(?P<value>[^\s|,\uff0c;\uff1b]+)", re.I
)


@dataclass(frozen=True)
class LegacySample:
    observed_at: datetime
    price: float | None
    p: float | None
    v: float | None
    a: float | None
    state: str | None
    signals: tuple[str, ...]
    source_line: int


@dataclass(frozen=True)
class ShadowSample:
    observed_at: datetime
    price: float | None
    p: float | None
    v: float | None
    a: float | None
    state: str | None
    signals: tuple[str, ...] | None
    source_line: int


@dataclass(frozen=True)
class ComparisonReport:
    rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]

    @property
    def pairs(self) -> tuple[dict[str, Any], ...]:
        return tuple(row for row in self.rows if row["type"] == "pair")

    @property
    def matched(self) -> int:
        return int(self.summary["totals"]["matched"])

    @property
    def missing_shadow(self) -> int:
        return int(self.summary["totals"]["missing_shadow"])


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace(",", ".").replace(" ", "T", 1)
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if re.search(r"[+-]\d{4}$", normalized):
        normalized = f"{normalized[:-2]}:{normalized[-2:]}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CN_TZ)
    return parsed.astimezone(_CN_TZ)


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_current_shadow_snapshot(raw: dict[str, Any]) -> datetime:
    validate_gold_snapshot(raw)
    if raw["symbol"] != "600489.SH":
        raise ValueError("unexpected symbol")
    if raw["quote_source"] != "tickflow":
        raise ValueError("unexpected quote source")
    if raw["state"] not in _GOLD_STATES:
        raise ValueError("unexpected state")

    try:
        market_date = date.fromisoformat(raw["market_date"])
    except ValueError as exc:
        raise ValueError("invalid market date") from exc
    if market_date.isoformat() != raw["market_date"]:
        raise ValueError("non-canonical market date")

    observed_value = raw["observed_at"]
    normalized = observed_value.replace("Z", "+00:00")
    try:
        observed_at = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("invalid observed timestamp") from exc
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed timestamp must include timezone")
    observed_at = observed_at.astimezone(_CN_TZ)

    if raw["quote_ts"] <= 0:
        raise ValueError("quote timestamp must be positive")
    try:
        quote_at_utc = _UNIX_EPOCH + timedelta(milliseconds=raw["quote_ts"])
    except OverflowError as exc:
        raise ValueError("invalid quote timestamp") from exc
    quote_at = quote_at_utc.astimezone(_CN_TZ)
    if observed_at.date() != market_date or quote_at.date() != market_date:
        raise ValueError("snapshot dates disagree")
    if observed_at.astimezone(_UTC) != quote_at_utc:
        raise ValueError("snapshot timestamps disagree")

    if any(raw[field] <= 0 for field in _POSITIVE_SNAPSHOT_FIELDS):
        raise ValueError("positive snapshot value required")
    candidate_signals = raw["candidate_signals"]
    new_candidate_signals = raw["new_candidate_signals"]
    if len(set(candidate_signals)) != len(candidate_signals):
        raise ValueError("candidate signals must be unique")
    if not set(candidate_signals).issubset(PVA_SIGNALS):
        raise ValueError("unexpected candidate signal")
    if len(set(new_candidate_signals)) != len(new_candidate_signals):
        raise ValueError("new candidate signals must be unique")
    if not set(new_candidate_signals).issubset(candidate_signals):
        raise ValueError("new candidate signals must be current candidates")
    return observed_at


def _warn_malformed(kind: str, path: Path, line_number: int) -> None:
    logger.warning("gold shadow compare malformed %s %s line %d", kind, path.name, line_number)


def parse_legacy_text(
    text: str, *, _source: Path | None = None
) -> list[LegacySample]:
    """Parse legacy samples and immediately adjacent alert lines from text."""
    source = _source or Path("<legacy-text>")
    rows: list[LegacySample] = []
    pending_index: int | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        if "[ALERT]" in line:
            if pending_index is not None:
                signals = list(rows[pending_index].signals)
                for signal in LEGACY_SIGNAL_VOCABULARY:
                    if signal in line and signal not in signals:
                        signals.append(signal)
                rows[pending_index] = replace(rows[pending_index], signals=tuple(signals))
            continue

        field_matches = {field: pattern.search(line) for field, pattern in _FIELD_PATTERNS.items()}
        state_match = _STATE_RE.search(line)
        if not any(field_matches.values()) and state_match is None:
            pending_index = None
            continue
        complete_sample = all(field_matches.values()) and state_match is not None
        if not complete_sample:
            _warn_malformed("legacy", source, line_number)
            pending_index = None
            continue
        timestamp_match = _TIMESTAMP_RE.search(line)
        observed_at = _parse_timestamp(timestamp_match.group("timestamp")) if timestamp_match else None
        if observed_at is None:
            _warn_malformed("legacy", source, line_number)
            pending_index = None
            continue
        values: dict[str, float | None] = {}
        for field, match in field_matches.items():
            values[field] = _numeric(match.group("value")) if match else None
        rows.append(
            LegacySample(
                observed_at=observed_at,
                price=values["price"],
                p=values["p"],
                v=values["v"],
                a=values["a"],
                state=state_match.group("value") if state_match else None,
                signals=(),
                source_line=line_number,
            )
        )
        pending_index = len(rows) - 1
    return rows


def parse_legacy_log(path: Path | str) -> list[LegacySample]:
    """Parse production legacy samples and immediately adjacent alert lines."""
    source = Path(path)
    return parse_legacy_text(source.read_text(encoding="utf-8"), _source=source)


def parse_shadow_rows(
    rows: list[dict[str, Any]],
    *,
    _source: Path | None = None,
    _source_lines: list[int] | None = None,
) -> list[ShadowSample]:
    """Parse valid shadow objects while warning and skipping malformed rows."""
    source = _source or Path("<shadow-rows>")
    source_lines = _source_lines or list(range(1, len(rows) + 1))
    if len(source_lines) != len(rows):
        raise ValueError("source lines must correspond to shadow rows")
    parsed_rows: list[ShadowSample] = []
    for raw, line_number in zip(rows, source_lines, strict=True):
        try:
            observed_at = _validate_current_shadow_snapshot(raw)
        except (KeyError, TypeError, ValueError):
            _warn_malformed("shadow", source, line_number)
            continue
        parsed_rows.append(
            ShadowSample(
                observed_at=observed_at,
                price=_numeric(raw.get("price")),
                p=_numeric(raw.get("P")),
                v=_numeric(raw.get("V")),
                a=_numeric(raw.get("A")),
                state=raw["state"],
                signals=tuple(raw["candidate_signals"]),
                source_line=line_number,
            )
        )
    return parsed_rows


def parse_shadow_jsonl(path: Path | str) -> list[ShadowSample]:
    """Parse valid shadow objects while warning and skipping malformed lines."""
    source = Path(path)
    raw_rows: list[dict[str, Any]] = []
    source_lines: list[int] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            _warn_malformed("shadow", source, line_number)
            continue
        if not isinstance(raw, dict):
            _warn_malformed("shadow", source, line_number)
            continue
        raw_rows.append(raw)
        source_lines.append(line_number)
    return parse_shadow_rows(raw_rows, _source=source, _source_lines=source_lines)


def _delta(left: object, right: object) -> float | None:
    if isinstance(left, bool) or isinstance(right, bool):
        return None
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    if not math.isfinite(left) or not math.isfinite(right):
        return None
    try:
        return float(abs(Decimal(str(left)) - Decimal(str(right))))
    except InvalidOperation:
        return None


def _json_number(value: float | None) -> float | None:
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _ordered_pva(signals: tuple[str, ...] | None) -> list[str] | None:
    if signals is None:
        return None
    selected = set(signals) & PVA_SIGNALS
    return [signal for signal in _PVA_SIGNAL_ORDER if signal in selected]


def compare_pair(legacy: LegacySample, shadow: ShadowSample) -> dict[str, Any]:
    """Return JSON-safe deltas and fail-closed checks for one matched pair."""
    tolerances = {
        "price": PRICE_TOLERANCE,
        "p": P_TOLERANCE,
        "v": V_TOLERANCE,
        "a": A_TOLERANCE,
    }
    deltas = {
        field: _delta(getattr(legacy, field), getattr(shadow, field)) for field in tolerances
    }
    legacy_signals = _ordered_pva(legacy.signals)
    shadow_signals = _ordered_pva(shadow.signals)
    checks = {
        field: deltas[field] is not None
        and Decimal(str(deltas[field])) <= Decimal(str(tolerance))
        for field, tolerance in tolerances.items()
    }
    checks["state"] = (
        isinstance(legacy.state, str)
        and isinstance(shadow.state, str)
        and legacy.state == shadow.state
    )
    checks["signals"] = (
        legacy_signals is not None
        and shadow_signals is not None
        and set(legacy_signals) == set(shadow_signals)
    )
    return {
        "schema_version": 1,
        "type": "pair",
        "legacy_observed_at": legacy.observed_at.isoformat(),
        "shadow_observed_at": shadow.observed_at.isoformat(),
        "time_delta_seconds": abs((shadow.observed_at - legacy.observed_at).total_seconds()),
        "legacy_source_line": legacy.source_line,
        "shadow_source_line": shadow.source_line,
        "legacy": {
            "price": _json_number(legacy.price),
            "p": _json_number(legacy.p),
            "v": _json_number(legacy.v),
            "a": _json_number(legacy.a),
            "state": legacy.state,
            "signals": legacy_signals,
        },
        "shadow": {
            "price": _json_number(shadow.price),
            "p": _json_number(shadow.p),
            "v": _json_number(shadow.v),
            "a": _json_number(shadow.a),
            "state": shadow.state,
            "signals": shadow_signals,
        },
        "deltas": deltas,
        "checks": checks,
    }


def _distance_microseconds(left: datetime, right: datetime) -> int:
    delta = abs(right - left)
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000) + delta.microseconds


def _score_is_better(candidate: tuple[int, int], current: tuple[int, int]) -> bool:
    return candidate[0] > current[0] or (
        candidate[0] == current[0] and candidate[1] < current[1]
    )


def _optimal_ordered_pairs(
    legacy_rows: list[LegacySample],
    shadow_rows: list[ShadowSample],
    match_window_seconds: int,
) -> list[tuple[int, int]]:
    """Return ordered matches maximizing count, then minimizing total distance."""
    legacy_count = len(legacy_rows)
    shadow_count = len(shadow_rows)
    scores = [[(0, 0)] * (shadow_count + 1) for _ in range(legacy_count + 1)]
    actions = [bytearray(shadow_count + 1) for _ in range(legacy_count + 1)]
    skip_legacy = 1
    skip_shadow = 2
    match = 3
    for legacy_index in range(1, legacy_count + 1):
        actions[legacy_index][0] = skip_legacy
    for shadow_index in range(1, shadow_count + 1):
        actions[0][shadow_index] = skip_shadow

    window_microseconds = match_window_seconds * 1_000_000
    for legacy_index in range(1, legacy_count + 1):
        for shadow_index in range(1, shadow_count + 1):
            best = scores[legacy_index - 1][shadow_index]
            action = skip_legacy

            candidate = scores[legacy_index][shadow_index - 1]
            if _score_is_better(candidate, best):
                best = candidate
                action = skip_shadow

            distance = _distance_microseconds(
                legacy_rows[legacy_index - 1].observed_at,
                shadow_rows[shadow_index - 1].observed_at,
            )
            if distance <= window_microseconds:
                previous = scores[legacy_index - 1][shadow_index - 1]
                candidate = (previous[0] + 1, previous[1] + distance)
                if _score_is_better(candidate, best):
                    best = candidate
                    action = match

            scores[legacy_index][shadow_index] = best
            actions[legacy_index][shadow_index] = action

    pairs: list[tuple[int, int]] = []
    legacy_index = legacy_count
    shadow_index = shadow_count
    while legacy_index or shadow_index:
        action = actions[legacy_index][shadow_index]
        if action == match:
            pairs.append((legacy_index - 1, shadow_index - 1))
            legacy_index -= 1
            shadow_index -= 1
        elif action == skip_legacy:
            legacy_index -= 1
        else:
            shadow_index -= 1
    pairs.reverse()
    return pairs


def compare_samples(
    legacy_rows: list[LegacySample],
    shadow_rows: list[ShadowSample],
    *,
    match_window_seconds: int = MATCH_WINDOW_SECONDS,
    observation_days_claimed: int = 0,
) -> ComparisonReport:
    """Optimally match ordered rows and summarize comparison-only evidence."""
    if match_window_seconds < 0:
        raise ValueError("match_window_seconds must be non-negative")
    if observation_days_claimed < 0:
        raise ValueError("observation_days_claimed must be non-negative")

    ordered_legacy = sorted(
        enumerate(legacy_rows), key=lambda item: (item[1].observed_at, item[1].source_line, item[0])
    )
    ordered_shadow = sorted(
        enumerate(shadow_rows), key=lambda item: (item[1].observed_at, item[1].source_line, item[0])
    )
    matched_positions = _optimal_ordered_pairs(
        [sample for _, sample in ordered_legacy],
        [sample for _, sample in ordered_shadow],
        match_window_seconds,
    )
    shadow_by_legacy = dict(matched_positions)
    used_shadow = {shadow_index for _, shadow_index in matched_positions}
    output: list[dict[str, Any]] = []
    for legacy_position, (_, legacy) in enumerate(ordered_legacy):
        shadow_position = shadow_by_legacy.get(legacy_position)
        if shadow_position is None:
            output.append(
                {
                    "schema_version": 1,
                    "type": "missing_shadow",
                    "legacy_observed_at": legacy.observed_at.isoformat(),
                    "legacy_source_line": legacy.source_line,
                    "reason": "no_unused_shadow_within_window",
                    "match_window_seconds": match_window_seconds,
                }
            )
            continue
        shadow = ordered_shadow[shadow_position][1]
        output.append(compare_pair(legacy, shadow))

    for shadow_position, (_, shadow) in enumerate(ordered_shadow):
        if shadow_position not in used_shadow:
            output.append(
                {
                    "schema_version": 1,
                    "type": "unmatched_shadow",
                    "shadow_observed_at": shadow.observed_at.isoformat(),
                    "shadow_source_line": shadow.source_line,
                    "reason": "no_legacy_match",
                }
            )

    pairs = [row for row in output if row["type"] == "pair"]
    matched = len(pairs)
    missing = len(legacy_rows) - matched
    pass_rates = {
        check: (sum(pair["checks"][check] for pair in pairs) / matched if matched else 0.0)
        for check in ("price", "p", "v", "a", "state", "signals")
    }
    availability_rate = matched / len(legacy_rows) if legacy_rows else 0.0
    threshold_checks = {
        "availability": availability_rate >= 0.99,
        **{check: matched > 0 and rate == 1.0 for check, rate in pass_rates.items()},
    }
    thresholds_pass = all(threshold_checks.values())
    valid_comparison_dates = sorted(
        {
            pair["legacy_observed_at"][:10]
            for pair in pairs
            if all(pair["checks"].values())
        }
    )
    summary = {
        "schema_version": 1,
        "type": "summary",
        "totals": {
            "legacy": len(legacy_rows),
            "shadow": len(shadow_rows),
            "matched": matched,
            "missing_shadow": missing,
            "unmatched_shadow": len(shadow_rows) - matched,
        },
        "availability_rate": availability_rate,
        "pass_rates": pass_rates,
        "stage_a": {
            "threshold_checks": threshold_checks,
            "thresholds_pass": thresholds_pass,
            "required_complete_trading_days": 10,
            "observation_days_claimed": observation_days_claimed,
            "valid_comparison_dates": valid_comparison_dates,
            "observation_window_verified": False,
            "observation_window_reason": (
                "full_day_completeness_not_verifiable_from_comparison_inputs"
            ),
            "formal_acceptance": False,
            "status": "observation_window_unverified",
        },
    }
    return ComparisonReport(rows=tuple(output), summary=summary)
