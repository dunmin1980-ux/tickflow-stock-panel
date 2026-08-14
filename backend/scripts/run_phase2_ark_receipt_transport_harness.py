#!/usr/bin/env python3
"""Prove the Ark TLS receipt channel locally with Docker network disabled."""

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
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_SOURCE = REPO_ROOT / "docker/phase2-ark-tls-connectivity-probe/probe.py"
FIXTURE_SOURCE = (
    REPO_ROOT
    / "docker/phase2-ark-tls-connectivity-probe/receipt_transport_fixture.py"
)
RUNNER_SOURCE = REPO_ROOT / "backend/scripts/run_phase2_ark_tls_connectivity_probe.py"
OUTPUT_ROOT = REPO_ROOT / "reports/phase2_provider_ark/tls_receipt_transport"
HISTORY_PATH = OUTPUT_ROOT / "historical_probe_baseline.json"
ARCHIVE_ROOT = OUTPUT_ROOT / "harness_archives"
EVIDENCE_PATH = OUTPUT_ROOT / "local_harness.json"
IMAGE_ID = "sha256:a8a01a003f7afc385b4e24fd2960bcdfd3eaac40c394f39aa6249fd7b2f0f79b"
CONTAINER_PREFIX = "phase2-ark-receipt-local-"
EXPECTED_FAULTS = {
    "permission_denied": "RECEIPT_OPEN_FAILED",
    "wrong_destination": "RECEIPT_OPEN_FAILED",
    "rename_fail": "RECEIPT_RENAME_FAILED",
    "missing_ready": "RECEIPT_OPEN_FAILED",
    "malformed": "RECEIPT_VALIDATION_FAILED",
}


class ReceiptHarnessError(RuntimeError):
    pass


def _load_module(path: Path, name: str) -> ModuleType:
    if not path.is_file() or path.is_symlink():
        raise ReceiptHarnessError("harness_source_invalid")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ReceiptHarnessError("harness_source_invalid")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _require_success(result: subprocess.CompletedProcess[str], category: str) -> None:
    if result.returncode != 0:
        raise ReceiptHarnessError(category)


def _mount(source: Path, destination: str, *, readonly: bool = True) -> str:
    value = f"type=bind,src={source},dst={destination}"
    return value + (",readonly" if readonly else "")


def build_fixture_command(
    *,
    container_name: str,
    mode: str,
    output_dir: Path,
    probe_id_file: Path,
) -> list[str]:
    if mode not in ({"happy"} | set(EXPECTED_FAULTS)):
        raise ValueError("fixture_mode_invalid")
    return [
        "docker",
        "create",
        "--name",
        container_name,
        "--network",
        "none",
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
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--mount",
        _mount(PROBE_SOURCE, "/probe/probe.py"),
        "--mount",
        _mount(FIXTURE_SOURCE, "/fixture/receipt_transport_fixture.py"),
        "--mount",
        _mount(probe_id_file, "/run/tickflow/probe-id"),
        "--mount",
        _mount(output_dir, "/output", readonly=mode == "permission_denied"),
        "--entrypoint",
        "/usr/bin/python3",
        IMAGE_ID,
        "/fixture/receipt_transport_fixture.py",
        mode,
    ]


def _container_exists(name: str) -> bool:
    result = _run(["docker", "container", "inspect", name], timeout=10)
    if result.returncode == 0:
        return True
    if result.returncode == 1 and "no such container" in result.stderr.lower():
        return False
    raise ReceiptHarnessError("harness_container_inspect_failed")


def _remove_container(name: str) -> None:
    if _container_exists(name):
        result = _run(["docker", "container", "rm", "--force", name], timeout=15)
        _require_success(result, "harness_container_cleanup_failed")


def cleanup_local_case_after_archive_gate(
    *,
    container_name: str,
    temporary_root: Path,
    container_started: bool,
    archive_completed: bool,
) -> tuple[int, int, bool]:
    if container_started and not archive_completed:
        return int(_container_exists(container_name)), int(temporary_root.exists()), False
    _remove_container(container_name)
    container_residue = int(_container_exists(container_name))
    shutil.rmtree(temporary_root, ignore_errors=False)
    return container_residue, int(temporary_root.exists()), True


