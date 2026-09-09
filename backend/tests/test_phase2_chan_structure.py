from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.phase2_chan_structure import analyze_chan

START = date(2026, 1, 1)


def _ranges(values: list[tuple[float, float]]) -> list[dict]:
    return [
        {
            "trade_date": (START + timedelta(days=i)).isoformat(),
            "open": low,
            "high": high,
            "low": low,
            "close": high,
        }
        for i, (high, low) in enumerate(values)
    ]


def _bars(prices: list[float]) -> list[dict]:
    return _ranges([(price + 1, price - 1) for price in prices])


def _analyze(rows: list[dict]) -> dict:
    return analyze_chan(rows, date.fromisoformat(rows[-1]["trade_date"]))


def test_empty_prefix_has_json_safe_insufficient_contract() -> None:
    assert analyze_chan([], date(2026, 1, 10)) == {
        "mode": "CHAN_DAILY_STRUCTURE_V1",
        "price_basis": "QFQ",
        "timeframe": "1d",
        "trade_date": None,
        "current_structure": "INSUFFICIENT_DATA",
        "previous_structure": "INSUFFICIENT_DATA",
        "change": "UNCHANGED",
        "top_fractals": [],
        "bottom_fractals": [],
        "stroke_candidates": [],
        "structure_high": None,
        "structure_low": None,
        "latest_close": None,
        "price_location": "NOT_AVAILABLE",
        "central_zone": "DEFERRED",
        "deterministic_contract_version": 1,
    }


@pytest.mark.parametrize(
    ("values", "field", "price"),
    [
        ([(10, 5), (12, 7), (11, 6)], "top_fractals", 12),
        ([(10, 5), (8, 3), (9, 4)], "bottom_fractals", 3),
    ],
)
def test_strict_fractal_uses_center_price_and_right_bar_confirmation(values, field, price):
    result = _analyze(_ranges(values))

    assert result[field] == [
        {
            "type": "TOP_FRACTAL" if field == "top_fractals" else "BOTTOM_FRACTAL",
            "date": "2026-01-02", "confirmed_at": "2026-01-03", "price": price,
        }
    ]
    other = "bottom_fractals" if field == "top_fractals" else "top_fractals"
    assert result[other] == []
    assert result["structure_high"] == (price if field == "top_fractals" else None)
    assert result["structure_low"] == (price if field == "bottom_fractals" else None)
    assert result["current_structure"] == "INSUFFICIENT_DATA"
    assert result["previous_structure"] == "INSUFFICIENT_DATA"
    assert result["change"] == "NEW_FRACTAL"


@pytest.mark.parametrize(
    "values",
    [
        [(12, 5), (12, 7), (11, 6)],  # Equal high on the left.
        [(10, 5), (12, 7), (12, 6)],  # Equal high on the right.
        [(10, 7), (12, 7), (11, 6)],  # Equal low on the left.
        [(10, 5), (12, 7), (11, 7)],  # Equal low on the right.
        [(8, 5), (8, 3), (9, 4)],
        [(10, 5), (8, 3), (8, 4)],
        [(10, 3), (8, 3), (9, 4)],
        [(10, 5), (8, 3), (9, 3)],
        [(10, 5), (12, 4), (11, 6)],  # Center includes both neighbors.
        [(10, 5), (9, 6), (11, 4)],  # Center is included by both neighbors.
        [(10, 5), (10, 5), (10, 5)],
    ],
)
def test_equality_and_inclusion_do_not_make_fractals(values):
    result = _analyze(_ranges(values))
    assert result["top_fractals"] == result["bottom_fractals"] == []
    assert result["change"] == "UNCHANGED"


@pytest.mark.parametrize("prices", [[10], [10, 20], [10, 20, 30], [30, 20, 10]])
def test_endpoints_and_monotonic_bars_are_not_confirmed(prices):
    result = _analyze(_bars(prices))
    assert result["trade_date"] == (START + timedelta(days=len(prices) - 1)).isoformat()
    assert result["top_fractals"] == result["bottom_fractals"] == []


