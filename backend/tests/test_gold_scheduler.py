import json
import logging
from datetime import date, datetime

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.market_time import CN_TZ
from app.services.gold_calendar import GoldCalendarAuthority
from app.services.gold_errors import GoldDataError
from app.services.gold_external_guard import DisabledGoldNotifier
from app.services.gold_scheduler import GoldSampler, register_gold_sampler
from app.services.gold_shadow import GoldShadowService
from app.services.gold_shadow_store import GoldShadowStore


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


class FakeGateway:
    def __init__(self) -> None:
        self.quote_calls = 0
        self.history_calls: list[date] = []

    def get_quote(self) -> dict[str, object]:
        self.quote_calls += 1
        return {"symbol": "600489.SH", "quote_source": "tickflow"}

    def get_completed_closes(self, expected_date: date, *, calendar) -> list[float]:
        assert calendar.previous_trading_day(expected_date) == date(2026, 7, 15)
        self.history_calls.append(expected_date)
        return [1.0] * 60


class FakeService:
    def __init__(self) -> None:
        self.calls = 0
        self.failures: list[tuple[str, str]] = []

    def evaluate_quote(self, quote, *, completed_closes, expected_date, calendar) -> None:
        self.calls += 1
        assert quote["symbol"] == "600489.SH"
        assert completed_closes == [1.0] * 60
        assert expected_date == date(2026, 7, 16)
        assert calendar.is_trading_day(expected_date) is True

    def fail(self, code: str, message: str) -> None:
        self.failures.append((code, message))


def write_calendar(
    tmp_path, *, covered_years=(2026,), holidays=()
) -> GoldCalendarAuthority:
    store = GoldShadowStore(tmp_path)
    store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (store.root / "holidays.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "timezone": "Asia/Shanghai",
                "covered_years": list(covered_years),
                "holidays": list(holidays),
            }
        ),
        encoding="utf-8",
    )
    return GoldCalendarAuthority(store)


@pytest.mark.parametrize(
    ("stamp", "calendar_kwargs", "expected_failures"),
    [
        (
            "2026-07-16T10:00:00+08:00",
            None,
            [("calendar_unconfigured", "Gold trading calendar is unavailable")],
        ),
        (
            "2026-07-16T10:00:00+08:00",
            {"covered_years": (2025,)},
            [("calendar_unconfigured", "Gold trading calendar is unavailable")],
        ),
        (
            "2026-01-01T10:00:00+08:00",
            {},
            [("calendar_unconfigured", "Gold trading calendar is unavailable")],
        ),
        ("2026-07-18T10:00:00+08:00", {}, []),
        ("2026-07-16T10:00:00+08:00", {"holidays": ("2026-07-16",)}, []),
    ],
)
def test_sampler_calendar_preflight_blocks_all_paid_calls(
    tmp_path, stamp, calendar_kwargs, expected_failures
):
    gateway = FakeGateway()
    service = FakeService()
    authority = (
        GoldCalendarAuthority(GoldShadowStore(tmp_path))
        if calendar_kwargs is None
        else write_calendar(tmp_path, **calendar_kwargs)
    )
    sampler = GoldSampler(gateway, service, authority, clock=lambda: at(stamp))

    sampler.run_once()

    assert gateway.quote_calls == 0
    assert gateway.history_calls == []
    assert service.calls == 0
    assert service.failures == expected_failures


def test_sampler_calls_gateway_once_inside_session(tmp_path):
    gateway = FakeGateway()
    service = FakeService()
    sampler = GoldSampler(
        gateway,
        service,
        write_calendar(tmp_path),
        clock=lambda: at("2026-07-16T10:00:00+08:00"),
    )

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
def test_sampler_never_calls_tickflow_outside_session(tmp_path, stamp):
    gateway = FakeGateway()
    service = FakeService()
    sampler = GoldSampler(gateway, service, write_calendar(tmp_path), clock=lambda: at(stamp))

    assert sampler.run_once() is None
    assert gateway.quote_calls == 0
    assert gateway.history_calls == []
    assert service.calls == 0


