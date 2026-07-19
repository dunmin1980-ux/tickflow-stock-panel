import json
import logging
import math
import stat
from datetime import date
from pathlib import Path

import pytest

from app.services.gold_shadow_store import GoldShadowStorageReadError, GoldShadowStore


def snapshot(
    *,
    observed_at="2026-07-16T15:00:00+08:00",
    market_date="2026-07-16",
):
    return {
        "schema_version": 1,
        "observed_at": observed_at,
        "market_date": market_date,
        "symbol": "600489.SH",
        "quote_source": "tickflow",
        "quote_ts": 1784195101000,
        "price": 19.99,
        "previous_close": 20.12,
        "legacy_reference_60": 22.87,
        "native_ema60": 22.74,
        "P": -12.59,
        "V": -0.13,
        "A": -0.93,
        "state": "恐慌",
        "candidate_signals": ["恐慌极端"],
        "new_candidate_signals": ["恐慌极端"],
    }


def valid_snapshot():
    return snapshot()


@pytest.mark.parametrize("field", ["price", "previous_close", "legacy_reference_60"])
def test_snapshot_rejects_nonpositive_reference_values(tmp_path, field):
    row = valid_snapshot()
    row[field] = 0
    with pytest.raises(ValueError, match=field):
        GoldShadowStore(tmp_path).append_snapshot(row)


def test_snapshot_requires_tickflow_source(tmp_path):
    row = valid_snapshot()
    row["quote_source"] = "tushare"
    with pytest.raises(ValueError, match="quote_source"):
        GoldShadowStore(tmp_path).append_snapshot(row)


def test_append_and_restart_restore_latest_snapshot(tmp_path):
    first = GoldShadowStore(tmp_path)
    first.append_snapshot(snapshot())

    second = GoldShadowStore(tmp_path)

    assert second.latest_snapshot()["observed_at"] == "2026-07-16T15:00:00+08:00"


def test_append_snapshot_rejects_missing_required_field_before_writing(tmp_path):
    row = snapshot()
    del row["observed_at"]

    with pytest.raises(ValueError):
        GoldShadowStore(tmp_path).append_snapshot(row)

    assert not (tmp_path / "user_data/gold_shadow/snapshots.jsonl").exists()


def test_append_snapshot_rejects_wrong_schema_version(tmp_path):
    row = snapshot()
    row["schema_version"] = 2

    with pytest.raises(ValueError):
        GoldShadowStore(tmp_path).append_snapshot(row)


def test_append_snapshot_rejects_non_finite_numeric_input(tmp_path):
    row = snapshot()
    row["P"] = math.inf

    with pytest.raises(ValueError):
        GoldShadowStore(tmp_path).append_snapshot(row)


def test_commit_evaluation_dedupes_candidates_in_canonical_snapshots(tmp_path):
    store = GoldShadowStore(tmp_path)

    first = store.commit_evaluation(snapshot())
    second_row = snapshot(observed_at="2026-07-16T15:05:00+08:00")
    second_row["quote_ts"] += 5 * 60 * 1000
    second = store.commit_evaluation(second_row)

    assert first["new_candidate_signals"] == ["恐慌极端"]
    assert second["new_candidate_signals"] == []
    assert len(store.list_snapshots()) == 2
    assert len(store.list_candidates()) == 1


def test_commit_evaluation_does_not_project_candidate_without_canonical_snapshot(
    tmp_path, monkeypatch
):
    store = GoldShadowStore(tmp_path)
    original_append = store._append_jsonl_locked

    def fail_snapshot_append(filename, row):
        if filename == "snapshots.jsonl":
            raise OSError("injected canonical append failure")
        return original_append(filename, row)

    monkeypatch.setattr(store, "_append_jsonl_locked", fail_snapshot_append)

    with pytest.raises(OSError, match="injected canonical append failure"):
        store.commit_evaluation(snapshot())

    assert not (store.root / "candidates.jsonl").exists()
    assert not (store.root / "candidate_state.json").exists()


