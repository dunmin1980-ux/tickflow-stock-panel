import hashlib
import json
import math
import stat
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.services.gold_calendar import GoldCalendarAuthority
from app.services.gold_shadow_store import GoldShadowStorageReadError, GoldShadowStore
from app.services.gold_tickflow import GOLD_SYMBOL, GoldDataError, GoldTickFlowGateway
from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet


class FakeQuotes:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or [
            {
                "symbol": GOLD_SYMBOL,
                "timestamp": 1784192400000,
                "last_price": 19.99,
                "prev_close": 20.12,
            }
        ]

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


class FakeKlines:
    def __init__(self, rows=None, symbol=GOLD_SYMBOL):
        self.calls = []
        self.rows = daily_rows(90) if rows is None else rows
        self.symbol = symbol

    def batch(self, symbols, **kwargs):
        self.calls.append((symbols, kwargs))
        return {self.symbol: self.rows}


def daily_rows(
    count: int, *, start: date | None = None
) -> list[dict[str, object]]:
    start = start or date.fromordinal(date(2026, 7, 15).toordinal() - count + 1)
    return [
        {
            "symbol": GOLD_SYMBOL,
            "date": date.fromordinal(start.toordinal() + offset).isoformat(),
            "close": 10.0 + offset,
        }
        for offset in range(count)
    ]


def capset(*caps: Cap) -> CapabilitySet:
    return CapabilitySet({cap: CapabilityLimits(rpm=60, batch=100) for cap in caps})


def make_gateway(tmp_path, client, *, capabilities=None):
    calendar_for(tmp_path)
    return GoldTickFlowGateway(
        GoldShadowStore(tmp_path),
        lambda: capabilities or capset(Cap.QUOTE_BATCH, Cap.KLINE_DAILY_BATCH),
        client_factory=lambda: client,
        clock=lambda: datetime(2026, 7, 16, 15, 5),
    )


def calendar_for(tmp_path, *, holidays=()):
    store = GoldShadowStore(tmp_path)
    store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (store.root / "holidays.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "timezone": "Asia/Shanghai",
                "covered_years": [2026],
                "holidays": list(holidays),
            }
        ),
        encoding="utf-8",
    )
    return GoldCalendarAuthority(store).load(date(2026, 7, 16))


def test_gateway_uses_only_paid_tickflow_namespaces(tmp_path):
    quotes = FakeQuotes()
    klines = FakeKlines()
    client = SimpleNamespace(quotes=quotes, klines=klines)
    gateway = make_gateway(tmp_path, client)

    assert gateway.get_quote()["quote_source"] == "tickflow"
    assert len(gateway.get_completed_closes(date(2026, 7, 16))) == 60
    assert quotes.calls == [{"symbols": [GOLD_SYMBOL], "as_dataframe": False}]
    assert klines.calls == [
        (
            [GOLD_SYMBOL],
            {"period": "1d", "count": 90, "adjust": "none", "as_dataframe": False},
        )
    ]


def test_gateway_paces_each_paid_request_with_resolved_safety_rpm(tmp_path, monkeypatch):
    client = SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines())
    calls = []
    monkeypatch.setattr(
        "app.services.gold_tickflow.sleep_between_batches",
        lambda index, rpm: calls.append((index, rpm)),
    )

    make_gateway(tmp_path, client).get_quote()

    assert calls == [(1, 48)]


@pytest.mark.parametrize("method", ["get_quote", "get_completed_closes"])
def test_gateway_requires_paid_client(tmp_path, method):
    gateway = make_gateway(tmp_path, None)

    with pytest.raises(GoldDataError, match="tickflow_paid_client_unavailable"):
        getattr(gateway, method)(date(2026, 7, 16)) if method == "get_completed_closes" else getattr(gateway, method)()


@pytest.mark.parametrize(
    ("capability", "method"),
    [
        (Cap.QUOTE_BATCH, "get_quote"),
        (Cap.KLINE_DAILY_BATCH, "get_completed_closes"),
    ],
)
def test_gateway_requires_paid_tickflow_capability(tmp_path, capability, method):
    client = SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines())
    gateway = make_gateway(
        tmp_path,
        client,
        capabilities=capset(
            Cap.KLINE_DAILY_BATCH if capability is Cap.QUOTE_BATCH else Cap.QUOTE_BATCH
        ),
    )

    with pytest.raises(GoldDataError, match="tickflow_capability_unavailable"):
        getattr(gateway, method)(date(2026, 7, 16)) if method == "get_completed_closes" else getattr(gateway, method)()


