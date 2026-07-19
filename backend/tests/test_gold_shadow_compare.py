from __future__ import annotations

import json
import logging
import math
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from app.services.gold_shadow_compare import (
    A_TOLERANCE,
    MATCH_WINDOW_SECONDS,
    P_TOLERANCE,
    PRICE_TOLERANCE,
    PVA_SIGNALS,
    V_TOLERANCE,
    LegacySample,
    ShadowSample,
    compare_pair,
    compare_samples,
    parse_legacy_log,
    parse_legacy_text,
    parse_shadow_jsonl,
    parse_shadow_rows,
)

FIXTURES = Path(__file__).parent / "fixtures" / "gold"
LEGACY_FIXTURE = FIXTURES / "legacy-monitor-2026-07-16.log"
SHADOW_FIXTURE = FIXTURES / "shadow-snapshots-2026-07-16.jsonl"


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def legacy_sample(**changes) -> LegacySample:
    sample = LegacySample(
        observed_at=at("2026-07-16T15:00:00+08:00"),
        price=20.00,
        p=-12.50,
        v=-0.12,
        a=-0.91,
        state="恐慌",
        signals=("恐慌极端",),
        source_line=1,
    )
    return replace(sample, **changes)


def shadow_sample(**changes) -> ShadowSample:
    sample = ShadowSample(
        observed_at=at("2026-07-16T15:00:01+08:00"),
        price=20.00,
        p=-12.50,
        v=-0.12,
        a=-0.91,
        state="恐慌",
        signals=("恐慌极端",),
        source_line=1,
    )
    return replace(sample, **changes)


def valid_shadow_row() -> dict:
    row = json.loads(SHADOW_FIXTURE.read_text(encoding="utf-8").splitlines()[0])
    row["quote_ts"] = int(at(row["observed_at"]).timestamp() * 1000)
    return row


def write_shadow_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_tolerance_and_signal_constants_are_exact():
    assert PRICE_TOLERANCE == 0.01
    assert P_TOLERANCE == 0.10
    assert V_TOLERANCE == 0.02
    assert A_TOLERANCE == 0.02
    assert MATCH_WINDOW_SECONDS == 150
    assert frozenset({"恐慌极端", "惯性衰竭", "均值回归", "贪婪态"}) == PVA_SIGNALS


def test_legacy_parser_handles_production_variants_and_adjacent_alerts():
    rows = parse_legacy_log(LEGACY_FIXTURE)

    assert len(rows) == 4
    assert rows[0].price == 19.99
    assert rows[0].signals == ("恐慌极端", "止盈触发")
    assert rows[1].observed_at == at("2026-07-16T15:00:00+08:00")
    assert rows[1].state == "恐慌"
    assert rows[-1].signals == ("恐慌极端",)


def test_parse_legacy_text_matches_path_parser():
    assert parse_legacy_text(LEGACY_FIXTURE.read_text(encoding="utf-8")) == parse_legacy_log(
        LEGACY_FIXTURE
    )


def test_legacy_parser_ignores_noise_and_does_not_bridge_it_to_alert(tmp_path):
    path = tmp_path / "legacy.log"
    path.write_text(
        "2026-07-16 15:00:00 INFO price=20 P=-1 V=0 A=0 state=死机\n"
        "2026-07-16 15:00:01 DEBUG unrelated noise\n"
        "2026-07-16 15:00:02 WARNING [ALERT] 恐慌极端\n",
        encoding="utf-8",
    )

    assert parse_legacy_log(path)[0].signals == ()


