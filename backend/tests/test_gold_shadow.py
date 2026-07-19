import json
from datetime import date, datetime
from pathlib import Path

import pytest

from app.market_time import CN_TZ
from app.services.gold_external_guard import DisabledGoldNotifier
from app.services.gold_shadow import GoldShadowService
from app.services.gold_shadow_store import GoldShadowStore

FIXTURE_PATH = Path(__file__).parent / "fixtures/gold/production_2026-07-16.json"


def fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def quote(*, market_date="2026-07-16", observed_at=None) -> dict:
    data = fixture()
    observed_at = observed_at or f"{market_date}T15:00:00+08:00"
    return {
        "symbol": "600489.SH",
        "timestamp": int(datetime.fromisoformat(observed_at).timestamp() * 1000),
        "last_price": data["current_price"],
        "prev_close": data["previous_close"],
        "quote_source": "tickflow",
    }


def calendar_payload(*, holidays=(), covered_years=(2026,)) -> dict[str, object]:
    return {
        "schema_version": 1,
        "timezone": "Asia/Shanghai",
        "covered_years": list(covered_years),
        "holidays": list(holidays),
    }


def make_service(tmp_path, *, clock=None, holidays=()) -> GoldShadowService:
    store = GoldShadowStore(tmp_path)
    store.root.mkdir(parents=True)
    (store.root / "holidays.json").write_text(
        json.dumps(calendar_payload(holidays=holidays)), encoding="utf-8"
    )
    return GoldShadowService(
        store,
        DisabledGoldNotifier(tmp_path / "user_data" / "gold_shadow"),
        clock=clock or (lambda: datetime(2026, 7, 16, 15, 0, tzinfo=CN_TZ)),
    )


@pytest.fixture
def service(tmp_path):
    return make_service(tmp_path)


def evaluate(service, raw_quote, *, closes=None, expected_date=date(2026, 7, 16)):
    return service.evaluate_quote(
        raw_quote,
        completed_closes=fixture()["completed_closes"] if closes is None else closes,
        expected_date=expected_date,
    )


@pytest.mark.parametrize("symbol", [None, "000001.SZ"])
def test_quote_symbol_must_match_gold_symbol_before_persistence(service, symbol):
    invalid_quote = quote()
    if symbol is None:
        del invalid_quote["symbol"]
    else:
        invalid_quote["symbol"] = symbol

    result = evaluate(service, invalid_quote)

    assert result.error_code == "quote_symbol_mismatch"
    assert service.store.list_snapshots(10) == []
    assert service.store.read_health()["last_error"]["code"] == "quote_symbol_mismatch"


@pytest.mark.parametrize("source", [None, "tushare"])
def test_quote_source_must_be_tickflow(service, source):
    invalid_quote = quote()
    invalid_quote["quote_source"] = source

    result = evaluate(service, invalid_quote)

    assert result.error_code == "quote_source_mismatch"
    assert service.store.list_snapshots(10) == []


def test_quote_market_date_must_match_beijing_expected_date(service):
    result = evaluate(
        service,
        quote(market_date="2026-07-15"),
        expected_date=date(2026, 7, 16),
    )

    assert result.error_code == "stale_market_date"
    assert service.store.read_health()["last_error"] == {
        "code": "stale_market_date",
        "message": "TickFlow quote market date is stale",
    }


@pytest.mark.parametrize("contents", [None, "{not-json", '{"unexpected": true}', b"\\xff"])
def test_missing_or_malformed_holiday_calendar_fails_closed(tmp_path, contents):
    store = GoldShadowStore(tmp_path)
    if contents is not None:
        store.root.mkdir(parents=True)
        path = store.root / "holidays.json"
        if isinstance(contents, bytes):
            path.write_bytes(contents)
        else:
            path.write_text(contents, encoding="utf-8")
    service = GoldShadowService(
        store,
        DisabledGoldNotifier(tmp_path / "user_data" / "gold_shadow"),
        clock=lambda: datetime(2026, 7, 16, 15, 0, tzinfo=CN_TZ),
    )

    result = evaluate(service, quote())

    assert result.error_code == "calendar_unconfigured"
    assert store.list_snapshots(10) == []


def test_quote_timestamp_is_interpreted_on_beijing_date(service):
    service.clock = lambda: datetime(2026, 7, 16, 9, 30, tzinfo=CN_TZ)

    result = evaluate(service, quote(observed_at="2026-07-16T01:30:00+00:00"))

    assert result.error_code is None
    assert result.snapshot["observed_at"] == "2026-07-16T09:30:00+08:00"


def test_configured_holiday_is_skipped(tmp_path):
    service = make_service(
        tmp_path,
        clock=lambda: datetime(2026, 6, 19, 15, 0, tzinfo=CN_TZ),
        holidays=("2026-06-19",),
    )

    result = evaluate(
        service,
        quote(market_date="2026-06-19"),
        expected_date=date(2026, 6, 19),
    )

    assert result.error_code == "market_closed"
    assert service.store.read_health() is None


