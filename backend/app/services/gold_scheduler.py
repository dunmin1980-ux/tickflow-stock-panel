"""Five-minute in-session sampling for the isolated Gold evaluator."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.market_time import CN_TZ, cn_now

logger = logging.getLogger(__name__)
_SESSION_WINDOWS = (
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
)


class GoldSampler:
    def __init__(self, gateway: Any, service: Any, *, clock: Callable[[], datetime] = cn_now) -> None:
        self.gateway = gateway
        self.service = service
        self.clock = clock

    def run_once(self) -> None:
        try:
            now = self._beijing_now()
            if not self._in_market_session(now):
                return
            quote = self.gateway.get_quote()
            completed_closes = self.gateway.get_completed_closes(now.date())
            self.service.evaluate_quote(
                quote,
                completed_closes=completed_closes,
                expected_date=now.date(),
            )
        except Exception:
            self._record_failure()

    def _beijing_now(self) -> datetime:
        now = self.clock()
        return now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)

    @staticmethod
    def _in_market_session(now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        observed_time = now.timetz().replace(tzinfo=None)
        return any(start <= observed_time <= end for start, end in _SESSION_WINDOWS)

    def _record_failure(self) -> None:
        try:
            self.service.fail("gold_sampling_failed", "Gold sampling failed")
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
