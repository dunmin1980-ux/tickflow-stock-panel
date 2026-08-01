from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import importlib.util
import json
import re
import subprocess
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
DOCKERFILE_PATH = WORKER_CONTEXT / "Dockerfile"
EXPECTED_BASE_DIGEST = (
    "gcr.io/distroless/python3-debian12@"
    "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)


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


def test_worker_dockerfile_is_digest_pinned_shellless_and_minimal() -> None:
    assert DOCKERFILE_PATH.is_file(), "isolated worker Dockerfile is missing"
    source = DOCKERFILE_PATH.read_text(encoding="utf-8")
    significant = [
        line.strip()
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert significant[0] == f"FROM {EXPECTED_BASE_DIGEST}"
    assert (
        'LABEL org.tickflow.phase2.base-image-digest="'
        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
        '"'
    ) in significant
    assert len([line for line in significant if line.startswith("COPY ")]) == 3
    assert all(
        line.startswith("COPY --chown=65532:65532 ")
        for line in significant
        if line.startswith("COPY ")
    )
    for expected in (
        "USER 65532:65532",
        "WORKDIR /worker",
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0",
        'ENTRYPOINT ["/usr/bin/python3", "/worker/worker.py"]',
        'CMD ["candidate"]',
    ):
        assert expected in significant
    assert not re.search(r"(?mi)^\s*(?:RUN|ADD)\b", source)
    for forbidden in (
        ":debug",
        "apt-get",
        "apt ",
        "apk ",
        "pip ",
        "curl ",
        "wget ",
        "http://",
        "https://",
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "API_KEY",
    ):
        assert forbidden not in source


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


def _blocked_probe() -> dict:
    blocked = {"blocked": True, "errno": "EPERM"}
    return {
        "probe_schema_version": 1,
        "network": {"blocked": True, "errno": "ENETUNREACH"},
        "shells": {
            "sh": {"blocked": True, "errno": "ENOENT"},
            "bash": {"blocked": True, "errno": "ENOENT"},
            "busybox": {"blocked": True, "errno": "ENOENT"},
        },
        "forbidden_reads": {
            name: dict(blocked)
            for name in (
                "host_home",
                "repository",
                "ssh",
                "config",
                "vault",
                "docker_socket",
            )
        },
        "forbidden_writes": {
            name: dict(blocked)
            for name in (
                "projection",
                "tmp",
                "worker",
                "worker_source",
                "etc",
                "extra_output",
            )
        },
    }


class FakeDockerExecutor:
    def __init__(
        self,
        *,
        probe: dict | None = None,
        cleanup_fails: bool = False,
    ) -> None:
        self.calls: list[list[str]] = []
        self.containers: dict[str, dict] = {}
        self.probe = probe or _blocked_probe()
        self.cleanup_fails = cleanup_fails
        self.worker = _load_worker()
        self.temp_paths: set[Path] = set()

    @staticmethod
    def _option(command: list[str], name: str) -> str:
        return command[command.index(name) + 1]

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command))
        if command[:2] == ["docker", "version"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "Version": "29.3.1",
                        "Os": "linux",
                        "Arch": "amd64",
                        "ApiVersion": "1.52",
                    }
                ),
                stderr="",
            )
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "Id": "sha256:" + "a" * 64,
                            "Config": {
                                "User": "65532:65532",
                                "Entrypoint": [
                                    "/usr/bin/python3",
                                    "/worker/worker.py",
                                ],
                                "Env": [
                                    "PYTHONDONTWRITEBYTECODE=1",
                                    "PYTHONHASHSEED=0",
                                ],
                                "Labels": {
                                    "org.tickflow.phase2.base-image-digest": (
                                        "sha256:" + "b" * 64
                                    )
                                },
                            },
                        }
                    ]
                ),
                stderr="",
            )
        if command[:2] == ["docker", "create"]:
            runtime = _runtime()
            name = self._option(command, "--name")
            mounts = [
                command[index + 1]
                for index, value in enumerate(command)
                if value == "--mount"
            ]
            mount_values = {
                part.split("=", 1)[0]: part.split("=", 1)[1]
                for mount in mounts
                for part in mount.split(",")
                if "=" in part
            }
            projection = Path(
                next(
                    mount.split("src=", 1)[1].split(",", 1)[0]
                    for mount in mounts
                    if "dst=/input/projection.json" in mount
                )
            )
            output = Path(
                next(
                    mount.split("src=", 1)[1].split(",", 1)[0]
                    for mount in mounts
                    if "dst=/output/candidate.json" in mount
                )
            )
            paths = runtime.IsolationPaths(projection=projection, output=output)
            self.temp_paths.update({projection, output, projection.parent})
            self.containers[name] = {
                "mode": command[-1],
                "paths": paths,
                "state": "created",
                "mount_values": mount_values,
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="fake-container-id\n",
                stderr="",
            )
        if command[:2] == ["docker", "inspect"]:
            name = command[-1]
            container = self.containers[name]
            payload = _inspect_payload(
                container["paths"],
                state=container["state"],
            )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(payload),
                stderr="",
            )
        if command[:3] == ["docker", "start", "--attach"]:
            name = command[-1]
            container = self.containers[name]
            if container["mode"] == "candidate":
                projection = json.loads(container["paths"].projection.read_bytes())
                value = self.worker.generate_candidate(projection)
            else:
                value = self.probe
            container["paths"].output.write_bytes(canonical_json_bytes(value))
            container["state"] = "exited"
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["docker", "rm", "-f"]:
            if self.cleanup_fails:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="",
                    stderr="cleanup failed",
                )
            self.containers.pop(command[-1], None)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected docker command: {command[:3]}")


def test_runtime_orchestrator_publishes_only_sanitized_verified_evidence(
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    executor = FakeDockerExecutor()

    result = runtime.run_runtime_validation(
        REPO_ROOT,
        reports_root,
        _executor=executor,
        _allow_test_output_root=True,
    )

    assert result.status == "PHASE2B_ISOLATION_RUNTIME_VERIFIED"
    assert result.errors == []
    assert result.cleanup_complete is True
    evidence_path = reports_root / "phase2_isolation_runtime/runtime_evidence.json"
    evidence = json.loads(evidence_path.read_bytes())
    assert evidence["status"] == "PHASE2B_ISOLATION_RUNTIME_VERIFIED"
    assert evidence["host_validation"]["claims_status"] == "CLAIMS_VALID"
    assert evidence["host_validation"]["renderer_status"] == "RENDERED_VALID"
    assert evidence["host_validation"]["claim_without_provenance_count"] == 0
    assert evidence["runtime_contract"]["candidate"]["contract_valid"] is True
    assert evidence["runtime_contract"]["probe"]["contract_valid"] is True
    assert evidence["probe"]["all_blocked"] is True
    assert evidence["external_actions"] == {
        "ai_call_count": 0,
        "cloud_mutation_count": 0,
        "external_send_count": 0,
        "integrated_gold_enabled": False,
        "obsidian_real_vault_write": False,
        "paper_trading_started": False,
        "provider_attempt_count": 0,
        "tickflow_api_request_count": 0,
    }
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "source",
        "container_id",
        "command",
        "environment",
        "Authorization",
        "Cookie",
        "Session",
        "api_key",
        "/Users/",
        "docker.sock",
    ):
        assert forbidden not in serialized
    assert not executor.containers
    assert all(not path.exists() for path in executor.temp_paths)


def test_runtime_orchestrator_has_no_retry_and_cleans_both_containers(
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    executor = FakeDockerExecutor()

    result = runtime.run_runtime_validation(
        REPO_ROOT,
        reports_root,
        _executor=executor,
        _allow_test_output_root=True,
    )

    assert result.status == "PHASE2B_ISOLATION_RUNTIME_VERIFIED"
    assert sum(call[:2] == ["docker", "create"] for call in executor.calls) == 2
    assert sum(call[:3] == ["docker", "start", "--attach"] for call in executor.calls) == 2
    assert sum(call[:3] == ["docker", "rm", "-f"] for call in executor.calls) == 2


def test_runtime_orchestrator_blocks_probe_or_cleanup_failure(
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    bad_probe = _blocked_probe()
    bad_probe["network"] = {"blocked": False, "errno": "NONE"}

    scenarios = (
        (FakeDockerExecutor(probe=bad_probe), True),
        (FakeDockerExecutor(cleanup_fails=True), False),
    )
    for executor, expected_cleanup in scenarios:
        reports_root = tmp_path / f"reports-{len(list(tmp_path.iterdir()))}"
        reports_root.mkdir()
        result = runtime.run_runtime_validation(
            REPO_ROOT,
            reports_root,
            _executor=executor,
            _allow_test_output_root=True,
        )
        assert result.status == "PHASE2B_ISOLATION_RUNTIME_BLOCKED"
        assert result.errors
        assert result.cleanup_complete is expected_cleanup
