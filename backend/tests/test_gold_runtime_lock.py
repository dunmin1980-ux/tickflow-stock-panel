"""Cross-process Gold runtime.lock tests (Stage A)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.gold_runtime_lock import GoldRuntimeLock, GoldRuntimeLockError


def test_single_instance_acquires(tmp_path: Path) -> None:
    root = tmp_path / "user_data" / "gold_shadow"
    lock = GoldRuntimeLock(root)
    lock.acquire()
    assert (root / "runtime.lock").is_file()
    lock.release()


def test_second_instance_rejected(tmp_path: Path) -> None:
    root = tmp_path / "user_data" / "gold_shadow"
    first = GoldRuntimeLock(root)
    first.acquire()
    second = GoldRuntimeLock(root)
    with pytest.raises(GoldRuntimeLockError, match="another Gold writer"):
        second.acquire()
    first.release()
    # After release, can acquire again
    second.acquire()
    second.release()


def test_symlink_lock_path_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data = tmp_path / "data"
    (data / "user_data").mkdir(parents=True)
    (data / "user_data" / "gold_shadow").symlink_to(outside, target_is_directory=True)
    lock = GoldRuntimeLock(data / "user_data" / "gold_shadow")
    with pytest.raises(GoldRuntimeLockError):
        lock.acquire()
    assert not (outside / "runtime.lock").exists()


def test_context_manager_releases(tmp_path: Path) -> None:
    root = tmp_path / "g"
    with GoldRuntimeLock(root) as lock:
        assert lock._lock_fd is not None
    again = GoldRuntimeLock(root)
    again.acquire()
    again.release()
