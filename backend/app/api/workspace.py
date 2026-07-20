"""Authenticated, versioned API for the bounded private workspace."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Generic, TypeVar
from zoneinfo import ZoneInfo

from fastapi import APIRouter, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from app.config import settings
from app.tickflow.capabilities import CapabilitySet
from app.tickflow.policy import tier_label
from app.workspace.commands import execute_command
from app.workspace.models import (
    ResourceName,
    ResourceSnapshot,
    WorkspaceCommandConflict,
    WorkspacePreconditionRequired,
    WorkspaceRevisionConflict,
)
from app.workspace.registry import snapshot_resource
from app.workspace.revision import etag_for, parse_if_match

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

_PRIVATE_NO_STORE = "private, no-store"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WatchlistSymbolDTO(_DTO):
    symbol: str
    added_at: str | None = None
    note: str | None = None


class WatchlistDataDTO(_DTO):
    symbols: list[WatchlistSymbolDTO]


class ClientPreferencesDTO(_DTO):
    indices_nav_pinned: bool | None = None
    watchlist_columns: list[str] | None = None
    screener_result_columns: list[str] | None = None
    sidebar_index_symbols: list[str] | None = None
    nav_order: list[str] | None = None
    nav_hidden: list[str] | None = None
    screener_auto_run: bool | None = None
    daily_data_provider: str | None = None
    adj_factor_provider: str | None = None
    minute_data_provider: str | None = None
    realtime_data_provider: str | None = None
    financial_data_provider: str | None = None
    has_feishu_webhook: bool
    has_wecom_webhook: bool
    has_wecom_bot: bool


class PreferencesDataDTO(_DTO):
    preferences: ClientPreferencesDTO


class StockReportMetadataDTO(_DTO):
    id: str
    symbol: str | None = None
    title: str | None = None
    created_at: str | None = None
    data_as_of: str | None = None
    verification_status: str | None = None
    can_publish: bool | None = None
    trading_advice: bool | None = None


class StockReportsDataDTO(_DTO):
    reports: list[StockReportMetadataDTO]


class MarketRecapMetadataDTO(_DTO):
    id: str
    title: str | None = None
    created_at: str | None = None
    data_as_of: str | None = None
    verification_status: str | None = None
    can_publish: bool | None = None
    trading_advice: bool | None = None


class MarketRecapsDataDTO(_DTO):
    reports: list[MarketRecapMetadataDTO]


class BacktestSummaryDTO(_DTO):
    id: str
    task: str
    strategy_id: str
    parameters_digest: str
    stats: dict[str, int | float]
    started_at: str
    finished_at: str
    data_as_of: str
    engine: str
    execution_target: str


class BacktestSummariesDataDTO(_DTO):
    summaries: list[BacktestSummaryDTO]


ResourceDataDTO = (
    WatchlistDataDTO
    | PreferencesDataDTO
    | StockReportsDataDTO
    | MarketRecapsDataDTO
    | BacktestSummariesDataDTO
)
ResourceDataT = TypeVar("ResourceDataT", bound=ResourceDataDTO)


class ResourceSnapshotDTO(_DTO, Generic[ResourceDataT]):
    revision: str
    updated_at: datetime
    data: ResourceDataT


class ResourceResponseDTO(ResourceSnapshotDTO[ResourceDataDTO]):
    resource: ResourceName


class WorkspaceResourcesDTO(_DTO):
    watchlist: ResourceSnapshotDTO[WatchlistDataDTO]
    preferences: ResourceSnapshotDTO[PreferencesDataDTO]
    stock_reports: ResourceSnapshotDTO[StockReportsDataDTO]
    market_recaps: ResourceSnapshotDTO[MarketRecapsDataDTO]
    backtest_summaries: ResourceSnapshotDTO[BacktestSummariesDataDTO]


class CapabilityLimitDTO(_DTO):
    rpm: int | None = None
    batch: int | None = None
    subscribe: int | None = None


class CapabilitiesDTO(_DTO):
    label: str
    capabilities: dict[str, CapabilityLimitDTO]


class WorkspaceBootstrapDTO(_DTO):
    schema_version: int
    server_time: datetime
    data_as_of: str | None
    mode: str
    capabilities: CapabilitiesDTO
    resources: WorkspaceResourcesDTO


class WorkspaceRevisionsDTO(_DTO):
    revisions: dict[ResourceName, str]


class WorkspaceCommandDTO(_DTO):
    operation: StrictStr
    payload: dict[str, Any]


_DATA_MODELS: dict[ResourceName, type[_DTO]] = {
    ResourceName.WATCHLIST: WatchlistDataDTO,
    ResourceName.PREFERENCES: PreferencesDataDTO,
    ResourceName.STOCK_REPORTS: StockReportsDataDTO,
    ResourceName.MARKET_RECAPS: MarketRecapsDataDTO,
    ResourceName.BACKTEST_SUMMARIES: BacktestSummariesDataDTO,
}


def _snapshot_dto(snapshot: ResourceSnapshot, *, include_resource: bool) -> dict[str, Any]:
    data = _DATA_MODELS[snapshot.resource].model_validate(snapshot.data)
    payload: dict[str, Any] = {
        "revision": snapshot.revision,
        "updated_at": snapshot.updated_at,
        "data": data.model_dump(exclude_none=True),
    }
    if include_resource:
        payload["resource"] = snapshot.resource
    return payload


def _private_json(payload: Any, *, etag: str | None = None, status_code: int = 200) -> JSONResponse:
    headers = {"Cache-Control": _PRIVATE_NO_STORE}
    if etag is not None:
        headers["ETag"] = etag
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(payload),
        headers=headers,
    )


def _data_as_of(request: Request) -> str | None:
    try:
        result = request.app.state.repo.get_enriched_latest()
        if not isinstance(result, tuple) or len(result) < 2:
            return None
        value = result[1]
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            return date.fromisoformat(value).isoformat()
    except Exception:
        return None
    return None


def _capabilities(request: Request) -> CapabilitiesDTO:
    capset = getattr(request.app.state, "capabilities", None)
    if not isinstance(capset, CapabilitySet):
        capset = CapabilitySet()
    try:
        label = tier_label()
    except Exception:
        label = "Unknown"
    return CapabilitiesDTO(label=label, capabilities=capset.to_dict())


def _all_snapshots() -> dict[ResourceName, ResourceSnapshot]:
    return {resource: snapshot_resource(resource) for resource in ResourceName}


@router.get(
    "/bootstrap",
    response_model=WorkspaceBootstrapDTO,
    response_model_exclude_none=True,
)
def bootstrap(request: Request) -> JSONResponse:
    snapshots = _all_snapshots()
    resources = WorkspaceResourcesDTO(
        watchlist=_snapshot_dto(snapshots[ResourceName.WATCHLIST], include_resource=False),
        preferences=_snapshot_dto(snapshots[ResourceName.PREFERENCES], include_resource=False),
        stock_reports=_snapshot_dto(snapshots[ResourceName.STOCK_REPORTS], include_resource=False),
        market_recaps=_snapshot_dto(snapshots[ResourceName.MARKET_RECAPS], include_resource=False),
        backtest_summaries=_snapshot_dto(
            snapshots[ResourceName.BACKTEST_SUMMARIES], include_resource=False
        ),
    )
    payload = WorkspaceBootstrapDTO(
        schema_version=1,
        server_time=datetime.now(_SHANGHAI),
        data_as_of=_data_as_of(request),
        mode="cloud",
        capabilities=_capabilities(request),
        resources=resources,
    )
    serialized = payload.model_dump(exclude_none=True)
    # The bootstrap contract distinguishes "no cache date" from a missing field.
    serialized["data_as_of"] = payload.data_as_of
    return _private_json(serialized)


@router.get(
    "/resources/{name}",
    response_model=ResourceResponseDTO,
    response_model_exclude_none=True,
)
def get_resource(name: ResourceName) -> JSONResponse:
    snapshot = snapshot_resource(name)
    payload = ResourceResponseDTO.model_validate(_snapshot_dto(snapshot, include_resource=True))
    return _private_json(
        payload.model_dump(exclude_none=True),
        etag=etag_for(snapshot.revision),
    )


@router.get("/revisions", response_model=WorkspaceRevisionsDTO)
def revisions() -> JSONResponse:
    payload = WorkspaceRevisionsDTO(
        revisions={resource: snapshot_resource(resource).revision for resource in ResourceName}
    )
    return _private_json(payload.model_dump())


def _command_error(code: str, detail: str, status_code: int) -> JSONResponse:
    return _private_json({"detail": detail, "code": code}, status_code=status_code)


@router.post(
    "/resources/{name}/commands",
    response_model=ResourceResponseDTO,
    response_model_exclude_none=True,
)
async def command(name: ResourceName, request: Request) -> JSONResponse:
    if not settings.workspace_sync_enabled:
        return _command_error(
            "WORKSPACE_SYNC_DISABLED",
            "Versioned workspace writes are disabled",
            503,
        )

    if_match = request.headers.get("if-match")
    if if_match is None:
        raise WorkspacePreconditionRequired(name)
    try:
        expected_revision = parse_if_match(if_match)
    except ValueError:
        return _command_error("INVALID_IF_MATCH", "If-Match must be one strong ETag", 422)

    try:
        raw = await request.json()
        command_dto = WorkspaceCommandDTO.model_validate(raw)
        snapshot = execute_command(
            name,
            command_dto.operation,
            command_dto.payload,
            expected_revision,
        )
    except (TypeError, ValueError, ValidationError):
        return _command_error(
            "INVALID_WORKSPACE_COMMAND",
            "Workspace command is invalid",
            422,
        )

    payload = ResourceResponseDTO.model_validate(_snapshot_dto(snapshot, include_resource=True))
    return _private_json(
        payload.model_dump(exclude_none=True),
        etag=etag_for(snapshot.revision),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(WorkspacePreconditionRequired)
    async def precondition_required_handler(
        _request: Request, _exc: WorkspacePreconditionRequired
    ) -> JSONResponse:
        return _command_error(
            "WORKSPACE_PRECONDITION_REQUIRED",
            "A strong If-Match header is required",
            428,
        )

    @app.exception_handler(WorkspaceRevisionConflict)
    async def revision_conflict_handler(
        _request: Request, exc: WorkspaceRevisionConflict
    ) -> JSONResponse:
        response = _command_error(
            "WORKSPACE_REVISION_CONFLICT",
            "Workspace revision is stale",
            412,
        )
        response.headers["ETag"] = etag_for(exc.current_revision)
        return response

    @app.exception_handler(WorkspaceCommandConflict)
    async def command_conflict_handler(
        _request: Request, _exc: WorkspaceCommandConflict
    ) -> JSONResponse:
        return _command_error(
            "WORKSPACE_COMMAND_CONFLICT",
            "Workspace command conflicts with current state",
            409,
        )


_LEGACY_FIXED_MUTATIONS = frozenset(
    {
        ("POST", "/api/watchlist"),
        ("POST", "/api/watchlist/batch"),
        ("POST", "/api/watchlist/import-image"),
        ("DELETE", "/api/watchlist"),
        ("PUT", "/api/settings/preferences/data-providers"),
        ("PUT", "/api/settings/preferences/nav-order"),
        ("PUT", "/api/settings/preferences/nav-hidden"),
        ("PUT", "/api/settings/preferences/watchlist-columns"),
        ("PUT", "/api/settings/preferences/screener-result-columns"),
        ("PUT", "/api/settings/preferences/indices-nav-pinned"),
        ("PUT", "/api/settings/preferences/realtime-monitor"),
        ("POST", "/api/stock-analysis/analyze"),
        ("POST", "/api/stock-analysis/reports"),
        ("POST", "/api/market-recap/analyze"),
        ("POST", "/api/market-recap/reports"),
    }
)
_LEGACY_DYNAMIC_MUTATIONS = (
    ("POST", re.compile(r"/api/watchlist/[^/]+/top")),
    ("DELETE", re.compile(r"/api/watchlist/[^/]+")),
    ("DELETE", re.compile(r"/api/stock-analysis/reports/[^/]+")),
    ("DELETE", re.compile(r"/api/market-recap/reports/[^/]+")),
)


def legacy_workspace_write_response(request: Request) -> JSONResponse | None:
    """Fail before parsing or side effects for every migrated legacy write class."""
    if not settings.workspace_sync_enabled:
        return None
    method_path = (request.method.upper(), request.url.path)
    blocked = method_path in _LEGACY_FIXED_MUTATIONS or any(
        method_path[0] == method and pattern.fullmatch(method_path[1])
        for method, pattern in _LEGACY_DYNAMIC_MUTATIONS
    )
    if not blocked:
        return None
    return _command_error(
        "LEGACY_WORKSPACE_WRITE_DISABLED",
        "Legacy workspace write is disabled while versioned sync is enabled",
        409,
    )