def test_validated_calendar_is_read_once_and_reused_for_closure(service, monkeypatch):
    raw_quote = quote()
    completed_closes = fixture()["completed_closes"]
    reads = 0

    def read_calendar():
        nonlocal reads
        reads += 1
        if reads == 1:
            return calendar_payload(holidays=("2026-07-16",))
        raise OSError("calendar unavailable after validation")

    monkeypatch.setattr(service.store, "read_calendar_config", read_calendar)

    result = service.evaluate_quote(
        raw_quote,
        completed_closes=completed_closes,
        expected_date=date(2026, 7, 16),
    )

    assert result.error_code == "market_closed"
    assert reads == 1
    assert service.store.list_snapshots(10) == []


def test_future_quote_fails_closed(service):
    service.clock = lambda: datetime(2026, 7, 16, 10, 0, tzinfo=CN_TZ)

    result = evaluate(service, quote(observed_at="2026-07-16T10:00:01+08:00"))

    assert result.error_code == "quote_timestamp_future"


def test_stale_quote_fails_closed(service):
    result = evaluate(service, quote(observed_at="2026-07-16T09:35:00+08:00"))

    assert result.error_code == "quote_timestamp_stale"


@pytest.mark.parametrize(
    "observed_at",
    [
        "2026-07-16T09:29:59+08:00",
        "2026-07-16T11:30:01+08:00",
        "2026-07-16T12:55:00+08:00",
        "2026-07-16T15:05:00+08:00",
    ],
)
def test_quote_outside_integrated_session_fails_closed(service, observed_at):
    service.clock = lambda: datetime.fromisoformat(observed_at)

    result = evaluate(service, quote(observed_at=observed_at))

    assert result.error_code == "quote_timestamp_out_of_session"
    assert service.store.list_snapshots(10) == []


@pytest.mark.parametrize(
    "observed_at",
    [
        "2026-07-16T09:30:00+08:00",
        "2026-07-16T11:30:00+08:00",
        "2026-07-16T13:00:00+08:00",
        "2026-07-16T15:00:00+08:00",
    ],
)
def test_session_boundaries_are_valid(service, observed_at):
    service.clock = lambda: datetime.fromisoformat(observed_at)

    result = evaluate(service, quote(observed_at=observed_at))

    assert result.error_code is None
    assert result.snapshot["observed_at"] == observed_at


@pytest.mark.parametrize("count", [59, 61])
def test_history_must_contain_exactly_60_completed_closes(service, count):
    closes = fixture()["completed_closes"][-59:]
    if count == 61:
        closes = [closes[0], *fixture()["completed_closes"]]

    result = evaluate(service, quote(), closes=closes)

    assert result.error_code == "history_insufficient"


def test_previous_close_must_be_positive(service):
    invalid_quote = quote()
    invalid_quote["prev_close"] = 0

    result = evaluate(service, invalid_quote)

    assert result.error_code == "previous_close_missing"
    assert service.store.list_snapshots(10) == []


def test_duplicate_quote_timestamp_reuses_committed_snapshot(service):
    first = evaluate(service, quote())
    second = evaluate(service, quote())

    assert first.error_code is None
    assert second.snapshot == first.snapshot
    assert len(service.store.list_snapshots(10)) == 1


def test_candidates_are_deduplicated_across_quote_timestamps(service):
    service.clock = lambda: datetime(2026, 7, 16, 14, 55, tzinfo=CN_TZ)
    first = evaluate(service, quote(observed_at="2026-07-16T14:55:00+08:00"))
    service.clock = lambda: datetime(2026, 7, 16, 15, 0, tzinfo=CN_TZ)
    second = evaluate(service, quote(observed_at="2026-07-16T15:00:00+08:00"))

    assert first.snapshot["new_candidate_signals"] == ["恐慌极端"]
    assert second.snapshot["candidate_signals"] == ["恐慌极端"]
    assert second.snapshot["new_candidate_signals"] == []


def test_evaluation_failure_records_safe_structured_health(service, monkeypatch):
    def explode(**kwargs):
        raise ValueError("credentials=must-not-appear")

    monkeypatch.setattr("app.services.gold_shadow.calculate_pva", explode)

    result = evaluate(service, quote())

    assert result.error_code == "evaluation_failed"
    assert service.store.list_snapshots(10) == []
    assert service.store.read_health()["last_error"] == {
        "code": "evaluation_failed",
        "message": "Gold evaluation failed",
    }


def test_fail_swallows_health_storage_errors(service, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("token=must-not-appear")

    monkeypatch.setattr(service.store, "read_health", explode)
    monkeypatch.setattr(service.store, "write_health", explode)

    result = service.fail("storage_unavailable", "local state unavailable")

    assert result.error_code == "storage_unavailable"
