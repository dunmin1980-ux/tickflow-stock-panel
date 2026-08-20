from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.schemas.phase2_option_c import (
    ContinuousDayInput,
    ResearchSignal,
    SimulationConfig,
    ValuationBar,
    canonical_option_c_bytes,
)
from app.services.phase2_option_c_continuous import (
    new_continuous_state,
    run_continuous_day,
)
from app.services.phase2_option_c_state import (
    OptionCStateError,
    OptionCStateStore,
)


def _hold_input(
    trade_date: date = date(2026, 8, 3),
    previous_trade_date: date = date(2026, 7, 31),
    signal_character: str = "1",
) -> ContinuousDayInput:
    decision_at = datetime.fromisoformat(f"{trade_date.isoformat()}T15:15:00+08:00")
    signal = ResearchSignal(
        signal_schema_version=1,
        signal_id=signal_character * 64,
        interpretation_id=chr(ord(signal_character) + 1) * 64,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        symbol="000403.SZ",
        trade_date=trade_date,
        code="MIXED_OBSERVATION",
        reason_refs=["fixture-hold"],
        facts_sha256="c" * 64,
        claims_sha256="d" * 64,
        fixture_identity="9" * 64,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    valuation = ValuationBar(
        valuation_bar_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        scenario_fixture_identity="9" * 64,
        symbol="000403.SZ",
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        available_at=datetime.fromisoformat(f"{trade_date.isoformat()}T15:01:00+08:00"),
        timezone="Asia/Shanghai",
        close=Decimal("10.00"),
        price_basis="raw",
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )
    return ContinuousDayInput.create(
        continuous_day_input_schema_version=1,
        source_fixture="DETERMINISTIC_TEST_FIXTURE",
        symbol="000403.SZ",
        trade_date=trade_date,
        decision_at=decision_at,
        facts_available_at=decision_at,
        projection_available_at=decision_at,
        signal_generated_at=decision_at,
        facts_sha256=signal.facts_sha256,
        projection_sha256="a" * 64,
        claims_sha256=signal.claims_sha256,
        fixture_identity=signal.fixture_identity,
        signal=signal,
        execution_fixture=None,
        valuation_bar=valuation,
        simulation_only="SIMULATION ONLY",
        can_publish=False,
        trading_advice=False,
    )


def _published_store(tmp_path: Path) -> tuple[OptionCStateStore, ContinuousDayInput]:
    store = OptionCStateStore(tmp_path / "paper-state")
    day_input = _hold_input()
    result = run_continuous_day(new_continuous_state(SimulationConfig.default()), day_input)
    store.publish_day(
        day_input,
        result,
        daily_json=b'{"simulation_only":"SIMULATION ONLY"}\n',
        daily_markdown=b"SIMULATION ONLY\n",
    )
    return store, day_input


def test_state_store_publishes_immutable_day_and_recovers_without_pointer(
    tmp_path: Path,
) -> None:
    store, day_input = _published_store(tmp_path)
    day_root = store.root / "days" / "2026-08-03"

    assert store.load_or_initialize(SimulationConfig.default()).last_completed_trade_date == (
        date(2026, 8, 3)
    )
    assert {path.name for path in day_root.iterdir()} == {
        "account_state.json",
        "chenquant_daily.json",
        "chenquant_daily.md",
        "continuous_state.json",
        "decision_ledger.json",
        "equity_history.json",
        "input.json",
        "input_identities.json",
        "position_lots.json",
        "run_receipt.json",
        "trade_ledger.json",
    }
    assert all(path.is_file() and not path.is_symlink() for path in day_root.iterdir())
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in day_root.iterdir())
    assert day_input.input_identity in (day_root / "input.json").read_text()

    (store.root / "current_state.json").unlink()
    recovered = store.load_or_initialize(SimulationConfig.default())
    assert recovered.last_completed_trade_date == date(2026, 8, 3)
    assert recovered.completed_input_identities == [day_input.input_identity]


def test_same_day_same_identity_is_idempotent_and_conflict_fails(
    tmp_path: Path,
) -> None:
    store, day_input = _published_store(tmp_path)
    before = {
        path.relative_to(store.root).as_posix(): path.read_bytes()
        for path in store.root.rglob("*")
        if path.is_file()
    }

    existing = store.find_published_day(day_input.trade_date, day_input.input_identity)
    assert existing is not None
    assert existing.status == "DAY_ALREADY_PUBLISHED"
    assert existing.state.last_completed_trade_date == day_input.trade_date

    after = {
        path.relative_to(store.root).as_posix(): path.read_bytes()
        for path in store.root.rglob("*")
        if path.is_file()
    }
    assert after == before

    with pytest.raises(OptionCStateError, match="published_day_identity_conflict"):
        store.find_published_day(day_input.trade_date, "0" * 64)


def test_atomic_publication_failure_preserves_previous_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.phase2_option_c_state as state_store

    store, _day_input = _published_store(tmp_path)
    current_before = (store.root / "current_state.json").read_bytes()
    day_before = {
        path.name: path.read_bytes() for path in (store.root / "days" / "2026-08-03").iterdir()
    }

    def fail_publish(_staging: Path, _destination: Path) -> None:
        raise OSError("injected_state_publish_failure")

    monkeypatch.setattr(state_store, "atomic_publish_directory", fail_publish)
    next_input = _hold_input(
        date(2026, 8, 4),
        date(2026, 8, 3),
        "3",
    )
    result = run_continuous_day(
        store.load_or_initialize(SimulationConfig.default()),
        next_input,
    )
    with pytest.raises(OSError, match="injected_state_publish_failure"):
        store.publish_day(
            next_input,
            result,
            daily_json=b'{"simulation_only":"SIMULATION ONLY","different":true}\n',
            daily_markdown=b"SIMULATION ONLY\ndifferent\n",
        )

    assert (store.root / "current_state.json").read_bytes() == current_before
    assert {
        path.name: path.read_bytes() for path in (store.root / "days" / "2026-08-03").iterdir()
    } == day_before


def test_state_store_rejects_symlink_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "paper-state"
    linked.symlink_to(target, target_is_directory=True)

    with pytest.raises(OptionCStateError, match="state_root_must_not_be_symlink"):
        OptionCStateStore(linked)


def test_current_state_is_canonical_continuous_state(tmp_path: Path) -> None:
    store, _ = _published_store(tmp_path)
    loaded = store.load_or_initialize(SimulationConfig.default())

    assert (store.root / "current_state.json").read_bytes() == canonical_option_c_bytes(loaded)


def test_lock_recovery_removes_stale_staging_directory(tmp_path: Path) -> None:
    store, _ = _published_store(tmp_path)
    stale = store.days_root / ".2026-08-04.staging-dead"
    stale.mkdir(mode=0o700)
    (stale / "partial.json").write_text("partial\n", encoding="utf-8")

    with store.locked():
        recovered = store.load_or_initialize(SimulationConfig.default())

    assert recovered.last_completed_trade_date == date(2026, 8, 3)
    assert not stale.exists()


def test_published_day_rejects_receipt_trade_date_mismatch(tmp_path: Path) -> None:
    store, day_input = _published_store(tmp_path)
    receipt_path = store.days_root / "2026-08-03" / "run_receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["trade_date"] = "2026-08-04"
    receipt_path.write_bytes(canonical_option_c_bytes(receipt))

    with pytest.raises(OptionCStateError, match="published_day_identity_invalid"):
        store.find_published_day(day_input.trade_date, day_input.input_identity)


def test_publish_rejects_non_append_only_state_chain(tmp_path: Path) -> None:
    store, _ = _published_store(tmp_path)
    next_input = _hold_input(
        date(2026, 8, 4),
        date(2026, 8, 3),
        "3",
    )
    disconnected = run_continuous_day(
        new_continuous_state(SimulationConfig.default()),
        next_input,
    )

    with pytest.raises(OptionCStateError, match="state_history_not_append_only"):
        store.publish_day(
            next_input,
            disconnected,
            daily_json=b'{"simulation_only":"SIMULATION ONLY"}\n',
            daily_markdown=b"SIMULATION ONLY\n",
        )