def test_gateway_rejects_wrong_quote_symbol(tmp_path):
    client = SimpleNamespace(quotes=FakeQuotes([{"symbol": "000001.SZ"}]), klines=FakeKlines())

    with pytest.raises(GoldDataError, match="quote_contract_invalid"):
        make_gateway(tmp_path, client).get_quote()


def test_gateway_rejects_mixed_symbol_quote_response(tmp_path):
    client = SimpleNamespace(
        quotes=FakeQuotes(
            [
                {"symbol": GOLD_SYMBOL, "last_price": 19.99},
                {"symbol": "000001.SZ", "last_price": 10.0},
            ]
        ),
        klines=FakeKlines(),
    )

    with pytest.raises(GoldDataError, match="quote_contract_invalid"):
        make_gateway(tmp_path, client).get_quote()


def test_gateway_maps_quote_sdk_exception_without_raw_details(tmp_path):
    class ExplodingQuotes:
        def get(self, **_kwargs):
            raise RuntimeError("status=503 token=quote-sdk-secret")

    client = SimpleNamespace(quotes=ExplodingQuotes(), klines=FakeKlines())

    with pytest.raises(GoldDataError) as caught:
        make_gateway(tmp_path, client).get_quote()

    assert caught.value.code == "tickflow_request_failed"
    assert str(caught.value) == "tickflow_request_failed"
    assert caught.value.__cause__ is None
    assert "quote-sdk-secret" not in repr(caught.value)


def test_gateway_maps_history_sdk_exception_without_raw_details(tmp_path):
    class ExplodingKlines:
        def batch(self, *_args, **_kwargs):
            raise RuntimeError("status=429 token=history-sdk-secret")

    client = SimpleNamespace(quotes=FakeQuotes(), klines=ExplodingKlines())

    with pytest.raises(GoldDataError) as caught:
        make_gateway(tmp_path, client).get_completed_closes(date(2026, 7, 16))

    assert caught.value.code == "tickflow_request_failed"
    assert str(caught.value) == "tickflow_request_failed"
    assert caught.value.__cause__ is None
    assert "history-sdk-secret" not in repr(caught.value)


def test_gateway_maps_capability_provider_exception_without_raw_details(tmp_path):
    calendar_for(tmp_path)

    def exploding_capabilities():
        raise RuntimeError("capability-provider-secret")

    gateway = GoldTickFlowGateway(
        GoldShadowStore(tmp_path),
        exploding_capabilities,
        client_factory=lambda: SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines()),
    )

    with pytest.raises(GoldDataError) as caught:
        gateway.get_quote()

    assert caught.value.code == "tickflow_capability_unavailable"
    assert caught.value.__cause__ is None
    assert "capability-provider-secret" not in repr(caught.value)


def test_gateway_maps_history_storage_read_failure(tmp_path, monkeypatch):
    gateway = make_gateway(
        tmp_path, SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines())
    )

    def fail_read():
        raise GoldShadowStorageReadError("storage-read-secret")

    monkeypatch.setattr(gateway.store, "read_daily_history", fail_read)

    with pytest.raises(GoldDataError, match="gold_storage_read_failed") as caught:
        gateway.get_completed_closes(date(2026, 7, 16))

    assert "storage-read-secret" not in repr(caught.value)


def test_gateway_maps_history_storage_write_failure(tmp_path, monkeypatch):
    gateway = make_gateway(
        tmp_path, SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines())
    )

    def fail_write(_payload):
        raise OSError("storage-write-secret")

    monkeypatch.setattr(gateway.store, "write_daily_history", fail_write)

    with pytest.raises(GoldDataError, match="gold_storage_write_failed") as caught:
        gateway.get_completed_closes(date(2026, 7, 16))

    assert "storage-write-secret" not in repr(caught.value)


def test_gateway_rejects_wrong_history_symbol(tmp_path):
    client = SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines(symbol="000001.SZ"))

    with pytest.raises(GoldDataError, match="history_contract_invalid"):
        make_gateway(tmp_path, client).get_completed_closes(date(2026, 7, 16))


def test_gateway_rejects_duplicate_completed_history_dates(tmp_path):
    rows = daily_rows(90)
    rows[1] = {**rows[1], "date": rows[0]["date"]}
    client = SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines(rows))

    with pytest.raises(GoldDataError, match="history_contract_invalid"):
        make_gateway(tmp_path, client).get_completed_closes(date(2026, 7, 16))


