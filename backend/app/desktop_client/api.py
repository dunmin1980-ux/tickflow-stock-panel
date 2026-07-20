"""Loopback API used by the packaged desktop shell."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.desktop_client.config import ClientConfig, load_client_config, save_client_config
from app.desktop_client.remote import RemoteAuthError, RemoteClient

router = APIRouter(prefix="/api/client", tags=["desktop-client"])


class ClientConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote_base_url: str
    preferred_mode: str = "hybrid"


class ClientLoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=128)


def _remote_for(config: ClientConfig) -> RemoteClient:
    return RemoteClient(config.remote_base_url)


def _require_config() -> ClientConfig:
    try:
        config = load_client_config(settings.data_dir)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="desktop client config is invalid") from exc
    if config is None:
        raise HTTPException(status_code=409, detail="cloud workspace is not configured")
    return config


@router.get("/config")
def get_client_config() -> dict[str, str]:
    config = load_client_config(settings.data_dir)
    if config is None:
        return {"remote_base_url": "", "preferred_mode": "hybrid"}
    return {
        "remote_base_url": config.remote_base_url,
        "preferred_mode": config.preferred_mode,
    }


@router.put("/config")
def put_client_config(payload: ClientConfigPayload) -> dict[str, str]:
    try:
        config = save_client_config(settings.data_dir, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "remote_base_url": config.remote_base_url,
        "preferred_mode": config.preferred_mode,
    }


@router.get("/status")
def get_client_status() -> dict[str, object]:
    try:
        config = load_client_config(settings.data_dir)
    except ValueError:
        return {
            "configured": False,
            "authenticated": False,
            "reachable": False,
            "mode": "hybrid",
            "error_code": "INVALID_CONFIG",
        }
    if config is None:
        return {
            "configured": False,
            "authenticated": False,
            "reachable": False,
            "mode": "hybrid",
            "error_code": "NOT_CONFIGURED",
        }
    try:
        remote_status = _remote_for(config).auth_status()
    except httpx.HTTPStatusError:
        return {
            "configured": True,
            "authenticated": False,
            "reachable": True,
            "mode": config.preferred_mode,
            "error_code": "REMOTE_HTTP_ERROR",
        }
    except (httpx.HTTPError, OSError, ValueError):
        return {
            "configured": True,
            "authenticated": False,
            "reachable": False,
            "mode": config.preferred_mode,
            "error_code": "UNREACHABLE",
        }
    return {
        "configured": True,
        "authenticated": remote_status.get("authenticated") is True,
        "reachable": True,
        "mode": config.preferred_mode,
        "error_code": None,
    }


@router.post("/auth/login")
def client_login(payload: ClientLoginPayload) -> dict[str, bool]:
    config = _require_config()
    try:
        _remote_for(config).login(payload.password)
    except httpx.HTTPStatusError as exc:
        status = 401 if exc.response.status_code in {401, 403} else 502
        raise HTTPException(status_code=status, detail="cloud authentication failed") from exc
    except (httpx.HTTPError, OSError, RemoteAuthError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="cloud authentication unavailable") from exc
    return {"authenticated": True}


@router.post("/auth/logout")
def client_logout() -> dict[str, bool]:
    config = load_client_config(settings.data_dir)
    if config is not None:
        _remote_for(config).logout()
    return {"authenticated": False}
