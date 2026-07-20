"""Integrity-checked cache for safe cloud workspace DTOs."""

from __future__ import annotations

import json
import os
import re
import secrets
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.workspace.models import ResourceName, ResourceSnapshot
from app.workspace.revision import revision_for

_SCHEMA_VERSION = 1
_REVISION = re.compile(r"[0-9a-f]{64}")
_ENVELOPE_FIELDS = {
    "schema_version",
    "resource",
    "revision",
    "fetched_at",
    "checksum",
    "data",
}
_WATCHLIST_FIELDS = {"symbol", "added_at", "note"}
_PREFERENCE_FIELDS = {
    "indices_nav_pinned",
    "watchlist_columns",
    "screener_result_columns",
    "sidebar_index_symbols",
    "nav_order",
    "nav_hidden",
    "screener_auto_run",
    "daily_data_provider",
    "adj_factor_provider",
    "minute_data_provider",
    "realtime_data_provider",
    "financial_data_provider",
    "has_feishu_webhook",
    "has_feishu_credential_data",
    "has_wecom_webhook",
    "has_wecom_bot",
    "has_wecom_bot_credential_data",
}
_STOCK_REPORT_FIELDS = {
    "id",
    "symbol",
    "title",
    "created_at",
    "data_as_of",
    "verification_status",
    "can_publish",
    "trading_advice",
}
_MARKET_RECAP_FIELDS = _STOCK_REPORT_FIELDS - {"symbol"}
_BACKTEST_SUMMARY_FIELDS = {
    "id",
    "task",
    "strategy_id",
    "parameters_digest",
    "stats",
    "started_at",
    "finished_at",
    "data_as_of",
    "engine",
    "execution_target",
}


def _object(value: Any, *, fields: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - fields or not required <= set(value):
        raise ValueError("cache data is not a safe workspace DTO")
    return value


def _objects(value: Any, *, fields: set[str], required: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("cache data is not a safe workspace DTO")
    return [_object(item, fields=fields, required=required) for item in value]


def _validate_safe_data(resource: ResourceName, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("cache data is not a safe workspace DTO")

    if resource is ResourceName.WATCHLIST:
        root = _object(data, fields={"symbols"}, required={"symbols"})
        rows = _objects(root["symbols"], fields=_WATCHLIST_FIELDS, required={"symbol"})
        if any(not isinstance(row["symbol"], str) for row in rows):
            raise ValueError("cache data is not a safe workspace DTO")
    elif resource is ResourceName.PREFERENCES:
        root = _object(data, fields={"preferences"}, required={"preferences"})
        _object(root["preferences"], fields=_PREFERENCE_FIELDS, required=set())
    elif resource is ResourceName.STOCK_REPORTS:
        root = _object(data, fields={"reports"}, required={"reports"})
        _objects(root["reports"], fields=_STOCK_REPORT_FIELDS, required={"id"})
    elif resource is ResourceName.MARKET_RECAPS:
        root = _object(data, fields={"reports"}, required={"reports"})
        _objects(root["reports"], fields=_MARKET_RECAP_FIELDS, required={"id"})
    elif resource is ResourceName.BACKTEST_SUMMARIES:
        root = _object(data, fields={"summaries"}, required={"summaries"})
        _objects(
            root["summaries"],
            fields=_BACKTEST_SUMMARY_FIELDS,
            required=_BACKTEST_SUMMARY_FIELDS,
        )
    else:  # pragma: no cover - exhaustive guard for future enum additions
        raise ValueError("cache data is not a safe workspace DTO")

    try:
        return json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("cache data is not a safe workspace DTO") from exc


def _timestamp(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


class WorkspaceCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            os.chmod(self.root, 0o700)

    def path(self, resource: ResourceName) -> Path:
        return self.root / f"{ResourceName(resource).value}.json"

    def _quarantine(self, resource: ResourceName, path: Path) -> None:
        quarantine = self.root / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            os.chmod(quarantine, 0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = quarantine / f"{stamp}-{resource.value}.json"
        temp = quarantine / f".{target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        record = json.dumps(
            {
                "schema_version": _SCHEMA_VERSION,
                "resource": resource.value,
                "quarantined_at": datetime.now(UTC).isoformat(),
                "reason": "invalid_cache",
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        fd: int | None = None
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                handle.write(record)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except OSError:
            pass
        finally:
            if fd is not None:
                os.close(fd)
            with suppress(OSError):
                temp.unlink(missing_ok=True)
            with suppress(OSError):
                path.unlink()

    def get(self, resource: ResourceName) -> ResourceSnapshot | None:
        resource = ResourceName(resource)
        path = self.path(resource)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
                raise ValueError("invalid cache envelope")
            if envelope["schema_version"] != _SCHEMA_VERSION:
                raise ValueError("invalid cache schema")
            if envelope["resource"] != resource.value:
                raise ValueError("invalid cache resource")
            revision = envelope["revision"]
            checksum = envelope["checksum"]
            if not isinstance(revision, str) or _REVISION.fullmatch(revision) is None:
                raise ValueError("invalid cache revision")
            if not isinstance(checksum, str) or _REVISION.fullmatch(checksum) is None:
                raise ValueError("invalid cache checksum")
            fetched_at = datetime.fromisoformat(str(envelope["fetched_at"]).replace("Z", "+00:00"))
            data = _validate_safe_data(resource, envelope["data"])
            actual_checksum = revision_for(data)
            if checksum != actual_checksum or revision != actual_checksum:
                raise ValueError("cache integrity check failed")
            return ResourceSnapshot(
                resource=resource,
                revision=revision,
                updated_at=fetched_at,
                data=data,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._quarantine(resource, path)
            return None

    def put(self, snapshot: ResourceSnapshot) -> None:
        resource = ResourceName(snapshot.resource)
        data = _validate_safe_data(resource, snapshot.data)
        checksum = revision_for(data)
        if _REVISION.fullmatch(snapshot.revision) is None or snapshot.revision != checksum:
            raise ValueError("cache snapshot revision is invalid")
        envelope = {
            "schema_version": _SCHEMA_VERSION,
            "resource": resource.value,
            "revision": snapshot.revision,
            "fetched_at": _timestamp(snapshot.updated_at),
            "checksum": checksum,
            "data": data,
        }
        target = self.path(resource)
        temp = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        payload = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fd: int | None = None
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
            with suppress(OSError):
                os.chmod(target, 0o600)
        finally:
            if fd is not None:
                os.close(fd)
            with suppress(OSError):
                temp.unlink(missing_ok=True)

    def quarantine(self, resource: ResourceName) -> None:
        path = self.path(resource)
        if path.exists():
            self._quarantine(ResourceName(resource), path)
