"""Validate published Phase 2B Provider Relay evidence without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.services.phase2_provider_relay_runner import (
    PHASE2B_PROVIDER_RELAY_E2E_BLOCKED,
    PHASE2B_PROVIDER_RELAY_READY,
    ProviderRelayResult,
)

_SENSITIVE = re.compile(
    rb"Authorization:|Bearer |api[_-]?key\s*[:=]|Cookie:|Session:|"
    rb"BEGIN [A-Z ]*PRIVATE KEY"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_REQUIRED_AUDIT_FIELDS = {
    "request_id",
    "symbol",
    "projection_sha256",
    "facts_sha256",
    "relay_image_digest",
    "proxy_image_digest",
    "mock_provider_image_digest",
    "relay_contract_hash",
    "proxy_policy_hash",
    "started_at",
    "completed_at",
    "provider_http_status",
    "provider_attempt_count",
    "retry_count",
    "response_size",
    "response_sha256",
    "candidate_sha256",
    "claims_validation_status",
    "renderer_status",
    "route",
    "cleanup_status",
}
_INVALID_SCENARIOS = {
    "markdown_instead_of_json",
    "extra_free_text_field",
    "unknown_claim_type",
    "unknown_predicate",
    "wrong_symbol",
    "wrong_projection_sha",
    "raw_qfq_mismatch",
    "unsupported_fact_pointer",
    "trading_claim",
    "oversized_response",
    "timeout",
    "http_429",
    "http_500",
    "invalid_json",
    "multiple_json_documents",
    "empty_response",
    "wrong_request_id",
    "wrong_facts_sha",
    "sensitive_shape",
    "redirect_response",
    "external_url_response",
}
_EXTERNAL_ACTIONS = {
    "tickflow_api_request_count": 0,
    "real_ai_call_count": 0,
    "real_provider_attempt_count": 0,
    "real_public_network_success_count": 0,
    "cloud_mutation_count": 0,
    "obsidian_real_vault_write": False,
    "external_send_count": 0,
    "integrated_gold_enabled": False,
    "paper_trading_started": False,
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    return parser.parse_args(argv)


def _object(path: Path, errors: list[str], code: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append(code)
        return {}
    if not isinstance(value, dict):
        errors.append(code)
        return {}
    return value


def validate_provider_relay_artifacts(artifact_root: Path) -> ProviderRelayResult:
    errors: list[str] = []
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        return ProviderRelayResult(
            status=PHASE2B_PROVIDER_RELAY_E2E_BLOCKED,
            errors=["artifact_root_invalid"],
            cleanup_complete=False,
            evidence={},
        )
    root = artifact_root.resolve(strict=True)
    for item in root.rglob("*"):
        if item.is_symlink():
            errors.append("artifact_symlink_forbidden")
        elif item.is_file():
            try:
                raw = item.read_bytes()
            except OSError:
                errors.append("artifact_read_failed")
                continue
            if _SENSITIVE.search(raw):
                errors.append("sensitive_shape_detected")

    evidence_path = root / "runtime_evidence.json"
    evidence = _object(evidence_path, errors, "runtime_evidence_invalid")
    if evidence.get("status") != PHASE2B_PROVIDER_RELAY_READY:
        errors.append("runtime_status_invalid")
    if evidence.get("errors") != []:
        errors.append("runtime_errors_present")
    if evidence.get("external_actions") != _EXTERNAL_ACTIONS:
        errors.append("external_actions_invalid")
    matrix = evidence.get("mock_matrix")
    if not isinstance(matrix, dict):
        errors.append("mock_matrix_invalid")
        matrix = {}
    if matrix.get("valid_run_count") != 2:
        errors.append("valid_run_count_invalid")
    if matrix.get("invalid_rejected_count") != 21:
        errors.append("invalid_rejected_count_invalid")
    if matrix.get("invalid_accepted_count") != 0:
        errors.append("invalid_accepted_count_invalid")
    if matrix.get("provider_attempt_count") != 24 or matrix.get("retry_count") != 0:
        errors.append("attempt_contract_invalid")
    scenarios = matrix.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 24:
        errors.append("scenario_matrix_invalid")
    else:
        invalid = {
            item.get("scenario")
            for item in scenarios
            if isinstance(item, dict) and item.get("expected_rejection") is True
        }
        if invalid != _INVALID_SCENARIOS:
            errors.append("scenario_set_invalid")
        if sum(
            isinstance(item, dict)
            and item.get("scenario") == "valid_typed_candidate"
            and item.get("mode") == "run"
            for item in scenarios
        ) != 2:
            errors.append("valid_scenario_repeat_invalid")
        if sum(
            isinstance(item, dict) and item.get("mode") == "probe"
            for item in scenarios
        ) != 1:
            errors.append("probe_run_count_invalid")

    inbox = root / "mock_preview/inbox"
    rejected = root / "mock_preview/rejected"
    receipts = root / "mock_preview/receipts"
    inbox_files = sorted(inbox.glob("*.md")) if inbox.is_dir() else []
    rejected_files = sorted(rejected.glob("*.json")) if rejected.is_dir() else []
    receipt_files = sorted(receipts.glob("*.json")) if receipts.is_dir() else []
    if len(inbox_files) != 1:
        errors.append("inbox_file_count_invalid")
    if len(rejected_files) != 21:
        errors.append("rejected_file_count_invalid")
    if len(receipt_files) != 24:
        errors.append("receipt_file_count_invalid")
    rejected_scenarios: set[str] = set()
    for path in rejected_files:
        value = _object(path, errors, "rejected_record_invalid")
        if value.get("status") != "REJECTED" or value.get("route") != (
            "mock_preview/rejected"
        ):
            errors.append("rejected_route_invalid")
        scenario = value.get("scenario")
        if isinstance(scenario, str):
            rejected_scenarios.add(scenario)
    if rejected_scenarios != _INVALID_SCENARIOS:
        errors.append("rejected_scenario_set_invalid")
    for path in receipt_files:
        value = _object(path, errors, "receipt_record_invalid")
        if value.get("receipt_schema_version") != 1:
            errors.append("receipt_schema_invalid")
        if value.get("cleanup_complete") is not True:
            errors.append("receipt_cleanup_invalid")
        if not _REQUIRED_AUDIT_FIELDS.issubset(value):
            errors.append("receipt_audit_fields_invalid")
            continue
        if (
            value.get("symbol") != "000403.SZ"
            or not isinstance(value.get("request_id"), str)
            or not _REQUEST_ID.fullmatch(value["request_id"])
            or value.get("retry_count") != 0
            or value.get("cleanup_status") != "CLEAN"
        ):
            errors.append("receipt_audit_binding_invalid")
        for field in (
            "projection_sha256",
            "facts_sha256",
            "relay_contract_hash",
            "proxy_policy_hash",
        ):
            field_value = value.get(field)
            if not isinstance(field_value, str) or not _SHA256.fullmatch(
                field_value
            ):
                errors.append("receipt_audit_hash_invalid")
        for field in (
            "relay_image_digest",
            "proxy_image_digest",
            "mock_provider_image_digest",
        ):
            field_value = value.get(field)
            if not isinstance(field_value, str) or not _IMAGE_SHA256.fullmatch(
                field_value
            ):
                errors.append("receipt_image_digest_invalid")

    determinism = evidence.get("determinism")
    if not isinstance(determinism, dict):
        errors.append("determinism_invalid")
        determinism = {}
    if determinism.get("candidate_hash_match") is not True:
        errors.append("candidate_determinism_invalid")
    if determinism.get("renderer_hash_match") is not True:
        errors.append("renderer_determinism_invalid")
    rendered_sha256 = determinism.get("rendered_sha256")
    if not isinstance(rendered_sha256, str) or not _SHA256.fullmatch(rendered_sha256):
        errors.append("rendered_sha256_invalid")
    elif len(inbox_files) == 1 and hashlib.sha256(
        inbox_files[0].read_bytes()
    ).hexdigest() != rendered_sha256:
        errors.append("rendered_hash_mismatch")
    candidate_sha256 = determinism.get("candidate_sha256")
    if not isinstance(candidate_sha256, str) or not _SHA256.fullmatch(
        candidate_sha256
    ):
        errors.append("candidate_sha256_invalid")

    secret = evidence.get("secret_boundary")
    if not isinstance(secret, dict) or secret != {
        "secret_present": True,
        "secret_value_hit_count": 0,
        "secret_digest_recorded": False,
        "secret_file_cleanup": True,
        "relay_has_secret_mount": False,
        "proxy_has_readonly_secret_mount": True,
        "mock_has_readonly_secret_mount": True,
    }:
        errors.append("secret_boundary_invalid")
    cleanup = evidence.get("cleanup")
    if not isinstance(cleanup, dict) or any(
        cleanup.get(field) != expected
        for field, expected in {
            "cleanup_complete": True,
            "container_residue_count": 0,
            "network_residue_count": 0,
            "secret_file_residue_count": 0,
            "temporary_file_residue_count": 0,
        }.items()
    ):
        errors.append("cleanup_evidence_invalid")

    expected_files = {
        evidence_path,
        *inbox_files,
        *rejected_files,
        *receipt_files,
    }
    actual_files = {item for item in root.rglob("*") if item.is_file()}
    if actual_files != expected_files:
        errors.append("artifact_file_set_invalid")
    unique_errors = sorted(set(errors))
    status = (
        PHASE2B_PROVIDER_RELAY_READY
        if not unique_errors
        else PHASE2B_PROVIDER_RELAY_E2E_BLOCKED
    )
    return ProviderRelayResult(
        status=status,
        errors=unique_errors,
        cleanup_complete=not unique_errors,
        evidence=evidence,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = validate_provider_relay_artifacts(args.artifact_root)
    print(
        json.dumps(
            {"status": result.status, "errors": result.errors},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if result.status == PHASE2B_PROVIDER_RELAY_READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