def test_legacy_parser_warning_is_sanitized(tmp_path, caplog):
    path = tmp_path / "legacy.log"
    path.write_text("api_key=legacy-secret P=-1 V=0 A=0 price=20\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert parse_legacy_log(path) == []

    assert "legacy.log line 1" in caplog.text
    assert "legacy-secret" not in caplog.text


def test_legacy_parser_rejects_single_metric_diagnostic_and_its_alert(tmp_path):
    path = tmp_path / "legacy.log"
    path.write_text(
        "2026-07-16 14:59:00 INFO diagnostic P=-12.59\n"
        "2026-07-16 14:59:01 WARNING [ALERT] 恐慌极端\n"
        "2026-07-16 15:00:00 INFO price=19.99 P=-12.59 V=-0.13 A=-0.93 state=恐慌\n",
        encoding="utf-8",
    )

    rows = parse_legacy_log(path)

    assert len(rows) == 1
    assert rows[0].observed_at == at("2026-07-16T15:00:00+08:00")
    assert rows[0].signals == ()


def test_legacy_parser_rejects_incomplete_marked_sample_and_its_alert(tmp_path):
    path = tmp_path / "legacy.log"
    path.write_text(
        "2026-07-16 15:00:00 INFO Gold Monitor | Price: 19.99 | SJM=恐慌\n"
        "2026-07-16 15:00:01 WARNING [ALERT] 恐慌极端\n"
        "2026-07-16 15:05:00 INFO price=20 P=-1 V=0 A=0 state=死机\n",
        encoding="utf-8",
    )

    rows = parse_legacy_log(path)

    assert len(rows) == 1
    assert rows[0].observed_at == at("2026-07-16T15:05:00+08:00")
    assert rows[0].signals == ()


def test_shadow_parser_skips_malformed_lines_with_sanitized_warning(caplog):
    with caplog.at_level(logging.WARNING):
        rows = parse_shadow_jsonl(SHADOW_FIXTURE)

    assert len(rows) == 3
    assert rows[0].signals == ("恐慌极端",)
    assert rows[-1].state == "死机"
    assert "shadow-snapshots-2026-07-16.jsonl line 3" in caplog.text
    assert "malformed-shadow-secret-token" not in caplog.text


def test_parse_shadow_rows_validates_in_memory_rows():
    rows = [
        json.loads(line)
        for line in SHADOW_FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.startswith("{") and not line.startswith("{malformed")
    ]

    parsed = parse_shadow_rows(rows)

    assert len(parsed) == 3
    assert [sample.source_line for sample in parsed] == [1, 2, 3]
    assert parsed[0].signals == ("恐慌极端",)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("schema_version", 2),
        ("symbol", "000001.SZ"),
        ("market_date", "2026-07-15"),
        ("observed_at", "not-a-timestamp"),
        ("observed_at", "2026-07-16T14:55:01"),
        ("quote_ts", "1784194501000"),
        ("quote_ts", True),
        ("quote_ts", -1),
        ("quote_ts", 1784108101000),
        ("price", "19.99"),
        ("P", True),
        ("A", math.nan),
        ("previous_close", 0),
        ("state", "panic"),
        ("candidate_signals", "恐慌极端"),
        ("candidate_signals", ["unknown"]),
        ("new_candidate_signals", ["惯性衰竭"]),
        ("quote_source", "legacy"),
    ],
)
def test_shadow_parser_rejects_rows_outside_current_snapshot_schema(
    tmp_path, caplog, field, invalid_value
):
    row = valid_shadow_row()
    row[field] = invalid_value
    path = tmp_path / "shadow.jsonl"
    write_shadow_rows(path, [row])

    with caplog.at_level(logging.WARNING):
        assert parse_shadow_jsonl(path) == []

    assert "shadow.jsonl line 1" in caplog.text


def test_shadow_parser_rejects_missing_required_field(tmp_path):
    row = valid_shadow_row()
    del row["native_ema60"]
    path = tmp_path / "shadow.jsonl"
    write_shadow_rows(path, [row])

    assert parse_shadow_jsonl(path) == []


def test_invalid_shadow_row_cannot_consume_nearest_match(tmp_path):
    invalid = valid_shadow_row()
    invalid["symbol"] = "000001.SZ"
    invalid["observed_at"] = "2026-07-16T15:00:00+08:00"
    valid = valid_shadow_row()
    valid["observed_at"] = "2026-07-16T15:01:40+08:00"
    valid["quote_ts"] = int(at(valid["observed_at"]).timestamp() * 1000)
    path = tmp_path / "shadow.jsonl"
    write_shadow_rows(path, [invalid, valid])

    report = compare_samples(
        [legacy_sample(observed_at=at("2026-07-16T15:00:00+08:00"))],
        parse_shadow_jsonl(path),
    )

    assert report.matched == 1
    assert report.pairs[0]["shadow_source_line"] == 2