def _prepare_archive_root(runner: ModuleType) -> None:
    if os.path.lexists(ARCHIVE_ROOT):
        metadata = os.lstat(ARCHIVE_ROOT)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ReceiptHarnessError("harness_archive_root_invalid")
        shutil.rmtree(ARCHIVE_ROOT)
    runner._ensure_directory(ARCHIVE_ROOT, mode=0o700)


def _write_probe_id(runner: ModuleType, path: Path, probe_id: str) -> None:
    runner._atomic_write_bytes(path, (probe_id + "\n").encode("ascii"), mode=0o400)


def _archive_host_state(
    runner: ModuleType,
    archive_dir: Path,
    *,
    lifecycle: Any,
    probe_id: str,
    mode: str,
    container_exit_code: int | None,
    category: str | None,
    stage: str,
    host_metadata: Mapping[str, Any] | None,
) -> None:
    runner._atomic_write_json(
        archive_dir / f"host-{stage}.json",
        {
            "archive_schema_version": 1,
            "probe_id": probe_id,
            "fixture_mode": mode,
            "network_mode": "none",
            "container_exit_code": container_exit_code,
            "transport_error_category": category,
            "host_lifecycle": lifecycle.values(),
            "host_metadata": (
                dict(host_metadata) if host_metadata is not None else None
            ),
            "provider_attempt_count": 0,
            "ai_call_count": 0,
            "secret_content_read_count": 0,
            "authorization_constructed_count": 0,
            "http_request_sent_count": 0,
            "real_public_network_success_count": 0,
        },
    )


def _run_container_case(
    *,
    runner: ModuleType,
    probe: ModuleType,
    case_id: str,
    mode: str,
    expected_category: str | None,
) -> dict[str, Any]:
    probe_id = uuid.uuid4().hex
    container_name = f"{CONTAINER_PREFIX}{case_id.lower()}-{probe_id[:12]}"
    temporary_root = runner.canonicalize_runtime_directory(
        Path(tempfile.mkdtemp(prefix=f"{CONTAINER_PREFIX}{case_id.lower()}-"))
    )
    output_dir = temporary_root / "output"
    output_dir.mkdir(mode=0o700)
    output_dir.chmod(0o700)
    output_dir = runner.canonicalize_runtime_directory(output_dir)
    probe_id_file = temporary_root / "probe-id"
    _write_probe_id(runner, probe_id_file, probe_id)
    archive_dir = ARCHIVE_ROOT / case_id
    lifecycle = runner.HostLifecycleRecorder(
        wall_clock=runner._wall_time,
        monotonic_ns=time.monotonic_ns,
    )
    result: subprocess.CompletedProcess[str] | None = None
    container_started = False
    category: str | None = None
    archive_completed = False
    cleanup_started_after_archive = False
    container_residue = 0
    temporary_residue = 0
    host_metadata: Mapping[str, Any] | None = None
    output_metadata = os.lstat(output_dir)
    host_directory_owner = (output_metadata.st_uid, output_metadata.st_gid)
    try:
        create = _run(
            build_fixture_command(
                container_name=container_name,
                mode=mode,
                output_dir=output_dir,
                probe_id_file=probe_id_file,
            )
        )
        _require_success(create, "harness_container_create_failed")
        result = _run(
            ["docker", "start", "--attach", container_name],
            timeout=30,
        )
        container_started = True
        lifecycle.record("container_wait_completed")
        try:
            bundle = runner.process_container_result(
                result=result,
                output_dir=output_dir,
                probe_id=probe_id,
                probe_module=probe,
                expected_publisher=(65532, 65532),
                expected_host_directory_owner=host_directory_owner,
                expected_host_file_owner=host_directory_owner,
            )
        except runner.ReceiptTransportError as error:
            category = error.category
            if expected_category is None or category != expected_category:
                raise ReceiptHarnessError("harness_fault_category_mismatch") from error
            runner.archive_transport_error(
                archive_dir,
                probe_id=probe_id,
                category=category,
                container_exit_code=result.returncode,
                lifecycle=lifecycle,
            )
        else:
            if expected_category is not None:
                raise ReceiptHarnessError("harness_fault_did_not_fail_closed")
            lifecycle.record("receipt_validated")
            host_metadata = bundle["host_metadata"]
            runner.archive_child_bundle(archive_dir, bundle, lifecycle)
        archive_completed = lifecycle.values()["receipt_archive_completed"]["occurred"]
        _archive_host_state(
            runner,
            archive_dir,
            lifecycle=lifecycle,
            probe_id=probe_id,
            mode=mode,
            container_exit_code=result.returncode,
            category=category,
            stage="precleanup",
            host_metadata=host_metadata,
        )
    finally:
        if archive_completed:
            lifecycle.record("cleanup_started")
            cleanup_started_after_archive = True
        (
            container_residue,
            temporary_residue,
            cleanup_performed,
        ) = cleanup_local_case_after_archive_gate(
            container_name=container_name,
            temporary_root=temporary_root,
            container_started=container_started,
            archive_completed=archive_completed,
        )
        if archive_completed and cleanup_performed:
            lifecycle.record("cleanup_completed")
            _archive_host_state(
                runner,
                archive_dir,
                lifecycle=lifecycle,
                probe_id=probe_id,
                mode=mode,
                container_exit_code=result.returncode if result is not None else None,
                category=category,
                stage="postcleanup",
                host_metadata=host_metadata,
            )
    if result is None or not archive_completed:
        raise ReceiptHarnessError("harness_archive_missing")
    result_name = "PASSED" if expected_category is None else "FAILED_CLOSED"
    return {
        "id": case_id,
        "name": mode,
        "result": result_name,
        "probe_id": probe_id,
        "container_exit_code": result.returncode,
        "transport_error_category": category,
        "network_mode": "none",
        "public_operation_count": 0,
        "archive_before_cleanup": cleanup_started_after_archive,
        "container_residue_count": container_residue,
        "network_residue_count": 0,
        "temporary_residue_count": temporary_residue,
        "host_metadata": dict(host_metadata) if host_metadata is not None else None,
        "permission_fault_mechanism": (
            "READ_ONLY_BIND_MOUNT" if mode == "permission_denied" else None
        ),
    }


