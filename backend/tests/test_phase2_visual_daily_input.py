from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.schemas.phase2_option_c import (
    ContinuousDayInput,
    SimulationConfig,
    canonical_option_c_bytes,
)
from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_option_c_continuous import new_continuous_state
from app.services.phase2_visual_daily_input import (
    DailyMarketSnapshot,
    Phase2VisualDailyInputBuilder,
    StockSdkTencentDailyGateway,
    ValidatedDailyManifest,
    VisualDailyInputError,
    load_validated_daily_bundle,
)

TARGET = date(2026, 8, 21)
NOW = datetime.fromisoformat("2026-08-21T16:20:00+08:00")


def _rows(
    *,
    target: date = TARGET,
    bullish: bool = False,
    risk: bool = False,
) -> tuple[list[dict], list[dict]]:
    raw: list[dict] = []
    qfq: list[dict] = []
    trading_dates: list[date] = []
    cursor = target
    while len(trading_dates) < 100:
        if cursor.weekday() < 5:
            trading_dates.insert(0, cursor)
        cursor = date.fromordinal(cursor.toordinal() - 1)
    base = 10.0
    for index, trade_date in enumerate(trading_dates):
        close = base
        if bullish and index >= 80:
            close += (index - 79) * 0.06
        if risk and index >= 80:
            close -= (index - 79) * 0.08
        row = {
            "trade_date": trade_date.isoformat(),
            "open": close - 0.02,
            "high": close + 0.08,
            "low": close - 0.08,
            "close": close,
            "volume": 100000 + index * 100,
            "amount": (100000 + index * 100) * close,
        }
        raw.append(copy.deepcopy(row))
        qfq.append(copy.deepcopy(row))
    return raw, qfq


class FakeGateway:
    def __init__(
        self,
        *,
        bullish: bool = False,
        risk: bool = False,
        fetched_at: datetime = NOW,
        target_date: date = TARGET,
        trading_dates: list[date] | None = None,
    ) -> None:
        self.raw, self.qfq = _rows(
            target=target_date,
            bullish=bullish,
            risk=risk,
        )
        self.fetched_at = fetched_at
        self.target_date = target_date
        self.trading_dates = trading_dates or [
            date.fromordinal(target_date.toordinal() - 1),
            target_date,
            date.fromordinal(target_date.toordinal() + 3),
        ]
        self.calls = 0

    def fetch(self, symbol: str, requested_date: date) -> DailyMarketSnapshot:
        self.calls += 1
        assert symbol == "000403.SZ"
        assert requested_date == self.target_date
        return DailyMarketSnapshot(
            source_provider="stocksdk_tencent_fallback",
            symbol=symbol,
            requested_date=requested_date,
            fetched_at=self.fetched_at,
            raw_rows=copy.deepcopy(self.raw),
            qfq_rows=copy.deepcopy(self.qfq),
            trading_dates=list(self.trading_dates),
            request_count=3,
        )


def _builder(tmp_path: Path, gateway: FakeGateway) -> Phase2VisualDailyInputBuilder:
    return Phase2VisualDailyInputBuilder(
        input_root=tmp_path / "inputs",
        market_gateway=gateway,
        now=lambda: gateway.fetched_at,
    )


def _resign_manifest(day_root: Path, artifact_name: str, raw: bytes) -> None:
    manifest_path = day_root / "manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["artifact_sha256"][artifact_name] = hashlib.sha256(raw).hexdigest()
    provisional = ValidatedDailyManifest.model_validate(payload, strict=False)
    payload["manifest_identity"] = provisional.computed_identity()
    manifest_path.write_bytes(canonical_json_bytes(payload))


def test_builds_and_reloads_atomic_validated_daily_bundle(tmp_path: Path) -> None:
    gateway = FakeGateway()
    builder = _builder(tmp_path, gateway)
    state = new_continuous_state(SimulationConfig.default())

    first = builder.prepare(TARGET, state)
    second = builder.prepare(TARGET, state)
    loaded = load_validated_daily_bundle(builder.input_root, TARGET)

    assert first.day_input.source_fixture == "VALIDATED_DAILY_INPUT"
    assert first.day_input.trade_date == TARGET
    assert first.day_input.valuation_bar.price_basis == "raw"
    assert first.validation.status == "CLAIMS_VALID"
    assert first.validation.unsourced_claim_count == 0
    assert first.validation.raw_qfq_mismatch_count == 0
    assert first.manifest.source_provider == "stocksdk_tencent_fallback"
    assert first.manifest.source_request_count == 3
    assert first.manifest.can_publish is False
    assert first.manifest.trading_advice is False
    assert first.manifest.artifact_sha256 == loaded.manifest.artifact_sha256
    assert first.day_input == second.day_input == loaded.day_input
    assert gateway.calls == 1
    assert not list(builder.input_root.glob(".*.staging-*"))


