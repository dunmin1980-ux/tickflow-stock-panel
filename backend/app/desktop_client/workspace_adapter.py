"""Cloud-backed workspace adapter for the desktop loopback server."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import threading
import uuid
import zipfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import nullcontext, suppress
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol

import httpx
from pydantic import ValidationError

from app.config import settings
from app.desktop_client.cache import WorkspaceCache
from app.desktop_client.remote import RemoteClient
from app.services.compute_input_bundle import (
    ALLOWED_COMPUTE_INPUT_ROOTS,
    MAX_BUNDLE_UNCOMPRESSED_BYTES,
    ComputeInputConfigError,
    ComputeInputManifest,
    compute_cache_key,
    compute_coverage_policy,
    compute_parameters_digest,
    normalize_compute_config,
    partition_coverage_error,
)
from app.workspace.models import (
    ResourceName,
    ResourceSnapshot,
    WorkspaceCommandConflict,
    WorkspacePreconditionRequired,
    WorkspaceRevisionConflict,
)
from app.workspace.revision import etag_for, parse_if_match


class WorkspaceOfflineReadOnly(RuntimeError):  # noqa: N818 - binding public contract
    pass


class ComputeInputIntegrityError(RuntimeError):
    """A downloaded local-compute input failed a security or freshness check."""


class WorkspaceAdapter(Protocol):
    def bootstrap(self) -> dict: ...

    def get(self, resource: ResourceName) -> ResourceSnapshot: ...

    def command(
        self,
        resource: ResourceName,
        operation: str,
        payload: dict,
        revision: str,
    ) -> ResourceSnapshot: ...

    def revisions(self) -> dict[ResourceName, str]: ...

    def stream_events(self) -> Iterator[dict]: ...


RemoteProvider = RemoteClient | Callable[[], RemoteClient]


class CloudWorkspaceAdapter:
    def __init__(
        self,
        remote: RemoteProvider,
        cache: WorkspaceCache,
        *,
        compute_root: Path | None = None,
        compute_today: Callable[[], date] = date.today,
        compute_max_uncompressed_bytes: int = MAX_BUNDLE_UNCOMPRESSED_BYTES,
        compute_max_age_days: int = 7,
    ) -> None:
        self._remote_provider = remote
        self.cache = cache
        self.compute_root = Path(compute_root or (settings.data_dir / "compute_inputs"))
        self._compute_today = compute_today
        self._compute_max_uncompressed_bytes = compute_max_uncompressed_bytes
        self._compute_max_age_days = compute_max_age_days
        self._compute_lock = threading.Lock()

    def _remote(self) -> RemoteClient:
        if callable(self._remote_provider):
            return self._remote_provider()
        return self._remote_provider

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("cloud workspace response must be an object")
        return payload

    @staticmethod
    def _snapshot(resource: ResourceName, payload: dict[str, Any]) -> ResourceSnapshot:
        candidate = dict(payload)
        candidate["resource"] = resource.value
        snapshot = ResourceSnapshot.model_validate(candidate)
        if snapshot.resource is not resource:
            raise ValueError("cloud workspace resource mismatch")
        return snapshot

    def _cached(self, resource: ResourceName) -> ResourceSnapshot:
        snapshot = self.cache.get(resource)
        if snapshot is None:
            raise WorkspaceOfflineReadOnly("cloud workspace is offline and no cache is available")
        return snapshot.model_copy(update={"offline_readonly": True})

    def bootstrap(self) -> dict:
        try:
            payload = self._payload(self._remote().request("GET", "/api/workspace/bootstrap"))
        except httpx.RequestError:
            resources: dict[str, dict[str, Any]] = {}
            for resource in ResourceName:
                snapshot = self._cached(resource)
                item = snapshot.model_dump(mode="json")
                item.pop("resource")
                resources[resource.value] = item
            return {
                "schema_version": 1,
                "server_time": datetime.now(UTC).isoformat(),
                "data_as_of": None,
                "mode": "cloud",
                "capabilities": {"label": "Offline", "capabilities": {}},
                "resources": resources,
                "offline_readonly": True,
            }

        resources = payload.get("resources")
        if not isinstance(resources, dict):
            raise ValueError("cloud workspace bootstrap resources are invalid")
        snapshots = [
            self._snapshot(resource, resources.get(resource.value, {})) for resource in ResourceName
        ]
        snapshots = [self.cache.validate(snapshot) for snapshot in snapshots]
        for snapshot in snapshots:
            self.cache.put(snapshot)
        return payload

    def get(self, resource: ResourceName) -> ResourceSnapshot:
        resource = ResourceName(resource)
        path = f"/api/workspace/resources/{resource.value}"
        try:
            payload = self._payload(self._remote().request("GET", path))
        except httpx.RequestError:
            return self._cached(resource)
        snapshot = self._snapshot(resource, payload)
        self.cache.put(snapshot)
        return snapshot

    def command(
        self,
        resource: ResourceName,
        operation: str,
        payload: dict,
        revision: str,
    ) -> ResourceSnapshot:
        resource = ResourceName(resource)
        path = f"/api/workspace/resources/{resource.value}/commands"
        try:
            response = self._remote().request(
                "POST",
                path,
                headers={"If-Match": etag_for(revision)},
                json={"operation": operation, "payload": payload},
            )
        except httpx.RequestError as exc:
            raise WorkspaceOfflineReadOnly("cloud workspace is offline and read-only") from exc
        if response.status_code == 428:
            raise WorkspacePreconditionRequired(resource)
        if response.status_code == 412:
            try:
                current_revision = parse_if_match(response.headers.get("etag"))
            except ValueError:
                current_revision = None
            if current_revision is not None:
                raise WorkspaceRevisionConflict(resource, current_revision)
        if response.status_code == 409:
            raise WorkspaceCommandConflict(resource, operation, "cloud command conflict")
        return self._snapshot(resource, self._payload(response))

    def revisions(self) -> dict[ResourceName, str]:
        try:
            payload = self._payload(self._remote().request("GET", "/api/workspace/revisions"))
        except httpx.RequestError as exc:
            cached = {
                resource: snapshot.revision
                for resource in ResourceName
                if (snapshot := self.cache.get(resource)) is not None
            }
            if not cached:
                raise WorkspaceOfflineReadOnly(
                    "cloud workspace is offline and no cache is available"
                ) from exc
            return cached
        resources = payload.get("resources")
        if not isinstance(resources, dict):
            raise ValueError("cloud workspace revisions are invalid")
        return {ResourceName(name): str(revision) for name, revision in resources.items()}

    @staticmethod
    def _safe_bundle_member(info: zipfile.ZipInfo) -> str:
        name = info.filename
        if (
            not name
            or "\x00" in name
            or "\\" in name
            or name.startswith("/")
            or (len(name) >= 2 and name[1] == ":")
        ):
            raise ComputeInputIntegrityError("compute input archive path is invalid")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ComputeInputIntegrityError("compute input archive traversal is not allowed")
        if path.as_posix() != name:
            raise ComputeInputIntegrityError("compute input archive path is not canonical")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ComputeInputIntegrityError("compute input archive symlinks are not allowed")
        if info.is_dir() or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
            raise ComputeInputIntegrityError("compute input archive members must be regular files")
        if name != "manifest.json" and path.parts[0] not in ALLOWED_COMPUTE_INPUT_ROOTS:
            raise ComputeInputIntegrityError("compute input archive root is not allowed")
        return name

    @staticmethod
    def _sha256_open_file(handle) -> str:
        digest = hashlib.sha256()
        handle.seek(0)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        handle.seek(0)
        return digest.hexdigest()

    @staticmethod
    def _manifest_partition_date(path: str) -> str | None:
        for part in PurePosixPath(path).parts:
            if not part.startswith("date="):
                continue
            value = part.removeprefix("date=")
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                return None
            return value if parsed.isoformat() == value else None
        return None

    def _validate_manifest_coverage(
        self,
        manifest: ComputeInputManifest,
        task: str,
        normalized: dict[str, Any],
    ) -> None:
        try:
            parsed_dates = [date.fromisoformat(value) for value in manifest.partition_dates]
            coverage_start = date.fromisoformat(manifest.coverage_start)
            coverage_end = date.fromisoformat(manifest.coverage_end)
        except ValueError as exc:
            raise ComputeInputIntegrityError("compute input coverage dates are invalid") from exc
        canonical_dates = [value.isoformat() for value in parsed_dates]
        reference_date = (
            self._compute_today()
            if task == "strategy_backtest" and not normalized.get("end")
            else date.fromisoformat(manifest.data_as_of)
        )
        expected_coverage = compute_coverage_policy(
            task,
            normalized,
            reference_date=reference_date,
        )
        if (
            not canonical_dates
            or canonical_dates != manifest.partition_dates
            or canonical_dates != sorted(set(canonical_dates))
            or coverage_start.isoformat() != manifest.coverage_start
            or coverage_end.isoformat() != manifest.coverage_end
            or manifest.coverage_start != canonical_dates[0]
            or manifest.coverage_end != canonical_dates[-1]
            or manifest.data_as_of != manifest.coverage_end
            or manifest.coverage != expected_coverage
        ):
            raise ComputeInputIntegrityError("compute input coverage manifest is inconsistent")
        if coverage_error := partition_coverage_error(parsed_dates, manifest.coverage):
            raise ComputeInputIntegrityError(coverage_error)

        roots = {PurePosixPath(entry.path).parts[0] for entry in manifest.files}
        partition_sets: dict[str, set[str]] = {
            "kline_daily": set(),
            "kline_daily_enriched": set(),
        }
        for entry in manifest.files:
            root = PurePosixPath(entry.path).parts[0]
            if root not in partition_sets:
                continue
            partition = self._manifest_partition_date(entry.path)
            if partition is None:
                raise ComputeInputIntegrityError("compute input partition path is invalid")
            partition_sets[root].add(partition)

        expected = set(canonical_dates)
        if "instruments" not in roots:
            raise ComputeInputIntegrityError("compute input instruments are missing")
        if task == "strategy_backtest":
            if "adj_factor" not in roots or any(
                partitions != expected for partitions in partition_sets.values()
            ):
                raise ComputeInputIntegrityError("compute input backtest coverage is incomplete")
        elif (
            partition_sets["kline_daily"]
            or partition_sets["kline_daily_enriched"] != expected
            or len(expected) != 1
        ):
            raise ComputeInputIntegrityError("compute input screener coverage is incomplete")

    def _validate_freshness(
        self,
        task: str,
        normalized: dict[str, Any],
        data_as_of: str,
    ) -> None:
        try:
            actual = date.fromisoformat(data_as_of)
        except (TypeError, ValueError) as exc:
            raise ComputeInputIntegrityError("compute input data date is invalid") from exc
        if actual.isoformat() != data_as_of:
            raise ComputeInputIntegrityError("compute input data date is not canonical")

        requested = normalized.get("as_of") if task == "screener" else normalized.get("end")
        expected = date.fromisoformat(requested) if requested else self._compute_today()
        if task == "screener" and requested and actual != expected:
            raise ComputeInputIntegrityError("screener input does not match the requested date")
        age = (expected - actual).days
        if age < 0 or age > self._compute_max_age_days:
            raise ComputeInputIntegrityError("compute input data is stale or from the future")

    @staticmethod
    def _make_tree_writable(path: Path) -> None:
        if path.is_symlink() or not path.exists():
            return
        for child in path.rglob("*"):
            if child.is_symlink():
                continue
            with suppress(OSError):
                os.chmod(child, 0o700 if child.is_dir() else 0o600)
        with suppress(OSError):
            os.chmod(path, 0o700)

    @classmethod
    def _remove_tree(cls, path: Path) -> None:
        if path.is_symlink() or path.is_file():
            with suppress(OSError):
                path.unlink()
            return
        cls._make_tree_writable(path)
        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _make_tree_read_only(path: Path) -> None:
        for child in path.rglob("*"):
            os.chmod(child, 0o555 if child.is_dir() else 0o444)
        os.chmod(path, 0o555)

    def _publish_compute_input(self, staging: Path, target: Path) -> None:
        suffix = f"{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
        backup = target.with_name(f".{target.name}.retired-{suffix}")
        failed_new = target.with_name(f".{target.name}.failed-{suffix}")
        moved_old = False
        moved_new = False
        try:
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                raise ComputeInputIntegrityError("compute input cache target is invalid")
            if target.exists():
                os.chmod(target, 0o700)
                try:
                    os.replace(target, backup)
                except Exception:
                    os.chmod(target, 0o555)
                    raise
                moved_old = True
            os.replace(staging, target)
            moved_new = True
            os.chmod(target, 0o555)
        except Exception:
            if moved_new and target.exists():
                # If this rename fails, keep the new tree and preserve the old backup.
                with suppress(OSError):
                    os.replace(target, failed_new)
            if moved_old and backup.exists():
                if not target.exists():
                    try:
                        os.replace(backup, target)
                        moved_old = False
                        os.chmod(target, 0o555)
                    except OSError:
                        if failed_new.exists() and not target.exists():
                            with suppress(OSError):
                                os.replace(failed_new, target)
                                os.chmod(target, 0o555)
                if not moved_old:
                    self._remove_tree(failed_new)
            elif failed_new.exists():
                self._remove_tree(failed_new)
            raise
        if moved_old:
            self._remove_tree(backup)

    def install_compute_input(
        self,
        bundle_path: Path | BinaryIO,
        *,
        task: str | None = None,
        config: dict[str, Any] | None = None,
        data_as_of: str | None = None,
        bundle_sha256: str | None = None,
    ) -> Path:
        """Validate an archive completely before publishing a read-only data mirror."""
        staging: Path | None = None
        try:
            if hasattr(bundle_path, "read") and hasattr(bundle_path, "fileno"):
                bundle_context = nullcontext(bundle_path)
            else:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                bundle_fd = os.open(Path(bundle_path), flags)
                bundle_context = os.fdopen(bundle_fd, "rb")
            compressed_limit = self._compute_max_uncompressed_bytes + 64 * 1024 * 1024
            with bundle_context as bundle_handle:
                metadata = os.fstat(bundle_handle.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise ComputeInputIntegrityError(
                        "compute input archive must be a regular file"
                    )
                if metadata.st_size > compressed_limit:
                    raise ComputeInputIntegrityError("compute input archive is too large")
                if not isinstance(bundle_sha256, str) or len(bundle_sha256) != 64:
                    raise ComputeInputIntegrityError("compute input bundle hash header is invalid")
                actual_bundle_hash = self._sha256_open_file(bundle_handle)
                if actual_bundle_hash != bundle_sha256:
                    raise ComputeInputIntegrityError("compute input bundle hash does not match")

                with zipfile.ZipFile(bundle_handle) as archive:
                    infos = archive.infolist()
                    if not infos:
                        raise ComputeInputIntegrityError("compute input archive is empty")
                    names = [self._safe_bundle_member(info) for info in infos]
                    if len(names) != len(set(names)) or names[0] != "manifest.json":
                        raise ComputeInputIntegrityError(
                            "compute input manifest must be the unique first item"
                        )
                    manifest_info = infos[0]
                    if manifest_info.file_size > 1024 * 1024:
                        raise ComputeInputIntegrityError("compute input manifest is too large")
                    try:
                        manifest = ComputeInputManifest.model_validate_json(
                            archive.read(manifest_info)
                        )
                    except (ValidationError, ValueError, TypeError) as exc:
                        raise ComputeInputIntegrityError(
                            "compute input manifest is invalid"
                        ) from exc

                    if task is None or config is None or data_as_of is None:
                        raise ComputeInputIntegrityError(
                            "compute input verification context is missing"
                        )
                    normalized = normalize_compute_config(task, config)
                    parameters_digest = compute_parameters_digest(normalized)
                    if manifest.task != task or manifest.parameters_digest != parameters_digest:
                        raise ComputeInputIntegrityError(
                            "compute input request contract does not match"
                        )
                    if manifest.data_as_of != data_as_of:
                        raise ComputeInputIntegrityError(
                            "compute input data date header does not match"
                        )
                    self._validate_freshness(task, normalized, data_as_of)
                    self._validate_manifest_coverage(manifest, task, normalized)

                    data_infos = infos[1:]
                    if len(manifest.files) != len({entry.path for entry in manifest.files}):
                        raise ComputeInputIntegrityError(
                            "compute input manifest paths are not unique"
                        )
                    manifest_files = {entry.path: entry for entry in manifest.files}
                    if set(names[1:]) != set(manifest_files):
                        raise ComputeInputIntegrityError(
                            "compute input archive contains unlisted files"
                        )
                    total = sum(info.file_size for info in data_infos)
                    if (
                        total > self._compute_max_uncompressed_bytes
                        or total != manifest.total_uncompressed_bytes
                        or total != sum(entry.size for entry in manifest.files)
                    ):
                        raise ComputeInputIntegrityError(
                            "compute input uncompressed size is invalid"
                        )
                    for info in data_infos:
                        entry = manifest_files[info.filename]
                        if info.file_size != entry.size:
                            raise ComputeInputIntegrityError(
                                "compute input file size does not match"
                            )

                    self.compute_root.parent.mkdir(parents=True, exist_ok=True)
                    if self.compute_root.parent.is_symlink():
                        raise ComputeInputIntegrityError(
                            "compute input cache parent must not be a symlink"
                        )
                    if self.compute_root.is_symlink():
                        raise ComputeInputIntegrityError(
                            "compute input cache root must not be a symlink"
                        )
                    self.compute_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.chmod(self.compute_root, 0o700)
                    staging = Path(
                        tempfile.mkdtemp(prefix=".staging-", dir=self.compute_root)
                    )

                    manifest_target = staging / "manifest.json"
                    manifest_target.write_bytes(archive.read(manifest_info))
                    os.chmod(manifest_target, 0o400)
                    extracted_total = 0
                    for info in data_infos:
                        entry = manifest_files[info.filename]
                        target_file = staging.joinpath(*PurePosixPath(info.filename).parts)
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        digest = hashlib.sha256()
                        size = 0
                        with archive.open(info) as source, target_file.open("xb") as output:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                size += len(chunk)
                                extracted_total += len(chunk)
                                if (
                                    size > entry.size
                                    or extracted_total > self._compute_max_uncompressed_bytes
                                ):
                                    raise ComputeInputIntegrityError(
                                        "compute input expanded beyond its declared size"
                                    )
                                digest.update(chunk)
                                output.write(chunk)
                        if size != entry.size or digest.hexdigest() != entry.sha256:
                            raise ComputeInputIntegrityError(
                                "compute input file integrity does not match"
                            )
                        os.chmod(target_file, 0o400)
                    if extracted_total != manifest.total_uncompressed_bytes:
                        raise ComputeInputIntegrityError(
                            "compute input extracted size does not match"
                        )

            cache_key = compute_cache_key(task, normalized, data_as_of)
            target = self.compute_root / cache_key
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                raise ComputeInputIntegrityError("compute input cache target is invalid")
            self._make_tree_read_only(staging)
            # The parent controls rename permission on POSIX, but macOS can reject
            # replacing a directory whose own mode is already read-only.
            os.chmod(staging, 0o700)
            with self._compute_lock:
                self._publish_compute_input(staging, target)
            staging = None
            return target
        except ComputeInputIntegrityError:
            raise
        except (
            OSError,
            EOFError,
            RuntimeError,
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            zlib.error,
            ComputeInputConfigError,
        ) as exc:
            raise ComputeInputIntegrityError("compute input validation failed") from exc
        finally:
            if staging is not None:
                self._remove_tree(staging)

    def _install_compute_input_from_handle(
        self,
        bundle_handle: BinaryIO,
        *,
        task: str,
        config: dict[str, Any],
        data_as_of: str | None,
        bundle_sha256: str | None,
    ) -> Path:
        return self.install_compute_input(
            bundle_handle,
            task=task,
            config=config,
            data_as_of=data_as_of,
            bundle_sha256=bundle_sha256,
        )

    @staticmethod
    def _temporary_path_matches_handle(path: Path, handle: BinaryIO) -> bool:
        try:
            path_metadata = os.stat(path, follow_symlinks=False)
            handle_metadata = os.fstat(handle.fileno())
        except OSError:
            return False
        return (
            stat.S_ISREG(path_metadata.st_mode)
            and stat.S_ISREG(handle_metadata.st_mode)
            and path_metadata.st_dev == handle_metadata.st_dev
            and path_metadata.st_ino == handle_metadata.st_ino
        )

    @staticmethod
    def _remove_owned_temporary(path: Path, identity: tuple[int, int]) -> None:
        try:
            metadata = os.stat(path, follow_symlinks=False)
        except OSError:
            return
        if (
            stat.S_ISREG(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino) == identity
        ):
            with suppress(OSError):
                path.unlink()

    def prepare_compute_input(self, task: str, config: dict[str, Any]) -> Path:
        """Download a cloud-built bundle, then verify and publish it locally."""
        normalize_compute_config(task, config)
        self.compute_root.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix="compute-input-",
            suffix=".zip",
            dir=self.compute_root.parent,
        )
        temporary = Path(temporary_name)
        metadata = os.fstat(fd)
        identity = (metadata.st_dev, metadata.st_ino)
        output: BinaryIO | None = None
        try:
            output = os.fdopen(fd, "w+b")
            fd = -1
            with output:
                remote = self._remote()
                stream_method = getattr(remote, "stream", None)
                if not callable(stream_method):
                    raise WorkspaceOfflineReadOnly(
                        "cloud compute input stream is unavailable"
                    )
                with stream_method(
                    "POST",
                    "/api/workspace/compute-inputs/build",
                    json={"task": task, "config": config},
                ) as response:
                    response.raise_for_status()
                    data_as_of = response.headers.get("X-Data-As-Of")
                    bundle_sha256 = response.headers.get("X-Bundle-SHA256")
                    compressed_limit = (
                        self._compute_max_uncompressed_bytes + 64 * 1024 * 1024
                    )
                    downloaded = 0
                    for chunk in response.iter_bytes(1024 * 1024):
                        downloaded += len(chunk)
                        if downloaded > compressed_limit:
                            raise ComputeInputIntegrityError(
                                "compute input download is too large"
                            )
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
                if not self._temporary_path_matches_handle(temporary, output):
                    raise ComputeInputIntegrityError(
                        "compute input temporary download path changed"
                    )
                output.seek(0)
                return self._install_compute_input_from_handle(
                    output,
                    task=task,
                    config=config,
                    data_as_of=data_as_of,
                    bundle_sha256=bundle_sha256,
                )
        except ComputeInputIntegrityError:
            raise
        except OSError as exc:
            raise ComputeInputIntegrityError(
                "compute input download staging failed"
            ) from exc
        finally:
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
            self._remove_owned_temporary(temporary, identity)

    def stream_events(self) -> Iterator[dict]:
        try:
            remote = self._remote()
            stream_method = getattr(remote, "stream", None)
            if callable(stream_method):
                with stream_method(
                    "GET",
                    "/api/workspace/events",
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    yield from self._event_payloads(response)
                return
            response = remote.request(
                "GET", "/api/workspace/events", headers={"Accept": "text/event-stream"}
            )
            yield from self._event_payloads(response)
        except httpx.RequestError as exc:
            raise WorkspaceOfflineReadOnly("cloud workspace event stream is offline") from exc

    @staticmethod
    def _event_payloads(response: httpx.Response) -> Iterator[dict]:
        response.raise_for_status()
        data_lines: list[str] = []
        for line in response.iter_lines():
            if line == "":
                if data_lines:
                    payload = json.loads("\n".join(data_lines))
                    if isinstance(payload, dict):
                        yield payload
                data_lines = []
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            payload = json.loads("\n".join(data_lines))
            if isinstance(payload, dict):
                yield payload
