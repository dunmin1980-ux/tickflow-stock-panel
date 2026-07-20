"""P0-6: document SDK-call vs HTTP-request budget limits for Stage A.

We cannot assume one SDK ``klines.batch`` equals one HTTP request. This module
records *SDK call site* counts under a fake client that can simulate internal
chunking. Until a real transport interceptor exists, Stage A must not claim
HTTP-level 80% RPM enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.gold_tickflow import GOLD_SYMBOL, GoldTickFlowGateway
from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet
from app.tickflow.rate_limits import apply_safety_rpm, sleep_between_batches


@dataclass
class CountingTransport:
    """Fake transport that counts logical HTTP posts after SDK-style chunking."""

    batch_limit: int = 100
    http_posts: list[list[str]] = field(default_factory=list)
    max_inflight: int = 0
    _inflight: int = 0

    def post_batch(self, symbols: list[str]) -> list[dict]:
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            chunks = [
                symbols[i : i + self.batch_limit]
                for i in range(0, len(symbols), self.batch_limit)
            ] or [[]]
            # Simulate SDK splitting into one HTTP post per chunk (serial).
            rows: list[dict] = []
            for chunk in chunks:
                self.http_posts.append(list(chunk))
                rows.extend({"symbol": s, "ok": True} for s in chunk)
            return rows
        finally:
            self._inflight -= 1


@dataclass
class SplittingKlines:
    transport: CountingTransport

    def batch(self, symbols, **kwargs):
        return self.transport.post_batch(list(symbols))


@dataclass
class SplittingClient:
    transport: CountingTransport

    def __post_init__(self) -> None:
        self.klines = SplittingKlines(self.transport)
        self.quotes = self

    def get(self, *, symbols, **kwargs):
        return self.transport.post_batch(list(symbols))


def _capset() -> CapabilitySet:
    return CapabilitySet(
        {
            Cap.QUOTE_BATCH: CapabilityLimits(rpm=60, batch=100),
            Cap.KLINE_DAILY_BATCH: CapabilityLimits(rpm=60, batch=100),
        }
    )


def test_safety_rpm_example_budget() -> None:
    assert apply_safety_rpm(60) == 48


def test_simulated_batch_split_100_vs_101_symbols() -> None:
    """Document: 100 symbols → 1 HTTP post; 101 → 2 posts at batch_limit=100."""
    t100 = CountingTransport(batch_limit=100)
    SplittingClient(t100).klines.batch([f"{i:06d}.SZ" for i in range(100)])
    assert len(t100.http_posts) == 1

    t101 = CountingTransport(batch_limit=100)
    SplittingClient(t101).klines.batch([f"{i:06d}.SZ" for i in range(101)])
    assert len(t101.http_posts) == 2
    assert len(t101.http_posts[0]) == 100
    assert len(t101.http_posts[1]) == 1


def test_simulated_batch_250_symbols_three_http_posts() -> None:
    t = CountingTransport(batch_limit=100)
    SplittingClient(t).klines.batch([f"{i:06d}.SZ" for i in range(250)])
    assert len(t.http_posts) == 3
    assert t.max_inflight == 1  # serial simulation — no parallel HTTP


def test_stage_a_gold_single_symbol_is_one_sdk_paced_call(monkeypatch) -> None:
    """Gold Stage A calls one symbol; pacing hits sleep_between_batches once."""
    paces: list[int] = []

    def _capture(index, rpm, **kwargs):
        paces.append(index)

    monkeypatch.setattr(
        "app.services.gold_tickflow.sleep_between_batches",
        _capture,
    )
    transport = CountingTransport(batch_limit=100)
    client = SplittingClient(transport)

    class _Store:
        def read_daily_history(self):
            return None

        def write_daily_history(self, payload):
            return None

        def write_health(self, **kwargs):
            return None

    gw = GoldTickFlowGateway(
        store=_Store(),  # type: ignore[arg-type]
        capset_provider=_capset,
        client_factory=lambda: client,
    )
    # Force quote only — simpler than calendar-backed history.
    row = {"symbol": GOLD_SYMBOL, "last_price": 1.0}
    transport.http_posts.clear()

    def _quote_get(*, symbols, **kwargs):
        transport.http_posts.append(list(symbols))
        return [row]

    client.get = _quote_get  # type: ignore[method-assign]
    out = gw.get_quote()
    assert out["symbol"] == GOLD_SYMBOL
    assert len(transport.http_posts) == 1
    assert paces == [1]


def test_first_batch_burst_still_documented() -> None:
    """Two index=0 paces can burst — Stage A must not run concurrent pipelines."""
    import time

    from app.tickflow.rate_limits import _next_slot, _slot_lock

    with _slot_lock:
        _next_slot.clear()
    t0 = time.perf_counter()
    sleep_between_batches(0, rpm=60)
    sleep_between_batches(0, rpm=60)
    assert time.perf_counter() - t0 < 0.05