def test_last_bar_cannot_be_confirmed_by_a_future_right_neighbor():
    rows = _bars([10, 20, 10])
    before = analyze_chan(rows, date(2026, 1, 2))
    assert before["top_fractals"] == []
    assert before["structure_high"] is None
    assert before["change"] == "UNCHANGED"
    assert _analyze(rows)["top_fractals"] == [
        {"type": "TOP_FRACTAL", "date": "2026-01-02", "confirmed_at": "2026-01-03", "price": 21}
    ]


def test_valid_future_suffix_never_changes_any_prefix_output():
    rows = _bars([10, 16, 12, 8, 13, 18, 14, 9, 14, 17, 12, 10, 13])
    for count in range(len(rows) + 1):
        as_of = START + timedelta(days=count - 1)
        expected = analyze_chan(rows[:count], as_of)
        assert analyze_chan(rows, as_of) == expected
        changed_future = deepcopy(rows)
        for row in changed_future[count:]:
            row.update(open=999, high=1000, low=998, close=1000)
        assert analyze_chan(changed_future, as_of) == expected
        for field in ("top_fractals", "bottom_fractals"):
            assert all(f["confirmed_at"] <= as_of.isoformat() for f in expected[field])


def test_previous_structure_uses_previous_supplied_bar_not_calendar_day():
    rows = _bars([10, 12, 10])
    rows[0]["trade_date"] = "2026-01-08"
    rows[1]["trade_date"] = "2026-01-09"
    rows[2]["trade_date"] = "2026-01-12"
    result = analyze_chan(rows, date(2026, 1, 13))
    assert result["trade_date"] == "2026-01-12"
    assert result["top_fractals"][0]["confirmed_at"] == "2026-01-12"
    assert result["previous_structure"] == analyze_chan(
        rows, date(2026, 1, 9)
    )["current_structure"]
    assert result["change"] == "NEW_FRACTAL"
    assert result == analyze_chan(rows, date(2026, 1, 12))


def test_raw_fractal_history_is_full_even_beyond_ten_events():
    rows = _bars([10, 12, 10, 8] * 12 + [10])
    result = _analyze(rows)
    assert len(result["top_fractals"]) == len(result["bottom_fractals"]) == 12
    assert result["top_fractals"][0]["date"] == "2026-01-02"
    assert result["bottom_fractals"][-1]["date"] == "2026-02-17"


def test_same_type_confirmations_never_erase_raw_history():
    result = _analyze(_bars([10, 15, 11, 11, 16, 12]))
    assert result["top_fractals"] == [
        {"type": "TOP_FRACTAL", "date": "2026-01-02", "confirmed_at": "2026-01-03", "price": 16},
        {"type": "TOP_FRACTAL", "date": "2026-01-05", "confirmed_at": "2026-01-06", "price": 17},
    ]
    assert result["bottom_fractals"] == []


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
@pytest.mark.parametrize(
    "bad", [None, True, False, "10", Decimal("10"), float("nan"), float("inf"),
            -float("inf"), 0, -1, 10**400],
)
def test_rejects_nonfinite_nonpositive_or_non_json_numeric_ohlc(field, bad):
    rows = _bars([10])
    rows[0][field] = bad
    with pytest.raises(ValueError):
        analyze_chan(rows, START)


@pytest.mark.parametrize("field", ["trade_date", "open", "high", "low", "close"])
def test_rejects_missing_required_fields(field):
    rows = _bars([10])
    del rows[0][field]
    with pytest.raises(ValueError):
        analyze_chan(rows, START)


@pytest.mark.parametrize(
    "patch",
    [
        {"high": 8}, {"low": 12}, {"open": 12}, {"open": 8},
        {"close": 12}, {"close": 8},
    ],
)
def test_rejects_inconsistent_ohlc_bounds(patch):
    rows = _bars([10])
    rows[0].update(patch)
    with pytest.raises(ValueError):
        analyze_chan(rows, START)