@pytest.mark.parametrize(
    ("bullish", "risk", "expected_signal"),
    [
        (True, False, "POSITIVE_OBSERVATION"),
        (False, True, "RISK_OBSERVATION"),
        (False, False, "MIXED_OBSERVATION"),
    ],
)
def test_signal_is_recomputed_from_typed_claims(
    tmp_path: Path,
    bullish: bool,
    risk: bool,
    expected_signal: str,
) -> None:
    bundle = _builder(tmp_path, FakeGateway(bullish=bullish, risk=risk)).prepare(
        TARGET,
        new_continuous_state(SimulationConfig.default()),
    )

    assert bundle.day_input.signal.code == expected_signal
    assert bundle.day_input.signal.reason_refs
    assert all(ref.startswith("000403SZ-20260821-") for ref in bundle.day_input.signal.reason_refs)


def test_raw_price_scale_does_not_change_qfq_technical_signal(tmp_path: Path) -> None:
    baseline_gateway = FakeGateway(bullish=True)
    scaled_gateway = FakeGateway(bullish=True)
    for row in scaled_gateway.raw:
        for field in ("open", "high", "low", "close"):
            row[field] *= 0.5
        row["amount"] *= 0.5

    baseline = _builder(tmp_path / "baseline", baseline_gateway).prepare(
        TARGET,
        new_continuous_state(SimulationConfig.default()),
    )
    scaled = _builder(tmp_path / "scaled", scaled_gateway).prepare(
        TARGET,
        new_continuous_state(SimulationConfig.default()),
    )

    assert baseline.day_input.signal.code == "POSITIVE_OBSERVATION"
    assert scaled.day_input.signal.code == baseline.day_input.signal.code
    assert scaled.facts["daily"]["price_basis"] == "raw"
    assert scaled.facts["indicators"]["ma20"]["price_basis"] == "qfq"


def test_rejects_raw_qfq_date_mismatch_without_publishing(tmp_path: Path) -> None:
    gateway = FakeGateway()
    gateway.qfq.pop()
    builder = _builder(tmp_path, gateway)

    with pytest.raises(VisualDailyInputError, match="RAW_QFQ_DATE_SET_MISMATCH"):
        builder.prepare(TARGET, new_continuous_state(SimulationConfig.default()))

    assert not (builder.input_root / TARGET.isoformat()).exists()


def test_rejects_before_close_and_stale_market_date(tmp_path: Path) -> None:
    before_close = FakeGateway(
        fetched_at=datetime.fromisoformat("2026-08-21T14:59:00+08:00")
    )
    builder = _builder(tmp_path, before_close)
    state = new_continuous_state(SimulationConfig.default())

    with pytest.raises(VisualDailyInputError, match="MARKET_NOT_CLOSED"):
        builder.prepare(TARGET, state)
    assert before_close.calls == 0

    stale = FakeGateway()
    stale.raw.pop()
    stale.qfq.pop()
    with pytest.raises(VisualDailyInputError, match="LATEST_TRADE_DATE_MISMATCH"):
        _builder(tmp_path / "stale", stale).prepare(TARGET, state)


def test_bundle_tamper_is_rejected(tmp_path: Path) -> None:
    builder = _builder(tmp_path, FakeGateway())
    builder.prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    facts_path = builder.input_root / TARGET.isoformat() / "facts.json"
    payload = json.loads(facts_path.read_text())
    payload["daily"]["close"] += 1
    facts_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(VisualDailyInputError, match="ARTIFACT_HASH_MISMATCH"):
        load_validated_daily_bundle(builder.input_root, TARGET)


def test_resigned_forged_valuation_bar_is_rejected(tmp_path: Path) -> None:
    builder = _builder(tmp_path, FakeGateway())
    bundle = builder.prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    day_root = builder.input_root / TARGET.isoformat()
    values = bundle.day_input.model_dump(exclude={"input_identity"})
    values["signal"] = bundle.day_input.signal
    values["execution_fixture"] = bundle.day_input.execution_fixture
    values["valuation_bar"] = bundle.day_input.valuation_bar.model_copy(
        update={"close": Decimal("999.99")}
    )
    forged = ContinuousDayInput.create(**values)
    raw = canonical_option_c_bytes(forged)
    (day_root / "input.json").write_bytes(raw)
    _resign_manifest(day_root, "input.json", raw)

    with pytest.raises(
        VisualDailyInputError,
        match="VALIDATED_DAILY_DERIVATION_MISMATCH",
    ):
        load_validated_daily_bundle(builder.input_root, TARGET)


