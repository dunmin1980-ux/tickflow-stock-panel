"""Integrity-checked cache for safe cloud workspace DTOs."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from app.services import preferences as preference_service
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
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "key",
    "password",
    "cookie",
    "webhook",
    "content",
    "markdown",
    "credential",
    "url",
)
_SAFE_STATUS_KEYS = {
    "has_feishu_webhook",
    "has_feishu_credential_data",
    "has_wecom_webhook",
    "has_wecom_bot",
    "has_wecom_bot_credential_data",
}


class _SafeDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class _WatchlistRow(_SafeDTO):
    symbol: StrictStr
    added_at: StrictStr | None = None
    note: StrictStr | None = None


class _WatchlistData(_SafeDTO):
    symbols: list[_WatchlistRow]


class _ClientPreferences(_SafeDTO):
    indices_nav_pinned: StrictBool | None = None
    watchlist_columns: list[dict[str, Any]] | None = None
    screener_result_columns: list[dict[str, Any]] | None = None
    sidebar_index_symbols: list[StrictStr] | None = None
    nav_order: list[StrictStr] | None = None
    nav_hidden: list[StrictStr] | None = None
    screener_auto_run: StrictBool | None = None
    daily_data_provider: StrictStr | None = None
    adj_factor_provider: StrictStr | None = None
    minute_data_provider: StrictStr | None = None
    realtime_data_provider: StrictStr | None = None
    financial_data_provider: StrictStr | None = None
    has_feishu_webhook: StrictBool
    has_feishu_credential_data: StrictBool
    has_wecom_webhook: StrictBool
    has_wecom_bot: StrictBool
    has_wecom_bot_credential_data: StrictBool

    @field_validator("watchlist_columns", "screener_result_columns")
    @classmethod
    def validate_columns(cls, value: object) -> object:
        if value is None:
            return value
        return preference_service._validated_column_configs(value)

    @field_validator("sidebar_index_symbols", "nav_order", "nav_hidden")
    @classmethod
    def validate_string_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any("://" in item for item in value):
            raise ValueError("preference list contains a URL")
        return value

    @field_validator(
        "daily_data_provider",
        "adj_factor_provider",
        "minute_data_provider",
        "realtime_data_provider",
        "financial_data_provider",
    )
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        if value is not None and ("://" in value or len(value) > 128):
            raise ValueError("preference provider is invalid")
        return value


class _PreferencesData(_SafeDTO):
    preferences: _ClientPreferences


class _StockReportMetadata(_SafeDTO):
    id: StrictStr
    symbol: StrictStr | None = None
    title: StrictStr | None = None
    created_at: StrictStr | None = None
    data_as_of: StrictStr | None = None
    verification_status: StrictStr | None = None
    can_publish: StrictBool | None = None
    trading_advice: StrictBool | None = None


class _StockReportsData(_SafeDTO):
    reports: list[_StockReportMetadata]


class _MarketRecapMetadata(_SafeDTO):
    id: StrictStr
    title: StrictStr | None = None
    created_at: StrictStr | None = None
    data_as_of: StrictStr | None = None
    verification_status: StrictStr | None = None
    can_publish: StrictBool | None = None
    trading_advice: StrictBool | None = None


class _MarketRecapsData(_SafeDTO):
    reports: list[_MarketRecapMetadata]


class _BacktestSummary(_SafeDTO):
    id: StrictStr
    task: StrictStr
    strategy_id: StrictStr
    parameters_digest: StrictStr
    stats: dict[StrictStr, StrictInt | StrictFloat]
    started_at: StrictStr
    finished_at: StrictStr
    data_as_of: StrictStr
    engine: StrictStr
    execution_target: StrictStr

    @model_validator(mode="after")
    def validate_bounded_summary(self) -> _BacktestSummary:
        string_values = (
            self.id,
            self.task,
            self.strategy_id,
            self.parameters_digest,
            self.started_at,
            self.finished_at,
            self.data_as_of,
            self.engine,
            self.execution_target,
        )
        if any(not value.strip() for value in string_values):
            raise ValueError("backtest summary strings must not be empty")
        if _DIGEST.fullmatch(self.parameters_digest) is None:
            raise ValueError("backtest summary digest is invalid")
        for value in self.stats.values():
            if isinstance(value, bool) or not math.isfinite(value):
                raise ValueError("backtest summary stats must be finite numbers")
        encoded = json.dumps(
            self.stats,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > 16 * 1024:
            raise ValueError("backtest summary stats are too large")
        return self


class _BacktestSummariesData(_SafeDTO):
    summaries: list[_BacktestSummary] = Field(max_length=100)


_DATA_MODELS: dict[ResourceName, type[_SafeDTO]] = {
    ResourceName.WATCHLIST: _WatchlistData,
    ResourceName.PREFERENCES: _PreferencesData,
    ResourceName.STOCK_REPORTS: _StockReportsData,
    ResourceName.MARKET_RECAPS: _MarketRecapsData,
    ResourceName.BACKTEST_SUMMARIES: _BacktestSummariesData,
}


def _is_sensitive_key(key: str, parents: tuple[str, ...]) -> bool:
    lowered = key.casefold()
    if parents == ("preferences",) and lowered in _SAFE_STATUS_KEYS:
        return False
    if lowered == "key" and parents and parents[-1] == "source":
        return False
    if lowered in {"bot_id", "botid"} or lowered.endswith("_bot_id"):
        return True
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _reject_sensitive_keys(value: Any, parents: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or _is_sensitive_key(key, parents):
                raise ValueError("cache data is not a safe workspace DTO")
            _reject_sensitive_keys(nested, (*parents, key.casefold()))
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive_keys(nested, parents)


def _validate_safe_data(resource: ResourceName, data: Any) -> dict[str, Any]:
    try:
        _reject_sensitive_keys(data)
        validated = _DATA_MODELS[resource].model_validate(data)
        payload = validated.model_dump(mode="json", exclude_unset=True)
        return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
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
        temp: Path | None = None
        fd: int | None = None
        try:
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
                with suppress(OSError):
                    os.close(fd)
            if temp is not None:
                with suppress(OSError):
                    temp.unlink(missing_ok=True)
            with suppress(OSError):
                path.unlink()

    def validate(self, snapshot: ResourceSnapshot) -> ResourceSnapshot:
        resource = ResourceName(snapshot.resource)
        data = _validate_safe_data(resource, snapshot.data)
        checksum = revision_for(data)
        if _REVISION.fullmatch(snapshot.revision) is None or snapshot.revision != checksum:
            raise ValueError("cache snapshot revision is invalid")
        return snapshot.model_copy(deep=True, update={"data": data})

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
        snapshot = self.validate(snapshot)
        resource = ResourceName(snapshot.resource)
        data = snapshot.data
        checksum = revision_for(data)
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
