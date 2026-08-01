#!/usr/bin/env python3
"""Build and verify the dedicated OpenAI proxy without registry or runtime egress."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.services.phase2_openai_proxy_artifact import (
    BASE_IMAGE_REFERENCE,
    IMAGE_NAME,
    PHASE2B_CANARY_BUILD_INPUT_BLOCKED,
    OpenAIProxyArtifactError,
    build_artifact_candidate,
    build_offline_image_command,
    compute_artifact_input_hashes,
    inspect_openai_proxy_image,
    validate_local_base_image,
    verify_image_contents,
    write_artifact_reports,
)


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


def _base_evidence() -> None:
    result = _run(["docker", "image", "inspect", BASE_IMAGE_REFERENCE])
    if result.returncode != 0:
        raise OpenAIProxyArtifactError(PHASE2B_CANARY_BUILD_INPUT_BLOCKED)
    try:
        validate_local_base_image(
            _strict_json(result.stdout, "artifact_base_image_unavailable")
        )
    except OpenAIProxyArtifactError as exc:
        raise OpenAIProxyArtifactError(PHASE2B_CANARY_BUILD_INPUT_BLOCKED) from exc


def _image_evidence(repo_root: Path):
    hashes = compute_artifact_input_hashes(repo_root)
    inspect_result = _run(["docker", "image", "inspect", IMAGE_NAME])
    history_result = _run(
        ["docker", "history", "--no-trunc", "--format", "{{json .}}", IMAGE_NAME]
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
    return hashes, inspect_openai_proxy_image(inspect_payload, history, hashes)


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
    modes.add_argument("--build", action="store_true")
    modes.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    root = _repo_root()
    try:
        _base_evidence()
        if args.check_base:
            _sanitized_output("PHASE2B_CANARY_BASE_IMAGE_LOCAL")
            return 0
        if args.build:
            hashes = compute_artifact_input_hashes(root)
            result = _run(build_offline_image_command(root, hashes), timeout=300)
            if result.returncode != 0:
                raise OpenAIProxyArtifactError("artifact_offline_build_failed")
            _image_evidence(root)
            _sanitized_output("PHASE2B_PROXY_ARTIFACT_BUILT_OFFLINE")
            return 0
        _hashes, image = _image_evidence(root)
        contents = verify_image_contents(
            root,
            image.image_id,
            executor=_executor,
        )
        candidate = build_artifact_candidate(root, image, contents)
        write_artifact_reports(root, candidate, image, contents)
        _sanitized_output("PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW")
        return 0
    except OpenAIProxyArtifactError as exc:
        _sanitized_output(exc.code)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
