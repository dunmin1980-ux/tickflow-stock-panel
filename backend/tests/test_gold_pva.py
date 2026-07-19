import json
import math
from pathlib import Path

import pytest

from app.services.gold_pva import (
    GoldPvaResult,
    build_candidate_signals,
    calculate_pva,
    classify_state,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures/gold/production_2026-07-16.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def result_with(*, p: float, v: float, a: float) -> GoldPvaResult:
    return GoldPvaResult(0, 0, p, v, a, classify_state(p))


def test_production_fixture_matches_2026_07_16():
    fixture = load_fixture()
    assert len(fixture["completed_closes"]) == 60
    result = calculate_pva(
        current_price=fixture["current_price"],
        previous_close=fixture["previous_close"],
        completed_closes=fixture["completed_closes"],
    )
    assert result.legacy_reference_60 == pytest.approx(22.87, abs=0.01)
    assert result.p == -12.59
    assert result.v == -0.13
    assert result.a == -0.93
    assert result.state == "恐慌"
    assert result.candidate_signals == ("恐慌极端",)


def test_acceleration_matches_deployed_legacy_history_indexing():
    result = calculate_pva(
        current_price=18.79,
        previous_close=19.99,
        completed_closes=[22.0] * 57 + [20.47, 20.12, 19.99],
    )

    assert result.v == -1.20
    assert result.a == -0.85


@pytest.mark.parametrize(
    ("p", "expected"),
    [(-5.00, "恐慌"), (-4.99, "死机"), (7.99, "死机"), (8.00, "贪婪")],
)
def test_state_thresholds_are_inclusive_at_production_boundaries(p, expected):
    assert classify_state(p) == expected


def test_inertia_exhaustion_requires_rising_recent_velocities_and_positive_a():
    result = result_with(p=-2.0, v=0.2, a=0.1)
    closes = [10.0, 9.4, 9.0, 8.8]
    assert "惯性衰竭" in build_candidate_signals(result, closes)


def test_inertia_exhaustion_requires_rising_recent_velocities():
    result = result_with(p=-2.0, v=0.2, a=0.1)
    closes = [10.0, 9.0, 9.5, 9.2]
    assert "惯性衰竭" not in build_candidate_signals(result, closes)


@pytest.mark.parametrize("p", [-3.00, -2.99])
def test_mean_reversion_uses_confirmed_deviation_boundary(p):
    expected = ("均值回归",) if p == -3.00 else ()
    assert build_candidate_signals(result_with(p=p, v=0.01, a=0), [1, 2, 3, 4]) == expected


@pytest.mark.parametrize("field", ["current_price", "previous_close", "completed_closes"])
@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_calculate_pva_rejects_nonfinite_prices(field, bad_value):
    kwargs = {
        "current_price": 10.0,
        "previous_close": 10.0,
        "completed_closes": [10.0] * 60,
    }
    if field == "completed_closes":
        kwargs[field][0] = bad_value
    else:
        kwargs[field] = bad_value
    with pytest.raises(ValueError, match="invalid_price"):
        calculate_pva(**kwargs)


def test_calculate_pva_rejects_fewer_than_60_completed_closes():
    with pytest.raises(ValueError, match="history_insufficient"):
        calculate_pva(current_price=10.0, previous_close=10.0, completed_closes=[10.0] * 59)


@pytest.mark.parametrize("field", ["current_price", "previous_close", "completed_closes"])
@pytest.mark.parametrize("bad_value", [0.0, -1.0])
def test_calculate_pva_rejects_nonpositive_prices(field, bad_value):
    kwargs = {
        "current_price": 10.0,
        "previous_close": 10.0,
        "completed_closes": [10.0] * 60,
    }
    if field == "completed_closes":
        kwargs[field][0] = bad_value
    else:
        kwargs[field] = bad_value
    with pytest.raises(ValueError, match="invalid_price"):
        calculate_pva(**kwargs)


def test_mean_reversion_requires_negative_deviation_and_positive_velocity():
    assert build_candidate_signals(result_with(p=-3.0, v=0.01, a=0), [1, 2, 3, 4]) == (
        "均值回归",
    )


def test_greed_signal_matches_state_boundary():
    assert build_candidate_signals(result_with(p=8.0, v=0, a=0), [1, 2, 3, 4]) == ("贪婪态",)
