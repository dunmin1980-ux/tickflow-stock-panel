from datetime import date, datetime

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.market_time import CN_TZ
from app.services.gold_scheduler import GoldSampler, register_gold_sampler


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


class FakeGateway:
    def __init__(self) -> None:
        self.quote_calls = 0
        self.history_calls: list[date] = []

    def get_quote(self) -> dict[str, object]:
        self.quote_calls += 1
        return {"symbol": "600489.SH", "quote_source": "tickflow"}

    def get_completed_closes(self, expected_date: date) -> list[float]:
        self.history_calls.append(expected_date)
        return [1.0] * 60


class FakeService:
    def __init__(self) -> None:
        self.calls = 0
        self.failures: list[tuple[str, str]] = []

    def evaluate_quote(self, quote, *, completed_closes, expected_date) -> None:
        self.calls += 1
        assert quote["symbol"] == "600489.SH"
        assert completed_closes == [1.0] * 60
        assert expected_date == date(2026, 7, 16)

    def fail(self, code: str, message: str) -> None:
        self.failures.append((code, message))


def test_sampler_calls_gateway_once_inside_session():
    gateway = FakeGateway()
    service = FakeService()
    sampler = GoldSampler(gateway, service, clock=lambda: at("2026-07-16T10:00:00+08:00"))

    sampler.run_once()

    assert gateway.quote_calls == 1
    assert gateway.history_calls == [date(2026, 7, 16)]
    assert service.calls == 1


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-07-16T09:25:00+08:00",
        "2026-07-16T11:35:00+08:00",
        "2026-07-16T12:55:00+08:00",
        "2026-07-16T15:05:00+08:00",
        "2026-07-18T10:00:00+08:00",
    ],
)
def test_sampler_never_calls_tickflow_outside_session(stamp):
    gateway = FakeGateway()
    service = FakeService()
    sampler = GoldSampler(gateway, service, clock=lambda: at(stamp))

    assert sampler.run_once() is None
    assert gateway.quote_calls == 0
    assert gateway.history_calls == []
    assert service.calls == 0


def test_sampler_records_safe_failure_without_rethrowing():
    class ExplodingGateway(FakeGateway):
        def get_quote(self) -> dict[str, object]:
            raise RuntimeError("token=must-not-appear")

    service = FakeService()
    sampler = GoldSampler(
        ExplodingGateway(),
        service,
        clock=lambda: at("2026-07-16T10:00:00+08:00"),
    )

    assert sampler.run_once() is None
    assert service.failures == [("gold_sampling_failed", "Gold sampling failed")]


def test_registers_five_minute_single_instance_job():
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    sampler = GoldSampler(
        FakeGateway(),
        FakeService(),
        clock=lambda: datetime(2026, 7, 16, 10, 0, tzinfo=CN_TZ),
    )

    register_gold_sampler(scheduler, sampler)
    job = scheduler.get_job("gold_sampler_5m")

    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == 30
    assert str(job.trigger.timezone) == "Asia/Shanghai"
    assert job.trigger.fields[6].expressions[0].step == 5
