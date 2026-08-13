#!/usr/bin/env python3
"""Build the Ark timeout-contract images without network access."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from app.providers.ark_contract import (
    ark_proxy_policy_sha256,
    build_ark_responses_contract,
)
from app.services.phase2_ark_timeout_contract import (
    ark_runtime_contract_sha256,
    write_ark_runtime_contract_copies,
)
from app.services.phase2_canary_runtime_artifact import BASE_IMAGE_DIGEST
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


def main() -> int:
    root = Path(__file__).resolve().parents[2]
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
    proxy_context = root / "docker/phase2-ark-egress-proxy"
    relay_context = root / "docker/phase2-ark-canary-relay"
    _run(
        _build_command(
            PROXY_IMAGE,
            proxy_context,
            {
                "BASE_IMAGE_DIGEST": BASE_IMAGE_DIGEST,
                "PROXY_SOURCE_SHA256": _sha256(proxy_context / "proxy.py"),
                "RESPONSES_CONTRACT_SHA256": _sha256(contract_path),
                "PROXY_POLICY_SHA256": ark_proxy_policy_sha256(),
                "RUNTIME_CONTRACT_SHA256": runtime_sha,
                "READINESS_CONTRACT_SHA256": _sha256(
                    proxy_context / "readiness-contract.json"
                ),
            },
        ),
        cwd=root,
    )
    _run(
        _build_command(
            RELAY_IMAGE,
            relay_context,
            {
                "BASE_IMAGE_DIGEST": BASE_IMAGE_DIGEST,
                "RELAY_SOURCE_SHA256": _sha256(relay_context / "relay.py"),
                "RUNTIME_CONTRACT_SHA256": runtime_sha,
            },
        ),
        cwd=root,
    )
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


if __name__ == "__main__":
    raise SystemExit(main())
