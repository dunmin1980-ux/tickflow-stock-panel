from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from app.services.gold_calendar import (
    GoldCalendarAuthority,
    GoldCalendarError,
    is_beijing_session,
)
from app.services.gold_shadow_store import GoldShadowStore


def calendar_payload(
    *, covered_years: list[int] | None = None, holidays: list[str] | None = None
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "timezone": "Asia/Shanghai",
        "covered_years": covered_years or [2026],
        "holidays": holidays or ["2026-01-01"],
    }


def write_calendar(store: GoldShadowStore, payload: object) -> None:
    store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (store.root / "holidays.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_calendar_authority_validates_schema_and_provides_trading_day_helpers(tmp_path):
    store = GoldShadowStore(tmp_path)
    write_calendar(
        store,
        calendar_payload(holidays=["2026-01-01", "2026-07-15"]),
    )

    calendar = GoldCalendarAuthority(store).load(date(2026, 7, 16))

    assert calendar.is_trading_day(date(2026, 7, 16)) is True
    assert calendar.is_trading_day(date(2026, 7, 15)) is False
    assert calendar.is_trading_day(date(2026, 7, 18)) is False
    assert calendar.previous_trading_day(date(2026, 7, 16)) == date(2026, 7, 14)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "timezone": "Asia/Shanghai", "covered_years": [2026]},
        {**calendar_payload(), "unexpected": True},
        {**calendar_payload(), "schema_version": True},
        {**calendar_payload(), "schema_version": 2},
        {**calendar_payload(), "timezone": "UTC"},
        {**calendar_payload(), "covered_years": []},
        {**calendar_payload(), "covered_years": [2026, 2026]},
        {**calendar_payload(), "covered_years": ["2026"]},
        {**calendar_payload(), "holidays": ["2026-02-30"]},
        {**calendar_payload(), "holidays": ["2026-01-01", "2026-01-01"]},
        {**calendar_payload(), "holidays": ["2027-01-01"]},
    ],
)
def test_calendar_authority_rejects_wrong_schema_without_details(tmp_path, payload):
    store = GoldShadowStore(tmp_path)
    write_calendar(store, payload)

    with pytest.raises(GoldCalendarError, match=r"^calendar_unconfigured$"):
        GoldCalendarAuthority(store).load(date(2026, 7, 16))


@pytest.mark.parametrize("contents", [None, b"{", b"\xff"])
def test_calendar_authority_rejects_missing_malformed_or_unreadable_data(
    tmp_path, contents
):
    store = GoldShadowStore(tmp_path)
    if contents is not None:
        store.root.mkdir(mode=0o700, parents=True)
        (store.root / "holidays.json").write_bytes(contents)

    with pytest.raises(GoldCalendarError, match=r"^calendar_unconfigured$"):
        GoldCalendarAuthority(store).load(date(2026, 7, 16))


def test_calendar_authority_rejects_symlink_without_reading_target(tmp_path):
    store = GoldShadowStore(tmp_path)
    store.root.mkdir(mode=0o700, parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(calendar_payload()), encoding="utf-8")
    (store.root / "holidays.json").symlink_to(outside)

    with pytest.raises(GoldCalendarError, match=r"^calendar_unconfigured$"):
        GoldCalendarAuthority(store).load(date(2026, 7, 16))


def test_calendar_authority_rejects_requested_year_without_coverage(tmp_path):
    store = GoldShadowStore(tmp_path)
    write_calendar(store, calendar_payload(covered_years=[2025], holidays=["2025-01-01"]))

    with pytest.raises(GoldCalendarError, match=r"^calendar_unconfigured$"):
        GoldCalendarAuthority(store).load(date(2026, 7, 16))


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("2026-07-16T09:30:00+08:00", True),
        ("2026-07-16T11:30:00+08:00", True),
        ("2026-07-16T13:00:00+08:00", True),
        ("2026-07-16T15:00:00+08:00", True),
        ("2026-07-16T09:29:59+08:00", False),
        ("2026-07-16T11:30:01+08:00", False),
        ("2026-07-16T12:59:59+08:00", False),
        ("2026-07-16T15:00:01+08:00", False),
        ("2026-07-16T01:30:00+00:00", True),
    ],
)
def test_beijing_session_predicate_has_inclusive_boundaries(stamp, expected):
    assert is_beijing_session(datetime.fromisoformat(stamp)) is expected


def test_beijing_session_predicate_rejects_naive_datetimes():
    assert is_beijing_session(datetime(2026, 7, 16, 10, 0)) is False
