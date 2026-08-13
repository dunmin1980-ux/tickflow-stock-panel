"""Canonical immutable Ark Responses contract and Approval Candidate builder."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.providers.ark_capability_gate import validate_ark_capability_evidence
from app.providers.ark_provider import (
    ARK_ENDPOINT_ALIAS,
    ARK_EXACT_MODEL_ID,
    ARK_PROVIDER_ID,
    ARK_RESPONSES_CONTRACT_VERSION,
    build_ark_responses_request,
)
from app.providers.ark_proxy_policy import ArkProxyPolicy
from app.providers.base import ProviderRequest
from app.schemas.phase2_claims import WorkerClaimsCandidate
from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_ark_timeout_contract import (
    ark_runtime_contract_sha256,
    load_ark_runtime_contract,
)
from app.services.phase2_canary_orchestrator import (
    ApprovalScopedLedgerNamespace,
    ApprovalScopeIdentity,
    compute_approval_scope_id,
)
from app.services.phase2_claims_service import (
    PREDICATE_RULES,
    canonical_json_bytes,
)

_ARK_HISTORY_HASHES = {
    "reports/phase2_provider_ark/live_canary/evidence/2f17745f58534063bdd7eda1eb0d16f1.json": "35f957462c493331fe89c39f43420b3699a52346df4b8d3f6ff5beeebab1466e",
    "reports/phase2_provider_ark/live_canary/receipts/2f17745f58534063bdd7eda1eb0d16f1/archive-status.json": "a540da4a95be4081225750dffa93c643cafee09c613ec90f21e23f649ae29f2e",
    "reports/phase2_provider_ark/live_canary/receipts/2f17745f58534063bdd7eda1eb0d16f1/child-metadata.json": "e598dff711ff759e1fdd84576ce55af30b897891bd8225771077a0fab0e7f2db",
    "reports/phase2_provider_ark/live_canary/receipts/2f17745f58534063bdd7eda1eb0d16f1/proxy-receipt.json": "46c05c526067bed736262354bd492e809ca0c8294d7523376d806f050e8be9e4",
    "reports/phase2_provider_ark/live_canary/receipts/2f17745f58534063bdd7eda1eb0d16f1/relay-receipt.json": "e64a6de0dfa81e51e6ff50ea11fa516db6e20dbdce0f7d3ceb4c3e717579e9d9",
    "reports/phase2_provider_ark/live_canary/rejected/2f17745f58534063bdd7eda1eb0d16f1.json": "31aedcefc27797eebccbc18ad9665738559aa31484fe46f2ec511508305e2853",
    "reports/phase2_provider_canary/attempts/898079e765f188ba48b2de143ff5fce81e1191ec7bfc6cb773352b2bed90982d/2f17745f58534063bdd7eda1eb0d16f1/ledger.json": "5eeba9e7068c9225a1963883f7b814b87fe6198c53202502595a177f671bc8ed",
}
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_LAYER_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_BASE_IMAGE_DIGEST = (
    "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)
_BUILD_PROVENANCE_PATH = Path(
    "reports/phase2_provider_ark/ark_build_provenance.json"
)
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


def _exact_int(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


def _assert_no_symlink_below(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("ark_history_baseline_invalid") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise ValueError("ark_history_baseline_invalid") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("ark_history_baseline_invalid")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def ark_proxy_policy_sha256() -> str:
    return _sha256(canonical_json_bytes(ArkProxyPolicy().model_dump(mode="json")))


def _ark_source_bindings(root: Path) -> dict[str, str]:
    return {
        "base_image_digest": _BASE_IMAGE_DIGEST,
        "proxy_dockerfile_sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/Dockerfile"
        ),
        "proxy_source_sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/proxy.py"
        ),
        "relay_dockerfile_sha256": _file_sha256(
            root / "docker/phase2-ark-canary-relay/Dockerfile"
        ),
        "relay_source_sha256": _file_sha256(
            root / "docker/phase2-ark-canary-relay/relay.py"
        ),
    }


def _expected_ark_image_labels(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    source = _ark_source_bindings(root)
    proxy = {
        "org.tickflow.phase2.provider-id": ARK_PROVIDER_ID,
        "org.tickflow.phase2.base-image-digest": source["base_image_digest"],
        "org.tickflow.phase2.proxy-dockerfile-sha256": source[
            "proxy_dockerfile_sha256"
        ],
        "org.tickflow.phase2.proxy-source-sha256": source["proxy_source_sha256"],
        "org.tickflow.phase2.responses-contract-sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/responses-contract.json"
        ),
        "org.tickflow.phase2.proxy-policy-sha256": ark_proxy_policy_sha256(),
        "org.tickflow.phase2.runtime-contract-sha256": ark_runtime_contract_sha256(),
        "org.tickflow.phase2.readiness-contract-sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/readiness-contract.json"
        ),
    }
    relay = {
        "org.tickflow.phase2.provider-id": ARK_PROVIDER_ID,
        "org.tickflow.phase2.base-image-digest": source["base_image_digest"],
        "org.tickflow.phase2.relay-dockerfile-sha256": source[
            "relay_dockerfile_sha256"
        ],
        "org.tickflow.phase2.relay-source-sha256": source["relay_source_sha256"],
        "org.tickflow.phase2.runtime-contract-sha256": ark_runtime_contract_sha256(),
    }
    return proxy, relay


def _sanitize_ark_inspect(
    value: Mapping[str, Any],
    *,
    expected_labels: Mapping[str, str],
) -> dict[str, Any]:
    rootfs = value.get("RootFS")
    labels = value.get("Config", {}).get("Labels") if isinstance(value.get("Config"), Mapping) else None
    layers = rootfs.get("Layers") if isinstance(rootfs, Mapping) else None
    if (
        not isinstance(value.get("Id"), str)
        or _IMAGE_ID.fullmatch(value["Id"]) is None
        or value.get("Os") != "linux"
        or value.get("Architecture") not in {"amd64", "arm64"}
        or not isinstance(value.get("Created"), str)
        or not value["Created"]
        or not isinstance(layers, list)
        or not layers
        or any(
            not isinstance(layer, str) or _LAYER_ID.fullmatch(layer) is None
            for layer in layers
        )
        or not isinstance(labels, Mapping)
        or dict(labels) != dict(expected_labels)
        or any(labels.get(key) != expected for key, expected in expected_labels.items())
    ):
        raise ValueError("ark_build_provenance_invalid")
    return {
        "image_id": value["Id"],
        "os": value["Os"],
        "architecture": value["Architecture"],
        "created": value["Created"],
        "rootfs_layers": list(layers),
        "labels": dict(expected_labels),
    }


def build_ark_build_provenance(
    repo_root: Path,
    *,
    base_inspect: Mapping[str, Any],
    proxy_inspect: Mapping[str, Any],
    relay_inspect: Mapping[str, Any],
    captured_at: str,
    build_path: str,
) -> dict[str, Any]:
    root = repo_root.resolve(strict=True)
    proxy_labels, relay_labels = _expected_ark_image_labels(root)
    base_id = base_inspect.get("Id")
    base_rootfs = base_inspect.get("RootFS")
    base_layers = base_rootfs.get("Layers") if isinstance(base_rootfs, Mapping) else None
    if (
        base_id != _BASE_IMAGE_DIGEST
        or not isinstance(base_layers, list)
        or not base_layers
        or any(
            not isinstance(layer, str) or _LAYER_ID.fullmatch(layer) is None
            for layer in base_layers
        )
    ):
        raise ValueError("ark_build_provenance_invalid")
    proxy = _sanitize_ark_inspect(proxy_inspect, expected_labels=proxy_labels)
    relay = _sanitize_ark_inspect(relay_inspect, expected_labels=relay_labels)
    if (
        proxy["rootfs_layers"][: len(base_layers)] != base_layers
        or relay["rootfs_layers"][: len(base_layers)] != base_layers
        or len(proxy["rootfs_layers"]) <= len(base_layers)
        or len(relay["rootfs_layers"]) <= len(base_layers)
    ):
        raise ValueError("ark_build_provenance_invalid")
    validate_ark_image_set(
        repo_root=root,
        proxy_image_id=proxy["image_id"],
        relay_image_id=relay["image_id"],
        proxy_labels=proxy["labels"],
        relay_labels=relay["labels"],
    )
    if (
        not _is_utc_timestamp(captured_at)
        or not _is_utc_timestamp(proxy["created"])
        or not _is_utc_timestamp(relay["created"])
        or build_path not in {"READ_ONLY_EXISTING_IMAGE_INSPECT", "FRESH_OFFLINE_REBUILD"}
    ):
        raise ValueError("ark_build_provenance_invalid")
    source = _ark_source_bindings(root)
    value: dict[str, Any] = {
        "evidence_schema_version": 1,
        "captured_at": captured_at,
        "build_path": build_path,
        "docker_access_mode": "read_only_inspect",
        **source,
        "base_image_id": base_id,
        "base_rootfs_layers": list(base_layers),
        "runtime_contract_sha256": ark_runtime_contract_sha256(),
        "readiness_contract_sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/readiness-contract.json"
        ),
        "proxy_image_id": proxy["image_id"],
        "relay_image_id": relay["image_id"],
        "proxy_os": proxy["os"],
        "proxy_architecture": proxy["architecture"],
        "proxy_created": proxy["created"],
        "proxy_rootfs_layers": proxy["rootfs_layers"],
        "proxy_base_rootfs_prefix_verified": True,
        "proxy_labels": proxy["labels"],
        "relay_os": relay["os"],
        "relay_architecture": relay["architecture"],
        "relay_created": relay["created"],
        "relay_rootfs_layers": relay["rootfs_layers"],
        "relay_base_rootfs_prefix_verified": True,
        "relay_labels": relay["labels"],
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "provider_http": "NOT_RUN",
    }
    value["evidence_sha256"] = _sha256(canonical_json_bytes(value))
    return value


def validate_ark_build_provenance(
    repo_root: Path,
    value: Mapping[str, Any],
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("ark_build_provenance_invalid")
    root = repo_root.resolve(strict=True)
    expected_fields = {
        "evidence_schema_version",
        "captured_at",
        "build_path",
        "docker_access_mode",
        "base_image_digest",
        "base_image_id",
        "base_rootfs_layers",
        "proxy_dockerfile_sha256",
        "proxy_source_sha256",
        "relay_dockerfile_sha256",
        "relay_source_sha256",
        "runtime_contract_sha256",
        "readiness_contract_sha256",
        "proxy_image_id",
        "relay_image_id",
        "proxy_os",
        "proxy_architecture",
        "proxy_created",
        "proxy_rootfs_layers",
        "proxy_base_rootfs_prefix_verified",
        "proxy_labels",
        "relay_os",
        "relay_architecture",
        "relay_created",
        "relay_rootfs_layers",
        "relay_base_rootfs_prefix_verified",
        "relay_labels",
        "provider_attempt_count",
        "ai_call_count",
        "provider_http",
        "evidence_sha256",
    }
    payload = dict(value)
    evidence_sha256 = payload.pop("evidence_sha256", None)
    source = _ark_source_bindings(root)
    proxy_labels, relay_labels = _expected_ark_image_labels(root)
    if (
        set(value) != expected_fields
        or not _exact_int(value.get("evidence_schema_version"), 1)
        or value.get("build_path")
        not in {"READ_ONLY_EXISTING_IMAGE_INSPECT", "FRESH_OFFLINE_REBUILD"}
        or value.get("docker_access_mode") != "read_only_inspect"
        or any(value.get(key) != expected for key, expected in source.items())
        or value.get("base_image_id") != _BASE_IMAGE_DIGEST
        or not isinstance(value.get("base_rootfs_layers"), list)
        or not value["base_rootfs_layers"]
        or value.get("runtime_contract_sha256") != ark_runtime_contract_sha256()
        or value.get("readiness_contract_sha256")
        != _file_sha256(root / "docker/phase2-ark-egress-proxy/readiness-contract.json")
        or _IMAGE_ID.fullmatch(str(value.get("proxy_image_id", ""))) is None
        or _IMAGE_ID.fullmatch(str(value.get("relay_image_id", ""))) is None
        or value.get("proxy_os") != "linux"
        or value.get("relay_os") != "linux"
        or value.get("proxy_architecture") not in {"amd64", "arm64"}
        or value.get("relay_architecture") not in {"amd64", "arm64"}
        or not _is_utc_timestamp(value.get("captured_at"))
        or not _is_utc_timestamp(value.get("proxy_created"))
        or not _is_utc_timestamp(value.get("relay_created"))
        or value.get("proxy_labels") != proxy_labels
        or value.get("relay_labels") != relay_labels
        or not isinstance(value.get("proxy_rootfs_layers"), list)
        or not value["proxy_rootfs_layers"]
        or not isinstance(value.get("relay_rootfs_layers"), list)
        or not value["relay_rootfs_layers"]
        or value.get("proxy_base_rootfs_prefix_verified") is not True
        or value.get("relay_base_rootfs_prefix_verified") is not True
        or value["proxy_rootfs_layers"][: len(value["base_rootfs_layers"])]
        != value["base_rootfs_layers"]
        or value["relay_rootfs_layers"][: len(value["base_rootfs_layers"])]
        != value["base_rootfs_layers"]
        or len(value["proxy_rootfs_layers"]) <= len(value["base_rootfs_layers"])
        or len(value["relay_rootfs_layers"]) <= len(value["base_rootfs_layers"])
        or any(
            _LAYER_ID.fullmatch(str(layer)) is None
            for layer in value["base_rootfs_layers"]
        )
        or any(
            _LAYER_ID.fullmatch(str(layer)) is None
            for layer in value["proxy_rootfs_layers"]
        )
        or any(
            _LAYER_ID.fullmatch(str(layer)) is None
            for layer in value["relay_rootfs_layers"]
        )
        or not _exact_int(value.get("provider_attempt_count"), 0)
        or not _exact_int(value.get("ai_call_count"), 0)
        or value.get("provider_http") != "NOT_RUN"
        or not isinstance(evidence_sha256, str)
        or evidence_sha256 != _sha256(canonical_json_bytes(payload))
    ):
        raise ValueError("ark_build_provenance_invalid")


def load_ark_build_provenance(repo_root: Path) -> tuple[dict[str, Any], bytes]:
    root = repo_root.resolve(strict=True)
    path = root / _BUILD_PROVENANCE_PATH
    try:
        _assert_no_symlink_below(root, path)
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("ark_build_provenance_invalid") from exc
    if raw != canonical_json_bytes(value):
        raise ValueError("ark_build_provenance_invalid")
    validate_ark_build_provenance(root, value)
    return dict(value), raw


def validate_ark_timeout_mock_evidence(value: Any) -> None:
    expected_identity = {
        "endpoint_alias": ARK_ENDPOINT_ALIAS,
        "exact_model_id": ARK_EXACT_MODEL_ID,
        "provider_id": ARK_PROVIDER_ID,
    }
    runs = value.get("runs") if isinstance(value, Mapping) else None
    expected_runs = (
        ("fast_response", 1.0, "SUCCEEDED", True, "VALID", "inbox", None),
        ("about_60s_response", 60.0, "SUCCEEDED", True, "VALID", "inbox", None),
        ("before_180s_response", 179.0, "SUCCEEDED", True, "VALID", "inbox", None),
        (
            "at_180s_deadline",
            180.0,
            "PROVIDER_TIMEOUT",
            False,
            "NOT_RUN",
            "rejected",
            "WAITING_FOR_PROVIDER_RESPONSE_HEADERS",
        ),
    )
    top_level_fields = {
        "ark_timeout_mock_e2e_schema_version",
        "exact_model_id",
        "host_timeout_seconds",
        "maximum_provider_attempts",
        "provider_id",
        "provider_timeout_seconds",
        "public_network_used",
        "real_ai_call_count",
        "real_provider_attempt_count",
        "relay_timeout_seconds",
        "retry_count",
        "runs",
        "runtime_contract_sha256",
        "status",
    }
    run_fields = {
        "candidate_valid",
        "claims_valid",
        "failure_stage",
        "passed",
        "provider_attempt_count",
        "provider_http",
        "proxy_receipt_identity",
        "real_provider_attempt_count",
        "renderer_deterministic",
        "result",
        "retry_count",
        "scenario",
        "terminal_state",
        "virtual_elapsed_seconds",
    }
    result_fields = {
        "approval_scope_id",
        "container_residue_count",
        "host_validation_status",
        "network_residue_count",
        "route",
        "secret_file_residue_count",
        "temporary_file_residue_count",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != top_level_fields
        or not _exact_int(value.get("ark_timeout_mock_e2e_schema_version"), 1)
        or value.get("status") != "PASSED"
        or value.get("provider_id") != ARK_PROVIDER_ID
        or value.get("exact_model_id") != ARK_EXACT_MODEL_ID
        or not _exact_int(value.get("provider_timeout_seconds"), 180)
        or not _exact_int(value.get("relay_timeout_seconds"), 195)
        or not _exact_int(value.get("host_timeout_seconds"), 210)
        or not _exact_int(value.get("retry_count"), 0)
        or not _exact_int(value.get("maximum_provider_attempts"), 1)
        or not _exact_int(value.get("real_provider_attempt_count"), 0)
        or not _exact_int(value.get("real_ai_call_count"), 0)
        or value.get("public_network_used") is not False
        or value.get("runtime_contract_sha256") != ark_runtime_contract_sha256()
        or not isinstance(runs, list)
        or len(runs) != 4
    ):
        raise ValueError("ark_timeout_mock_evidence_invalid")
    for run, expected in zip(runs, expected_runs, strict=True):
        scenario, elapsed, terminal, succeeded, validation, route, failure = expected
        result = run.get("result") if isinstance(run, Mapping) else None
        expected_scope_id = compute_approval_scope_id(
            ApprovalScopeIdentity(
                approval_candidate_sha256=_sha256(scenario.encode("utf-8")),
                symbol="000403.SZ",
                provider_id=ARK_PROVIDER_ID,
                exact_model_id=ARK_EXACT_MODEL_ID,
                endpoint_alias=ARK_ENDPOINT_ALIAS,
            )
        )
        if (
            not isinstance(run, Mapping)
            or set(run) != run_fields
            or run.get("scenario") != scenario
            or isinstance(run.get("virtual_elapsed_seconds"), bool)
            or run.get("virtual_elapsed_seconds") != elapsed
            or run.get("terminal_state") != terminal
            or not _exact_int(run.get("provider_attempt_count"), 1)
            or not _exact_int(run.get("retry_count"), 0)
            or run.get("provider_http") != "MOCK_ONLY"
            or not _exact_int(run.get("real_provider_attempt_count"), 0)
            or run.get("proxy_receipt_identity") != expected_identity
            or run.get("candidate_valid") is not succeeded
            or run.get("claims_valid") is not succeeded
            or run.get("renderer_deterministic") is not succeeded
            or run.get("failure_stage") != failure
            or run.get("passed") is not True
            or not isinstance(result, Mapping)
            or set(result) != result_fields
            or result.get("approval_scope_id") != expected_scope_id
            or _HEX_64.fullmatch(str(result.get("approval_scope_id", ""))) is None
            or result.get("host_validation_status") != validation
            or result.get("route") != route
            or any(
                not _exact_int(result.get(field), 0)
                for field in (
                    "container_residue_count",
                    "network_residue_count",
                    "secret_file_residue_count",
                    "temporary_file_residue_count",
                )
            )
        ):
            raise ValueError("ark_timeout_mock_evidence_invalid")


def validate_ark_mock_e2e_evidence(repo_root: Path, value: Any) -> None:
    root = repo_root.resolve(strict=True)
    provenance, _ = load_ark_build_provenance(root)
    facts_path = root / "reports/phase2_facts/000403SZ_facts.json"
    facts_raw = facts_path.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_raw),
        _sha256(facts_raw),
        facts_bytes=facts_raw,
    )
    identity = {
        "provider_id": ARK_PROVIDER_ID,
        "endpoint_alias": ARK_ENDPOINT_ALIAS,
        "exact_model_id": ARK_EXACT_MODEL_ID,
    }
    top_fields = {
        "ark_mock_e2e_schema_version",
        "can_publish",
        "completed_run_count",
        "container_residue_count",
        "distinct_approval_scope_count",
        "endpoint_alias",
        "exact_model_id",
        "facts_sha256",
        "mock_provider_attempt_count",
        "network_residue_count",
        "orchestrator_source_sha256",
        "projection_sha256",
        "provider_id",
        "proxy_image_id",
        "real_ai_call_count",
        "real_provider_attempt_count",
        "real_public_network_success_count",
        "relay_image_id",
        "requested_run_count",
        "retry_count",
        "runs",
        "runtime_contract_sha256",
        "status",
        "temporary_residue_count",
    }
    run_fields = {
        "approval_candidate_sha256",
        "approval_scope_id",
        "can_publish",
        "candidate_valid",
        "claims_valid",
        "container_residue_count",
        "egress_network_internal",
        "endpoint_alias",
        "exact_model_id",
        "facts_sha256",
        "mock_request_contract_valid",
        "network_residue_count",
        "orchestrator_source_sha256",
        "passed",
        "projection_sha256",
        "provider_attempt_count",
        "provider_http_status",
        "provider_id",
        "proxy_ready",
        "proxy_receipt_identity",
        "relay_completed",
        "renderer_deterministic",
        "renderer_sha256",
        "request_id",
        "retry_count",
        "run",
        "runtime_contract_sha256",
        "seven_stages_complete",
        "temporary_residue_count",
    }
    runs = value.get("runs") if isinstance(value, Mapping) else None
    expected_orchestrator = _file_sha256(
        root / "backend/app/services/phase2_canary_orchestrator.py"
    )
    expected_runtime = ark_runtime_contract_sha256()
    if (
        not isinstance(value, Mapping)
        or set(value) != top_fields
        or not _exact_int(value.get("ark_mock_e2e_schema_version"), 1)
        or value.get("status") != "PASSED"
        or any(value.get(key) != expected for key, expected in identity.items())
        or value.get("proxy_image_id") != provenance["proxy_image_id"]
        or value.get("relay_image_id") != provenance["relay_image_id"]
        or value.get("orchestrator_source_sha256") != expected_orchestrator
        or value.get("runtime_contract_sha256") != expected_runtime
        or value.get("facts_sha256") != _sha256(facts_raw)
        or value.get("projection_sha256") != projection["projection_sha256"]
        or not _exact_int(value.get("requested_run_count"), 3)
        or not _exact_int(value.get("completed_run_count"), 3)
        or not _exact_int(value.get("distinct_approval_scope_count"), 3)
        or not _exact_int(value.get("mock_provider_attempt_count"), 3)
        or not _exact_int(value.get("retry_count"), 0)
        or not _exact_int(value.get("real_provider_attempt_count"), 0)
        or not _exact_int(value.get("real_ai_call_count"), 0)
        or not _exact_int(value.get("real_public_network_success_count"), 0)
        or not _exact_int(value.get("container_residue_count"), 0)
        or not _exact_int(value.get("network_residue_count"), 0)
        or not _exact_int(value.get("temporary_residue_count"), 0)
        or value.get("can_publish") is not False
        or not isinstance(runs, list)
        or len(runs) != 3
    ):
        raise ValueError("ark_mock_e2e_invalid")
    observed_scopes: set[str] = set()
    observed_renderer_hashes: set[str] = set()
    for index, run in enumerate(runs, start=1):
        candidate_sha = _sha256(f"ark-mock-scope-{index}".encode())
        expected_scope = compute_approval_scope_id(
            ApprovalScopeIdentity(
                approval_candidate_sha256=candidate_sha,
                symbol="000403.SZ",
                provider_id=ARK_PROVIDER_ID,
                exact_model_id=ARK_EXACT_MODEL_ID,
                endpoint_alias=ARK_ENDPOINT_ALIAS,
            )
        )
        if (
            not isinstance(run, Mapping)
            or set(run) != run_fields
            or not _exact_int(run.get("run"), index)
            or run.get("request_id") != ark_mock_request_id(index)
            or any(run.get(key) != expected for key, expected in identity.items())
            or run.get("approval_candidate_sha256") != candidate_sha
            or run.get("approval_scope_id") != expected_scope
            or run.get("facts_sha256") != _sha256(facts_raw)
            or run.get("projection_sha256") != projection["projection_sha256"]
            or run.get("orchestrator_source_sha256") != expected_orchestrator
            or run.get("runtime_contract_sha256") != expected_runtime
            or not _exact_int(run.get("provider_attempt_count"), 1)
            or not _exact_int(run.get("retry_count"), 0)
            or not _exact_int(run.get("provider_http_status"), 200)
            or run.get("proxy_receipt_identity") != identity
            or any(
                run.get(field) is not True
                for field in (
                    "proxy_ready",
                    "relay_completed",
                    "seven_stages_complete",
                    "candidate_valid",
                    "claims_valid",
                    "renderer_deterministic",
                    "mock_request_contract_valid",
                    "egress_network_internal",
                    "passed",
                )
            )
            or run.get("can_publish") is not False
            or any(
                not _exact_int(run.get(field), 0)
                for field in (
                    "container_residue_count",
                    "network_residue_count",
                    "temporary_residue_count",
                )
            )
            or _HEX_64.fullmatch(str(run.get("renderer_sha256", ""))) is None
        ):
            raise ValueError("ark_mock_e2e_invalid")
        observed_scopes.add(expected_scope)
        observed_renderer_hashes.add(str(run["renderer_sha256"]))
    if len(observed_scopes) != 3 or len(observed_renderer_hashes) != 1:
        raise ValueError("ark_mock_e2e_invalid")


def ark_mock_request_id(index: int) -> str:
    if index not in (1, 2, 3):
        raise ValueError("ark_mock_run_index_invalid")
    return _sha256(f"phase2-ark-mock-run:{index}".encode())[:32]


def validate_ark_history_baseline(repo_root: Path) -> dict[str, Any]:
    root = repo_root.resolve(strict=True)
    path = root / "reports/phase2_provider_ark/timeout_contract_history_baseline.json"
    _assert_no_symlink_below(root, path)
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("ark_history_baseline_invalid") from exc
    hashes = value.get("historical_hashes") if isinstance(value, dict) else None
    if (
        raw != canonical_json_bytes(value)
        or set(value)
        != {
            "attempt_status",
            "baseline_schema_version",
            "failure_stage",
            "historical_hashes",
            "old_approval_candidate_sha256",
            "request_id",
            "terminal_state",
        }
        or not _exact_int(value.get("baseline_schema_version"), 1)
        or value.get("request_id") != "2f17745f58534063bdd7eda1eb0d16f1"
        or value.get("terminal_state") != "FAILED_AFTER_DISPATCH"
        or value.get("attempt_status") != "CONSUMED"
        or value.get("failure_stage") != "WAITING_FOR_PROVIDER_RESPONSE_HEADERS"
        or value.get("old_approval_candidate_sha256")
        != "0fee5a1c509c77ae9b03107dfddf8caefa453673106cced13db4a4d6ac523ffc"
        or hashes != _ARK_HISTORY_HASHES
    ):
        raise ValueError("ark_history_baseline_invalid")
    historical_roots = (
        root / "reports/phase2_provider_ark/live_canary",
        root
        / "reports/phase2_provider_canary/attempts"
        / "898079e765f188ba48b2de143ff5fce81e1191ec7bfc6cb773352b2bed90982d",
    )
    for historical_root in historical_roots:
        _assert_no_symlink_below(root, historical_root)
    for relative, expected_sha256 in _ARK_HISTORY_HASHES.items():
        candidate = root / relative
        _assert_no_symlink_below(root, candidate)
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or _file_sha256(candidate) != expected_sha256
        ):
            raise ValueError("ark_history_baseline_invalid")
    historical_files = {
        path.relative_to(root).as_posix()
        for base in historical_roots
        for path in base.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if historical_files != set(_ARK_HISTORY_HASHES):
        raise ValueError("ark_history_baseline_invalid")
    return value


def _provider_request(repo_root: Path) -> ProviderRequest:
    facts_path = repo_root / "reports/phase2_facts/000403SZ_facts.json"
    facts_raw = facts_path.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_raw),
        _sha256(facts_raw),
        facts_bytes=facts_raw,
    )
    return ProviderRequest(
        provider_id=ARK_PROVIDER_ID,
        exact_model_id=ARK_EXACT_MODEL_ID,
        endpoint_alias=ARK_ENDPOINT_ALIAS,
        request_id="0" * 32,
        symbol="000403.SZ",
        projection_sha256=projection["projection_sha256"],
        facts_sha256=projection["facts_sha256"],
        claims_schema=WorkerClaimsCandidate.model_json_schema(mode="validation"),
        minimal_projection=projection,
    )


def build_ark_responses_contract(repo_root: Path) -> dict[str, Any]:
    root = repo_root.resolve(strict=True)
    capability = validate_ark_capability_evidence(root)
    request = build_ark_responses_request(_provider_request(root))
    from app.schemas.phase2_claims import ALLOWED_CLAIM_TYPES

    runtime = load_ark_runtime_contract()
    runtime_sha256 = ark_runtime_contract_sha256()
    value = {
        "contract_schema_version": 1,
        "ark_responses_contract_version": ARK_RESPONSES_CONTRACT_VERSION,
        "runtime_contract_sha256": runtime_sha256,
        "policy": {
            "provider_id": ARK_PROVIDER_ID,
            "model_id": ARK_EXACT_MODEL_ID,
            "endpoint_host": "ark.cn-beijing.volces.com",
            "endpoint_port": 443,
            "endpoint_path": "/api/v3/responses",
            "http_method": "POST",
            "tls_verification": True,
            "minimum_tls_version": "TLSv1_2",
            "follow_redirects": False,
            "stream": False,
            "store": False,
            "tools": [],
            "retry_count": 0,
            "maximum_attempts": 1,
            "connect_timeout_seconds": runtime.provider_connect_timeout_seconds,
            "read_timeout_seconds": runtime.provider_read_timeout_seconds,
            "total_timeout_seconds": runtime.provider_total_timeout_seconds,
            "maximum_bytes": 1_048_576,
            "approved_symbol": "000403.SZ",
        },
        "response_format": request["text"]["format"],
        "relay_contract": {
            "protocol_version": 1,
            "claims_schema_version": 1,
            "response_format": "typed_claims_json",
            "approved_symbol": "000403.SZ",
            "approved_trade_date": "2026-07-31",
            "allowed_claim_types": sorted(ALLOWED_CLAIM_TYPES),
            "allowed_predicates": sorted(PREDICATE_RULES),
            "generation": {
                "temperature": 0,
                "response_format": "typed_claims_json",
                "tool_use": False,
                "web_browsing": False,
                "file_tools": False,
                "function_calling": False,
                "streaming": False,
                "retry_count": 0,
                "maximum_output_bytes": 1_048_576,
                "timeout_seconds": runtime.relay_candidate_wait_timeout_seconds,
            },
        },
        "fixed_instructions": [
            "Use only supplied projection facts and pointers.",
            "Return exactly one schema-valid candidate object.",
            "Do not provide trading advice, predictions, news, or financial assertions.",
            "Do not emit markdown, prose, URLs, tool calls, or file references.",
        ],
        "capability_evidence_sha256": _sha256(
            canonical_json_bytes(capability.model_dump(mode="json"))
        ),
    }
    value["contract_sha256"] = _sha256(canonical_json_bytes(value))
    return value


def build_ark_approval_candidate(
    repo_root: Path,
    *,
    proxy_image_id: str,
    relay_image_id: str,
) -> dict[str, Any]:
    root = repo_root.resolve(strict=True)
    facts_path = root / "reports/phase2_facts/000403SZ_facts.json"
    facts_raw = facts_path.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_raw),
        _sha256(facts_raw),
        facts_bytes=facts_raw,
    )
    contract_raw = canonical_json_bytes(build_ark_responses_contract(root))
    schema_raw = canonical_json_bytes(WorkerClaimsCandidate.model_json_schema(mode="validation"))
    timeout_mock_path = root / "reports/phase2_provider_ark/timeout_mock_e2e.json"
    try:
        timeout_mock = json.loads(timeout_mock_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("ark_timeout_mock_evidence_invalid") from exc
    validate_ark_timeout_mock_evidence(timeout_mock)
    validate_ark_history_baseline(root)
    provenance, provenance_raw = load_ark_build_provenance(root)
    if (
        proxy_image_id != provenance["proxy_image_id"]
        or relay_image_id != provenance["relay_image_id"]
    ):
        raise ValueError("ark_build_provenance_invalid")
    mock_path = root / "reports/phase2_provider_ark/mock_e2e.json"
    try:
        mock_e2e = json.loads(mock_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("ark_mock_e2e_invalid") from exc
    validate_ark_mock_e2e_evidence(root, mock_e2e)
    history_baseline_path = (
        root / "reports/phase2_provider_ark/timeout_contract_history_baseline.json"
    )
    hashes = {
        "ark_responses_contract_sha256": _sha256(contract_raw),
        "ark_proxy_policy_sha256": ark_proxy_policy_sha256(),
        "ark_adapter_source_sha256": _file_sha256(root / "backend/app/providers/ark_provider.py"),
        "ark_launcher_source_sha256": _file_sha256(
            root / "backend/scripts/run_phase2_ark_single_symbol_canary.py"
        ),
        "orchestrator_source_sha256": _file_sha256(
            root / "backend/app/services/phase2_canary_orchestrator.py"
        ),
        "runtime_contract_sha256": ark_runtime_contract_sha256(),
        "readiness_contract_sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/readiness-contract.json"
        ),
        "facts_sha256": _sha256(facts_raw),
        "history_baseline_sha256": _file_sha256(history_baseline_path),
        "projection_sha256": projection["projection_sha256"],
        "typed_claims_schema_sha256": _sha256(schema_raw),
        "timeout_mock_e2e_sha256": _file_sha256(
            timeout_mock_path
        ),
        "mock_e2e_sha256": _file_sha256(
            mock_path
        ),
        "build_provenance_sha256": _sha256(provenance_raw),
    }
    candidate_without_hash: dict[str, Any] = {
        "ark_approval_candidate_schema_version": 1,
        "status": "PHASE2B_ARK_TIMEOUT_CONTRACT_READY_FOR_REAPPROVAL",
        "provider_id": ARK_PROVIDER_ID,
        "exact_model_id": ARK_EXACT_MODEL_ID,
        "endpoint_alias": ARK_ENDPOINT_ALIAS,
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3/responses",
        "keychain_service": "tickflow-phase2-canary-volcengine-ark",
        "symbol": "000403.SZ",
        "trade_date": "2026-07-31",
        "artifact_hashes": hashes,
        "source_bindings": _ark_source_bindings(root),
        "proxy_image_id": proxy_image_id,
        "relay_image_id": relay_image_id,
        "approval_installed": False,
        "provider_http": "NOT_RUN",
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "retry_count": 0,
        "maximum_provider_attempts": 1,
        "can_publish": False,
        "openai_provider": "FROZEN",
        "old_deepseek_model": "PHASE2B_ARK_STRUCTURED_OUTPUT_BLOCKED_PRESERVED",
    }
    return candidate_without_hash


def build_ark_approval_scope_evidence(
    candidate_bytes: bytes,
    *,
    repo_root: Path,
    mock_e2e_sha256: str,
) -> dict[str, Any]:
    """Bind the independent Ark ledger namespace to final candidate bytes."""
    if (
        not isinstance(mock_e2e_sha256, str)
        or len(mock_e2e_sha256) != 64
        or any(character not in "0123456789abcdef" for character in mock_e2e_sha256)
    ):
        raise ValueError("ark_mock_e2e_sha256_invalid")
    try:
        candidate_value = json.loads(candidate_bytes)
        if canonical_json_bytes(candidate_value) != candidate_bytes:
            raise ValueError
        validate_ark_approval_candidate(repo_root, candidate_value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("ark_approval_scope_candidate_invalid") from exc
    root = repo_root.resolve(strict=True)
    mock_path = root / "reports/phase2_provider_ark/mock_e2e.json"
    try:
        _assert_no_symlink_below(root, mock_path)
        mock_raw = mock_path.read_bytes()
        mock_value = json.loads(mock_raw)
        if _sha256(mock_raw) != mock_e2e_sha256:
            raise ValueError("ark_mock_e2e_sha256_mismatch")
        validate_ark_mock_e2e_evidence(root, mock_value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("ark_mock_e2e_sha256_mismatch") from exc
    candidate_sha256 = _sha256(candidate_bytes)
    scope = ApprovalScopeIdentity(
        approval_candidate_sha256=candidate_sha256,
        symbol="000403.SZ",
        provider_id=ARK_PROVIDER_ID,
        exact_model_id=ARK_EXACT_MODEL_ID,
        endpoint_alias=ARK_ENDPOINT_ALIAS,
    )
    attempts_root = root / "reports/phase2_provider_canary/attempts"
    legacy_state_root = (
        Path.home() / "Library/Application Support/TickFlowPhase2Canary/runtime-v1"
    )
    historical_root = root / "reports/phase2_provider_canary/live_canary"
    candidate_roots = (
        root / "reports/phase2_provider_canary/superseded",
        root / "reports/phase2_provider_canary",
        root / "reports/phase2_provider_ark",
    )
    preflight = ApprovalScopedLedgerNamespace(
        repo_root=root,
        attempts_root=attempts_root,
        legacy_state_root=legacy_state_root,
        historical_evidence_root=historical_root,
        candidate_roots=candidate_roots,
        additional_historical_evidence_roots=(
            root / "reports/phase2_provider_ark/live_canary",
        ),
    ).preflight(scope)
    if preflight.status != "READY":
        raise ValueError("ark_approval_scope_preflight_blocked")
    historical_attempts = preflight.current_scope_provider_attempts
    attempt_availability = preflight.current_scope_attempt_availability
    global_request_id_count = len(preflight.global_request_ids)
    value = {
        "ark_approval_scope_schema_version": 1,
        "approval_candidate_sha256": candidate_sha256,
        "provider_id": ARK_PROVIDER_ID,
        "exact_model_id": ARK_EXACT_MODEL_ID,
        "endpoint_alias": ARK_ENDPOINT_ALIAS,
        "symbol": "000403.SZ",
        "ledger_namespace_version": 1,
        "approval_scope_id": compute_approval_scope_id(scope),
        "historical_attempts": historical_attempts,
        "attempt_availability": attempt_availability,
        "global_request_id_count": global_request_id_count,
        "ledger_preflight_status": "READY",
        "approval_installed": False,
    }
    value["mock_e2e_sha256"] = mock_e2e_sha256
    return value


def validate_ark_image_set(
    *,
    repo_root: Path,
    proxy_image_id: str,
    relay_image_id: str,
    proxy_labels: Mapping[str, str],
    relay_labels: Mapping[str, str],
) -> None:
    root = repo_root.resolve(strict=True)
    expected_proxy_labels, expected_relay_labels = _expected_ark_image_labels(root)
    if (
        _IMAGE_ID.fullmatch(proxy_image_id) is None
        or _IMAGE_ID.fullmatch(relay_image_id) is None
        or dict(proxy_labels) != expected_proxy_labels
        or dict(relay_labels) != expected_relay_labels
        or any(proxy_labels.get(key) != value for key, value in expected_proxy_labels.items())
        or any(relay_labels.get(key) != value for key, value in expected_relay_labels.items())
    ):
        raise ValueError("ark_image_set_invalid")


def validate_ark_approval_candidate(
    repo_root: Path,
    value: Mapping[str, Any],
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("ark_approval_candidate_invalid")
    try:
        expected = build_ark_approval_candidate(
            repo_root,
            proxy_image_id=str(value.get("proxy_image_id")),
            relay_image_id=str(value.get("relay_image_id")),
        )
    except ValueError as exc:
        raise ValueError("ark_approval_candidate_invalid") from exc
    try:
        value_raw = canonical_json_bytes(dict(value))
        expected_raw = canonical_json_bytes(expected)
    except (TypeError, ValueError) as exc:
        raise ValueError("ark_approval_candidate_invalid") from exc
    if value_raw != expected_raw:
        raise ValueError("ark_approval_candidate_invalid")


@dataclass(frozen=True)
class ApprovedArkCandidateSnapshot:
    candidate: dict[str, Any]
    candidate_raw: bytes
    candidate_sha256: str


def _read_regular_snapshot(
    path: Path,
    *,
    maximum_bytes: int = 262_144,
    required_mode: int | None = None,
    required_uid: int | None = None,
    required_parent_mode: int | None = None,
    required_parent_uid: int | None = None,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise ValueError("ark_approval_invalid") from exc
    try:
        directory_metadata = os.fstat(directory)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or (
                required_parent_mode is not None
                and stat.S_IMODE(directory_metadata.st_mode) != required_parent_mode
            )
            or (
                required_parent_uid is not None and directory_metadata.st_uid != required_parent_uid
            )
        ):
            raise ValueError("ark_approval_invalid")
        try:
            descriptor = os.open(path.name, flags, dir_fd=directory)
        except OSError as exc:
            raise ValueError("ark_approval_invalid") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (required_mode is not None and stat.S_IMODE(metadata.st_mode) != required_mode)
                or (required_uid is not None and metadata.st_uid != required_uid)
            ):
                raise ValueError("ark_approval_invalid")
            raw = b""
            while len(raw) <= maximum_bytes:
                chunk = os.read(descriptor, min(65_536, maximum_bytes + 1 - len(raw)))
                if not chunk:
                    break
                raw += chunk
            if len(raw) > maximum_bytes:
                raise ValueError("ark_approval_invalid")
            return raw
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def load_installed_ark_approval(
    candidate_path: Path,
    approval_path: Path,
) -> ApprovedArkCandidateSnapshot:
    """Load a future Ark approval without accepting or installing one here."""
    try:
        candidate_path.lstat()
        approval_directory = approval_path.parent.lstat()
        approval_metadata = approval_path.lstat()
    except OSError as exc:
        raise ValueError("ark_approval_not_installed") from exc
    if (
        candidate_path.is_symlink()
        or approval_path.is_symlink()
        or not candidate_path.is_file()
        or not approval_path.is_file()
        or not approval_path.parent.is_dir()
        or (approval_directory.st_mode & 0o777) != 0o700
        or (approval_metadata.st_mode & 0o777) != 0o600
        or approval_directory.st_uid != os.geteuid()
        or approval_metadata.st_uid != os.geteuid()
    ):
        raise ValueError("ark_approval_invalid")
    candidate_raw = _read_regular_snapshot(candidate_path)
    approval_raw = _read_regular_snapshot(
        approval_path,
        required_mode=0o600,
        required_uid=os.geteuid(),
        required_parent_mode=0o700,
        required_parent_uid=os.geteuid(),
    )
    try:
        candidate = json.loads(candidate_raw)
        approval = json.loads(approval_raw)
    except json.JSONDecodeError as exc:
        raise ValueError("ark_approval_invalid") from exc
    if (
        canonical_json_bytes(candidate) != candidate_raw
        or canonical_json_bytes(approval) != approval_raw
        or set(approval)
        != {
            "ark_runtime_approval_schema_version",
            "approved_candidate_sha256",
            "approval_scope",
            "provider_id",
            "exact_model_id",
            "endpoint_alias",
            "symbol",
            "maximum_provider_attempts",
            "retry_count",
        }
        or not _exact_int(approval.get("ark_runtime_approval_schema_version"), 1)
        or approval.get("approval_scope") != "single_symbol_ark_canary_runtime_v1"
        or approval.get("provider_id") != ARK_PROVIDER_ID
        or approval.get("exact_model_id") != ARK_EXACT_MODEL_ID
        or approval.get("endpoint_alias") != ARK_ENDPOINT_ALIAS
        or approval.get("symbol") != "000403.SZ"
        or not _exact_int(approval.get("maximum_provider_attempts"), 1)
        or not _exact_int(approval.get("retry_count"), 0)
        or approval.get("approved_candidate_sha256") != _sha256(candidate_raw)
    ):
        raise ValueError("ark_approval_invalid")
    validate_ark_approval_candidate(candidate_path.parents[2], candidate)
    return ApprovedArkCandidateSnapshot(
        candidate=dict(candidate),
        candidate_raw=candidate_raw,
        candidate_sha256=_sha256(candidate_raw),
    )


def build_ark_approval_candidate_bytes(
    repo_root: Path,
    *,
    proxy_image_id: str,
    relay_image_id: str,
) -> bytes:
    return canonical_json_bytes(
        build_ark_approval_candidate(
            repo_root,
            proxy_image_id=proxy_image_id,
            relay_image_id=relay_image_id,
        )
    )