@pytest.mark.parametrize(
    "bad", [None, True, START, "", "2026-1-01", "20260101", "2026-W01-1",
            "2026-02-30", "2026-01-01T00:00:00", " 2026-01-01"],
)
def test_rejects_noncanonical_or_malformed_trade_dates(bad):
    rows = _bars([10])
    rows[0]["trade_date"] = bad
    with pytest.raises(ValueError):
        analyze_chan(rows, START)


@pytest.mark.parametrize("duplicate", [False, True])
def test_rejects_unsorted_and_duplicate_dates_including_future_rows(duplicate):
    rows = _bars([10, 11, 12])
    if duplicate:
        rows[-1]["trade_date"] = rows[-2]["trade_date"]
    else:
        rows[-2:] = rows[-2:][::-1]
    with pytest.raises(ValueError):
        analyze_chan(rows, START)


@pytest.mark.parametrize("rows", [None, {}, (), [None], [1], [[]], [{}]])
def test_rejects_malformed_row_containers(rows):
    with pytest.raises(ValueError):
        analyze_chan(rows, START)


@pytest.mark.parametrize("as_of", [None, "2026-01-01", datetime(2026, 1, 1), 1])
def test_rejects_as_of_values_other_than_date(as_of):
    with pytest.raises(ValueError):
        analyze_chan([], as_of)


def test_malformed_future_ohlc_is_rejected_at_input_boundary():
    rows = _bars([10, 11])
    rows[-1]["close"] = float("nan")
    with pytest.raises(ValueError):
        analyze_chan(rows, START)


def test_flat_valid_ohlc_and_unrelated_columns_are_accepted_without_mutation():
    rows = _ranges([(10.5, 10.5)] * 4)
    rows[0]["volume"] = None
    before = deepcopy(rows)
    result = _analyze(rows)
    assert result["trade_date"] == "2026-01-04"
    assert result["top_fractals"] == result["bottom_fractals"] == []
    assert rows == before


def test_repeat_analysis_is_byte_exact_json_safe_and_does_not_mutate_input():
    rows = _bars([10, 15, 10, 6, 10, 16, 11, 7, 12])
    before = deepcopy(rows)
    first = _analyze(rows)
    second = _analyze(rows)
    assert first == second
    assert json.dumps(first, allow_nan=False) == json.dumps(second, allow_nan=False)
    assert json.loads(json.dumps(first, allow_nan=False)) == first
    assert rows == before


def test_alternating_pivots_form_complete_dated_stroke_candidates():
    result = _analyze(_bars([10, 16, 12, 8, 13, 18, 14, 9, 14]))
    assert result["stroke_candidates"] == [
        {
            "type": "STROKE_CANDIDATE", "direction": "DOWN_STROKE",
            "start_date": "2026-01-02", "end_date": "2026-01-04",
            "start_price": 17, "end_price": 7, "confirmed_at": "2026-01-05",
            "source_bar_separation": 2,
        },
        {
            "type": "STROKE_CANDIDATE", "direction": "UP_STROKE",
            "start_date": "2026-01-04", "end_date": "2026-01-06",
            "start_price": 7, "end_price": 19, "confirmed_at": "2026-01-07",
            "source_bar_separation": 2,
        },
        {
            "type": "STROKE_CANDIDATE", "direction": "DOWN_STROKE",
            "start_date": "2026-01-06", "end_date": "2026-01-08",
            "start_price": 19, "end_price": 8, "confirmed_at": "2026-01-09",
            "source_bar_separation": 2,
        },
    ]


@pytest.mark.parametrize("mirror", [False, True], ids=["tops", "bottoms"])
@pytest.mark.parametrize(("later", "end_date"), [(16, "2026-01-07"), (15, "2026-01-04"),
                                                  (14, "2026-01-04")])
