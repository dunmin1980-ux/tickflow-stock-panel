#!/usr/bin/env python3
"""Build and verify both Canary runtime images without registry or Provider egress."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.services.phase2_canary_runtime_artifact import (
    BASE_IMAGE_REFERENCE,
    OLD_CANDIDATE_SHA256,
    READY_STATUS,
    RuntimeArtifactError,
    RuntimeArtifactInputHashes,
    build_runtime_artifact_candidate,
    build_runtime_image_commands,
    compute_runtime_artifact_input_hashes,
    inspect_runtime_image,
    validate_local_base_image,
    verify_runtime_artifact_candidate,
    verify_runtime_image_contents,
)
from app.services.phase2_claims_service import canonical_json_bytes

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPORT_ROOT = Path("reports/phase2_provider_canary")
_CANDIDATE = _REPORT_ROOT / "runtime_contract_candidate.json"
_EVIDENCE = _REPORT_ROOT / "runtime_contract_build_evidence.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run(
    command: Sequence[str],
    *,
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeArtifactError("artifact_executor_failed") from exc


def _artifact_executor(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return _run(command, timeout=90)


def _json_result(command: Sequence[str], category: str) -> Any:
    result = _run(command)
    if result.returncode != 0:
        raise RuntimeArtifactError(category)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeArtifactError(category) from exc


def _history(image_id: str) -> list[dict[str, Any]]:
    result = _run(
        [
            "docker",
            "history",
            "--no-trunc",
            "--format",
            "{{json .}}",
            image_id,
        ]
    )
    if result.returncode != 0:
        raise RuntimeArtifactError("artifact_image_history_invalid")
    try:
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise RuntimeArtifactError("artifact_image_history_invalid") from exc


def _read_iid(path: Path) -> str:
    try:
        metadata = os.lstat(path)
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeArtifactError("artifact_iidfile_invalid") from exc
    image_id = raw.decode("ascii", errors="strict").strip()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or len(raw) > 80
        or not _IMAGE_ID.fullmatch(image_id)
    ):
        raise RuntimeArtifactError("artifact_iidfile_invalid")
    return image_id


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeArtifactError("artifact_report_path_invalid")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise RuntimeArtifactError("artifact_report_write_failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise RuntimeArtifactError("artifact_report_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _build_image(command: Sequence[str], iidfile: Path) -> str:
    result = _run(command, timeout=300)
    if result.returncode != 0:
        raise RuntimeArtifactError("artifact_offline_build_failed")
    return _read_iid(iidfile)


def _inspect_images(
    hashes: RuntimeArtifactInputHashes,
    *,
    proxy_image_id: str,
    relay_image_id: str,
):
    base = validate_local_base_image(
        _json_result(
            ["docker", "image", "inspect", BASE_IMAGE_REFERENCE],
            "artifact_base_image_unavailable",
        )
    )
    proxy = inspect_runtime_image(
        _json_result(
            ["docker", "image", "inspect", proxy_image_id],
            "artifact_image_inspect_invalid",
        ),
        _history(proxy_image_id),
        role="proxy",
        hashes=hashes,
        base=base,
        expected_image_id=proxy_image_id,
    )
    relay = inspect_runtime_image(
        _json_result(
            ["docker", "image", "inspect", relay_image_id],
            "artifact_image_inspect_invalid",
        ),
        _history(relay_image_id),
        role="relay",
        hashes=hashes,
        base=base,
        expected_image_id=relay_image_id,
    )
    return base, proxy, relay


def build(repo_root: Path) -> str:
    base = validate_local_base_image(
        _json_result(
            ["docker", "image", "inspect", BASE_IMAGE_REFERENCE],
            "artifact_base_image_unavailable",
        )
    )
    hashes_before = compute_runtime_artifact_input_hashes(repo_root)
    with tempfile.TemporaryDirectory(prefix="phase2-canary-runtime-build-") as temp:
        staging = Path(temp)
        staging.chmod(0o700)
        proxy_iid = staging / "proxy.iid"
        relay_iid = staging / "relay.iid"
        proxy_command, relay_command = build_runtime_image_commands(
            repo_root,
            hashes_before,
            proxy_iidfile=proxy_iid,
            relay_iidfile=relay_iid,
        )
        proxy_image_id = _build_image(proxy_command, proxy_iid)
        relay_image_id = _build_image(relay_command, relay_iid)
    hashes_after = compute_runtime_artifact_input_hashes(repo_root)
    if hashes_before != hashes_after:
        raise RuntimeArtifactError("artifact_build_input_mutated")
    inspected_base, proxy, relay = _inspect_images(
        hashes_after,
        proxy_image_id=proxy_image_id,
        relay_image_id=relay_image_id,
    )
    if inspected_base != base:
        raise RuntimeArtifactError("artifact_base_image_mutated")
    proxy_contents = verify_runtime_image_contents(
        repo_root,
        role="proxy",
        image_id=proxy_image_id,
        executor=_artifact_executor,
    )
    relay_contents = verify_runtime_image_contents(
        repo_root,
        role="relay",
        image_id=relay_image_id,
        executor=_artifact_executor,
    )
    candidate = build_runtime_artifact_candidate(
        repo_root,
        proxy=proxy,
        relay=relay,
        proxy_contents=proxy_contents,
        relay_contents=relay_contents,
    )
    candidate_raw = canonical_json_bytes(candidate.model_dump(mode="json"))
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    evidence = {
        "runtime_build_evidence_schema_version": 1,
        "status": READY_STATUS,
        "approval_candidate_sha256": candidate_sha256,
        "old_approval_candidate_sha256": OLD_CANDIDATE_SHA256,
        "inputs_before": hashes_before.model_dump(mode="json"),
        "inputs_after": hashes_after.model_dump(mode="json"),
        "input_hashes_stable": True,
        "base_image": base.model_dump(mode="json"),
        "proxy_image": proxy.model_dump(mode="json"),
        "relay_image": relay.model_dump(mode="json"),
        "proxy_contents": proxy_contents.model_dump(mode="json"),
        "relay_contents": relay_contents.model_dump(mode="json"),
        "fresh_offline_build": True,
        "build_network": "none",
        "pull_allowed": False,
        "no_cache": True,
        "base_digest_pinned": True,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "provider_http": "NOT_RUN",
        "tickflow_request_count": 0,
        "real_public_network_success_count": 0,
        "container_residue_count": 0,
        "network_residue_count": 0,
        "temporary_file_residue_count": 0,
        "new_approval_installed": False,
        "historical_evidence": "UNCHANGED",
    }
    root = repo_root.resolve(strict=True)
    candidate_path = root / _CANDIDATE
    evidence_path = root / _EVIDENCE
    _atomic_write(candidate_path, candidate_raw)
    _atomic_write(evidence_path, canonical_json_bytes(evidence))
    verify_runtime_artifact_candidate(
        root,
        candidate_path,
        expected_candidate_sha256=candidate_sha256,
        executor=_artifact_executor,
    )
    return candidate_sha256


def verify(repo_root: Path) -> str:
    root = repo_root.resolve(strict=True)
    candidate_path = root / _CANDIDATE
    evidence_path = root / _EVIDENCE
    candidate_raw = candidate_path.read_bytes()
    evidence_raw = evidence_path.read_bytes()
    try:
        evidence = json.loads(evidence_raw)
    except json.JSONDecodeError as exc:
        raise RuntimeArtifactError("artifact_build_evidence_invalid") from exc
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    if (
        not isinstance(evidence, dict)
        or evidence_raw != canonical_json_bytes(evidence)
        or evidence.get("runtime_build_evidence_schema_version") != 1
        or evidence.get("status") != READY_STATUS
        or evidence.get("approval_candidate_sha256") != candidate_sha256
        or evidence.get("old_approval_candidate_sha256") != OLD_CANDIDATE_SHA256
        or evidence.get("inputs_before") != evidence.get("inputs_after")
        or evidence.get("input_hashes_stable") is not True
        or evidence.get("fresh_offline_build") is not True
        or evidence.get("build_network") != "none"
        or evidence.get("pull_allowed") is not False
        or evidence.get("no_cache") is not True
        or evidence.get("base_digest_pinned") is not True
        or evidence.get("provider_attempt_count") != 0
        or evidence.get("ai_call_count") != 0
        or evidence.get("provider_http") != "NOT_RUN"
        or evidence.get("tickflow_request_count") != 0
        or evidence.get("real_public_network_success_count") != 0
        or evidence.get("container_residue_count") != 0
        or evidence.get("network_residue_count") != 0
        or evidence.get("temporary_file_residue_count") != 0
        or evidence.get("new_approval_installed") is not False
        or evidence.get("historical_evidence") != "UNCHANGED"
    ):
        raise RuntimeArtifactError("artifact_build_evidence_invalid")
    verify_runtime_artifact_candidate(
        root,
        candidate_path,
        expected_candidate_sha256=candidate_sha256,
        executor=_artifact_executor,
    )
    return candidate_sha256


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    repo_root = _repo_root()
    candidate_sha256 = build(repo_root) if args.build else verify(repo_root)
    print(
        json.dumps(
            {
                "approval_candidate_sha256": candidate_sha256,
                "provider_attempt_count": 0,
                "ai_call_count": 0,
                "provider_http": "NOT_RUN",
                "status": READY_STATUS,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
