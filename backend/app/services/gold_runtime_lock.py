"""Cross-process exclusive writer lock for a Gold data root (Stage A)."""
from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path
from types import TracebackType

from app.services.gold_path_security import (
    GoldPathSecurityError,
    assert_no_symlink_components,
    open_beneath,
    open_gold_root_fd,
)

_LOCK_NAME = "runtime.lock"


class GoldRuntimeLockError(RuntimeError):
    """Raised when the Gold writer lock cannot be acquired safely."""


class GoldRuntimeLock:
    """``flock``-based exclusive lock under a validated Gold root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._root_fd: int | None = None
        self._lock_fd: int | None = None

    def acquire(self) -> None:
        if self._lock_fd is not None:
            return
        try:
            self._root_fd = open_gold_root_fd(self.root, create=True)
            assert_no_symlink_components(self.root / _LOCK_NAME if (self.root / _LOCK_NAME).exists() else self.root)
            flags = os.O_RDWR | os.O_CREAT
            self._lock_fd = open_beneath(self._root_fd, _LOCK_NAME, flags, 0o600)
            os.fchmod(self._lock_fd, 0o600)
        except (GoldPathSecurityError, OSError) as exc:
            self.release()
            raise GoldRuntimeLockError(f"gold runtime lock path rejected: {exc}") from exc

        try:
            if sys.platform == "win32":
                # Best-effort: msvcrt locking not used; Stage A targets Linux/macOS.
                import msvcrt

                msvcrt.locking(self._lock_fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.release()
            raise GoldRuntimeLockError(
                "another Gold writer holds this data directory"
            ) from exc

        payload = f"pid={os.getpid()}\n".encode()
        os.lseek(self._lock_fd, 0, os.SEEK_SET)
        os.ftruncate(self._lock_fd, 0)
        os.write(self._lock_fd, payload)
        os.fsync(self._lock_fd)
        atexit.register(self.release)

    def release(self) -> None:
        fd = self._lock_fd
        root_fd = self._root_fd
        self._lock_fd = None
        self._root_fd = None
        if fd is not None:
            try:
                if sys.platform != "win32":
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
        if root_fd is not None:
            try:
                os.close(root_fd)
            except OSError:
                pass

    def __enter__(self) -> GoldRuntimeLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        self.release()
