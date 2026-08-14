#!/usr/bin/env python3
"""Run the separately approved one-shot Ark TLS connectivity probe."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_SOURCE = (
    REPO_ROOT / "docker/phase2-ark-tls-connectivity-probe/probe.py"
)
PROXY_IMAGE_ID = (
    "sha256:a8a01a003f7afc385b4e24fd2960bcdfd3eaac40c394f39aa6249fd7b2f0f79b"
)
OUTPUT_ROOT = (
    REPO_ROOT / "reports/phase2_provider_ark/tls_connectivity_probe"
)
BASELINE_PATH = OUTPUT_ROOT / "historical_baseline.json"
LEDGER_PATH = OUTPUT_ROOT / "execution-ledger.json"
LOCAL_APPROVAL_PATH = (
    Path.home()
    / "Library/Application Support/TickFlowPhase2CanaryArk/runtime-approval.json"
)
V2_OUTPUT_ROOT = REPO_ROOT / "reports/phase2_provider_ark/tls_probe_v2"
V2_CANDIDATE_PATH = V2_OUTPUT_ROOT / "approval_candidate.json"
V2_SCOPE_PATH = V2_OUTPUT_ROOT / "approval_scope.json"
V2_ATTEMPTS_ROOT = V2_OUTPUT_ROOT / "attempts"
V2_LOCAL_APPROVAL_PATH = (
    Path.home()
    / "Library/Application Support/TickFlowPhase2ArkTlsProbeV2/runtime-approval.json"
)
CHILD_RECEIPT_NAME = "child-receipt.json"
READY_MARKER_NAME = "receipt.ready"
TRANSPORT_ERROR_CATEGORIES = {
    "RECEIPT_OPEN_FAILED",
    "RECEIPT_WRITE_FAILED",
    "RECEIPT_FSYNC_FAILED",
    "RECEIPT_RENAME_FAILED",
    "RECEIPT_PARENT_FSYNC_FAILED",
    "RECEIPT_VALIDATION_FAILED",
}
MAXIMUM_JSON_BYTES = 131_072
_NOFOLLOW = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
_DIRECTORY = os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0


class ProbeRunnerError(RuntimeError):
    pass


class ReceiptTransportError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class HostLifecycleRecorder:
    EVENT_NAMES = (
        "container_wait_completed",
        "receipt_validated",
        "receipt_archive_completed",
        "cleanup_started",
        "cleanup_completed",
    )

    def __init__(
        self,
        *,
        wall_clock: Callable[[], str],
        monotonic_ns: Callable[[], int],
    ) -> None:
        self._wall_clock = wall_clock
        self._monotonic_ns = monotonic_ns
        self._events = {
            name: {
                "occurred": False,
                "wall_time": None,
                "monotonic_ns": None,
                "category": "NOT_REACHED",
            }
            for name in self.EVENT_NAMES
        }

    def record(self, name: str, *, category: str = "PASSED") -> None:
        if name not in self._events or self._events[name]["occurred"]:
            raise ValueError("host_lifecycle_event_invalid")
        if (
            name == "cleanup_started"
            and not self._events["receipt_archive_completed"]["occurred"]
        ):
            raise ValueError("archive_required_before_cleanup")
        if name == "receipt_archive_completed" and self._events[
            "cleanup_started"
        ]["occurred"]:
            raise ValueError("archive_required_before_cleanup")
        self._events[name] = {
            "occurred": True,
            "wall_time": self._wall_clock(),
            "monotonic_ns": self._monotonic_ns(),
            "category": category,
        }

    def values(self) -> dict[str, dict[str, Any]]:
        return {name: dict(value) for name, value in self._events.items()}


def _wall_time() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_no_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("historical_evidence_invalid")


def _ensure_directory(path: Path, *, mode: int) -> None:
    _assert_no_symlink_components(path.parent)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        os.mkdir(path, mode)
        metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("probe_output_directory_invalid")
    os.chmod(path, mode)


def validate_runtime_directory(path: Path) -> None:
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("runtime_directory_invalid")
    for child in path.iterdir():
        if child.is_symlink():
            raise ValueError("runtime_path_symlink")


def canonicalize_runtime_directory(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as error:
            raise ValueError("runtime_directory_invalid") from error
        if not stat.S_ISLNK(metadata.st_mode):
            continue
        trusted_macos_var_alias = (
            sys.platform == "darwin"
            and current == Path("/var")
            and Path("/var").resolve(strict=True) == Path("/private/var")
        )
        if not trusted_macos_var_alias:
            raise ValueError("runtime_path_symlink")
    canonical = absolute.resolve(strict=True)
    validate_runtime_directory(canonical)
    return canonical


def _atomic_write_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o600) -> None:
    _ensure_directory(path.parent, mode=0o700)
    raw = (
        json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | _DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_bytes(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    _ensure_directory(path.parent, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | _DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _exclusive_create_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    mode: int = 0o600,
) -> None:
    _ensure_directory(path.parent, mode=0o700)
    raw = (
        json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            mode,
        )
    except FileExistsError as error:
        raise ValueError("probe_execution_already_exists") from error
    with os.fdopen(descriptor, "wb") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY | _DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_json_regular(path: Path) -> dict[str, Any]:
    _assert_no_symlink_components(path)
    descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAXIMUM_JSON_BYTES:
            raise ValueError("json_file_invalid")
        raw = os.read(descriptor, MAXIMUM_JSON_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("json_file_invalid") from error
    if not isinstance(value, dict):
        raise ValueError("json_file_invalid")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _read_probe_v2_local_approval(path: Path) -> dict[str, Any]:
    try:
        parent_metadata = os.lstat(path.parent)
        file_metadata = os.lstat(path)
    except OSError as error:
        raise ProbeRunnerError("probe_v2_approval_invalid") from error
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        or parent_metadata.st_uid != os.getuid()
        or stat.S_ISLNK(file_metadata.st_mode)
        or not stat.S_ISREG(file_metadata.st_mode)
        or stat.S_IMODE(file_metadata.st_mode) != 0o600
        or file_metadata.st_uid != os.getuid()
    ):
        raise ProbeRunnerError("probe_v2_approval_invalid")
    try:
        return _read_json_regular(path)
    except (OSError, ValueError) as error:
        raise ProbeRunnerError("probe_v2_approval_invalid") from error


def load_probe_v2_execution_identity(
    repo_root: Path,
    *,
    candidate_path: Path,
    scope_path: Path,
    approval_path: Path,
    attempts_root: Path,
) -> dict[str, Any]:
    try:
        candidate = _read_json_regular(candidate_path)
        scope = _read_json_regular(scope_path)
        approval = _read_probe_v2_local_approval(approval_path)
    except ProbeRunnerError:
        raise
    except (OSError, ValueError) as error:
        raise ProbeRunnerError("probe_v2_identity_invalid") from error

    candidate_raw = _canonical_json_bytes(candidate)
    if (
        candidate_path.read_bytes() != candidate_raw
        or scope_path.read_bytes() != _canonical_json_bytes(scope)
    ):
        raise ProbeRunnerError("probe_v2_identity_invalid")
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    scope_identity = {
        "scope_type": "ark_tls_connectivity_probe_v2",
        "approval_candidate_sha256": candidate_sha256,
        "target_host": "ark.cn-beijing.volces.com",
        "target_port": 443,
    }
    scope_id = hashlib.sha256(_canonical_json_bytes(scope_identity)).hexdigest()
    expected_approval = {
        "probe_v2_runtime_approval_schema_version": 1,
        "approval_status": "APPROVED",
        "approval_candidate_sha256": candidate_sha256,
        "approval_scope_id": scope_id,
        "source_git_head": candidate.get("source_git_head"),
        "scope_type": "ark_tls_connectivity_probe_v2",
    }
    fixed_candidate = {
        "probe_approval_candidate_schema_version": 2,
        "candidate_status": "TLS_PROBE_CANDIDATE_READY_FOR_REVIEW",
        "approval_installed": False,
        "scope_type": "ark_tls_connectivity_probe_v2",
        "probe_contract_version": 2,
        "target_host": "ark.cn-beijing.volces.com",
        "target_port": 443,
        "sni_hostname": "ark.cn-beijing.volces.com",
        "hostname_verification_target": "ark.cn-beijing.volces.com",
        "retry_count": 0,
        "maximum_probe_attempts": 1,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "authorization_constructed": False,
        "http_request_sent": False,
        "secret_content_read": False,
        "real_public_network_success_count": 0,
    }
    if any(candidate.get(key) != value for key, value in fixed_candidate.items()):
        raise ProbeRunnerError("probe_v2_candidate_invalid")
    source_head = candidate.get("source_git_head")
    if (
        not isinstance(source_head, str)
        or len(source_head) != 40
        or any(character not in "0123456789abcdef" for character in source_head)
    ):
        raise ProbeRunnerError("probe_v2_candidate_invalid")
    bindings = candidate.get("source_bindings")
    artifacts = candidate.get("artifact_hashes")
    if not isinstance(bindings, dict) or not isinstance(artifacts, dict):
        raise ProbeRunnerError("probe_v2_candidate_invalid")
    required_sources = {
        "probe_runner_source": (
            "docker/phase2-ark-tls-connectivity-probe/probe.py",
            artifacts.get("probe_runner_source_sha256"),
        ),
        "probe_host_orchestrator": (
            "backend/scripts/run_phase2_ark_tls_connectivity_probe.py",
            artifacts.get("probe_host_orchestrator_sha256"),
        ),
    }
    for name, (expected_path, artifact_sha) in required_sources.items():
        binding = bindings.get(name)
        source_path = repo_root / expected_path
        if (
            not isinstance(binding, dict)
            or binding != {"path": expected_path, "sha256": artifact_sha}
            or not isinstance(artifact_sha, str)
            or len(artifact_sha) != 64
            or not source_path.is_file()
            or source_path.is_symlink()
            or _sha256(source_path) != artifact_sha
        ):
            raise ProbeRunnerError("probe_v2_source_binding_invalid")
    if artifacts.get("container_image_id") != PROXY_IMAGE_ID:
        raise ProbeRunnerError("probe_v2_candidate_invalid")

    expected_scope = {
        "probe_approval_scope_schema_version": 1,
        **scope_identity,
        "approval_scope_id": scope_id,
        "approval_installed": False,
        "historical_attempts": 0,
        "attempt_availability": "AVAILABLE",
        "retry_count": 0,
        "maximum_probe_attempts": 1,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "provider_scope_shared": False,
        "ai_canary_scope_shared": False,
        "openai_scope_shared": False,
    }
    if scope != expected_scope or approval != expected_approval:
        raise ProbeRunnerError("probe_v2_identity_invalid")
    try:
        metadata = os.lstat(attempts_root)
    except FileNotFoundError:
        attempts_root.mkdir(mode=0o700, parents=True)
        metadata = os.lstat(attempts_root)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (attempts_root / scope_id).exists()
        or os.path.lexists(attempts_root / scope_id)
    ):
        raise ProbeRunnerError("probe_v2_scope_unavailable")
    return {
        "approval_candidate_sha256": candidate_sha256,
        "approval_scope_id": scope_id,
        "source_git_head": source_head,
        "historical_attempts": 0,
        "attempt_availability": "AVAILABLE",
        "probe_source_sha256": artifacts["probe_runner_source_sha256"],
        "host_orchestrator_sha256": artifacts[
            "probe_host_orchestrator_sha256"
        ],
    }


def reserve_probe_v2_scope(
    identity: Mapping[str, Any],
    *,
    probe_id: str,
    attempts_root: Path,
) -> dict[str, Path]:
    if (
        len(probe_id) != 32
        or any(character not in "0123456789abcdef" for character in probe_id)
        or identity.get("attempt_availability") != "AVAILABLE"
        or identity.get("historical_attempts") != 0
    ):
        raise ValueError("probe_scope_identity_invalid")
    validate_runtime_directory(attempts_root)
    scope_directory = attempts_root / str(identity["approval_scope_id"])
    attempt_directory = scope_directory / probe_id
    try:
        os.mkdir(scope_directory, 0o700)
    except FileExistsError as error:
        raise ValueError("probe_scope_attempt_unavailable") from error
    try:
        root_descriptor = os.open(attempts_root, os.O_RDONLY | _DIRECTORY)
        try:
            os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)
        os.mkdir(attempt_directory, 0o700)
        scope_descriptor = os.open(scope_directory, os.O_RDONLY | _DIRECTORY)
        try:
            os.fsync(scope_descriptor)
        finally:
            os.close(scope_descriptor)
    except BaseException:
        raise
    return {
        "scope_directory": scope_directory,
        "attempt_directory": attempt_directory,
        "ledger_path": attempt_directory / "ledger.json",
        "receipt_directory": attempt_directory / "evidence",
    }


def _load_probe_module() -> ModuleType:
    if not PROBE_SOURCE.is_file() or PROBE_SOURCE.is_symlink():
        raise ValueError("probe_source_invalid")
    spec = importlib.util.spec_from_file_location(
        "phase2_ark_tls_connectivity_probe_child",
        PROBE_SOURCE,
    )
    if spec is None or spec.loader is None:
        raise ValueError("probe_source_invalid")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_historical_baseline(
    repo_root: Path,
    baseline_path: Path,
    approval_path: Path,
) -> int:
    baseline = _read_json_regular(baseline_path)
    files = baseline.get("files")
    frozen_roots = baseline.get("frozen_roots")
    if (
        set(baseline)
        != {"historical_baseline_schema_version", "frozen_roots", "files"}
        or baseline.get("historical_baseline_schema_version") != 1
        or not isinstance(frozen_roots, list)
        or not isinstance(files, list)
        or not files
    ):
        raise ValueError("historical_baseline_invalid")

    normalized_roots: list[Path] = []
    for raw_root in frozen_roots:
        if not isinstance(raw_root, str) or not raw_root:
            raise ValueError("historical_baseline_invalid")
        root = Path(raw_root)
        if root.is_absolute() or ".." in root.parts:
            raise ValueError("historical_baseline_invalid")
        normalized = Path(os.path.normpath(raw_root))
        if normalized in normalized_roots:
            raise ValueError("historical_baseline_invalid")
        normalized_roots.append(normalized)

    observed: set[tuple[str, str]] = set()
    expected_repo_paths: set[str] = set()
    for item in files:
        if (
            not isinstance(item, dict)
            or set(item) != {"path_kind", "path", "sha256"}
            or item.get("path_kind") not in {"repo", "approval"}
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 64
        ):
            raise ValueError("historical_baseline_invalid")
        identity = (item["path_kind"], item["path"])
        if identity in observed:
            raise ValueError("historical_baseline_invalid")
        observed.add(identity)
        relative_path = Path(item["path"])
        if item["path_kind"] == "repo" and (
            relative_path.is_absolute() or ".." in relative_path.parts
        ):
            raise ValueError("historical_baseline_invalid")
        if item["path_kind"] == "repo":
            expected_repo_paths.add(relative_path.as_posix())
        candidate = (
            repo_root / relative_path
            if item["path_kind"] == "repo"
            else approval_path
        )
        _assert_no_symlink_components(candidate)
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError as error:
            raise ValueError("historical_evidence_invalid") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("historical_evidence_invalid")
        if _sha256(candidate) != item["sha256"]:
            raise ValueError("historical_evidence_mutated")

    actual_frozen_paths: set[str] = set()
    expected_frozen_paths: set[str] = set()
    for root in normalized_roots:
        frozen_root = repo_root / root
        _assert_no_symlink_components(frozen_root)
        try:
            root_metadata = os.lstat(frozen_root)
        except FileNotFoundError as error:
            raise ValueError("historical_evidence_invalid") from error
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise ValueError("historical_evidence_invalid")

        root_parts = root.parts
        expected_frozen_paths.update(
            path
            for path in expected_repo_paths
            if Path(path).parts[: len(root_parts)] == root_parts
        )
        for directory, dirnames, filenames in os.walk(frozen_root, followlinks=False):
            directory_path = Path(directory)
            for name in dirnames:
                child = directory_path / name
                if stat.S_ISLNK(os.lstat(child).st_mode):
                    raise ValueError("historical_evidence_invalid")
            for name in filenames:
                child = directory_path / name
                metadata = os.lstat(child)
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("historical_evidence_invalid")
                actual_frozen_paths.add(child.relative_to(repo_root).as_posix())

    if actual_frozen_paths != expected_frozen_paths:
        raise ValueError("historical_evidence_set_changed")
    return len(files)


def verify_receipt_transport_history(repo_root: Path, baseline_path: Path) -> int:
    baseline = _read_json_regular(baseline_path)
    files = baseline.get("files")
    if (
        set(baseline)
        != {
            "baseline_schema_version",
            "captured_at",
            "file_count",
            "files",
            "historical_probe_id",
            "historical_status",
        }
        or baseline.get("baseline_schema_version") != 1
        or baseline.get("historical_probe_id")
        != "5dd641ca9e4b4ccba1035fff4d695409"
        or baseline.get("file_count") != 7
        or not isinstance(files, list)
        or len(files) != 7
    ):
        raise ValueError("receipt_transport_history_invalid")
    expected: set[str] = set()
    for item in files:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 64
        ):
            raise ValueError("receipt_transport_history_invalid")
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("receipt_transport_history_invalid")
        expected.add(relative.as_posix())
        candidate = repo_root / relative
        _assert_no_symlink_components(candidate)
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("receipt_transport_history_mutated")
        if _sha256(candidate) != item["sha256"]:
            raise ValueError("receipt_transport_history_mutated")
    history_root = repo_root / "reports/phase2_provider_ark/tls_connectivity_probe"
    expected_directories = {history_root}
    for relative_path in expected:
        candidate = repo_root / relative_path
        try:
            candidate.relative_to(history_root)
        except ValueError as error:
            raise ValueError("receipt_transport_history_invalid") from error
        expected_directories.update(
            parent
            for parent in candidate.parents
            if parent == history_root or history_root in parent.parents
        )
    actual_files: set[str] = set()
    actual_directories: set[Path] = {history_root}
    for directory, dirnames, filenames in os.walk(history_root, followlinks=False):
        directory_path = Path(directory)
        for name in dirnames:
            child = directory_path / name
            metadata = os.lstat(child)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("receipt_transport_history_invalid")
            actual_directories.add(child)
        for name in filenames:
            child = directory_path / name
            metadata = os.lstat(child)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ValueError("receipt_transport_history_invalid")
            actual_files.add(child.relative_to(repo_root).as_posix())
    if actual_files != expected or actual_directories != expected_directories:
        raise ValueError("receipt_transport_history_set_changed")
    return len(files)


def create_execution_ledger(
    path: Path,
    *,
    probe_id: str,
    probe_source_sha256: str,
    wall_time: str,
    approval_candidate_sha256: str | None = None,
    approval_scope_id: str | None = None,
) -> dict[str, Any]:
    value = {
        "probe_ledger_schema_version": (
            2 if approval_candidate_sha256 and approval_scope_id else 1
        ),
        "probe_id": probe_id,
        "target_host": "ark.cn-beijing.volces.com",
        "target_port": 443,
        "proxy_image_id": PROXY_IMAGE_ID,
        "probe_source_sha256": probe_source_sha256,
        "state": "PREPARED",
        "prepared_at": wall_time,
        "dispatch_started_at": None,
        "completed_at": None,
        "probe_attempt_count": 0,
        "retry_count": 0,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "final_status": None,
        "receipt_sha256": None,
    }
    if approval_candidate_sha256 is not None or approval_scope_id is not None:
        if (
            not isinstance(approval_candidate_sha256, str)
            or len(approval_candidate_sha256) != 64
            or not isinstance(approval_scope_id, str)
            or len(approval_scope_id) != 64
        ):
            raise ValueError("probe_v2_ledger_identity_invalid")
        value.update(
            {
                "approval_candidate_sha256": approval_candidate_sha256,
                "approval_scope_id": approval_scope_id,
                "scope_type": "ark_tls_connectivity_probe_v2",
            }
        )
    _exclusive_create_json(path, value)
    return value


def mark_probe_dispatched(path: Path, *, wall_time: str) -> dict[str, Any]:
    value = _read_json_regular(path)
    if value.get("state") != "PREPARED" or value.get("probe_attempt_count") != 0:
        raise ValueError("probe_execution_already_consumed")
    value.update(
        {
            "state": "NETWORK_DISPATCH_STARTED",
            "dispatch_started_at": wall_time,
            "probe_attempt_count": 1,
        }
    )
    _atomic_write_json(path, value)
    return value


def build_probe_container_command(
    *,
    probe_id: str,
    container_name: str,
    network_name: str,
    output_dir: Path,
    probe_source: Path,
    probe_id_file: Path,
) -> list[str]:
    if len(probe_id) != 32 or any(character not in "0123456789abcdef" for character in probe_id):
        raise ValueError("probe_id_invalid")
    return [
        "docker",
        "create",
        "--name",
        container_name,
        "--network",
        network_name,
        "--read-only",
        "--user",
        "65532:65532",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--restart",
        "no",
        "--pids-limit",
        "16",
        "--memory",
        "64m",
        "--cpus",
        "0.25",
        "--ipc",
        "none",
        "--mount",
        f"type=bind,src={probe_source},dst=/probe/probe.py,readonly",
        "--mount",
        f"type=bind,src={probe_id_file},dst=/run/tickflow/probe-id,readonly",
        "--mount",
        f"type=bind,src={output_dir},dst=/output",
        "--entrypoint",
        "/usr/bin/python3",
        PROXY_IMAGE_ID,
        "/probe/probe.py",
    ]


def _read_regular_bytes(path: Path) -> bytes:
    _assert_no_symlink_components(path)
    descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > MAXIMUM_JSON_BYTES
        ):
            raise ValueError("receipt_file_invalid")
        raw = os.read(descriptor, MAXIMUM_JSON_BYTES + 1)
    finally:
        os.close(descriptor)
    return raw


def read_receipt_bundle(
    output_dir: Path,
    probe_id: str,
    probe_module: ModuleType,
    *,
    expected_publisher: tuple[int, int] | None = None,
    expected_host_directory_owner: tuple[int, int] | None = None,
    expected_host_file_owner: tuple[int, int] | None = None,
) -> dict[str, Any]:
    try:
        canonical = canonicalize_runtime_directory(output_dir)
        directory_metadata = os.lstat(canonical)
        if stat.S_IMODE(directory_metadata.st_mode) != 0o700:
            raise ReceiptTransportError("RECEIPT_VALIDATION_FAILED")
        if expected_host_directory_owner is not None and (
            directory_metadata.st_uid,
            directory_metadata.st_gid,
        ) != expected_host_directory_owner:
            raise ReceiptTransportError("RECEIPT_VALIDATION_FAILED")
        names = {path.name for path in canonical.iterdir()}
        if names != {CHILD_RECEIPT_NAME, READY_MARKER_NAME}:
            raise ReceiptTransportError("RECEIPT_OPEN_FAILED")
        receipt_path = canonical / CHILD_RECEIPT_NAME
        marker_path = canonical / READY_MARKER_NAME
        receipt_metadata = os.lstat(receipt_path)
        marker_metadata = os.lstat(marker_path)
        if (
            (receipt_metadata.st_uid, receipt_metadata.st_gid)
            != (marker_metadata.st_uid, marker_metadata.st_gid)
            or (
                expected_host_file_owner is not None
                and (receipt_metadata.st_uid, receipt_metadata.st_gid)
                != expected_host_file_owner
            )
        ):
            raise ReceiptTransportError("RECEIPT_VALIDATION_FAILED")
        receipt_bytes = _read_regular_bytes(receipt_path)
        marker_bytes = _read_regular_bytes(marker_path)
        receipt = json.loads(receipt_bytes)
        marker = json.loads(marker_bytes)
    except ReceiptTransportError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ReceiptTransportError("RECEIPT_VALIDATION_FAILED") from error
    marker_fields = {
        "publication_schema_version",
        "publication_complete",
        "probe_id",
        "receipt_sha256",
        "publisher_uid",
        "publisher_gid",
        "directory_uid",
        "directory_gid",
        "directory_mode",
    }
    if (
        not isinstance(marker, dict)
        or set(marker) != marker_fields
        or marker.get("publication_schema_version") != 1
        or marker.get("publication_complete") is not True
        or marker.get("probe_id") != probe_id
        or marker.get("receipt_sha256") != hashlib.sha256(receipt_bytes).hexdigest()
        or type(marker.get("publisher_uid")) is not int
        or type(marker.get("publisher_gid")) is not int
        or type(marker.get("directory_uid")) is not int
        or type(marker.get("directory_gid")) is not int
        or marker.get("directory_mode") != "0700"
    ):
        raise ReceiptTransportError("RECEIPT_VALIDATION_FAILED")
    if expected_publisher is not None and (
        (marker["publisher_uid"], marker["publisher_gid"]) != expected_publisher
        or (marker["directory_uid"], marker["directory_gid"])
        != expected_publisher
    ):
        raise ReceiptTransportError("RECEIPT_VALIDATION_FAILED")
    try:
        validated = probe_module.validate_probe_receipt(
            receipt,
            require_cleanup=False,
        )
    except ValueError as error:
        raise ReceiptTransportError("RECEIPT_VALIDATION_FAILED") from error
    if validated.get("probe_id") != probe_id:
        raise ReceiptTransportError("RECEIPT_VALIDATION_FAILED")
    return {
        "receipt": validated,
        "receipt_bytes": receipt_bytes,
        "marker": marker,
        "marker_bytes": marker_bytes,
        "host_metadata": {
            "directory_uid": directory_metadata.st_uid,
            "directory_gid": directory_metadata.st_gid,
            "directory_mode": f"{stat.S_IMODE(directory_metadata.st_mode):04o}",
            "receipt_uid": receipt_metadata.st_uid,
            "receipt_gid": receipt_metadata.st_gid,
            "receipt_mode": f"{stat.S_IMODE(receipt_metadata.st_mode):04o}",
            "marker_uid": marker_metadata.st_uid,
            "marker_gid": marker_metadata.st_gid,
            "marker_mode": f"{stat.S_IMODE(marker_metadata.st_mode):04o}",
        },
    }


def _transport_category_from_stderr(stderr: str) -> str | None:
    try:
        value = json.loads(stderr.strip())
    except (json.JSONDecodeError, UnicodeError):
        return None
    if (
        isinstance(value, dict)
        and set(value) == {"category", "receipt_transport_schema_version"}
        and value.get("receipt_transport_schema_version") == 1
        and value.get("category") in TRANSPORT_ERROR_CATEGORIES
    ):
        return str(value["category"])
    return None


def process_container_result(
    *,
    result: subprocess.CompletedProcess[str],
    output_dir: Path,
    probe_id: str,
    probe_module: ModuleType,
    expected_publisher: tuple[int, int] | None = None,
    expected_host_directory_owner: tuple[int, int] | None = None,
    expected_host_file_owner: tuple[int, int] | None = None,
) -> dict[str, Any]:
    child_category = _transport_category_from_stderr(result.stderr)
    if child_category is not None:
        raise ReceiptTransportError(
            child_category if result.returncode != 0 else "RECEIPT_VALIDATION_FAILED"
        )
    try:
        bundle = read_receipt_bundle(
            output_dir,
            probe_id,
            probe_module,
            expected_publisher=expected_publisher,
            expected_host_directory_owner=expected_host_directory_owner,
            expected_host_file_owner=expected_host_file_owner,
        )
    except ReceiptTransportError as error:
        raise ReceiptTransportError(error.category) from error
    expected_exit = 0 if bundle["receipt"]["terminal_status"] == "PROBE_PASSED" else 2
    if result.returncode != expected_exit:
        raise ReceiptTransportError("RECEIPT_VALIDATION_FAILED")
    return bundle


def archive_child_bundle(
    receipt_dir: Path,
    bundle: Mapping[str, Any],
    lifecycle: HostLifecycleRecorder,
) -> None:
    _ensure_directory(receipt_dir, mode=0o700)
    _atomic_write_bytes(
        receipt_dir / "child-receipt.json",
        bytes(bundle["receipt_bytes"]),
    )
    _atomic_write_bytes(
        receipt_dir / READY_MARKER_NAME,
        bytes(bundle["marker_bytes"]),
    )
    lifecycle.record("receipt_archive_completed")


def archive_transport_error(
    receipt_dir: Path,
    *,
    probe_id: str,
    category: str,
    container_exit_code: int | None,
    lifecycle: HostLifecycleRecorder,
) -> None:
    _ensure_directory(receipt_dir, mode=0o700)
    _atomic_write_json(
        receipt_dir / "transport-error.json",
        {
            "transport_error_schema_version": 1,
            "probe_id": probe_id,
            "category": category,
            "container_exit_code": container_exit_code,
            "provider_attempt_count": 0,
            "ai_call_count": 0,
            "secret_content_read": False,
            "authorization_constructed": False,
            "http_request_sent": False,
        },
    )
    lifecycle.record(
        "receipt_archive_completed",
        category="TRANSPORT_ERROR_PERSISTED",
    )


def transport_evidence(
    *,
    probe_id: str,
    container_exit_code: int | None,
    transport_error_category: str | None,
    lifecycle: HostLifecycleRecorder,
    temporary_residue_count: int | None,
) -> dict[str, Any]:
    return {
        "transport_evidence_schema_version": 1,
        "probe_id": probe_id,
        "container_exit_code": container_exit_code,
        "transport_error_category": transport_error_category,
        "host_lifecycle": lifecycle.values(),
        "temporary_residue_count": temporary_residue_count,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "secret_content_read": False,
        "authorization_constructed": False,
        "http_request_sent": False,
    }


def finalize_probe_receipt(
    child_receipt: Mapping[str, Any],
    *,
    wall_time: str,
    monotonic_ns: int,
    probe_module: ModuleType,
    cleanup_succeeded: bool = True,
) -> dict[str, Any]:
    probe_module.validate_probe_receipt(child_receipt, require_cleanup=False)
    value = json.loads(json.dumps(child_receipt, allow_nan=False))
    if value["events"]["cleanup_completed"]["occurred"] is not False:
        raise ValueError("probe_cleanup_event_invalid")
    value["events"]["cleanup_completed"] = {
        "occurred": True,
        "wall_time": wall_time,
        "monotonic_ns": monotonic_ns,
        "category": "PASSED" if cleanup_succeeded else "RESIDUE_DETECTED",
    }
    probe_module.validate_probe_receipt(value, require_cleanup=True)
    return value


def final_status_for_category(category: str) -> str:
    if category == "NONE":
        return "PHASE2B_ARK_TLS_CONNECTIVITY_PROBE_PASSED"
    if category == "DNS_RESOLUTION_FAILED":
        return "PHASE2B_ARK_TLS_PROBE_DNS_BLOCKED"
    if category in {"TCP_CONNECT_FAILED", "TCP_CONNECT_TIMEOUT"}:
        return "PHASE2B_ARK_TLS_PROBE_TCP_BLOCKED"
    if category == "TLS_CERT_VERIFY_FAILED":
        return "PHASE2B_ARK_TLS_PROBE_CERT_BLOCKED"
    if category == "TLS_HOSTNAME_VERIFY_FAILED":
        return "PHASE2B_ARK_TLS_PROBE_HOSTNAME_BLOCKED"
    if category in {
        "TLS_HANDSHAKE_TIMEOUT",
        "TLS_PROTOCOL_FAILED",
        "TLS_CONNECTION_RESET",
        "TLS_EOF",
        "TLS_OTHER_SSL_ERROR",
    }:
        return "PHASE2B_ARK_TLS_PROBE_HANDSHAKE_BLOCKED"
    return "PHASE2B_ARK_TLS_PROBE_UNKNOWN"


def _run(
    command: Sequence[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _require_success(result: subprocess.CompletedProcess[str], code: str) -> None:
    if result.returncode != 0:
        raise ProbeRunnerError(code)


def _write_probe_id(path: Path, probe_id: str) -> None:
    path.write_text(probe_id + "\n", encoding="ascii")
    path.chmod(0o444)


def _empty_process_receipt(probe_id: str, probe_module: ModuleType) -> dict[str, Any]:
    events = {
        name: {
            "occurred": False,
            "wall_time": None,
            "monotonic_ns": None,
            "category": "NOT_REACHED",
        }
        for name in probe_module.EVENT_NAMES
    }
    value = {
        "probe_receipt_schema_version": 1,
        "probe_id": probe_id,
        "target": {
            "host": probe_module.TARGET_HOST,
            "port": probe_module.TARGET_PORT,
            "sni": probe_module.TARGET_HOST,
            "hostname_verification_target": probe_module.TARGET_HOST,
        },
        "probe_attempt_count": 1,
        "retry_count": 0,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "secret_content_read": False,
        "authorization_constructed": False,
        "http_request_sent": False,
        "business_body_sent": False,
        "terminal_status": "PROCESS_ERROR",
        "tls_error_category": "PROCESS_ERROR",
        "verify_code": None,
        "verify_message": None,
        "tls_version": None,
        "cipher_name": None,
        "certificate_not_before": None,
        "certificate_not_after": None,
        "san_contains_hostname": None,
        "events": events,
    }
    probe_module.validate_probe_receipt(value, require_cleanup=False)
    return value


def _container_exists(name: str) -> bool:
    result = _run(["docker", "container", "inspect", name], timeout=10)
    if result.returncode == 0:
        return True
    if result.returncode == 1 and "no such container" in result.stderr.lower():
        return False
    raise ProbeRunnerError("container_inspect_failed")


def _network_exists(name: str) -> bool:
    result = _run(["docker", "network", "inspect", name], timeout=10)
    if result.returncode == 0:
        return True
    lowered = result.stderr.lower()
    if result.returncode == 1 and (
        "no such network" in lowered or "network" in lowered and "not found" in lowered
    ):
        return False
    raise ProbeRunnerError("network_inspect_failed")


def _cleanup(container_name: str, network_name: str) -> tuple[int, int]:
    try:
        if _container_exists(container_name):
            _run(
                ["docker", "container", "rm", "--force", container_name],
                timeout=15,
            )
    except (OSError, ProbeRunnerError, subprocess.SubprocessError):
        pass
    try:
        if _network_exists(network_name):
            _run(["docker", "network", "rm", network_name], timeout=15)
    except (OSError, ProbeRunnerError, subprocess.SubprocessError):
        pass
    try:
        container_residue = int(_container_exists(container_name))
    except (OSError, ProbeRunnerError, subprocess.SubprocessError):
        container_residue = 1
    try:
        network_residue = int(_network_exists(network_name))
    except (OSError, ProbeRunnerError, subprocess.SubprocessError):
        network_residue = 1
    return container_residue, network_residue


def cleanup_runtime_after_archive_gate(
    *,
    container_name: str,
    network_name: str,
    temporary_root: Path | None,
    dispatched: bool,
    archive_completed: bool,
) -> tuple[int, int, int, bool]:
    if dispatched and not archive_completed:
        try:
            container_residue = int(_container_exists(container_name))
        except (OSError, ProbeRunnerError, subprocess.SubprocessError):
            container_residue = 1
        try:
            network_residue = int(_network_exists(network_name))
        except (OSError, ProbeRunnerError, subprocess.SubprocessError):
            network_residue = 1
        temporary_residue = int(
            temporary_root is not None and temporary_root.exists()
        )
        return container_residue, network_residue, temporary_residue, False

    container_residue, network_residue = _cleanup(container_name, network_name)
    if temporary_root is not None:
        shutil.rmtree(temporary_root, ignore_errors=True)
    temporary_residue = int(temporary_root is not None and temporary_root.exists())
    return container_residue, network_residue, temporary_residue, True


def _update_terminal_ledger(
    path: Path,
    *,
    final_status: str,
    receipt_path: Path,
    wall_time: str,
) -> dict[str, Any]:
    ledger = _read_json_regular(path)
    if (
        ledger.get("state") != "NETWORK_DISPATCH_STARTED"
        or ledger.get("probe_attempt_count") != 1
    ):
        raise ValueError("probe_ledger_state_invalid")
    ledger.update(
        {
            "state": "COMPLETED",
            "completed_at": wall_time,
            "final_status": final_status,
            "receipt_sha256": _sha256(receipt_path),
        }
    )
    _atomic_write_json(path, ledger)
    return ledger


def run_live_probe() -> dict[str, Any]:
    probe_module = _load_probe_module()
    verify_historical_baseline(REPO_ROOT, BASELINE_PATH, LOCAL_APPROVAL_PATH)
    execution_identity = load_probe_v2_execution_identity(
        REPO_ROOT,
        candidate_path=V2_CANDIDATE_PATH,
        scope_path=V2_SCOPE_PATH,
        approval_path=V2_LOCAL_APPROVAL_PATH,
        attempts_root=V2_ATTEMPTS_ROOT,
    )

    probe_id = uuid.uuid4().hex
    reservation = reserve_probe_v2_scope(
        execution_identity,
        probe_id=probe_id,
        attempts_root=V2_ATTEMPTS_ROOT,
    )
    ledger_path = reservation["ledger_path"]
    receipt_dir = reservation["receipt_directory"]
    image = _run(
        ["docker", "image", "inspect", PROXY_IMAGE_ID, "--format", "{{.Id}}"],
        timeout=15,
    )
    _require_success(image, "probe_image_missing")
    if image.stdout.strip() != PROXY_IMAGE_ID:
        raise ProbeRunnerError("probe_image_identity_mismatch")

    suffix = probe_id[:12]
    container_name = f"phase2-ark-tls-probe-{suffix}"
    network_name = f"phase2-ark-tls-probe-net-{suffix}"
    probe_source_sha256 = _sha256(PROBE_SOURCE)
    create_execution_ledger(
        ledger_path,
        probe_id=probe_id,
        probe_source_sha256=probe_source_sha256,
        wall_time=_wall_time(),
        approval_candidate_sha256=execution_identity[
            "approval_candidate_sha256"
        ],
        approval_scope_id=execution_identity["approval_scope_id"],
    )

    temporary_root: Path | None = None
    bundle: dict[str, Any] | None = None
    dispatched = False
    container_residue = 0
    network_residue = 0
    temporary_residue = 0
    transport_error_category: str | None = None
    container_exit_code: int | None = None
    final_result: dict[str, Any] | None = None
    lifecycle = HostLifecycleRecorder(
        wall_clock=_wall_time,
        monotonic_ns=time.monotonic_ns,
    )
    try:
        temporary_root = canonicalize_runtime_directory(
            Path(tempfile.mkdtemp(prefix="phase2-ark-tls-probe-"))
        )
        output_dir = temporary_root / "output"
        output_dir.mkdir(mode=0o700)
        output_dir.chmod(0o700)
        output_metadata = os.lstat(output_dir)
        host_directory_owner = (output_metadata.st_uid, output_metadata.st_gid)
        probe_id_file = temporary_root / "probe-id"
        _write_probe_id(probe_id_file, probe_id)
        output_dir = canonicalize_runtime_directory(output_dir)
        try:
            network = _run(
                ["docker", "network", "create", network_name],
                timeout=15,
            )
            _require_success(network, "probe_network_create_failed")
            create = _run(
                build_probe_container_command(
                    probe_id=probe_id,
                    container_name=container_name,
                    network_name=network_name,
                    output_dir=output_dir,
                    probe_source=PROBE_SOURCE,
                    probe_id_file=probe_id_file,
                ),
                timeout=15,
            )
            _require_success(create, "probe_container_create_failed")
            mark_probe_dispatched(ledger_path, wall_time=_wall_time())
            dispatched = True
            try:
                start_result = _run(
                    ["docker", "start", "--attach", container_name],
                    timeout=205,
                )
                container_exit_code = start_result.returncode
                lifecycle.record("container_wait_completed")
                bundle = process_container_result(
                    result=start_result,
                    output_dir=output_dir,
                    probe_id=probe_id,
                    probe_module=probe_module,
                    expected_publisher=(65532, 65532),
                    expected_host_directory_owner=host_directory_owner,
                    expected_host_file_owner=host_directory_owner,
                )
                lifecycle.record("receipt_validated")
                archive_child_bundle(receipt_dir, bundle, lifecycle)
            except (
                OSError,
                ValueError,
                ProbeRunnerError,
                ReceiptTransportError,
                subprocess.SubprocessError,
            ) as error:
                if not lifecycle.values()["container_wait_completed"]["occurred"]:
                    lifecycle.record(
                        "container_wait_completed",
                        category="PROCESS_ERROR",
                    )
                transport_error_category = (
                    error.category
                    if isinstance(error, ReceiptTransportError)
                    else "RECEIPT_OPEN_FAILED"
                )
                if bundle is not None:
                    raise
                archive_transport_error(
                    receipt_dir,
                    probe_id=probe_id,
                    category=transport_error_category,
                    container_exit_code=container_exit_code,
                    lifecycle=lifecycle,
                )
        finally:
            if lifecycle.values()["receipt_archive_completed"]["occurred"]:
                try:
                    _atomic_write_json(
                        receipt_dir / "transport-evidence-precleanup.json",
                        transport_evidence(
                            probe_id=probe_id,
                            container_exit_code=container_exit_code,
                            transport_error_category=transport_error_category,
                            lifecycle=lifecycle,
                            temporary_residue_count=None,
                        ),
                    )
                except (OSError, ValueError):
                    transport_error_category = (
                        transport_error_category or "RECEIPT_WRITE_FAILED"
                    )
                lifecycle.record("cleanup_started")
            (
                container_residue,
                network_residue,
                temporary_residue,
                cleanup_performed,
            ) = cleanup_runtime_after_archive_gate(
                container_name=container_name,
                network_name=network_name,
                temporary_root=temporary_root,
                dispatched=dispatched,
                archive_completed=lifecycle.values()["receipt_archive_completed"][
                    "occurred"
                ],
            )
            if (
                cleanup_performed
                and lifecycle.values()["cleanup_started"]["occurred"]
            ):
                lifecycle.record(
                    "cleanup_completed",
                    category=(
                        "PASSED"
                        if not (
                            container_residue
                            or network_residue
                            or temporary_residue
                        )
                        else "RESIDUE_DETECTED"
                    ),
                )

        if not dispatched:
            raise ProbeRunnerError("probe_not_dispatched")
        child_receipt = (
            dict(bundle["receipt"])
            if bundle is not None
            else _empty_process_receipt(probe_id, probe_module)
        )
        final_receipt = finalize_probe_receipt(
            child_receipt,
            wall_time=_wall_time(),
            monotonic_ns=time.monotonic_ns(),
            probe_module=probe_module,
            cleanup_succeeded=not (
                container_residue or network_residue or temporary_residue
            ),
        )
        final_status = (
            "PHASE2B_ARK_TLS_PROBE_UNKNOWN"
            if (
                transport_error_category
                or container_residue
                or network_residue
                or temporary_residue
            )
            else final_status_for_category(final_receipt["tls_error_category"])
        )
        _ensure_directory(receipt_dir, mode=0o700)
        receipt_path = receipt_dir / "receipt.json"
        _atomic_write_json(receipt_path, final_receipt)
        _atomic_write_json(
            receipt_dir / "transport-evidence.json",
            transport_evidence(
                probe_id=probe_id,
                container_exit_code=container_exit_code,
                transport_error_category=transport_error_category,
                lifecycle=lifecycle,
                temporary_residue_count=temporary_residue,
            ),
        )
        _update_terminal_ledger(
            ledger_path,
            final_status=final_status,
            receipt_path=receipt_path,
            wall_time=_wall_time(),
        )
        final_result = {
            "final_status": final_status,
            "probe_id": probe_id,
            "probe_attempt_count": 1,
            "provider_attempt_count": 0,
            "ai_call_count": 0,
            "retry_count": 0,
            "container_residue_count": container_residue,
            "network_residue_count": network_residue,
            "temporary_residue_count": temporary_residue,
            "transport_error_category": transport_error_category,
            "receipt_path": receipt_path.relative_to(REPO_ROOT).as_posix(),
            "approval_candidate_sha256": execution_identity[
                "approval_candidate_sha256"
            ],
            "approval_scope_id": execution_identity["approval_scope_id"],
        }
    finally:
        if (
            temporary_root is not None
            and (
                not dispatched
                or lifecycle.values()["receipt_archive_completed"]["occurred"]
            )
        ):
            shutil.rmtree(temporary_root, ignore_errors=True)

    if final_result is None:
        raise ProbeRunnerError("probe_result_missing")
    final_result["temporary_residue_count"] = int(
        temporary_root is not None and temporary_root.exists()
    )
    return final_result


def main() -> int:
    try:
        result = run_live_probe()
    except (OSError, ValueError, ProbeRunnerError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "final_status": "PHASE2B_ARK_TLS_PROBE_UNKNOWN",
                    "error_category": type(error).__name__,
                    "provider_attempt_count": 0,
                    "ai_call_count": 0,
                    "retry_count": 0,
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["final_status"] == "PHASE2B_ARK_TLS_CONNECTIVITY_PROBE_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
