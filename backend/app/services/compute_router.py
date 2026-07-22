"""Route supported desktop compute without broad exception fallbacks."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
        "worker_start_failed",
    }
)
_DATA_ROOTS = frozenset(
    {
        "adj_factor",
        "instruments",
        "kline_daily",
        "kline_daily_enriched",
    }
)
_TASK_REQUIRED_ROOTS = {
    "strategy_backtest": (
        "adj_factor",
        "instruments",
        "kline_daily",
        "kline_daily_enriched",
    ),
    "screener": ("instruments", "kline_daily_enriched"),
}


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


@dataclass
class ComputeInputLease:
    """One execution's immutable copy of a verified compute-input generation."""

    path: Path
    manifest: Any
    _closed: bool = False

    def __enter__(self) -> ComputeInputLease:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        _remove_lease_tree(self.path)


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


def _remove_lease_tree(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    for child in path.rglob("*"):
        if child.is_symlink():
            child.unlink(missing_ok=True)
        else:
            with suppress(OSError):
                os.chmod(child, 0o700 if child.is_dir() else 0o600)
    with suppress(OSError):
        os.chmod(path, 0o700)
    shutil.rmtree(path, ignore_errors=True)


def _open_snapshot_root(path: Path) -> int:
    from app.desktop_client.workspace_adapter import ComputeInputIntegrityError

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(path, flags)
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("compute input root is not a directory")
        return fd
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        raise ComputeInputIntegrityError("compute input root cannot be pinned") from exc


def _open_relative_fd(root_fd: int, path: PurePosixPath, *, directory: bool) -> int:
    from app.desktop_client.workspace_adapter import ComputeInputIntegrityError

    current_fd = os.dup(root_fd)
    try:
        for part in path.parts[:-1]:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            next_fd = os.open(part, flags, dir_fd=current_fd)
            if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                os.close(next_fd)
                raise OSError("compute input path component is not a directory")
            os.close(current_fd)
            current_fd = next_fd
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        if directory:
            flags |= getattr(os, "O_DIRECTORY", 0)
        target_fd = os.open(path.parts[-1], flags, dir_fd=current_fd)
        metadata = os.fstat(target_fd)
        expected = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        if not expected:
            os.close(target_fd)
            raise OSError("compute input member has an invalid file type")
        return target_fd
    except OSError as exc:
        raise ComputeInputIntegrityError("compute input member cannot be pinned") from exc
    finally:
        os.close(current_fd)


def _read_manifest_from_root(
    root_fd: int,
    task: ComputeTask,
    config: dict[str, Any],
):
    from app.desktop_client.workspace_adapter import ComputeInputIntegrityError
    from app.services.compute_input_bundle import (
        ComputeInputManifest,
        compute_parameters_digest,
        normalize_compute_config,
    )

    manifest_fd = _open_relative_fd(root_fd, PurePosixPath("manifest.json"), directory=False)
    try:
        metadata = os.fstat(manifest_fd)
        if metadata.st_size > 1024 * 1024:
            raise ComputeInputIntegrityError("compute input manifest file is invalid")
        payload = bytearray()
        while True:
            chunk = os.read(manifest_fd, 1024 * 1024)
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > 1024 * 1024:
                raise ComputeInputIntegrityError("compute input manifest file is invalid")
    except OSError as exc:
        raise ComputeInputIntegrityError("compute input manifest cannot be read") from exc
    finally:
        os.close(manifest_fd)
    try:
        manifest = ComputeInputManifest.model_validate_json(payload)
        normalized = normalize_compute_config(task, config)
        if (
            manifest.task != task
            or manifest.parameters_digest != compute_parameters_digest(normalized)
        ):
            raise ComputeInputIntegrityError("compute input execution contract does not match")
        seen: set[str] = set()
        for entry in manifest.files:
            root_name = PurePosixPath(entry.path).parts[0]
            if root_name not in _DATA_ROOTS or entry.path in seen:
                raise ComputeInputIntegrityError("compute input manifest file set is invalid")
            seen.add(entry.path)
        for root_name in _TASK_REQUIRED_ROOTS[task]:
            root_dir_fd = _open_relative_fd(
                root_fd,
                PurePosixPath(root_name),
                directory=True,
            )
            os.close(root_dir_fd)
        return manifest
    except ComputeInputIntegrityError:
        raise
    except (TypeError, ValueError, ValidationError) as exc:
        raise ComputeInputIntegrityError("compute input manifest cannot be rebound") from exc


def _copy_manifest_entry(root_fd: int, destination: Path, entry: Any) -> None:
    """Copy one authenticated regular file from a pinned snapshot root."""
    from app.desktop_client.workspace_adapter import ComputeInputIntegrityError

    source_fd = _open_relative_fd(root_fd, PurePosixPath(entry.path), directory=False)
    target_fd = -1
    target = destination.joinpath(*PurePosixPath(entry.path).parts)
    try:
        if os.fstat(source_fd).st_size != entry.size:
            raise ComputeInputIntegrityError("compute input file size changed")
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        except OSError as exc:
            raise LocalComputeUnavailable("local_repo_init_failed") from exc

        digest = hashlib.sha256()
        copied = 0
        while True:
            try:
                chunk = os.read(source_fd, 1024 * 1024)
            except OSError as exc:
                raise ComputeInputIntegrityError("compute input file cannot be read") from exc
            if not chunk:
                break
            copied += len(chunk)
            if copied > entry.size:
                raise ComputeInputIntegrityError("compute input file exceeded declared size")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                try:
                    written = os.write(target_fd, view)
                except OSError as exc:
                    raise LocalComputeUnavailable("local_repo_init_failed") from exc
                if written <= 0:
                    raise LocalComputeUnavailable("local_repo_init_failed")
                view = view[written:]
        if copied != entry.size or digest.hexdigest() != entry.sha256:
            raise ComputeInputIntegrityError("compute input file integrity does not match")
        try:
            os.fchmod(target_fd, 0o400)
        except OSError as exc:
            raise LocalComputeUnavailable("local_repo_init_failed") from exc
    finally:
        os.close(source_fd)
        if target_fd >= 0:
            os.close(target_fd)


def lease_compute_input(
    data_dir: Path,
    task: ComputeTask,
    config: dict[str, Any],
) -> ComputeInputLease:
    """Pin, reverify, and copy one cache generation for a single execution."""
    from app.desktop_client.workspace_adapter import ComputeInputIntegrityError

    root_fd = _open_snapshot_root(Path(data_dir))
    lease_path: Path | None = None
    try:
        manifest = _read_manifest_from_root(root_fd, task, config)
        try:
            lease_path = Path(tempfile.mkdtemp(prefix="tickflow-compute-lease-"))
            os.chmod(lease_path, 0o700)
        except OSError as exc:
            raise LocalComputeUnavailable("local_repo_init_failed") from exc

        for entry in manifest.files:
            _copy_manifest_entry(root_fd, lease_path, entry)
        copied_size = sum(entry.size for entry in manifest.files)
        if copied_size != manifest.total_uncompressed_bytes:
            raise ComputeInputIntegrityError("compute input total size does not match")
        try:
            manifest_path = lease_path / "manifest.json"
            manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
            os.chmod(manifest_path, 0o400)
            for child in sorted(lease_path.rglob("*"), reverse=True):
                os.chmod(child, 0o555 if child.is_dir() else 0o400)
            os.chmod(lease_path, 0o555)
        except OSError as exc:
            raise LocalComputeUnavailable("local_repo_init_failed") from exc
        return ComputeInputLease(path=lease_path, manifest=manifest)
    except Exception:
        if lease_path is not None:
            _remove_lease_tree(lease_path)
        raise
    finally:
        os.close(root_fd)


def open_read_only_compute_repository(data_dir: Path):
    """Open a verified snapshot through a disposable writable cache overlay."""
    from app.desktop_client.workspace_adapter import ComputeInputIntegrityError

    try:
        from app.tickflow.repository import DataStore, KlineRepository
    except (ImportError, OSError) as exc:
        raise LocalComputeUnavailable("native_library_unavailable") from exc

    snapshot = Path(data_dir)
    for root_name in _DATA_ROOTS:
        source = snapshot / root_name
        if source.is_symlink() or (source.exists() and not source.is_dir()):
            raise ComputeInputIntegrityError("compute input dataset root is invalid")
    overlay = None
    try:
        overlay = tempfile.TemporaryDirectory(prefix="tickflow-local-compute-")
        overlay_root = Path(overlay.name)
        for root_name in _DATA_ROOTS:
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
    root_fd = _open_snapshot_root(Path(data_dir))
    try:
        return _read_manifest_from_root(root_fd, task, config)
    finally:
        os.close(root_fd)


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
