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
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
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
CHILD_RECEIPT_NAME = "child-receipt.json"
MAXIMUM_JSON_BYTES = 131_072
_NOFOLLOW = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
_DIRECTORY = os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0


class ProbeRunnerError(RuntimeError):
    pass


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


def create_execution_ledger(
    path: Path,
    *,
    probe_id: str,
    probe_source_sha256: str,
    wall_time: str,
) -> dict[str, Any]:
    value = {
        "probe_ledger_schema_version": 1,
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
        f"type=bind,src={output_dir},dst=/output",
        "--entrypoint",
        "/usr/bin/python3",
        PROXY_IMAGE_ID,
        "/probe/probe.py",
    ]


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
    if os.path.lexists(LEDGER_PATH):
        raise ProbeRunnerError("probe_execution_already_exists")
    image = _run(
        ["docker", "image", "inspect", PROXY_IMAGE_ID, "--format", "{{.Id}}"],
        timeout=15,
    )
    _require_success(image, "probe_image_missing")
    if image.stdout.strip() != PROXY_IMAGE_ID:
        raise ProbeRunnerError("probe_image_identity_mismatch")

    probe_id = uuid.uuid4().hex
    suffix = probe_id[:12]
    container_name = f"phase2-ark-tls-probe-{suffix}"
    network_name = f"phase2-ark-tls-probe-net-{suffix}"
    probe_source_sha256 = _sha256(PROBE_SOURCE)
    create_execution_ledger(
        LEDGER_PATH,
        probe_id=probe_id,
        probe_source_sha256=probe_source_sha256,
        wall_time=_wall_time(),
    )

    temporary_root: Path | None = None
    child_receipt: dict[str, Any] | None = None
    dispatched = False
    container_residue = 0
    network_residue = 0
    final_result: dict[str, Any] | None = None
    try:
        temporary_root = Path(tempfile.mkdtemp(prefix="phase2-ark-tls-probe-"))
        output_dir = temporary_root / "output"
        output_dir.mkdir(mode=0o777)
        output_dir.chmod(0o777)
        _write_probe_id(output_dir / "probe-id", probe_id)
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
                ),
                timeout=15,
            )
            _require_success(create, "probe_container_create_failed")
            mark_probe_dispatched(LEDGER_PATH, wall_time=_wall_time())
            dispatched = True
            try:
                _run(
                    ["docker", "start", "--attach", container_name],
                    timeout=205,
                )
                child_path = output_dir / CHILD_RECEIPT_NAME
                if child_path.is_file() and not child_path.is_symlink():
                    child_receipt = _read_json_regular(child_path)
                    probe_module.validate_probe_receipt(
                        child_receipt,
                        require_cleanup=False,
                    )
            except (
                OSError,
                ValueError,
                ProbeRunnerError,
                subprocess.SubprocessError,
            ):
                child_receipt = _empty_process_receipt(probe_id, probe_module)
        finally:
            container_residue, network_residue = _cleanup(
                container_name,
                network_name,
            )

        if not dispatched:
            raise ProbeRunnerError("probe_not_dispatched")
        child_receipt = child_receipt or _empty_process_receipt(probe_id, probe_module)
        final_receipt = finalize_probe_receipt(
            child_receipt,
            wall_time=_wall_time(),
            monotonic_ns=time.monotonic_ns(),
            probe_module=probe_module,
            cleanup_succeeded=not (container_residue or network_residue),
        )
        final_status = (
            "PHASE2B_ARK_TLS_PROBE_UNKNOWN"
            if container_residue or network_residue
            else final_status_for_category(final_receipt["tls_error_category"])
        )
        receipt_dir = OUTPUT_ROOT / probe_id
        _ensure_directory(receipt_dir, mode=0o700)
        receipt_path = receipt_dir / "receipt.json"
        _atomic_write_json(receipt_path, final_receipt)
        _update_terminal_ledger(
            LEDGER_PATH,
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
            "temporary_residue_count": 0,
            "receipt_path": receipt_path.relative_to(REPO_ROOT).as_posix(),
        }
    finally:
        if temporary_root is not None:
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