def test_candidate_same_type_keeps_extreme_or_earliest_equal_without_erasing_raw(
    mirror, later, end_date,
):
    prices = [10, 6, 10, 15, 11, 11, later, 12, 8, 10]
    if mirror:
        prices = [30 - price for price in prices]
    result = _analyze(_bars(prices))
    field = "bottom_fractals" if mirror else "top_fractals"
    assert [item["date"] for item in result[field]] == ["2026-01-04", "2026-01-07"]
    strokes = result["stroke_candidates"]
    assert len(strokes) == 2
    assert strokes[0]["direction"] == ("DOWN_STROKE" if mirror else "UP_STROKE")
    assert strokes[0]["end_date"] == strokes[1]["start_date"] == end_date
    assert strokes[0]["end_price"] == (30 - max(15, later) - 1 if mirror else max(15, later) + 1)
    assert strokes[0]["confirmed_at"] == ("2026-01-08" if later == 16 else "2026-01-05")
    assert strokes[0]["source_bar_separation"] == (5 if later == 16 else 2)
    assert result["current_structure"] == (
        ("DOWN_STRUCTURE" if mirror else "UP_STRUCTURE") if later == 16 else "RANGE_STRUCTURE"
    )
    level = "structure_low" if mirror else "structure_high"
    assert result[level] == (30 - later - 1 if mirror else later + 1)


def test_adjacent_centers_are_not_a_stroke_even_with_large_calendar_separation():
    rows = _bars([10, 16, 8, 10])
    rows[2]["trade_date"] = "2026-02-01"
    rows[3]["trade_date"] = "2026-03-01"
    result = _analyze(rows)
    assert len(result["top_fractals"]) == len(result["bottom_fractals"]) == 1
    assert result["stroke_candidates"] == []


@pytest.mark.parametrize("mirror", [False, True], ids=["down", "up"])
@pytest.mark.parametrize("bottom_low", [12, 19], ids=["equal", "wrong_direction"])
def test_opposite_fractal_with_non_strict_price_direction_is_not_a_stroke(mirror, bottom_low):
    ranges = [(10, 5), (12, 7), (11, 6), (30, 6), (25, bottom_low + 1),
              (24, bottom_low), (26, bottom_low + 2)]
    if mirror:
        ranges = [(40 - low, 40 - high) for high, low in ranges]
    result = _analyze(_ranges(ranges))
    assert len(result["top_fractals"]) == len(result["bottom_fractals"]) == 1
    assert result["stroke_candidates"] == []
    assert result["current_structure"] == "TRANSITION"


@pytest.mark.parametrize(
    ("top1", "top2", "bottom1", "bottom2", "expected"),
    [
        (12, 14, 5, 7, "UP_STRUCTURE"),
        (14, 12, 7, 5, "DOWN_STRUCTURE"),
        (14, 12, 5, 7, "RANGE_STRUCTURE"),
        (12, 14, 7, 5, "TRANSITION"),
        (12, 12, 5, 7, "RANGE_STRUCTURE"),
        (14, 12, 5, 5, "RANGE_STRUCTURE"),
        (12, 12, 5, 5, "RANGE_STRUCTURE"),
        (12, 14, 5, 5, "TRANSITION"),
        (12, 12, 7, 5, "TRANSITION"),
    ],
)
def test_structure_uses_latest_two_raw_tops_and_bottoms(top1, top2, bottom1, bottom2, expected):
    result = _analyze(_bars([10, top1, 10, bottom1, 10, top2, 10, bottom2, 10]))
    assert result["current_structure"] == expected
    assert result["previous_structure"] == "INSUFFICIENT_DATA"
    assert result["structure_high"] == top2 + 1
    assert result["structure_low"] == bottom2 - 1
    assert result["change"] == {
        "UP_STRUCTURE": "STRENGTHENING", "DOWN_STRUCTURE": "WEAKENING",
    }.get(expected, "NEW_FRACTAL")


@pytest.mark.parametrize("count", [3, 5, 7, 8])
def test_non_crossed_history_needs_two_fractals_of_each_type(count):
    rows = _bars([10, 12, 10, 5, 10, 14, 10, 7, 10])[:count]
    assert _analyze(rows)["current_structure"] == "INSUFFICIENT_DATA"