def test_manifest_cannot_omit_an_artifact_hash(tmp_path: Path) -> None:
    builder = _builder(tmp_path, FakeGateway())
    builder.prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    manifest_path = builder.input_root / TARGET.isoformat() / "manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["artifact_sha256"].pop("projection.json")
    provisional = ValidatedDailyManifest.model_validate(payload, strict=False)
    payload["manifest_identity"] = provisional.computed_identity()
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(VisualDailyInputError, match="MANIFEST_ARTIFACT_SET_INVALID"):
        load_validated_daily_bundle(builder.input_root, TARGET)


def test_projection_rebinding_cannot_bypass_input_identity(tmp_path: Path) -> None:
    builder = _builder(tmp_path, FakeGateway())
    builder.prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    day_root = builder.input_root / TARGET.isoformat()
    projection_path = day_root / "projection.json"
    projection = json.loads(projection_path.read_text())
    projection["safe_facts"]["daily"]["close"] += 1
    projection.pop("projection_sha256")
    projection["projection_sha256"] = hashlib.sha256(
        canonical_json_bytes(projection)
    ).hexdigest()
    projection_raw = canonical_json_bytes(projection)
    projection_path.write_bytes(projection_raw)

    manifest_path = day_root / "manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["artifact_sha256"]["projection.json"] = hashlib.sha256(
        projection_raw
    ).hexdigest()
    provisional = ValidatedDailyManifest.model_validate(payload, strict=False)
    payload["manifest_identity"] = provisional.computed_identity()
    manifest_path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(VisualDailyInputError, match="PROJECTION_BINDING_MISMATCH"):
        load_validated_daily_bundle(builder.input_root, TARGET)


def test_pending_action_uses_next_trading_day_open_and_calendar(tmp_path: Path) -> None:
    prior_target = date(2026, 8, 20)
    prior_gateway = FakeGateway(
        bullish=True,
        fetched_at=datetime.fromisoformat("2026-08-20T16:20:00+08:00"),
        target_date=prior_target,
        trading_dates=[date(2026, 8, 19), prior_target, TARGET],
    )
    prior = _builder(tmp_path / "prior", prior_gateway).prepare(
        prior_target,
        new_continuous_state(SimulationConfig.default()),
    )
    from app.services.phase2_option_c_continuous import run_continuous_day

    state_with_pending = run_continuous_day(
        new_continuous_state(SimulationConfig.default()), prior.day_input
    ).state
    assert state_with_pending.pending_action is not None

    current_gateway = FakeGateway(
        trading_dates=[prior_target, TARGET, date(2026, 8, 24)]
    )
    current = _builder(tmp_path / "current", current_gateway).prepare(
        TARGET,
        state_with_pending,
    )
    fixture = current.day_input.execution_fixture
    assert fixture is not None
    assert fixture.source_fixture == "VALIDATED_DAILY_INPUT"
    assert fixture.trade_dates == [prior_target, TARGET, date(2026, 8, 24)]
    assert fixture.bars[0].trade_date == TARGET
    gateway_open = current.facts["daily"]["open"]
    assert fixture.bars[0].open == Decimal(str(gateway_open)).quantize(Decimal("0.01"))
    assert gateway_open > 0


def test_open_position_rejects_material_adjustment_factor_change(tmp_path: Path) -> None:
    from app.services.phase2_option_c_continuous import run_continuous_day

    day_one = date(2026, 8, 19)
    day_two = date(2026, 8, 20)
    first = _builder(
        tmp_path / "day-one",
        FakeGateway(
            bullish=True,
            fetched_at=datetime.fromisoformat("2026-08-19T16:20:00+08:00"),
            target_date=day_one,
            trading_dates=[date(2026, 8, 18), day_one, day_two],
        ),
    ).prepare(day_one, new_continuous_state(SimulationConfig.default()))
    state = run_continuous_day(
        new_continuous_state(SimulationConfig.default()), first.day_input
    ).state
    second = _builder(
        tmp_path / "day-two",
        FakeGateway(
            fetched_at=datetime.fromisoformat("2026-08-20T16:20:00+08:00"),
            target_date=day_two,
            trading_dates=[day_one, day_two, TARGET],
        ),
    ).prepare(day_two, state)
    state = run_continuous_day(state, second.day_input).state
    assert state.account.lots

    corporate_action = FakeGateway(
        trading_dates=[day_two, TARGET, date(2026, 8, 24)]
    )
    for row in corporate_action.qfq[:-1]:
        for field in ("open", "high", "low", "close"):
            row[field] *= 0.75

    with pytest.raises(
        VisualDailyInputError,
        match="CORPORATE_ACTION_ACCOUNTING_UNSUPPORTED",
    ):
        _builder(tmp_path / "day-three", corporate_action).prepare(TARGET, state)


