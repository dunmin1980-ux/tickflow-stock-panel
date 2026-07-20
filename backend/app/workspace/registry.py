"""Registry for the bounded resources shared by cloud and private clients."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.services import (
    backtest_summaries,
    market_recap_reports,
    preferences,
    stock_reports,
    watchlist,
)
from app.workspace.locks import resource_lock
from app.workspace.models import ResourceName, ResourceSnapshot
from app.workspace.revision import revision_for

ResourceLoader = Callable[[], dict]
ResourcePath = Callable[[], Path]

LOADERS: dict[ResourceName, ResourceLoader] = {
    ResourceName.WATCHLIST: lambda: {"symbols": watchlist.list_symbols()},
    ResourceName.PREFERENCES: lambda: {
        "preferences": preferences.load_client_preferences(),
    },
    ResourceName.STOCK_REPORTS: lambda: {
        "reports": stock_reports.list_report_metadata(),
    },
    ResourceName.MARKET_RECAPS: lambda: {
        "reports": market_recap_reports.list_report_metadata(),
    },
    ResourceName.BACKTEST_SUMMARIES: lambda: {
        "summaries": backtest_summaries.list_summaries(),
    },
}

RESOURCE_PATHS: dict[ResourceName, ResourcePath] = {
    ResourceName.WATCHLIST: lambda: settings.data_dir / "user_data" / "watchlist.parquet",
    ResourceName.PREFERENCES: lambda: settings.data_dir / "user_data" / "preferences.json",
    ResourceName.STOCK_REPORTS: lambda: settings.data_dir / "user_data" / "ai_stock_reports.json",
    ResourceName.MARKET_RECAPS: lambda: settings.data_dir / "user_data" / "ai_market_recaps.json",
    ResourceName.BACKTEST_SUMMARIES: lambda: (
        settings.data_dir / "user_data" / "backtest_summaries.json"
    ),
}


def _resource_name(name: ResourceName | str) -> ResourceName:
    try:
        return ResourceName(name)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown workspace resource: {name!r}") from exc


def load_resource(name: ResourceName | str) -> dict:
    resource = _resource_name(name)
    with resource_lock(resource):
        return LOADERS[resource]()


def resource_mtime(name: ResourceName | str) -> datetime:
    resource = _resource_name(name)
    with resource_lock(resource):
        path = RESOURCE_PATHS[resource]()
        timestamp = path.stat().st_mtime if path.exists() else 0
        return datetime.fromtimestamp(timestamp, UTC)


def snapshot_resource(name: ResourceName | str) -> ResourceSnapshot:
    resource = _resource_name(name)
    with resource_lock(resource):
        data = LOADERS[resource]()
        return ResourceSnapshot(
            resource=resource,
            revision=revision_for(data),
            updated_at=resource_mtime(resource),
            data=data,
        )


def replace_watchlist(rows: list[dict]) -> list[dict]:
    with resource_lock(ResourceName.WATCHLIST):
        return watchlist.replace_all(rows)
