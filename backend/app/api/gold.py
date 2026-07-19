"""Authenticated API surface for the isolated Gold research workspace."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile
from pydantic import BaseModel, Field

from app.services.gold_legacy_import import GoldImportError
from app.services.gold_shadow_store import GoldShadowStorageReadError

router = APIRouter(prefix="/api/gold", tags=["gold-research"])

_DISABLED_HEALTH = {
    "latest_success_at": None,
    "latest_market_date": None,
    "consecutive_failures": 0,
    "last_error": None,
}
_DISABLED_STATUS = {
    "enabled": False,
    "symbol": None,
    "latest": None,
    "latest_market_date": None,
    "health": _DISABLED_HEALTH,
    "external_send_count": 0,
}
_DISABLED_GATE = {
    "status": "collecting",
    "required_complete_trading_days": 10,
    "complete_trading_days": 0,
    "external_send_count": 0,
    "reasons": ["gold_workspace_disabled"],
}

GoldLimit = Annotated[int, Query(ge=1, le=1000)]
GoldRunId = Annotated[
    str,
    Path(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]


class GoldComparisonRequest(BaseModel):
    import_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    market_date: date


class GoldDayReviewRequest(BaseModel):
    kind: Literal["day"] = "day"
    market_date: date
    run_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    complete_window_verified: bool
    note: str = Field(min_length=1, max_length=500)


class GoldRestartReviewRequest(BaseModel):
    kind: Literal["restart"] = "restart"
    verified: bool
    note: str = Field(min_length=1, max_length=500)


GoldObservationReviewRequest = Annotated[
    GoldDayReviewRequest | GoldRestartReviewRequest,
    Field(discriminator="kind"),
]


def _state(request: Request, name: str):
    return getattr(request.app.state, name, None)


def _require_enabled_workspace(request: Request) -> None:
    if _state(request, "gold_shadow_service") is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "gold_workspace_disabled"},
        )


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "gold_storage_unavailable"},
    )


@router.get("/status")
def status(request: Request):
    service = _state(request, "gold_shadow_service")
    return service.status() if service is not None else _DISABLED_STATUS


@router.get("/health")
def health(request: Request):
    service = _state(request, "gold_shadow_service")
    if service is None:
        return {**_DISABLED_HEALTH, "next_scheduled_run": None}
    result = dict(service.status()["health"])
    scheduler = getattr(request.app.state, "scheduler", None)
    job = scheduler.get_job("gold_sampler_5m") if scheduler is not None else None
    next_run = getattr(job, "next_run_time", None)
    result["next_scheduled_run"] = next_run.isoformat() if next_run is not None else None
    return result


@router.get("/snapshots")
def snapshots(request: Request, limit: GoldLimit = 100):
    store = _state(request, "gold_store")
    if store is None:
        return {"rows": []}
    try:
        return {"rows": store.list_snapshots(limit)}
    except GoldShadowStorageReadError:
        raise _storage_unavailable() from None


@router.get("/candidates")
def candidates(request: Request, limit: GoldLimit = 100):
    store = _state(request, "gold_store")
    if store is None:
        return {"rows": []}
    try:
        return {"rows": store.list_candidates(limit)}
    except GoldShadowStorageReadError:
        raise _storage_unavailable() from None


@router.post(
    "/imports/legacy",
    status_code=201,
    dependencies=[Depends(_require_enabled_workspace)],
)
async def import_legacy(request: Request, file: Annotated[UploadFile, File()]):
    importer = _state(request, "gold_legacy_importer")
    try:
        result = importer.import_bytes(file.filename or "upload", await file.read())
    except GoldImportError as exc:
        raise HTTPException(status_code=400, detail={"code": str(exc)}) from None
    except GoldShadowStorageReadError:
        raise _storage_unavailable() from None
    return {
        key: getattr(result, key)
        for key in ("import_id", "sha256", "filename", "imported_at", "sample_count")
    }


@router.get("/imports")
def imports(request: Request, limit: GoldLimit = 100):
    store = _state(request, "gold_store")
    if store is None:
        return {"rows": []}
    try:
        return {"rows": store.list_imports(limit)}
    except GoldShadowStorageReadError:
        raise _storage_unavailable() from None


@router.post(
    "/comparisons",
    dependencies=[Depends(_require_enabled_workspace)],
)
def create_comparison(request: Request, payload: GoldComparisonRequest):
    runner = _state(request, "gold_comparison_runner")
    try:
        return runner.run(payload.import_id, payload.market_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "gold_comparison_invalid", "message": str(exc)},
        ) from None
    except GoldShadowStorageReadError:
        raise _storage_unavailable() from None


@router.get("/comparisons")
def comparisons(request: Request, limit: GoldLimit = 100):
    runner = _state(request, "gold_comparison_runner")
    if runner is None:
        return {"rows": []}
    try:
        return {"rows": runner.list_runs(limit)}
    except GoldShadowStorageReadError:
        raise _storage_unavailable() from None


@router.get("/comparisons/{run_id}")
def comparison(request: Request, run_id: GoldRunId):
    runner = _state(request, "gold_comparison_runner")
    if runner is None:
        raise HTTPException(status_code=404, detail={"code": "gold_comparison_not_found"})
    try:
        result = runner.get_run(run_id)
    except GoldShadowStorageReadError:
        raise _storage_unavailable() from None
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "gold_comparison_not_found"})
    return result


@router.get("/observation-gate")
def observation_gate(request: Request):
    service = _state(request, "gold_observation_service")
    return service.status() if service is not None else _DISABLED_GATE


@router.post(
    "/observation-reviews",
    dependencies=[Depends(_require_enabled_workspace)],
)
def review_observation(request: Request, payload: GoldObservationReviewRequest):
    service = _state(request, "gold_observation_service")
    try:
        if isinstance(payload, GoldDayReviewRequest):
            service.review_day(
                payload.market_date,
                payload.run_id,
                payload.complete_window_verified,
                payload.note,
            )
        else:
            service.review_restart(payload.verified, payload.note)
        return service.status()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "gold_observation_review_invalid", "message": str(exc)},
        ) from None
    except GoldShadowStorageReadError:
        raise _storage_unavailable() from None