def test_open_position_rejects_adjustment_change_across_a_missed_day(
    tmp_path: Path,
) -> None:
    from app.services.phase2_option_c_continuous import run_continuous_day

    day_one = date(2026, 8, 19)
    day_two = date(2026, 8, 20)
    state = new_continuous_state(SimulationConfig.default())
    first_gateway = FakeGateway(
        bullish=True,
        fetched_at=datetime.fromisoformat("2026-08-19T16:20:00+08:00"),
        target_date=day_one,
        trading_dates=[date(2026, 8, 18), day_one, day_two],
    )
    first = _builder(tmp_path / "first", first_gateway).prepare(day_one, state)
    state = run_continuous_day(state, first.day_input).state
    second_gateway = FakeGateway(
        fetched_at=datetime.fromisoformat("2026-08-20T16:20:00+08:00"),
        target_date=day_two,
        trading_dates=[day_one, day_two, date(2026, 8, 21)],
    )
    second = _builder(tmp_path / "second", second_gateway).prepare(day_two, state)
    state = run_continuous_day(state, second.day_input).state
    assert state.account.lots

    target = date(2026, 8, 24)
    after_missed_event = FakeGateway(
        fetched_at=datetime.fromisoformat("2026-08-24T16:20:00+08:00"),
        target_date=target,
        trading_dates=[date(2026, 8, 21), target, date(2026, 8, 25)],
    )
    for row in after_missed_event.qfq:
        if row["trade_date"] <= day_two.isoformat():
            for field in ("open", "high", "low", "close"):
                row[field] *= 0.75

    with pytest.raises(
        VisualDailyInputError,
        match="CORPORATE_ACTION_ACCOUNTING_UNSUPPORTED",
    ):
        _builder(tmp_path / "after-missed", after_missed_event).prepare(target, state)


def test_concurrent_first_prepare_is_rejected_before_second_market_call(
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingGateway(FakeGateway):
        def fetch(self, symbol: str, requested_date: date) -> DailyMarketSnapshot:
            started.set()
            assert release.wait(timeout=5)
            return super().fetch(symbol, requested_date)

    first_gateway = BlockingGateway()
    second_gateway = FakeGateway()
    first = _builder(tmp_path, first_gateway)
    second = _builder(tmp_path, second_gateway)
    state = new_continuous_state(SimulationConfig.default())
    failures: list[BaseException] = []

    def run_first() -> None:
        try:
            first.prepare(TARGET, state)
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    worker = threading.Thread(target=run_first)
    worker.start()
    assert started.wait(timeout=5)
    try:
        with pytest.raises(VisualDailyInputError, match="INPUT_PREPARATION_ALREADY_LOCKED"):
            second.prepare(TARGET, state)
    finally:
        release.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert failures == []
    assert first_gateway.calls == 1
    assert second_gateway.calls == 0


def test_production_gateway_uses_only_daily_and_calendar_jobs_in_shanghai_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.plugins.stocksdk import bridge

    jobs: list[dict] = []

    def fake_run_job(job: dict, *, timeout: int) -> dict:
        jobs.append({**job, "timeout": timeout})
        if job["op"] == "calendar":
            return {"rows": ["2026-08-20", "2026-08-21", "2026-08-24"]}
        return {
            "rows": {
                "000403.SZ": [
                    {
                        "date": "2026-08-21",
                        "open": 10.0,
                        "high": 10.2,
                        "low": 9.9,
                        "close": 10.1,
                        "volume": 1000,
                        "amount": 10000,
                    }
                ]
            }
        }

    monkeypatch.setattr(bridge, "run_job", fake_run_job)
    gateway = StockSdkTencentDailyGateway(
        now=lambda: datetime.fromisoformat("2026-08-21T08:20:00+00:00")
    )

    snapshot = gateway.fetch("000403.SZ", date(2026, 8, 21))

    assert snapshot.fetched_at.isoformat() == "2026-08-21T16:20:00+08:00"
    assert [(job["op"], job.get("adjust")) for job in jobs] == [
        ("visual_daily", "none"),
        ("visual_daily", "qfq"),
        ("calendar", None),
    ]
    assert all(job.get("symbols", ["000403.SZ"]) == ["000403.SZ"] for job in jobs)
    assert snapshot.request_count == 3
