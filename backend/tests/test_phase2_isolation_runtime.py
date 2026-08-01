from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from app.services.phase2_ai_worker_protocol import (
    WORKER_CANDIDATE_VALID,
    build_worker_projection,
    validate_worker_candidate,
)
from app.services.phase2_claims_service import canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_PATH = REPO_ROOT / "docker/phase2-ai-worker/worker.py"
WORKER_CONTEXT = REPO_ROOT / "docker/phase2-ai-worker"


def _load_worker() -> ModuleType:
    assert WORKER_PATH.is_file(), "isolated fake worker module is missing"
    spec = importlib.util.spec_from_file_location(
        "phase2_isolated_fake_worker",
        WORKER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime() -> ModuleType:
    spec = importlib.util.find_spec("app.services.phase2_isolation_runtime")
    assert spec is not None, "phase2 isolation runtime module is missing"
    return importlib.import_module("app.services.phase2_isolation_runtime")


def _projection(symbol: str = "000403.SZ") -> dict:
    facts_path = (
        REPO_ROOT
        / "reports/phase2_facts"
        / f'{symbol.replace(".", "")}_facts.json'
    )
    facts_bytes = facts_path.read_bytes()
    facts = json.loads(facts_bytes)
    return build_worker_projection(
        facts,
        hashlib.sha256(facts_bytes).hexdigest(),
        facts_bytes=facts_bytes,
    )


def test_fake_provider_generates_complete_host_valid_candidate() -> None:
    worker = _load_worker()
    projection = _projection()

    candidate = worker.generate_candidate(projection)
    validation = validate_worker_candidate(candidate, projection)

    assert validation.status == WORKER_CANDIDATE_VALID
    assert validation.errors == []
    assert validation.claim_count == 42
    assert candidate["symbol"] == "000403.SZ"
    assert candidate["trading_advice"] is False


def test_fake_provider_generation_is_byte_deterministic() -> None:
    worker = _load_worker()
    projection = _projection()

    first = canonical_json_bytes(worker.generate_candidate(projection))
    second = canonical_json_bytes(worker.generate_candidate(projection))

    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_worker_runtime_has_a_standard_library_only_dependency_surface() -> None:
    assert WORKER_PATH.is_file(), "isolated fake worker module is missing"
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imported_roots <= {
        "__future__",
        "collections",
        "errno",
        "hashlib",
        "itertools",
        "json",
        "math",
        "os",
        "pathlib",
        "socket",
        "subprocess",
        "sys",
        "typing",
    }


def test_worker_mount_placeholders_are_small_regular_safe_files() -> None:
    expected = {
        "projection.placeholder.json": b"{}\n",
        "candidate.placeholder.json": b"\n",
    }

    for filename, content in expected.items():
        path = WORKER_CONTEXT / filename
        assert path.is_file() and not path.is_symlink()
        assert path.read_bytes() == content
        assert path.stat().st_size < 128


def _runtime_paths(tmp_path: Path):
    runtime = _runtime()
    projection = tmp_path / "projection.json"
    output = tmp_path / "candidate.json"
    projection.write_text("{}\n", encoding="utf-8")
    output.write_text("\n", encoding="utf-8")
    return runtime.IsolationPaths(projection=projection, output=output)


def _inspect_payload(paths, *, state: str = "created") -> list[dict]:
    return [
        {
            "Config": {
                "User": "65532:65532",
                "Env": [
                    "PYTHONDONTWRITEBYTECODE=1",
                    "PYTHONHASHSEED=0",
                ],
            },
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "PidsLimit": 16,
                "Memory": 134_217_728,
                "NanoCpus": 500_000_000,
                "IpcMode": "none",
                "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(paths.projection),
                    "Destination": "/input/projection.json",
                    "RW": False,
                },
                {
                    "Type": "bind",
                    "Source": str(paths.output),
                    "Destination": "/output/candidate.json",
                    "RW": True,
                },
            ],
            "State": {"Status": state, "ExitCode": 0},
        }
    ]