def test_sampler_records_safe_failure_without_rethrowing(tmp_path):
    class ExplodingGateway(FakeGateway):
        def get_quote(self) -> dict[str, object]:
            raise RuntimeError("token=must-not-appear")

    service = FakeService()
    sampler = GoldSampler(
        ExplodingGateway(),
        service,
        write_calendar(tmp_path),
        clock=lambda: at("2026-07-16T10:00:00+08:00"),
    )

    assert sampler.run_once() is None
    assert service.failures == [("gold_sampling_failed", "Gold sampling failed")]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("tickflow_capability_unavailable", "TickFlow capability is unavailable"),
        ("tickflow_paid_client_unavailable", "TickFlow paid client is unavailable"),
        ("tickflow_request_failed", "TickFlow request failed"),
        ("quote_contract_invalid", "TickFlow quote contract is invalid"),
        ("history_contract_invalid", "TickFlow history contract is invalid"),
        (
            "history_preceding_session_mismatch",
            "TickFlow history does not end on the expected trading session",
        ),
        ("calendar_unconfigured", "Gold trading calendar is unavailable"),
        ("quote_timestamp_stale", "TickFlow quote timestamp is stale"),
        ("quote_timestamp_future", "TickFlow quote timestamp is in the future"),
        ("quote_timestamp_out_of_session", "TickFlow quote is outside session"),
        ("gold_storage_read_failed", "Gold storage read failed"),
        ("gold_storage_write_failed", "Gold storage write failed"),
    ],
)
def test_sampler_persists_only_allowlisted_gold_error_codes(
    tmp_path, code, message
):
    class FailingGateway(FakeGateway):
        def get_quote(self):
            raise GoldDataError(code)

    service = FakeService()
    sampler = GoldSampler(
        FailingGateway(),
        service,
        write_calendar(tmp_path),
        clock=lambda: at("2026-07-16T10:00:00+08:00"),
    )

    sampler.run_once()

    assert service.failures == [(code, message)]


def test_sampler_collapses_unknown_gold_error_code(tmp_path):
    class FailingGateway(FakeGateway):
        def get_quote(self):
            raise GoldDataError("sdk-secret-unknown-code")

    service = FakeService()
    sampler = GoldSampler(
        FailingGateway(),
        service,
        write_calendar(tmp_path),
        clock=lambda: at("2026-07-16T10:00:00+08:00"),
    )

    sampler.run_once()

    assert service.failures == [("gold_sampling_failed", "Gold sampling failed")]
    assert "sdk-secret" not in repr(service.failures)


def test_sampler_never_persists_logs_or_returns_raw_exception_details(tmp_path, caplog):
    class ExplodingGateway(FakeGateway):
        def get_quote(self):
            raise RuntimeError("status=503 token=raw-sdk-secret")

    authority = write_calendar(tmp_path)
    store = authority.store
    service = GoldShadowService(
        store,
        DisabledGoldNotifier(store.root),
        calendar_authority=authority,
    )
    sampler = GoldSampler(
        ExplodingGateway(),
        service,
        authority,
        clock=lambda: at("2026-07-16T10:00:00+08:00"),
    )

    with caplog.at_level(logging.WARNING):
        sampler.run_once()

    health_text = (store.root / "health.json").read_text(encoding="utf-8")
    status_text = json.dumps(service.status(), ensure_ascii=False)
    assert store.read_health()["last_error"] == {
        "code": "gold_sampling_failed",
        "message": "Gold sampling failed",
    }
    assert "raw-sdk-secret" not in health_text
    assert "raw-sdk-secret" not in status_text
    assert "raw-sdk-secret" not in caplog.text


def test_registers_five_minute_single_instance_job(tmp_path):
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    sampler = GoldSampler(
        FakeGateway(),
        FakeService(),
        write_calendar(tmp_path),
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
