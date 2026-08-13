#!/usr/bin/env python3
"""Write/check Ark immutable contract and Approval Candidate artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from app.providers.ark_contract import (
    build_ark_approval_candidate,
    build_ark_approval_scope_evidence,
    build_ark_responses_contract,
    validate_ark_image_set,
)
from app.services.phase2_claims_service import canonical_json_bytes
from scripts.run_phase2_ark_single_symbol_canary import (
    normalize_committed_runtime_modes,
)

PROXY_IMAGE = "tickflow-phase2-ark-egress-proxy:timeout-v2"
RELAY_IMAGE = "tickflow-phase2-ark-canary-relay:timeout-v2"


def _inspect_image(reference: str) -> tuple[str, dict[str, str]]:
    result = subprocess.run(
        ["docker", "image", "inspect", reference],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("ark_image_missing")
    value = json.loads(result.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError("ark_image_inspect_invalid")
    image = value[0]
    labels = image.get("Config", {}).get("Labels", {})
    if not isinstance(image.get("Id"), str) or not isinstance(labels, dict):
        raise RuntimeError("ark_image_inspect_invalid")
    return image["Id"], labels


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    normalize_committed_runtime_modes(root)
    proxy_image_id, proxy_labels = _inspect_image(PROXY_IMAGE)
    relay_image_id, relay_labels = _inspect_image(RELAY_IMAGE)
    validate_ark_image_set(
        repo_root=root,
        proxy_image_id=proxy_image_id,
        relay_image_id=relay_image_id,
        proxy_labels=proxy_labels,
        relay_labels=relay_labels,
    )
    contract = canonical_json_bytes(build_ark_responses_contract(root))
    mock_path = root / "reports/phase2_provider_ark/mock_e2e.json"
    mock_sha256 = hashlib.sha256(mock_path.read_bytes()).hexdigest()
    candidate = canonical_json_bytes(
        build_ark_approval_candidate(
            root,
            proxy_image_id=proxy_image_id,
            relay_image_id=relay_image_id,
        )
    )
    scope = canonical_json_bytes(
        build_ark_approval_scope_evidence(
            candidate,
            repo_root=root,
            mock_e2e_sha256=mock_sha256,
        )
    )
    targets = {
        root / "docker/phase2-ark-egress-proxy/responses-contract.json": contract,
        root / "reports/phase2_provider_ark/approval_candidate.json": candidate,
        root / "reports/phase2_provider_ark/approval_scope_preflight.json": scope,
    }
    if args.write:
        for path, raw in targets.items():
            _atomic_write(path, raw)
        return 0
    return 0 if all(path.read_bytes() == raw for path, raw in targets.items()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
