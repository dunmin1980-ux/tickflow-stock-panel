"""Session storage backed by macOS Keychain, with an in-memory fallback."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import threading
from typing import ClassVar

from app.desktop_client.config import validate_remote_url

_SERVICE = "com.tickflow.stockpanel.remote-session"
_SAFE_COOKIE_VALUE = re.compile(r"^[A-Za-z0-9._~-]+$")


class KeychainSessionStore:
    _memory: ClassVar[dict[str, str]] = {}
    _memory_lock: ClassVar[threading.Lock] = threading.Lock()

    @staticmethod
    def _account(remote_base_url: str) -> str:
        canonical = validate_remote_url(remote_base_url)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]

    def save(self, remote_base_url: str, token: str) -> None:
        if not token or not _SAFE_COOKIE_VALUE.fullmatch(token):
            raise ValueError("cloud returned an invalid session cookie")
        account = self._account(remote_base_url)
        if sys.platform != "darwin":
            with self._memory_lock:
                self._memory[account] = token
            return
        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-a",
                account,
                "-s",
                _SERVICE,
                "-U",
                "-w",
            ],
            # With ``-w`` and no argv value, macOS security prompts for the
            # password twice. Feed both prompts through stdin so the token
            # never appears in the process list.
            input=f"{token}\n{token}\n",
            text=True,
            capture_output=True,
            check=True,
            shell=False,
        )

    def load(self, remote_base_url: str) -> str | None:
        account = self._account(remote_base_url)
        if sys.platform != "darwin":
            with self._memory_lock:
                return self._memory.get(account)
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                account,
                "-s",
                _SERVICE,
                "-w",
            ],
            text=True,
            capture_output=True,
            check=False,
            shell=False,
        )
        if result.returncode != 0:
            return None
        token = result.stdout.rstrip("\r\n")
        return token if token and _SAFE_COOKIE_VALUE.fullmatch(token) else None

    def delete(self, remote_base_url: str) -> None:
        account = self._account(remote_base_url)
        if sys.platform != "darwin":
            with self._memory_lock:
                self._memory.pop(account, None)
            return
        subprocess.run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-a",
                account,
                "-s",
                _SERVICE,
            ],
            text=True,
            capture_output=True,
            check=False,
            shell=False,
        )