def test_shifted_shadow_timestamp_is_skipped_and_cannot_consume_match(tmp_path):
    shifted = valid_shadow_row()
    shifted["observed_at"] = "2026-07-16T15:00:00+08:00"
    shifted["quote_ts"] = int(at("2026-07-16T15:00:30+08:00").timestamp() * 1000)
    valid = valid_shadow_row()
    valid["observed_at"] = "2026-07-16T15:01:40+08:00"
    valid["quote_ts"] = int(at(valid["observed_at"]).timestamp() * 1000)
    path = tmp_path / "shadow.jsonl"
    write_shadow_rows(path, [shifted, valid])

    report = compare_samples(
        [legacy_sample(observed_at=at("2026-07-16T15:00:00+08:00"))],
        parse_shadow_jsonl(path),
    )

    assert report.matched == 1
    assert report.pairs[0]["shadow_source_line"] == 2


def test_timezone_equivalent_shadow_timestamp_is_accepted(tmp_path):
    row = valid_shadow_row()
    row["observed_at"] = "2026-07-16T06:55:01Z"
    path = tmp_path / "shadow.jsonl"
    write_shadow_rows(path, [row])

    parsed = parse_shadow_jsonl(path)

    assert len(parsed) == 1
    assert parsed[0].observed_at == at("2026-07-16T14:55:01+08:00")


@pytest.mark.parametrize(
    ("field", "legacy_value", "shadow_value"),
    [
        ("price", 20.00, 20.01),
        ("p", -12.50, -12.40),
        ("v", -0.12, -0.10),
        ("a", -0.91, -0.89),
    ],
)
def test_numeric_tolerance_boundaries_pass(field, legacy_value, shadow_value):
    pair = compare_pair(
        legacy_sample(**{field: legacy_value}),
        shadow_sample(**{field: shadow_value}),
    )

    assert pair["checks"][field] is True


def test_values_outside_tolerance_fail_and_checks_are_machine_readable():
    row = compare_pair(legacy_sample(), shadow_sample(price=20.0101, p=-12.3999))

    assert row["checks"] == {
        "price": False,
        "p": False,
        "v": True,
        "a": True,
        "state": True,
        "signals": True,
    }
    assert row["deltas"]["price"] == pytest.approx(0.0101)


