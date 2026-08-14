#!/usr/bin/env python3
"""Write/check Ark immutable contract and Approval Candidate artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.providers.ark_contract import (
    _ARTIFACT_GENERATION_FILES,
    _ARTIFACT_GENERATION_PATH,
    ark_source_snapshot,
    build_ark_approval_candidate,
    build_ark_approval_scope_evidence,
    build_ark_artifact_generation_manifest,
    build_ark_build_provenance,
    build_ark_responses_contract,
    exclusive_ark_artifact_lock,
    load_ark_artifact_generation,
    load_ark_build_provenance,
    validate_ark_approval_candidate,
    validate_ark_build_provenance,
    validate_ark_image_set,
    validate_ark_mock_e2e_evidence,
    validate_ark_mock_e2e_historical_evidence,
    validate_ark_source_snapshot,
    validate_ark_timeout_mock_evidence,
)
from app.services.phase2_claims_service import canonical_json_bytes
from scripts.run_phase2_ark_single_symbol_canary import (
    normalize_committed_runtime_modes,
)

PROXY_IMAGE = "tickflow-phase2-ark-egress-proxy:timeout-v2"
RELAY_IMAGE = "tickflow-phase2-ark-canary-relay:timeout-v2"
BASE_IMAGE = (
    "gcr.io/distroless/python3-debian12@"
    "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)


def _inspect_image(reference: str) -> dict[str, object]:
    result = subprocess.run(
        ["docker", "image", "inspect", reference],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError("ark_image_missing")
    value = json.loads(result.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError("ark_image_inspect_invalid")
    image = value[0]
    if not isinstance(image, dict):
        raise RuntimeError("ark_image_inspect_invalid")
    return image


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


def _invalidate_artifact_generation(manifest_path: Path) -> None:
    try:
        metadata = manifest_path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("ark_artifact_generation_invalid")
    manifest_path.unlink()
    directory = os.open(
        manifest_path.parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _publish_artifact_generation(
    *,
    targets: dict[Path, bytes],
    manifest_path: Path,
    manifest_raw: bytes,
    validate_published,
    writer=_atomic_write,
) -> None:
    _invalidate_artifact_generation(manifest_path)
    for path, raw in targets.items():
        writer(path, raw)
    validate_published()
    writer(manifest_path, manifest_raw)


def _validate_published_generation(root: Path, expected_scope: bytes) -> None:
    provenance, _ = load_ark_build_provenance(root)
    validate_ark_build_provenance(root, provenance)
    mock = json.loads((root / "reports/phase2_provider_ark/mock_e2e.json").read_bytes())
    candidate_path = root / "reports/phase2_provider_ark/approval_candidate.json"
    candidate = json.loads(candidate_path.read_bytes())
    if candidate.get("ark_approval_candidate_schema_version") == 2:
        validate_ark_mock_e2e_historical_evidence(mock)
    else:
        validate_ark_mock_e2e_evidence(root, mock)
    timeout_mock = json.loads(
        (root / "reports/phase2_provider_ark/timeout_mock_e2e.json").read_bytes()
    )
    validate_ark_timeout_mock_evidence(timeout_mock)
    validate_ark_approval_candidate(root, candidate)
    if (
        root.joinpath("reports/phase2_provider_ark/approval_scope_preflight.json").read_bytes()
        != expected_scope
    ):
        raise RuntimeError("ark_artifact_generation_invalid")


def _build_candidate_generation(
    root: Path,
    args: argparse.Namespace,
    *,
    proxy_image_id: str,
    relay_image_id: str,
    proxy_labels,
    relay_labels,
) -> int:
    with ark_source_snapshot(root):
        return _build_candidate_generation_snapshot(
            root,
            args,
            proxy_image_id=proxy_image_id,
            relay_image_id=relay_image_id,
            proxy_labels=proxy_labels,
            relay_labels=relay_labels,
        )


def _build_candidate_generation_snapshot(
    root: Path,
    args: argparse.Namespace,
    *,
    proxy_image_id: str,
    relay_image_id: str,
    proxy_labels,
    relay_labels,
) -> int:
    validate_ark_image_set(
        repo_root=root,
        proxy_image_id=proxy_image_id,
        relay_image_id=relay_image_id,
        proxy_labels=proxy_labels,
        relay_labels=relay_labels,
    )
    provenance_path = root / "reports/phase2_provider_ark/ark_build_provenance.json"
    provenance, provenance_raw = load_ark_build_provenance(root)
    validate_ark_build_provenance(root, provenance)
    if (
        provenance["proxy_image_id"] != proxy_image_id
        or provenance["relay_image_id"] != relay_image_id
        or hashlib.sha256(provenance_path.read_bytes()).hexdigest()
        != hashlib.sha256(provenance_raw).hexdigest()
    ):
        raise RuntimeError("ark_build_provenance_image_mismatch")
    contract = canonical_json_bytes(build_ark_responses_contract(root))
    mock_path = root / "reports/phase2_provider_ark/mock_e2e.json"
    mock_sha256 = hashlib.sha256(mock_path.read_bytes()).hexdigest()
    candidate = canonical_json_bytes(
        build_ark_approval_candidate(
            root,
            proxy_image_id=proxy_image_id,
            relay_image_id=relay_image_id,
            current_git_head=getattr(args, "source_git_head", None),
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
    validate_ark_source_snapshot(root)
    if args.write:
        artifact_raw: dict[str, bytes] = {}
        for relative in _ARTIFACT_GENERATION_FILES:
            path = root / relative
            artifact_raw[relative] = targets[path] if path in targets else path.read_bytes()
        manifest_raw = canonical_json_bytes(build_ark_artifact_generation_manifest(artifact_raw))
        _publish_artifact_generation(
            targets=targets,
            manifest_path=root / _ARTIFACT_GENERATION_PATH,
            manifest_raw=manifest_raw,
            validate_published=lambda: _validate_published_generation(root, scope),
        )
        load_ark_artifact_generation(root)
        return 0
    if not all(path.read_bytes() == raw for path, raw in targets.items()):
        return 2
    load_ark_artifact_generation(root)
    return 0


def _main_locked(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[2]
    normalize_committed_runtime_modes(root)
    proxy_inspect = _inspect_image(PROXY_IMAGE)
    relay_inspect = _inspect_image(RELAY_IMAGE)
    base_inspect = _inspect_image(BASE_IMAGE)
    proxy_image_id = str(proxy_inspect.get("Id"))
    relay_image_id = str(relay_inspect.get("Id"))
    proxy_labels = proxy_inspect.get("Config", {}).get("Labels", {})
    relay_labels = relay_inspect.get("Config", {}).get("Labels", {})
    provenance_path = root / "reports/phase2_provider_ark/ark_build_provenance.json"
    if args.capture_build_provenance:
        provenance = canonical_json_bytes(
            build_ark_build_provenance(
                root,
                base_inspect=base_inspect,
                proxy_inspect=proxy_inspect,
                relay_inspect=relay_inspect,
                captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                build_path=args.build_path,
            )
        )
        if not args.write:
            raise RuntimeError("ark_build_provenance_write_required")
        _invalidate_artifact_generation(root / _ARTIFACT_GENERATION_PATH)
        _atomic_write(provenance_path, provenance)
    return _build_candidate_generation(
        root,
        args,
        proxy_image_id=proxy_image_id,
        relay_image_id=relay_image_id,
        proxy_labels=proxy_labels,
        relay_labels=relay_labels,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--source-git-head")
    parser.add_argument("--capture-build-provenance", action="store_true")
    parser.add_argument(
        "--build-path",
        choices=("READ_ONLY_EXISTING_IMAGE_INSPECT", "FRESH_OFFLINE_REBUILD"),
        default="READ_ONLY_EXISTING_IMAGE_INSPECT",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    with exclusive_ark_artifact_lock(root):
        return _main_locked(args)


if __name__ == "__main__":
    raise SystemExit(main())