def test_container_create_command_enforces_every_runtime_boundary(
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    paths = _runtime_paths(tmp_path)

    command = runtime.build_container_create_command(
        "tickflow-phase2-fake-worker:runtime-v1",
        "phase2-runtime-test",
        "candidate",
        paths,
    )
    joined = " ".join(command)

    assert command[:2] == ["docker", "create"]
    assert command[-2:] == ["tickflow-phase2-fake-worker:runtime-v1", "candidate"]
    assert command.count("--mount") == 2
    for expected in (
        "--network none",
        "--read-only",
        "--user 65532:65532",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit 16",
        "--memory 128m",
        "--cpus 0.5",
        "--ipc none",
        "--restart no",
        "dst=/input/projection.json,readonly",
        "dst=/output/candidate.json",
    ):
        assert expected in joined
    for forbidden in (
        "--privileged",
        "--network host",
        ".ssh",
        ".config",
        ".obsidian",
        "docker.sock",
        "--env-file",
    ):
        assert forbidden not in joined


def test_container_inspect_contract_accepts_only_the_exact_runtime(
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    paths = _runtime_paths(tmp_path)

    evidence = runtime.validate_container_inspect(
        _inspect_payload(paths),
        paths,
        expected_state="created",
    )

    assert evidence.errors == []
    assert evidence.contract_valid is True
    assert evidence.mount_count == 2
    assert evidence.network_none is True
    assert evidence.rootfs_readonly is True
    assert evidence.non_root is True


def test_container_inspect_contract_rejects_each_security_regression(
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    paths = _runtime_paths(tmp_path)
    mutations = {
        "runtime_user_invalid": ("Config", "User", "0:0"),
        "runtime_network_not_none": ("HostConfig", "NetworkMode", "bridge"),
        "runtime_rootfs_not_readonly": ("HostConfig", "ReadonlyRootfs", False),
        "runtime_cap_drop_invalid": ("HostConfig", "CapDrop", []),
        "runtime_security_opt_invalid": ("HostConfig", "SecurityOpt", []),
        "runtime_pids_limit_invalid": ("HostConfig", "PidsLimit", 0),
        "runtime_memory_limit_invalid": ("HostConfig", "Memory", 0),
        "runtime_cpu_limit_invalid": ("HostConfig", "NanoCpus", 0),
        "runtime_ipc_mode_invalid": ("HostConfig", "IpcMode", "private"),
    }

    for expected_error, (section, field, value) in mutations.items():
        payload = _inspect_payload(paths)
        payload[0][section][field] = value
        evidence = runtime.validate_container_inspect(
            payload,
            paths,
            expected_state="created",
        )
        assert expected_error in evidence.errors

    payload = _inspect_payload(paths)
    payload[0]["Mounts"].append(
        {
            "Type": "bind",
            "Source": "/forbidden",
            "Destination": "/workspace",
            "RW": False,
        }
    )
    evidence = runtime.validate_container_inspect(
        payload,
        paths,
        expected_state="created",
    )
    assert "runtime_mount_set_invalid" in evidence.errors


def test_host_revalidates_worker_candidate_claims_and_renderer() -> None:
    runtime = _runtime()
    worker = _load_worker()
    projection = _projection()
    candidate = worker.generate_candidate(projection)

    evidence = runtime.validate_isolated_candidate(
        REPO_ROOT,
        candidate,
        projection,
    )

    assert evidence.errors == []
    assert evidence.worker_status == "WORKER_CANDIDATE_VALID"
    assert evidence.claims_status == "CLAIMS_VALID"
    assert evidence.renderer_status == "RENDERED_VALID"
    assert evidence.claim_count == 42
    assert len(evidence.candidate_sha256) == 64
    assert len(evidence.rendered_sha256) == 64


def test_host_adapter_blocks_candidate_tampering_before_rendering() -> None:
    runtime = _runtime()
    worker = _load_worker()
    projection = _projection()
    original = worker.generate_candidate(projection)
    mutations = []

    bad_sha = copy.deepcopy(original)
    bad_sha["claims"][0]["provenance"]["facts_sha256"] = "0" * 64
    mutations.append(bad_sha)

    freeform = copy.deepcopy(original)
    freeform["claims"][0]["analysis"] = "arbitrary prose"
    mutations.append(freeform)

    mixed_basis = copy.deepcopy(original)
    comparison = next(
        claim
        for claim in mixed_basis["claims"]
        if claim["predicate"] == "macd_dif_vs_dea"
    )
    comparison["object"]["left"]["price_basis"] = "raw"
    mutations.append(mixed_basis)

    for candidate in mutations:
        evidence = runtime.validate_isolated_candidate(
            REPO_ROOT,
            candidate,
            projection,
        )
        assert evidence.errors
        assert evidence.renderer_status == "RENDERED_BLOCKED"