@pytest.mark.parametrize("value", [None, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["price", "p", "v", "a"])
def test_missing_and_nonfinite_numeric_values_fail_closed(field, value):
    row = compare_pair(legacy_sample(), shadow_sample(**{field: value}))

    assert row["checks"][field] is False
    assert row["deltas"][field] is None


def test_nonfinite_pair_values_remain_valid_machine_readable_json():
    row = compare_pair(
        legacy_sample(price=math.nan),
        shadow_sample(p=math.inf, v=-math.inf),
    )

    assert json.loads(json.dumps(row, allow_nan=False))["checks"] == row["checks"]


def test_candidate_comparison_only_uses_stage_a_pva_signals():
    row = compare_pair(
        legacy_sample(signals=("恐慌极端", "止盈触发")),
        shadow_sample(signals=("恐慌极端",)),
    )

    assert row["checks"]["signals"] is True
    assert row["legacy"]["signals"] == ["恐慌极端"]


def test_nearest_match_uses_inclusive_150_second_window():
    legacy = [
        legacy_sample(observed_at=at("2026-07-16T15:00:00+08:00"), source_line=1),
        legacy_sample(observed_at=at("2026-07-16T15:10:00+08:00"), source_line=2),
    ]
    shadow = [
        shadow_sample(observed_at=at("2026-07-16T15:02:30+08:00"), source_line=10),
    ]

    report = compare_samples(legacy, shadow, match_window_seconds=150)

    assert report.matched == 1
    assert report.missing_shadow == 1
    assert report.pairs[0]["time_delta_seconds"] == 150.0


def test_optimal_matching_maximizes_cardinality_before_distance():
    legacy = [
        legacy_sample(observed_at=at("2026-07-16T15:00:00+08:00"), source_line=1),
        legacy_sample(observed_at=at("2026-07-16T15:02:00+08:00"), source_line=2),
    ]
    shadow = [
        shadow_sample(observed_at=at("2026-07-16T14:58:00+08:00"), source_line=10),
        shadow_sample(observed_at=at("2026-07-16T15:01:00+08:00"), source_line=11),
    ]

    report = compare_samples(legacy, shadow)

    assert report.matched == 2
    assert [pair["shadow_source_line"] for pair in report.pairs] == [10, 11]
    assert len({pair["shadow_source_line"] for pair in report.pairs}) == 2


def test_optimal_matching_tie_uses_earliest_shadow_deterministically():
    legacy = [legacy_sample(observed_at=at("2026-07-16T15:00:00+08:00"))]
    shadow = [
        shadow_sample(observed_at=at("2026-07-16T14:59:30+08:00"), source_line=10),
        shadow_sample(observed_at=at("2026-07-16T15:00:30+08:00"), source_line=11),
    ]

    first = compare_samples(legacy, shadow)
    second = compare_samples(list(reversed(legacy)), list(reversed(shadow)))

    assert first.pairs[0]["shadow_source_line"] == 10
    assert second.pairs[0]["shadow_source_line"] == 10


def test_match_is_deterministic_on_ties_and_never_reuses_shadow():
    legacy = [
        legacy_sample(observed_at=at("2026-07-16T15:00:00+08:00"), source_line=1),
        legacy_sample(observed_at=at("2026-07-16T15:01:00+08:00"), source_line=2),
    ]
    shadow = [
        shadow_sample(observed_at=at("2026-07-16T14:59:30+08:00"), source_line=10),
        shadow_sample(observed_at=at("2026-07-16T15:00:30+08:00"), source_line=11),
    ]

    report = compare_samples(legacy, shadow)

    assert report.matched == 2
    assert [pair["shadow_source_line"] for pair in report.pairs] == [10, 11]
    assert len({pair["shadow_source_line"] for pair in report.pairs}) == 2


def test_summary_exposes_thresholds_without_claiming_incomplete_acceptance():
    report = compare_samples(
        [legacy_sample()],
        [shadow_sample()],
        observation_days_claimed=10,
    )
    summary = report.summary

    assert summary["totals"] == {
        "legacy": 1,
        "shadow": 1,
        "matched": 1,
        "missing_shadow": 0,
        "unmatched_shadow": 0,
    }
    assert summary["availability_rate"] == 1.0
    assert summary["pass_rates"] == {
        "price": 1.0,
        "p": 1.0,
        "v": 1.0,
        "a": 1.0,
        "state": 1.0,
        "signals": 1.0,
    }
    assert summary["stage_a"]["thresholds_pass"] is True
    assert summary["stage_a"]["observation_days_claimed"] == 10
    assert summary["stage_a"]["observation_window_verified"] is False
    assert summary["stage_a"]["observation_window_reason"] == (
        "full_day_completeness_not_verifiable_from_comparison_inputs"
    )
    assert "observation_complete" not in summary["stage_a"]
    assert summary["stage_a"]["formal_acceptance"] is False
    assert summary["stage_a"]["status"] == "observation_window_unverified"


def test_fixture_report_captures_missing_state_mismatch_and_malformed_json(caplog):
    with caplog.at_level(logging.WARNING):
        report = compare_samples(
            parse_legacy_log(LEGACY_FIXTURE),
            parse_shadow_jsonl(SHADOW_FIXTURE),
            observation_days_claimed=1,
        )

    assert report.matched == 3
    assert report.missing_shadow == 1
    assert report.summary["availability_rate"] == 0.75
    assert report.summary["pass_rates"]["state"] == pytest.approx(2 / 3)
    assert report.summary["stage_a"]["thresholds_pass"] is False
