"""Route supported desktop compute without broad exception fallbacks."""

from __future__ import annotations

import logging
import os
import stat
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

logger = logging.getLogger(__name__)

ComputeTask = Literal["strategy_backtest", "screener"]
ExecutionTarget = Literal["local", "cloud"]

_ALLOWED_TASKS = frozenset({"strategy_backtest", "screener"})
_INFRASTRUCTURE_ERROR_CODES = frozenset(
    {
        "compute_input_download_timeout",
        "compute_input_transport_unavailable",
        "local_repo_init_failed",
        "native_library_unavailable",
        "worker_exit_failed",
        "worker_start_failed",
    }
)


class LocalComputeUnavailable(RuntimeError):  # noqa: N818 - public routing contract
    """A bounded local infrastructure failure that permits one cloud attempt."""

    def __init__(self, error_code: str, *, detail: str | None = None) -> None:
        if error_code not in _INFRASTRUCTURE_ERROR_CODES:
            raise ValueError("unsupported local infrastructure error")
        super().__init__("local compute is unavailable")
        self.error_code = error_code
        del detail


@dataclass(frozen=True)
class ComputeResult:
    value: dict[str, Any]
    execution_target: ExecutionTarget


def _result(value: Any, target: ExecutionTarget) -> ComputeResult:
    if not isinstance(value, dict):
        raise TypeError("compute result must be an object")
    return ComputeResult(value=value, execution_target=target)


class ComputeRouter:
    """Prefer local compute and permit exactly one explicit cloud fallback."""

    def __init__(self, cloud_enabled: bool) -> None:
        self.cloud_enabled = bool(cloud_enabled)

    @staticmethod
    def _log(task_id: str, target: ExecutionTarget, started: float, error_code: str) -> None:
        logger.info(
            "compute task_id=%s target=%s elapsed_ms=%d error_code=%s",
            task_id,
            target,
            int((time.monotonic() - started) * 1000),
            error_code,
        )

    def execute(
        self,
        task: str,
        local: Callable[[], dict[str, Any]],
        cloud: Callable[[], dict[str, Any]],
    ) -> ComputeResult:
        if task not in _ALLOWED_TASKS:
            raise ValueError("unsupported compute task")

        task_id = uuid.uuid4().hex
        local_started = time.monotonic()
        try:
            value = local()
        except LocalComputeUnavailable as exc:
            self._log(task_id, "local", local_started, exc.error_code)
            if not self.cloud_enabled:
                raise
        else:
            result = _result(value, "local")
            self._log(task_id, "local", local_started, "none")
            return result

        cloud_started = time.monotonic()
        value = cloud()
        result = _result(value, "cloud")
        self._log(task_id, "cloud", cloud_started, "none")
        return result


def prepare_local_compute_input(
    adapter: Any,
    task: ComputeTask,
    config: dict[str, Any],
) -> Path:
    """Convert only authenticated bundle transport failures into fallback signals."""
    try:
        return Path(adapter.prepare_compute_input(task, config))
    except httpx.TimeoutException as exc:
        raise LocalComputeUnavailable("compute_input_download_timeout") from exc
    except httpx.RequestError as exc:
        raise LocalComputeUnavailable("compute_input_transport_unavailable") from exc


def run_local_worker(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Translate only worker failures already classified as infrastructure."""
    from app.backtest.worker import BacktestWorkerInfrastructureError

    try:
        return operation()
    except BacktestWorkerInfrastructureError as exc:
        raise LocalComputeUnavailable(exc.error_code) from exc


def open_read_only_compute_repository(data_dir: Path):
    """Open a verified snapshot through a disposable writable cache overlay."""
    from app.desktop_client.workspace_adapter import ComputeInputIntegrityError

    try:
        from app.tickflow.repository import DataStore, KlineRepository
    except (ImportError, OSError) as exc:
        raise LocalComputeUnavailable("native_library_unavailable") from exc

    snapshot = Path(data_dir)
    for root_name in (
        "adj_factor",
        "instruments",
        "kline_daily",
        "kline_daily_enriched",
    ):
        source = snapshot / root_name
        if source.is_symlink() or (source.exists() and not source.is_dir()):
            raise ComputeInputIntegrityError("compute input dataset root is invalid")
    overlay = None
    try:
        overlay = tempfile.TemporaryDirectory(prefix="tickflow-local-compute-")
        overlay_root = Path(overlay.name)
        for root_name in (
            "adj_factor",
            "instruments",
            "kline_daily",
            "kline_daily_enriched",
        ):
            source = snapshot / root_name
            if source.exists():
                (overlay_root / root_name).symlink_to(source, target_is_directory=True)
        store = DataStore(overlay_root)
        store._compute_overlay = overlay
        repo = KlineRepository(store)
    except Exception as exc:
        if overlay is not None:
            overlay.cleanup()
        raise LocalComputeUnavailable("local_repo_init_failed") from exc
    return store, repo


def load_compute_input_manifest(
    data_dir: Path,
    task: ComputeTask,
    config: dict[str, Any],
):
    """Re-bind the published directory to the task digest before execution."""
    from app.desktop_client.workspace_adapter import ComputeInputIntegrityError
    from app.services.compute_input_bundle import (
        ComputeInputManifest,
        compute_parameters_digest,
        normalize_compute_config,
    )

    manifest_path = Path(data_dir) / "manifest.json"
    fd = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(manifest_path, flags)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
            raise ComputeInputIntegrityError("compute input manifest file is invalid")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            manifest = ComputeInputManifest.model_validate_json(handle.read())
        normalized = normalize_compute_config(task, config)
        if (
            manifest.task != task
            or manifest.parameters_digest != compute_parameters_digest(normalized)
        ):
            raise ComputeInputIntegrityError("compute input execution contract does not match")
        required_roots = (
            ("adj_factor", "instruments", "kline_daily", "kline_daily_enriched")
            if task == "strategy_backtest"
            else ("instruments", "kline_daily_enriched")
        )
        for root_name in required_roots:
            root = Path(data_dir) / root_name
            if root.is_symlink() or not root.is_dir():
                raise ComputeInputIntegrityError("compute input dataset root is invalid")
        return manifest
    except ComputeInputIntegrityError:
        raise
    except (OSError, TypeError, ValueError, ValidationError) as exc:
        raise ComputeInputIntegrityError("compute input manifest cannot be rebound") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def request_cloud_json(
    adapter: Any,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Make one authenticated cloud call while preserving its HTTP status."""
    remote_provider = getattr(adapter, "_remote", None)
    if not callable(remote_provider):
        raise RuntimeError("cloud compute client is unavailable")
    response = remote_provider().request("POST", path, json=payload)
    if response.status_code >= 400:
        detail: Any = "Cloud compute request failed"
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("detail"), (str, dict, list)):
                detail = body["detail"]
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        value = response.json()
    except ValueError as exc:
        raise ValueError("cloud compute response is not JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("cloud compute response must be an object")
    return value
