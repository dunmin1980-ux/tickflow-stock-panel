"""Five-minute in-session sampling for the isolated Gold evaluator."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.market_time import CN_TZ, cn_now
from app.services.gold_calendar import (
    GoldCalendarAuthority,
    GoldCalendarError,
    is_beijing_session,
)

logger = logging.getLogger(__name__)
class GoldSampler:
    def __init__(
        self,
        gateway: Any,
        service: Any,
        calendar_authority: GoldCalendarAuthority,
        *,
        clock: Callable[[], datetime] = cn_now,
    ) -> None:
        self.gateway = gateway
        self.service = service
        self.calendar_authority = calendar_authority
        self.clock = clock

    def run_once(self) -> None:
        try:
            now = self._beijing_now()
            if not is_beijing_session(now):
                return
            calendar = self.calendar_authority.load(now.date())
            if not calendar.is_trading_day(now.date()):
                return
            calendar.previous_trading_day(now.date())
            quote = self.gateway.get_quote()
            completed_closes = self.gateway.get_completed_closes(
                now.date(), calendar=calendar
            )
            self.service.evaluate_quote(
                quote,
                completed_closes=completed_closes,
                expected_date=now.date(),
                calendar=calendar,
            )
        except GoldCalendarError:
            self._record_failure(
                "calendar_unconfigured", "Gold trading calendar is unavailable"
            )
        except Exception:
            self._record_failure("gold_sampling_failed", "Gold sampling failed")

    def _beijing_now(self) -> datetime:
        now = self.clock()
        return now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)

    def _record_failure(self, code: str, message: str) -> None:
        try:
            self.service.fail(code, message)
        except Exception:
            logger.warning("gold sampler failure could not be persisted")


def register_gold_sampler(scheduler: AsyncIOScheduler, sampler: GoldSampler) -> None:
    scheduler.add_job(
        sampler.run_once,
        trigger=CronTrigger(day_of_week="mon-fri", minute="*/5", timezone="Asia/Shanghai"),
        id="gold_sampler_5m",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        replace_existing=True,
    )