@pytest.mark.parametrize("bottom_low", [12, 19], ids=["zero_width", "crossed"])
def test_inconsistent_latest_levels_override_range_without_sorting_or_fabrication(bottom_low):
    rows = _ranges([(10, 5), (12, 7), (10, 5), (8, 3), (10, 5), (12, 7),
                    (11, 6), (30, 6), (25, bottom_low + 1),
                    (24, bottom_low), (26, bottom_low + 2)])
    result = _analyze(rows)
    assert len(result["top_fractals"]) == len(result["bottom_fractals"]) == 2
    assert result["current_structure"] == "TRANSITION"
    assert result["structure_high"] == 12
    assert result["structure_low"] == bottom_low


@pytest.mark.parametrize(
    ("prices", "new_top", "previous", "current", "change"),
    [
        ([10, 12, 10, 5, 10, 14, 10, 7, 10], 13, "UP_STRUCTURE", "RANGE_STRUCTURE", "WEAKENING"),
        ([10, 14, 10, 7, 10, 12, 10, 5, 10], 15, "DOWN_STRUCTURE", "TRANSITION", "STRENGTHENING"),
    ],
)
def test_losing_a_trend_reports_price_direction_before_new_fractal(
    prices, new_top, previous, current, change,
):
    rows = _bars([*prices, new_top, 10])
    result = _analyze(rows)
    assert result["previous_structure"] == previous
    assert result["previous_structure"] == _analyze(rows[:-1])["current_structure"]
    assert result["current_structure"] == current
    assert result["change"] == change


def test_continuing_trend_reports_new_fractal_then_unchanged_without_new_confirmation():
    rows = _bars([10, 12, 10, 5, 10, 14, 10, 7, 10, 15, 12, 11])
    result = _analyze(rows[:-1])
    assert result["current_structure"] == result["previous_structure"] == "UP_STRUCTURE"
    assert result["change"] == "NEW_FRACTAL"
    result = _analyze(rows)
    assert result["current_structure"] == result["previous_structure"] == "UP_STRUCTURE"
    assert result["change"] == "UNCHANGED"


def test_previous_prefix_excludes_the_center_confirmed_only_today():
    rows = _bars([10, 12, 10, 5, 10, 14, 10, 7, 10])
    rows[-1]["trade_date"] = "2026-01-12"
    result = _analyze(rows)
    assert result["current_structure"] == "UP_STRUCTURE"
    assert result["previous_structure"] == "INSUFFICIENT_DATA"
    assert result["bottom_fractals"][-1]["confirmed_at"] == "2026-01-12"
    assert result["change"] == "STRENGTHENING"


@pytest.mark.parametrize(
    ("previous", "current", "has_new", "expected"),
    [
        ("UP_STRUCTURE", "DOWN_STRUCTURE", True, "REVERSING"),
        ("DOWN_STRUCTURE", "UP_STRUCTURE", True, "REVERSING"),
        ("RANGE_STRUCTURE", "UP_STRUCTURE", True, "STRENGTHENING"),
        ("TRANSITION", "UP_STRUCTURE", True, "STRENGTHENING"),
        ("INSUFFICIENT_DATA", "UP_STRUCTURE", True, "STRENGTHENING"),
        ("RANGE_STRUCTURE", "DOWN_STRUCTURE", True, "WEAKENING"),
        ("TRANSITION", "DOWN_STRUCTURE", True, "WEAKENING"),
        ("INSUFFICIENT_DATA", "DOWN_STRUCTURE", True, "WEAKENING"),
        ("UP_STRUCTURE", "RANGE_STRUCTURE", True, "WEAKENING"),
        ("UP_STRUCTURE", "TRANSITION", True, "WEAKENING"),
        ("DOWN_STRUCTURE", "RANGE_STRUCTURE", True, "STRENGTHENING"),
        ("DOWN_STRUCTURE", "TRANSITION", True, "STRENGTHENING"),
        ("RANGE_STRUCTURE", "TRANSITION", True, "NEW_FRACTAL"),
        ("UP_STRUCTURE", "UP_STRUCTURE", True, "NEW_FRACTAL"),
        ("DOWN_STRUCTURE", "DOWN_STRUCTURE", True, "NEW_FRACTAL"),
        ("INSUFFICIENT_DATA", "INSUFFICIENT_DATA", True, "NEW_FRACTAL"),
        ("UP_STRUCTURE", "UP_STRUCTURE", False, "UNCHANGED"),
        ("DOWN_STRUCTURE", "DOWN_STRUCTURE", False, "UNCHANGED"),
    ],
)
def test_change_priority_contract_including_reserved_direct_reversal(previous, current, has_new, expected):
    # One new bar cannot change both fractal types; direct reversal is a reserved mapping.
    from app.services.phase2_chan_structure import _change

    assert _change(current, previous, has_new) == expected


