from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.gold_comparison_runs import GoldComparisonRunner, _run_id
from app.services.gold_shadow_store import GoldShadowStorageReadError, GoldShadowStore

CN_TZ = timezone(timedelta(hours=8))


def observed_at(market_date: date, *, minute: int = 0) -> str:
    return datetime(
        market_date.year, market_date.month, market_date.day, 15, minute, tzinfo=CN_TZ
    ).isoformat()


def legacy_row(market_date: date, *, minute: int = 0, price: float = 19.99) -> dict[str, object]:
    return {
        "schema_version": 1,
        "observed_at": observed_at(market_date, minute=minute),
        "price": price,
        "P": -12.59,
        "V": -0.13,
        "A": -0.93,
        "state": "恐慌",
        "signals": ["恐慌极端"],
    }


def snapshot(market_date: date, *, minute: int = 0, price: float = 19.99) -> dict[str, object]:
    at = datetime.fromisoformat(observed_at(market_date, minute=minute))
    return {
        "schema_version": 1,
        "observed_at": at.isoformat(),
        "market_date": market_date.isoformat(),
        "symbol": "600489.SH",
        "quote_source": "tickflow",
        "quote_ts": int(at.timestamp() * 1000),
        "price": price,
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


def commit_import(store: GoldShadowStore, label: str, rows: list[dict[str, object]]) -> str:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return store.commit_import(digest, f"{label}.log", rows)["import_id"]


def make_runner(tmp_path) -> GoldComparisonRunner:
    return GoldComparisonRunner(GoldShadowStore(tmp_path))


def test_same_inputs_return_same_run_id_and_one_canonical_index_entry(tmp_path):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_id = commit_import(runner.store, "import-a", [legacy_row(market_date)])
    runner.store.commit_evaluation(snapshot(market_date))

    first = runner.run(import_id, market_date)
    second = runner.run(import_id, market_date)

    assert second["run_id"] == first["run_id"]
    assert second["run_id"] == _run_id(
        market_date=market_date,
        legacy_digest=first["legacy_digest"],
        shadow_digest=first["shadow_digest"],
    )
    assert len(runner.list_runs(10)) == 1


def test_changed_import_supersedes_same_day_run(tmp_path):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_a = commit_import(runner.store, "import-a", [legacy_row(market_date)])
    import_b = commit_import(runner.store, "import-b", [legacy_row(market_date, price=20.00)])
    runner.store.commit_evaluation(snapshot(market_date))

    first = runner.run(import_a, market_date)
    second = runner.run(import_b, market_date)

    assert second["supersedes_run_id"] == first["run_id"]


def test_run_filters_legacy_and_shadow_to_exact_beijing_market_date(tmp_path):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_id = commit_import(
        runner.store,
        "multi-day",
        [legacy_row(date(2026, 7, 15)), legacy_row(market_date)],
    )
    runner.store.commit_evaluation(snapshot(date(2026, 7, 15)))
    runner.store.commit_evaluation(snapshot(market_date))

    run = runner.run(import_id, market_date)
    persisted = runner.get_run(run["run_id"])

    assert run["legacy_sample_count"] == 1
    assert run["shadow_sample_count"] == 1
    assert persisted is not None
    assert persisted["rows"][-1]["type"] == "summary"
    assert persisted["rows"][-1]["totals"] == {
        "legacy": 1,
        "shadow": 1,
        "matched": 1,
        "missing_shadow": 0,
        "unmatched_shadow": 0,
    }


def test_run_canonicalizes_multiple_same_day_samples(tmp_path):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_id = commit_import(
        runner.store,
        "same-day-samples",
        [legacy_row(market_date, minute=5), legacy_row(market_date)],
    )
    runner.store.commit_evaluation(snapshot(market_date, minute=5))
    runner.store.commit_evaluation(snapshot(market_date))

    run = runner.run(import_id, market_date)

    assert run["legacy_sample_count"] == 2
    assert run["shadow_sample_count"] == 2


@pytest.mark.parametrize(
    ("legacy_rows", "shadow_rows", "expected_totals"),
    [
        ([], [snapshot(date(2026, 7, 16))], {"legacy": 0, "shadow": 1}),
        ([legacy_row(date(2026, 7, 16))], [], {"legacy": 1, "shadow": 0}),
    ],
)
def test_run_persists_terminal_summary_when_one_side_has_no_samples(
    tmp_path, legacy_rows, shadow_rows, expected_totals
):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_id = commit_import(runner.store, f"empty-{len(legacy_rows)}", legacy_rows or [legacy_row(date(2026, 7, 15))])
    for row in shadow_rows:
        runner.store.commit_evaluation(row)

    run = runner.run(import_id, market_date)
    persisted = runner.get_run(run["run_id"])

    assert persisted is not None
    assert [row["type"] for row in persisted["rows"]].count("summary") == 1
    assert persisted["rows"][-1]["type"] == "summary"
    assert {
        key: persisted["rows"][-1]["totals"][key] for key in expected_totals
    } == expected_totals


def test_run_fails_closed_for_corrupted_import_without_publishing_metadata(tmp_path):
    runner = make_runner(tmp_path)
    import_id = commit_import(runner.store, "corrupt", [legacy_row(date(2026, 7, 16))])
    path = runner.store.root / "imports" / f"{import_id}.jsonl"
    path.write_text("{bad\n", encoding="utf-8")

    with pytest.raises(GoldShadowStorageReadError, match="unable to read import"):
        runner.run(import_id, date(2026, 7, 16))

    assert not (runner.store.root / "comparison_index.jsonl").exists()


def test_interrupted_run_data_write_leaves_no_committed_metadata_or_temp_file(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_id = commit_import(runner.store, "interrupted", [legacy_row(market_date)])
    runner.store.commit_evaluation(snapshot(market_date))

    import app.services.gold_shadow_store as store_module

    original_replace = store_module.os.replace

    def fail_data_replace(source, target):
        if target.parent.name == "comparison_runs":
            raise OSError("injected run data write failure")
        return original_replace(source, target)

    monkeypatch.setattr(store_module.os, "replace", fail_data_replace)

    with pytest.raises(OSError, match="injected run data write failure"):
        runner.run(import_id, market_date)

    assert not (runner.store.root / "comparison_index.jsonl").exists()
    assert list((runner.store.root / "comparison_runs").glob("*.tmp")) == []


def test_list_runs_uses_canonical_market_date_then_run_id_ordering(tmp_path):
    runner = make_runner(tmp_path)
    runs = []
    for market_date, label in (
        (date(2026, 7, 15), "run-a"),
        (date(2026, 7, 17), "run-b"),
        (date(2026, 7, 16), "run-c"),
    ):
        import_id = commit_import(runner.store, label, [legacy_row(market_date)])
        runner.store.commit_evaluation(snapshot(market_date))
        runs.append(runner.run(import_id, market_date))

    expected = sorted(runs, key=lambda row: (row["market_date"], row["run_id"]), reverse=True)

    assert runner.list_runs(10) == expected
    assert runner.list_runs(0) == []


def test_run_file_contains_comparison_rows_and_exactly_one_terminal_summary(tmp_path):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_id = commit_import(runner.store, "run-rows", [legacy_row(market_date)])
    runner.store.commit_evaluation(snapshot(market_date, minute=4))

    run = runner.run(import_id, market_date)
    path = runner.store.root / "comparison_runs" / f"{run['run_id']}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert [row["type"] for row in rows] == ["missing_shadow", "unmatched_shadow", "summary"]


def test_schema_valid_normalized_import_tampering_rejects_comparison_run(tmp_path):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_id = commit_import(runner.store, "valid-json-tamper", [legacy_row(market_date)])
    runner.store.commit_evaluation(snapshot(market_date))
    path = runner.store.root / "imports" / f"{import_id}.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["price"] = 20.00
    path.write_text(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    with pytest.raises(GoldShadowStorageReadError, match="unable to read import"):
        runner.run(import_id, market_date)

    assert not (runner.store.root / "comparison_index.jsonl").exists()


@pytest.mark.parametrize(
    "corruption",
    ["missing", "truncated", "removed_row", "duplicate_summary", "reordered", "tampered", "malformed"],
)
def test_indexed_run_corruption_fails_closed(tmp_path, corruption):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_id = commit_import(
        runner.store,
        f"integrity-{corruption}",
        [legacy_row(market_date), legacy_row(market_date, minute=5)],
    )
    runner.store.commit_evaluation(snapshot(market_date))
    runner.store.commit_evaluation(snapshot(market_date, minute=5))
    run = runner.run(import_id, market_date)
    path = runner.store.root / "comparison_runs" / f"{run['run_id']}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    if corruption == "missing":
        path.unlink()
    elif corruption == "truncated":
        path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    elif corruption == "removed_row":
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in [rows[0], rows[-1]]), encoding="utf-8"
        )
    elif corruption == "duplicate_summary":
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in [*rows, rows[-1]]), encoding="utf-8"
        )
    elif corruption == "reordered":
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in [rows[1], rows[0], rows[-1]]), encoding="utf-8"
        )
    elif corruption == "tampered":
        rows[0]["checks"]["price"] = False
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    else:
        path.write_text(path.read_text(encoding="utf-8") + "{bad\n", encoding="utf-8")

    with pytest.raises(GoldShadowStorageReadError):
        runner.get_run(run["run_id"])


def test_idempotent_commit_and_list_reject_tampered_indexed_run(tmp_path):
    runner = make_runner(tmp_path)
    market_date = date(2026, 7, 16)
    import_id = commit_import(runner.store, "integrity-access", [legacy_row(market_date)])
    runner.store.commit_evaluation(snapshot(market_date))
    run = runner.run(import_id, market_date)
    path = runner.store.root / "comparison_runs" / f"{run['run_id']}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["checks"]["price"] = False
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(GoldShadowStorageReadError):
        runner.run(import_id, market_date)
    with pytest.raises(GoldShadowStorageReadError):
        runner.list_runs(10)
