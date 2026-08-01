"""Crash-safe publication of a complete directory tree."""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path


class AtomicDirectoryError(OSError):
    """Raised when the platform cannot atomically publish a directory."""


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_exchange(first: Path, second: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    first_bytes = os.fsencode(first)
    second_bytes = os.fsencode(second)
    if sys.platform == "darwin":
        rename_swap = 0x00000002
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(first_bytes, second_bytes, rename_swap)
    elif sys.platform.startswith("linux"):
        rename_exchange = 0x00000002
        at_fdcwd = -100
        function = getattr(libc, "renameat2", None)
        if function is None:
            raise AtomicDirectoryError("renameat2 is unavailable on this Linux host")
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            at_fdcwd,
            first_bytes,
            at_fdcwd,
            second_bytes,
            rename_exchange,
        )
    else:
        raise AtomicDirectoryError(
            f"atomic directory exchange is unsupported on {sys.platform}"
        )
    if result != 0:
        error_number = ctypes.get_errno()
        raise AtomicDirectoryError(
            error_number,
            os.strerror(error_number),
            str(first),
            str(second),
        )


def atomic_publish_directory(staging: Path, destination: Path) -> None:
    """Publish staging while keeping destination present across replacement."""
    if staging.is_symlink() or destination.is_symlink():
        raise AtomicDirectoryError("publication paths must not be symlinks")
    if not staging.is_dir():
        raise AtomicDirectoryError("staging path must be a directory")
    parent = destination.parent.resolve(strict=True)
    staging_parent = staging.parent.resolve(strict=True)
    if parent != staging_parent:
        raise AtomicDirectoryError("staging and destination must share a parent")
    destination = parent / destination.name
    staging = parent / staging.name
    if destination.exists() and not destination.is_dir():
        raise AtomicDirectoryError("destination exists and is not a directory")

    _fsync_directory(staging)
    _fsync_directory(parent)
    if not destination.exists():
        os.replace(staging, destination)
        _fsync_directory(parent)
        return

    _atomic_exchange(staging, destination)
    _fsync_directory(parent)
    shutil.rmtree(staging)
    _fsync_directory(parent)
