"""Authoritative deployment-provided trading calendar for Gold research."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from app.market_time import CN_TZ
from app.services.gold_errors import GoldDataError
from app.services.gold_shadow_store import GoldShadowStorageReadError, GoldShadowStore

_CALENDAR_FIELDS = frozenset(
    {"schema_version", "timezone", "covered_years", "holidays"}
)
_SESSION_WINDOWS = (
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
)


class GoldCalendarError(GoldDataError):
    def __init__(self) -> None:
        super().__init__("calendar_unconfigured")


def is_trading_day(
    market_date: date,
    *,
    covered_years: frozenset[int],
    holidays: frozenset[date],
) -> bool:
    if market_date.year not in covered_years:
        raise GoldCalendarError
    return market_date.weekday() < 5 and market_date not in holidays


def previous_trading_day(
    market_date: date,
    *,
    covered_years: frozenset[int],
    holidays: frozenset[date],
) -> date:
    candidate = market_date - timedelta(days=1)
    while not is_trading_day(
        candidate, covered_years=covered_years, holidays=holidays
    ):
        candidate -= timedelta(days=1)
    return candidate


def is_beijing_session(observed_at: datetime) -> bool:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        return False
    observed_time = observed_at.astimezone(CN_TZ).timetz().replace(tzinfo=None)
    return any(start <= observed_time <= end for start, end in _SESSION_WINDOWS)


@dataclass(frozen=True)
class GoldTradingCalendar:
    covered_years: frozenset[int]
    holidays: frozenset[date]

    def is_trading_day(self, market_date: date) -> bool:
        return is_trading_day(
            market_date,
            covered_years=self.covered_years,
            holidays=self.holidays,
        )

    def previous_trading_day(self, market_date: date) -> date:
        return previous_trading_day(
            market_date,
            covered_years=self.covered_years,
            holidays=self.holidays,
        )


class GoldCalendarAuthority:
    def __init__(self, store: GoldShadowStore) -> None:
        self.store = store

    def load(self, requested_date: date | None = None) -> GoldTradingCalendar:
        try:
            payload = self.store.read_calendar_config()
            calendar = self._validate(payload)
            if requested_date is not None:
                calendar.is_trading_day(requested_date)
            return calendar
        except (GoldShadowStorageReadError, GoldCalendarError, TypeError, ValueError):
            raise GoldCalendarError from None

    @staticmethod
    def _validate(payload: Any) -> GoldTradingCalendar:
        if not isinstance(payload, dict) or set(payload) != _CALENDAR_FIELDS:
            raise GoldCalendarError
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise GoldCalendarError
        if payload["timezone"] != "Asia/Shanghai":
            raise GoldCalendarError

        years = payload["covered_years"]
        if (
            not isinstance(years, list)
            or not years
            or any(type(year) is not int or year < 1 or year > 9999 for year in years)
            or len(set(years)) != len(years)
        ):
            raise GoldCalendarError
        covered_years = frozenset(years)

        configured_holidays = payload["holidays"]
        if not isinstance(configured_holidays, list) or not all(
            isinstance(day, str) for day in configured_holidays
        ):
            raise GoldCalendarError
        if len(set(configured_holidays)) != len(configured_holidays):
            raise GoldCalendarError
        holidays: set[date] = set()
        for configured_day in configured_holidays:
            try:
                holiday = date.fromisoformat(configured_day)
            except ValueError:
                raise GoldCalendarError from None
            if holiday.isoformat() != configured_day or holiday.year not in covered_years:
                raise GoldCalendarError
            holidays.add(holiday)
        return GoldTradingCalendar(covered_years, frozenset(holidays))
