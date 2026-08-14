from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "docker/phase2-ark-tls-connectivity-probe/probe.py"
RUNNER_PATH = REPO_ROOT / "backend/scripts/run_phase2_ark_tls_connectivity_probe.py"
HISTORY_PATH = (
    REPO_ROOT
    / "reports/phase2_provider_ark/tls_receipt_transport/historical_probe_baseline.json"
)
PROBE_ID = "a" * 32


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def probe_module() -> ModuleType:
    return _load_module(PROBE_PATH, "phase2_ark_receipt_transport_probe")


@pytest.fixture
def runner_module() -> ModuleType:
    return _load_module(RUNNER_PATH, "phase2_ark_receipt_transport_runner")


def _valid_receipt(probe_module: ModuleType) -> dict[str, Any]:
    counter = iter(range(1, 100))
    recorder = probe_module.ProbeEventRecorder(
        wall_clock=lambda: "2026-08-14T00:00:00Z",
        monotonic_ns=lambda: next(counter),
    )
    for name in probe_module.CHILD_SUCCESS_EVENTS:
        recorder.record(name)
    receipt = probe_module._base_receipt(PROBE_ID, recorder)
    receipt.update(
        {
            "terminal_status": "PROBE_PASSED",
            "tls_error_category": "NONE",
            "tls_version": "LOCAL_ONLY",
            "cipher_name": "LOCAL_ONLY",
            "san_contains_hostname": True,
            "events": recorder.values(),
        }
    )
    probe_module.validate_probe_receipt(receipt, require_cleanup=False)
    return receipt


