"""Canonical immutable Ark Responses contract and Approval Candidate builder."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
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
from app.services.phase2_canary_orchestrator import (
    ApprovalScopedLedgerNamespace,
    ApprovalScopeIdentity,
    compute_approval_scope_id,
)
from app.services.phase2_canary_runtime_contract import runtime_contract_sha256
from app.services.phase2_claims_service import (
    PREDICATE_RULES,
    canonical_json_bytes,
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def ark_proxy_policy_sha256() -> str:
    return _sha256(canonical_json_bytes(ArkProxyPolicy().model_dump(mode="json")))


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

    runtime_sha256 = runtime_contract_sha256()
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
            "connect_timeout_seconds": 10,
            "read_timeout_seconds": 60,
            "total_timeout_seconds": 60,
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
                "timeout_seconds": 75,
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
        "runtime_contract_sha256": runtime_contract_sha256(),
        "readiness_contract_sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/readiness-contract.json"
        ),
        "facts_sha256": _sha256(facts_raw),
        "projection_sha256": projection["projection_sha256"],
        "typed_claims_schema_sha256": _sha256(schema_raw),
    }
    candidate_without_hash: dict[str, Any] = {
        "ark_approval_candidate_schema_version": 1,
        "status": "PHASE2B_ARK_PROVIDER_READY_FOR_REAPPROVAL",
        "provider_id": ARK_PROVIDER_ID,
        "exact_model_id": ARK_EXACT_MODEL_ID,
        "endpoint_alias": ARK_ENDPOINT_ALIAS,
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3/responses",
        "keychain_service": "tickflow-phase2-canary-volcengine-ark",
        "symbol": "000403.SZ",
        "trade_date": "2026-07-31",
        "artifact_hashes": hashes,
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
    mock_e2e_sha256: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Bind the independent Ark ledger namespace to final candidate bytes."""
    if (
        not isinstance(mock_e2e_sha256, str)
        or len(mock_e2e_sha256) != 64
        or any(character not in "0123456789abcdef" for character in mock_e2e_sha256)
    ):
        raise ValueError("ark_mock_e2e_sha256_invalid")
    candidate_sha256 = _sha256(candidate_bytes)
    scope = ApprovalScopeIdentity(
        approval_candidate_sha256=candidate_sha256,
        symbol="000403.SZ",
        provider_id=ARK_PROVIDER_ID,
        exact_model_id=ARK_EXACT_MODEL_ID,
        endpoint_alias=ARK_ENDPOINT_ALIAS,
    )
    historical_attempts = 0
    attempt_availability = "AVAILABLE"
    global_request_id_count = 0
    if repo_root is not None:
        root = repo_root.resolve(strict=True)
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
    expected_proxy_labels = {
        "org.tickflow.phase2.provider-id": ARK_PROVIDER_ID,
        "org.tickflow.phase2.proxy-source-sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/proxy.py"
        ),
        "org.tickflow.phase2.responses-contract-sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/responses-contract.json"
        ),
        "org.tickflow.phase2.proxy-policy-sha256": ark_proxy_policy_sha256(),
        "org.tickflow.phase2.runtime-contract-sha256": runtime_contract_sha256(),
        "org.tickflow.phase2.readiness-contract-sha256": _file_sha256(
            root / "docker/phase2-ark-egress-proxy/readiness-contract.json"
        ),
    }
    expected_relay_labels = {
        "org.tickflow.phase2.relay-source-sha256": _file_sha256(
            root / "docker/phase2-canary-relay/relay.py"
        ),
        "org.tickflow.phase2.runtime-contract-sha256": runtime_contract_sha256(),
    }
    if (
        not proxy_image_id.startswith("sha256:")
        or not relay_image_id.startswith("sha256:")
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
    expected = build_ark_approval_candidate(
        repo_root,
        proxy_image_id=str(value.get("proxy_image_id")),
        relay_image_id=str(value.get("relay_image_id")),
    )
    if dict(value) != expected:
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
        or approval.get("ark_runtime_approval_schema_version") != 1
        or approval.get("approval_scope") != "single_symbol_ark_canary_runtime_v1"
        or approval.get("provider_id") != ARK_PROVIDER_ID
        or approval.get("exact_model_id") != ARK_EXACT_MODEL_ID
        or approval.get("endpoint_alias") != ARK_ENDPOINT_ALIAS
        or approval.get("symbol") != "000403.SZ"
        or approval.get("maximum_provider_attempts") != 1
        or approval.get("retry_count") != 0
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
