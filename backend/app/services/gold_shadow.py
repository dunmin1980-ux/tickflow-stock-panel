"""Isolated evaluation orchestration for the single-symbol gold shadow."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.market_time import CN_TZ, cn_now
from app.services.gold_calendar import (
    GoldCalendarAuthority,
    GoldCalendarError,
    GoldTradingCalendar,
    is_beijing_session,
)
from app.services.gold_external_guard import DisabledGoldNotifier
from app.services.gold_pva import GoldPvaResult, calculate_pva

logger = logging.getLogger(__name__)
_UNSET = object()
QUOTE_MAX_AGE = timedelta(minutes=10)


@dataclass(frozen=True)
class GoldEvaluation:
    snapshot: dict[str, Any] | None
    error_code: str | None
    error_message: str | None


def market_date_from_timestamp(timestamp: object) -> date | None:
    """Return the Beijing market date represented by a TickFlow millisecond timestamp."""
    parsed = _parse_quote_timestamp(timestamp)
    return parsed[1].date() if parsed is not None else None


def _parse_quote_timestamp(timestamp: object) -> tuple[int, datetime] | None:
    if isinstance(timestamp, bool) or timestamp is None:
        return None
    try:
        quote_ts = int(timestamp)
        return quote_ts, datetime.fromtimestamp(quote_ts / 1000.0, tz=CN_TZ)
    except (OSError, OverflowError, TypeError, ValueError):
        return None


class GoldShadowService:
    SYMBOL = "600489.SH"

    def __init__(
        self,
        store,
        notifier: DisabledGoldNotifier,
        *,
        clock=cn_now,
        calendar_authority: GoldCalendarAuthority | None = None,
    ) -> None:
        self.store = store
        self.notifier = notifier
        self.clock = clock
        self.calendar_authority = calendar_authority or GoldCalendarAuthority(store)

    def evaluate_quote(
        self,
        quote: dict[str, Any],
        *,
        completed_closes: list[float],
        expected_date: date,
        calendar: GoldTradingCalendar | None = None,
    ) -> GoldEvaluation:
        if quote.get("symbol") != self.SYMBOL:
            return self.fail("quote_symbol_mismatch", "TickFlow quote symbol does not match Gold")
        if quote.get("quote_source") != "tickflow":
            return self.fail("quote_source_mismatch", "Gold quote source must be tickflow")
        now = self._beijing_now()
        parsed = _parse_quote_timestamp(quote.get("timestamp"))
        if parsed is None:
            return self.fail("quote_timestamp_missing", "TickFlow quote has no valid timestamp")
        quote_ts, quote_time = parsed
        if quote_time.date() != expected_date:
            return self.fail("stale_market_date", "TickFlow quote market date is stale")
        try:
            calendar = calendar or self.calendar_authority.load(expected_date)
            trading_day = calendar.is_trading_day(expected_date)
        except GoldCalendarError:
            return self.fail("calendar_unconfigured", "Gold holiday calendar unavailable")
        if not trading_day:
            return GoldEvaluation(None, "market_closed", expected_date.isoformat())
        if quote_time > now:
            return self.fail("quote_timestamp_future", "TickFlow quote timestamp is in the future")
        if now - quote_time > QUOTE_MAX_AGE:
            return self.fail("quote_timestamp_stale", "TickFlow quote is older than 600 seconds")
        if not is_beijing_session(quote_time):
            return self.fail("quote_timestamp_out_of_session", "TickFlow quote is outside session")
        if len(completed_closes) != 60:
            return self.fail("history_insufficient", "Gold requires exactly 60 completed closes")
        previous_close = self._positive_previous_close(quote.get("prev_close"))
        if previous_close is None:
            return self.fail("previous_close_missing", "TickFlow quote has no positive previous close")

        try:
            result = calculate_pva(
                current_price=float(quote["last_price"]),
                previous_close=previous_close,
                completed_closes=completed_closes,
            )
            snapshot = self._build_snapshot(
                quote, quote_ts, quote_time, expected_date, result, previous_close
            )
            committed = self.store.commit_evaluation(snapshot, now=now)
            self._record_success(committed)
            return GoldEvaluation(committed, None, None)
        except (KeyError, TypeError, ValueError):
            return self.fail("evaluation_failed", "Gold evaluation failed")

    def fail(self, code: str, message: str) -> GoldEvaluation:
        """Persist a structured, local-only failure state and return it to the caller."""
        health = self._health_state()
        self._write_health(
            last_success=health["latest_success_at"],
            consecutive_failures=health["consecutive_failures"] + 1,
            last_error={"code": code, "message": message},
        )
        return GoldEvaluation(None, code, message)

    def status(self) -> dict[str, Any]:
        latest, latest_read_failed = self._read_latest_snapshot()
        persisted_health, health_read_failed = self._read_health_state()
        latest_market_date = latest.get("market_date") if isinstance(latest, dict) else None
        health = self._health_state(latest, persisted_health)
        if latest_read_failed or health_read_failed:
            health["last_error"] = {
                "code": "storage_read_failed",
                "message": "gold shadow storage read failed",
            }
        send_count = self.notifier.attempt_count()
        return {
            "enabled": True,
            "symbol": self.SYMBOL,
            "latest": latest,
            "latest_market_date": latest_market_date,
            "health": health,
            "external_send_count": send_count,
            "telegram_send_count": send_count,
        }

    def _beijing_now(self) -> datetime:
        now = self.clock()
        return now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)

    @staticmethod
    def _positive_previous_close(value: object) -> float | None:
        try:
            previous_close = float(value)
        except (TypeError, ValueError):
            return None
        return previous_close if math.isfinite(previous_close) and previous_close > 0 else None

    def _build_snapshot(
        self,
        quote: dict[str, Any],
        quote_ts: int,
        quote_time: datetime,
        market_date: date,
        result: GoldPvaResult,
        previous_close: float,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "observed_at": quote_time.isoformat(),
            "market_date": market_date.isoformat(),
            "symbol": self.SYMBOL,
            "quote_source": "tickflow",
            "quote_ts": quote_ts,
            "price": float(quote["last_price"]),
            "previous_close": previous_close,
            "legacy_reference_60": result.legacy_reference_60,
            "native_ema60": result.native_ema60,
            "P": result.p,
            "V": result.v,
            "A": result.a,
            "state": result.state,
            "candidate_signals": list(result.candidate_signals),
            "new_candidate_signals": [],
        }

    def _record_success(self, snapshot: dict[str, Any]) -> None:
        self._write_health(
            last_success=snapshot["observed_at"],
            consecutive_failures=0,
            last_error=None,
        )

    def _health_state(
        self,
        latest: dict[str, Any] | None | object = _UNSET,
        health: dict[str, Any] | None | object = _UNSET,
    ) -> dict[str, Any]:
        health = self._read_health() if health is _UNSET else health
        latest = self._latest_snapshot() if latest is _UNSET else latest
        last_success = None
        if isinstance(latest, dict):
            observed_at = latest.get("observed_at")
            last_success = observed_at if isinstance(observed_at, str) else None
        if last_success is None and isinstance(health, dict):
            last_success = health.get("last_success")
        failures = health.get("consecutive_failures", 0) if isinstance(health, dict) else 0
        return {
            "latest_success_at": last_success,
            "latest_market_date": latest.get("market_date") if isinstance(latest, dict) else None,
            "consecutive_failures": failures if isinstance(failures, int) and failures >= 0 else 0,
            "last_error": health.get("last_error") if isinstance(health, dict) else None,
        }

    def _read_health(self) -> dict[str, Any] | None:
        health, _ = self._read_health_state()
        return health

    def _read_health_state(self) -> tuple[dict[str, Any] | None, bool]:
        try:
            health = self.store.read_health()
        except Exception:
            logger.warning("gold shadow health read failed")
            return None, True
        return (health if isinstance(health, dict) else None), False

    def _latest_snapshot(self) -> dict[str, Any] | None:
        latest, _ = self._read_latest_snapshot()
        return latest

    def _read_latest_snapshot(self) -> tuple[dict[str, Any] | None, bool]:
        try:
            latest = self.store.latest_snapshot()
        except Exception:
            logger.warning("gold shadow latest snapshot read failed")
            return None, True
        return (latest if isinstance(latest, dict) else None), False

    def _write_health(
        self,
        *,
        last_success: str | None,
        consecutive_failures: int,
        last_error: dict[str, str] | None,
    ) -> None:
        try:
            self.store.write_health(
                last_success=last_success,
                consecutive_failures=consecutive_failures,
                last_error=last_error,
            )
        except Exception:
            logger.warning("gold shadow health write failed")
