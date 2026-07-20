"""Fail-closed path safety for Gold Stage A storage roots.

Symlink components (including the root itself and any parent) are rejected
before create/read/write. ``O_NOFOLLOW`` alone is not trusted as the only
defense — every ancestor is ``lstat``'d first.
"""
from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path


class GoldPathSecurityError(OSError):
    """Raised when a Gold storage path is unsafe (symlink / escape)."""


def _relative_parts(relative: str) -> tuple[str, ...]:
    path = Path(relative)
    parts = path.parts
    if path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise GoldPathSecurityError(f"illegal relative path: {relative}")
    return parts


def _lstat(path: Path) -> os.stat_result:
    return os.lstat(os.fspath(path))


def assert_no_symlink_components(path: Path) -> Path:
    """Reject if *path* or any existing ancestor is a symlink."""
    path = Path(path)
    # Walk from root toward leaf so a symlinked parent cannot hide.
    resolved_parts: list[str] = []
    if path.is_absolute():
        cursor = Path(path.anchor)
        parts = path.parts[1:]
    else:
        cursor = Path(".")
        parts = path.parts
    # Validate absolute anchor itself is not a symlink (rare on POSIX).
    if path.is_absolute() and cursor.exists():
        st = _lstat(cursor)
        if stat.S_ISLNK(st.st_mode):
            raise GoldPathSecurityError(f"symlink component rejected: {cursor}")
    for part in parts:
        cursor = cursor / part
        try:
            st = _lstat(cursor)
        except FileNotFoundError:
            # Remaining components do not exist yet — OK if parents were clean.
            break
        if stat.S_ISLNK(st.st_mode):
            raise GoldPathSecurityError(f"symlink component rejected: {cursor}")
        resolved_parts.append(part)
    return path


def assert_path_inside_root(path: Path, root: Path) -> None:
    """After resolving real paths, ensure *path* stays under *root*."""
    root_real = os.path.realpath(os.fspath(root))
    path_real = os.path.realpath(os.fspath(path))
    try:
        common = os.path.commonpath([root_real, path_real])
    except ValueError as exc:
        raise GoldPathSecurityError("path escapes gold root") from exc
    if common != root_real:
        raise GoldPathSecurityError("path escapes gold root")


def open_gold_root_fd(root: Path, *, create: bool) -> int:
    """Open Gold root directory with fail-closed symlink checks.

    Returns a directory file descriptor. Caller must ``os.close``.
    """
    root = Path(root)
    assert_no_symlink_components(root)

    if not root.exists():
        if not create:
            raise FileNotFoundError(os.fspath(root))
        # Create parents then leaf after symlink checks; refuse symlink leaf.
        parent = root.parent
        assert_no_symlink_components(parent)
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        assert_no_symlink_components(parent)
        with suppress(FileExistsError):
            root.mkdir(mode=0o700, exist_ok=False)
        assert_no_symlink_components(root)
    else:
        assert_no_symlink_components(root)

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(os.fspath(root), flags)
    except OSError as exc:
        raise GoldPathSecurityError(f"unable to open gold root safely: {root}") from exc

    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise GoldPathSecurityError("gold root is not a directory")
        # Confirm the opened inode is not reachable via a symlink path swap:
        # compare lstat of configured path (must be directory, not link).
        lst = _lstat(root)
        if stat.S_ISLNK(lst.st_mode):
            raise GoldPathSecurityError("gold root is a symlink")
        if not stat.S_ISDIR(lst.st_mode):
            raise GoldPathSecurityError("gold root is not a directory")
        if (lst.st_dev, lst.st_ino) != (st.st_dev, st.st_ino):
            raise GoldPathSecurityError("gold root inode mismatch after open")
        os.fchmod(fd, 0o700)
    except Exception:
        os.close(fd)
        raise
    return fd


def open_beneath(
    root_fd: int,
    relative: str,
    flags: int,
    mode: int = 0o600,
) -> int:
    """Open a file below a trusted root without following any path symlink."""
    parts = _relative_parts(relative)
    parent_fd = (
        os.dup(root_fd)
        if len(parts) == 1
        else open_directory_beneath(root_fd, "/".join(parts[:-1]), create=False)
    )
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        try:
            return os.open(parts[-1], flags, mode, dir_fd=parent_fd)
        except OSError as exc:
            try:
                target = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except OSError:
                raise exc from None
            if stat.S_ISLNK(target.st_mode):
                raise GoldPathSecurityError(
                    f"symlink component rejected: {relative}"
                ) from exc
            raise
    finally:
        os.close(parent_fd)


def open_directory_beneath(
    root_fd: int,
    relative: str,
    *,
    create: bool,
    mode: int = 0o700,
) -> int:
    """Open or create a directory chain below ``root_fd`` without symlinks."""
    parts = _relative_parts(relative)
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            try:
                target = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise
                with suppress(FileExistsError):
                    os.mkdir(part, mode, dir_fd=current_fd)
                target = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            if stat.S_ISLNK(target.st_mode):
                raise GoldPathSecurityError(
                    f"symlink component rejected: {relative}"
                )
            if not stat.S_ISDIR(target.st_mode):
                raise GoldPathSecurityError(
                    f"directory component rejected: {relative}"
                )

            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            if hasattr(os, "O_DIRECTORY"):
                flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            next_fd = os.open(part, flags, dir_fd=current_fd)
            os.fchmod(next_fd, mode)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise
