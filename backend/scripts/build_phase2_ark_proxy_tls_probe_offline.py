#!/usr/bin/env python3
"""Build and verify immutable Production Proxy TLS Probe artifacts offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / "reports/phase2_provider_ark/proxy_tls_probe"
ATTEMPTS_ROOT = OUTPUT_ROOT / "attempts"
CONTRACT_ROOT = Path("docker/phase2-ark-proxy-tls-probe/contracts")
BASE_IMAGE_REFERENCE = (
    "gcr.io/distroless/python3-debian12@"
    "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)
BASE_IMAGE_DIGEST = (
    "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)
IMAGE_TAG = "tickflow-phase2-ark-egress-proxy:proxy-tls-probe-v1"
SCOPE_TYPE = "production_proxy_path_tls_only_probe"
TARGET_HOST = "ark.cn-beijing.volces.com"
TARGET_PORT = 443
STARTING_HEAD = "ad458daa57425093940480f1a090537e885ba35d"
STARTING_PROXY_SOURCE_SHA256 = (
    "5bd5349d899d0d693054d332b66d81e9d64bc3332133903b91e2d05e71c479c7"
)
STARTING_ORCHESTRATOR_SOURCE_SHA256 = (
    "c94ac29ca20bc2eb590bf465fc576505821b651ef9b7a7a6ac4b5be7181ebd9e"
)
HISTORICAL_BASELINE_PATH = (
    REPO_ROOT
    / "reports/phase2_provider_ark/proxy_tls_differential/historical_baseline.json"
)
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_BUILD_PROVENANCE_FIELDS = {
    "ai_call_count",
    "base_image_digest",
    "build_arguments",
    "build_network",
    "build_provenance_schema_version",
    "ca_bundle_identity",
    "context_hashes",
    "image_identity",
    "no_cache",
    "provider_attempt_count",
    "pull",
    "real_public_network_success_count",
    "runtime_identity",
    "secret_content_read",
    "source_git_head",
}
_MOCK_EXPECTED_CASES = {
    "happy_path_1": "PROBE_PASSED",
    "happy_path_2": "PROBE_PASSED",
    "happy_path_3": "PROBE_PASSED",
    "relay_internal_plus_egress": "PROBE_PASSED",
    "unknown_ca": "TLS_CERT_VERIFY_FAILED",
    "hostname_mismatch": "TLS_HOSTNAME_VERIFY_FAILED",
    "connection_reset": "TLS_CONNECTION_RESET",
    "handshake_timeout": "TLS_HANDSHAKE_TIMEOUT",
    "protocol_incompatibility": "TLS_PROTOCOL_FAILED",
    "connection_refused": "PROVIDER_CONNECT_FAILED",
    "egress_network_missing": "TOPOLOGY_BLOCKED",
    "wrong_network_attachment": "TOPOLOGY_BLOCKED",
}

SOURCE_BINDING_PATHS = {
    "builder_source": "backend/scripts/build_phase2_ark_proxy_tls_probe_offline.py",
    "host_launcher_source": "backend/scripts/run_phase2_ark_proxy_tls_probe.py",
    "container_runner_source": "docker/phase2-ark-proxy-tls-probe/probe.py",
    "dispatch_gate_source": "docker/phase2-ark-proxy-tls-probe/dispatch_gate.py",
    "proxy_image_dockerfile": (
        "docker/phase2-ark-proxy-tls-probe/"
        "Dockerfile.network-attachment-v2"
    ),
    "network_attachment_harness_source": (
        "backend/scripts/run_phase2_ark_proxy_network_attachment_harness.py"
    ),
    "mock_harness_source": (
        "backend/scripts/run_phase2_ark_proxy_tls_probe_mock_e2e.py"
    ),
    "mock_server_source": "docker/phase2-ark-proxy-tls-mock/server.py",
    "local_parity_harness_source": (
        "backend/scripts/run_phase2_ark_proxy_tls_parity.py"
    ),
    "proxy_source": "docker/phase2-ark-egress-proxy/proxy.py",
    "orchestrator_source": "backend/app/services/phase2_canary_orchestrator.py",
    "proxy_dockerfile": "docker/phase2-ark-egress-proxy/Dockerfile",
    "proxy_runtime_contract": "docker/phase2-ark-egress-proxy/runtime-contract.json",
    "proxy_readiness_contract": "docker/phase2-ark-egress-proxy/readiness-contract.json",
    "proxy_responses_contract": "docker/phase2-ark-egress-proxy/responses-contract.json",
    "probe_contract": str(CONTRACT_ROOT / "probe-contract.json"),
    "probe_runtime_contract": str(CONTRACT_ROOT / "runtime-contract.json"),
    "probe_readiness_contract": str(CONTRACT_ROOT / "readiness-contract.json"),
    "internal_network_contract": str(
        CONTRACT_ROOT / "internal-network-contract.json"
    ),
    "egress_network_contract": str(
        CONTRACT_ROOT / "egress-network-contract.json"
    ),
    "proxy_network_attachment_contract": str(
        CONTRACT_ROOT / "proxy-network-attachment-contract.json"
    ),
    "egress_policy": str(CONTRACT_ROOT / "egress-policy.json"),
    "dns_configuration_contract": str(
        CONTRACT_ROOT / "dns-configuration-contract.json"
    ),
    "receipt_schema": str(CONTRACT_ROOT / "receipt.schema.json"),
    "historical_tls_probe_evidence": (
        "reports/phase2_provider_ark/tls_probe_v2/attempts/"
        "abd999450d007f23cadccd86d22136608c3fbb04b1d92c0078153088781f919c/"
        "d73e91f766854ff7a64c0e725939eb64/evidence/transport-evidence.json"
    ),
    "proxy_tls_differential_evidence": (
        "reports/phase2_provider_ark/proxy_tls_differential/verification.json"
    ),
    "local_proxy_parity_evidence": (
        "reports/phase2_provider_ark/proxy_tls_differential/local_parity.json"
    ),
    "mock_e2e_evidence": (
        "reports/phase2_provider_ark/proxy_tls_probe/mock_e2e.json"
    ),
    "network_attachment_harness_evidence": (
        "reports/phase2_provider_ark/proxy_tls_probe/"
        "network_attachment_harness.json"
    ),
    "backend_failure_baseline": (
        "reports/phase2_provider_ark/proxy_tls_probe/backend_failure_baseline.json"
    ),
    "backend_failure_postcheck": (
        "reports/phase2_provider_ark/proxy_tls_probe/backend_failure_postcheck.json"
    ),
    "historical_evidence_baseline": (
        "reports/phase2_provider_ark/proxy_tls_differential/"
        "historical_baseline.json"
    ),
}
REQUIRED_SOURCE_BINDING_KEYS = tuple(SOURCE_BINDING_PATHS)

PROBE_IMAGE_CONTEXT_SOURCES = {
    "Dockerfile": (
        "docker/phase2-ark-proxy-tls-probe/"
        "Dockerfile.network-attachment-v2"
    ),
    "dispatch_gate.py": "docker/phase2-ark-proxy-tls-probe/dispatch_gate.py",
    "probe.py": "docker/phase2-ark-proxy-tls-probe/probe.py",
    "proxy.py": "docker/phase2-ark-egress-proxy/proxy.py",
    "readiness-contract.json": (
        "docker/phase2-ark-egress-proxy/readiness-contract.json"
    ),
    "responses-contract.json": (
        "docker/phase2-ark-egress-proxy/responses-contract.json"
    ),
    "runtime-contract.json": "docker/phase2-ark-egress-proxy/runtime-contract.json",
}


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def strict_build_command(
    *,
    tag: str,
    context: Path,
    build_arguments: Mapping[str, str],
) -> list[str]:
    command = [
        "docker",
        "build",
        "--network",
        "none",
        "--pull=false",
        "--no-cache",
        "--tag",
        tag,
    ]
    for key, value in sorted(build_arguments.items()):
        command.extend(["--build-arg", f"{key}={value}"])
    command.append(str(context))
    return command


def context_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("proxy_build_context_invalid")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("proxy_build_context_invalid")
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        hashes[path.relative_to(root).as_posix()] = sha256_file(path)
    return hashes


def build_arguments_for_context(
    hashes: Mapping[str, str],
    *,
    proxy_policy_sha256: str,
) -> dict[str, str]:
    required = {
        "Dockerfile",
        "dispatch_gate.py",
        "probe.py",
        "proxy.py",
        "readiness-contract.json",
        "responses-contract.json",
        "runtime-contract.json",
    }
    if not required <= set(hashes) or _HEX_64.fullmatch(proxy_policy_sha256) is None:
        raise ValueError("proxy_build_context_invalid")
    return {
        "BASE_IMAGE_DIGEST": BASE_IMAGE_DIGEST,
        "DISPATCH_GATE_SHA256": hashes["dispatch_gate.py"],
        "PROBE_RUNNER_SHA256": hashes["probe.py"],
        "PROXY_DOCKERFILE_SHA256": hashes["Dockerfile"],
        "PROXY_POLICY_SHA256": proxy_policy_sha256,
        "PROXY_SOURCE_SHA256": hashes["proxy.py"],
        "READINESS_CONTRACT_SHA256": hashes["readiness-contract.json"],
        "RESPONSES_CONTRACT_SHA256": hashes["responses-contract.json"],
        "RUNTIME_CONTRACT_SHA256": hashes["runtime-contract.json"],
    }


def expected_image_labels(build_arguments: Mapping[str, str]) -> dict[str, str]:
    required = {
        "BASE_IMAGE_DIGEST",
        "DISPATCH_GATE_SHA256",
        "PROBE_RUNNER_SHA256",
        "PROXY_DOCKERFILE_SHA256",
        "PROXY_POLICY_SHA256",
        "PROXY_SOURCE_SHA256",
        "READINESS_CONTRACT_SHA256",
        "RESPONSES_CONTRACT_SHA256",
        "RUNTIME_CONTRACT_SHA256",
    }
    if set(build_arguments) != required:
        raise ValueError("proxy_build_arguments_invalid")
    return {
        "org.tickflow.phase2.base-image-digest": build_arguments[
            "BASE_IMAGE_DIGEST"
        ],
        "org.tickflow.phase2.dispatch-gate-sha256": build_arguments[
            "DISPATCH_GATE_SHA256"
        ],
        "org.tickflow.phase2.provider-id": "volcengine_ark",
        "org.tickflow.phase2.probe-runner-sha256": build_arguments[
            "PROBE_RUNNER_SHA256"
        ],
        "org.tickflow.phase2.proxy-dockerfile-sha256": build_arguments[
            "PROXY_DOCKERFILE_SHA256"
        ],
        "org.tickflow.phase2.proxy-policy-sha256": build_arguments[
            "PROXY_POLICY_SHA256"
        ],
        "org.tickflow.phase2.proxy-source-sha256": build_arguments[
            "PROXY_SOURCE_SHA256"
        ],
        "org.tickflow.phase2.readiness-contract-sha256": build_arguments[
            "READINESS_CONTRACT_SHA256"
        ],
        "org.tickflow.phase2.responses-contract-sha256": build_arguments[
            "RESPONSES_CONTRACT_SHA256"
        ],
        "org.tickflow.phase2.runtime-contract-sha256": build_arguments[
            "RUNTIME_CONTRACT_SHA256"
        ],
    }


def validate_image_inspect(
    payload: Any,
    *,
    expected_labels: Mapping[str, str],
) -> dict[str, Any]:
    try:
        value = payload[0]
        labels = value["Config"]["Labels"]
        user = value["Config"]["User"]
    except (IndexError, KeyError, TypeError) as error:
        raise ValueError("proxy_image_inspect_invalid") from error
    image_id = value.get("Id")
    if (
        not isinstance(image_id, str)
        or not image_id.startswith("sha256:")
        or _HEX_64.fullmatch(image_id.removeprefix("sha256:")) is None
    ):
        raise ValueError("proxy_image_identity_invalid")
    if labels != dict(expected_labels):
        raise ValueError("proxy_image_labels_mismatch")
    if value.get("Architecture") != "amd64" or value.get("Os") != "linux":
        raise ValueError("proxy_image_platform_mismatch")
    if user != "65532:65532":
        raise ValueError("proxy_image_user_mismatch")
    return {
        "architecture": value["Architecture"],
        "image_id": image_id,
        "labels": dict(labels),
        "os": value["Os"],
        "user": user,
    }


def build_provenance(
    *,
    source_git_head: str,
    context_hashes: Mapping[str, str],
    build_arguments: Mapping[str, str],
    image_identity: Mapping[str, Any],
    ca_identity: Mapping[str, Any],
    runtime_identity: Mapping[str, Any],
) -> dict[str, Any]:
    if _HEX_40.fullmatch(source_git_head) is None:
        raise ValueError("source_git_head_invalid")
    return {
        "ai_call_count": 0,
        "base_image_digest": BASE_IMAGE_DIGEST,
        "build_arguments": dict(build_arguments),
        "build_network": "none",
        "build_provenance_schema_version": 1,
        "ca_bundle_identity": dict(ca_identity),
        "context_hashes": dict(context_hashes),
        "image_identity": dict(image_identity),
        "no_cache": True,
        "provider_attempt_count": 0,
        "pull": False,
        "real_public_network_success_count": 0,
        "runtime_identity": dict(runtime_identity),
        "secret_content_read": False,
        "source_git_head": source_git_head,
    }


def inspect_local_base(
    *,
    run: Any = subprocess.run,
) -> dict[str, Any]:
    command = ["docker", "image", "inspect", BASE_IMAGE_REFERENCE]
    result = run(command, timeout=30)
    try:
        values = json.loads(result.stdout) if result.returncode == 0 else None
    except json.JSONDecodeError:
        values = None
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
        or values[0].get("Id") != BASE_IMAGE_DIGEST
        or values[0].get("Architecture") != "amd64"
        or values[0].get("Os") != "linux"
    ):
        raise ValueError("offline_base_image_missing")
    return values[0]


def copy_readonly_context(source: Path, target: Path) -> dict[str, str]:
    hashes = context_hashes(source)
    target.mkdir(mode=0o700)
    for relative in hashes:
        source_path = source / relative
        target_path = target / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path, follow_symlinks=False)
        target_path.chmod(0o400)
    for directory in sorted(
        (path for path in target.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        directory.chmod(0o500)
    target.chmod(0o500)
    if context_hashes(source) != hashes or context_hashes(target) != hashes:
        raise ValueError("proxy_build_context_changed")
    return hashes


def copy_probe_image_context(repo_root: Path, target: Path) -> dict[str, str]:
    bindings = {
        destination: source_binding(repo_root, source)
        for destination, source in PROBE_IMAGE_CONTEXT_SOURCES.items()
    }
    target.mkdir(mode=0o700)
    for destination, binding in bindings.items():
        target_path = target / destination
        shutil.copyfile(
            repo_root / binding["path"],
            target_path,
            follow_symlinks=False,
        )
        target_path.chmod(0o400)
    target.chmod(0o500)
    expected = {
        destination: binding["sha256"]
        for destination, binding in bindings.items()
    }
    if context_hashes(target) != expected:
        raise ValueError("proxy_build_context_changed")
    if any(
        source_binding(repo_root, source)["sha256"] != expected[destination]
        for destination, source in PROBE_IMAGE_CONTEXT_SOURCES.items()
    ):
        raise ValueError("proxy_build_context_changed")
    return expected


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".staging", dir=path.parent
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        raw = canonical_json_bytes(value)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def read_canonical_json(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("artifact_invalid")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("artifact_invalid") from error
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise ValueError("artifact_invalid")
    return value


def _run_local(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _require_success(
    result: subprocess.CompletedProcess[str],
    category: str,
) -> None:
    if result.returncode != 0:
        raise ValueError(category)


def inspect_built_image(
    *,
    expected_labels: Mapping[str, str],
    reference: str = IMAGE_TAG,
    run: Any = _run_local,
) -> dict[str, Any]:
    result = run(["docker", "image", "inspect", reference], timeout=30.0)
    _require_success(result, "proxy_image_inspect_failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("proxy_image_inspect_invalid") from error
    return validate_image_inspect(payload, expected_labels=expected_labels)


def inspect_runtime_identity(
    image_id: str,
    *,
    run: Any = _run_local,
) -> dict[str, Any]:
    program = (
        "import json,os,ssl,sys;"
        "print(json.dumps({"
        "'python_version':'.'.join(map(str,sys.version_info[:3])),"
        "'openssl_version':ssl.OPENSSL_VERSION,"
        "'uid':os.getuid(),'gid':os.getgid(),"
        "'default_verify_paths':ssl.get_default_verify_paths()._asdict()"
        "},sort_keys=True))"
    )
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/usr/bin/python3",
            image_id,
            "-c",
            program,
        ],
        timeout=30.0,
    )
    _require_success(result, "proxy_runtime_identity_failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("proxy_runtime_identity_invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("uid") != 65532
        or value.get("gid") != 65532
        or not isinstance(value.get("python_version"), str)
        or not isinstance(value.get("openssl_version"), str)
        or not isinstance(value.get("default_verify_paths"), dict)
    ):
        raise ValueError("proxy_runtime_identity_invalid")
    return value


def extract_ca_identity(
    image_id: str,
    *,
    run: Any = _run_local,
    temporary_parent: Path | None = None,
) -> dict[str, Any]:
    parent = temporary_parent.resolve(strict=True) if temporary_parent else None
    temporary_root = Path(
        tempfile.mkdtemp(prefix="phase2-proxy-tls-probe-ca-", dir=parent)
    ).resolve(strict=True)
    container_name = f"phase2-proxy-tls-probe-ca-{os.getpid()}"
    destination = temporary_root / "ca-certificates.crt"
    created = False
    primary_error: BaseException | None = None
    try:
        create = run(
            [
                "docker",
                "container",
                "create",
                "--name",
                container_name,
                "--network",
                "none",
                image_id,
            ],
            timeout=30.0,
        )
        _require_success(create, "ca_container_create_failed")
        created = True
        copied = run(
            [
                "docker",
                "container",
                "cp",
                f"{container_name}:/etc/ssl/certs/ca-certificates.crt",
                str(destination),
            ],
            timeout=30.0,
        )
        _require_success(copied, "ca_bundle_copy_failed")
        metadata = destination.lstat()
        if destination.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("ca_bundle_invalid")
        return {
            "path": "/etc/ssl/certs/ca-certificates.crt",
            "sha256": sha256_file(destination),
            "size_bytes": metadata.st_size,
        }
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if created:
            removed = run(
                ["docker", "container", "rm", "--force", container_name],
                timeout=30.0,
            )
            if removed.returncode != 0 and primary_error is None:
                raise ValueError("ca_container_cleanup_failed")
        destination.unlink(missing_ok=True)
        temporary_root.rmdir()


def current_git_head(repo_root: Path = REPO_ROOT) -> str:
    result = _run_local(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        timeout=15.0,
    )
    _require_success(result, "git_head_unavailable")
    value = result.stdout.strip()
    if _HEX_40.fullmatch(value) is None:
        raise ValueError("git_head_invalid")
    return value


def build_proxy_image(
    repo_root: Path = REPO_ROOT,
    *,
    source_git_head: str,
    run: Any = _run_local,
) -> dict[str, Any]:
    if _HEX_40.fullmatch(source_git_head) is None:
        raise ValueError("source_git_head_invalid")
    inspect_local_base(run=run)
    from app.providers.ark_contract import ark_proxy_policy_sha256

    with tempfile.TemporaryDirectory(
        prefix="phase2-proxy-tls-probe-build-"
    ) as name:
        staging_root = Path(name).resolve(strict=True)
        staging_root.chmod(0o700)
        context = staging_root / "proxy"
        hashes = copy_probe_image_context(repo_root, context)
        arguments = build_arguments_for_context(
            hashes,
            proxy_policy_sha256=ark_proxy_policy_sha256(),
        )
        build = run(
            strict_build_command(
                tag=IMAGE_TAG,
                context=context,
                build_arguments=arguments,
            ),
            cwd=repo_root,
            timeout=180.0,
        )
        _require_success(build, "proxy_image_build_failed")
        labels = expected_image_labels(arguments)
        image_identity = inspect_built_image(expected_labels=labels, run=run)
        runtime_identity = inspect_runtime_identity(image_identity["image_id"], run=run)
        ca_identity = extract_ca_identity(image_identity["image_id"], run=run)
        if context_hashes(context) != hashes:
            raise ValueError("proxy_build_context_changed")
    provenance = build_provenance(
        source_git_head=source_git_head,
        context_hashes=hashes,
        build_arguments=arguments,
        image_identity=image_identity,
        ca_identity=ca_identity,
        runtime_identity=runtime_identity,
    )
    atomic_write_json(OUTPUT_ROOT / "build_provenance.json", provenance)
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build-image", action="store_true")
    mode.add_argument("--build-failure-baseline", action="store_true")
    mode.add_argument("--build-failure-postcheck", action="store_true")
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    head = current_git_head()
    if args.build_image:
        provenance = build_proxy_image(source_git_head=head)
        print(
            json.dumps(
                {
                    "image_id": provenance["image_identity"]["image_id"],
                    "provider_attempt_count": 0,
                    "real_public_network_success_count": 0,
                    "status": "PROXY_IMAGE_BUILT_OFFLINE",
                },
                sort_keys=True,
            )
        )
    elif args.build_failure_baseline:
        baseline = build_backend_failure_baseline(
            OUTPUT_ROOT / "backend_full_baseline.xml",
            source_git_head=head,
            expected_passed=2656,
            expected_failed=57,
        )
        atomic_write_json(OUTPUT_ROOT / "backend_failure_baseline.json", baseline)
        print(
            json.dumps(
                {
                    "failed": baseline["failed"],
                    "failure_set_sha256": baseline["failure_set_sha256"],
                    "passed": baseline["passed"],
                    "status": "BACKEND_FAILURE_BASELINE_FROZEN",
                },
                sort_keys=True,
            )
        )
    elif args.build_failure_postcheck:
        postcheck = build_backend_failure_postcheck(
            OUTPUT_ROOT / "backend_failure_baseline.json",
            OUTPUT_ROOT / "backend_full_post.xml",
            source_git_head=head,
        )
        atomic_write_json(OUTPUT_ROOT / "backend_failure_postcheck.json", postcheck)
        print(json.dumps(postcheck, sort_keys=True))
    elif args.prepare:
        generated = prepare_artifacts(
            REPO_ROOT,
            output_root=OUTPUT_ROOT,
            attempts_root=ATTEMPTS_ROOT,
            current_head=head,
            historical_evidence=verify_historical_evidence(REPO_ROOT),
        )
        print(json.dumps(generated, sort_keys=True))
    else:
        verified = verify_artifacts(
            REPO_ROOT,
            output_root=OUTPUT_ROOT,
            attempts_root=ATTEMPTS_ROOT,
            current_head=head,
            historical_evidence=verify_historical_evidence(REPO_ROOT),
            publish=True,
        )
        print(json.dumps(verified, sort_keys=True))
    return 0


def source_binding(repo_root: Path, relative_path: str) -> dict[str, str]:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source_binding_escape")
    root = repo_root.resolve(strict=True)
    current = repo_root
    for part in relative.parts:
        current = current / part
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise ValueError("source_binding_missing") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("source_binding_symlink")
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("source_binding_escape") from error
    metadata = os.lstat(current)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("source_binding_not_regular")
    return {"path": relative.as_posix(), "sha256": sha256_file(current)}


def source_bindings(repo_root: Path) -> dict[str, dict[str, str]]:
    return {
        name: source_binding(repo_root, path)
        for name, path in SOURCE_BINDING_PATHS.items()
    }


def _classify_failure(node_id: str) -> str:
    if node_id.startswith("tests/test_gold_"):
        return "pre_existing_gold_debt"
    if node_id.startswith("tests/test_ark_orchestrator_identity.py::") and not node_id.endswith(
        "test_ark_approval_loader_returns_the_approved_single_snapshot"
    ):
        return "pre_existing_launcher_debt"
    return "expected_immutable_identity_fail_closed"


def build_backend_failure_baseline(
    junit_path: Path,
    *,
    source_git_head: str,
    expected_passed: int,
    expected_failed: int,
) -> dict[str, Any]:
    if _HEX_40.fullmatch(source_git_head) is None:
        raise ValueError("source_git_head_invalid")
    root = ElementTree.parse(junit_path).getroot()
    testcases = list(root.iter("testcase"))
    failed_cases = [
        case
        for case in testcases
        if case.find("failure") is not None or case.find("error") is not None
    ]
    passed = sum(
        case.find("failure") is None
        and case.find("error") is None
        and case.find("skipped") is None
        for case in testcases
    )
    failed_node_ids = sorted(
        f"{case.attrib['classname'].replace('.', '/')}.py::{case.attrib['name']}"
        for case in failed_cases
    )
    if passed != expected_passed or len(failed_node_ids) != expected_failed:
        raise ValueError("backend_failure_baseline_count_mismatch")
    categories = {
        "expected_immutable_identity_fail_closed": 0,
        "pre_existing_gold_debt": 0,
        "pre_existing_launcher_debt": 0,
    }
    for node_id in failed_node_ids:
        categories[_classify_failure(node_id)] += 1
    return {
        "backend_failure_baseline_schema_version": 1,
        "classification_counts": categories,
        "failed": len(failed_node_ids),
        "failed_node_ids": failed_node_ids,
        "failure_set_sha256": sha256_bytes(canonical_json_bytes(failed_node_ids)),
        "junit_sha256": sha256_file(junit_path),
        "passed": passed,
        "source_git_head": source_git_head,
    }


def _junit_summary(junit_path: Path) -> tuple[int, list[str]]:
    try:
        root = ElementTree.parse(junit_path).getroot()
    except (ElementTree.ParseError, OSError) as error:
        raise ValueError("backend_junit_invalid") from error
    testcases = list(root.iter("testcase"))
    failed_cases = [
        case
        for case in testcases
        if case.find("failure") is not None or case.find("error") is not None
    ]
    passed = sum(
        case.find("failure") is None
        and case.find("error") is None
        and case.find("skipped") is None
        for case in testcases
    )
    try:
        failed_node_ids = sorted(
            f"{case.attrib['classname'].replace('.', '/')}.py::{case.attrib['name']}"
            for case in failed_cases
        )
    except KeyError as error:
        raise ValueError("backend_junit_invalid") from error
    return passed, failed_node_ids


def build_backend_failure_postcheck(
    baseline_path: Path,
    junit_path: Path,
    *,
    source_git_head: str,
) -> dict[str, Any]:
    if _HEX_40.fullmatch(source_git_head) is None:
        raise ValueError("source_git_head_invalid")
    baseline = read_canonical_json(baseline_path)
    passed, failed_node_ids = _junit_summary(junit_path)
    failure_set_sha = sha256_bytes(canonical_json_bytes(failed_node_ids))
    if (
        failed_node_ids != baseline.get("failed_node_ids")
        or failure_set_sha != baseline.get("failure_set_sha256")
    ):
        raise ValueError("backend_failure_set_changed")
    return {
        "backend_failure_postcheck_schema_version": 1,
        "baseline_sha256": sha256_file(baseline_path),
        "classification_counts": baseline.get("classification_counts"),
        "failed": len(failed_node_ids),
        "failed_node_ids": failed_node_ids,
        "failure_set_sha256": failure_set_sha,
        "junit_sha256": sha256_file(junit_path),
        "passed": passed,
        "source_git_head": source_git_head,
        "status": "KNOWN_BACKEND_FAILURE_SET_UNCHANGED",
    }


def build_candidate(
    repo_root: Path,
    *,
    current_git_head: str,
    proxy_image_id: str,
    build_provenance_sha256: str,
    ca_bundle_sha256: str,
) -> dict[str, Any]:
    if _HEX_40.fullmatch(current_git_head) is None:
        raise ValueError("current_git_head_invalid")
    if not proxy_image_id.startswith("sha256:") or _HEX_64.fullmatch(
        proxy_image_id.removeprefix("sha256:")
    ) is None:
        raise ValueError("proxy_image_id_invalid")
    for value in (build_provenance_sha256, ca_bundle_sha256):
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("candidate_hash_invalid")
    bindings = source_bindings(repo_root)
    if set(bindings) != set(REQUIRED_SOURCE_BINDING_KEYS):
        raise ValueError("candidate_source_bindings_incomplete")
    return {
        "approval_candidate_schema_version": 1,
        "base_image_digest": BASE_IMAGE_DIGEST,
        "build_provenance_sha256": build_provenance_sha256,
        "ca_bundle_sha256": ca_bundle_sha256,
        "candidate_status": "PROXY_TLS_PROBE_CANDIDATE_READY_FOR_REVIEW",
        "check_hostname": True,
        "current_git_head": current_git_head,
        "dns_configuration_identity": bindings["dns_configuration_contract"][
            "sha256"
        ],
        "egress_network_contract_sha256": bindings["egress_network_contract"][
            "sha256"
        ],
        "egress_policy_sha256": bindings["egress_policy"]["sha256"],
        "dispatch_gate_source_sha256": bindings["dispatch_gate_source"]["sha256"],
        "historical_tls_probe_sha256": bindings[
            "historical_tls_probe_evidence"
        ]["sha256"],
        "host_launcher_source_sha256": bindings["host_launcher_source"]["sha256"],
        "hostname_verification_target": TARGET_HOST,
        "internal_network_contract_sha256": bindings[
            "internal_network_contract"
        ]["sha256"],
        "local_proxy_parity_evidence_sha256": bindings[
            "local_proxy_parity_evidence"
        ]["sha256"],
        "maximum_probe_attempts": 1,
        "network_attachment_harness_evidence_sha256": bindings[
            "network_attachment_harness_evidence"
        ]["sha256"],
        "orchestrator_source_sha256": bindings["orchestrator_source"]["sha256"],
        "probe_contract_version": 1,
        "production_proxy_dockerfile_sha256": bindings["proxy_dockerfile"][
            "sha256"
        ],
        "proxy_dockerfile_sha256": bindings["proxy_image_dockerfile"]["sha256"],
        "proxy_image_id": proxy_image_id,
        "proxy_network_attachment_contract_sha256": bindings[
            "proxy_network_attachment_contract"
        ]["sha256"],
        "proxy_source_sha256": bindings["proxy_source"]["sha256"],
        "proxy_tls_differential_evidence_sha256": bindings[
            "proxy_tls_differential_evidence"
        ]["sha256"],
        "readiness_contract_sha256": bindings["probe_readiness_contract"][
            "sha256"
        ],
        "retry_count": 0,
        "scope_type": SCOPE_TYPE,
        "sni": TARGET_HOST,
        "source_bindings": bindings,
        "target_host": TARGET_HOST,
        "target_port": TARGET_PORT,
        "tls_contract_sha256": bindings["probe_contract"]["sha256"],
        "tls_minimum": "TLSv1_2",
        "verify_mode": "CERT_REQUIRED",
    }


def _scope_id(candidate_sha256: str) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "approval_candidate_sha256": candidate_sha256,
                "scope_type": SCOPE_TYPE,
                "target_host": TARGET_HOST,
                "target_port": TARGET_PORT,
            }
        )
    )


def build_scope(candidate_raw: bytes, *, attempts_root: Path) -> dict[str, Any]:
    candidate_sha = sha256_bytes(candidate_raw)
    scope_id = _scope_id(candidate_sha)
    if os.path.lexists(attempts_root / scope_id):
        raise ValueError("probe_scope_attempt_unavailable")
    return {
        "ai_canary_scope_shared": False,
        "approval_candidate_sha256": candidate_sha,
        "approval_scope_id": scope_id,
        "approval_scope_schema_version": 1,
        "attempt_availability": "AVAILABLE",
        "generic_tls_probe_scope_shared": False,
        "historical_attempts": 0,
        "maximum_probe_attempts": 1,
        "openai_scope_shared": False,
        "provider_scope_shared": False,
        "retry_count": 0,
        "scope_type": SCOPE_TYPE,
    }


def verify_historical_evidence(repo_root: Path) -> dict[str, Any]:
    baseline_path = (
        repo_root
        / "reports/phase2_provider_ark/proxy_tls_differential/"
        "historical_baseline.json"
    )
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        groups = (baseline["successful_probe"], baseline["failed_canary"])
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("historical_evidence_baseline_invalid") from error
    expected: dict[str, str] = {}
    for group in groups:
        files = group.get("files")
        if not isinstance(files, dict):
            raise ValueError("historical_evidence_baseline_invalid")
        expected.update(files)
    if len(expected) != 13:
        raise ValueError("historical_evidence_baseline_invalid")
    for relative_path, expected_sha in expected.items():
        binding = source_binding(repo_root, relative_path)
        if binding["sha256"] != expected_sha:
            raise ValueError("historical_evidence_mutated")
    return {
        "baseline_sha256": sha256_file(baseline_path),
        "checked_file_count": len(expected),
        "status": "UNCHANGED",
    }


def _validate_build_provenance(
    provenance: Mapping[str, Any],
    *,
    current_head: str,
    repo_root: Path = REPO_ROOT,
) -> tuple[str, str]:
    try:
        image_id = provenance["image_identity"]["image_id"]
        ca_sha = provenance["ca_bundle_identity"]["sha256"]
    except (KeyError, TypeError) as error:
        raise ValueError("build_provenance_invalid") from error
    from app.providers.ark_contract import ark_proxy_policy_sha256

    expected_context_hashes = {
        destination: source_binding(repo_root, source)["sha256"]
        for destination, source in PROBE_IMAGE_CONTEXT_SOURCES.items()
    }
    expected_build_arguments = build_arguments_for_context(
        expected_context_hashes,
        proxy_policy_sha256=ark_proxy_policy_sha256(),
    )
    expected_labels = expected_image_labels(expected_build_arguments)
    valid = (
        set(provenance) == _BUILD_PROVENANCE_FIELDS
        and provenance.get("build_provenance_schema_version") == 1
        and provenance.get("source_git_head") == current_head
        and provenance.get("base_image_digest") == BASE_IMAGE_DIGEST
        and provenance.get("context_hashes") == expected_context_hashes
        and provenance.get("build_arguments") == expected_build_arguments
        and provenance.get("build_network") == "none"
        and provenance.get("pull") is False
        and provenance.get("no_cache") is True
        and type(provenance.get("provider_attempt_count")) is int
        and provenance.get("provider_attempt_count") == 0
        and type(provenance.get("ai_call_count")) is int
        and provenance.get("ai_call_count") == 0
        and provenance.get("secret_content_read") is False
        and type(provenance.get("real_public_network_success_count")) is int
        and provenance.get("real_public_network_success_count") == 0
        and isinstance(image_id, str)
        and image_id.startswith("sha256:")
        and _HEX_64.fullmatch(image_id.removeprefix("sha256:")) is not None
        and isinstance(ca_sha, str)
        and _HEX_64.fullmatch(ca_sha) is not None
    )
    if not valid:
        raise ValueError("build_provenance_invalid")
    try:
        inspect_local_base(run=_run_local)
        inspected_image = inspect_built_image(
            expected_labels=expected_labels,
            reference=image_id,
        )
        inspected_runtime = inspect_runtime_identity(image_id)
        inspected_ca = extract_ca_identity(image_id)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError("build_provenance_invalid") from error
    if (
        provenance.get("image_identity") != inspected_image
        or provenance.get("runtime_identity") != inspected_runtime
        or provenance.get("ca_bundle_identity") != inspected_ca
    ):
        raise ValueError("build_provenance_invalid")
    return image_id, ca_sha


def _validate_mock_evidence(
    mock: Mapping[str, Any],
    *,
    proxy_image_id: str,
) -> None:
    from scripts.run_phase2_ark_proxy_tls_probe import validate_probe_receipt

    expected_residue = {"container_count": 0, "network_count": 0}
    expected_fields = {
        "ai_call_count",
        "authorization_constructed",
        "dispatch_gate_e2e",
        "double_network_topology",
        "happy_path_runs",
        "happy_path_status",
        "http_request_sent",
        "mock_e2e_schema_version",
        "mock_networks_internal",
        "mock_durable_dispatch_marks",
        "production_topology_mock",
        "provider_attempt_count",
        "proxy_image_id",
        "real_public_network_success_count",
        "residue",
        "results",
        "secret_content_read",
        "status",
    }
    results = mock.get("results")
    valid = (
        set(mock) == expected_fields
        and mock.get("mock_e2e_schema_version") == 1
        and mock.get("status") == "PRODUCTION_PROXY_TLS_PROBE_MOCK_PASSED"
        and mock.get("proxy_image_id") == proxy_image_id
        and mock.get("production_topology_mock") == "PASSED"
        and mock.get("double_network_topology") == "VERIFIED"
        and mock.get("dispatch_gate_e2e") == "PASSED"
        and mock.get("mock_networks_internal") is True
        and mock.get("mock_durable_dispatch_marks") == len(_MOCK_EXPECTED_CASES) - 2
        and mock.get("happy_path_status") == "PASSED"
        and mock.get("happy_path_runs") == 3
        and mock.get("provider_attempt_count") == 0
        and mock.get("ai_call_count") == 0
        and mock.get("secret_content_read") is False
        and mock.get("authorization_constructed") is False
        and mock.get("http_request_sent") is False
        and mock.get("real_public_network_success_count") == 0
        and mock.get("residue") == expected_residue
        and isinstance(results, Mapping)
        and set(results) == set(_MOCK_EXPECTED_CASES)
    )
    if not valid:
        raise ValueError("mock_e2e_invalid")
    for case, expected_status in _MOCK_EXPECTED_CASES.items():
        result = results[case]
        if not isinstance(result, Mapping) or result.get("case") != case:
            raise ValueError("mock_e2e_invalid")
        if case == "egress_network_missing":
            if dict(result) != {
                "case": case,
                "egress_network_absent": True,
                "terminal_status": "TOPOLOGY_BLOCKED",
            }:
                raise ValueError("mock_e2e_invalid")
            continue
        if case == "wrong_network_attachment":
            if dict(result) != {
                "case": case,
                "terminal_status": "TOPOLOGY_BLOCKED",
                "wrong_network_attachment_verified": True,
            }:
                raise ValueError("mock_e2e_invalid")
            continue
        if (
            result.get("terminal_status") != expected_status
            or result.get("double_network_topology") != "VERIFIED"
            or result.get("dispatch_gate_published") is not True
            or result.get("mock_dispatch_attempt_count") != 1
            or result.get("post_start_attachment_validation") != "PASSED"
            or type(result.get("container_exit_code")) is not int
        ):
            raise ValueError("mock_e2e_invalid")
        receipt = {
            key: value
            for key, value in result.items()
            if key
            not in {
                "case",
                "container_exit_code",
                "dispatch_gate_published",
                "double_network_topology",
                "mock_dispatch_attempt_count",
                "post_start_attachment_validation",
            }
        }
        try:
            validate_probe_receipt(
                receipt,
                probe_id=receipt.get("probe_id"),
                container_exit_code=result["container_exit_code"],
            )
        except (TypeError, ValueError) as error:
            raise ValueError("mock_e2e_invalid") from error


def _validate_attachment_harness_evidence(
    report: Mapping[str, Any],
    *,
    proxy_image_id: str,
    repo_root: Path,
) -> None:
    from scripts.run_phase2_ark_proxy_network_attachment_harness import (
        validate_report,
    )

    try:
        validate_report(report, proxy_image_id=proxy_image_id)
    except ValueError as error:
        raise ValueError("attachment_harness_evidence_invalid") from error
    expected_contract_sha = sha256_file(
        repo_root
        / "docker/phase2-ark-proxy-tls-probe/contracts/"
        "proxy-network-attachment-contract.json"
    )
    if report.get("attachment_contract_sha256") != expected_contract_sha:
        raise ValueError("attachment_harness_evidence_invalid")


def _validate_backend_failure_baseline(baseline: Mapping[str, Any]) -> None:
    if (
        baseline.get("passed") != 2656
        or baseline.get("failed") != 57
        or not isinstance(baseline.get("failure_set_sha256"), str)
        or _HEX_64.fullmatch(baseline["failure_set_sha256"]) is None
    ):
        raise ValueError("backend_failure_baseline_invalid")


def _validate_backend_failure_postcheck(
    postcheck: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    baseline_sha256: str,
    current_head: str,
) -> None:
    if (
        postcheck.get("status") != "KNOWN_BACKEND_FAILURE_SET_UNCHANGED"
        or postcheck.get("source_git_head") != current_head
        or postcheck.get("baseline_sha256") != baseline_sha256
        or postcheck.get("failed") != baseline.get("failed")
        or postcheck.get("failed_node_ids") != baseline.get("failed_node_ids")
        or postcheck.get("failure_set_sha256")
        != baseline.get("failure_set_sha256")
        or not isinstance(postcheck.get("passed"), int)
        or postcheck["passed"] < baseline.get("passed", 0)
    ):
        raise ValueError("backend_failure_postcheck_invalid")


def _external_activity() -> dict[str, Any]:
    return {
        "ai_call_count": 0,
        "authorization_constructed": False,
        "http_request_sent": False,
        "provider_attempt_count": 0,
        "real_proxy_tls_probe_attempts": 0,
        "real_public_network_success_count": 0,
        "secret_content_read": False,
    }


def _preflight_artifact(
    *,
    current_head: str,
    candidate_sha256: str,
    scope_id: str,
    build_provenance_sha256: str,
    network_attachment_harness_sha256: str,
    mock_e2e_sha256: str,
    backend_failure_set_sha256: str,
    historical_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_sha = historical_evidence.get("baseline_sha256")
    if (
        historical_evidence.get("checked_file_count") != 13
        or historical_evidence.get("status") != "UNCHANGED"
        or (
            baseline_sha is not None
            and (
                not isinstance(baseline_sha, str)
                or _HEX_64.fullmatch(baseline_sha) is None
            )
        )
    ):
        raise ValueError("historical_evidence_mutated")
    return {
        "approval_candidate_sha256": candidate_sha256,
        "approval_scope_id": scope_id,
        "backend_failure_set_sha256": backend_failure_set_sha256,
        "base_head": STARTING_HEAD,
        "base_orchestrator_source_sha256": STARTING_ORCHESTRATOR_SOURCE_SHA256,
        "base_proxy_source_sha256": STARTING_PROXY_SOURCE_SHA256,
        "build_provenance_sha256": build_provenance_sha256,
        "current_git_head": current_head,
        "external_activity": _external_activity(),
        "historical_evidence": dict(historical_evidence),
        "mock_e2e_sha256": mock_e2e_sha256,
        "network_attachment_harness_sha256": network_attachment_harness_sha256,
        "preparation_preflight_schema_version": 2,
        "status": "PROXY_TLS_PROBE_PREPARATION_PASSED",
    }


def _artifact_generation(
    *,
    current_head: str,
    artifacts: Mapping[str, str],
    candidate_sha256: str,
    scope_id: str,
) -> dict[str, Any]:
    return {
        "approval_candidate_sha256": candidate_sha256,
        "approval_scope_id": scope_id,
        "artifact_generation_schema_version": 1,
        "artifacts": dict(artifacts),
        "current_git_head": current_head,
        "external_activity": _external_activity(),
        "status": "PROXY_TLS_PROBE_ARTIFACTS_GENERATED",
    }


def prepare_artifacts(
    repo_root: Path,
    *,
    output_root: Path,
    attempts_root: Path,
    current_head: str,
    historical_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if _HEX_40.fullmatch(current_head) is None:
        raise ValueError("current_git_head_invalid")
    provenance_path = output_root / "build_provenance.json"
    mock_path = output_root / "mock_e2e.json"
    attachment_harness_path = output_root / "network_attachment_harness.json"
    baseline_path = output_root / "backend_failure_baseline.json"
    postcheck_path = output_root / "backend_failure_postcheck.json"
    provenance = read_canonical_json(provenance_path)
    mock = read_canonical_json(mock_path)
    attachment_harness = read_canonical_json(attachment_harness_path)
    baseline = read_canonical_json(baseline_path)
    postcheck = read_canonical_json(postcheck_path)
    image_id, ca_sha = _validate_build_provenance(
        provenance,
        current_head=current_head,
        repo_root=repo_root,
    )
    _validate_mock_evidence(mock, proxy_image_id=image_id)
    _validate_attachment_harness_evidence(
        attachment_harness,
        proxy_image_id=image_id,
        repo_root=repo_root,
    )
    _validate_backend_failure_baseline(baseline)
    _validate_backend_failure_postcheck(
        postcheck,
        baseline=baseline,
        baseline_sha256=sha256_file(baseline_path),
        current_head=current_head,
    )

    candidate = build_candidate(
        repo_root,
        current_git_head=current_head,
        proxy_image_id=image_id,
        build_provenance_sha256=sha256_file(provenance_path),
        ca_bundle_sha256=ca_sha,
    )
    candidate_raw = canonical_json_bytes(candidate)
    candidate_sha = sha256_bytes(candidate_raw)
    scope = build_scope(candidate_raw, attempts_root=attempts_root)
    scope_raw = canonical_json_bytes(scope)
    preflight = _preflight_artifact(
        current_head=current_head,
        candidate_sha256=candidate_sha,
        scope_id=scope["approval_scope_id"],
        build_provenance_sha256=sha256_file(provenance_path),
        network_attachment_harness_sha256=sha256_file(attachment_harness_path),
        mock_e2e_sha256=sha256_file(mock_path),
        backend_failure_set_sha256=baseline["failure_set_sha256"],
        historical_evidence=historical_evidence,
    )

    candidate_path = output_root / "approval_candidate.json"
    scope_path = output_root / "approval_scope.json"
    preflight_path = output_root / "preparation_preflight.json"
    atomic_write_json(candidate_path, candidate)
    atomic_write_json(scope_path, scope)
    atomic_write_json(preflight_path, preflight)
    artifacts = {
        "approval_candidate.json": sha256_bytes(candidate_raw),
        "approval_scope.json": sha256_bytes(scope_raw),
        "backend_failure_baseline.json": sha256_file(baseline_path),
        "backend_failure_postcheck.json": sha256_file(postcheck_path),
        "build_provenance.json": sha256_file(provenance_path),
        "mock_e2e.json": sha256_file(mock_path),
        "network_attachment_harness.json": sha256_file(
            attachment_harness_path
        ),
        "preparation_preflight.json": sha256_file(preflight_path),
    }
    generated = _artifact_generation(
        current_head=current_head,
        artifacts=artifacts,
        candidate_sha256=candidate_sha,
        scope_id=scope["approval_scope_id"],
    )
    atomic_write_json(output_root / "artifact_generation.json", generated)
    return generated


def verify_artifacts(
    repo_root: Path,
    *,
    output_root: Path,
    attempts_root: Path,
    current_head: str,
    historical_evidence: Mapping[str, Any],
    publish: bool,
) -> dict[str, Any]:
    provenance_path = output_root / "build_provenance.json"
    mock_path = output_root / "mock_e2e.json"
    attachment_harness_path = output_root / "network_attachment_harness.json"
    baseline_path = output_root / "backend_failure_baseline.json"
    postcheck_path = output_root / "backend_failure_postcheck.json"
    candidate_path = output_root / "approval_candidate.json"
    scope_path = output_root / "approval_scope.json"
    preflight_path = output_root / "preparation_preflight.json"
    generation_path = output_root / "artifact_generation.json"

    provenance = read_canonical_json(provenance_path)
    mock = read_canonical_json(mock_path)
    attachment_harness = read_canonical_json(attachment_harness_path)
    baseline = read_canonical_json(baseline_path)
    postcheck = read_canonical_json(postcheck_path)
    image_id, ca_sha = _validate_build_provenance(
        provenance,
        current_head=current_head,
        repo_root=repo_root,
    )
    _validate_mock_evidence(mock, proxy_image_id=image_id)
    _validate_attachment_harness_evidence(
        attachment_harness,
        proxy_image_id=image_id,
        repo_root=repo_root,
    )
    _validate_backend_failure_baseline(baseline)
    _validate_backend_failure_postcheck(
        postcheck,
        baseline=baseline,
        baseline_sha256=sha256_file(baseline_path),
        current_head=current_head,
    )
    expected_candidate = build_candidate(
        repo_root,
        current_git_head=current_head,
        proxy_image_id=image_id,
        build_provenance_sha256=sha256_file(provenance_path),
        ca_bundle_sha256=ca_sha,
    )
    if candidate_path.read_bytes() != canonical_json_bytes(expected_candidate):
        raise ValueError("approval_candidate_mismatch")
    candidate_raw = candidate_path.read_bytes()
    candidate_sha = sha256_bytes(candidate_raw)
    expected_scope = build_scope(candidate_raw, attempts_root=attempts_root)
    if scope_path.read_bytes() != canonical_json_bytes(expected_scope):
        raise ValueError("approval_scope_mismatch")
    expected_preflight = _preflight_artifact(
        current_head=current_head,
        candidate_sha256=candidate_sha,
        scope_id=expected_scope["approval_scope_id"],
        build_provenance_sha256=sha256_file(provenance_path),
        network_attachment_harness_sha256=sha256_file(attachment_harness_path),
        mock_e2e_sha256=sha256_file(mock_path),
        backend_failure_set_sha256=baseline["failure_set_sha256"],
        historical_evidence=historical_evidence,
    )
    if preflight_path.read_bytes() != canonical_json_bytes(expected_preflight):
        raise ValueError("preparation_preflight_mismatch")
    artifact_hashes = {
        "approval_candidate.json": sha256_file(candidate_path),
        "approval_scope.json": sha256_file(scope_path),
        "backend_failure_baseline.json": sha256_file(baseline_path),
        "backend_failure_postcheck.json": sha256_file(postcheck_path),
        "build_provenance.json": sha256_file(provenance_path),
        "mock_e2e.json": sha256_file(mock_path),
        "network_attachment_harness.json": sha256_file(
            attachment_harness_path
        ),
        "preparation_preflight.json": sha256_file(preflight_path),
    }
    expected_generation = _artifact_generation(
        current_head=current_head,
        artifacts=artifact_hashes,
        candidate_sha256=candidate_sha,
        scope_id=expected_scope["approval_scope_id"],
    )
    if generation_path.read_bytes() != canonical_json_bytes(expected_generation):
        raise ValueError("artifact_generation_mismatch")
    verification = {
        "approval_candidate_sha256": candidate_sha,
        "approval_scope_id": expected_scope["approval_scope_id"],
        "artifact_hashes": artifact_hashes,
        "build_provenance": "PASSED",
        "candidate": "VALID",
        "current_git_head": current_head,
        "double_network_topology": "VERIFIED",
        "external_activity": _external_activity(),
        "happy_path_x3": "PASSED",
        "historical_evidence": dict(historical_evidence),
        "known_backend_failure_set": "UNCHANGED",
        "network_attachment_harness": "PASSED",
        "production_topology_mock": "PASSED",
        "scope": "AVAILABLE",
        "scope_historical_attempts": 0,
        "status": "PROXY_TLS_PROBE_OFFLINE_VERIFIED",
        "tls_precise_classification": "READY",
        "verification_schema_version": 1,
    }
    if publish:
        atomic_write_json(output_root / "verification.json", verification)
    return verification


if __name__ == "__main__":
    raise SystemExit(main())
