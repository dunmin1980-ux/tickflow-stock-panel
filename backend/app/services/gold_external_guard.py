"""Fail-closed external-send port for the isolated Gold runtime."""
from __future__ import annotations

import json
import os
import re
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
    row = json.dumps(
        {
            "schema_version": 1,
            "attempted_at": datetime.now(UTC).isoformat(),
            "channel": channel,
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    root_descriptor = _open_root(root, create=True)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(
            _ATTEMPTS_FILENAME, flags, 0o600, dir_fd=root_descriptor
        )
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(row)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(root_descriptor)
    finally:
        os.close(root_descriptor)


def _validated_attempt_rows(root: Path) -> list[dict[str, object]]:
    root = Path(root)
    rows: list[dict[str, object]] = []
    try:
        root_descriptor = _open_root(root, create=False)
    except FileNotFoundError:
        return rows
    with _LOCK:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            try:
                descriptor = os.open(_ATTEMPTS_FILENAME, flags, dir_fd=root_descriptor)
            except FileNotFoundError:
                return rows
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
                            raise ValueError(
                                f"invalid external send attempt at line {line_number}"
                            )
                        _validate_channel(value["channel"])
                        rows.append(value)
            finally:
                os.close(descriptor)
        finally:
            os.close(root_descriptor)
    return rows


def _open_root(root: Path, *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(root, flags)
    except FileNotFoundError:
        if not create:
            raise
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(root, flags)
    os.fchmod(descriptor, 0o700)
    return descriptor


def _fsync_directory(descriptor: int) -> None:
    os.fsync(descriptor)
