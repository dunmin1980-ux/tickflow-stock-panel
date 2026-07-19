import hashlib
import json
import stat
from datetime import date, datetime, timedelta

import pytest

from app.services.gold_external_guard import (
    DisabledGoldNotifier,
    GoldExternalSendDenied,
)
from app.services.gold_observation import GoldObservationService
from app.services.gold_shadow_store import GoldShadowStore


@pytest.fixture
def service(tmp_path):
    store = GoldShadowStore(tmp_path)
    notifier = DisabledGoldNotifier(store.root)
    return GoldObservationService(store, notifier)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _commit_run(
    store: GoldShadowStore,
    market_date: date,
    *,
    passing: bool = True,
    revision: int = 1,
) -> dict:
    date_text = market_date.isoformat()
    return store.commit_comparison_run(
        {
            "schema_version": 1,
            "run_id": _digest(f"run:{date_text}:{revision}"),
            "market_date": date_text,
            "legacy_import_id": _digest(f"import:{date_text}:{revision}"),
            "legacy_digest": _digest(f"legacy:{date_text}:{revision}"),
            "shadow_digest": _digest(f"shadow:{date_text}:{revision}"),
            "comparator_version": "1",
            "legacy_sample_count": 1,
            "shadow_sample_count": 1,
        },
        [
            {
                "schema_version": 1,
                "type": "summary",
                "stage_a": {"thresholds_pass": passing},
            }
        ],
    )


def _trading_dates(count: int) -> list[date]:
    result = []
    current = date(2026, 7, 1)
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def _seed_valid_days(service: GoldObservationService, count: int) -> list[dict]:
    runs = []
    for market_date in _trading_dates(count):
        run = _commit_run(service.store, market_date)
        service.review_day(
            market_date,
            run["run_id"],
            verified=True,
            note="full session reviewed",
        )
        runs.append(run)
    return runs


def test_nine_valid_days_remain_collecting(service):
    _seed_valid_days(service, count=9)

    result = service.status()

    assert result["status"] == "collecting"
    assert result["complete_trading_days"] == 9


def test_ten_days_and_restart_review_are_review_eligible(service):
    _seed_valid_days(service, count=10)
    service.review_restart(verified=True, note="snapshot and dedupe restored")

    result = service.status()

    assert result["status"] == "review_eligible"
    assert result["required_complete_trading_days"] == 10
    assert result["complete_trading_days"] == 10
    assert "notifications_enabled" not in result
    assert not any(name.startswith(("activate", "enable")) for name in dir(service))


def test_external_attempt_forces_failed(service):
    _seed_valid_days(service, count=10)
    service.review_restart(verified=True, note="restart reviewed")

    with pytest.raises(GoldExternalSendDenied):
        service.notifier.send("telegram", {})

    result = service.status()
    assert result["status"] == "failed"
    assert result["external_send_count"] == 1


def test_duplicate_day_reviews_count_once(service):
    runs = _seed_valid_days(service, count=9)
    first = runs[0]
    service.review_day(
        date.fromisoformat(first["market_date"]),
        first["run_id"],
        verified=True,
        note="second full-day review",
    )
    service.review_restart(verified=True, note="restart reviewed")

    result = service.status()

    assert result["status"] == "collecting"
    assert result["complete_trading_days"] == 9


def test_failed_tolerance_forces_failed_even_with_manual_review(service):
    _seed_valid_days(service, count=9)
    market_date = _trading_dates(10)[-1]
    run = _commit_run(service.store, market_date, passing=False)
    service.review_day(
        market_date,
        run["run_id"],
        verified=True,
        note="manual review cannot replace automatic checks",
    )
    service.review_restart(verified=True, note="restart reviewed")

    result = service.status()

    assert result["status"] == "failed"
    assert result["complete_trading_days"] == 9


def test_missing_manual_full_day_review_remains_collecting(service):
    dates = _trading_dates(10)
    for market_date in dates:
        run = _commit_run(service.store, market_date)
        if market_date != dates[-1]:
            service.review_day(
                market_date,
                run["run_id"],
                verified=True,
                note="full session reviewed",
            )
    service.review_restart(verified=True, note="restart reviewed")

    result = service.status()

    assert result["status"] == "collecting"
    assert result["complete_trading_days"] == 9


def test_unresolved_supersede_chain_fails_closed(service):
    market_date = _trading_dates(1)[0]
    _commit_run(service.store, market_date, revision=1)
    latest = _commit_run(service.store, market_date, revision=2)
    service.review_day(
        market_date,
        latest["run_id"],
        verified=True,
        note="full session reviewed",
    )
    index_path = service.store.root / "comparison_index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    rows[-1]["supersedes_run_id"] = "f" * 64
    index_path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    result = service.status()

    assert result["status"] == "failed"
    assert result["complete_trading_days"] == 0


