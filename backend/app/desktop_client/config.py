"""Non-secret desktop client configuration."""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import httpx

PreferredMode = Literal["hybrid", "cloud"]
_ALLOWED_FIELDS = frozenset({"remote_base_url", "preferred_mode"})


@dataclass(frozen=True)
class ClientConfig:
    remote_base_url: str
    preferred_mode: PreferredMode = "hybrid"

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ClientConfig:
        if set(value) - _ALLOWED_FIELDS:
            raise ValueError("desktop client config contains unsupported fields")
        remote_base_url = value.get("remote_base_url")
        if not isinstance(remote_base_url, str):
            raise ValueError("remote_base_url must be a string")
        preferred_mode = value.get("preferred_mode", "hybrid")
        if preferred_mode not in {"hybrid", "cloud"}:
            raise ValueError("preferred_mode must be hybrid or cloud")
        return cls(
            remote_base_url=validate_remote_url(remote_base_url),
            preferred_mode=preferred_mode,
        )


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_remote_url(value: str) -> str:
    """Return a canonical HTTPS base URL, allowing HTTP only on loopback."""
    if not value or value != value.strip() or any(char in value for char in "\r\n\0"):
        raise ValueError("remote_base_url must be a clean absolute URL")
    try:
        url = httpx.URL(value)
    except (TypeError, httpx.InvalidURL) as exc:
        raise ValueError("remote_base_url must be a valid absolute URL") from exc

    if not url.is_absolute_url or not url.host:
        raise ValueError("remote_base_url must be a valid absolute URL")
    if url.scheme not in {"http", "https"}:
        raise ValueError("remote_base_url must use HTTPS")
    if url.scheme != "https" and not (url.scheme == "http" and _is_loopback(url.host)):
        raise ValueError("remote_base_url must use HTTPS except on loopback")
    if url.userinfo:
        raise ValueError("remote_base_url must not contain credentials")
    if url.path not in {"", "/"} or url.query or url.fragment:
        raise ValueError("remote_base_url must not contain path, query or fragment")
    return str(url).rstrip("/")


def client_config_path(data_dir: Path) -> Path:
    return Path(data_dir) / "desktop_client.json"


def load_client_config(data_dir: Path) -> ClientConfig | None:
    path = client_config_path(data_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("desktop client config is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("desktop client config must be an object")
    return ClientConfig.from_mapping(payload)


def save_client_config(data_dir: Path, value: Mapping[str, object]) -> ClientConfig:
    """Atomically persist the two public client settings with owner-only permissions."""
    config = ClientConfig.from_mapping(value)
    target = client_config_path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    payload = json.dumps(asdict(config), ensure_ascii=True, indent=2, sort_keys=True) + "\n"

    fd: int | None = None
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        # Some non-POSIX filesystems do not expose owner-only modes.
        with suppress(OSError):
            os.chmod(target, 0o600)
    finally:
        if fd is not None:
            os.close(fd)
        with suppress(OSError):
            temp.unlink(missing_ok=True)
    return config