def _cleanup_before_archive_case(runner: ModuleType) -> dict[str, Any]:
    probe_id = uuid.uuid4().hex
    archive_dir = ARCHIVE_ROOT / "G"
    lifecycle = runner.HostLifecycleRecorder(
        wall_clock=runner._wall_time,
        monotonic_ns=time.monotonic_ns,
    )
    rejected = False
    try:
        lifecycle.record("cleanup_started")
    except ValueError as error:
        rejected = str(error) == "archive_required_before_cleanup"
    if not rejected:
        raise ReceiptHarnessError("cleanup_before_archive_not_rejected")
    runner.archive_transport_error(
        archive_dir,
        probe_id=probe_id,
        category="RECEIPT_VALIDATION_FAILED",
        container_exit_code=None,
        lifecycle=lifecycle,
    )
    _archive_host_state(
        runner,
        archive_dir,
        lifecycle=lifecycle,
        probe_id=probe_id,
        mode="cleanup_before_archive",
        container_exit_code=None,
        category="ARCHIVE_REQUIRED_BEFORE_CLEANUP",
        stage="precleanup",
        host_metadata=None,
    )
    lifecycle.record("cleanup_started")
    lifecycle.record("cleanup_completed")
    _archive_host_state(
        runner,
        archive_dir,
        lifecycle=lifecycle,
        probe_id=probe_id,
        mode="cleanup_before_archive",
        container_exit_code=None,
        category="ARCHIVE_REQUIRED_BEFORE_CLEANUP",
        stage="postcleanup",
        host_metadata=None,
    )
    return {
        "id": "G",
        "name": "cleanup_before_archive",
        "result": "FAILED_CLOSED",
        "probe_id": probe_id,
        "container_exit_code": None,
        "transport_error_category": "ARCHIVE_REQUIRED_BEFORE_CLEANUP",
        "network_mode": "none",
        "public_operation_count": 0,
        "archive_before_cleanup": True,
        "container_residue_count": 0,
        "network_residue_count": 0,
        "temporary_residue_count": 0,
    }


