"""Gold Stage A path security: fail-closed symlink / escape tests."""
from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import pytest

from app.services.gold_path_security import (
    GoldPathSecurityError,
    assert_no_symlink_components,
    open_gold_root_fd,
)
from app.services.gold_shadow_store import GoldShadowStorageReadError, GoldShadowStore


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file())


def test_normal_root_allows_json_writes(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    before = _count_files(outside)
    store = GoldShadowStore(tmp_path)
    store.write_health(
        last_success="2026-07-16T10:00:00+08:00",
        consecutive_failures=0,
        last_error=None,
    )
    assert store.read_health()["consecutive_failures"] == 0
    assert _count_files(outside) == before


def test_root_symlink_to_existing_dir_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    before = _count_files(outside)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user = data_dir / "user_data"
    user.mkdir()
    (user / "gold_shadow").symlink_to(outside, target_is_directory=True)
    store = GoldShadowStore(data_dir)
    with pytest.raises((GoldShadowStorageReadError, GoldPathSecurityError, OSError)):
        store.write_health(last_success=None, consecutive_failures=1, last_error={"code": "x"})
    assert _count_files(outside) == before


def test_dangling_root_symlink_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "user_data").mkdir(parents=True)
    missing = tmp_path / "missing-outside"
    (data_dir / "user_data" / "gold_shadow").symlink_to(missing, target_is_directory=True)
    store = GoldShadowStore(data_dir)
    with pytest.raises((GoldShadowStorageReadError, GoldPathSecurityError, OSError)):
        store.write_health(last_success=None, consecutive_failures=1, last_error={"code": "x"})
    assert not missing.exists()


def test_parent_symlink_rejected(tmp_path: Path) -> None:
    real_parent = tmp_path / "real_user_data"
    real_parent.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "user_data").symlink_to(real_parent, target_is_directory=True)
    store = GoldShadowStore(data_dir)
    with pytest.raises((GoldShadowStorageReadError, GoldPathSecurityError, OSError)):
        open_gold_root_fd(store.root, create=True)


def test_assert_no_symlink_components_on_file_symlink(tmp_path: Path) -> None:
    target = tmp_path / "t.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "l.json"
    link.symlink_to(target)
    with pytest.raises(GoldPathSecurityError):
        assert_no_symlink_components(link)


def test_open_root_fd_then_relative_write_stays_inside(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    before = _count_files(outside)
    store = GoldShadowStore(tmp_path)
    fd = open_gold_root_fd(store.root, create=True)
    try:
        import os

        child = os.open("health.json", os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600, dir_fd=fd)
        try:
            os.write(child, b'{"ok":true}\n')
        finally:
            os.close(child)
    finally:
        os.close(fd)
    assert (store.root / "health.json").is_file()
    assert _count_files(outside) == before


def test_review_root_symlink_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    before = _count_files(outside)
    store = GoldShadowStore(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    # review lives under gold root in current store design; simulate symlink child
    review = store.root / "review"
    review.symlink_to(outside, target_is_directory=True)
    with pytest.raises((GoldShadowStorageReadError, GoldPathSecurityError, OSError)):
        from app.services.gold_path_security import assert_no_symlink_components

        assert_no_symlink_components(review)
    assert _count_files(outside) == before


def test_jsonl_append_and_atomic_health_no_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    before = _count_files(outside)
    store = GoldShadowStore(tmp_path)
    store.append_snapshot(
        {
            "schema_version": 1,
            "observed_at": "2026-07-16T15:00:00+08:00",
            "market_date": "2026-07-16",
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
    )
    store.write_health(last_success=None, consecutive_failures=0, last_error=None)
    from app.services.gold_external_guard import DisabledGoldNotifier, GoldExternalSendDenied

    notifier = DisabledGoldNotifier(store.root)
    with pytest.raises(GoldExternalSendDenied):
        notifier.send("telegram", {"x": 1})
    assert notifier.attempt_count() == 1
    assert _count_files(outside) == before


def test_concurrent_writes_stay_inside(tmp_path: Path) -> None:
    import threading

    outside = tmp_path / "outside"
    outside.mkdir()
    before = _count_files(outside)
    store = GoldShadowStore(tmp_path)
    errors: list[BaseException] = []

    def _worker(i: int) -> None:
        try:
            store.write_health(
                last_success=None,
                consecutive_failures=i,
                last_error={"code": "concurrent", "n": i},
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert _count_files(outside) == before


def test_atomic_state_temp_symlink_cannot_overwrite_outside(tmp_path: Path) -> None:
    store = GoldShadowStore(tmp_path)
    store._ensure_root()
    outside = tmp_path / "outside-health.txt"
    outside.write_text("ORIGINAL", encoding="utf-8")
    (store.root / ".health.json.tmp").symlink_to(outside)

    with suppress(GoldShadowStorageReadError, GoldPathSecurityError, OSError):
        store.write_health(
            last_success=None,
            consecutive_failures=1,
            last_error={"code": "probe"},
        )

    assert outside.read_text(encoding="utf-8") == "ORIGINAL"
    assert not (store.root / "health.json").is_symlink()


def test_snapshot_symlink_is_rejected_instead_of_reading_outside(tmp_path: Path) -> None:
    store = GoldShadowStore(tmp_path)
    store._ensure_root()
    outside = tmp_path / "outside-snapshots.jsonl"
    outside.write_text(
        '{"schema_version":1,"observed_at":"2026-07-16T15:00:00+08:00",'
        '"market_date":"2026-07-16","symbol":"600489.SH",'
        '"quote_source":"tickflow","quote_ts":1784195101000,"price":19.99,'
        '"previous_close":20.12,"legacy_reference_60":22.87,"native_ema60":22.74,'
        '"P":-12.59,"V":-0.13,"A":-0.93,"state":"恐慌",'
        '"candidate_signals":[],"new_candidate_signals":[]}\n',
        encoding="utf-8",
    )
    (store.root / "snapshots.jsonl").symlink_to(outside)

    with pytest.raises((GoldShadowStorageReadError, GoldPathSecurityError, OSError)):
        store.list_snapshots()


@pytest.mark.parametrize(
    ("directory", "writer"),
    [
        ("imports", "_write_import_rows_locked"),
        ("comparison_runs", "_write_comparison_run_rows_locked"),
    ],
)
def test_child_directory_symlink_cannot_escape(
    tmp_path: Path, directory: str, writer: str
) -> None:
    store = GoldShadowStore(tmp_path)
    store._ensure_root()
    outside = tmp_path / f"outside-{directory}"
    outside.mkdir()
    (store.root / directory).symlink_to(outside, target_is_directory=True)

    digest = "a" * 64
    with pytest.raises((GoldShadowStorageReadError, GoldPathSecurityError, OSError)):
        getattr(store, writer)(digest, [])

    assert list(outside.iterdir()) == []
