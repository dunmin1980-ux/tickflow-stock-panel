"""Bounded, deterministic daily Chan-style research on caller-supplied QFQ bars."""

from __future__ import annotations

import math
from datetime import date
from itertools import pairwise


def _validated_prefix(rows: list[dict], as_of: date) -> list[dict]:
    if type(as_of) is not date or not isinstance(rows, list):
        raise ValueError("CHAN_INPUT_INVALID: expected list of rows and date as_of")
    prefix = []
    previous_date = None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("trade_date"), str):
            raise ValueError("CHAN_DATE_INVALID: expected ISO trade_date")
        try:
            day = date.fromisoformat(row["trade_date"])
        except ValueError as exc:
            raise ValueError("CHAN_DATE_INVALID: expected YYYY-MM-DD") from exc
        if day.isoformat() != row["trade_date"]:
            raise ValueError("CHAN_DATE_INVALID: expected YYYY-MM-DD")
        if previous_date is not None and day <= previous_date:
            raise ValueError("CHAN_DATE_ORDER_INVALID: dates must strictly increase")
        previous_date = day
        for field in ("open", "high", "low", "close"):
            value = row.get(field)
            try:
                valid = type(value) in (int, float) and math.isfinite(value) and value > 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"CHAN_OHLC_INVALID: {field} must be finite and positive")
        if not (
            row["low"] <= row["open"] <= row["high"]
            and row["low"] <= row["close"] <= row["high"]
        ):
            raise ValueError("CHAN_OHLC_INVALID: open and close must be within low/high")
        if day <= as_of:
            prefix.append(row)
    return prefix


def _confirmed_fractals(rows: list[dict]) -> list[dict]:
    fractals = []
    for index in range(1, len(rows) - 1):
        left, center, right = rows[index - 1 : index + 2]
        if all(center[key] > left[key] and center[key] > right[key] for key in ("high", "low")):
            kind, price = "TOP", center["high"]
        elif all(center[key] < left[key] and center[key] < right[key] for key in ("high", "low")):
            kind, price = "BOTTOM", center["low"]
        else:
            continue
        fractals.append({
            "type": kind,
            "index": index,
            "date": center["trade_date"],
            "confirmed_at": right["trade_date"],
            "price": price,
        })
    return fractals


def _history(fractals: list[dict], kind: str) -> list[dict]:
    return [
        {"type": f"{kind}_FRACTAL", **{key: item[key] for key in ("date", "confirmed_at", "price")}}
        for item in fractals if item["type"] == kind
    ]


def _stroke_candidates(fractals: list[dict]) -> list[dict]:
    pivots = []
    for item in fractals:
        if not pivots:
            pivots.append(item)
            continue
        previous = pivots[-1]
        more_extreme = (
            item["price"] > previous["price"] if item["type"] == "TOP"
            else item["price"] < previous["price"]
        )
        if item["type"] == previous["type"]:
            if more_extreme:
                pivots[-1] = item
        elif item["index"] - previous["index"] >= 2 and more_extreme:
            pivots.append(item)
    return [
        {
            "type": "STROKE_CANDIDATE",
            "direction": "UP_STROKE" if start["type"] == "BOTTOM" else "DOWN_STROKE",
            "start_date": start["date"],
            "end_date": end["date"],
            "start_price": start["price"],
            "end_price": end["price"],
            "confirmed_at": end["confirmed_at"],
            "source_bar_separation": end["index"] - start["index"],
        }
        for start, end in pairwise(pivots)
    ]


def _structure(tops: list[dict], bottoms: list[dict]) -> str:
    # Preserve actual levels; a crossed or zero-width pair is not a valid range.
    if tops and bottoms and tops[-1]["price"] <= bottoms[-1]["price"]:
        return "TRANSITION"
    if len(tops) < 2 or len(bottoms) < 2:
        return "INSUFFICIENT_DATA"
    high, previous_high = tops[-1]["price"], tops[-2]["price"]
    low, previous_low = bottoms[-1]["price"], bottoms[-2]["price"]
    if high > previous_high and low > previous_low:
        return "UP_STRUCTURE"
    if high < previous_high and low < previous_low:
        return "DOWN_STRUCTURE"
    if high <= previous_high and low >= previous_low:
        return "RANGE_STRUCTURE"
    return "TRANSITION"


def _change(current: str, previous: str, has_new_fractal: bool) -> str:
    directional = {"UP_STRUCTURE", "DOWN_STRUCTURE"}
    if current != previous:
        if current in directional and previous in directional:
            return "REVERSING"
        if current == "UP_STRUCTURE" or previous == "DOWN_STRUCTURE":
            return "STRENGTHENING"
        if current == "DOWN_STRUCTURE" or previous == "UP_STRUCTURE":
            return "WEAKENING"
    return "NEW_FRACTAL" if has_new_fractal else "UNCHANGED"


def analyze_chan(rows: list[dict], as_of: date) -> dict:
    """Return engineering structure, not full Chan theory or a trading signal."""
    prefix = _validated_prefix(rows, as_of)
    fractals = _confirmed_fractals(prefix)
    previous_fractals = _confirmed_fractals(prefix[:-1])
    tops, bottoms = _history(fractals, "TOP"), _history(fractals, "BOTTOM")
    current = _structure(tops, bottoms)
    previous = _structure(
        _history(previous_fractals, "TOP"), _history(previous_fractals, "BOTTOM")
    )
    high = tops[-1]["price"] if tops else None
    low = bottoms[-1]["price"] if bottoms else None
    latest_close = prefix[-1]["close"] if prefix else None
    price_location = "NOT_AVAILABLE"
    if latest_close is not None and high is not None and low is not None and high > low:
        if latest_close > high:
            price_location = "ABOVE_CONFIRMED_RANGE"
        elif latest_close < low:
            price_location = "BELOW_CONFIRMED_RANGE"
        else:
            price_location = "INSIDE_CONFIRMED_RANGE"
    return {
        "mode": "CHAN_DAILY_STRUCTURE_V1",
        "price_basis": "QFQ",
        "timeframe": "1d",
        "trade_date": prefix[-1]["trade_date"] if prefix else None,
        "current_structure": current,
        "previous_structure": previous,
        "change": _change(current, previous, len(fractals) > len(previous_fractals)),
        "top_fractals": tops,
        "bottom_fractals": bottoms,
        "stroke_candidates": _stroke_candidates(fractals),
        "structure_high": high,
        "structure_low": low,
        "latest_close": latest_close,
        "price_location": price_location,
        "central_zone": "DEFERRED",
        "deterministic_contract_version": 1,
    }
