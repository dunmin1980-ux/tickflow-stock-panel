"""Build bounded, integrity-described local data bundles for desktop compute."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

ComputeTask = Literal["strategy_backtest", "screener"]

_ALLOWED_TASKS = frozenset({"strategy_backtest", "screener"})
ALLOWED_COMPUTE_INPUT_ROOTS = (
    "kline_daily",
    "kline_daily_enriched",
    "instruments",
    "adj_factor",
)
_DATE_PARTITION = re.compile(r"date=(\d{4}-\d{2}-\d{2})")
_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_RELATIVE_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:[\\/][A-Za-z0-9_.-]+)+$")
_BARE_FILE = re.compile(
    r"^[A-Za-z0-9_.-]+\.(?:parquet|csv|json|db|sqlite|pkl|joblib|py)$",
    re.IGNORECASE,
)
_PATH_KEYS = frozenset(
    {
        "path",
        "file",
        "filename",
        "directory",
        "dir",
        "root",
        "data_dir",
        "data_path",
        "model_path",
        "cache_dir",
    }
)
_MAX_CANONICAL_CONFIG_BYTES = 64 * 1024
MAX_BUNDLE_UNCOMPRESSED_BYTES = 2 * 1024**3
DEFAULT_CACHE_TTL_SECONDS = 10 * 60
DEFAULT_CACHE_MAX_ENTRIES = 4
DEFAULT_CACHE_MAX_BYTES = MAX_BUNDLE_UNCOMPRESSED_BYTES
_FIELDS_SET_KEY = "__fields_set__"


class ComputeInputConfigError(ValueError):
    """The requested compute task cannot be represented by the bounded contract."""


class ComputeInputDataUnavailable(RuntimeError):  # noqa: N818 - public service contract
    """Required local parquet inputs are not present for the requested period."""


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ComputeInputFile(_ManifestModel):
    path: StrictStr
    size: StrictInt = Field(ge=0)
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class ComputeInputManifest(_ManifestModel):
    schema_version: Literal[1]
    task: ComputeTask
    parameters_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    data_as_of: StrictStr
    coverage_start: StrictStr
    coverage_end: StrictStr
    partition_dates: list[StrictStr]
    files: list[ComputeInputFile]
    total_uncompressed_bytes: StrictInt = Field(ge=0, le=MAX_BUNDLE_UNCOMPRESSED_BYTES)


@dataclass(frozen=True)
class ComputeInputArtifact:
    path: Path
    task: ComputeTask
    parameters_digest: str
    data_as_of: str
    bundle_sha256: str
    manifest: ComputeInputManifest
    from_cache: bool
    _cleanup: Callable[[], None]

    def cleanup(self) -> None:
        self._cleanup()


@dataclass(frozen=True)
class _SourceFile:
    source: Path
    archive_path: str


@dataclass(frozen=True)
class _CachedBundle:
    path: Path
    parameters_digest: str
    data_as_of: str
    bundle_sha256: str
    manifest: ComputeInputManifest
    expires_at: float
    size: int


def _looks_like_path(value: str) -> bool:
    lowered = value.casefold()
    if (
        value.startswith(("/", "~/", "\\\\"))
        or _WINDOWS_DRIVE_PATH.match(value)
        or lowered.startswith("file:")
        or "../" in value
        or "..\\" in value
    ):
        return True
    return _BARE_FILE.fullmatch(value) is not None or _RELATIVE_PATH.fullmatch(value) is not None


def _reject_paths(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ComputeInputConfigError("compute config keys must be strings")
            lowered = key.casefold()
            if lowered in _PATH_KEYS or lowered.endswith(("_path", "_file", "_dir", "_root")):
                raise ComputeInputConfigError("compute config must not contain file paths")
            _reject_paths(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_paths(nested)
    elif isinstance(value, str) and _looks_like_path(value):
        raise ComputeInputConfigError("compute config must not contain file paths")


def _validate_known_fields(config: dict[str, Any], model: type[BaseModel]) -> BaseModel:
    unknown = set(config).difference(model.model_fields)
    if unknown:
        raise ComputeInputConfigError("compute config contains unsupported fields")
    try:
        return model.model_validate(config)
    except ValidationError as exc:
        raise ComputeInputConfigError("compute config is invalid") from exc


def normalize_compute_config(task: str, config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate through the existing endpoint DTOs and return canonical JSON data."""
    if task not in _ALLOWED_TASKS:
        raise ComputeInputConfigError("unsupported compute task")
    if not isinstance(config, Mapping):
        raise ComputeInputConfigError("compute config must be an object")
    raw = dict(config)
    _reject_paths(raw)

    if task == "strategy_backtest":
        from app.api.backtest import StrategyBacktestRequest

        model = _validate_known_fields(raw, StrategyBacktestRequest)
        if model.asset_type != "stock":
            raise ComputeInputConfigError("local compute inputs currently support A-share stocks only")
        if model.minute_fill or model.exit_fill == "signal_next_minute":
            raise ComputeInputConfigError("minute data is outside the local compute input contract")
        if model.start is not None and model.end is not None and model.start > model.end:
            raise ComputeInputConfigError("compute date range is invalid")
    else:
        from app.api.screener import CustomRequest, PresetRequest

        has_custom = "conditions" in raw
        has_preset = "strategy_id" in raw
        if has_custom == has_preset:
            raise ComputeInputConfigError("screener config must select one existing request type")
        model = _validate_known_fields(raw, CustomRequest if has_custom else PresetRequest)
        if model.asset_type != "stock":
            raise ComputeInputConfigError("local compute inputs currently support A-share stocks only")
        if model.ext_columns:
            raise ComputeInputConfigError("extension datasets are outside the local compute contract")
        if getattr(model, "timeframe", "1d") != "1d":
            raise ComputeInputConfigError("local compute inputs support daily timeframe only")

    normalized = model.model_dump(mode="json")
    normalized[_FIELDS_SET_KEY] = sorted(model.model_fields_set)
    try:
        canonical_config_bytes(normalized)
    except (TypeError, ValueError) as exc:
        raise ComputeInputConfigError("compute config is not finite canonical JSON") from exc
    return normalized


