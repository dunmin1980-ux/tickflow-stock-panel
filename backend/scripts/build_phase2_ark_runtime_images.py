#!/usr/bin/env python3
"""Build the Ark timeout-contract images without network access."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from app.providers.ark_contract import (
    ark_proxy_policy_sha256,
    build_ark_responses_contract,
    exclusive_ark_artifact_lock,
    invalidate_ark_artifact_generation,
)
from app.services.phase2_ark_timeout_contract import (
    ark_runtime_contract_sha256,
    write_ark_runtime_contract_copies,
)
from app.services.phase2_canary_runtime_artifact import (
    BASE_IMAGE_DIGEST,
    BASE_IMAGE_REFERENCE,
)
from app.services.phase2_claims_service import canonical_json_bytes

PROXY_IMAGE = "tickflow-phase2-ark-egress-proxy:timeout-v2"
RELAY_IMAGE = "tickflow-phase2-ark-canary-relay:timeout-v2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o644)
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


def _run(command: list[str], *, cwd: Path) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "ark_runtime_image_build_failed:"
            + json.dumps(
                {"command": command[:4], "stderr": result.stderr[-2000:]},
                sort_keys=True,
            )
        )


def _build_command(tag: str, context: Path, arguments: dict[str, str]) -> list[str]:
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
    for key, value in sorted(arguments.items()):
        command.extend(["--build-arg", f"{key}={value}"])
    command.append(str(context))
    return command


def _inspect_local_base(reference: str) -> dict[str, object]:
    result = subprocess.run(
        ["docker", "image", "inspect", reference],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    try:
        values = json.loads(result.stdout) if result.returncode == 0 else None
    except json.JSONDecodeError:
        values = None
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
        or values[0].get("Id") != BASE_IMAGE_DIGEST
    ):
        raise RuntimeError("ARK_OFFLINE_REBUILD_BLOCKED_BASE_IMAGE_MISSING")
    return values[0]


def _context_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError("ark_build_context_invalid")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("ark_build_context_invalid")
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        hashes[relative] = _sha256(path)
    return hashes


def _copy_readonly_context(source: Path, target: Path) -> dict[str, str]:
    hashes = _context_hashes(source)
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
    if _context_hashes(target) != hashes:
        raise RuntimeError("ark_build_context_invalid")
    return hashes


def _build_runtime_images(
    root: Path,
    *,
    runtime_sha: str,
    runner=_run,
    image_inspector=_inspect_local_base,
) -> None:
    base = image_inspector(BASE_IMAGE_REFERENCE)
    if base.get("Id") != BASE_IMAGE_DIGEST:
        raise RuntimeError("ARK_OFFLINE_REBUILD_BLOCKED_BASE_IMAGE_MISSING")
    proxy_source = root / "docker/phase2-ark-egress-proxy"
    relay_source = root / "docker/phase2-ark-canary-relay"
    with tempfile.TemporaryDirectory(prefix="phase2-ark-offline-build-") as name:
        staging = Path(name).resolve(strict=True)
        staging.chmod(0o700)
        proxy_context = staging / "proxy"
        relay_context = staging / "relay"
        proxy_hashes = _copy_readonly_context(proxy_source, proxy_context)
        relay_hashes = _copy_readonly_context(relay_source, relay_context)
        runner(
            _build_command(
                PROXY_IMAGE,
                proxy_context,
                {
                    "BASE_IMAGE_DIGEST": BASE_IMAGE_DIGEST,
                    "PROXY_DOCKERFILE_SHA256": proxy_hashes["Dockerfile"],
                    "PROXY_SOURCE_SHA256": proxy_hashes["proxy.py"],
                    "RESPONSES_CONTRACT_SHA256": proxy_hashes["responses-contract.json"],
                    "PROXY_POLICY_SHA256": ark_proxy_policy_sha256(),
                    "RUNTIME_CONTRACT_SHA256": runtime_sha,
                    "READINESS_CONTRACT_SHA256": proxy_hashes["readiness-contract.json"],
                },
            ),
            cwd=root,
        )
        runner(
            _build_command(
                RELAY_IMAGE,
                relay_context,
                {
                    "BASE_IMAGE_DIGEST": BASE_IMAGE_DIGEST,
                    "RELAY_DOCKERFILE_SHA256": relay_hashes["Dockerfile"],
                    "RELAY_SOURCE_SHA256": relay_hashes["relay.py"],
                    "RUNTIME_CONTRACT_SHA256": runtime_sha,
                },
            ),
            cwd=root,
        )
        if (
            _context_hashes(proxy_context) != proxy_hashes
            or _context_hashes(relay_context) != relay_hashes
            or _context_hashes(proxy_source) != proxy_hashes
            or _context_hashes(relay_source) != relay_hashes
        ):
            raise RuntimeError("ark_build_context_changed")


def _main_locked(root: Path) -> int:
    invalidate_ark_artifact_generation(root)
    write_ark_runtime_contract_copies(root)
    contract = canonical_json_bytes(build_ark_responses_contract(root))
    contract_path = root / "docker/phase2-ark-egress-proxy/responses-contract.json"
    _atomic_write(contract_path, contract)
    _run(
        [
            str(root / "backend/.venv/bin/python"),
            str(root / "backend/scripts/prepare_phase2_ark_proxy_source.py"),
            "--write",
        ],
        cwd=root,
    )
    _run(
        [
            str(root / "backend/.venv/bin/python"),
            str(root / "backend/scripts/prepare_phase2_ark_relay_source.py"),
            "--write",
        ],
        cwd=root,
    )
    runtime_sha = ark_runtime_contract_sha256()
    _build_runtime_images(root, runtime_sha=runtime_sha)
    print(
        json.dumps(
            {
                "status": "BUILT",
                "provider_http": "NOT_RUN",
                "proxy_image": PROXY_IMAGE,
                "relay_image": RELAY_IMAGE,
                "runtime_contract_sha256": runtime_sha,
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    with exclusive_ark_artifact_lock(root):
        return _main_locked(root)


if __name__ == "__main__":
    raise SystemExit(main())
