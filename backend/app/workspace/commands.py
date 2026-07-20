"""Conditional commands for the bounded cloud workspace resources."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.services import (
    backtest_summaries,
    market_recap_reports,
    preferences,
    stock_reports,
    watchlist,
)
from app.workspace.locks import resource_lock
from app.workspace.models import (
    ResourceName,
    ResourceSnapshot,
    WorkspaceCommandConflict,
    WorkspacePreconditionRequired,
    WorkspaceRevisionConflict,
)
from app.workspace.registry import snapshot_resource

_REVISION_PATTERN = re.compile(r"[0-9a-f]{64}")
_FRONTMATTER_PATTERN = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_REQUIRED_VERIFICATION = [
    "latest_close",
    "key_levels",
    "technical_indicators",
    "financial_data_availability",
    "material_news",
    "corporate_actions",
]


class _StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


def _normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not symbol:
        raise ValueError("watchlist symbol must not be empty")
    return symbol


class _WatchlistAddPayload(_StrictPayload):
    symbol: StrictStr
    note: StrictStr = ""

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)


class _WatchlistBatchAddPayload(_StrictPayload):
    symbols: list[StrictStr] = Field(min_length=1)
    note: StrictStr = ""

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            symbol = _normalize_symbol(value)
            if symbol not in seen:
                seen.add(symbol)
                normalized.append(symbol)
        return normalized


class _WatchlistSymbolPayload(_StrictPayload):
    symbol: StrictStr

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)


class _EmptyPayload(_StrictPayload):
    pass


class _DeletePayload(_StrictPayload):
    id: StrictStr

    @field_validator("id")
    @classmethod
    def require_id(cls, value: str) -> str:
        identifier = value.strip()
        if not identifier:
            raise ValueError("report id must not be empty")
        return identifier


class _StockReportAppendPayload(_StrictPayload):
    symbol: StrictStr
    name: StrictStr = ""
    focus: StrictStr = ""
    title: StrictStr
    content: StrictStr
    summary: StrictStr = ""
    close: float | None = None
    levels: dict[str, Any] | None = None
    data_as_of: StrictStr
    type: Literal["stock-analysis"]
    source_system: Literal["tickflow-stock-panel"]
    data_scope: Literal["watchlist-sample"]
    can_publish: Literal[False]
    trading_advice: Literal[False]
    verification_status: Literal["pending"]
    needs_verification: list[StrictStr]
    timeframe: Literal["1d"]

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)

    @field_validator("title", "content", "data_as_of")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required report text must not be empty")
        return value

    @field_validator("needs_verification")
    @classmethod
    def require_verification_fields(cls, value: list[str]) -> list[str]:
        if value != _REQUIRED_VERIFICATION:
            raise ValueError("stock report needs_verification list is incomplete")
        return value


class _MarketRecapAppendPayload(_StrictPayload):
    as_of: StrictStr
    focus: StrictStr = ""
    content: StrictStr
    summary: StrictStr = ""
    emotion_score: int | None = Field(default=None, ge=0, le=100)
    emotion_label: StrictStr = ""
    title: StrictStr
    data_as_of: StrictStr
    verification_status: Literal["pending"]
    can_publish: Literal[False]
    trading_advice: Literal[False]

    @field_validator("as_of", "content", "title", "data_as_of")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required market recap text must not be empty")
        return value


def _model_payload(model: type[_StrictPayload], payload: dict[str, Any]) -> dict[str, Any]:
    return model.model_validate(copy.deepcopy(payload)).model_dump(mode="python")


def _watchlist_rows_for_front(symbols: list[str], note: str) -> list[dict[str, Any]]:
    requested = [{"symbol": symbol, "note": note} for symbol in symbols]
    requested_set = set(symbols)
    remaining = [
        copy.deepcopy(row)
        for row in watchlist.list_symbols()
        if _normalize_symbol(str(row.get("symbol") or "")) not in requested_set
    ]
    return requested + remaining


def _watchlist_add(payload: dict[str, Any]) -> None:
    data = _model_payload(_WatchlistAddPayload, payload)
    watchlist.replace_all(_watchlist_rows_for_front([data["symbol"]], data["note"]))


def _watchlist_batch_add(payload: dict[str, Any]) -> None:
    data = _model_payload(_WatchlistBatchAddPayload, payload)
    watchlist.replace_all(_watchlist_rows_for_front(data["symbols"], data["note"]))


def _watchlist_remove(payload: dict[str, Any]) -> None:
    data = _model_payload(_WatchlistSymbolPayload, payload)
    watchlist.remove(data["symbol"])


def _watchlist_move_to_top(payload: dict[str, Any]) -> None:
    data = _model_payload(_WatchlistSymbolPayload, payload)
    watchlist.move_to_top(data["symbol"])


def _watchlist_clear(payload: dict[str, Any]) -> None:
    _model_payload(_EmptyPayload, payload)
    watchlist.clear()


def _preferences_merge_safe(payload: dict[str, Any]) -> None:
    preferences.merge_client_preferences(copy.deepcopy(payload))


def _stock_report_frontmatter(content: str) -> dict[str, Any]:
    match = _FRONTMATTER_PATTERN.match(content)
    if match is None:
        raise ValueError("stock report Markdown must begin with YAML frontmatter")
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError("stock report frontmatter is invalid YAML") from exc
    if not isinstance(frontmatter, dict):
        raise ValueError("stock report frontmatter must be an object")
    return frontmatter


def _stock_report_append(payload: dict[str, Any]) -> None:
    data = _model_payload(_StockReportAppendPayload, payload)
    frontmatter = _stock_report_frontmatter(data["content"])
    safety_fields = (
        "type",
        "source_system",
        "data_scope",
        "can_publish",
        "trading_advice",
        "verification_status",
        "needs_verification",
        "timeframe",
    )
    for field in safety_fields:
        if frontmatter.get(field) != data[field]:
            raise ValueError(f"stock report frontmatter {field} does not match payload")
    stock_reports.save_report(data)


def _market_recap_append(payload: dict[str, Any]) -> None:
    data = _model_payload(_MarketRecapAppendPayload, payload)
    market_recap_reports.save_report(data)


def _backtest_summary_append(payload: dict[str, Any]) -> None:
    backtest_summaries.append(copy.deepcopy(payload))


def _delete(
    resource: ResourceName,
    operation: str,
    payload: dict[str, Any],
    delete: Callable[[str], bool],
) -> None:
    data = _model_payload(_DeletePayload, payload)
    if not delete(data["id"]):
        raise WorkspaceCommandConflict(resource, operation, "report not found")


def _stock_report_delete(payload: dict[str, Any]) -> None:
    _delete(ResourceName.STOCK_REPORTS, "delete", payload, stock_reports.delete_report)


def _market_recap_delete(payload: dict[str, Any]) -> None:
    _delete(ResourceName.MARKET_RECAPS, "delete", payload, market_recap_reports.delete_report)


def _backtest_summary_delete(payload: dict[str, Any]) -> None:
    _delete(ResourceName.BACKTEST_SUMMARIES, "delete", payload, backtest_summaries.delete)


CommandHandler = Callable[[dict[str, Any]], None]

_COMMANDS: dict[ResourceName, dict[str, CommandHandler]] = {
    ResourceName.WATCHLIST: {
        "add": _watchlist_add,
        "batch_add": _watchlist_batch_add,
        "remove": _watchlist_remove,
        "move_to_top": _watchlist_move_to_top,
        "clear": _watchlist_clear,
    },
    ResourceName.PREFERENCES: {"merge_safe": _preferences_merge_safe},
    ResourceName.STOCK_REPORTS: {
        "append": _stock_report_append,
        "delete": _stock_report_delete,
    },
    ResourceName.MARKET_RECAPS: {
        "append": _market_recap_append,
        "delete": _market_recap_delete,
    },
    ResourceName.BACKTEST_SUMMARIES: {
        "append": _backtest_summary_append,
        "delete": _backtest_summary_delete,
    },
}


def _resource_name(value: ResourceName | str) -> ResourceName:
    try:
        return ResourceName(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown workspace resource: {value!r}") from exc


def _expected_revision(resource: ResourceName, value: str | None) -> str:
    if value is None or value == "":
        raise WorkspacePreconditionRequired(resource)
    if not isinstance(value, str) or _REVISION_PATTERN.fullmatch(value) is None:
        raise ValueError("expected revision must be a 64-character lowercase hexadecimal digest")
    return value


def execute_command(
    resource: ResourceName | str,
    operation: str,
    payload: dict[str, Any],
    expected_revision: str | None,
) -> ResourceSnapshot:
    """Compare and execute one fixed workspace command under its resource lock."""
    resource_name = _resource_name(resource)
    revision = _expected_revision(resource_name, expected_revision)
    if not isinstance(payload, dict):
        raise ValueError("workspace command payload must be an object")
    handler = _COMMANDS[resource_name].get(operation) if isinstance(operation, str) else None
    if handler is None:
        raise WorkspaceCommandConflict(resource_name, str(operation), "unsupported operation")
    safe_payload = copy.deepcopy(payload)

    with resource_lock(resource_name):
        current = snapshot_resource(resource_name)
        if current.revision != revision:
            raise WorkspaceRevisionConflict(resource_name, current.revision)
        handler(safe_payload)
        return snapshot_resource(resource_name)
