import hashlib
import io
import json
import os
import stat
from datetime import date, datetime, timedelta

import pytest

from app.services.gold_external_guard import (
    DisabledGoldNotifier,
    GoldExternalSendDenied,
)
from app.services.gold_observation import GoldObservationService
from app.services.gold_shadow_store import GoldShadowStorageReadError, GoldShadowStore


def _calendar_payload(*, holidays=(), covered_years=(2026,)) -> dict[str, object]:
    return {
        "schema_version": 1,
        "timezone": "Asia/Shanghai",
        "covered_years": list(covered_years),
        "holidays": list(holidays),
    }


def _write_calendar(store: GoldShadowStore, *, holidays=(), covered_years=(2026,)) -> None:
    (store.root / "holidays.json").write_text(
        json.dumps(
            _calendar_payload(holidays=holidays, covered_years=covered_years)
        ),
        encoding="utf-8",
    )


@pytest.fixture
def service(tmp_path):
    store = GoldShadowStore(tmp_path)
    store.root.mkdir(mode=0o700, parents=True)
    _write_calendar(store)
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
    legacy_excluded: int = 0,
    shadow_excluded: int = 0,
) -> dict:
    date_text = market_date.isoformat()
    return store.commit_comparison_run(
        {
            "schema_version": 2,
            "run_id": _digest(f"run:{date_text}:{revision}"),
            "market_date": date_text,
            "legacy_import_id": _digest(f"import:{date_text}:{revision}"),
            "legacy_digest": _digest(f"legacy:{date_text}:{revision}"),
            "shadow_digest": _digest(f"shadow:{date_text}:{revision}"),
            "comparator_version": "2",
            "legacy_sample_count": 1,
            "shadow_sample_count": 1,
            "legacy_excluded_out_of_session_count": legacy_excluded,
            "shadow_excluded_out_of_session_count": shadow_excluded,
        },
        [
            {
                "schema_version": 1,
                "type": "summary",
                "quality": {
                    "legacy_excluded_out_of_session_count": legacy_excluded,
                    "shadow_excluded_out_of_session_count": shadow_excluded,
                },
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


def _restart_review(note: str = "restart reviewed") -> dict:
    return {
        "schema_version": 1,
        "review_type": "restart",
        "verified": True,
        "note": note,
        "reviewed_at": "2026-07-20T12:00:00+00:00",
    }


def _day_review(market_date: date, run_id: str) -> dict:
    return {
        "schema_version": 1,
        "review_type": "day",
        "market_date": market_date.isoformat(),
        "run_id": run_id,
        "verified": True,
        "note": "persisted full-day review",
        "reviewed_at": "2026-07-20T12:00:00+00:00",
    }


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


def test_status_exposes_sanitized_persisted_review_state(service):
    dates = _trading_dates(3)
    verified_run = _commit_run(service.store, dates[0], passing=True)
    rejected_run = _commit_run(service.store, dates[1], passing=False)
    pending_run = _commit_run(service.store, dates[2], passing=True)
    service.review_day(
        dates[0], verified_run["run_id"], verified=True, note="complete window verified"
    )
    service.review_day(
        dates[1], rejected_run["run_id"], verified=False, note="window incomplete"
    )
    service.review_restart(verified=False, note="restart recovery incomplete")

    result = service.status()

    assert result["canonical_days"] == [
        {
            "market_date": dates[2].isoformat(),
            "run_id": pending_run["run_id"],
            "automatic_passed": True,
            "review_recorded": False,
            "review_verified": None,
        },
        {
            "market_date": dates[1].isoformat(),
            "run_id": rejected_run["run_id"],
            "automatic_passed": False,
            "review_recorded": True,
            "review_verified": False,
        },
        {
            "market_date": dates[0].isoformat(),
            "run_id": verified_run["run_id"],
            "automatic_passed": True,
            "review_recorded": True,
            "review_verified": True,
        },
    ]
    assert result["restart_review"] == {"recorded": True, "verified": False}
    serialized = json.dumps(result)
    assert "complete window verified" not in serialized
    assert "restart recovery incomplete" not in serialized
    assert "notifications_enabled" not in result


def test_corrupt_review_storage_reports_unknown_persisted_review_state(service):
    market_date = _trading_dates(1)[0]
    run = _commit_run(service.store, market_date)
    (service.store.root / "observation_reviews.jsonl").write_text(
        '{"schema_version":1,"review_type":"restart"}\n',
        encoding="utf-8",
    )

    result = service.status()

    assert result["canonical_days"] == [
        {
            "market_date": market_date.isoformat(),
            "run_id": run["run_id"],
            "automatic_passed": True,
            "review_recorded": None,
            "review_verified": None,
        }
    ]
    assert result["restart_review"] == {"recorded": None, "verified": None}


def test_weekend_comparison_runs_cannot_be_reviewed_or_counted(service):
    first_saturday = date(2026, 7, 4)
    for offset in range(10):
        market_date = first_saturday + timedelta(days=offset * 7)
        run = _commit_run(service.store, market_date)
        with pytest.raises(ValueError, match="trading day"):
            service.review_day(
                market_date,
                run["run_id"],
                verified=True,
                note="weekend must not count",
            )
        service.store.append_observation_review(_day_review(market_date, run["run_id"]))
    service.review_restart(verified=True, note="restart reviewed")

    result = service.status()

    assert result["status"] == "collecting"
    assert result["complete_trading_days"] == 0


def test_configured_holiday_cannot_be_reviewed_or_counted(service):
    market_date = _trading_dates(1)[0]
    _write_calendar(service.store, holidays=(market_date.isoformat(),))
    run = _commit_run(service.store, market_date)

    with pytest.raises(ValueError, match="trading day"):
        service.review_day(
            market_date,
            run["run_id"],
            verified=True,
            note="configured holiday must not count",
        )
    service.store.append_observation_review(_day_review(market_date, run["run_id"]))

    result = service.status()
    assert result["status"] == "collecting"
    assert result["complete_trading_days"] == 0


@pytest.mark.parametrize(
    "calendar_contents",
    [None, "{not-json", '["not-a-date"]', '{"holidays": []}'],
)
def test_unavailable_or_corrupt_calendar_fails_closed(service, calendar_contents):
    path = service.store.root / "holidays.json"
    if calendar_contents is None:
        path.unlink()
    else:
        path.write_text(calendar_contents, encoding="utf-8")

    result = service.status()

    assert result["status"] == "failed"
    assert "trading_calendar_unavailable" in result["reasons"]


def test_symlinked_calendar_fails_closed_without_reading_outside(service, tmp_path):
    outside = tmp_path / "outside-holidays.json"
    outside.write_text("[]\n", encoding="utf-8")
    calendar = service.store.root / "holidays.json"
    calendar.unlink()
    calendar.symlink_to(outside)

    result = service.status()

    assert result["status"] == "failed"
    assert "trading_calendar_unavailable" in result["reasons"]


def test_unreadable_calendar_fails_closed(service, monkeypatch):
    from app.services import gold_shadow_store

    real_open = gold_shadow_store.os.open

    def fail_calendar_open(path, flags, mode=0o777, *, dir_fd=None):
        if os.fspath(path) == "holidays.json":
            raise PermissionError("injected unreadable calendar")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(gold_shadow_store.os, "open", fail_calendar_open)

    result = service.status()

    assert result["status"] == "failed"
    assert "trading_calendar_unavailable" in result["reasons"]


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


def test_out_of_session_comparison_evidence_forces_gate_failure(service):
    market_date = _trading_dates(1)[0]
    run = _commit_run(service.store, market_date, legacy_excluded=1)
    service.review_day(
        market_date,
        run["run_id"],
        verified=True,
        note="review cannot override excluded evidence",
    )
    service.review_restart(verified=True, note="restart reviewed")

    result = service.status()

    assert result["status"] == "failed"
    assert f"comparison_out_of_session_exclusions:{run['run_id']}" in result["reasons"]
    assert result["complete_trading_days"] == 0


def test_legacy_comparison_without_quality_metadata_forces_gate_failure(service):
    market_date = _trading_dates(1)[0]
    run_id = _digest("legacy-quality-run")
    run = service.store.commit_comparison_run(
        {
            "schema_version": 1,
            "run_id": run_id,
            "market_date": market_date.isoformat(),
            "legacy_import_id": _digest("legacy-quality-import"),
            "legacy_digest": _digest("legacy-quality-legacy"),
            "shadow_digest": _digest("legacy-quality-shadow"),
            "comparator_version": "1",
            "legacy_sample_count": 1,
            "shadow_sample_count": 1,
        },
        [
            {
                "schema_version": 1,
                "type": "summary",
                "stage_a": {"thresholds_pass": True},
            }
        ],
    )

    result = service.status()

    assert result["status"] == "failed"
    assert f"comparison_quality_metadata_missing:{run['run_id']}" in result["reasons"]
    assert result["complete_trading_days"] == 0


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
    service.store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (service.store.root / "observation_reviews.jsonl").write_text(
        '{"schema_version":1,"review_type":"restart"}\n',
        encoding="utf-8",
    )

    result = service.status()

    assert result["status"] == "failed"


def test_symlinked_review_file_is_rejected_without_outside_read_or_write(service, tmp_path):
    outside = tmp_path / "outside-reviews.jsonl"
    outside.write_text(json.dumps(_restart_review("outside")) + "\n", encoding="utf-8")
    original = outside.read_bytes()
    path = service.store.root / "observation_reviews.jsonl"
    path.symlink_to(outside)

    with pytest.raises(GoldShadowStorageReadError):
        service.store.list_observation_reviews()
    with pytest.raises((GoldShadowStorageReadError, OSError)):
        service.review_restart(verified=True, note="must remain local")

    assert outside.read_bytes() == original


def test_dangling_review_file_symlink_is_rejected_for_read_and_write(service, tmp_path):
    path = service.store.root / "observation_reviews.jsonl"
    outside = tmp_path / "missing-reviews.jsonl"
    path.symlink_to(outside)

    with pytest.raises(GoldShadowStorageReadError):
        service.store.list_observation_reviews()
    with pytest.raises((GoldShadowStorageReadError, OSError)):
        service.review_restart(verified=True, note="must remain local")

    assert not outside.exists()


def test_symlinked_review_root_is_rejected_without_outside_read_or_write(tmp_path):
    store = GoldShadowStore(tmp_path)
    outside = tmp_path / "outside-root"
    outside.mkdir()
    outside_path = outside / "observation_reviews.jsonl"
    outside_path.write_text(json.dumps(_restart_review("outside")) + "\n", encoding="utf-8")
    original = outside_path.read_bytes()
    store.root.parent.mkdir(parents=True)
    store.root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(GoldShadowStorageReadError):
        store.list_observation_reviews()
    with pytest.raises((GoldShadowStorageReadError, OSError)):
        store.append_observation_review(_restart_review("must remain local"))

    assert outside_path.read_bytes() == original


def test_dangling_review_root_is_rejected_without_creating_target(tmp_path):
    store = GoldShadowStore(tmp_path)
    missing = tmp_path / "missing-root"
    store.root.parent.mkdir(parents=True)
    store.root.symlink_to(missing, target_is_directory=True)

    with pytest.raises(GoldShadowStorageReadError):
        store.list_observation_reviews()
    with pytest.raises(GoldShadowStorageReadError):
        store.append_observation_review(_restart_review())

    assert not missing.exists()


def test_root_swap_before_review_write_cannot_redirect_append(tmp_path, monkeypatch):
    from app.services import gold_shadow_store

    store = GoldShadowStore(tmp_path)
    store.root.mkdir(mode=0o700, parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    detached = tmp_path / "detached"
    real_open = gold_shadow_store.os.open
    swapped = False

    def swap_before_write_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if (
            not swapped
            and flags & (os.O_WRONLY | os.O_RDWR)
            and os.fspath(path).endswith("observation_reviews.jsonl")
        ):
            store.root.rename(detached)
            store.root.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(gold_shadow_store.os, "open", swap_before_write_open)

    store.append_observation_review(_restart_review())

    assert swapped is True
    assert not (outside / "observation_reviews.jsonl").exists()
    assert (detached / "observation_reviews.jsonl").exists()


def test_root_swap_before_review_read_cannot_redirect_read(tmp_path, monkeypatch):
    store = GoldShadowStore(tmp_path)
    store.append_observation_review(_restart_review("trusted"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "observation_reviews.jsonl").write_text(
        json.dumps(_restart_review("outside")) + "\n",
        encoding="utf-8",
    )
    detached = tmp_path / "detached"
    real_open = io.open
    swapped = False

    def swap_before_read(file, *args, **kwargs):
        nonlocal swapped
        is_review_path = os.fspath(file).endswith("observation_reviews.jsonl") if not isinstance(file, int) else False
        if not swapped and (is_review_path or isinstance(file, int)):
            store.root.rename(detached)
            store.root.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", swap_before_read)

    rows = store.list_observation_reviews()

    assert swapped is True
    assert rows[0]["note"] == "trusted"


def test_corrupted_comparison_run_fails_closed(service):
    market_date = _trading_dates(1)[0]
    run = _commit_run(service.store, market_date)
    path = service.store.root / "comparison_runs" / f"{run['run_id']}.jsonl"
    path.write_text('{"schema_version":1,"type":"summary"}\n', encoding="utf-8")

    result = service.status()

    assert result["status"] == "failed"


def test_corrupted_external_attempt_state_fails_closed(service):
    service.store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
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
