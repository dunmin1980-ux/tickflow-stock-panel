"""Fail-closed external-send port for the isolated Gold runtime."""
from __future__ import annotations

import json
import os
import re
import stat
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

_ATTEMPTS_FILENAME = "external_send_attempts.jsonl"
_CHANNEL_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_LOCK = threading.Lock()


class GoldExternalSendDenied(RuntimeError):  # noqa: N818 - public interface is fixed by the brief
    """Raised for every Gold external-send attempt."""


class GoldNotificationPort(Protocol):
    def send(self, channel: str, payload: Mapping[str, object]) -> None:
        ...


class DisabledGoldNotifier:
    """Record sanitized attempt metadata, then deny the send unconditionally."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def send(self, channel: str, payload: Mapping[str, object]) -> None:
        del payload
        _validate_channel(channel)
        with _LOCK:
            _record_attempt(self.root, channel)
        raise GoldExternalSendDenied(f"Gold external send disabled: {channel}")

    def attempt_count(self) -> int:
        return len(_validated_attempt_rows(self.root))


def attempt_count(root: Path) -> int:
    """Return the durable count, rejecting malformed state instead of guessing."""
    return len(_validated_attempt_rows(Path(root)))


def _validate_channel(channel: str) -> None:
    if not isinstance(channel, str) or _CHANNEL_PATTERN.fullmatch(channel) is None:
        raise ValueError("channel must match the lowercase external-send channel pattern")


def _record_attempt(root: Path, channel: str) -> None:
    root = Path(root)
    _reject_symlink(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink(root)
    os.chmod(root, 0o700)
    path = root / _ATTEMPTS_FILENAME
    row = json.dumps(
        {
            "schema_version": 1,
            "attempted_at": datetime.now(UTC).isoformat(),
            "channel": channel,
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(row)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(root)


def _validated_attempt_rows(root: Path) -> list[dict[str, object]]:
    root = Path(root)
    _reject_symlink(root)
    path = root / _ATTEMPTS_FILENAME
    if not _reject_symlink(path):
        return []
    rows: list[dict[str, object]] = []
    with _LOCK:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            with os.fdopen(descriptor, encoding="utf-8", closefd=False) as handle:
                for line_number, line in enumerate(handle, start=1):
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"invalid external send attempt at line {line_number}"
                        ) from exc
                    if (
                        not isinstance(value, dict)
                        or value.get("schema_version") != 1
                        or not isinstance(value.get("attempted_at"), str)
                        or not isinstance(value.get("channel"), str)
                    ):
                        raise ValueError(f"invalid external send attempt at line {line_number}")
                    _validate_channel(value["channel"])
                    rows.append(value)
        finally:
            os.close(descriptor)
    return rows


def _reject_symlink(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(mode):
        raise OSError(f"symlink is not allowed: {path}")
    return True


def _fsync_directory(root: Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