def test_missing_restart_review_remains_collecting(service):
    _seed_valid_days(service, count=10)

    result = service.status()

    assert result["status"] == "collecting"
    assert result["complete_trading_days"] == 10


def test_superseded_review_does_not_count_for_latest_run(service):
    market_date = _trading_dates(1)[0]
    first = _commit_run(service.store, market_date, passing=False, revision=1)
    service.review_day(
        market_date,
        first["run_id"],
        verified=True,
        note="old full-day review",
    )
    latest = _commit_run(service.store, market_date, passing=True, revision=2)

    before = service.status()
    service.review_day(
        market_date,
        latest["run_id"],
        verified=True,
        note="latest full-day review",
    )
    after = service.status()

    assert before["status"] == "collecting"
    assert before["complete_trading_days"] == 0
    assert after["status"] == "collecting"
    assert after["complete_trading_days"] == 1


def test_day_review_requires_existing_canonical_run(service):
    market_date = _trading_dates(1)[0]
    first = _commit_run(service.store, market_date, revision=1)
    _commit_run(service.store, market_date, revision=2)

    with pytest.raises(ValueError, match="canonical"):
        service.review_day(
            market_date,
            first["run_id"],
            verified=True,
            note="superseded run",
        )


@pytest.mark.parametrize("review_type", ["day", "restart"])
@pytest.mark.parametrize("note", ["", "   "])
def test_verified_reviews_require_non_empty_notes(service, review_type, note):
    if review_type == "day":
        market_date = _trading_dates(1)[0]
        run = _commit_run(service.store, market_date)
        review = lambda: service.review_day(  # noqa: E731 - compact parameterized call
            market_date, run["run_id"], verified=True, note=note
        )
    else:
        review = lambda: service.review_restart(  # noqa: E731 - compact parameterized call
            verified=True, note=note
        )

    with pytest.raises(ValueError, match="note"):
        review()


def test_unverified_review_does_not_count(service):
    market_date = _trading_dates(1)[0]
    run = _commit_run(service.store, market_date)
    service.review_day(
        market_date,
        run["run_id"],
        verified=False,
        note="review rejected",
    )

    assert service.status()["complete_trading_days"] == 0


def test_reviews_survive_restart_and_use_private_storage(service):
    market_date = _trading_dates(1)[0]
    run = _commit_run(service.store, market_date)
    service.review_day(
        market_date,
        run["run_id"],
        verified=True,
        note="full session reviewed",
    )
    service.review_restart(verified=True, note="restart reviewed")
    path = service.store.root / "observation_reviews.jsonl"

    restarted_store = GoldShadowStore(service.store.root.parents[1])
    restarted = GoldObservationService(
        restarted_store,
        DisabledGoldNotifier(restarted_store.root),
    )

    assert restarted.status()["complete_trading_days"] == 1
    assert stat.S_IMODE(service.store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_corrupted_review_state_fails_closed(service):
    service.store.root.mkdir(mode=0o700, parents=True)
    (service.store.root / "observation_reviews.jsonl").write_text(
        '{"schema_version":1,"review_type":"restart"}\n',
        encoding="utf-8",
    )

    result = service.status()

    assert result["status"] == "failed"


def test_corrupted_comparison_run_fails_closed(service):
    market_date = _trading_dates(1)[0]
    run = _commit_run(service.store, market_date)
    path = service.store.root / "comparison_runs" / f"{run['run_id']}.jsonl"
    path.write_text('{"schema_version":1,"type":"summary"}\n', encoding="utf-8")

    result = service.status()

    assert result["status"] == "failed"


def test_corrupted_external_attempt_state_fails_closed(service):
    service.store.root.mkdir(mode=0o700, parents=True)
    (service.store.root / "external_send_attempts.jsonl").write_text(
        '{"schema_version":1}\n',
        encoding="utf-8",
    )

    result = service.status()

    assert result["status"] == "failed"
    assert result["external_send_count"] is None


def test_all_status_values_are_from_the_closed_status_set(service):
    assert service.status()["status"] in {"collecting", "failed", "review_eligible"}


def test_review_timestamp_is_utc_and_schema_is_minimal(service):
    service.review_restart(verified=True, note="restart reviewed")
    row = json.loads(
        (service.store.root / "observation_reviews.jsonl").read_text(encoding="utf-8")
    )

    assert set(row) == {
        "schema_version",
        "review_type",
        "verified",
        "note",
        "reviewed_at",
    }
    assert datetime.fromisoformat(row["reviewed_at"]).utcoffset() == timedelta(0)