def canonical_config_bytes(config: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(
        dict(config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_CANONICAL_CONFIG_BYTES:
        raise ComputeInputConfigError("compute config is too large")
    return encoded


def compute_parameters_digest(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_config_bytes(config)).hexdigest()


def compute_cache_key(task: str, config: Mapping[str, Any], data_as_of: str) -> str:
    payload = task.encode() + b"\n" + canonical_config_bytes(config) + b"\n" + data_as_of.encode()
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _partition_date(path: Path) -> date | None:
    for part in path.parts:
        match = _DATE_PARTITION.fullmatch(part)
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                return None
    return None


class ComputeInputBundleService:
    """Builds bundles exclusively from an existing local parquet snapshot."""

    def __init__(
        self,
        data_dir: Path,
        *,
        temp_root: Path | None = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        cache_max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
        cache_max_bytes: int = DEFAULT_CACHE_MAX_BYTES,
        monotonic: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        if temp_root is None:
            temp_root = Path(tempfile.mkdtemp(prefix="tickflow-compute-inputs-"))
        self.temp_root = Path(temp_root)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.temp_root, 0o700)
        self._cache_ttl_seconds = cache_ttl_seconds
        if cache_max_entries < 0 or cache_max_bytes < 0:
            raise ValueError("compute input cache limits must be non-negative")
        self._cache_max_entries = cache_max_entries
        self._cache_max_bytes = cache_max_bytes
        self._monotonic = monotonic
        self._today = today
        self._lock = threading.Lock()
        self._cache: dict[str, _CachedBundle] = {}
        self._closed = False
        root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        self._data_dir_fd = os.open(self.data_dir, root_flags)
        if not stat.S_ISDIR(os.fstat(self._data_dir_fd).st_mode):
            os.close(self._data_dir_fd)
            raise ComputeInputConfigError("compute data directory is not a local directory")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cache.clear()
            os.close(self._data_dir_fd)
            shutil.rmtree(self.temp_root, ignore_errors=True)

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, item in self._cache.items() if item.expires_at <= now]
        for key in expired:
            item = self._cache.pop(key)
            item.path.unlink(missing_ok=True)

    def _enforce_cache_limits(self) -> None:
        total = sum(item.size for item in self._cache.values())
        while self._cache and (
            len(self._cache) > self._cache_max_entries or total > self._cache_max_bytes
        ):
            oldest_key = next(iter(self._cache))
            oldest = self._cache.pop(oldest_key)
            total -= oldest.size
            oldest.path.unlink(missing_ok=True)

    def _root_files(self, root_name: str) -> list[Path]:
        root = self.data_dir / root_name
        if not root.exists() or not root.is_dir():
            return []
        if root.is_symlink() or not root.resolve().is_relative_to(self.data_dir):
            raise ComputeInputConfigError("compute data root is not a local directory")
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                raise ComputeInputConfigError("compute data roots must not contain symlinks")
        files = sorted(path for path in root.rglob("*.parquet") if path.is_file())
        for path in files:
            if not path.resolve().is_relative_to(root.resolve()):
                raise ComputeInputConfigError("compute data file escaped its whitelist root")
        return files

    def _select_sources(
        self,
        task: ComputeTask,
        normalized: dict[str, Any],
        original: Mapping[str, Any],
    ) -> tuple[list[_SourceFile], str]:
        daily = self._root_files("kline_daily")
        enriched = self._root_files("kline_daily_enriched")
        instruments = self._root_files("instruments")
        factors = self._root_files("adj_factor")
        if not instruments:
            raise ComputeInputDataUnavailable("instrument data is unavailable")

        selected: list[Path]
        if task == "screener":
            enriched_by_date: dict[date, list[Path]] = {}
            for path in enriched:
                if partition := _partition_date(path.relative_to(self.data_dir)):
                    enriched_by_date.setdefault(partition, []).append(path)
            requested = normalized.get("as_of")
            target = date.fromisoformat(requested) if requested else max(enriched_by_date, default=None)
            if target is None or target not in enriched_by_date:
                raise ComputeInputDataUnavailable("screener enriched data is unavailable")
            selected = sorted(enriched_by_date[target]) + instruments
            data_as_of = target
        else:
            end_value = normalized.get("end")
            end = date.fromisoformat(end_value) if end_value else self._today()
            start_value = normalized.get("start")
            if start_value:
                start: date | None = date.fromisoformat(start_value)
            elif "start" in normalized[_FIELDS_SET_KEY]:
                start = None
            else:
                start = end - timedelta(days=180)

            daily_by_date: dict[date, list[Path]] = {}
            enriched_by_date: dict[date, list[Path]] = {}
            for path in daily:
                if (
                    (partition := _partition_date(path.relative_to(self.data_dir)))
                    and partition <= end
                    and (start is None or partition >= start)
                ):
                    daily_by_date.setdefault(partition, []).append(path)
            for path in enriched:
                if (
                    (partition := _partition_date(path.relative_to(self.data_dir)))
                    and partition <= end
                    and (start is None or partition >= start)
                ):
                    enriched_by_date.setdefault(partition, []).append(path)
            daily_dates = set(daily_by_date)
            enriched_dates = set(enriched_by_date)
            if daily_dates != enriched_dates:
                raise ComputeInputDataUnavailable(
                    "daily and enriched backtest partitions do not match"
                )
            partition_dates = sorted(daily_dates)
            if not partition_dates:
                raise ComputeInputDataUnavailable("daily backtest data is unavailable")
            if start_value and partition_dates[0] != date.fromisoformat(start_value):
                raise ComputeInputDataUnavailable("requested backtest start coverage is unavailable")
            if not factors:
                raise ComputeInputDataUnavailable("adjustment factor data is unavailable")
            selected = []
            for partition in partition_dates:
                selected.extend(sorted(daily_by_date[partition]))
                selected.extend(sorted(enriched_by_date[partition]))
            selected.extend(instruments)
            selected.extend(factors)
            data_as_of = partition_dates[-1]

        sources = [
            _SourceFile(path, path.relative_to(self.data_dir).as_posix()) for path in selected
        ]
        archive_names = [source.archive_path for source in sources]
        if len(archive_names) != len(set(archive_names)):
            raise ComputeInputConfigError("compute input archive paths are not unique")
        if any(
            PurePosixPath(name).parts[0] not in ALLOWED_COMPUTE_INPUT_ROOTS
            for name in archive_names
        ):
            raise ComputeInputConfigError("compute input escaped the dataset whitelist")
        return sources, data_as_of.isoformat()

    def _open_source_fd(self, archive_path: str) -> int:
        parts = PurePosixPath(archive_path).parts
        if not parts or parts[0] not in ALLOWED_COMPUTE_INPUT_ROOTS:
            raise ComputeInputConfigError("compute input escaped the dataset whitelist")
        directory_fd = os.dup(self._data_dir_fd)
        try:
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            for part in parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            return os.open(parts[-1], file_flags, dir_fd=directory_fd)
        except OSError as exc:
            raise ComputeInputConfigError("compute input source changed or is unsafe") from exc
        finally:
            os.close(directory_fd)

    def _snapshot_source(self, source: _SourceFile, staging: Path) -> ComputeInputFile:
        target = staging / source.archive_path
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = self._open_source_fd(source.archive_path)
        digest = hashlib.sha256()
        size = 0
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ComputeInputConfigError("compute input source is not a regular file")
            with os.fdopen(fd, "rb", closefd=False) as source_handle, target.open("xb") as output:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                        raise ComputeInputConfigError("compute input source is too large")
                    digest.update(chunk)
                    output.write(chunk)
        finally:
            os.close(fd)
        os.chmod(target, 0o400)
        return ComputeInputFile(path=source.archive_path, size=size, sha256=digest.hexdigest())

    @staticmethod
    def _zip_info(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (stat.S_IFREG | 0o444) << 16
        return info

    def _create_master(
        self,
        cache_key: str,
        task: ComputeTask,
        parameters_digest: str,
        sources: list[_SourceFile],
        data_as_of: str,
        now: float,
    ) -> _CachedBundle:
        with tempfile.TemporaryDirectory(dir=self.temp_root, prefix="build-") as build_dir:
            staging = Path(build_dir)
            entries: list[ComputeInputFile] = []
            total = 0
            for source in sources:
                entry = self._snapshot_source(source, staging)
                total += entry.size
                if total > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                    raise ComputeInputConfigError("compute input bundle exceeds 2 GiB")
                entries.append(entry)

            partition_dates = sorted(
                {
                    partition.isoformat()
                    for entry in entries
                    if (partition := _partition_date(Path(entry.path))) is not None
                }
            )
            if not partition_dates:
                raise ComputeInputDataUnavailable("compute input partition coverage is unavailable")

            manifest = ComputeInputManifest(
                schema_version=1,
                task=task,
                parameters_digest=parameters_digest,
                data_as_of=data_as_of,
                coverage_start=partition_dates[0],
                coverage_end=partition_dates[-1],
                partition_dates=partition_dates,
                files=entries,
                total_uncompressed_bytes=total,
            )
            temporary_zip = staging / "bundle.zip"
            manifest_bytes = json.dumps(
                manifest.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            with zipfile.ZipFile(
                temporary_zip,
                "x",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                archive.writestr(self._zip_info("manifest.json"), manifest_bytes)
                for entry in entries:
                    with archive.open(self._zip_info(entry.path), "w") as output, (
                        staging / entry.path
                    ).open("rb") as source_handle:
                        shutil.copyfileobj(source_handle, output, length=1024 * 1024)

            bundle_sha256 = _sha256_file(temporary_zip)
            master = self.temp_root / f"master-{cache_key}.zip"
            os.replace(temporary_zip, master)
            os.chmod(master, 0o400)
        return _CachedBundle(
            path=master,
            parameters_digest=parameters_digest,
            data_as_of=data_as_of,
            bundle_sha256=bundle_sha256,
            manifest=manifest,
            expires_at=now + self._cache_ttl_seconds,
            size=master.stat().st_size,
        )

    def _lease(self, cached: _CachedBundle, task: ComputeTask, from_cache: bool) -> ComputeInputArtifact:
        fd, lease_name = tempfile.mkstemp(prefix="lease-", suffix=".zip", dir=self.temp_root)
        os.close(fd)
        lease = Path(lease_name)
        lease.unlink()
        try:
            os.link(cached.path, lease)
        except OSError:
            shutil.copyfile(cached.path, lease)
        os.chmod(lease, 0o400)
        return ComputeInputArtifact(
            path=lease,
            task=task,
            parameters_digest=cached.parameters_digest,
            data_as_of=cached.data_as_of,
            bundle_sha256=cached.bundle_sha256,
            manifest=cached.manifest,
            from_cache=from_cache,
            _cleanup=lambda: lease.unlink(missing_ok=True),
        )

    def build(self, task: str, config: Mapping[str, Any]) -> ComputeInputArtifact:
        normalized = normalize_compute_config(task, config)
        typed_task: ComputeTask = task  # type: ignore[assignment]
        parameters_digest = compute_parameters_digest(normalized)
        server_cache_key = hashlib.sha256(
            f"{typed_task}\n{parameters_digest}".encode("ascii")
        ).hexdigest()

        with self._lock:
            if self._closed:
                raise RuntimeError("compute input bundle service is closed")
            now = self._monotonic()
            self._purge_expired(now)
            if cached := self._cache.get(server_cache_key):
                return self._lease(cached, typed_task, True)
            sources, data_as_of = self._select_sources(typed_task, normalized, config)
            cached = self._create_master(
                server_cache_key,
                typed_task,
                parameters_digest,
                sources,
                data_as_of,
                now,
            )
            artifact = self._lease(cached, typed_task, False)
            if (
                self._cache_max_entries > 0
                and cached.size <= self._cache_max_bytes
            ):
                self._cache[server_cache_key] = cached
                self._enforce_cache_limits()
            else:
                cached.path.unlink(missing_ok=True)
            return artifact