@pytest.mark.parametrize("bad_close", [math.nan, math.inf, -math.inf])
def test_gateway_rejects_nonfinite_history_closes(tmp_path, bad_close):
    rows = daily_rows(90)
    rows[0] = {**rows[0], "close": bad_close}
    client = SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines(rows))

    with pytest.raises(GoldDataError, match="history_contract_invalid"):
        make_gateway(tmp_path, client).get_completed_closes(date(2026, 7, 16))


def test_gateway_rejects_fewer_than_sixty_completed_sessions(tmp_path):
    client = SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines(daily_rows(59)))

    with pytest.raises(GoldDataError, match="history_insufficient"):
        make_gateway(tmp_path, client).get_completed_closes(date(2026, 7, 16))


def test_gateway_requires_history_to_end_on_authoritative_preceding_session(tmp_path):
    rows = daily_rows(60, start=date(2026, 5, 16))
    client = SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines(rows))

    with pytest.raises(GoldDataError, match="history_preceding_session_mismatch"):
        make_gateway(tmp_path, client).get_completed_closes(
            date(2026, 7, 16), calendar=calendar_for(tmp_path)
        )


def test_gateway_reads_valid_same_day_cache_without_requesting_sdk(tmp_path):
    store = GoldShadowStore(tmp_path)
    calendar_for(tmp_path)
    rows = [{"date": row["date"], "close": row["close"]} for row in daily_rows(60)]
    payload = daily_cache_payload(rows, expected_date=date(2026, 7, 16))
    store.write_daily_history(payload)
    gateway = GoldTickFlowGateway(store, lambda: CapabilitySet(), client_factory=lambda: None)

    assert gateway.get_completed_closes(date(2026, 7, 16)) == [float(row["close"]) for row in rows]


def test_gateway_rejects_cache_with_non_tickflow_source(tmp_path):
    store = GoldShadowStore(tmp_path)
    calendar_for(tmp_path)
    rows = [{"date": row["date"], "close": row["close"]} for row in daily_rows(60)]
    payload = daily_cache_payload(rows, expected_date=date(2026, 7, 16))
    payload["source"] = "tushare"
    store._write_json_state_locked("daily_history.json", payload)
    gateway = GoldTickFlowGateway(store, lambda: CapabilitySet(), client_factory=lambda: None)

    with pytest.raises(GoldDataError, match="daily_cache_invalid"):
        gateway.get_completed_closes(date(2026, 7, 16))


@pytest.mark.parametrize("bad_date", ["2026-07-16", "2026-07-17"])
def test_gateway_rejects_cache_rows_on_or_after_expected_market_date(tmp_path, bad_date):
    store = GoldShadowStore(tmp_path)
    calendar_for(tmp_path)
    rows = [{"date": row["date"], "close": row["close"]} for row in daily_rows(60)]
    payload = daily_cache_payload(rows, expected_date=date(2026, 7, 16))
    payload["rows"].append({"date": bad_date, "close": 99.0})
    store.write_daily_history(payload)
    gateway = GoldTickFlowGateway(store, lambda: CapabilitySet(), client_factory=lambda: None)

    with pytest.raises(GoldDataError, match="daily_cache_invalid"):
        gateway.get_completed_closes(date(2026, 7, 16))


def test_gateway_persists_normalized_tickflow_daily_cache_at_private_permissions(tmp_path):
    client = SimpleNamespace(quotes=FakeQuotes(), klines=FakeKlines())
    gateway = make_gateway(tmp_path, client)

    closes = gateway.get_completed_closes(date(2026, 7, 16))
    cache_path = tmp_path / "user_data" / "gold_shadow" / "daily_history.json"
    payload = json.loads(cache_path.read_text(encoding="utf-8"))

    assert len(closes) == 60
    assert payload["schema_version"] == 1
    assert payload["source"] == "tickflow"
    assert payload["expected_market_date"] == "2026-07-16"
    assert payload["rows"] == [
        {"date": row["date"], "close": float(row["close"])} for row in daily_rows(90)
    ]
    assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600


def daily_cache_payload(rows, *, expected_date):
    encoded = json.dumps(rows, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return {
        "schema_version": 1,
        "source": "tickflow",
        "expected_market_date": expected_date.isoformat(),
        "fetched_at": "2026-07-16T15:05:00+08:00",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "rows": rows,
    }
