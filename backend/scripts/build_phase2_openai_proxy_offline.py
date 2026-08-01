#!/usr/bin/env python3
"""Build and verify the dedicated OpenAI proxy without registry or runtime egress."""

from __future__ import annotations

import argparse
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

from app.services.phase2_openai_proxy_artifact import (
    BASE_IMAGE_REFERENCE,
    PHASE2B_CANARY_BUILD_INPUT_BLOCKED,
    FreshBuildProvenance,
    OpenAIProxyArtifactCandidate,
    OpenAIProxyArtifactError,
    build_artifact_candidate,
    build_offline_image_command,
    compute_artifact_input_hashes,
    inspect_openai_proxy_image,
    validate_fresh_build_provenance,
    validate_local_base_image,
    verify_image_contents,
    write_artifact_reports,
)

_REPORT_SENSITIVE = re.compile(
    rb"sk-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9_-]{20,}|"
    rb"OPENAI_API_KEY\s*[:=]",
    re.IGNORECASE,
)
_READY_EVIDENCE_FIELDS = {
    "evidence_schema_version",
    "status",
    "image_name",
    "image_id",
    "offline_build",
    "pull_allowed",
    "build_network",
    "image_identity_valid",
    "image_history_clean",
    "image_contents_valid",
    "cleanup_complete",
    "fresh_offline_build",
    "no_cache",
    "iidfile_verified",
    "input_hashes_stable",
    "base_rootfs_prefix_verified",
    "provider_attempt_count",
    "ai_call_count",
    "tickflow_request_count",
    "public_network_request_count",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _executor(
    command: Sequence[str],
    *,
    check: bool,
    capture_output: bool,
    text: bool,
    shell: bool,
    timeout: int,
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        list(command),
        check=check,
        capture_output=capture_output,
        text=text,
        shell=shell,
        timeout=timeout,
    )


def _strict_json(raw: str, category: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite")

    try:
        return json.loads(raw, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise OpenAIProxyArtifactError(category) from exc


def _run(command: Sequence[str], *, timeout: int = 60) -> subprocess.CompletedProcess[Any]:
    try:
        return _executor(
            command,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpenAIProxyArtifactError("artifact_executor_failed") from exc


def _base_evidence():
    result = _run(["docker", "image", "inspect", BASE_IMAGE_REFERENCE])
    if result.returncode != 0:
        raise OpenAIProxyArtifactError(PHASE2B_CANARY_BUILD_INPUT_BLOCKED)
    try:
        return validate_local_base_image(
            _strict_json(result.stdout, "artifact_base_image_unavailable")
        )
    except OpenAIProxyArtifactError as exc:
        raise OpenAIProxyArtifactError(PHASE2B_CANARY_BUILD_INPUT_BLOCKED) from exc


def _image_evidence(
    repo_root: Path,
    *,
    expected_image_id: str,
    base: Any,
):
    hashes = compute_artifact_input_hashes(repo_root)
    inspect_result = _run(["docker", "image", "inspect", expected_image_id])
    history_result = _run(
        [
            "docker",
            "history",
            "--no-trunc",
            "--format",
            "{{json .}}",
            expected_image_id,
        ]
    )
    if inspect_result.returncode != 0 or history_result.returncode != 0:
        raise OpenAIProxyArtifactError("artifact_image_inspect_invalid")
    inspect_payload = _strict_json(
        inspect_result.stdout,
        "artifact_image_inspect_invalid",
    )
    history = [
        _strict_json(line, "artifact_image_history_invalid")
        for line in history_result.stdout.splitlines()
        if line.strip()
    ]
    return hashes, inspect_openai_proxy_image(
        inspect_payload,
        history,
        hashes,
        base,
        expected_image_id=expected_image_id,
    )


def _read_iidfile(path: Path) -> str:
    try:
        metadata = os.lstat(path)
        raw = path.read_bytes()
    except OSError as exc:
        raise OpenAIProxyArtifactError("artifact_iidfile_invalid") from exc
    image_id = raw.decode("ascii", errors="strict").strip()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or len(raw) > 80
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
    ):
        raise OpenAIProxyArtifactError("artifact_iidfile_invalid")
    return image_id


def _fresh_offline_build(repo_root: Path, *, base: Any):
    hashes_before = compute_artifact_input_hashes(repo_root)
    with tempfile.TemporaryDirectory(
        prefix="tickflow-phase2-openai-proxy-build-"
    ) as temporary:
        staging = Path(temporary)
        staging.chmod(0o700)
        iidfile = staging / "image.iid"
        result = _run(
            build_offline_image_command(
                repo_root,
                hashes_before,
                iidfile=iidfile,
            ),
            timeout=300,
        )
        if result.returncode != 0:
            raise OpenAIProxyArtifactError("artifact_offline_build_failed")
        image_id = _read_iidfile(iidfile)
    hashes_after, image = _image_evidence(
        repo_root,
        expected_image_id=image_id,
        base=base,
    )
    provenance = validate_fresh_build_provenance(
        hashes_before=hashes_before,
        hashes_after=hashes_after,
        iidfile_image_id=image_id,
        image=image,
        base=base,
    )
    return image, provenance


def _read_report(path: Path, category: str) -> tuple[bytes, dict[str, Any]]:
    try:
        metadata = os.lstat(path)
        raw = path.read_bytes()
    except OSError as exc:
        raise OpenAIProxyArtifactError(category) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or not 0 < len(raw) == metadata.st_size <= 65_536
        or _REPORT_SENSITIVE.search(raw)
    ):
        raise OpenAIProxyArtifactError(category)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenAIProxyArtifactError(category) from exc
    value = _strict_json(decoded, category)
    if not isinstance(value, dict):
        raise OpenAIProxyArtifactError(category)
    return raw, value


def _load_attested_candidate(
    repo_root: Path,
    *,
    base: Any,
) -> tuple[OpenAIProxyArtifactCandidate, FreshBuildProvenance]:
    report_root = repo_root / "reports/phase2_openai_proxy_artifact"
    candidate_raw, _candidate_value = _read_report(
        report_root / "approval_candidate.json",
        "artifact_candidate_invalid",
    )
    _evidence_raw, evidence = _read_report(
        report_root / "build_evidence.json",
        "artifact_build_evidence_invalid",
    )
    try:
        candidate = OpenAIProxyArtifactCandidate.model_validate_json(candidate_raw)
    except ValueError as exc:
        raise OpenAIProxyArtifactError("artifact_candidate_invalid") from exc
    hashes = compute_artifact_input_hashes(repo_root)
    status = evidence.get("status")
    expected_fields = set(_READY_EVIDENCE_FIELDS)
    if status == "PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED":
        expected_fields.update({"independent_review", "approval_candidate_sha256"})
    if (
        set(evidence) != expected_fields
        or candidate.proxy_source_sha256 != hashes.proxy_source_sha256
        or candidate.dockerfile_sha256 != hashes.dockerfile_sha256
        or candidate.responses_contract_sha256 != hashes.responses_contract_sha256
        or candidate.proxy_policy_sha256 != hashes.proxy_policy_sha256
        or candidate.launcher_source_sha256 != hashes.launcher_source_sha256
        or evidence.get("image_id") != candidate.image_id
        or evidence.get("image_name") != candidate.image_name
        or evidence.get("fresh_offline_build") is not True
        or evidence.get("pull_allowed") is not False
        or evidence.get("build_network") != "none"
        or evidence.get("no_cache") is not True
        or evidence.get("iidfile_verified") is not True
        or evidence.get("input_hashes_stable") is not True
        or evidence.get("base_rootfs_prefix_verified") is not True
        or evidence.get("image_identity_valid") is not True
        or evidence.get("image_history_clean") is not True
        or evidence.get("image_contents_valid") is not True
        or evidence.get("cleanup_complete") is not True
        or evidence.get("provider_attempt_count") != 0
        or evidence.get("ai_call_count") != 0
        or evidence.get("tickflow_request_count") != 0
        or evidence.get("public_network_request_count") != 0
        or status
        not in {
            "PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW",
            "PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED",
        }
        or evidence.get("evidence_schema_version") != 1
        or evidence.get("offline_build") is not True
        or (
            status == "PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED"
            and (
                evidence.get("independent_review") != "PASSED"
                or evidence.get("approval_candidate_sha256")
                != hashlib.sha256(candidate_raw).hexdigest()
            )
        )
    ):
        raise OpenAIProxyArtifactError("artifact_build_evidence_invalid")
    provenance = FreshBuildProvenance(
        fresh_offline_build=True,
        pull_allowed=False,
        build_network="none",
        no_cache=True,
        iidfile_verified=True,
        input_hashes_stable=True,
        base_rootfs_prefix_verified=True,
        image_id=candidate.image_id,
        base_image_id=base.local_config_id,
    )
    return candidate, provenance


def _verify_attested_artifact(repo_root: Path, *, base: Any) -> None:
    candidate, provenance = _load_attested_candidate(repo_root, base=base)
    _hashes, image = _image_evidence(
        repo_root,
        expected_image_id=candidate.image_id,
        base=base,
    )
    contents = verify_image_contents(
        repo_root,
        candidate.image_id,
        executor=_executor,
    )
    rebuilt = build_artifact_candidate(
        repo_root,
        image,
        contents,
        provenance,
    )
    if rebuilt != candidate:
        raise OpenAIProxyArtifactError("artifact_attestation_mismatch")


def _sanitized_output(status: str) -> None:
    print(
        json.dumps(
            {
                "status": status,
                "provider_attempt_count": 0,
                "ai_call_count": 0,
                "tickflow_request_count": 0,
                "public_network_request_count": 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-base", action="store_true")
    modes.add_argument("--attest", action="store_true")
    modes.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    root = _repo_root()
    try:
        base = _base_evidence()
        if args.check_base:
            _sanitized_output("PHASE2B_CANARY_BASE_IMAGE_LOCAL")
            return 0
        if args.verify:
            _verify_attested_artifact(root, base=base)
            _sanitized_output("PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW")
            return 0
        image, provenance = _fresh_offline_build(root, base=base)
        contents = verify_image_contents(
            root,
            image.image_id,
            executor=_executor,
        )
        candidate = build_artifact_candidate(
            root,
            image,
            contents,
            provenance,
        )
        write_artifact_reports(root, candidate, image, contents, provenance)
        _sanitized_output("PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW")
        return 0
    except OpenAIProxyArtifactError as exc:
        _sanitized_output(exc.code)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