@pytest.mark.parametrize(
    "failure_boundary",
    ["candidate_event", "candidate_state", "snapshot_retention"],
)
def test_commit_evaluation_recovers_projection_after_post_canonical_failure(
    tmp_path, monkeypatch, failure_boundary
):
    store = GoldShadowStore(tmp_path)

    if failure_boundary == "candidate_event":
        original = store._append_jsonl_locked

        def fail_candidate_append(filename, row):
            if filename == "candidates.jsonl":
                raise OSError("injected candidate event failure")
            return original(filename, row)

        monkeypatch.setattr(store, "_append_jsonl_locked", fail_candidate_append)
    elif failure_boundary == "candidate_state":
        original = store._write_json_state_locked

        def fail_candidate_state(filename, value):
            if filename == "candidate_state.json":
                raise OSError("injected candidate state failure")
            return original(filename, value)

        monkeypatch.setattr(store, "_write_json_state_locked", fail_candidate_state)
    else:
        original = store._replace_jsonl_locked

        def fail_snapshot_retention(filename, rows):
            if filename == "snapshots.jsonl":
                raise OSError("injected snapshot retention failure")
            return original(filename, rows)

        monkeypatch.setattr(store, "_replace_jsonl_locked", fail_snapshot_retention)

    with pytest.raises(OSError, match="injected"):
        store.commit_evaluation(snapshot())

    canonical_rows = [
        json.loads(line)
        for line in (store.root / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(canonical_rows) == 1
    assert canonical_rows[0]["new_candidate_signals"] == ["恐慌极端"]

    restarted = GoldShadowStore(tmp_path)
    assert len(restarted.list_candidates()) == 1
    recovered = restarted.commit_evaluation(snapshot())

    assert recovered == canonical_rows[0]
    assert len(restarted.list_snapshots()) == 1
    assert len(restarted.list_candidates()) == 1
    state = json.loads((store.root / "candidate_state.json").read_text(encoding="utf-8"))
    assert state["keys"] == ["恐慌极端|600489.SH|2026-07-16"]


def test_candidate_key_is_signal_symbol_and_market_date(tmp_path):
    store = GoldShadowStore(tmp_path)

    assert store.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is True
    assert store.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is False
    assert store.register_candidate("恐慌极端", "600489.SH", "2026-07-17") is True


def test_candidate_dedupe_survives_restart_and_does_not_cross_signal_or_symbol(tmp_path):
    first = GoldShadowStore(tmp_path)
    assert first.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is True

    second = GoldShadowStore(tmp_path)

    assert second.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is False
    assert second.register_candidate("均值回归", "600489.SH", "2026-07-16") is True
    assert second.register_candidate("恐慌极端", "000001.SZ", "2026-07-16") is True


def test_candidate_event_recovers_after_state_write_failure(tmp_path, monkeypatch):
    store = GoldShadowStore(tmp_path)
    original_write = store._write_json_state_locked

    def fail_state_write(filename, value):
        if filename == "candidate_state.json":
            raise OSError("injected state write failure")
        original_write(filename, value)

    monkeypatch.setattr(store, "_write_json_state_locked", fail_state_write)

    with pytest.raises(OSError, match="injected state write failure"):
        store.register_candidate("恐慌极端", "600489.SH", "2026-07-16")

    restarted = GoldShadowStore(tmp_path)
    assert restarted.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is False
    assert len(restarted.list_candidates()) == 1
    state = json.loads(
        (tmp_path / "user_data/gold_shadow/candidate_state.json").read_text(encoding="utf-8")
    )
    assert state["keys"] == ["恐慌极端|600489.SH|2026-07-16"]


def test_candidate_append_separates_unterminated_malformed_tail(tmp_path, caplog):
    path = tmp_path / "user_data/gold_shadow/candidates.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{bad", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        store = GoldShadowStore(tmp_path)
        assert store.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is True

        restarted = GoldShadowStore(tmp_path)
        assert restarted.list_candidates() == [
            {
                "key": "恐慌极端|600489.SH|2026-07-16",
                "signal": "恐慌极端",
                "symbol": "600489.SH",
                "market_date": "2026-07-16",
            }
        ]
        assert restarted.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is False

    state = json.loads(
        (tmp_path / "user_data/gold_shadow/candidate_state.json").read_text(encoding="utf-8")
    )
    assert state["keys"] == ["恐慌极端|600489.SH|2026-07-16"]
    assert "malformed candidates.jsonl line 1" in caplog.text


def test_malformed_jsonl_line_is_skipped(tmp_path, caplog):
    path = tmp_path / "user_data/gold_shadow/snapshots.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"observed_at":"2026-07-16T15:00:00+08:00"}\n{bad\n', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        rows = GoldShadowStore(tmp_path).list_snapshots(limit=10)

    assert len(rows) == 1
    assert "malformed" in caplog.text


def test_prune_keeps_newest_rows_and_max_days(tmp_path):
    store = GoldShadowStore(tmp_path, max_records=3, max_days=30)
    for market_date in ("2026-06-01", "2026-07-14", "2026-07-15", "2026-07-16"):
        store.append_snapshot(snapshot(market_date=market_date), now=date(2026, 7, 16))

    assert [row["market_date"] for row in store.list_snapshots(10)] == [
        "2026-07-16",
        "2026-07-15",
        "2026-07-14",
    ]


def test_append_candidate_writes_only_first_registration(tmp_path):
    store = GoldShadowStore(tmp_path)

    assert store.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is True
    assert store.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is False

    rows = store.list_candidates()
    assert len(rows) == 1
    assert rows[0]["key"] == "恐慌极端|600489.SH|2026-07-16"


def test_candidate_state_write_replaces_temp_file(tmp_path, monkeypatch):
    store = GoldShadowStore(tmp_path)
    replaced = []
    original_replace = __import__("os").replace

    def record_replace(source, target):
        replaced.append((source, target))
        return original_replace(source, target)

    monkeypatch.setattr("app.services.gold_shadow_store.os.replace", record_replace)

    assert store.register_candidate("恐慌极端", "600489.SH", "2026-07-16") is True

    assert replaced
    assert replaced[-1][1].name == "candidate_state.json"
    assert not (tmp_path / "user_data/gold_shadow/candidate_state.json.tmp").exists()


def test_health_state_is_atomic_and_restart_safe(tmp_path):
    store = GoldShadowStore(tmp_path)
    store.write_health(last_success="2026-07-16T15:00:00+08:00", consecutive_failures=0, last_error=None)

    assert GoldShadowStore(tmp_path).read_health() == {
        "last_success": "2026-07-16T15:00:00+08:00",
        "consecutive_failures": 0,
        "last_error": None,
    }


def test_storage_root_and_persisted_files_are_private(tmp_path):
    store = GoldShadowStore(tmp_path)
    store.commit_evaluation(snapshot())
    store.append_comparison({"comparison": "redacted"})
    store.write_health(last_success="2026-07-16T15:00:00+08:00", consecutive_failures=0, last_error=None)

    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    for filename in (
        "snapshots.jsonl",
        "candidates.jsonl",
        "candidate_state.json",
        "comparisons.jsonl",
        "health.json",
    ):
        assert stat.S_IMODE((store.root / filename).stat().st_mode) == 0o600


def test_read_health_distinguishes_missing_from_corrupt_state(tmp_path):
    store = GoldShadowStore(tmp_path)

    assert store.read_health() is None
    store.root.mkdir(parents=True)
    (store.root / "health.json").write_text("{bad", encoding="utf-8")

    with pytest.raises(GoldShadowStorageReadError, match=r"health\.json"):
        store.read_health()


def test_latest_snapshot_distinguishes_missing_from_unreadable_jsonl(tmp_path):
    store = GoldShadowStore(tmp_path)

    assert store.latest_snapshot() is None
    store.root.mkdir(parents=True)
    (store.root / "snapshots.jsonl").write_bytes(b"\xff")

    with pytest.raises(GoldShadowStorageReadError, match=r"snapshots\.jsonl"):
        store.latest_snapshot()


def test_append_snapshot_preserves_invalid_utf8_snapshot_file(tmp_path):
    store = GoldShadowStore(tmp_path)
    path = store.root / "snapshots.jsonl"
    path.parent.mkdir(parents=True)
    original = b'\xff{"partial":"bytes"}'
    path.write_bytes(original)

    with pytest.raises(GoldShadowStorageReadError, match=r"snapshots\.jsonl"):
        store.append_snapshot(snapshot())

    assert path.read_bytes() == original


def test_append_snapshot_preserves_file_when_snapshot_read_fails(tmp_path, monkeypatch):
    store = GoldShadowStore(tmp_path)
    path = store.root / "snapshots.jsonl"
    path.parent.mkdir(parents=True)
    original = b'{"observed_at":"2026-07-16T15:00:00+08:00"}\n'
    path.write_bytes(original)
    original_open = Path.open

    def fail_text_open(self, *args, **kwargs):
        if self == path and kwargs.get("encoding") == "utf-8":
            raise OSError("injected snapshot read failure")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_text_open)

    with pytest.raises(GoldShadowStorageReadError, match=r"snapshots\.jsonl"):
        store.append_snapshot(snapshot())

    assert path.read_bytes() == original


def test_holidays_are_read_only(tmp_path):
    path = tmp_path / "user_data/gold_shadow/holidays.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(["2026-10-01"]), encoding="utf-8")

    assert GoldShadowStore(tmp_path).read_holidays() == ["2026-10-01"]