def _write_valid_bundle(
    probe_module: ModuleType,
    output_dir: Path,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    receipt = _valid_receipt(probe_module)
    marker = probe_module.publish_receipt_bundle(receipt, output_dir=output_dir)
    raw = (output_dir / "child-receipt.json").read_bytes()
    return receipt, raw, marker


def test_user_created_ancestor_symlink_is_rejected(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    real = tmp_path / "private" / "var"
    real.mkdir(parents=True)
    alias = tmp_path / "system-alias"
    alias.symlink_to(real, target_is_directory=True)
    output = alias / "probe" / "output"
    output.mkdir(parents=True, mode=0o700)
    with pytest.raises(ValueError, match="runtime_path_symlink"):
        runner_module.canonicalize_runtime_directory(output)


def test_verified_macos_var_alias_is_allowed(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    canonical = tmp_path.resolve(strict=True)
    canonical_text = str(canonical)
    if not canonical_text.startswith("/private/var/"):
        pytest.skip("test requires the macOS /var to /private/var system alias")
    logical = Path("/var") / canonical.relative_to("/private/var")

    assert runner_module.canonicalize_runtime_directory(logical) == canonical


def test_historical_probe_baseline_matches_exact_file_set(
    runner_module: ModuleType,
) -> None:
    assert (
        runner_module.verify_receipt_transport_history(REPO_ROOT, HISTORY_PATH)
        == 7
    )


@pytest.mark.parametrize("unexpected", ["broken_symlink", "empty_directory"])
def test_historical_probe_baseline_rejects_every_unlisted_entry(
    runner_module: ModuleType,
    tmp_path: Path,
    unexpected: str,
) -> None:
    baseline = json.loads(HISTORY_PATH.read_bytes())
    for item in baseline["files"]:
        source = REPO_ROOT / item["path"]
        destination = tmp_path / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    baseline_path = tmp_path / "receipt-history.json"
    baseline_path.write_bytes(HISTORY_PATH.read_bytes())
    history_root = tmp_path / "reports/phase2_provider_ark/tls_connectivity_probe"
    if unexpected == "broken_symlink":
        (history_root / "unexpected").symlink_to(history_root / "missing")
    else:
        (history_root / "unexpected").mkdir()

    with pytest.raises(
        ValueError,
        match="receipt_transport_history_(invalid|set_changed)",
    ):
        runner_module.verify_receipt_transport_history(tmp_path, baseline_path)


def test_happy_publication_is_durable_and_marker_matches_receipt(
    probe_module: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    receipt, raw, marker = _write_valid_bundle(probe_module, output)

    assert sorted(path.name for path in output.iterdir()) == [
        "child-receipt.json",
        "receipt.ready",
    ]
    assert marker["publication_complete"] is True
    assert marker["probe_id"] == receipt["probe_id"]
    assert marker["receipt_sha256"] == hashlib.sha256(raw).hexdigest()
    assert stat.S_IMODE((output / "child-receipt.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((output / "receipt.ready").stat().st_mode) == 0o600
    assert not (output / "child-receipt.json").is_symlink()
    assert not (output / "receipt.ready").is_symlink()
    assert json.loads(raw) == receipt
    assert json.loads((output / "receipt.ready").read_bytes()) == marker


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("open", "RECEIPT_OPEN_FAILED"),
        ("chmod", "RECEIPT_OPEN_FAILED"),
        ("write", "RECEIPT_WRITE_FAILED"),
        ("fsync", "RECEIPT_FSYNC_FAILED"),
        ("rename", "RECEIPT_RENAME_FAILED"),
        ("parent_fsync", "RECEIPT_PARENT_FSYNC_FAILED"),
        ("validation", "RECEIPT_VALIDATION_FAILED"),
    ],
)
def test_publication_fault_has_exact_category(
    probe_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str,
    expected: str,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    receipt = _valid_receipt(probe_module)
    if fault == "validation":
        receipt["unexpected"] = True
    elif fault == "open":
        monkeypatch.setattr(
            probe_module.tempfile,
            "mkstemp",
            lambda **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )
    elif fault == "write":
        monkeypatch.setattr(
            probe_module.os,
            "write",
            lambda *_args: (_ for _ in ()).throw(OSError("write failed")),
        )
    elif fault == "chmod":
        monkeypatch.setattr(
            probe_module.os,
            "fchmod",
            lambda *_args: (_ for _ in ()).throw(OSError("chmod failed")),
        )
    elif fault == "rename":
        monkeypatch.setattr(
            probe_module.os,
            "replace",
            lambda *_args: (_ for _ in ()).throw(OSError("rename failed")),
        )
    elif fault in {"fsync", "parent_fsync"}:
        original_fsync = probe_module.os.fsync
        calls = 0

        def injected_fsync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            target = 1 if fault == "fsync" else 2
            if calls == target:
                raise OSError(f"{fault} failed")
            original_fsync(descriptor)

        monkeypatch.setattr(probe_module.os, "fsync", injected_fsync)

    with pytest.raises(probe_module.ReceiptPublicationError) as raised:
        probe_module.publish_receipt_bundle(receipt, output_dir=output)
    assert raised.value.category == expected


def test_probe_id_is_read_only_separate_mount_and_output_is_mode_0700(
    runner_module: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    probe_id_file = tmp_path / "probe-id"
    probe_id_file.write_text(PROBE_ID + "\n", encoding="ascii")
    command = runner_module.build_probe_container_command(
        probe_id=PROBE_ID,
        container_name="local-receipt",
        network_name="none",
        output_dir=output,
        probe_source=PROBE_PATH,
        probe_id_file=probe_id_file,
    )

    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    assert len(mounts) == 3
    assert any("dst=/run/tickflow/probe-id,readonly" in value for value in mounts)
    assert any("dst=/output" in value and "readonly" not in value for value in mounts)
    assert stat.S_IMODE(output.stat().st_mode) == 0o700


def test_exit_zero_without_host_visible_receipt_fails_closed(
    runner_module: ModuleType,
    probe_module: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    result = subprocess.CompletedProcess(
        ["docker", "start", "--attach", "local-fixture"], 0, "", ""
    )

    with pytest.raises(runner_module.ReceiptTransportError) as raised:
        runner_module.process_container_result(
            result=result,
            output_dir=output,
            probe_id=PROBE_ID,
            probe_module=probe_module,
        )
    assert raised.value.category == "RECEIPT_OPEN_FAILED"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_ready",
        "wrong_hash",
        "wrong_probe_id",
        "malformed_receipt",
        "unexpected_file",
        "symlink_receipt",
    ],
)
def test_host_rejects_incomplete_or_tampered_bundle(
    runner_module: ModuleType,
    probe_module: ModuleType,
    tmp_path: Path,
    mutation: str,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    _receipt, _raw, marker = _write_valid_bundle(probe_module, output)
    if mutation == "missing_ready":
        (output / "receipt.ready").unlink()
    elif mutation == "wrong_hash":
        marker["receipt_sha256"] = "0" * 64
        (output / "receipt.ready").write_text(json.dumps(marker), encoding="utf-8")
    elif mutation == "wrong_probe_id":
        marker["probe_id"] = "b" * 32
        (output / "receipt.ready").write_text(json.dumps(marker), encoding="utf-8")
    elif mutation == "malformed_receipt":
        (output / "child-receipt.json").write_text("{", encoding="utf-8")
    elif mutation == "unexpected_file":
        (output / "unexpected").write_text("x", encoding="ascii")
    elif mutation == "symlink_receipt":
        receipt_path = output / "child-receipt.json"
        receipt_path.unlink()
        receipt_path.symlink_to(tmp_path / "outside")

    with pytest.raises(runner_module.ReceiptTransportError):
        runner_module.read_receipt_bundle(output, PROBE_ID, probe_module)


def test_valid_bundle_is_accepted_for_matching_container_exit(
    runner_module: ModuleType,
    probe_module: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    receipt, raw, marker = _write_valid_bundle(probe_module, output)
    result = subprocess.CompletedProcess(["docker", "start"], 0, "", "")

    bundle = runner_module.process_container_result(
        result=result,
        output_dir=output,
        probe_id=PROBE_ID,
        probe_module=probe_module,
    )

    assert bundle["receipt"] == receipt
    assert bundle["receipt_bytes"] == raw
    assert bundle["marker"] == marker


def test_nonzero_child_publication_category_wins_over_visible_valid_bundle(
    runner_module: ModuleType,
    probe_module: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    _write_valid_bundle(probe_module, output)
    result = subprocess.CompletedProcess(
        ["docker", "start"],
        70,
        "",
        json.dumps(
            {
                "category": "RECEIPT_PARENT_FSYNC_FAILED",
                "receipt_transport_schema_version": 1,
            }
        ),
    )

    with pytest.raises(
        runner_module.ReceiptTransportError,
        match="RECEIPT_PARENT_FSYNC_FAILED",
    ):
        runner_module.process_container_result(
            result=result,
            output_dir=output,
            probe_id=PROBE_ID,
            probe_module=probe_module,
        )


@pytest.mark.parametrize("target", ["directory", "receipt", "marker"])
def test_host_rejects_unsafe_runtime_modes(
    runner_module: ModuleType,
    probe_module: ModuleType,
    tmp_path: Path,
    target: str,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    _write_valid_bundle(probe_module, output)
    path = {
        "directory": output,
        "receipt": output / "child-receipt.json",
        "marker": output / "receipt.ready",
    }[target]
    path.chmod(0o755 if target == "directory" else 0o644)

    with pytest.raises(
        runner_module.ReceiptTransportError,
        match="RECEIPT_VALIDATION_FAILED",
    ):
        runner_module.read_receipt_bundle(output, PROBE_ID, probe_module)


def test_host_enforces_container_and_host_ownership_contracts(
    runner_module: ModuleType,
    probe_module: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    _receipt, _raw, marker = _write_valid_bundle(probe_module, output)
    marker["directory_uid"] += 1
    marker_path = output / "receipt.ready"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    marker_path.chmod(0o600)

    with pytest.raises(
        runner_module.ReceiptTransportError,
        match="RECEIPT_VALIDATION_FAILED",
    ):
        runner_module.read_receipt_bundle(
            output,
            PROBE_ID,
            probe_module,
            expected_publisher=(os.getuid(), os.getgid()),
            expected_host_directory_owner=(os.getuid(), os.getgid()),
            expected_host_file_owner=(os.getuid(), os.getgid()),
        )

    marker["directory_uid"] = marker["publisher_uid"]
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    marker_path.chmod(0o600)
    with pytest.raises(
        runner_module.ReceiptTransportError,
        match="RECEIPT_VALIDATION_FAILED",
    ):
        runner_module.read_receipt_bundle(
            output,
            PROBE_ID,
            probe_module,
            expected_publisher=(os.getuid(), os.getgid()),
            expected_host_directory_owner=(os.getuid(), os.getgid()),
            expected_host_file_owner=(os.getuid(), os.getgid() + 1),
        )


def test_dispatched_archive_failure_preserves_runtime_for_recovery(
    runner_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(runner_module, "_container_exists", lambda _name: True)
    monkeypatch.setattr(runner_module, "_network_exists", lambda _name: True)
    monkeypatch.setattr(
        runner_module,
        "_cleanup",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cleanup forbidden")),
    )

    result = runner_module.cleanup_runtime_after_archive_gate(
        container_name="container",
        network_name="network",
        temporary_root=runtime,
        dispatched=True,
        archive_completed=False,
    )

    assert result == (1, 1, 1, False)
    assert runtime.is_dir()


def test_validated_bundle_archive_failure_never_downgrades_to_error_only_archive(
    runner_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "reports"
    output_root.mkdir()
    attempts_root = output_root / "attempts"
    attempts_root.mkdir(mode=0o700)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    container_exists = False
    network_exists = False

    def fake_run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal container_exists, network_exists
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(
                command, 0, runner_module.PROXY_IMAGE_ID + "\n", ""
            )
        if command[:3] == ["docker", "network", "create"]:
            network_exists = True
            return subprocess.CompletedProcess(command, 0, "network\n", "")
        if command[:2] == ["docker", "create"]:
            container_exists = True
            return subprocess.CompletedProcess(command, 0, "container\n", "")
        if command[:3] == ["docker", "start", "--attach"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(runner_module, "OUTPUT_ROOT", output_root)
    monkeypatch.setattr(runner_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner_module, "BASELINE_PATH", tmp_path / "baseline.json")
    monkeypatch.setattr(runner_module, "LOCAL_APPROVAL_PATH", tmp_path / "approval.json")
    monkeypatch.setattr(runner_module, "V2_ATTEMPTS_ROOT", attempts_root)
    monkeypatch.setattr(
        runner_module,
        "load_probe_v2_execution_identity",
        lambda *_args, **_kwargs: {
            "approval_candidate_sha256": "a" * 64,
            "approval_scope_id": "b" * 64,
            "source_git_head": "c" * 40,
            "historical_attempts": 0,
            "attempt_availability": "AVAILABLE",
            "probe_source_sha256": "d" * 64,
            "host_orchestrator_sha256": "e" * 64,
        },
    )
    monkeypatch.setattr(
        runner_module.uuid,
        "uuid4",
        lambda: type("FixedUUID", (), {"hex": PROBE_ID})(),
    )
    monkeypatch.setattr(
        runner_module,
        "verify_historical_baseline",
        lambda *_args: 1,
    )
    monkeypatch.setattr(runner_module.tempfile, "mkdtemp", lambda **_kwargs: str(runtime))
    monkeypatch.setattr(runner_module, "_run", fake_run)
    monkeypatch.setattr(
        runner_module,
        "process_container_result",
        lambda **_kwargs: {
            "receipt": {"probe_id": PROBE_ID},
            "receipt_bytes": b"{}\n",
            "marker": {},
            "marker_bytes": b"{}\n",
            "host_metadata": {},
        },
    )
    monkeypatch.setattr(
        runner_module,
        "archive_child_bundle",
        lambda *_args: (_ for _ in ()).throw(OSError("archive failed")),
    )
    monkeypatch.setattr(
        runner_module,
        "archive_transport_error",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("error-only archive is insufficient")
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "_container_exists",
        lambda _name: container_exists,
    )
    monkeypatch.setattr(
        runner_module,
        "_network_exists",
        lambda _name: network_exists,
    )
    monkeypatch.setattr(
        runner_module,
        "_cleanup",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cleanup forbidden")),
    )

    with pytest.raises(OSError, match="archive failed"):
        runner_module.run_live_probe()

    assert runtime.is_dir()
    ledger = json.loads(
        (attempts_root / ("b" * 64) / PROBE_ID / "ledger.json").read_bytes()
    )
    assert ledger["state"] == "NETWORK_DISPATCH_STARTED"
    assert ledger["probe_attempt_count"] == 1


def test_cleanup_cannot_start_before_archive(runner_module: ModuleType) -> None:
    counter = iter(range(1, 100))
    lifecycle = runner_module.HostLifecycleRecorder(
        wall_clock=lambda: "2026-08-14T00:00:00Z",
        monotonic_ns=lambda: next(counter),
    )

    with pytest.raises(ValueError, match="archive_required_before_cleanup"):
        lifecycle.record("cleanup_started")


def test_archive_is_durable_before_cleanup_event(
    runner_module: ModuleType,
    probe_module: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    _write_valid_bundle(probe_module, output)
    bundle = runner_module.read_receipt_bundle(output, PROBE_ID, probe_module)
    counter = iter(range(1, 100))
    lifecycle = runner_module.HostLifecycleRecorder(
        wall_clock=lambda: "2026-08-14T00:00:00Z",
        monotonic_ns=lambda: next(counter),
    )
    lifecycle.record("container_wait_completed")
    lifecycle.record("receipt_validated")
    archive = tmp_path / "archive"

    runner_module.archive_child_bundle(archive, bundle, lifecycle)
    lifecycle.record("cleanup_started")

    assert (archive / "child-receipt.json").read_bytes() == bundle["receipt_bytes"]
    assert json.loads((archive / "receipt.ready").read_bytes()) == bundle["marker"]
    events = lifecycle.values()
    assert events["receipt_archive_completed"]["monotonic_ns"] < events[
        "cleanup_started"
    ]["monotonic_ns"]
