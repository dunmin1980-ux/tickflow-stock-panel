from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ResourceName(StrEnum):
    WATCHLIST = "watchlist"
    PREFERENCES = "preferences"
    STOCK_REPORTS = "stock_reports"
    MARKET_RECAPS = "market_recaps"
    BACKTEST_SUMMARIES = "backtest_summaries"


class ResourceSnapshot(BaseModel):
    resource: ResourceName
    revision: str
    updated_at: datetime
    data: dict[str, Any]


class WorkspacePreconditionRequired(RuntimeError):  # noqa: N818 - public contract
    pass


class WorkspaceRevisionConflict(RuntimeError):  # noqa: N818 - public contract
    def __init__(self, resource: ResourceName, current_revision: str) -> None:
        super().__init__(f"stale revision for {resource}")
        self.resource = resource
        self.current_revision = current_revision
