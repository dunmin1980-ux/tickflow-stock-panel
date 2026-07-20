"""TickFlow failure classification + Stage A 429 circuit."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.gold_errors import GoldDataError
from app.services.gold_tickflow import GOLD_SYMBOL
from app.services.gold_tickflow_errors import RateLimitCircuit, classify_tickflow_exception
from tests.test_gold_tickflow import FakeKlines, calendar_for, make_gateway


class _HttpError(Exception):
    def __init__(self, status: int, retry_after: str | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.response = SimpleNamespace(
            status_code=status,
            headers={"Retry-After": retry_after} if retry_after else {},
        )


def test_classify_status_codes() -> None:
    assert classify_tickflow_exception(_HttpError(429)).code == "tickflow_rate_limited"
    assert classify_tickflow_exception(_HttpError(429, "1.5")).retry_after_seconds == 1.5
    assert classify_tickflow_exception(_HttpError(401)).code == "tickflow_auth_failed"
    assert classify_tickflow_exception(_HttpError(403)).code == "tickflow_auth_failed"
    assert classify_tickflow_exception(_HttpError(500)).code == "tickflow_request_failed"


def test_classify_network_timeout() -> None:
    assert classify_tickflow_exception(TimeoutError("timed out")).code == "tickflow_network_failed"


def test_classify_redacts_secrets_in_message() -> None:
    # classification must not require logging the secret; redact helper covers strings
    from app.services.gold_tickflow_errors import _redact

    redacted = _redact("Authorization: Bearer SECRETTOKEN api_key=abc123")
    assert "SECRETTOKEN" not in redacted
    assert "abc123" not in redacted


def test_circuit_halts_then_trips() -> None:
    circuit = RateLimitCircuit()
    e1 = circuit.note_rate_limited(
        pipeline="gold_quote", capability="quote.batch", symbol_count=1, retry_after_seconds=1.0
    )
    assert e1["task_status"] == "pipeline_halted"
    assert not circuit.tripped
    e2 = circuit.note_rate_limited(
        pipeline="gold_quote", capability="quote.batch", symbol_count=1, retry_after_seconds=None
    )
    assert e2["task_status"] == "circuit_open"
    assert circuit.tripped


def test_gateway_maps_429_and_opens_circuit(tmp_path) -> None:
    class BoomQuotes:
        def get(self, **kwargs):
            raise _HttpError(429, "2")

    client = SimpleNamespace(quotes=BoomQuotes(), klines=FakeKlines())
    calendar_for(tmp_path)
    gw = make_gateway(tmp_path, client)
    with pytest.raises(GoldDataError) as first:
        gw.get_quote()
    assert first.value.code == "tickflow_rate_limited"
    with pytest.raises(GoldDataError) as second:
        gw.get_quote()
    assert second.value.code in {"tickflow_rate_limited", "tickflow_circuit_open"}
    # third call should be hard-blocked by circuit
    with pytest.raises(GoldDataError) as third:
        gw.get_quote()
    assert third.value.code == "tickflow_circuit_open"


def test_gateway_maps_401(tmp_path) -> None:
    class BoomQuotes:
        def get(self, **kwargs):
            raise _HttpError(401)

    client = SimpleNamespace(quotes=BoomQuotes(), klines=FakeKlines())
    with pytest.raises(GoldDataError) as exc:
        make_gateway(tmp_path, client).get_quote()
    assert exc.value.code == "tickflow_auth_failed"


def test_gateway_success_resets_circuit_counter(tmp_path) -> None:
    class Flaky:
        def __init__(self) -> None:
            self.n = 0

        def get(self, **kwargs):
            self.n += 1
            if self.n == 1:
                raise _HttpError(429)
            return [
                {
                    "symbol": GOLD_SYMBOL,
                    "timestamp": 1784192400000,
                    "last_price": 19.99,
                    "prev_close": 20.12,
                }
            ]

    quotes = Flaky()
    client = SimpleNamespace(quotes=quotes, klines=FakeKlines())
    gw = make_gateway(tmp_path, client)
    with pytest.raises(GoldDataError):
        gw.get_quote()
    row = gw.get_quote()
    assert row["symbol"] == GOLD_SYMBOL
    assert gw.rate_circuit.consecutive_429 == 0
