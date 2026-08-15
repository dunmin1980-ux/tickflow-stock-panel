#!/usr/bin/env python3
"""Validate pre/post-start proxy attachments without releasing the network gate."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.run_phase2_ark_proxy_tls_probe import (
    atomic_write_json,
    network_connect_command,
    network_create_commands,
    proxy_create_command,
    validate_post_start_proxy_attachments,
    validate_pre_start_proxy_attachments,
    validate_production_networks,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = (
    REPO_ROOT
    / "reports/phase2_provider_ark/proxy_tls_probe/"
    "network_attachment_harness.json"
)
PROVENANCE_PATH = OUTPUT_PATH.parent / "build_provenance.json"
ATTACHMENT_CONTRACT_PATH = (
    REPO_ROOT
    / "docker/phase2-ark-proxy-tls-probe/contracts/"
    "proxy-network-attachment-contract.json"
)
PREFIX = "phase2-proxy-attachment-harness-"


def _run(
    command: Sequence[str],
    *,
    check: bool = True,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=check,
        text=True,
        timeout=timeout,
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_names(suffix: str) -> dict[str, str]:
    return {
        "relay_network": f"{PREFIX}relay-{suffix}",
        "egress_network": f"{PREFIX}egress-{suffix}",
        "proxy": f"{PREFIX}proxy-{suffix}",
    }


def _inspect_container(names: Mapping[str, str]) -> list[dict[str, Any]]:
    result = _run(["docker", "container", "inspect", names["proxy"]])
    value = json.loads(result.stdout)
    if not isinstance(value, list):
        raise ValueError("attachment_harness_inspect_invalid")
    return value


def _network_metadata(names: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for role in ("relay_network", "egress_network"):
        result = _run(["docker", "network", "inspect", names[role]])
        value = json.loads(result.stdout)
        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            raise ValueError("attachment_harness_inspect_invalid")
        values[names[role]] = value[0]
    return values


def _empty_endpoint_metadata(payload: list[dict[str, Any]]) -> bool:
    try:
        networks = payload[0]["NetworkSettings"]["Networks"]
    except (IndexError, KeyError, TypeError) as error:
        raise ValueError("attachment_harness_inspect_invalid") from error
    return isinstance(networks, dict) and all(
        isinstance(value, dict)
        and value.get("NetworkID") == ""
        and value.get("EndpointID") == ""
        for value in networks.values()
    )


def _cleanup(names: Mapping[str, str]) -> None:
    _run(["docker", "container", "rm", "--force", names["proxy"]], check=False)
    for role in ("egress_network", "relay_network"):
        _run(["docker", "network", "rm", names[role]], check=False)


def _residue_counts() -> tuple[int, int]:
    containers = _run(
        ["docker", "container", "ls", "--all", "--format", "{{.Names}}"]
    ).stdout.splitlines()
    networks = _run(
        ["docker", "network", "ls", "--format", "{{.Name}}"]
    ).stdout.splitlines()
    return (
        sum(name.startswith(PREFIX) for name in containers),
        sum(name.startswith(PREFIX) for name in networks),
    )


def run_case(*, run_number: int, image_id: str, root: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    probe_id = uuid.uuid4().hex
    names = _runtime_names(suffix)
    output_dir = root / f"output-{run_number}-{suffix}"
    output_dir.mkdir(mode=0o733)
    try:
        for command in network_create_commands(names, mock_mode=False):
            _run(command)
        _run(
            proxy_create_command(
                names=names,
                image_id=image_id,
                output_dir=output_dir,
                probe_id=probe_id,
            )
        )
        _run(network_connect_command(names))
        pre_start = _inspect_container(names)
        validate_pre_start_proxy_attachments(pre_start, names)
        pre_start_networks = _network_metadata(names)
        validate_production_networks(pre_start_networks, names)
        empty_metadata = _empty_endpoint_metadata(pre_start)
        _run(["docker", "container", "start", names["proxy"]])
        post_start = _inspect_container(names)
        post_start_networks = _network_metadata(names)
        validate_production_networks(post_start_networks, names)
        validate_post_start_proxy_attachments(post_start, names, post_start_networks)
        if (output_dir / "dispatch.ready").exists():
            raise ValueError("attachment_harness_dispatch_gate_released")
        return {
            "post_start_validation": "PASSED",
            "pre_start_empty_endpoint_metadata": empty_metadata,
            "pre_start_validation": "PASSED",
            "probe_attempt_count": 0,
            "run": run_number,
        }
    finally:
        _cleanup(names)


def validate_report(
    report: Mapping[str, Any],
    *,
    proxy_image_id: str | None = None,
) -> None:
    expected_fields = {
        "ai_call_count",
        "attachment_contract_sha256",
        "authorization_constructed",
        "container_residue",
        "happy_path_runs",
        "historical_attempts",
        "http_request_sent",
        "network_residue",
        "network_attachment_harness_schema_version",
        "post_start_validation",
        "pre_start_empty_endpoint_metadata_observed",
        "pre_start_validation",
        "probe_attempt_count",
        "provider_attempt_count",
        "proxy_image_id",
        "real_public_network_success_count",
        "results",
        "secret_content_read",
        "status",
        "temporary_residue",
    }
    results = report.get("results")
    valid = (
        set(report) == expected_fields
        and report.get("network_attachment_harness_schema_version") == 1
        and report.get("status") == "PROXY_NETWORK_ATTACHMENT_HARNESS_PASSED"
        and report.get("happy_path_runs") == 3
        and report.get("pre_start_validation") == "PASSED"
        and report.get("post_start_validation") == "PASSED"
        and report.get("pre_start_empty_endpoint_metadata_observed") is True
        and report.get("historical_attempts") == 0
        and report.get("probe_attempt_count") == 0
        and report.get("provider_attempt_count") == 0
        and report.get("ai_call_count") == 0
        and report.get("secret_content_read") is False
        and report.get("authorization_constructed") is False
        and report.get("http_request_sent") is False
        and report.get("real_public_network_success_count") == 0
        and report.get("container_residue") == 0
        and report.get("network_residue") == 0
        and report.get("temporary_residue") == 0
        and isinstance(report.get("attachment_contract_sha256"), str)
        and len(report["attachment_contract_sha256"]) == 64
        and isinstance(report.get("proxy_image_id"), str)
        and report["proxy_image_id"].startswith("sha256:")
        and (proxy_image_id is None or report["proxy_image_id"] == proxy_image_id)
        and isinstance(results, list)
        and len(results) == 3
    )
    if not valid:
        raise ValueError("attachment_harness_report_invalid")
    for index, result in enumerate(results, start=1):
        if not isinstance(result, Mapping) or dict(result) != {
            "post_start_validation": "PASSED",
            "pre_start_empty_endpoint_metadata": True,
            "pre_start_validation": "PASSED",
            "probe_attempt_count": 0,
            "run": index,
        }:
            raise ValueError("attachment_harness_report_invalid")


def build_report(*, image_id: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=PREFIX) as name:
        root = Path(name)
        results = [
            run_case(run_number=index, image_id=image_id, root=root)
            for index in range(1, 4)
        ]
    container_residue, network_residue = _residue_counts()
    report = {
        "ai_call_count": 0,
        "attachment_contract_sha256": _sha256_file(ATTACHMENT_CONTRACT_PATH),
        "authorization_constructed": False,
        "container_residue": container_residue,
        "happy_path_runs": 3,
        "historical_attempts": 0,
        "http_request_sent": False,
        "network_residue": network_residue,
        "network_attachment_harness_schema_version": 1,
        "post_start_validation": "PASSED",
        "pre_start_empty_endpoint_metadata_observed": all(
            result["pre_start_empty_endpoint_metadata"] is True for result in results
        ),
        "pre_start_validation": "PASSED",
        "probe_attempt_count": 0,
        "provider_attempt_count": 0,
        "proxy_image_id": image_id,
        "real_public_network_success_count": 0,
        "results": results,
        "secret_content_read": False,
        "status": "PROXY_NETWORK_ATTACHMENT_HARNESS_PASSED",
        "temporary_residue": 0,
    }
    validate_report(report, proxy_image_id=image_id)
    return report


def main() -> int:
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    image_id = provenance.get("image_identity", {}).get("image_id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise ValueError("attachment_harness_image_invalid")
    report = build_report(image_id=image_id)
    atomic_write_json(OUTPUT_PATH, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