def _archive_manifest() -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for path in sorted(ARCHIVE_ROOT.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        values.append(
            {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return values


def main() -> int:
    runner = _load_module(RUNNER_SOURCE, "receipt_transport_runner")
    probe = _load_module(PROBE_SOURCE, "receipt_transport_probe")
    fixture = _load_module(FIXTURE_SOURCE, "receipt_transport_fixture")
    if fixture.MODES != ({"happy"} | set(EXPECTED_FAULTS)):
        raise ReceiptHarnessError("fixture_modes_invalid")
    if runner.verify_receipt_transport_history(REPO_ROOT, HISTORY_PATH) != 7:
        raise ReceiptHarnessError("historical_evidence_invalid")
    image = _run(["docker", "image", "inspect", IMAGE_ID, "--format", "{{.Id}}"])
    _require_success(image, "harness_image_missing")
    if image.stdout.strip() != IMAGE_ID:
        raise ReceiptHarnessError("harness_image_identity_mismatch")
    _prepare_archive_root(runner)

    happy_runs = [
        _run_container_case(
            runner=runner,
            probe=probe,
            case_id=f"A{index}",
            mode="happy",
            expected_category=None,
        )
        for index in range(1, 4)
    ]
    first_happy = dict(happy_runs[0])
    first_happy.update({"id": "A", "name": "happy_path"})
    fault_cases = []
    for case_id, mode in zip("BCDEF", EXPECTED_FAULTS, strict=True):
        fault_cases.append(
            _run_container_case(
                runner=runner,
                probe=probe,
                case_id=case_id,
                mode=mode,
                expected_category=EXPECTED_FAULTS[mode],
            )
        )
    cleanup_fault = _cleanup_before_archive_case(runner)
    archive_success = {
        "id": "H",
        "name": "archive_before_cleanup",
        "result": "PASSED",
        "archive_before_cleanup": all(
            item["archive_before_cleanup"] for item in happy_runs
        ),
        "network_mode": "none",
        "public_operation_count": 0,
    }
    repeated_happy = {
        "id": "I",
        "name": "three_independent_happy_runs",
        "result": "PASSED",
        "independent_happy_run_count": len(happy_runs),
        "probe_ids": [item["probe_id"] for item in happy_runs],
        "all_exit_zero": all(item["container_exit_code"] == 0 for item in happy_runs),
        "network_mode": "none",
        "public_operation_count": 0,
    }
    scenarios = [first_happy, *fault_cases, cleanup_fault, archive_success, repeated_happy]
    all_runtime_cases = [*happy_runs, *fault_cases, cleanup_fault]
    if runner.verify_receipt_transport_history(REPO_ROOT, HISTORY_PATH) != 7:
        raise ReceiptHarnessError("historical_evidence_mutated")
    report = {
        "receipt_transport_harness_schema_version": 1,
        "generated_at": runner._wall_time(),
        "status": "PASSED",
        "proxy_image_id": IMAGE_ID,
        "probe_source_sha256": hashlib.sha256(PROBE_SOURCE.read_bytes()).hexdigest(),
        "fixture_source_sha256": hashlib.sha256(FIXTURE_SOURCE.read_bytes()).hexdigest(),
        "runner_source_sha256": hashlib.sha256(RUNNER_SOURCE.read_bytes()).hexdigest(),
        "harness_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "network_mode": "none",
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "happy_path_runs": len(happy_runs),
        "happy_path_all_exit_zero": all(
            item["container_exit_code"] == 0 for item in happy_runs
        ),
        "happy_path_all_archived_before_cleanup": all(
            item["archive_before_cleanup"] for item in happy_runs
        ),
        "fault_injection_all_fail_closed": all(
            item["result"] == "FAILED_CLOSED" for item in [*fault_cases, cleanup_fault]
        ),
        "historical_file_count": 7,
        "historical_evidence": "UNCHANGED",
        "historical_probe_id": "5dd641ca9e4b4ccba1035fff4d695409",
        "historical_probe_status": "PRESERVED",
        "archive_root": ARCHIVE_ROOT.relative_to(REPO_ROOT).as_posix(),
        "archived_files": _archive_manifest(),
        "real_public_network_success_count": 0,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "secret_content_read_count": 0,
        "authorization_constructed_count": 0,
        "http_request_sent_count": 0,
        "container_residue_count": sum(
            item["container_residue_count"] for item in all_runtime_cases
        ),
        "network_residue_count": 0,
        "temporary_residue_count": sum(
            item["temporary_residue_count"] for item in all_runtime_cases
        ),
    }
    runner._atomic_write_json(EVIDENCE_PATH, report)
    print(json.dumps({"status": report["status"], "scenarios": len(scenarios)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
