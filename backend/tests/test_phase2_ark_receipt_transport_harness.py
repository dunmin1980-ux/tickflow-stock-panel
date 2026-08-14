from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = (
    REPO_ROOT / "backend/scripts/run_phase2_ark_receipt_transport_harness.py"
)
FIXTURE_PATH = (
    REPO_ROOT
    / "docker/phase2-ark-tls-connectivity-probe/receipt_transport_fixture.py"
)
EVIDENCE_PATH = (
    REPO_ROOT
    / "reports/phase2_provider_ark/tls_receipt_transport/local_harness.json"
)
PROBE_PATH = REPO_ROOT / "docker/phase2-ark-tls-connectivity-probe/probe.py"
PROBE_ID = "a" * 32


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_harness_files_and_aggregate_evidence_exist() -> None:
    assert FIXTURE_PATH.is_file()
    assert HARNESS_PATH.is_file()
    assert EVIDENCE_PATH.is_file()


def test_fixture_container_contract_is_networkless_and_minimal(tmp_path: Path) -> None:
    harness = _load_module(HARNESS_PATH, "phase2_ark_receipt_transport_harness")
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    probe_id_file = tmp_path / "probe-id"
    probe_id_file.write_text(PROBE_ID + "\n", encoding="ascii")

    command = harness.build_fixture_command(
        container_name="phase2-ark-receipt-test",
        mode="happy",
        output_dir=output,
        probe_id_file=probe_id_file,
    )

    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--user") + 1] == "65532:65532"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    mounts = [command[index + 1] for index, item in enumerate(command) if item == "--mount"]
    assert len(mounts) == 4
    assert sum("readonly" not in mount for mount in mounts) == 1
    assert any("dst=/output" in mount and "readonly" not in mount for mount in mounts)
    assert all("ark.cn-beijing.volces.com" not in item for item in command)


def test_container_inspect_error_cannot_be_reported_as_zero_residue(
    monkeypatch,
) -> None:
    harness = _load_module(HARNESS_PATH, "phase2_ark_receipt_transport_inspect")
    monkeypatch.setattr(
        harness,
        "_run",
        lambda *_args, **_kwargs: __import__("subprocess").CompletedProcess(
            ["docker", "container", "inspect"],
            1,
            "",
            "permission denied",
        ),
    )

    with __import__("pytest").raises(
        harness.ReceiptHarnessError,
        match="harness_container_inspect_failed",
    ):
        harness._container_exists("container")


def test_permission_denied_uses_a_real_read_only_output_mount(tmp_path: Path) -> None:
    harness = _load_module(HARNESS_PATH, "phase2_ark_receipt_transport_permissions")
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    probe_id_file = tmp_path / "probe-id"
    probe_id_file.write_text(PROBE_ID + "\n", encoding="ascii")

    command = harness.build_fixture_command(
        container_name="phase2-ark-receipt-permission",
        mode="permission_denied",
        output_dir=output,
        probe_id_file=probe_id_file,
    )

    mounts = [command[index + 1] for index, item in enumerate(command) if item == "--mount"]
    assert all("readonly" in mount for mount in mounts)
    fixture_source = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "probe.tempfile.mkstemp =" not in fixture_source


def test_started_harness_case_preserves_runtime_when_archive_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    harness = _load_module(HARNESS_PATH, "phase2_ark_receipt_transport_cleanup")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(harness, "_container_exists", lambda _name: True)
    monkeypatch.setattr(
        harness,
        "_remove_container",
        lambda _name: (_ for _ in ()).throw(AssertionError("cleanup forbidden")),
    )

    result = harness.cleanup_local_case_after_archive_gate(
        container_name="container",
        temporary_root=runtime,
        container_started=True,
        archive_completed=False,
    )

    assert result == (1, 1, False)
    assert runtime.is_dir()


def test_aggregate_evidence_proves_all_nine_requirements() -> None:
    report = json.loads(EVIDENCE_PATH.read_bytes())

    assert report["receipt_transport_harness_schema_version"] == 1
    assert report["status"] == "PASSED"
    assert report["network_mode"] == "none"
    assert report["scenario_count"] == 9
    assert [item["id"] for item in report["scenarios"]] == list("ABCDEFGHI")
    assert report["happy_path_runs"] == 3
    assert report["happy_path_all_exit_zero"] is True
    assert report["happy_path_all_archived_before_cleanup"] is True
    assert report["fault_injection_all_fail_closed"] is True
    assert report["historical_file_count"] == 7
    assert report["historical_evidence"] == "UNCHANGED"
    assert report["historical_probe_id"] == "5dd641ca9e4b4ccba1035fff4d695409"
    assert report["historical_probe_status"] == "PRESERVED"
    assert report["real_public_network_success_count"] == 0
    assert report["provider_attempt_count"] == 0
    assert report["ai_call_count"] == 0
    assert report["secret_content_read_count"] == 0
    assert report["authorization_constructed_count"] == 0
    assert report["http_request_sent_count"] == 0
    assert report["container_residue_count"] == 0
    assert report["network_residue_count"] == 0
    assert report["temporary_residue_count"] == 0


def test_fault_categories_and_three_independent_happy_runs_are_exact() -> None:
    report = json.loads(EVIDENCE_PATH.read_bytes())
    scenarios = {item["id"]: item for item in report["scenarios"]}

    assert scenarios["A"]["result"] == "PASSED"
    assert scenarios["B"]["transport_error_category"] == "RECEIPT_OPEN_FAILED"
    assert scenarios["B"]["permission_fault_mechanism"] == "READ_ONLY_BIND_MOUNT"
    assert scenarios["C"]["transport_error_category"] == "RECEIPT_OPEN_FAILED"
    assert scenarios["D"]["transport_error_category"] == "RECEIPT_RENAME_FAILED"
    assert scenarios["E"]["transport_error_category"] == "RECEIPT_OPEN_FAILED"
    assert scenarios["F"]["transport_error_category"] == "RECEIPT_VALIDATION_FAILED"
    assert scenarios["G"]["transport_error_category"] == (
        "ARCHIVE_REQUIRED_BEFORE_CLEANUP"
    )
    assert scenarios["H"]["archive_before_cleanup"] is True
    assert scenarios["I"]["independent_happy_run_count"] == 3
    assert len(set(scenarios["I"]["probe_ids"])) == 3
    assert all(
        item["network_mode"] == "none" and item["public_operation_count"] == 0
        for item in report["scenarios"]
    )


def test_archives_are_regular_and_match_reported_hashes() -> None:
    report = json.loads(EVIDENCE_PATH.read_bytes())
    archive_root = REPO_ROOT / report["archive_root"]
    assert archive_root.is_dir() and not archive_root.is_symlink()

    for item in report["archived_files"]:
        path = REPO_ROOT / item["path"]
        assert path.is_file() and not path.is_symlink()
        assert item["sha256"] == __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def test_evidence_is_bound_to_exact_local_sources() -> None:
    hashlib = __import__("hashlib")
    report = json.loads(EVIDENCE_PATH.read_bytes())
    expected = {
        "probe_source_sha256": PROBE_PATH,
        "fixture_source_sha256": FIXTURE_PATH,
        "runner_source_sha256": (
            REPO_ROOT / "backend/scripts/run_phase2_ark_tls_connectivity_probe.py"
        ),
        "harness_source_sha256": HARNESS_PATH,
    }
    for field, path in expected.items():
        assert report[field] == hashlib.sha256(path.read_bytes()).hexdigest()