@pytest.mark.parametrize(
    ("close", "location"),
    [
        (14, "ABOVE_CONFIRMED_RANGE"),
        (13, "INSIDE_CONFIRMED_RANGE"),
        (11, "INSIDE_CONFIRMED_RANGE"),
        (6, "INSIDE_CONFIRMED_RANGE"),
        (5, "BELOW_CONFIRMED_RANGE"),
    ],
)
def test_current_close_location_is_separate_from_confirmed_structure(close, location):
    rows = _bars([10, 14, 10, 5, 10, 12, 10, 7, 10])
    before = _analyze(rows)
    rows.append({"trade_date": "2026-01-10", "open": 10, "high": 20, "low": 2, "close": close})
    result = _analyze(rows)

    assert result["latest_close"] == close
    assert result["price_location"] == location
    assert result["current_structure"] == result["previous_structure"] == "RANGE_STRUCTURE"
    assert (result["structure_high"], result["structure_low"]) == (13, 6)
    assert result["change"] == "UNCHANGED"
    assert result["top_fractals"] == before["top_fractals"]
    assert result["bottom_fractals"] == before["bottom_fractals"]


@pytest.mark.parametrize("prices", [[10], [10, 12, 10], [10, 8, 10]])
def test_price_location_is_unavailable_when_a_confirmed_level_is_missing(prices):
    result = _analyze(_bars(prices))
    assert result["latest_close"] == prices[-1] + 1
    assert result["price_location"] == "NOT_AVAILABLE"


@pytest.mark.parametrize("bottom_low", [12, 19])
def test_price_location_is_unavailable_for_zero_width_or_crossed_levels(bottom_low):
    rows = _ranges([(10, 5), (12, 7), (11, 6), (30, 6), (25, bottom_low + 1),
                    (24, bottom_low), (26, bottom_low + 2)])
    result = _analyze(rows)
    assert result["latest_close"] == 26
    assert result["price_location"] == "NOT_AVAILABLE"
    assert (result["structure_high"], result["structure_low"]) == (12, bottom_low)
    assert result["current_structure"] == "TRANSITION"


def test_price_location_can_use_valid_levels_before_two_fractals_per_type():
    result = _analyze(_bars([10, 12, 10, 6, 10]))
    assert result["current_structure"] == "INSUFFICIENT_DATA"
    assert result["latest_close"] == 11
    assert result["price_location"] == "INSIDE_CONFIRMED_RANGE"


def test_latest_close_and_location_use_only_as_of_prefix_with_no_future_confirmation():
    rows = _bars([10, 14, 10, 5, 10, 12, 10, 7, 10])
    rows.append({"trade_date": "2026-01-10", "open": 10, "high": 20, "low": 2, "close": 5})
    before = _analyze(rows)
    rows.extend([
        {"trade_date": "2026-01-11", "open": 100, "high": 100, "low": 1, "close": 100},
        {"trade_date": "2026-01-12", "open": 1, "high": 1000, "low": 1, "close": 1},
    ])
    result = analyze_chan(rows, date(2026, 1, 10))
    assert result == before
    assert result["latest_close"] == 5
    assert result["price_location"] == "BELOW_CONFIRMED_RANGE"
    empty = analyze_chan(rows, START - timedelta(days=1))
    assert empty["latest_close"] is None
    assert empty["price_location"] == "NOT_AVAILABLE"
