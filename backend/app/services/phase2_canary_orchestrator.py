"""Fail-closed state primitives for the one-shot Phase 2B Canary."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from app.schemas.phase2_claims import ClaimsDocument, WorkerClaimsCandidate
from app.services.phase2_ai_worker_protocol import (
    compute_projection_sha256,
)
from app.services.phase2_canary_observability import (
    ChildExecutionTiming,
    ChildProcessEvidence,
    ChildState,
    classify_child_exception,
    classify_completed_child,
    classify_running_child,
)
from app.services.phase2_canary_runtime_artifact import (
    BASE_IMAGE_DIGEST,
    BASE_IMAGE_REFERENCE,
    PROXY_IMAGE_NAME,
    RELAY_IMAGE_NAME,
    RUNTIME_USER,
    RuntimeArtifactCandidate,
)
from app.services.phase2_canary_runtime_contract import (
    load_runtime_contract,
    runtime_contract_sha256,
)
from app.services.phase2_claims_renderer import (
    render_claims_document,
    validate_rendered_document,
)
from app.services.phase2_claims_service import (
    CLAIMS_VALID,
    canonical_json_bytes,
    validate_claims_document,
)
from app.services.phase2_isolation_runtime import validate_isolated_candidate
from app.services.phase2_provider_relay_protocol import build_relay_request

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_MAXIMUM_LEDGER_BYTES = 65_536
_LEDGER_NAME = "attempt-ledger.json"
_SCOPED_LEDGER_NAME = "ledger.json"
_LOCK_NAME = ".runtime.lock"
_LEDGER_NAMESPACE_VERSION = 1
_LEDGER_NAMESPACE_PREFIX = "tickflow-canary-ledger-v1"
_EXACT_MODEL_ID = "gpt-5.6-terra"
_EVENT_FIELDS = {
    "event",
    "occurred",
    "wall_time",
    "monotonic_ns",
    "http_status",
    "byte_count",
}
_PROXY_EVENTS = (
    "process_started",
    "proxy_ready",
    "relay_request_received",
    "provider_connect_started",
    "provider_connect_completed",
    "tls_completed",
    "request_write_started",
    "request_write_completed",
    "response_headers_received",
    "response_body_completed",
)
_RELAY_EVENTS = (
    "relay_started",
    "proxy_connect_started",
    "proxy_connect_completed",
    "request_submitted",
    "response_wait_started",
    "response_received",
    "candidate_write_started",
    "candidate_write_completed",
    "candidate_ready_published",
)
_STAGE_TIE_ORDER = (
    "process_started",
    "proxy_ready",
    "relay_started",
    "proxy_connect_started",
    "proxy_connect_completed",
    "request_submitted",
    "relay_request_received",
    "provider_connect_started",
    "provider_connect_completed",
    "tls_completed",
    "request_write_started",
    "request_write_completed",
    "response_wait_started",
    "response_headers_received",
    "response_body_completed",
    "response_received",
    "candidate_write_started",
    "candidate_write_completed",
    "candidate_ready_published",
)
_PROXY_RECEIPT_FIELDS = {
    "receipt_schema_version",
    "request_id",
    "component",
    "method_allowed",
    "path_allowed",
    "auth_present",
    "tls_verification",
    "redirect_followed",
    "provider_attempt_count",
    "retry_count",
    "provider_http_status",
    "response_category",
    "response_size",
    "response_bytes",
    "terminal_status",
    *_PROXY_EVENTS,
}
_ARK_PROXY_RECEIPT_IDENTITY = {
    "provider_id": "volcengine_ark",
    "endpoint_alias": "ark_responses_cn_beijing_v1",
    "exact_model_id": "doubao-seed-2-1-turbo-260628",
}
_OPENAI_PROXY_RECEIPT_IDENTITY: dict[str, str] = {}
_ARK_PROXY_RECEIPT_FIELDS = _PROXY_RECEIPT_FIELDS | set(_ARK_PROXY_RECEIPT_IDENTITY)
_RELAY_RECEIPT_FIELDS = {
    "receipt_schema_version",
    "request_id",
    "component",
    "status",
    "exit_code",
    "terminal_status",
    "terminal_reason_source",
    "error_category",
    "proxy_http_status",
    "proxy_request_count",
    "retry_count",
    "response_size",
    "response_sha256",
    "started_at",
    "completed_at",
    *_RELAY_EVENTS,
}
_RELAY_TERMINAL_STATUSES = {
    "RELAY_COMPLETED",
    "RELAY_TIMEOUT",
    "RELAY_NONZERO_EXIT",
    "RELAY_PROCESS_ERROR",
    "RELAY_SIGNALLED",
    "RELAY_START_FAILED",
    "RELAY_PROXY_CONNECT_FAILED",
    "RELAY_PROTOCOL_REJECTED",
}
_TERMINAL_REASON_SOURCES = {
    "relay_self",
    "host_child_process",
    "host_timeout",
}
_PROXY_READY_FIELDS = {
    "request_id",
    "proxy_ready",
    "wall_time",
    "monotonic_ns",
    "listener_ready",
}
_SENSITIVE_EVIDENCE = re.compile(
    r"Authorization\s*:|Bearer\s+[^\s]+|"
    r"(?:api[_-]?key|openai_api_key|secret|token|password|credential)"
    r"\s*[:=]\s*[^\s]+|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}",
    re.IGNORECASE,
)
_RUNTIME_EVIDENCE_BASE_FIELDS = frozenset(
    {
        "approval_candidate_sha256",
        "attempt_ledger_state",
        "can_publish",
        "candidate_ready_at",
        "cleanup_completed_at",
        "container_residue_count",
        "dispatch_started_at",
        "host_validation_completed_at",
        "manual_review",
        "network_residue_count",
        "orchestrator_source_sha256",
        "provider_attempt_count",
        "provider_http_status",
        "proxy_image_id",
        "relay_image_id",
        "request_id",
        "response_received_at",
        "retry_count",
        "route",
        "runtime_contract_version",
        "runtime_evidence_schema_version",
        "secret_file_residue_count",
        "symbol",
        "temporary_file_residue_count",
        "terminal_state",
        "timeout_contract_sha256",
    }
)
_RUNTIME_EVIDENCE_SCOPE_FIELDS = frozenset(
    {"approval_scope_id", "ledger_namespace_version"}
)
_RUNTIME_EVIDENCE_OBSERVABILITY_FIELDS = frozenset(
    {"receipt_archive_status", "last_proven_stage", "child_terminal_reason"}
)
_HISTORICAL_RECEIPT_FILES = frozenset(
    {
        "archive-status.json",
        "child-metadata.json",
        "proxy-receipt.json",
        "relay-receipt.json",
    }
)
_REJECTION_FIELDS = frozenset(
    {"can_publish", "error_category", "request_id", "retry_count", "route"}
)
# This pre-observability bundle is accepted only byte-for-byte. The adapter
# supplies fields added later without rewriting the preserved audit evidence.
_PINNED_LEGACY_RECEIPT_BUNDLES = {
    "3d3e6adbe5b74c98ad18a2efe0fd1d0c": {
        "archive-status.json": (
            "f62dfc0ab09307cb0cd21aa9fa43a746bc8e32dd9a263ab31eb7586414739b8d"
        ),
        "child-metadata.json": (
            "c13fd267d78f57550d229fd76ce84d9b3b0d45494e40226cc159ffd0973fdad7"
        ),
        "relay-receipt.json": (
            "25acd73fed9bc65053421b57eff7e59acf00db1d0cf37c3e245c3eb49a1a3c17"
        ),
    },
    "9728fc1f156c4568a98cea9c05ec9fa7": {
        "archive-status.json": (
            "391b85501266b47ae2cb15ba3eeac174880b28b59cd7b80bf5d1e28251e7b598"
        ),
        "child-metadata.json": (
            "489de0ad618c2912fc18d56ef57de4f44f3a0d0a5ff3272940cd76c9aeaaffbe"
        ),
        "proxy-receipt.json": (
            "0d6bbe87812c1f4766dc25097228499e5a11525fd9db2b4ca9a576c11a031604"
        ),
        "relay-receipt.json": (
            "15ec73d5b5c45c659790bc0942b0e5b11c5a6fc5dfe289b2d77d630ae6812294"
        ),
    },
}


def _pinned_receipt_bundle_matches(
    raw_by_name: Mapping[str, bytes],
    request_id: str,
) -> bool:
    expected = _PINNED_LEGACY_RECEIPT_BUNDLES.get(request_id)
    if expected is None:
        return False
    return set(raw_by_name) == set(expected) and all(
        hashlib.sha256(raw_by_name[name]).hexdigest() == digest
        for name, digest in expected.items()
    )


_ARCHIVE_STATUS_FIELDS = frozenset(
    {
        "archive_status",
        "archive_status_schema_version",
        "child_metadata_status",
        "child_terminal_reason",
        "host_child_terminal_reason",
        "last_proven_stage",
        "proxy_receipt_status",
        "relay_receipt_status",
        "request_id",
        "retry_count",
        "terminal_reason_source",
    }
)
_CHILD_METADATA_FIELDS = frozenset(
    {"child_metadata_schema_version", "proxy", "relay", "request_id"}
)
_CHILD_PROCESS_FIELDS = frozenset(
    {
        "child_state",
        "component",
        "elapsed_ms",
        "exit_code",
        "exited_at",
        "exited_monotonic_ns",
        "signal",
        "started_at",
        "started_monotonic_ns",
        "stderr",
        "terminal_reason",
    }
)
_CHILD_STDERR_FIELDS = frozenset(
    {
        "sensitive_hit_count",
        "stderr_byte_count",
        "stderr_excerpt",
        "stderr_redacted",
        "stderr_sha256",
        "stderr_truncated",
    }
)
_HISTORICAL_CANDIDATE_FIELDS = frozenset(
    {
        "ai_call_count",
        "artifact_identity",
        "base_digest_pinned",
        "base_image_reference",
        "build_network",
        "can_publish",
        "endpoint_alias",
        "exact_model",
        "inputs",
        "manual_review",
        "maximum_provider_attempts",
        "new_approval_installed",
        "no_cache",
        "old_approval_candidate_sha256",
        "provider",
        "provider_attempt_count",
        "provider_http",
        "proxy_contents",
        "proxy_image",
        "pull_allowed",
        "relay_contents",
        "relay_image",
        "retry_count",
        "runtime_candidate_schema_version",
        "runtime_contract",
        "status",
        "symbol",
        "trade_date",
    }
)
_HISTORICAL_ARTIFACT_IDENTITY_FIELDS = {
    1: frozenset(
        {
            "facts_sha256",
            "orchestrator_source_sha256",
            "projection_sha256",
            "proxy_image_id",
            "relay_image_id",
            "timeout_contract_sha256",
        }
    ),
    2: frozenset(
        {
            "facts_sha256",
            "orchestrator_source_sha256",
            "projection_sha256",
            "proxy_image_id",
            "readiness_contract_sha256",
            "relay_image_id",
            "timeout_contract_sha256",
        }
    ),
}
_HISTORICAL_INPUT_FIELDS = {
    1: frozenset(
        {
            "artifact_builder_source_sha256",
            "artifact_verifier_source_sha256",
            "launcher_source_sha256",
            "orchestrator_source_sha256",
            "proxy_dockerfile_sha256",
            "proxy_policy_sha256",
            "proxy_source_sha256",
            "relay_dockerfile_sha256",
            "relay_source_sha256",
            "responses_contract_sha256",
            "runbook_sha256",
            "runtime_contract_sha256",
        }
    ),
    2: frozenset(
        {
            "artifact_builder_source_sha256",
            "artifact_verifier_source_sha256",
            "launcher_source_sha256",
            "observability_source_sha256",
            "orchestrator_source_sha256",
            "proxy_dockerfile_sha256",
            "proxy_policy_sha256",
            "proxy_source_sha256",
            "readiness_contract_sha256",
            "relay_dockerfile_sha256",
            "relay_source_sha256",
            "responses_contract_sha256",
            "runbook_sha256",
            "runtime_contract_sha256",
        }
    ),
}
_PINNED_OBSERVABILITY_V1_CANDIDATES = frozenset(
    {
        "b30c156bee1f5f2a1c8d44004fcbd25934e56a1caf6739ab58ffe7ead0e881fb"
    }
)
_HISTORICAL_IMAGE_FIELDS = {
    1: frozenset(
        {
            "base_image_digest",
            "base_rootfs_prefix_verified",
            "dockerfile_sha256",
            "entrypoint",
            "history_clean",
            "image_id",
            "image_name",
            "image_valid",
            "proxy_policy_sha256",
            "responses_contract_sha256",
            "role",
            "rootfs_layer_count",
            "runtime_contract_sha256",
            "runtime_user",
            "source_sha256",
        }
    ),
    2: frozenset(
        {
            "base_image_digest",
            "base_rootfs_prefix_verified",
            "dockerfile_sha256",
            "entrypoint",
            "history_clean",
            "image_id",
            "image_name",
            "image_valid",
            "proxy_policy_sha256",
            "readiness_contract_sha256",
            "responses_contract_sha256",
            "role",
            "rootfs_layer_count",
            "runtime_contract_sha256",
            "runtime_user",
            "source_sha256",
        }
    ),
}
_HISTORICAL_CONTENT_FIELDS = {
    1: frozenset(
        {
            "cleanup_complete",
            "content_valid",
            "responses_contract_sha256",
            "role",
            "runtime_contract_sha256",
            "source_sha256",
        }
    ),
    2: frozenset(
        {
            "cleanup_complete",
            "content_valid",
            "readiness_contract_sha256",
            "responses_contract_sha256",
            "role",
            "runtime_contract_sha256",
            "source_sha256",
        }
    ),
}


def _wall_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class OrchestratorError(RuntimeError):
    """Stable fail-closed error category for Host orchestration."""


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _HEX_64.fullmatch(value) is not None


def _is_image_id(value: object) -> bool:
    return isinstance(value, str) and _IMAGE_ID.fullmatch(value) is not None


def _historical_candidate_nested_valid(
    value: Mapping[str, Any],
    version: int,
    candidate_sha256: str,
) -> bool:
    inputs = value.get("inputs")
    identity = value.get("artifact_identity")
    runtime_contract = value.get("runtime_contract")
    expected_input_fields = _HISTORICAL_INPUT_FIELDS[version]
    if (
        version == 1
        and candidate_sha256 in _PINNED_OBSERVABILITY_V1_CANDIDATES
    ):
        expected_input_fields = expected_input_fields | {
            "observability_source_sha256"
        }
    if (
        not isinstance(inputs, dict)
        or frozenset(inputs) != expected_input_fields
        or any(not _is_sha256(item) for item in inputs.values())
        or not isinstance(identity, dict)
        or frozenset(identity)
        != _HISTORICAL_ARTIFACT_IDENTITY_FIELDS[version]
        or any(
            not _is_sha256(identity.get(field))
            for field in identity
            if field not in {"proxy_image_id", "relay_image_id"}
        )
        or not _is_image_id(identity.get("proxy_image_id"))
        or not _is_image_id(identity.get("relay_image_id"))
        or value.get("base_image_reference") != BASE_IMAGE_REFERENCE
    ):
        return False
    try:
        expected_contract = load_runtime_contract().model_dump(mode="json")
    except (OSError, ValueError):
        return False
    if runtime_contract != expected_contract:
        return False

    images: dict[str, Mapping[str, Any]] = {}
    contents: dict[str, Mapping[str, Any]] = {}
    for role in ("proxy", "relay"):
        image = value.get(f"{role}_image")
        content = value.get(f"{role}_contents")
        expected_name = PROXY_IMAGE_NAME if role == "proxy" else RELAY_IMAGE_NAME
        expected_entrypoint = (
            ["/usr/bin/python3", "/proxy/proxy.py"]
            if role == "proxy"
            else ["/usr/bin/python3", "/relay/relay.py"]
        )
        if (
            not isinstance(image, dict)
            or frozenset(image) != _HISTORICAL_IMAGE_FIELDS[version]
            or image.get("role") != role
            or image.get("image_valid") is not True
            or image.get("history_clean") is not True
            or image.get("base_rootfs_prefix_verified") is not True
            or image.get("image_name") != expected_name
            or not _is_image_id(image.get("image_id"))
            or image.get("base_image_digest") != BASE_IMAGE_DIGEST
            or not _is_sha256(image.get("source_sha256"))
            or not _is_sha256(image.get("dockerfile_sha256"))
            or not _is_sha256(image.get("runtime_contract_sha256"))
            or image.get("runtime_user") != RUNTIME_USER
            or image.get("entrypoint") != expected_entrypoint
            or type(image.get("rootfs_layer_count")) is not int
            or image.get("rootfs_layer_count") < 1
            or not isinstance(content, dict)
            or frozenset(content) != _HISTORICAL_CONTENT_FIELDS[version]
            or content.get("role") != role
            or content.get("content_valid") is not True
            or content.get("cleanup_complete") is not True
            or not _is_sha256(content.get("source_sha256"))
            or not _is_sha256(content.get("runtime_contract_sha256"))
        ):
            return False
        images[role] = image
        contents[role] = content

    readiness_hash = inputs.get("readiness_contract_sha256")
    return not (
        identity.get("proxy_image_id") != images["proxy"].get("image_id")
        or identity.get("relay_image_id") != images["relay"].get("image_id")
        or identity.get("timeout_contract_sha256")
        != inputs.get("runtime_contract_sha256")
        or identity.get("orchestrator_source_sha256")
        != inputs.get("orchestrator_source_sha256")
        or images["proxy"].get("source_sha256")
        != inputs.get("proxy_source_sha256")
        or images["relay"].get("source_sha256")
        != inputs.get("relay_source_sha256")
        or images["proxy"].get("dockerfile_sha256")
        != inputs.get("proxy_dockerfile_sha256")
        or images["relay"].get("dockerfile_sha256")
        != inputs.get("relay_dockerfile_sha256")
        or images["proxy"].get("runtime_contract_sha256")
        != inputs.get("runtime_contract_sha256")
        or images["relay"].get("runtime_contract_sha256")
        != inputs.get("runtime_contract_sha256")
        or images["proxy"].get("responses_contract_sha256")
        != inputs.get("responses_contract_sha256")
        or images["relay"].get("responses_contract_sha256") is not None
        or images["proxy"].get("proxy_policy_sha256")
        != inputs.get("proxy_policy_sha256")
        or images["relay"].get("proxy_policy_sha256") is not None
        or contents["proxy"].get("source_sha256")
        != inputs.get("proxy_source_sha256")
        or contents["relay"].get("source_sha256")
        != inputs.get("relay_source_sha256")
        or contents["proxy"].get("runtime_contract_sha256")
        != inputs.get("runtime_contract_sha256")
        or contents["relay"].get("runtime_contract_sha256")
        != inputs.get("runtime_contract_sha256")
        or contents["proxy"].get("responses_contract_sha256")
        != inputs.get("responses_contract_sha256")
        or contents["relay"].get("responses_contract_sha256") is not None
        or (
            version == 2
            and (
                identity.get("readiness_contract_sha256") != readiness_hash
                or images["proxy"].get("readiness_contract_sha256")
                != readiness_hash
                or images["relay"].get("readiness_contract_sha256") is not None
                or contents["proxy"].get("readiness_contract_sha256")
                != readiness_hash
                or contents["relay"].get("readiness_contract_sha256") is not None
            )
        )
    )


class LedgerState(StrEnum):
    PREPARED = "PREPARED"
    NETWORK_DISPATCH_STARTED = "NETWORK_DISPATCH_STARTED"
    PROVIDER_RESPONSE_RECEIVED = "PROVIDER_RESPONSE_RECEIVED"
    CANDIDATE_COLLECTED = "CANDIDATE_COLLECTED"
    HOST_VALIDATION_COMPLETED = "HOST_VALIDATION_COMPLETED"
    CLEANUP_COMPLETED = "CLEANUP_COMPLETED"
    FAILED_BEFORE_DISPATCH = "FAILED_BEFORE_DISPATCH"
    FAILED_AFTER_DISPATCH = "FAILED_AFTER_DISPATCH"
    ATTEMPT_CONSUMED_UNKNOWN = "ATTEMPT_CONSUMED_UNKNOWN"


_POST_DISPATCH_STATES = {
    LedgerState.NETWORK_DISPATCH_STARTED,
    LedgerState.PROVIDER_RESPONSE_RECEIVED,
    LedgerState.CANDIDATE_COLLECTED,
    LedgerState.HOST_VALIDATION_COMPLETED,
    LedgerState.CLEANUP_COMPLETED,
    LedgerState.FAILED_AFTER_DISPATCH,
    LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
}
_TERMINAL_STATES = {
    LedgerState.CLEANUP_COMPLETED,
    LedgerState.FAILED_BEFORE_DISPATCH,
    LedgerState.FAILED_AFTER_DISPATCH,
    LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
}
_TRANSITIONS = {
    LedgerState.PREPARED: {
        LedgerState.NETWORK_DISPATCH_STARTED,
        LedgerState.FAILED_BEFORE_DISPATCH,
    },
    LedgerState.NETWORK_DISPATCH_STARTED: {
        LedgerState.PROVIDER_RESPONSE_RECEIVED,
        LedgerState.FAILED_AFTER_DISPATCH,
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
    },
    LedgerState.PROVIDER_RESPONSE_RECEIVED: {
        LedgerState.CANDIDATE_COLLECTED,
        LedgerState.FAILED_AFTER_DISPATCH,
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
    },
    LedgerState.CANDIDATE_COLLECTED: {
        LedgerState.HOST_VALIDATION_COMPLETED,
        LedgerState.FAILED_AFTER_DISPATCH,
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
    },
    LedgerState.HOST_VALIDATION_COMPLETED: {
        LedgerState.CLEANUP_COMPLETED,
        LedgerState.FAILED_AFTER_DISPATCH,
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
    },
}


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CanaryRunIdentity(_StrictFrozenModel):
    request_id: str
    symbol: Literal["000403.SZ"]
    trade_date: Literal["2026-07-31"]
    provider: Literal["openai", "volcengine_ark"]
    endpoint_alias: Literal[
        "openai_responses_v1",
        "ark_responses_cn_beijing_v1",
    ]
    facts_sha256: str
    projection_sha256: str
    approval_candidate_sha256: str
    proxy_image_id: str
    relay_image_id: str
    timeout_contract_sha256: str
    orchestrator_source_sha256: str

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        if _HEX_32.fullmatch(value) is None:
            raise ValueError("request_id_invalid")
        return value

    @field_validator(
        "facts_sha256",
        "projection_sha256",
        "approval_candidate_sha256",
        "timeout_contract_sha256",
        "orchestrator_source_sha256",
    )
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("sha256_invalid")
        return value

    @field_validator("proxy_image_id", "relay_image_id")
    @classmethod
    def validate_image_id(cls, value: str) -> str:
        if _IMAGE_ID.fullmatch(value) is None:
            raise ValueError("image_id_invalid")
        return value


class AttemptLedger(_StrictFrozenModel):
    ledger_schema_version: Literal[1] = 1
    identity: CanaryRunIdentity
    state: LedgerState
    provider_attempt_count: Literal[0, 1]
    retry_count: Literal[0] = 0
    prepared_at: str
    dispatch_started_at: str | None = None
    response_received_at: str | None = None
    candidate_ready_at: str | None = None
    host_validation_completed_at: str | None = None
    cleanup_completed_at: str | None = None
    failed_at: str | None = None

    @field_validator(
        "ledger_schema_version",
        "provider_attempt_count",
        "retry_count",
        mode="before",
    )
    @classmethod
    def require_exact_integer_contract_fields(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("ledger_integer_field_invalid")
        return value

    @model_validator(mode="after")
    def require_consistent_timeline(self) -> AttemptLedger:
        timestamps = {
            "prepared_at": self.prepared_at,
            "dispatch_started_at": self.dispatch_started_at,
            "response_received_at": self.response_received_at,
            "candidate_ready_at": self.candidate_ready_at,
            "host_validation_completed_at": self.host_validation_completed_at,
            "cleanup_completed_at": self.cleanup_completed_at,
            "failed_at": self.failed_at,
        }
        if any(
            value is not None and (not value or value != value.strip())
            for value in timestamps.values()
        ):
            raise ValueError("ledger_timestamp_invalid")
        expected_attempts = 1 if self.state in _POST_DISPATCH_STATES else 0
        if self.provider_attempt_count != expected_attempts:
            raise ValueError("ledger_attempt_count_invalid")
        required: dict[LedgerState, tuple[str, ...]] = {
            LedgerState.PREPARED: ("prepared_at",),
            LedgerState.NETWORK_DISPATCH_STARTED: (
                "prepared_at",
                "dispatch_started_at",
            ),
            LedgerState.PROVIDER_RESPONSE_RECEIVED: (
                "prepared_at",
                "dispatch_started_at",
                "response_received_at",
            ),
            LedgerState.CANDIDATE_COLLECTED: (
                "prepared_at",
                "dispatch_started_at",
                "response_received_at",
                "candidate_ready_at",
            ),
            LedgerState.HOST_VALIDATION_COMPLETED: (
                "prepared_at",
                "dispatch_started_at",
                "response_received_at",
                "candidate_ready_at",
                "host_validation_completed_at",
            ),
            LedgerState.CLEANUP_COMPLETED: tuple(timestamps)[:-1],
            LedgerState.FAILED_BEFORE_DISPATCH: ("prepared_at", "failed_at"),
            LedgerState.FAILED_AFTER_DISPATCH: (
                "prepared_at",
                "dispatch_started_at",
                "failed_at",
            ),
            LedgerState.ATTEMPT_CONSUMED_UNKNOWN: (
                "prepared_at",
                "dispatch_started_at",
                "failed_at",
            ),
        }[self.state]
        required_set = set(required)
        if any(timestamps[field] is None for field in required_set):
            raise ValueError("ledger_timeline_incomplete")
        if self.state in {
            LedgerState.PREPARED,
            LedgerState.NETWORK_DISPATCH_STARTED,
            LedgerState.PROVIDER_RESPONSE_RECEIVED,
            LedgerState.CANDIDATE_COLLECTED,
            LedgerState.HOST_VALIDATION_COMPLETED,
            LedgerState.CLEANUP_COMPLETED,
            LedgerState.FAILED_BEFORE_DISPATCH,
        } and any(
            value is not None
            for field, value in timestamps.items()
            if field not in required_set
        ):
            raise ValueError("ledger_timeline_contradictory")
        if (
            self.candidate_ready_at is not None
            and self.response_received_at is None
        ) or (
            self.host_validation_completed_at is not None
            and self.candidate_ready_at is None
        ) or (
            self.cleanup_completed_at is not None
            and self.host_validation_completed_at is None
        ):
            raise ValueError("ledger_timeline_contradictory")
        return self


class ApprovalScopeIdentity(_StrictFrozenModel):
    ledger_namespace_version: Literal[1] = 1
    approval_candidate_sha256: str
    symbol: Literal["000403.SZ"]
    provider_id: Literal["openai", "volcengine_ark"]
    exact_model_id: Literal[
        "gpt-5.6-terra",
        "doubao-seed-2-1-turbo-260628",
    ]
    endpoint_alias: Literal[
        "openai_responses_v1",
        "ark_responses_cn_beijing_v1",
    ]

    @field_validator("ledger_namespace_version", mode="before")
    @classmethod
    def require_exact_namespace_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("ledger_namespace_version_invalid")
        return value

    @field_validator("approval_candidate_sha256")
    @classmethod
    def validate_candidate_sha256(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("approval_candidate_sha256_invalid")
        return value


def approval_scope_material(scope: ApprovalScopeIdentity) -> bytes:
    if not isinstance(scope, ApprovalScopeIdentity):
        raise OrchestratorError("approval_scope_identity_invalid")
    values = (
        _LEDGER_NAMESPACE_PREFIX,
        scope.approval_candidate_sha256,
        scope.symbol,
        scope.provider_id,
        scope.exact_model_id,
        scope.endpoint_alias,
    )
    return "\n|\n".join(values).encode("utf-8")


def compute_approval_scope_id(scope: ApprovalScopeIdentity) -> str:
    return hashlib.sha256(approval_scope_material(scope)).hexdigest()


class ApprovalScopedAttemptLedger(_StrictFrozenModel):
    ledger_namespace_version: Literal[1] = 1
    approval_scope_id: str
    exact_model_id: Literal[
        "gpt-5.6-terra",
        "doubao-seed-2-1-turbo-260628",
    ]
    attempt: AttemptLedger

    @field_validator("ledger_namespace_version", mode="before")
    @classmethod
    def require_exact_namespace_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("ledger_namespace_version_invalid")
        return value

    @field_validator("approval_scope_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("approval_scope_id_invalid")
        return value

    @model_validator(mode="after")
    def require_scope_binding(self) -> ApprovalScopedAttemptLedger:
        identity = self.attempt.identity
        scope = ApprovalScopeIdentity(
            approval_candidate_sha256=identity.approval_candidate_sha256,
            symbol=identity.symbol,
            provider_id=identity.provider,
            exact_model_id=self.exact_model_id,
            endpoint_alias=identity.endpoint_alias,
        )
        if self.approval_scope_id != compute_approval_scope_id(scope):
            raise ValueError("approval_scope_binding_invalid")
        return self

    @property
    def identity(self) -> CanaryRunIdentity:
        return self.attempt.identity

    @property
    def state(self) -> LedgerState:
        return self.attempt.state

    @property
    def provider_attempt_count(self) -> int:
        return self.attempt.provider_attempt_count

    @property
    def dispatch_started_at(self) -> str | None:
        return self.attempt.dispatch_started_at

    @property
    def response_received_at(self) -> str | None:
        return self.attempt.response_received_at

    @property
    def candidate_ready_at(self) -> str | None:
        return self.attempt.candidate_ready_at

    @property
    def host_validation_completed_at(self) -> str | None:
        return self.attempt.host_validation_completed_at

    @property
    def cleanup_completed_at(self) -> str | None:
        return self.attempt.cleanup_completed_at


def _canonical_bytes(value: object) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise OrchestratorError("ledger_encoding_failed") from exc
    return (rendered + "\n").encode("utf-8")


def _assert_no_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise OrchestratorError("state_root_symlink_forbidden")


def _validate_state_root(root: Path) -> Path:
    path = Path(os.path.abspath(os.fspath(root)))
    _assert_no_symlink_components(path)
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError("state_root_missing") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise OrchestratorError("state_root_symlink_forbidden")
    if not stat.S_ISDIR(metadata.st_mode):
        raise OrchestratorError("state_root_not_directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise OrchestratorError("state_root_mode_invalid")
    return path


def _read_regular(path: Path, category: str) -> bytes:
    try:
        before = os.lstat(path)
    except FileNotFoundError as exc:
        raise OrchestratorError("ledger_missing") from exc
    except OSError as exc:
        raise OrchestratorError(category) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 0 < before.st_size <= _MAXIMUM_LEDGER_BYTES
    ):
        raise OrchestratorError(category)
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise OrchestratorError(category)
        raw = os.read(descriptor, _MAXIMUM_LEDGER_BYTES + 1)
        after = os.lstat(path)
        if (
            len(raw) != opened.st_size
            or len(raw) > _MAXIMUM_LEDGER_BYTES
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OrchestratorError(category)
        return raw
    except OSError as exc:
        raise OrchestratorError(category) from exc
    finally:
        os.close(descriptor)


def _decode_ledger(raw: bytes) -> AttemptLedger:
    try:
        ledger = AttemptLedger.model_validate_json(raw)
    except ValueError as exc:
        raise OrchestratorError("ledger_invalid") from exc
    if raw != _canonical_bytes(ledger.model_dump(mode="json")):
        raise OrchestratorError("ledger_not_canonical")
    expected_attempts = 1 if ledger.state in _POST_DISPATCH_STATES else 0
    if ledger.provider_attempt_count != expected_attempts:
        raise OrchestratorError("ledger_attempt_count_invalid")
    return ledger


def _atomic_write(path: Path, raw: bytes) -> None:
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)
    ):
        raise OrchestratorError("ledger_target_invalid")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".partial",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OrchestratorError("ledger_write_failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OrchestratorError:
        raise
    except OSError as exc:
        raise OrchestratorError("ledger_publish_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _frozen_legacy_state_roots() -> tuple[Path, ...]:
    return (
        Path.home()
        / "Library/Application Support/TickFlowPhase2Canary/runtime-v1",
    )


class AttemptLedgerStore:
    """Read legacy ledgers; writes are restricted to isolated test fixtures."""

    def __init__(
        self,
        state_root: Path,
        *,
        fixture_writes_enabled: bool = False,
    ) -> None:
        self.root = _validate_state_root(state_root)
        self.path = self.root / _LEDGER_NAME
        if not isinstance(fixture_writes_enabled, bool):
            raise OrchestratorError("fixture_write_setting_invalid")
        self.fixture_writes_enabled = fixture_writes_enabled

    def _assert_fixture_write_allowed(self) -> None:
        if not self.fixture_writes_enabled:
            raise OrchestratorError("legacy_ledger_read_only")
        for frozen in _frozen_legacy_state_roots():
            frozen_root = Path(os.path.abspath(os.fspath(frozen)))
            if self.root == frozen_root or frozen_root in self.root.parents:
                raise OrchestratorError("historical_ledger_immutable")

    def prepare(self, identity: CanaryRunIdentity, *, timestamp: str) -> AttemptLedger:
        self._assert_fixture_write_allowed()
        if not isinstance(identity, CanaryRunIdentity):
            raise OrchestratorError("identity_invalid")
        try:
            existing = os.lstat(self.path)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
                raise OrchestratorError("ledger_target_invalid")
            raise OrchestratorError("ledger_already_exists")
        ledger = AttemptLedger(
            identity=identity,
            state=LedgerState.PREPARED,
            provider_attempt_count=0,
            prepared_at=timestamp,
        )
        _atomic_write(self.path, _canonical_bytes(ledger.model_dump(mode="json")))
        return ledger

    def load(self) -> AttemptLedger:
        return _decode_ledger(_read_regular(self.path, "ledger_target_invalid"))

    def transition(
        self,
        state: LedgerState,
        *,
        timestamp: str,
    ) -> AttemptLedger:
        self._assert_fixture_write_allowed()
        current = self.load()
        if state not in _TRANSITIONS.get(current.state, set()):
            raise OrchestratorError("ledger_transition_invalid")
        updates: dict[str, Any] = {
            "state": state,
            "provider_attempt_count": 1 if state in _POST_DISPATCH_STATES else 0,
        }
        timestamp_field = {
            LedgerState.NETWORK_DISPATCH_STARTED: "dispatch_started_at",
            LedgerState.PROVIDER_RESPONSE_RECEIVED: "response_received_at",
            LedgerState.CANDIDATE_COLLECTED: "candidate_ready_at",
            LedgerState.HOST_VALIDATION_COMPLETED: "host_validation_completed_at",
            LedgerState.CLEANUP_COMPLETED: "cleanup_completed_at",
            LedgerState.FAILED_BEFORE_DISPATCH: "failed_at",
            LedgerState.FAILED_AFTER_DISPATCH: "failed_at",
            LedgerState.ATTEMPT_CONSUMED_UNKNOWN: "failed_at",
        }[state]
        updates[timestamp_field] = timestamp
        ledger = current.model_copy(update=updates)
        ledger = AttemptLedger.model_validate_json(
            _canonical_bytes(ledger.model_dump(mode="json"))
        )
        _atomic_write(self.path, _canonical_bytes(ledger.model_dump(mode="json")))
        return ledger


def recover_attempt_state(
    store: AttemptLedgerStore,
    *,
    timestamp: str,
) -> AttemptLedger:
    """Close an interrupted state without ever reopening the attempt."""
    current = store.load()
    if current.state in _TERMINAL_STATES:
        return current
    if current.state is LedgerState.PREPARED:
        return store.transition(
            LedgerState.FAILED_BEFORE_DISPATCH,
            timestamp=timestamp,
        )
    return store.transition(
        LedgerState.ATTEMPT_CONSUMED_UNKNOWN,
        timestamp=timestamp,
    )


def _validate_report_directory(path: Path, category: str) -> Path:
    target = Path(os.path.abspath(os.fspath(path)))
    try:
        _assert_no_symlink_components(target)
        metadata = os.lstat(target)
    except (OSError, OrchestratorError) as exc:
        raise OrchestratorError(category) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise OrchestratorError(category)
    return target


def _read_report_regular(
    path: Path,
    category: str,
    *,
    maximum_bytes: int = 262_144,
) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > maximum_bytes
    ):
        raise OrchestratorError(category)
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    try:
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, maximum_bytes + 1)
        after = os.lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or len(raw) != opened.st_size
            or len(raw) > maximum_bytes
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OrchestratorError(category)
        return raw
    except OSError as exc:
        raise OrchestratorError(category) from exc
    finally:
        os.close(descriptor)


def _open_private_directory(
    path: Path,
    category: str,
    *,
    allow_read_only_public: bool = False,
) -> int:
    target = Path(os.path.abspath(os.fspath(path)))
    try:
        _assert_no_symlink_components(target)
        before = os.lstat(target)
        descriptor = os.open(
            target,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
        )
        opened = os.fstat(descriptor)
    except (OSError, OrchestratorError) as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise OrchestratorError(category) from exc
    mode = stat.S_IMODE(before.st_mode)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or (
            mode != 0o700
            and not (allow_read_only_public and mode & 0o022 == 0)
        )
        or not stat.S_ISDIR(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
    ):
        os.close(descriptor)
        raise OrchestratorError(category)
    return descriptor


def _write_descriptor(descriptor: int, raw: bytes, category: str) -> None:
    offset = 0
    while offset < len(raw):
        try:
            written = os.write(descriptor, raw[offset:])
        except OSError as exc:
            raise OrchestratorError(category) from exc
        if written <= 0:
            raise OrchestratorError(category)
        offset += written
    os.fsync(descriptor)


def _atomic_create_no_clobber(path: Path, raw: bytes) -> None:
    parent_descriptor = _open_private_directory(
        path.parent,
        "ledger_namespace_invalid",
    )
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.partial"
    temporary_descriptor = -1
    linked = False
    try:
        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=parent_descriptor,
        )
        _write_descriptor(
            temporary_descriptor,
            raw,
            "scoped_ledger_write_failed",
        )
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise OrchestratorError("scoped_ledger_already_exists") from exc
        linked = True
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except OrchestratorError:
        raise
    except OSError as exc:
        raise OrchestratorError("scoped_ledger_write_failed") from exc
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if not linked:
            with contextlib.suppress(OSError):
                os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.close(parent_descriptor)


def _atomic_replace_expected(
    path: Path,
    raw: bytes,
    *,
    expected_raw: bytes,
) -> None:
    parent_descriptor = _open_private_directory(
        path.parent,
        "ledger_namespace_invalid",
    )
    target_descriptor = -1
    temporary_descriptor = -1
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.partial"
    backup_name = f".{path.name}.{uuid.uuid4().hex}.previous"
    backup_present = False

    def restore_backup() -> bool:
        nonlocal backup_present
        try:
            os.link(
                backup_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False
        os.unlink(backup_name, dir_fd=parent_descriptor)
        backup_present = False
        os.fsync(parent_descriptor)
        return True

    try:
        target_descriptor = os.open(
            path.name,
            os.O_RDONLY | _NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        target_metadata = os.fstat(target_descriptor)
        observed = os.read(target_descriptor, _MAXIMUM_LEDGER_BYTES + 1)
        if (
            not stat.S_ISREG(target_metadata.st_mode)
            or stat.S_IMODE(target_metadata.st_mode) != 0o600
            or len(observed) != target_metadata.st_size
            or observed != expected_raw
        ):
            raise OrchestratorError("scoped_ledger_changed")
        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=parent_descriptor,
        )
        _write_descriptor(
            temporary_descriptor,
            raw,
            "scoped_ledger_write_failed",
        )
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        os.rename(
            path.name,
            backup_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        backup_present = True
        moved = os.stat(
            backup_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        os.lseek(target_descriptor, 0, os.SEEK_SET)
        moved_raw = os.read(target_descriptor, _MAXIMUM_LEDGER_BYTES + 1)
        if (
            (moved.st_dev, moved.st_ino)
            != (target_metadata.st_dev, target_metadata.st_ino)
            or moved_raw != expected_raw
            or len(moved_raw) != moved.st_size
        ):
            restore_backup()
            raise OrchestratorError("scoped_ledger_changed")
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            restore_backup()
            raise OrchestratorError("scoped_ledger_changed") from exc
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.unlink(backup_name, dir_fd=parent_descriptor)
        backup_present = False
        os.fsync(parent_descriptor)
    except OrchestratorError:
        raise
    except OSError as exc:
        raise OrchestratorError("scoped_ledger_write_failed") from exc
    finally:
        if target_descriptor >= 0:
            os.close(target_descriptor)
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        with contextlib.suppress(OSError):
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        if backup_present:
            with contextlib.suppress(OSError):
                os.fsync(parent_descriptor)
        os.close(parent_descriptor)


def _private_namespace_child(parent: Path, name: str) -> Path:
    child = parent / name
    parent_descriptor = _open_private_directory(
        parent,
        "ledger_namespace_invalid",
    )
    try:
        metadata = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
        except FileExistsError:
            pass
        except OSError as exc:
            os.close(parent_descriptor)
            raise OrchestratorError("ledger_namespace_create_failed") from exc
        try:
            metadata = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            os.close(parent_descriptor)
            raise OrchestratorError("ledger_namespace_create_failed") from exc
    except OSError as exc:
        os.close(parent_descriptor)
        raise OrchestratorError("ledger_namespace_invalid") from exc
    try:
        if stat.S_ISLNK(metadata.st_mode):
            raise OrchestratorError("ledger_namespace_symlink_forbidden")
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise OrchestratorError("ledger_namespace_mode_invalid")
        child_descriptor = os.open(
            name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        try:
            opened = os.fstat(child_descriptor)
            if (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise OrchestratorError("ledger_namespace_changed")
        finally:
            os.close(child_descriptor)
        return child
    finally:
        os.close(parent_descriptor)


def _decode_scoped_ledger(raw: bytes) -> ApprovalScopedAttemptLedger:
    try:
        ledger = ApprovalScopedAttemptLedger.model_validate_json(raw)
    except ValueError as exc:
        raise OrchestratorError("scoped_ledger_invalid") from exc
    if raw != _canonical_bytes(ledger.model_dump(mode="json")):
        raise OrchestratorError("scoped_ledger_not_canonical")
    expected_attempts = 1 if ledger.state in _POST_DISPATCH_STATES else 0
    if ledger.provider_attempt_count != expected_attempts:
        raise OrchestratorError("scoped_ledger_attempt_count_invalid")
    return ledger


class ApprovalScopedAttemptLedgerStore:
    """Fsync-backed ledger for one request inside one approval scope."""

    def __init__(
        self,
        attempts_root: Path,
        *,
        scope: ApprovalScopeIdentity,
        request_id: str,
    ) -> None:
        self.attempts_root = _validate_state_root(attempts_root)
        if not isinstance(scope, ApprovalScopeIdentity):
            raise OrchestratorError("approval_scope_identity_invalid")
        if _HEX_32.fullmatch(request_id) is None:
            raise OrchestratorError("request_id_invalid")
        self.scope = scope
        self.scope_id = compute_approval_scope_id(scope)
        self.request_id = request_id
        self.scope_root = self.attempts_root / self.scope_id
        self.root = self.scope_root / request_id
        self.path = self.root / _SCOPED_LEDGER_NAME

    def _ensure_root(self) -> None:
        scope_root = _private_namespace_child(self.attempts_root, self.scope_id)
        request_root = _private_namespace_child(scope_root, self.request_id)
        if request_root != self.root:
            raise OrchestratorError("ledger_namespace_path_invalid")

    def _validate_existing_root(self) -> None:
        for path in (self.scope_root, self.root):
            try:
                metadata = os.lstat(path)
            except OSError as exc:
                raise OrchestratorError("scoped_ledger_missing") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise OrchestratorError("ledger_namespace_symlink_forbidden")
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise OrchestratorError("ledger_namespace_mode_invalid")

    def prepare(
        self,
        identity: CanaryRunIdentity,
        *,
        timestamp: str,
    ) -> ApprovalScopedAttemptLedger:
        if (
            not isinstance(identity, CanaryRunIdentity)
            or identity.request_id != self.request_id
            or identity.approval_candidate_sha256
            != self.scope.approval_candidate_sha256
            or identity.symbol != self.scope.symbol
            or identity.provider != self.scope.provider_id
            or identity.endpoint_alias != self.scope.endpoint_alias
        ):
            raise OrchestratorError("scoped_ledger_identity_invalid")
        self._ensure_root()
        attempt = AttemptLedger(
            identity=identity,
            state=LedgerState.PREPARED,
            provider_attempt_count=0,
            prepared_at=timestamp,
        )
        ledger = ApprovalScopedAttemptLedger(
            approval_scope_id=self.scope_id,
            exact_model_id=self.scope.exact_model_id,
            attempt=attempt,
        )
        _atomic_create_no_clobber(
            self.path,
            _canonical_bytes(ledger.model_dump(mode="json")),
        )
        return ledger

    def load(self) -> ApprovalScopedAttemptLedger:
        self._validate_existing_root()
        ledger = _decode_scoped_ledger(
            _read_regular(self.path, "scoped_ledger_target_invalid")
        )
        if (
            ledger.approval_scope_id != self.scope_id
            or ledger.identity.request_id != self.request_id
            or ledger.exact_model_id != self.scope.exact_model_id
        ):
            raise OrchestratorError("scoped_ledger_identity_invalid")
        return ledger

    def transition(
        self,
        state: LedgerState,
        *,
        timestamp: str,
    ) -> ApprovalScopedAttemptLedger:
        current = self.load()
        if state not in _TRANSITIONS.get(current.state, set()):
            raise OrchestratorError("ledger_transition_invalid")
        updates: dict[str, Any] = {
            "state": state,
            "provider_attempt_count": 1 if state in _POST_DISPATCH_STATES else 0,
        }
        timestamp_field = {
            LedgerState.NETWORK_DISPATCH_STARTED: "dispatch_started_at",
            LedgerState.PROVIDER_RESPONSE_RECEIVED: "response_received_at",
            LedgerState.CANDIDATE_COLLECTED: "candidate_ready_at",
            LedgerState.HOST_VALIDATION_COMPLETED: "host_validation_completed_at",
            LedgerState.CLEANUP_COMPLETED: "cleanup_completed_at",
            LedgerState.FAILED_BEFORE_DISPATCH: "failed_at",
            LedgerState.FAILED_AFTER_DISPATCH: "failed_at",
            LedgerState.ATTEMPT_CONSUMED_UNKNOWN: "failed_at",
        }[state]
        updates[timestamp_field] = timestamp
        attempt = current.attempt.model_copy(update=updates)
        ledger = ApprovalScopedAttemptLedger(
            approval_scope_id=current.approval_scope_id,
            exact_model_id=current.exact_model_id,
            attempt=attempt,
        )
        _atomic_replace_expected(
            self.path,
            _canonical_bytes(ledger.model_dump(mode="json")),
            expected_raw=_canonical_bytes(current.model_dump(mode="json")),
        )
        return ledger


@dataclass(frozen=True)
class LedgerNamespacePreflight:
    status: str
    current_scope_id: str
    current_scope_provider_attempts: int
    current_scope_attempt_availability: Literal["AVAILABLE", "BLOCKED"]
    historical_requests: tuple[str, ...]
    global_request_ids: tuple[str, ...]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class _DiscoveredLedger:
    ledger: AttemptLedger | ApprovalScopedAttemptLedger
    path: Path
    scoped: bool


class ApprovalScopedLedgerNamespace:
    """Read-only preflight across legacy and approval-scoped attempts."""

    def __init__(
        self,
        *,
        repo_root: Path,
        attempts_root: Path,
        legacy_state_root: Path,
        historical_evidence_root: Path,
        candidate_roots: tuple[Path, ...],
    ) -> None:
        self.repo_root = _validate_report_directory(
            repo_root,
            "repo_root_invalid",
        )
        self.attempts_root = _validate_state_root(attempts_root)
        self.legacy_state_root = _validate_state_root(legacy_state_root)
        self.historical_evidence_root = _validate_report_directory(
            historical_evidence_root,
            "historical_evidence_root_invalid",
        )
        if not candidate_roots:
            raise OrchestratorError("candidate_roots_missing")
        self.candidate_roots = tuple(
            _validate_report_directory(path, "candidate_root_invalid")
            for path in candidate_roots
        )

    def _legacy_ledgers(self) -> list[_DiscoveredLedger]:
        allowed_entries = {_LEDGER_NAME, "history", ".runtime.lock"}
        if any(
            entry.name not in allowed_entries
            for entry in self.legacy_state_root.iterdir()
        ):
            raise OrchestratorError("historical_state_entries_invalid")
        paths: list[Path] = []
        current = self.legacy_state_root / _LEDGER_NAME
        if current.exists() or current.is_symlink():
            paths.append(current)
        history = self.legacy_state_root / "history"
        if history.exists() or history.is_symlink():
            history = _validate_state_root(history)
            for request_root in sorted(history.iterdir(), key=lambda item: item.name):
                if _HEX_32.fullmatch(request_root.name) is None:
                    raise OrchestratorError("historical_request_path_invalid")
                request_root = _validate_state_root(request_root)
                if {item.name for item in request_root.iterdir()} != {
                    _LEDGER_NAME
                }:
                    raise OrchestratorError(
                        "historical_request_entries_invalid"
                    )
                ledger_path = request_root / _LEDGER_NAME
                if not ledger_path.exists() and not ledger_path.is_symlink():
                    raise OrchestratorError("historical_ledger_missing")
                paths.append(ledger_path)
        discovered: list[_DiscoveredLedger] = []
        for path in paths:
            ledger = _decode_ledger(_read_regular(path, "ledger_target_invalid"))
            if (
                path.parent.name != "history"
                and path.parent != self.legacy_state_root
                and path.parent.name != ledger.identity.request_id
            ):
                raise OrchestratorError("historical_request_path_invalid")
            discovered.append(_DiscoveredLedger(ledger=ledger, path=path, scoped=False))
        return discovered

    def _scoped_ledgers(self) -> list[_DiscoveredLedger]:
        discovered: list[_DiscoveredLedger] = []
        for scope_root in sorted(self.attempts_root.iterdir(), key=lambda item: item.name):
            if _HEX_64.fullmatch(scope_root.name) is None:
                raise OrchestratorError("scoped_scope_path_invalid")
            scope_root = _validate_state_root(scope_root)
            for request_root in sorted(scope_root.iterdir(), key=lambda item: item.name):
                if _HEX_32.fullmatch(request_root.name) is None:
                    raise OrchestratorError("scoped_request_path_invalid")
                request_root = _validate_state_root(request_root)
                if {item.name for item in request_root.iterdir()} != {
                    _SCOPED_LEDGER_NAME
                }:
                    raise OrchestratorError("scoped_request_entries_invalid")
                ledger_path = request_root / _SCOPED_LEDGER_NAME
                if not ledger_path.exists() and not ledger_path.is_symlink():
                    raise OrchestratorError("scoped_ledger_missing")
                ledger = _decode_scoped_ledger(
                    _read_regular(ledger_path, "scoped_ledger_target_invalid")
                )
                if (
                    ledger.approval_scope_id != scope_root.name
                    or ledger.identity.request_id != request_root.name
                ):
                    raise OrchestratorError("scoped_ledger_path_binding_invalid")
                discovered.append(
                    _DiscoveredLedger(ledger=ledger, path=ledger_path, scoped=True)
                )
        return discovered

    def _candidate_identity(self, candidate_sha256: str) -> ApprovalScopeIdentity:
        for root in self.candidate_roots:
            candidates = (
                root / f"{candidate_sha256}.json",
                root / "runtime_contract_candidate.json",
                root / "approval_candidate.json",
            )
            path = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.exists() or candidate.is_symlink()
                ),
                None,
            )
            if path is None:
                continue
            raw = _read_report_regular(path, "historical_candidate_invalid")
            if hashlib.sha256(raw).hexdigest() != candidate_sha256:
                if path.name == "runtime_contract_candidate.json":
                    continue
                raise OrchestratorError("historical_candidate_hash_mismatch")
            try:
                value = _strict_json_bytes(raw, "historical_candidate_invalid")
            except OrchestratorError as exc:
                raise OrchestratorError("historical_candidate_invalid") from exc
            if value.get("ark_approval_candidate_schema_version") == 1:
                artifact_hashes = value.get("artifact_hashes")
                expected_fields = {
                    "ai_call_count",
                    "approval_installed",
                    "ark_approval_candidate_schema_version",
                    "artifact_hashes",
                    "can_publish",
                    "endpoint",
                    "endpoint_alias",
                    "exact_model_id",
                    "keychain_service",
                    "maximum_provider_attempts",
                    "old_deepseek_model",
                    "openai_provider",
                    "provider_attempt_count",
                    "provider_http",
                    "provider_id",
                    "proxy_image_id",
                    "relay_image_id",
                    "retry_count",
                    "status",
                    "symbol",
                    "trade_date",
                }
                expected_hashes = {
                    "ark_adapter_source_sha256",
                    "ark_launcher_source_sha256",
                    "ark_proxy_policy_sha256",
                    "ark_responses_contract_sha256",
                    "facts_sha256",
                    "orchestrator_source_sha256",
                    "projection_sha256",
                    "readiness_contract_sha256",
                    "runtime_contract_sha256",
                    "typed_claims_schema_sha256",
                }
                if (
                    raw != canonical_json_bytes(value)
                    or set(value) != expected_fields
                    or not isinstance(artifact_hashes, dict)
                    or set(artifact_hashes) != expected_hashes
                    or any(
                        not isinstance(digest, str) or _HEX_64.fullmatch(digest) is None
                        for digest in artifact_hashes.values()
                    )
                    or value.get("status")
                    != "PHASE2B_ARK_PROVIDER_READY_FOR_REAPPROVAL"
                    or value.get("provider_id") != "volcengine_ark"
                    or value.get("exact_model_id") != "doubao-seed-2-1-turbo-260628"
                    or value.get("endpoint_alias") != "ark_responses_cn_beijing_v1"
                    or value.get("endpoint")
                    != "https://ark.cn-beijing.volces.com/api/v3/responses"
                    or value.get("keychain_service")
                    != "tickflow-phase2-canary-volcengine-ark"
                    or value.get("symbol") != "000403.SZ"
                    or value.get("trade_date") != "2026-07-31"
                    or value.get("approval_installed") is not False
                    or value.get("provider_http") != "NOT_RUN"
                    or value.get("provider_attempt_count") != 0
                    or value.get("ai_call_count") != 0
                    or value.get("retry_count") != 0
                    or value.get("maximum_provider_attempts") != 1
                    or value.get("can_publish") is not False
                    or value.get("openai_provider") != "FROZEN"
                    or value.get("old_deepseek_model")
                    != "PHASE2B_ARK_STRUCTURED_OUTPUT_BLOCKED_PRESERVED"
                    or not isinstance(value.get("proxy_image_id"), str)
                    or _IMAGE_ID.fullmatch(value["proxy_image_id"]) is None
                    or not isinstance(value.get("relay_image_id"), str)
                    or _IMAGE_ID.fullmatch(value["relay_image_id"]) is None
                ):
                    raise OrchestratorError("historical_candidate_invalid")
                return ApprovalScopeIdentity(
                    approval_candidate_sha256=candidate_sha256,
                    symbol=value["symbol"],
                    provider_id=value["provider_id"],
                    exact_model_id=value["exact_model_id"],
                    endpoint_alias=value["endpoint_alias"],
                )
            version = value.get("runtime_candidate_schema_version")
            artifact_identity = value.get("artifact_identity")
            nested_objects = (
                "inputs",
                "proxy_contents",
                "proxy_image",
                "relay_contents",
                "relay_image",
                "runtime_contract",
            )
            if (
                raw != canonical_json_bytes(value)
                or not isinstance(value, dict)
                or frozenset(value) != _HISTORICAL_CANDIDATE_FIELDS
                or type(version) is not int
                or version not in _HISTORICAL_ARTIFACT_IDENTITY_FIELDS
                or not isinstance(artifact_identity, dict)
                or frozenset(artifact_identity)
                != _HISTORICAL_ARTIFACT_IDENTITY_FIELDS[version]
                or any(not isinstance(value.get(field), dict) for field in nested_objects)
                or not _historical_candidate_nested_valid(
                    value,
                    version,
                    candidate_sha256,
                )
                or value.get("status")
                != "PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL"
                or value.get("symbol") != "000403.SZ"
                or value.get("trade_date") != "2026-07-31"
                or value.get("provider") != "openai"
                or value.get("exact_model") != _EXACT_MODEL_ID
                or value.get("endpoint_alias") != "openai_responses_v1"
                or not isinstance(
                    value.get("old_approval_candidate_sha256"), str
                )
                or _HEX_64.fullmatch(value["old_approval_candidate_sha256"])
                is None
                or value.get("new_approval_installed") is not False
                or value.get("pull_allowed") is not False
                or value.get("no_cache") is not True
                or value.get("base_digest_pinned") is not True
                or value.get("build_network") != "none"
                or value.get("provider_http") != "NOT_RUN"
                or value.get("can_publish") is not False
                or value.get("manual_review") != "NOT_STARTED"
                or any(
                    type(value.get(field)) is not int
                    for field in (
                        "ai_call_count",
                        "maximum_provider_attempts",
                        "provider_attempt_count",
                        "retry_count",
                    )
                )
                or value.get("ai_call_count") != 0
                or value.get("maximum_provider_attempts") != 1
                or value.get("provider_attempt_count") != 0
                or value.get("retry_count") != 0
                or any(
                    not isinstance(artifact_identity.get(field), str)
                    or _HEX_64.fullmatch(artifact_identity[field]) is None
                    for field in (
                        "facts_sha256",
                        "orchestrator_source_sha256",
                        "projection_sha256",
                        "timeout_contract_sha256",
                    )
                )
                or not isinstance(artifact_identity.get("proxy_image_id"), str)
                or _IMAGE_ID.fullmatch(artifact_identity["proxy_image_id"])
                is None
                or not isinstance(artifact_identity.get("relay_image_id"), str)
                or _IMAGE_ID.fullmatch(artifact_identity["relay_image_id"])
                is None
                or (
                    version == 2
                    and (
                        not isinstance(
                            artifact_identity.get("readiness_contract_sha256"),
                            str,
                        )
                        or _HEX_64.fullmatch(
                            artifact_identity["readiness_contract_sha256"]
                        )
                        is None
                    )
                )
            ):
                raise OrchestratorError("historical_candidate_invalid")
            try:
                return ApprovalScopeIdentity(
                    approval_candidate_sha256=candidate_sha256,
                    symbol=value["symbol"],
                    provider_id=value["provider"],
                    exact_model_id=value["exact_model"],
                    endpoint_alias=value["endpoint_alias"],
                )
            except (KeyError, ValidationError) as exc:
                raise OrchestratorError(
                    "historical_approval_identity_unresolved"
                ) from exc
        raise OrchestratorError("historical_approval_identity_unresolved")

    def _validate_historical_evidence(
        self,
        ledger: AttemptLedger | ApprovalScopedAttemptLedger,
    ) -> None:
        path = (
            self.historical_evidence_root
            / "evidence"
            / f"{ledger.identity.request_id}.json"
        )
        raw = _read_report_regular(path, "historical_runtime_evidence_missing")
        try:
            value = _strict_json_bytes(raw, "historical_runtime_evidence_invalid")
        except OrchestratorError as exc:
            raise OrchestratorError("historical_runtime_evidence_invalid") from exc
        scoped = isinstance(ledger, ApprovalScopedAttemptLedger)
        expected_fields = _RUNTIME_EVIDENCE_BASE_FIELDS
        allowed_fields = {
            expected_fields,
            expected_fields | _RUNTIME_EVIDENCE_OBSERVABILITY_FIELDS,
        }
        if scoped:
            expected_fields = (
                expected_fields
                | _RUNTIME_EVIDENCE_SCOPE_FIELDS
                | _RUNTIME_EVIDENCE_OBSERVABILITY_FIELDS
            )
            allowed_fields = {expected_fields}
        attempt = ledger.attempt if scoped else ledger
        route_valid = (
            value.get("route") == "inbox"
            and value.get("terminal_state") == "SUCCEEDED"
            if ledger.state is LedgerState.CLEANUP_COMPLETED
            else value.get("route") == "rejected"
            and value.get("terminal_state")
            not in {"SUCCEEDED", "CLEANUP_COMPLETED"}
        )
        observability_valid = True
        if _RUNTIME_EVIDENCE_OBSERVABILITY_FIELDS.issubset(value):
            observability_valid = (
                isinstance(value.get("receipt_archive_status"), str)
                and bool(value["receipt_archive_status"])
                and (
                    value.get("last_proven_stage") is None
                    or isinstance(value.get("last_proven_stage"), str)
                )
                and (
                    value.get("child_terminal_reason") is None
                    or isinstance(value.get("child_terminal_reason"), str)
                )
            )
        integer_fields_valid = all(
            type(value.get(field)) is int
            for field in (
                "runtime_evidence_schema_version",
                "runtime_contract_version",
                "provider_attempt_count",
                "retry_count",
                "container_residue_count",
                "network_residue_count",
                "secret_file_residue_count",
                "temporary_file_residue_count",
            )
        ) and (
            not scoped
            or type(value.get("ledger_namespace_version")) is int
        )
        receipt_root = (
            self.historical_evidence_root
            / "receipts"
            / ledger.identity.request_id
        )
        receipt_root_present = receipt_root.exists() or receipt_root.is_symlink()
        declared_archive_status = value.get("receipt_archive_status")
        if (
            not receipt_root_present
            and declared_archive_status not in {None, "NOT_RUN"}
        ):
            raise OrchestratorError("historical_receipt_missing")
        if (
            not receipt_root_present
            and ledger.state is LedgerState.CLEANUP_COMPLETED
        ):
            raise OrchestratorError("historical_receipt_missing")
        archive_valid = (
            not receipt_root_present
            and declared_archive_status in {None, "NOT_RUN"}
        )
        if receipt_root_present:
            pinned_result: list[bool] = []
            receipts = self._validate_historical_receipt_directory(
                receipt_root,
                ledger.identity.request_id,
                pinned_result=pinned_result,
                expected_proxy_identity=(
                    _ARK_PROXY_RECEIPT_IDENTITY
                    if ledger.identity.provider == "volcengine_ark"
                    else _OPENAI_PROXY_RECEIPT_IDENTITY
                ),
            )
            pinned_legacy_bundle = pinned_result == [True]
            archive = receipts.get("archive-status.json")
            child_metadata = receipts.get("child-metadata.json")
            child_metadata_status = (
                archive.get("child_metadata_status")
                if archive is not None
                else None
            )
            child_metadata_valid = (
                child_metadata_status == "VALID"
                and child_metadata is not None
                and isinstance(child_metadata.get("relay"), dict)
                and isinstance(child_metadata.get("proxy"), dict)
            ) or (
                child_metadata_status == "PARTIAL_PRE_DISPATCH"
                and child_metadata is not None
                and child_metadata.get("relay") is None
                and isinstance(child_metadata.get("proxy"), dict)
            ) or (
                child_metadata_status == "MISSING"
                and child_metadata is None
            )
            child_terminal_reason = (
                archive.get("child_terminal_reason")
                if archive is not None
                else None
            )
            host_child_terminal_reason = (
                archive.get("host_child_terminal_reason")
                if archive is not None
                else None
            )
            terminal_reason_source = (
                archive.get("terminal_reason_source")
                if archive is not None
                else None
            )
            terminal_reason_valid = (
                terminal_reason_source is None
                and child_terminal_reason is None
                and host_child_terminal_reason is None
            ) or (
                terminal_reason_source == "relay_self"
                and host_child_terminal_reason == "RELAY_NONZERO_EXIT"
                and child_terminal_reason in _RELAY_TERMINAL_STATUSES
                and child_terminal_reason != "RELAY_COMPLETED"
            ) or (
                terminal_reason_source in {"host_child_process", "host_timeout"}
                and isinstance(host_child_terminal_reason, str)
                and bool(host_child_terminal_reason)
                and child_terminal_reason == host_child_terminal_reason
            )
            proxy_receipt = receipts.get("proxy-receipt.json")
            relay_receipt = receipts.get("relay-receipt.json")
            receipt_values = {
                component: receipt
                for component, receipt in (
                    ("proxy", proxy_receipt),
                    ("relay", relay_receipt),
                )
                if receipt is not None
            }
            relay_child = (
                child_metadata.get("relay")
                if child_metadata is not None
                else None
            )
            proxy_http_status = (
                proxy_receipt.get("provider_http_status")
                if proxy_receipt is not None
                else None
            )
            proxy_headers = (
                proxy_receipt.get("response_headers_received")
                if proxy_receipt is not None
                else None
            )
            relay_response = (
                relay_receipt.get("response_received")
                if relay_receipt is not None
                else None
            )
            http_semantics_valid = (
                (
                    proxy_receipt is None
                    or value.get("provider_http_status") == proxy_http_status
                    or (
                        pinned_legacy_bundle
                        and ledger.identity.request_id
                        == "9728fc1f156c4568a98cea9c05ec9fa7"
                        and value.get("provider_http_status") is None
                        and proxy_http_status == 401
                    )
                )
                and (
                    proxy_receipt is None
                    or not isinstance(proxy_headers, dict)
                    or (
                        proxy_headers.get("http_status")
                        == proxy_http_status
                    )
                )
                and (
                    relay_receipt is None
                    or proxy_receipt is None
                    or relay_receipt.get("proxy_http_status") == proxy_http_status
                    or (
                        pinned_legacy_bundle
                        and ledger.identity.request_id
                        == "9728fc1f156c4568a98cea9c05ec9fa7"
                        and proxy_receipt.get("response_category")
                        == "UPSTREAM_REJECTED"
                        and proxy_http_status == 401
                        and relay_receipt.get("proxy_http_status") == 502
                    )
                )
                and (
                    relay_receipt is None
                    or not isinstance(relay_response, dict)
                    or relay_response.get("http_status")
                    == relay_receipt.get("proxy_http_status")
                    or (
                        pinned_legacy_bundle
                        and ledger.identity.request_id
                        == "9728fc1f156c4568a98cea9c05ec9fa7"
                        and relay_response.get("occurred") is False
                        and relay_response.get("http_status") is None
                        and relay_receipt.get("proxy_http_status") == 502
                    )
                )
            )
            expected_host_child_reason = next(
                (
                    child["terminal_reason"]
                    for component in ("relay", "proxy")
                    if child_metadata is not None
                    and isinstance(
                        child := child_metadata.get(component),
                        dict,
                    )
                    and child.get("child_state")
                    not in {
                        ChildState.RUNNING.value,
                        ChildState.EXITED_ZERO.value,
                    }
                ),
                None,
            )
            receipt_semantics_valid = (
                (
                    proxy_receipt is None
                    or proxy_receipt.get("provider_attempt_count")
                    == ledger.provider_attempt_count
                )
                and (
                    relay_receipt is None
                    or relay_receipt.get("proxy_request_count")
                    == ledger.provider_attempt_count
                )
                and (
                    relay_receipt is None
                    or (
                        isinstance(relay_child, dict)
                        and (
                            (
                                relay_receipt.get("status") == "SUCCEEDED"
                                and relay_child.get("child_state")
                                == ChildState.EXITED_ZERO.value
                            )
                            or (
                                relay_receipt.get("status") == "REJECTED"
                                and relay_child.get("child_state")
                                != ChildState.EXITED_ZERO.value
                            )
                        )
                    )
                )
                and http_semantics_valid
                and archive is not None
                and archive.get("host_child_terminal_reason")
                == expected_host_child_reason
                and archive.get("last_proven_stage")
                == _select_last_proven_stage(receipt_values)
            )
            if not receipt_semantics_valid:
                raise OrchestratorError("historical_receipt_conflict")
            pre_dispatch_valid = (
                archive is not None
                and archive.get("archive_status") == "PRE_DISPATCH_ARCHIVED"
                and child_metadata_status == "PARTIAL_PRE_DISPATCH"
                and archive.get("proxy_receipt_status") == "VALID"
                and archive.get("relay_receipt_status") == "RECEIPT_MISSING"
                and archive.get("last_proven_stage") == "proxy_ready"
                and ledger.state is LedgerState.FAILED_BEFORE_DISPATCH
                and ledger.provider_attempt_count == 0
                and terminal_reason_source is None
                and isinstance(proxy_receipt, dict)
                and proxy_receipt.get("provider_attempt_count") == 0
                and proxy_receipt.get("response_category") == "PROXY_READY"
                and proxy_receipt.get("terminal_status") == "PROXY_READY"
            )
            archive_valid = (
                archive is not None
                and set(archive) == _ARCHIVE_STATUS_FIELDS
                and type(archive.get("archive_status_schema_version")) is int
                and archive.get("archive_status_schema_version") == 1
                and archive.get("request_id") == ledger.identity.request_id
                and type(archive.get("retry_count")) is int
                and archive.get("retry_count") == 0
                and archive.get("archive_status")
                in {
                    "ARCHIVED",
                    "CHILD_EVIDENCE_MISSING",
                    "PRE_DISPATCH_ARCHIVED",
                    "RECEIPT_MALFORMED",
                    "RECEIPT_MISSING",
                }
                and all(
                    isinstance(archive.get(field), str)
                    and bool(archive[field])
                    for field in (
                        "archive_status",
                        "child_metadata_status",
                        "proxy_receipt_status",
                        "relay_receipt_status",
                    )
                )
                and all(
                    archive.get(field) is None
                    or (
                        isinstance(archive.get(field), str)
                        and bool(archive[field])
                    )
                    for field in (
                        "child_terminal_reason",
                        "host_child_terminal_reason",
                        "last_proven_stage",
                    )
                )
                and (
                    archive.get("terminal_reason_source") is None
                    or archive.get("terminal_reason_source")
                    in _TERMINAL_REASON_SOURCES
                )
                and terminal_reason_valid
                and archive.get("archive_status")
                == value.get("receipt_archive_status")
                and archive.get("child_terminal_reason")
                == value.get("child_terminal_reason")
                and archive.get("last_proven_stage")
                == value.get("last_proven_stage")
                and child_metadata_valid
                and (
                    (archive.get("proxy_receipt_status") == "VALID")
                    == ("proxy-receipt.json" in receipts)
                )
                and (
                    (archive.get("relay_receipt_status") == "VALID")
                    == ("relay-receipt.json" in receipts)
                )
                and (
                    archive.get("archive_status") != "PRE_DISPATCH_ARCHIVED"
                    or pre_dispatch_valid
                )
            )
        if (
            raw != canonical_json_bytes(value)
            or frozenset(value) not in allowed_fields
            or value.get("runtime_evidence_schema_version") != 1
            or value.get("runtime_contract_version") != 1
            or value.get("request_id") != ledger.identity.request_id
            or value.get("symbol") != ledger.identity.symbol
            or value.get("approval_candidate_sha256")
            != ledger.identity.approval_candidate_sha256
            or value.get("proxy_image_id") != ledger.identity.proxy_image_id
            or value.get("relay_image_id") != ledger.identity.relay_image_id
            or value.get("orchestrator_source_sha256")
            != ledger.identity.orchestrator_source_sha256
            or value.get("timeout_contract_sha256")
            != ledger.identity.timeout_contract_sha256
            or value.get("attempt_ledger_state") != ledger.state.value
            or value.get("dispatch_started_at") != attempt.dispatch_started_at
            or value.get("response_received_at") != attempt.response_received_at
            or value.get("candidate_ready_at") != attempt.candidate_ready_at
            or value.get("host_validation_completed_at")
            != attempt.host_validation_completed_at
            or value.get("cleanup_completed_at") != attempt.cleanup_completed_at
            or value.get("provider_attempt_count") != ledger.provider_attempt_count
            or value.get("retry_count") != 0
            or (
                value.get("provider_http_status") is not None
                and (
                    isinstance(value.get("provider_http_status"), bool)
                    or not isinstance(value.get("provider_http_status"), int)
                )
            )
            or value.get("can_publish") is not False
            or value.get("manual_review") != "PENDING"
            or not route_valid
            or not observability_valid
            or not integer_fields_valid
            or not archive_valid
            or any(
                value.get(field) != 0
                for field in (
                    "container_residue_count",
                    "network_residue_count",
                    "secret_file_residue_count",
                    "temporary_file_residue_count",
                )
            )
            or (
                scoped
                and (
                    value.get("approval_scope_id") != ledger.approval_scope_id
                    or value.get("ledger_namespace_version") != 1
                )
            )
        ):
            raise OrchestratorError("historical_runtime_evidence_invalid")

    def _validate_historical_receipt_directory(
        self,
        request_root: Path,
        request_id: str,
        *,
        pinned_result: list[bool] | None = None,
        expected_proxy_identity: Mapping[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        request_root = _validate_report_directory(
            request_root,
            "historical_artifact_directory_invalid",
        )
        entries = sorted(request_root.iterdir(), key=lambda item: item.name)
        if not entries:
            raise OrchestratorError("historical_receipt_entry_invalid")
        raw_by_name: dict[str, bytes] = {}
        for path in entries:
            if path.name not in _HISTORICAL_RECEIPT_FILES:
                raise OrchestratorError("historical_receipt_entry_invalid")
            try:
                metadata = os.lstat(path)
            except OSError as exc:
                raise OrchestratorError("historical_receipt_invalid") from exc
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise OrchestratorError("historical_receipt_invalid")
            raw_by_name[path.name] = _read_report_regular(
                path,
                "historical_receipt_invalid",
            )
        pinned_legacy_bundle = _pinned_receipt_bundle_matches(
            raw_by_name,
            request_id,
        )
        if pinned_result is not None:
            pinned_result.append(pinned_legacy_bundle)
        legacy_adapter_bundle = (
            pinned_legacy_bundle and request_id == "3d3e6adbe5b74c98ad18a2efe0fd1d0c"
        )
        receipts: dict[str, dict[str, Any]] = {}
        for path in entries:
            raw = raw_by_name[path.name]
            value = _strict_json_bytes(raw, "historical_receipt_invalid")
            if (
                raw != canonical_json_bytes(value)
                or value.get("request_id") != request_id
            ):
                raise OrchestratorError("historical_receipt_invalid")
            if path.name in {"relay-receipt.json", "proxy-receipt.json"}:
                component: Literal["relay", "proxy"] = (
                    "relay" if path.name == "relay-receipt.json" else "proxy"
                )
                try:
                    if legacy_adapter_bundle and component == "relay":
                        value = {
                            **value,
                            "terminal_reason_source": "host_child_process",
                        }
                        _validate_stage_receipt(
                            value,
                            component=component,
                            request_id=request_id,
                        )
                    else:
                        _parse_stage_receipt_bytes(
                            raw,
                            component=component,
                            request_id=request_id,
                            expected_proxy_identity=(
                                expected_proxy_identity
                                if component == "proxy"
                                else None
                            ),
                        )
                except OrchestratorError as exc:
                    raise OrchestratorError(
                        "historical_receipt_invalid"
                    ) from exc
            elif path.name == "child-metadata.json" and not (
                _valid_historical_child_metadata(value, request_id)
            ):
                raise OrchestratorError("historical_receipt_invalid")
            elif path.name == "archive-status.json" and legacy_adapter_bundle:
                value = {
                    **value,
                    "host_child_terminal_reason": value.get(
                        "child_terminal_reason"
                    ),
                    "terminal_reason_source": "host_child_process",
                }
            receipts[path.name] = value
        return receipts

    def _historical_artifact_request_ids(self) -> dict[str, set[str]]:
        discovered: dict[str, set[str]] = {
            "evidence": set(),
            "rejected": set(),
            "inbox": set(),
            "receipts": set(),
        }
        for directory_name in ("evidence", "rejected", "inbox"):
            directory = self.historical_evidence_root / directory_name
            if not directory.exists() and not directory.is_symlink():
                continue
            directory = _validate_report_directory(
                directory,
                "historical_artifact_directory_invalid",
            )
            suffixes_by_request: dict[str, set[str]] = {}
            inbox_documents: dict[str, ClaimsDocument] = {}
            inbox_markdown: dict[str, bytes] = {}
            for path in sorted(directory.iterdir(), key=lambda item: item.name):
                allowed_suffixes = {".json", ".md"} if directory_name == "inbox" else {".json"}
                if path.suffix not in allowed_suffixes or _HEX_32.fullmatch(path.stem) is None:
                    raise OrchestratorError("historical_artifact_path_invalid")
                raw = _read_report_regular(path, "historical_artifact_invalid")
                if path.suffix == ".json":
                    try:
                        value = _strict_json_bytes(
                            raw,
                            "historical_artifact_invalid",
                        )
                    except OrchestratorError as exc:
                        raise OrchestratorError("historical_artifact_invalid") from exc
                    if raw != canonical_json_bytes(value):
                        raise OrchestratorError("historical_artifact_invalid")
                    if directory_name == "inbox":
                        try:
                            document = ClaimsDocument.model_validate(value)
                        except ValidationError as exc:
                            raise OrchestratorError(
                                "historical_artifact_invalid"
                            ) from exc
                        if raw != canonical_json_bytes(
                            document.model_dump(mode="json")
                        ):
                            raise OrchestratorError("historical_artifact_invalid")
                        inbox_documents[path.stem] = document
                    elif directory_name == "rejected":
                        if (
                            frozenset(value) != _REJECTION_FIELDS
                            or value.get("request_id") != path.stem
                            or value.get("can_publish") is not False
                            or value.get("route") != "rejected"
                            or not isinstance(value.get("error_category"), str)
                            or not value["error_category"]
                            or type(value.get("retry_count")) is not int
                            or value.get("retry_count") != 0
                        ):
                            raise OrchestratorError(
                                "historical_artifact_invalid"
                            )
                    elif value.get("request_id") != path.stem:
                        raise OrchestratorError("historical_artifact_invalid")
                elif directory_name == "inbox":
                    inbox_markdown[path.stem] = raw
                suffixes_by_request.setdefault(path.stem, set()).add(path.suffix)
                discovered[directory_name].add(path.stem)
            if directory_name == "inbox" and any(
                suffixes != {".json", ".md"}
                for suffixes in suffixes_by_request.values()
            ):
                raise OrchestratorError("historical_artifact_invalid")
            if directory_name == "inbox":
                try:
                    for request_id, document in inbox_documents.items():
                        validation = validate_claims_document(
                            self.repo_root,
                            document,
                        )
                        rendered = render_claims_document(document, validation)
                        raw_markdown = inbox_markdown.get(request_id)
                        if (
                            validation.status != CLAIMS_VALID
                            or validation.errors
                            or raw_markdown != rendered.encode("utf-8")
                            or validate_rendered_document(
                                document,
                                validation,
                                rendered,
                            )
                        ):
                            raise OrchestratorError(
                                "historical_artifact_invalid"
                            )
                except OrchestratorError:
                    raise
                except Exception as exc:
                    raise OrchestratorError(
                        "historical_artifact_invalid"
                    ) from exc

        receipts = self.historical_evidence_root / "receipts"
        if receipts.exists() or receipts.is_symlink():
            receipts = _validate_report_directory(
                receipts,
                "historical_artifact_directory_invalid",
            )
            for request_root in sorted(receipts.iterdir(), key=lambda item: item.name):
                if _HEX_32.fullmatch(request_root.name) is None:
                    raise OrchestratorError("historical_artifact_path_invalid")
                self._validate_historical_receipt_directory(
                    request_root,
                    request_root.name,
                )
                discovered["receipts"].add(request_root.name)
        return discovered

    @staticmethod
    def _blocked(scope_id: str, error: str) -> LedgerNamespacePreflight:
        return LedgerNamespacePreflight(
            status="GLOBAL_LEDGER_SAFETY_BLOCKED",
            current_scope_id=scope_id,
            current_scope_provider_attempts=0,
            current_scope_attempt_availability="BLOCKED",
            historical_requests=(),
            global_request_ids=(),
            errors=(error,),
        )

    def preflight(self, current_scope: ApprovalScopeIdentity) -> LedgerNamespacePreflight:
        current_scope_id = compute_approval_scope_id(current_scope)
        try:
            discovered = [*self._legacy_ledgers(), *self._scoped_ledgers()]
            historical_artifacts = self._historical_artifact_request_ids()
        except OrchestratorError as exc:
            return self._blocked(current_scope_id, str(exc))
        request_ids = [item.ledger.identity.request_id for item in discovered]
        if len(request_ids) != len(set(request_ids)):
            return self._blocked(current_scope_id, "global_request_id_collision")
        ledger_request_ids = set(request_ids)
        for category, artifact_request_ids in historical_artifacts.items():
            if not artifact_request_ids.issubset(ledger_request_ids):
                error = (
                    "historical_runtime_evidence_orphan"
                    if category == "evidence"
                    else "historical_artifact_orphan"
                )
                return self._blocked(current_scope_id, error)

        current_attempts = 0
        historical: list[str] = []
        for item in discovered:
            ledger = item.ledger
            if not item.scoped:
                if ledger.state not in _TERMINAL_STATES:
                    return self._blocked(
                        current_scope_id,
                        "historical_ledger_nonterminal",
                    )
                try:
                    resolved_scope = self._candidate_identity(
                        ledger.identity.approval_candidate_sha256
                    )
                except OrchestratorError as exc:
                    return self._blocked(current_scope_id, str(exc))
                if (
                    resolved_scope.symbol != ledger.identity.symbol
                    or resolved_scope.provider_id != ledger.identity.provider
                    or resolved_scope.endpoint_alias != ledger.identity.endpoint_alias
                ):
                    return self._blocked(
                        current_scope_id,
                        "historical_approval_identity_conflict",
                    )
                try:
                    self._validate_historical_evidence(ledger)
                except OrchestratorError as exc:
                    return self._blocked(current_scope_id, str(exc))
                ledger_scope_id = compute_approval_scope_id(resolved_scope)
            else:
                ledger_scope_id = ledger.approval_scope_id
                if ledger_scope_id != current_scope_id:
                    if ledger.state not in _TERMINAL_STATES:
                        return self._blocked(
                            current_scope_id,
                            "historical_ledger_nonterminal",
                        )
                    try:
                        resolved_scope = self._candidate_identity(
                            ledger.identity.approval_candidate_sha256
                        )
                    except OrchestratorError as exc:
                        return self._blocked(current_scope_id, str(exc))
                    if (
                        compute_approval_scope_id(resolved_scope) != ledger_scope_id
                        or resolved_scope.symbol != ledger.identity.symbol
                        or resolved_scope.provider_id != ledger.identity.provider
                        or resolved_scope.exact_model_id != ledger.exact_model_id
                        or resolved_scope.endpoint_alias
                        != ledger.identity.endpoint_alias
                    ):
                        return self._blocked(
                            current_scope_id,
                            "historical_approval_identity_conflict",
                        )
                    try:
                        self._validate_historical_evidence(ledger)
                    except OrchestratorError as exc:
                        return self._blocked(current_scope_id, str(exc))

            if ledger_scope_id == current_scope_id:
                if ledger.state is LedgerState.FAILED_BEFORE_DISPATCH:
                    try:
                        self._validate_historical_evidence(ledger)
                    except OrchestratorError as exc:
                        return self._blocked(current_scope_id, str(exc))
                    request_id = ledger.identity.request_id
                    if (
                        request_id in historical_artifacts["inbox"]
                        or request_id not in historical_artifacts["rejected"]
                    ):
                        return self._blocked(
                            current_scope_id,
                            "historical_route_artifact_conflict",
                        )
                current_attempts += ledger.provider_attempt_count
                if ledger.state not in _TERMINAL_STATES:
                    return self._blocked(current_scope_id, "current_scope_nonterminal")
            else:
                request_id = ledger.identity.request_id
                has_inbox = request_id in historical_artifacts["inbox"]
                has_rejection = request_id in historical_artifacts["rejected"]
                if (
                    ledger.state is LedgerState.CLEANUP_COMPLETED
                    and (not has_inbox or has_rejection)
                ) or (
                    ledger.state is not LedgerState.CLEANUP_COMPLETED
                    and (has_inbox or not has_rejection)
                ):
                    return self._blocked(
                        current_scope_id,
                        "historical_route_artifact_conflict",
                    )
                historical.append(ledger.identity.request_id)

        if current_attempts > 0:
            return LedgerNamespacePreflight(
                status="CURRENT_SCOPE_ATTEMPT_CONSUMED",
                current_scope_id=current_scope_id,
                current_scope_provider_attempts=current_attempts,
                current_scope_attempt_availability="BLOCKED",
                historical_requests=tuple(sorted(historical)),
                global_request_ids=tuple(sorted(request_ids)),
            )
        return LedgerNamespacePreflight(
            status="READY",
            current_scope_id=current_scope_id,
            current_scope_provider_attempts=0,
            current_scope_attempt_availability="AVAILABLE",
            historical_requests=tuple(sorted(historical)),
            global_request_ids=tuple(sorted(request_ids)),
        )

    def assert_request_id_available(
        self,
        request_id: str,
        *,
        preflight: LedgerNamespacePreflight,
    ) -> None:
        if _HEX_32.fullmatch(request_id) is None:
            raise OrchestratorError("request_id_invalid")
        if preflight.status != "READY":
            raise OrchestratorError(preflight.status)
        if request_id in preflight.global_request_ids:
            raise OrchestratorError("REQUEST_ID_COLLISION_BLOCKED")

    def prove_failed_before_dispatch(
        self,
        approval_candidate_sha256: str,
        approval_scope_id: str,
        request_id: str,
    ) -> bool:
        if (
            _HEX_64.fullmatch(approval_candidate_sha256) is None
            or _HEX_64.fullmatch(approval_scope_id) is None
            or _HEX_32.fullmatch(request_id) is None
        ):
            return False
        try:
            scope_root = _validate_state_root(
                self.attempts_root / approval_scope_id
            )
            request_root = _validate_state_root(scope_root / request_id)
            if {item.name for item in request_root.iterdir()} != {
                _SCOPED_LEDGER_NAME
            }:
                return False
            ledger = _decode_scoped_ledger(
                _read_regular(
                    request_root / _SCOPED_LEDGER_NAME,
                    "scoped_ledger_target_invalid",
                )
            )
            if (
                ledger.approval_scope_id != approval_scope_id
                or ledger.identity.request_id != request_id
                or ledger.identity.approval_candidate_sha256
                != approval_candidate_sha256
                or ledger.state is not LedgerState.FAILED_BEFORE_DISPATCH
                or ledger.provider_attempt_count != 0
            ):
                return False
            self._validate_historical_evidence(ledger)
            artifacts = self._historical_artifact_request_ids()
            return (
                request_id not in artifacts["inbox"]
                and request_id in artifacts["rejected"]
            )
        except (OSError, OrchestratorError):
            return False


class ExclusiveCanaryLock:
    """A nonblocking process lock acquired before request-id generation."""

    def __init__(
        self,
        state_root: Path,
        *,
        attempts_root: Path | None = None,
        stale_lock_validator: Callable[[str, str, str], bool] | None = None,
        approval_candidate_sha256: str,
        approval_scope_id: str | None = None,
        provider_id: Literal["openai", "volcengine_ark"] = "openai",
        endpoint_alias: Literal[
            "openai_responses_v1",
            "ark_responses_cn_beijing_v1",
        ] = "openai_responses_v1",
        pid: int,
        started_at: str,
    ) -> None:
        self.root = _validate_state_root(state_root)
        if _HEX_64.fullmatch(approval_candidate_sha256) is None:
            raise OrchestratorError("approval_candidate_sha256_invalid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise OrchestratorError("lock_pid_invalid")
        if approval_scope_id is not None and _HEX_64.fullmatch(approval_scope_id) is None:
            raise OrchestratorError("approval_scope_id_invalid")
        self.approval_candidate_sha256 = approval_candidate_sha256
        self.approval_scope_id = approval_scope_id
        allowed_identity_pairs = {
            ("openai", "openai_responses_v1"),
            ("volcengine_ark", "ark_responses_cn_beijing_v1"),
        }
        if (provider_id, endpoint_alias) not in allowed_identity_pairs:
            raise OrchestratorError("lock_provider_identity_invalid")
        self.provider_id = provider_id
        self.endpoint_alias = endpoint_alias
        self.attempts_root = (
            _validate_state_root(attempts_root)
            if attempts_root is not None
            else None
        )
        self.stale_lock_validator = stale_lock_validator
        self.pid = pid
        self.started_at = started_at
        self.path = self.root / _LOCK_NAME
        self._descriptor: int | None = None

    def _read_locked(self, descriptor: int) -> bytes:
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, _MAXIMUM_LEDGER_BYTES + 1)
        if len(raw) > _MAXIMUM_LEDGER_BYTES:
            raise OrchestratorError("stale_lock_state_unknown")
        return raw

    def _stale_lock_is_recoverable(self, raw: bytes) -> bool:
        if not raw.strip():
            return True
        try:
            value = _strict_json_bytes(raw, "stale_lock_state_unknown")
        except OrchestratorError:
            return False
        if raw != _canonical_bytes(value):
            return False
        expected_fields = {
            "lock_schema_version",
            "pid",
            "started_at",
            "symbol",
            "provider",
            "endpoint_alias",
            "approval_candidate_sha256",
        }
        expected_schema = 1
        if self.approval_scope_id is not None:
            expected_fields.update({"approval_scope_id", "request_id"})
            expected_schema = 3
        if (
            not isinstance(value, dict)
            or set(value) != expected_fields
            or value.get("lock_schema_version") != expected_schema
            or value.get("symbol") != "000403.SZ"
            or value.get("provider") != self.provider_id
            or value.get("endpoint_alias") != self.endpoint_alias
            or value.get("approval_candidate_sha256") != self.approval_candidate_sha256
            or value.get("approval_scope_id") != self.approval_scope_id
            or isinstance(value.get("pid"), bool)
            or not isinstance(value.get("pid"), int)
            or value["pid"] <= 0
            or not isinstance(value.get("started_at"), str)
            or not value["started_at"]
            or value["started_at"] != value["started_at"].strip()
        ):
            return False
        if self.approval_scope_id is not None:
            request_id = value.get("request_id")
            if (
                self.stale_lock_validator is None
                or not isinstance(request_id, str)
                or _HEX_32.fullmatch(request_id) is None
            ):
                return False
            try:
                return self.stale_lock_validator(
                    self.approval_candidate_sha256,
                    self.approval_scope_id,
                    request_id,
                )
            except Exception:
                return False
        try:
            ledger = AttemptLedgerStore(self.root).load()
        except OrchestratorError:
            return False
        return (
            ledger.state is LedgerState.FAILED_BEFORE_DISPATCH
            and ledger.identity.approval_candidate_sha256
            == self.approval_candidate_sha256
        )

    def _payload(self, *, request_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "lock_schema_version": (
                3 if request_id is not None else 2
                if self.approval_scope_id is not None
                else 1
            ),
            "pid": self.pid,
            "started_at": self.started_at,
            "symbol": "000403.SZ",
            "provider": self.provider_id,
            "endpoint_alias": self.endpoint_alias,
            "approval_candidate_sha256": self.approval_candidate_sha256,
        }
        if self.approval_scope_id is not None:
            payload["approval_scope_id"] = self.approval_scope_id
        if request_id is not None:
            payload["request_id"] = request_id
        return payload

    @staticmethod
    def _write_locked(descriptor: int, payload: dict[str, Any]) -> None:
        rendered = _canonical_bytes(payload)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        offset = 0
        while offset < len(rendered):
            written = os.write(descriptor, rendered[offset:])
            if written <= 0:
                raise OrchestratorError("canary_lock_write_failed")
            offset += written
        os.fsync(descriptor)

    def acquire(self) -> None:
        if self._descriptor is not None:
            raise OrchestratorError("canary_lock_already_acquired")
        try:
            descriptor = os.open(
                self.path,
                os.O_RDWR | os.O_CREAT | _NOFOLLOW,
                0o600,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise OrchestratorError("canary_lock_target_invalid")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise OrchestratorError("canary_lock_held") from exc
                raise
            raw = self._read_locked(descriptor)
            if not self._stale_lock_is_recoverable(raw):
                raise OrchestratorError("stale_lock_state_unknown")
            self._write_locked(descriptor, self._payload())
            self._descriptor = descriptor
        except OrchestratorError:
            if "descriptor" in locals():
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            raise
        except OSError as exc:
            if "descriptor" in locals():
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            raise OrchestratorError("canary_lock_failed") from exc

    def bind_request_id(self, request_id: str) -> None:
        descriptor = self._descriptor
        if (
            descriptor is None
            or self.approval_scope_id is None
            or _HEX_32.fullmatch(request_id) is None
        ):
            raise OrchestratorError("canary_lock_request_binding_invalid")
        raw = self._read_locked(descriptor)
        if raw != _canonical_bytes(self._payload()):
            raise OrchestratorError("canary_lock_request_binding_invalid")
        self._write_locked(descriptor, self._payload(request_id=request_id))

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            self._descriptor = None

    def __enter__(self) -> ExclusiveCanaryLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class ApprovedCanaryArtifacts(_StrictFrozenModel):
    """Exact immutable identities approved before a future live run."""

    approval_candidate_sha256: str
    facts_sha256: str
    projection_sha256: str
    proxy_image_id: str
    relay_image_id: str
    timeout_contract_sha256: str
    readiness_contract_sha256: str
    orchestrator_source_sha256: str

    @field_validator(
        "approval_candidate_sha256",
        "facts_sha256",
        "projection_sha256",
        "timeout_contract_sha256",
        "readiness_contract_sha256",
        "orchestrator_source_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("artifact_sha256_invalid")
        return value

    @field_validator("proxy_image_id", "relay_image_id")
    @classmethod
    def validate_artifact_image_id(cls, value: str) -> str:
        if _IMAGE_ID.fullmatch(value) is None:
            raise ValueError("artifact_image_id_invalid")
        return value


@dataclass(frozen=True)
class CanaryOrchestratorConfig:
    repo_root: Path
    state_root: Path
    attempts_root: Path
    work_root: Path
    canary_output_root: Path
    historical_evidence_root: Path
    candidate_roots: tuple[Path, ...]
    facts_path: Path

    def __post_init__(self) -> None:
        repo = self.repo_root.resolve(strict=True)
        if not repo.is_dir():
            raise OrchestratorError("repo_root_invalid")
        object.__setattr__(self, "repo_root", repo)
        for field in (
            "state_root",
            "attempts_root",
            "work_root",
            "canary_output_root",
        ):
            path = _validate_state_root(getattr(self, field))
            object.__setattr__(self, field, path)
        historical = _validate_report_directory(
            self.historical_evidence_root,
            "historical_evidence_root_invalid",
        )
        object.__setattr__(self, "historical_evidence_root", historical)
        if not self.candidate_roots:
            raise OrchestratorError("candidate_roots_missing")
        candidate_roots = tuple(
            _validate_report_directory(path, "candidate_root_invalid")
            for path in self.candidate_roots
        )
        object.__setattr__(self, "candidate_roots", candidate_roots)
        facts = Path(os.path.abspath(os.fspath(self.facts_path)))
        _assert_no_symlink_components(facts)
        try:
            metadata = os.lstat(facts)
        except OSError as exc:
            raise OrchestratorError("facts_file_invalid") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OrchestratorError("facts_file_invalid")
        object.__setattr__(self, "facts_path", facts)


@dataclass(frozen=True)
class CanaryRuntimeContext:
    request_id: str
    request_path: Path
    projection_path: Path
    output_dir: Path
    proxy_output_dir: Path
    receipt_archive_dir: Path
    secret_path: Path
    proxy_image_id: str
    relay_image_id: str
    runtime_contract: Mapping[str, Any]


@dataclass(frozen=True)
class BackendDispatchResult:
    provider_http_status: int | None
    response_received: bool


@dataclass(frozen=True)
class CleanupResult:
    completed: bool
    timed_out: bool = False
    container_residue_count: int = 0
    network_residue_count: int = 0


@dataclass(frozen=True)
class ArchiveResult:
    completed: bool
    status: str
    archive_dir: Path | None = None
    last_proven_stage: str | None = None
    child_terminal_reason: str | None = None


class CanaryBackend(Protocol):
    def inspect_global_residue(self, *, deadline: float) -> CleanupResult: ...

    def prepare(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> None: ...

    def dispatch(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> BackendDispatchResult: ...

    def archive_evidence(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> ArchiveResult: ...

    def cleanup(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> CleanupResult: ...


class BackendError(OrchestratorError):
    def __init__(
        self,
        category: str,
        *,
        unknown_dispatch_state: bool = False,
    ) -> None:
        self.category = category
        self.unknown_dispatch_state = unknown_dispatch_state
        super().__init__(category)


@dataclass(frozen=True)
class CanaryRunResult:
    terminal_state: str
    request_id: str | None
    approval_scope_id: str | None
    attempt_ledger_state: LedgerState | None
    provider_attempt_count: int
    retry_count: int
    provider_http_status: int | None
    route: str
    host_validation_status: str
    container_residue_count: int
    network_residue_count: int
    secret_file_residue_count: int
    temporary_file_residue_count: int
    receipt_archive_status: str
    last_proven_stage: str | None
    child_terminal_reason: str | None
    error_category: str | None


def _write_private_file(path: Path, raw: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise OrchestratorError("private_file_parent_invalid")
    if path.exists() or path.is_symlink():
        raise OrchestratorError("private_file_already_exists")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
        )
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OrchestratorError("private_file_write_failed")
            offset += written
        os.fsync(descriptor)
    except OrchestratorError:
        raise
    except OSError as exc:
        raise OrchestratorError("private_file_write_failed") from exc
    finally:
        if "descriptor" in locals():
            os.close(descriptor)


def _publish_private_file(path: Path, raw: bytes) -> None:
    parent_descriptor = _open_private_directory(
        path.parent,
        "artifact_parent_invalid",
        allow_read_only_public=True,
    )
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.partial"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=parent_descriptor,
        )
        _write_descriptor(descriptor, raw, "artifact_write_failed")
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise OrchestratorError("artifact_already_exists") from exc
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except OrchestratorError:
        raise
    except OSError as exc:
        raise OrchestratorError("artifact_publish_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.close(parent_descriptor)


def _publish_private_directory_no_clobber(
    staging: Path,
    target: Path,
) -> None:
    if staging.parent != target.parent or staging.name == target.name:
        raise OrchestratorError("artifact_directory_layout_invalid")
    parent_descriptor = _open_private_directory(
        target.parent,
        "artifact_directory_parent_invalid",
    )
    staging_descriptor = _open_private_directory(
        staging,
        "artifact_directory_staging_invalid",
    )
    target_descriptor = -1
    try:
        entries = sorted(
            os.listdir(staging_descriptor),
            key=lambda name: (name == "archive-status.json", name),
        )
        if not entries:
            raise OrchestratorError("artifact_directory_staging_invalid")
        source_metadata: dict[str, os.stat_result] = {}
        for name in entries:
            if name in {".", ".."} or Path(name).name != name:
                raise OrchestratorError("artifact_directory_staging_invalid")
            metadata = os.stat(
                name,
                dir_fd=staging_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise OrchestratorError("artifact_directory_staging_invalid")
            source_metadata[name] = metadata
        try:
            os.mkdir(target.name, mode=0o700, dir_fd=parent_descriptor)
        except FileExistsError as exc:
            raise OrchestratorError(
                "artifact_directory_already_exists"
            ) from exc
        created_metadata = os.stat(
            target.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(created_metadata.st_mode)
            or stat.S_IMODE(created_metadata.st_mode) != 0o700
        ):
            raise OrchestratorError("artifact_directory_publish_failed")
        target_descriptor = os.open(
            target.name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        target_metadata = os.fstat(target_descriptor)
        if (
            not stat.S_ISDIR(target_metadata.st_mode)
            or stat.S_IMODE(target_metadata.st_mode) != 0o700
            or (target_metadata.st_dev, target_metadata.st_ino)
            != (created_metadata.st_dev, created_metadata.st_ino)
        ):
            raise OrchestratorError("artifact_directory_publish_failed")
        for name in entries:
            os.link(
                name,
                name,
                src_dir_fd=staging_descriptor,
                dst_dir_fd=target_descriptor,
                follow_symlinks=False,
            )
            published = os.stat(
                name,
                dir_fd=target_descriptor,
                follow_symlinks=False,
            )
            source = source_metadata[name]
            if (published.st_dev, published.st_ino) != (
                source.st_dev,
                source.st_ino,
            ):
                raise OrchestratorError("artifact_directory_publish_failed")
        os.fsync(target_descriptor)
        published_target = os.stat(
            target.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (published_target.st_dev, published_target.st_ino) != (
            target_metadata.st_dev,
            target_metadata.st_ino,
        ):
            raise OrchestratorError("artifact_directory_publish_failed")
        os.fsync(parent_descriptor)
    except OrchestratorError:
        raise
    except OSError as exc:
        raise OrchestratorError("artifact_directory_publish_failed") from exc
    finally:
        if target_descriptor >= 0:
            os.close(target_descriptor)
        os.close(staging_descriptor)
        os.close(parent_descriptor)


def _strict_json_bytes(raw: bytes, category: str) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_key")
            value[key] = item
        return value

    try:
        text = raw.decode("utf-8")
        value, end = json.JSONDecoder(
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        ).raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise OrchestratorError(category) from exc
    if text[end:].strip() or not isinstance(value, dict):
        raise OrchestratorError(category)
    return value


def _strict_json_object(path: Path, category: str) -> dict[str, Any]:
    return _strict_json_bytes(
        _read_regular_unrestricted(path, category),
        category,
    )


def _read_regular_unrestricted(path: Path, category: str) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or not 0 < before.st_size <= _MAXIMUM_LEDGER_BYTES * 16
    ):
        raise OrchestratorError(category)
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, _MAXIMUM_LEDGER_BYTES * 16 + 1)
        after = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or len(raw) != opened.st_size
        or len(raw) > _MAXIMUM_LEDGER_BYTES * 16
        or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise OrchestratorError(category)
    return raw


def _read_trusted_runtime_approval(path: Path) -> bytes:
    category = "runtime_approval_invalid"
    try:
        directory = os.lstat(path.parent)
        before = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    current_uid = os.geteuid()
    if (
        stat.S_ISLNK(directory.st_mode)
        or not stat.S_ISDIR(directory.st_mode)
        or stat.S_IMODE(directory.st_mode) != 0o700
        or directory.st_uid != current_uid
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_uid != current_uid
    ):
        raise OrchestratorError(category)
    raw = _read_regular_unrestricted(path, category)
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise OrchestratorError(category) from exc
    if (
        (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or stat.S_IMODE(after.st_mode) != 0o600
        or after.st_uid != current_uid
    ):
        raise OrchestratorError(category)
    return raw


def _valid_stage_event(value: object, expected_name: str) -> bool:
    if not isinstance(value, dict) or set(value) != _EVENT_FIELDS:
        return False
    occurred = value.get("occurred")
    wall_time = value.get("wall_time")
    monotonic_ns = value.get("monotonic_ns")
    http_status = value.get("http_status")
    byte_count = value.get("byte_count")
    if value.get("event") != expected_name or not isinstance(occurred, bool):
        return False
    if not occurred:
        return all(
            item is None
            for item in (wall_time, monotonic_ns, http_status, byte_count)
        )
    if (
        not isinstance(wall_time, str)
        or not wall_time
        or isinstance(monotonic_ns, bool)
        or not isinstance(monotonic_ns, int)
        or monotonic_ns < 0
    ):
        return False
    if http_status is not None and (
        isinstance(http_status, bool)
        or not isinstance(http_status, int)
        or not 100 <= http_status <= 599
    ):
        return False
    return not (
        byte_count is not None
        and (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
        )
    )


def _valid_stage_event_chain(
    value: Mapping[str, Any],
    events: tuple[str, ...],
) -> bool:
    missing_seen = False
    previous_monotonic_ns: int | None = None
    for name in events:
        event = value.get(name)
        if not _valid_stage_event(event, name):
            return False
        assert isinstance(event, dict)
        if event["occurred"] is not True:
            missing_seen = True
            continue
        if missing_seen:
            return False
        monotonic_ns = event["monotonic_ns"]
        assert isinstance(monotonic_ns, int)
        if (
            previous_monotonic_ns is not None
            and monotonic_ns <= previous_monotonic_ns
        ):
            return False
        previous_monotonic_ns = monotonic_ns
    return True


def _valid_historical_child_process(value: object, component: str) -> bool:
    if not isinstance(value, dict) or frozenset(value) != _CHILD_PROCESS_FIELDS:
        return False
    state = value.get("child_state")
    started_ns = value.get("started_monotonic_ns")
    exited_ns = value.get("exited_monotonic_ns")
    elapsed_ms = value.get("elapsed_ms")
    exit_code = value.get("exit_code")
    signal_value = value.get("signal")
    stderr = value.get("stderr")
    if (
        value.get("component") != component
        or state not in {item.value for item in ChildState}
        or value.get("terminal_reason")
        != f"{component.upper()}_{state.removeprefix('CHILD_')}"
        or not isinstance(value.get("started_at"), str)
        or not value["started_at"]
        or not isinstance(value.get("exited_at"), str)
        or not value["exited_at"]
        or type(started_ns) is not int
        or type(exited_ns) is not int
        or type(elapsed_ms) is not int
        or started_ns < 0
        or exited_ns < started_ns
        or elapsed_ms != (exited_ns - started_ns) // 1_000_000
        or (
            exit_code is not None
            and type(exit_code) is not int
        )
        or (
            signal_value is not None
            and type(signal_value) is not int
        )
        or not isinstance(stderr, dict)
        or frozenset(stderr) != _CHILD_STDERR_FIELDS
        or stderr.get("stderr_excerpt") is not None
        or stderr.get("stderr_sha256") is not None
        or type(stderr.get("stderr_byte_count")) is not int
        or stderr.get("stderr_byte_count") != 0
        or type(stderr.get("stderr_truncated")) is not bool
        or type(stderr.get("stderr_redacted")) is not bool
        or type(stderr.get("sensitive_hit_count")) is not int
        or stderr.get("sensitive_hit_count") < 0
        or (
            stderr.get("sensitive_hit_count") > 0
            and stderr.get("stderr_redacted") is not True
        )
    ):
        return False
    return not (
        (state == ChildState.EXITED_ZERO.value and exit_code != 0)
        or (
            state == ChildState.NONZERO_EXIT.value
            and (exit_code is None or exit_code == 0)
        )
        or (
            state == ChildState.SIGNALLED.value
            and (signal_value is None or signal_value <= 0)
        )
    )


def _valid_historical_child_metadata(
    value: object,
    request_id: str,
) -> bool:
    if (
        not isinstance(value, dict)
        or frozenset(value) != _CHILD_METADATA_FIELDS
        or type(value.get("child_metadata_schema_version")) is not int
        or value.get("child_metadata_schema_version") != 1
        or value.get("request_id") != request_id
    ):
        return False
    present = 0
    for component in ("relay", "proxy"):
        child = value.get(component)
        if child is None:
            continue
        present += 1
        if not _valid_historical_child_process(child, component):
            return False
    return present > 0


def _validate_stage_receipt(
    value: dict[str, Any],
    *,
    component: Literal["relay", "proxy"],
    request_id: str,
    expected_proxy_identity: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    events = _RELAY_EVENTS if component == "relay" else _PROXY_EVENTS
    fields = (
        _RELAY_RECEIPT_FIELDS
        if component == "relay"
        else _PROXY_RECEIPT_FIELDS
    )
    proxy_contract_invalid = component == "proxy" and (
        any(
            type(value.get(field)) is not bool
            for field in (
                "auth_present",
                "method_allowed",
                "path_allowed",
                "redirect_followed",
                "tls_verification",
            )
        )
        or type(value.get("provider_attempt_count")) is not int
        or value.get("provider_attempt_count") not in {0, 1}
        or (
            value.get("provider_http_status") is not None
            and (
                type(value.get("provider_http_status")) is not int
                or not 100 <= value["provider_http_status"] <= 599
            )
        )
        or any(
            value.get(field) is not None
            and (
                type(value.get(field)) is not int
                or value[field] < 0
            )
            for field in ("response_bytes", "response_size")
        )
        or not isinstance(value.get("response_category"), str)
        or not value.get("response_category")
        or not isinstance(value.get("terminal_status"), str)
        or not value.get("terminal_status")
    )
    relay_contract_invalid = component == "relay" and (
        value.get("terminal_reason_source") not in _TERMINAL_REASON_SOURCES
        or value.get("terminal_status") not in _RELAY_TERMINAL_STATUSES
        or type(value.get("exit_code")) is not int
        or type(value.get("proxy_request_count")) is not int
        or value.get("proxy_request_count") < 0
        or (
            value.get("response_size") is not None
            and (
                type(value.get("response_size")) is not int
                or value.get("response_size") < 0
            )
        )
        or (
            value.get("proxy_http_status") is not None
            and (
                type(value.get("proxy_http_status")) is not int
                or not 100 <= value["proxy_http_status"] <= 599
            )
        )
        or not isinstance(value.get("started_at"), str)
        or not value.get("started_at")
        or not isinstance(value.get("completed_at"), str)
        or not value.get("completed_at")
        or (
            value.get("response_sha256") is not None
            and (
                not isinstance(value.get("response_sha256"), str)
                or _HEX_64.fullmatch(value["response_sha256"]) is None
            )
        )
        or (
            value.get("status") == "SUCCEEDED"
            and (
                value.get("exit_code") != 0
                or value.get("terminal_status") != "RELAY_COMPLETED"
                or value.get("error_category") is not None
            )
        )
        or (
            value.get("status") == "REJECTED"
            and (
                value.get("exit_code") != 2
                or value.get("terminal_status") == "RELAY_COMPLETED"
                or not isinstance(value.get("error_category"), str)
            )
        )
        or value.get("status") not in {"SUCCEEDED", "REJECTED"}
    )
    observed_fields = set(value)
    fields_valid = observed_fields == fields
    proxy_identity_invalid = False
    if component == "proxy":
        expected_identity = (
            dict(expected_proxy_identity)
            if expected_proxy_identity is not None
            else None
        )
        if expected_identity == _OPENAI_PROXY_RECEIPT_IDENTITY:
            fields_valid = observed_fields == _PROXY_RECEIPT_FIELDS
        elif expected_identity == _ARK_PROXY_RECEIPT_IDENTITY:
            fields_valid = observed_fields == _ARK_PROXY_RECEIPT_FIELDS
            proxy_identity_invalid = not fields_valid or any(
                value.get(field) != expected
                for field, expected in _ARK_PROXY_RECEIPT_IDENTITY.items()
            )
        elif expected_identity is not None:
            proxy_identity_invalid = True
        elif observed_fields == _ARK_PROXY_RECEIPT_FIELDS:
            fields_valid = True
            proxy_identity_invalid = any(
                value.get(field) != expected
                for field, expected in _ARK_PROXY_RECEIPT_IDENTITY.items()
            )
    if (
        not fields_valid
        or type(value.get("receipt_schema_version")) is not int
        or value.get("receipt_schema_version") != 2
        or value.get("request_id") != request_id
        or value.get("component") != component
        or type(value.get("retry_count")) is not int
        or value.get("retry_count") != 0
        or any(
            not _valid_stage_event(value.get(name), name) for name in events
        )
        or not _valid_stage_event_chain(value, events)
        or proxy_contract_invalid
        or relay_contract_invalid
        or proxy_identity_invalid
    ):
        raise OrchestratorError("RECEIPT_MALFORMED")
    serialized = canonical_json_bytes(value).decode("utf-8")
    if _SENSITIVE_EVIDENCE.search(serialized):
        raise OrchestratorError("RECEIPT_MALFORMED")
    return tuple(
        name
        for name in events
        if isinstance(value.get(name), dict)
        and value[name].get("occurred") is True
    )


def _load_stage_receipt(
    path: Path,
    *,
    component: Literal["relay", "proxy"],
    request_id: str,
    expected_proxy_identity: Mapping[str, str] | None = None,
) -> tuple[bytes | None, dict[str, Any] | None, str, tuple[str, ...]]:
    if not path.exists() or path.is_symlink():
        return None, None, "RECEIPT_MISSING", ()
    try:
        raw = _read_regular_unrestricted(path, "RECEIPT_MALFORMED")
        value, events = _parse_stage_receipt_bytes(
            raw,
            component=component,
            request_id=request_id,
            expected_proxy_identity=expected_proxy_identity,
        )
    except OrchestratorError:
        return None, None, "RECEIPT_MALFORMED", ()
    return raw, value, "VALID", events


def _parse_stage_receipt_bytes(
    raw: bytes,
    *,
    component: Literal["relay", "proxy"],
    request_id: str,
    expected_proxy_identity: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    value = _strict_json_bytes(raw, "RECEIPT_MALFORMED")
    events = _validate_stage_receipt(
        value,
        component=component,
        request_id=request_id,
        expected_proxy_identity=expected_proxy_identity,
    )
    if raw != canonical_json_bytes(value):
        raise OrchestratorError("RECEIPT_MALFORMED")
    return value, events


def _abnormal_child_reason(
    child_evidence: Mapping[str, ChildProcessEvidence],
) -> str | None:
    for component in ("relay", "proxy"):
        evidence = child_evidence.get(component)
        if evidence is not None and evidence.child_state not in {
            ChildState.RUNNING,
            ChildState.EXITED_ZERO,
        }:
            return evidence.terminal_reason
    return None


def _select_last_proven_stage(
    receipt_values: Mapping[str, Mapping[str, Any]],
) -> str | None:
    rank = {name: index for index, name in enumerate(_STAGE_TIE_ORDER)}
    candidates: list[tuple[int, str, int, str]] = []
    for component, events in (("proxy", _PROXY_EVENTS), ("relay", _RELAY_EVENTS)):
        receipt = receipt_values.get(component)
        if receipt is None:
            continue
        for name in events:
            event = receipt.get(name)
            if not isinstance(event, Mapping) or event.get("occurred") is not True:
                continue
            monotonic_ns = event.get("monotonic_ns")
            wall_time = event.get("wall_time")
            if not isinstance(monotonic_ns, int) or not isinstance(wall_time, str):
                continue
            candidates.append((monotonic_ns, wall_time, rank[name], name))
    return max(candidates)[-1] if candidates else None


def _archive_stage_evidence(
    context: CanaryRuntimeContext,
    child_evidence: Mapping[str, ChildProcessEvidence],
    validated_receipts: Mapping[str, bytes] | None = None,
    *,
    expected_proxy_identity: Mapping[str, str] | None = None,
) -> ArchiveResult:
    sources = {
        "relay": context.output_dir / "relay-receipt.json",
        "proxy": context.proxy_output_dir / "proxy-receipt.json",
    }
    loaded: dict[str, bytes] = {}
    receipt_values: dict[str, dict[str, Any]] = {}
    statuses: dict[str, str] = {}
    proven_events: dict[str, tuple[str, ...]] = {}
    for component in ("relay", "proxy"):
        _value: dict[str, Any] | None = None
        cached = (validated_receipts or {}).get(component)
        if cached is None:
            raw, _value, status, events = _load_stage_receipt(
                sources[component],
                component=component,
                request_id=context.request_id,
                expected_proxy_identity=(
                    expected_proxy_identity if component == "proxy" else None
                ),
            )
        else:
            try:
                _value, events = _parse_stage_receipt_bytes(
                    cached,
                    component=component,
                    request_id=context.request_id,
                    expected_proxy_identity=(
                        expected_proxy_identity if component == "proxy" else None
                    ),
                )
            except OrchestratorError:
                raw, status, events = None, "RECEIPT_MALFORMED", ()
            else:
                raw, status = cached, "VALID"
        statuses[component] = status
        proven_events[component] = events
        if _value is not None:
            receipt_values[component] = _value
        if raw is not None:
            loaded[component] = raw

    proxy_receipt = receipt_values.get("proxy")
    pre_dispatch_archive = (
        statuses.get("proxy") == "VALID"
        and statuses.get("relay") == "RECEIPT_MISSING"
        and proxy_receipt is not None
        and proxy_receipt.get("provider_attempt_count") == 0
        and proxy_receipt.get("response_category") == "PROXY_READY"
        and proxy_receipt.get("terminal_status") == "PROXY_READY"
        and proven_events.get("proxy") == ("process_started", "proxy_ready")
        and "relay" not in child_evidence
    )
    if "RECEIPT_MALFORMED" in statuses.values():
        archive_status = "RECEIPT_MALFORMED"
    elif pre_dispatch_archive:
        archive_status = "PRE_DISPATCH_ARCHIVED"
    elif "RECEIPT_MISSING" in statuses.values():
        archive_status = "RECEIPT_MISSING"
    elif set(child_evidence) != {"relay", "proxy"}:
        archive_status = "CHILD_EVIDENCE_MISSING"
    else:
        archive_status = "ARCHIVED"

    last_proven_stage = _select_last_proven_stage(receipt_values)
    host_child_terminal_reason = _abnormal_child_reason(child_evidence)
    child_terminal_reason = host_child_terminal_reason
    terminal_reason_source = (
        "host_timeout"
        if host_child_terminal_reason == "ORCHESTRATOR_TIMEOUT"
        else "host_child_process"
        if host_child_terminal_reason is not None
        else None
    )
    relay_receipt = receipt_values.get("relay")
    if (
        host_child_terminal_reason == "RELAY_NONZERO_EXIT"
        and relay_receipt is not None
        and relay_receipt.get("status") == "REJECTED"
        and relay_receipt.get("terminal_reason_source") == "relay_self"
        and relay_receipt.get("terminal_status") in _RELAY_TERMINAL_STATUSES
    ):
        child_terminal_reason = relay_receipt["terminal_status"]
        terminal_reason_source = "relay_self"
    child_payload = {
        "child_metadata_schema_version": 1,
        "request_id": context.request_id,
        "relay": (
            asdict(child_evidence["relay"])
            if "relay" in child_evidence
            else None
        ),
        "proxy": (
            asdict(child_evidence["proxy"])
            if "proxy" in child_evidence
            else None
        ),
    }
    status_payload = {
        "archive_status_schema_version": 1,
        "request_id": context.request_id,
        "archive_status": archive_status,
        "relay_receipt_status": statuses["relay"],
        "proxy_receipt_status": statuses["proxy"],
        "child_metadata_status": (
            "VALID"
            if set(child_evidence) == {"relay", "proxy"}
            else (
                "PARTIAL_PRE_DISPATCH"
                if pre_dispatch_archive and set(child_evidence) == {"proxy"}
                else "MISSING"
            )
        ),
        "last_proven_stage": last_proven_stage,
        "child_terminal_reason": child_terminal_reason,
        "host_child_terminal_reason": host_child_terminal_reason,
        "terminal_reason_source": terminal_reason_source,
        "retry_count": 0,
    }
    for value in (child_payload, status_payload):
        if _SENSITIVE_EVIDENCE.search(
            canonical_json_bytes(value).decode("utf-8")
        ):
            raise OrchestratorError("RECEIPT_SECURITY_BLOCKED")

    target = context.receipt_archive_dir
    parent = target.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (
        parent.is_symlink()
        or not parent.is_dir()
        or stat.S_IMODE(parent.stat().st_mode) != 0o700
        or target.exists()
        or target.is_symlink()
    ):
        raise OrchestratorError("RECEIPT_ARCHIVE_FAILED")
    staging = parent / f".{target.name}.{uuid.uuid4().hex}.partial"
    try:
        staging.mkdir(mode=0o700)
        for component, raw in loaded.items():
            _write_private_file(staging / f"{component}-receipt.json", raw)
        _write_private_file(
            staging / "child-metadata.json",
            canonical_json_bytes(child_payload),
        )
        _write_private_file(
            staging / "archive-status.json",
            canonical_json_bytes(status_payload),
        )
        staging_descriptor = os.open(
            staging,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
        )
        try:
            os.fsync(staging_descriptor)
        finally:
            os.close(staging_descriptor)
        _publish_private_directory_no_clobber(staging, target)
    except (OSError, OrchestratorError) as exc:
        raise OrchestratorError("RECEIPT_ARCHIVE_FAILED") from exc
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(staging)
    return ArchiveResult(
        completed=True,
        status=archive_status,
        archive_dir=target,
        last_proven_stage=last_proven_stage,
        child_terminal_reason=child_terminal_reason,
    )


def _collect_candidate(
    context: CanaryRuntimeContext,
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    entries = list(context.output_dir.iterdir())
    if any(path.name.endswith(".partial") for path in entries):
        raise OrchestratorError("CANDIDATE_PARTIAL_WRITE")
    candidate_files = [
        path
        for path in entries
        if path.name.startswith("candidate")
        and path.name.endswith(".json")
        and path.name != "candidate.ready.json"
    ]
    if len(candidate_files) > 1:
        raise OrchestratorError("CANDIDATE_MULTIPLE")
    candidate_path = context.output_dir / "candidate.json"
    marker_path = context.output_dir / "candidate.ready.json"
    if len(candidate_files) != 1 or not candidate_path.is_file():
        raise OrchestratorError("CANDIDATE_NOT_PRODUCED")
    if not marker_path.is_file():
        raise OrchestratorError("CANDIDATE_NOT_PRODUCED")
    candidate_raw = _read_regular_unrestricted(
        candidate_path,
        "CANDIDATE_INVALID",
    )
    marker = _strict_json_object(marker_path, "CANDIDATE_INVALID")
    if (
        set(marker)
        != {"candidate_sha256", "projection_sha256", "request_id"}
        or marker.get("candidate_sha256")
        != hashlib.sha256(candidate_raw).hexdigest()
        or marker.get("projection_sha256") != projection.get("projection_sha256")
        or marker.get("request_id") != context.request_id
    ):
        raise OrchestratorError("CANDIDATE_INVALID")
    try:
        value = json.loads(candidate_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestratorError("CANDIDATE_INVALID") from exc
    if not isinstance(value, dict):
        raise OrchestratorError("CANDIDATE_INVALID")
    if (
        value.get("request_id") != context.request_id
        or value.get("symbol") != "000403.SZ"
        or value.get("projection_sha256") != projection.get("projection_sha256")
        or not isinstance(value.get("claims_candidate"), dict)
    ):
        raise OrchestratorError("CANDIDATE_INVALID")
    return value


def _host_validate_and_render(
    repo_root: Path,
    candidate: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    evidence = validate_isolated_candidate(
        repo_root,
        candidate,
        projection,
    )
    if (
        evidence.errors
        or evidence.claims_status != CLAIMS_VALID
        or evidence.renderer_status != "RENDERED_VALID"
        or evidence.trading_claim_count != 0
        or evidence.sensitive_hit_count != 0
    ):
        raise OrchestratorError("CANDIDATE_INVALID")
    try:
        parsed = WorkerClaimsCandidate.model_validate(candidate)
        scope = projection["safe_facts"]["scope"]
        document = ClaimsDocument.model_validate(
            {
                "claims_schema_version": 1,
                "source_system": "tickflow-stock-panel",
                "symbol": parsed.symbol,
                "name": parsed.name,
                "trade_date": parsed.trade_date,
                "timezone": parsed.timezone,
                "facts_binding": {
                    "facts_file": "reports/phase2_facts/000403SZ_facts.json",
                    "facts_sha256": projection["facts_sha256"],
                    "facts_schema_version": 1,
                },
                "scope": {
                    "market_scope": scope["market_scope"],
                    "financial_scope": scope["financial_scope"],
                    "news_scope": scope["news_scope"],
                    "industry_scope": "unavailable",
                },
                "claims": [
                    item.model_dump(mode="json") for item in parsed.claims
                ],
                "vendor_pending": projection["safe_facts"]["vendor_pending"],
                "trading_advice": False,
            }
        )
        validation = validate_claims_document(repo_root, document)
        if validation.status != CLAIMS_VALID:
            raise OrchestratorError("CANDIDATE_INVALID")
        rendered = render_claims_document(document, validation)
        if validate_rendered_document(document, validation, rendered):
            raise OrchestratorError("CANDIDATE_INVALID")
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        if isinstance(exc, OrchestratorError):
            raise
        raise OrchestratorError("CANDIDATE_INVALID") from exc
    return document.model_dump(mode="json"), rendered


def _publish_route(
    root: Path,
    request_id: str,
    document: Mapping[str, Any],
    rendered: str,
) -> tuple[Path, Path]:
    inbox = root / "inbox"
    inbox.mkdir(mode=0o700, exist_ok=True)
    if inbox.is_symlink() or not inbox.is_dir():
        raise OrchestratorError("inbox_invalid")
    json_path = inbox / f"{request_id}.json"
    markdown_path = inbox / f"{request_id}.md"
    _publish_private_file(json_path, canonical_json_bytes(document))
    try:
        _publish_private_file(markdown_path, rendered.encode("utf-8"))
    except Exception:
        json_path.unlink(missing_ok=True)
        raise
    return json_path, markdown_path


def _remove_route(paths: tuple[Path, Path] | None) -> None:
    if paths is None:
        return
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def _publish_rejection(root: Path, request_id: str, category: str) -> None:
    rejected = root / "rejected"
    rejected.mkdir(mode=0o700, exist_ok=True)
    path = rejected / f"{request_id}.json"
    if path.exists():
        return
    _publish_private_file(
        path,
        canonical_json_bytes(
            {
                "can_publish": False,
                "error_category": category,
                "request_id": request_id,
                "retry_count": 0,
                "route": "rejected",
            }
        ),
    )


def _result(
    *,
    terminal_state: str,
    request_id: str | None,
    ledger: AttemptLedger | ApprovalScopedAttemptLedger | None,
    approval_scope_id: str | None = None,
    provider_http_status: int | None = None,
    route: str = "none",
    host_validation_status: str = "NOT_RUN",
    cleanup: CleanupResult | None = None,
    secret_file_residue_count: int = 0,
    temporary_file_residue_count: int = 0,
    archive: ArchiveResult | None = None,
    error_category: str | None = None,
) -> CanaryRunResult:
    cleanup = cleanup or CleanupResult(completed=True)
    archive = archive or ArchiveResult(completed=False, status="NOT_RUN")
    return CanaryRunResult(
        terminal_state=terminal_state,
        request_id=request_id,
        approval_scope_id=(
            ledger.approval_scope_id
            if isinstance(ledger, ApprovalScopedAttemptLedger)
            else approval_scope_id
        ),
        attempt_ledger_state=ledger.state if ledger else None,
        provider_attempt_count=ledger.provider_attempt_count if ledger else 0,
        retry_count=0,
        provider_http_status=provider_http_status,
        route=route,
        host_validation_status=host_validation_status,
        container_residue_count=cleanup.container_residue_count,
        network_residue_count=cleanup.network_residue_count,
        secret_file_residue_count=secret_file_residue_count,
        temporary_file_residue_count=temporary_file_residue_count,
        receipt_archive_status=archive.status,
        last_proven_stage=archive.last_proven_stage,
        child_terminal_reason=archive.child_terminal_reason,
        error_category=error_category,
    )


def _terminal_category(error: BaseException) -> str:
    value = str(error).strip()
    if not value:
        return "INTERNAL_ERROR"
    return value.upper()


def _publish_runtime_evidence(
    root: Path,
    artifacts: ApprovedCanaryArtifacts,
    result: CanaryRunResult,
    ledger: AttemptLedger | ApprovalScopedAttemptLedger,
) -> None:
    evidence_root = root / "evidence"
    evidence_root.mkdir(mode=0o700, exist_ok=True)
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise OrchestratorError("runtime_evidence_root_invalid")
    value = {
        "runtime_evidence_schema_version": 1,
        "runtime_contract_version": 1,
        "request_id": ledger.identity.request_id,
        "symbol": ledger.identity.symbol,
        "approval_scope_id": (
            ledger.approval_scope_id
            if isinstance(ledger, ApprovalScopedAttemptLedger)
            else None
        ),
        "ledger_namespace_version": (
            ledger.ledger_namespace_version
            if isinstance(ledger, ApprovalScopedAttemptLedger)
            else None
        ),
        "approval_candidate_sha256": artifacts.approval_candidate_sha256,
        "proxy_image_id": artifacts.proxy_image_id,
        "relay_image_id": artifacts.relay_image_id,
        "orchestrator_source_sha256": artifacts.orchestrator_source_sha256,
        "timeout_contract_sha256": artifacts.timeout_contract_sha256,
        "attempt_ledger_state": ledger.state,
        "dispatch_started_at": ledger.dispatch_started_at,
        "response_received_at": ledger.response_received_at,
        "candidate_ready_at": ledger.candidate_ready_at,
        "host_validation_completed_at": ledger.host_validation_completed_at,
        "cleanup_completed_at": ledger.cleanup_completed_at,
        "provider_attempt_count": result.provider_attempt_count,
        "retry_count": result.retry_count,
        "provider_http_status": result.provider_http_status,
        "terminal_state": result.terminal_state,
        "route": result.route,
        "can_publish": False,
        "manual_review": "PENDING",
        "container_residue_count": result.container_residue_count,
        "network_residue_count": result.network_residue_count,
        "secret_file_residue_count": result.secret_file_residue_count,
        "temporary_file_residue_count": result.temporary_file_residue_count,
        "receipt_archive_status": result.receipt_archive_status,
        "last_proven_stage": result.last_proven_stage,
        "child_terminal_reason": result.child_terminal_reason,
    }
    _publish_private_file(
        evidence_root / f"{ledger.identity.request_id}.json",
        canonical_json_bytes(value),
    )


def _with_runtime_evidence(
    root: Path,
    artifacts: ApprovedCanaryArtifacts,
    result: CanaryRunResult,
    ledger: AttemptLedger | ApprovalScopedAttemptLedger | None,
    *,
    success_route_paths: tuple[Path, Path] | None = None,
) -> CanaryRunResult:
    if ledger is None:
        return result
    try:
        _publish_runtime_evidence(root, artifacts, result, ledger)
    except OrchestratorError:
        _remove_route(success_route_paths)
        with contextlib.suppress(OrchestratorError):
            _publish_rejection(
                root,
                ledger.identity.request_id,
                "RUNTIME_EVIDENCE_PUBLISH_FAILED",
            )
        return replace(
            result,
            terminal_state="RUNTIME_EVIDENCE_PUBLISH_FAILED",
            error_category="RUNTIME_EVIDENCE_PUBLISH_FAILED",
            route="rejected",
        )
    return result


def run_single_symbol_canary(
    *,
    config: CanaryOrchestratorConfig,
    artifacts: ApprovedCanaryArtifacts,
    projection: Mapping[str, Any],
    backend: CanaryBackend,
    artifact_verifier: Callable[[ApprovedCanaryArtifacts], None],
    secret_reader: Callable[[], str],
    request_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    wall_clock: Callable[[], str],
    monotonic: Callable[[], float] = time.monotonic,
    provider_id: Literal["openai", "volcengine_ark"] = "openai",
    exact_model_id: Literal[
        "gpt-5.6-terra",
        "doubao-seed-2-1-turbo-260628",
    ] = _EXACT_MODEL_ID,
    endpoint_alias: Literal[
        "openai_responses_v1",
        "ark_responses_cn_beijing_v1",
    ] = "openai_responses_v1",
) -> CanaryRunResult:
    """Execute one complete attempt; no code path retries a dispatch."""
    request_id: str | None = None
    ledger: ApprovalScopedAttemptLedger | None = None
    store: ApprovalScopedAttemptLedgerStore | None = None
    context: CanaryRuntimeContext | None = None
    cleanup = CleanupResult(completed=True)
    archive = ArchiveResult(completed=False, status="NOT_RUN")
    route_paths: tuple[Path, Path] | None = None
    provider_http_status: int | None = None
    host_validation_status = "NOT_RUN"
    prepared = False
    workdir: Path | None = None

    def archive_runtime_evidence() -> ArchiveResult:
        nonlocal archive
        if archive.status != "NOT_RUN":
            return archive
        if (
            context is None
            or ledger is None
        ):
            return archive
        try:
            archive = backend.archive_evidence(
                context,
                deadline=(
                    monotonic()
                    + context.runtime_contract["cleanup_timeout_seconds"]
                ),
            )
        except Exception:
            archive = ArchiveResult(
                completed=False,
                status="RECEIPT_ARCHIVE_FAILED",
            )
        return archive

    def finalize_runtime() -> tuple[CleanupResult, int, int]:
        nonlocal cleanup, prepared, workdir
        if prepared and context is not None:
            try:
                cleanup = backend.cleanup(
                    context,
                    deadline=(
                        monotonic()
                        + context.runtime_contract["cleanup_timeout_seconds"]
                    ),
                )
            except Exception:
                cleanup = CleanupResult(completed=False, timed_out=True)
            prepared = False
        secret_residue = 0
        if context is not None:
            with contextlib.suppress(OSError):
                context.secret_path.unlink(missing_ok=True)
            secret_residue = int(context.secret_path.exists())
        temporary_residue = 0
        if workdir is not None:
            try:
                shutil.rmtree(workdir)
            except OSError:
                temporary_residue = int(workdir.exists())
            else:
                workdir = None
        return cleanup, secret_residue, temporary_residue
    if (provider_id, exact_model_id, endpoint_alias) not in {
        ("openai", _EXACT_MODEL_ID, "openai_responses_v1"),
        (
            "volcengine_ark",
            "doubao-seed-2-1-turbo-260628",
            "ark_responses_cn_beijing_v1",
        ),
    }:
        raise OrchestratorError("provider_identity_invalid")
    scope = ApprovalScopeIdentity(
        approval_candidate_sha256=artifacts.approval_candidate_sha256,
        symbol="000403.SZ",
        provider_id=provider_id,
        exact_model_id=exact_model_id,
        endpoint_alias=endpoint_alias,
    )
    scope_id = compute_approval_scope_id(scope)
    namespace = ApprovalScopedLedgerNamespace(
        repo_root=config.repo_root,
        attempts_root=config.attempts_root,
        legacy_state_root=config.state_root,
        historical_evidence_root=config.historical_evidence_root,
        candidate_roots=config.candidate_roots,
    )
    lock = ExclusiveCanaryLock(
        config.state_root,
        attempts_root=config.attempts_root,
        stale_lock_validator=namespace.prove_failed_before_dispatch,
        approval_candidate_sha256=artifacts.approval_candidate_sha256,
        approval_scope_id=scope_id,
        provider_id=provider_id,
        endpoint_alias=endpoint_alias,
        pid=os.getpid(),
        started_at=wall_clock(),
    )
    try:
        lock.acquire()
    except OrchestratorError as exc:
        category = _terminal_category(exc)
        return _result(
            terminal_state=category,
            request_id=None,
            ledger=None,
            approval_scope_id=scope_id,
            error_category=category,
        )

    try:
        artifact_verifier(artifacts)
        facts_raw = _read_regular_unrestricted(
            config.facts_path,
            "FACTS_FILE_INVALID",
        )
        if (
            hashlib.sha256(facts_raw).hexdigest() != artifacts.facts_sha256
            or projection.get("facts_sha256") != artifacts.facts_sha256
            or projection.get("projection_sha256")
            != artifacts.projection_sha256
            or compute_projection_sha256(projection)
            != artifacts.projection_sha256
        ):
            raise OrchestratorError("PROJECTION_BINDING_INVALID")
        runtime = load_runtime_contract().model_dump(mode="json")
        if runtime_contract_sha256() != artifacts.timeout_contract_sha256:
            raise OrchestratorError("TIMEOUT_CONTRACT_MISMATCH")

        preflight = namespace.preflight(scope)
        if preflight.status != "READY":
            return _result(
                terminal_state=preflight.status,
                request_id=None,
                ledger=None,
                approval_scope_id=scope_id,
                error_category=preflight.status,
            )

        residue_deadline = monotonic() + float(
            runtime["cleanup_timeout_seconds"]
        )
        residue = backend.inspect_global_residue(deadline=residue_deadline)
        if (
            residue.timed_out
            or not residue.completed
            or residue.container_residue_count
            or residue.network_residue_count
        ):
            return _result(
                terminal_state="GLOBAL_LEDGER_SAFETY_BLOCKED",
                request_id=None,
                ledger=None,
                approval_scope_id=scope_id,
                cleanup=residue,
                error_category="GLOBAL_LEDGER_SAFETY_BLOCKED",
            )

        request_id = request_id_factory()
        try:
            namespace.assert_request_id_available(request_id, preflight=preflight)
        except OrchestratorError as exc:
            category = _terminal_category(exc)
            return _result(
                terminal_state=category,
                request_id=None,
                ledger=None,
                approval_scope_id=scope_id,
                error_category=category,
            )
        identity = CanaryRunIdentity(
            request_id=request_id,
            symbol="000403.SZ",
            trade_date="2026-07-31",
            provider=provider_id,
            endpoint_alias=endpoint_alias,
            **artifacts.model_dump(
                mode="python",
                exclude={"readiness_contract_sha256"},
            ),
        )
        store = ApprovalScopedAttemptLedgerStore(
            config.attempts_root,
            scope=scope,
            request_id=request_id,
        )
        ledger = store.prepare(identity, timestamp=wall_clock())
        lock.bind_request_id(request_id)
        workdir = Path(tempfile.mkdtemp(prefix="phase2-canary-", dir=config.work_root))
        workdir.chmod(0o700)
        input_dir = workdir / "input"
        output_dir = workdir / "output"
        proxy_output_dir = workdir / "proxy-output"
        input_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o733)
        proxy_output_dir.mkdir(mode=0o733)
        receipts_root = config.canary_output_root / "receipts"
        receipts_root.mkdir(mode=0o700, exist_ok=True)
        if (
            receipts_root.is_symlink()
            or not receipts_root.is_dir()
            or stat.S_IMODE(receipts_root.stat().st_mode) != 0o700
        ):
            raise OrchestratorError("RECEIPT_ARCHIVE_ROOT_INVALID")
        projection_path = input_dir / "projection.json"
        request_path = input_dir / "request.json"
        secret_path = workdir / "provider-auth"
        _write_private_file(projection_path, canonical_json_bytes(projection))
        projection_path.chmod(0o444)
        request = build_relay_request(
            projection,
            request_id=request_id,
        ).model_dump(mode="json")
        _write_private_file(request_path, canonical_json_bytes(request))
        request_path.chmod(0o444)
        secret = secret_reader()
        if (
            not isinstance(secret, str)
            or not secret
            or secret != secret.strip()
            or any(character in secret for character in ("\x00", "\r", "\n"))
        ):
            raise OrchestratorError("SECRET_INVALID")
        _write_private_file(secret_path, secret.encode("utf-8"))
        del secret
        context = CanaryRuntimeContext(
            request_id=request_id,
            request_path=request_path,
            projection_path=projection_path,
            output_dir=output_dir,
            proxy_output_dir=proxy_output_dir,
            receipt_archive_dir=receipts_root / request_id,
            secret_path=secret_path,
            proxy_image_id=artifacts.proxy_image_id,
            relay_image_id=artifacts.relay_image_id,
            runtime_contract=runtime,
        )
        host_deadline = (
            monotonic() + runtime["host_orchestrator_timeout_seconds"]
        )
        prepared = True
        backend.prepare(context, deadline=host_deadline)
        ledger = store.transition(
            LedgerState.NETWORK_DISPATCH_STARTED,
            timestamp=wall_clock(),
        )
        dispatch = backend.dispatch(context, deadline=host_deadline)
        if monotonic() > host_deadline:
            raise BackendError("ORCHESTRATOR_TIMEOUT")
        provider_http_status = dispatch.provider_http_status
        if dispatch.response_received:
            ledger = store.transition(
                LedgerState.PROVIDER_RESPONSE_RECEIVED,
                timestamp=wall_clock(),
            )
        relay = _collect_candidate(context, projection)
        if monotonic() > host_deadline:
            raise BackendError("ORCHESTRATOR_TIMEOUT")
        ledger = store.transition(
            LedgerState.CANDIDATE_COLLECTED,
            timestamp=wall_clock(),
        )
        document, rendered = _host_validate_and_render(
            config.repo_root,
            relay["claims_candidate"],
            projection,
        )
        if monotonic() > host_deadline:
            raise BackendError("ORCHESTRATOR_TIMEOUT")
        host_validation_status = "VALID"
        route_paths = _publish_route(
            config.canary_output_root,
            request_id,
            document,
            rendered,
        )
        ledger = store.transition(
            LedgerState.HOST_VALIDATION_COMPLETED,
            timestamp=wall_clock(),
        )

        archive = archive_runtime_evidence()
        if not archive.completed or archive.status != "ARCHIVED":
            raise BackendError(archive.status)
        cleanup, secret_residue, temporary_residue = finalize_runtime()
        if cleanup.timed_out or not cleanup.completed:
            raise OrchestratorError("CLEANUP_TIMEOUT")
        if cleanup.container_residue_count or cleanup.network_residue_count:
            raise OrchestratorError("CLEANUP_RESIDUE")
        if secret_residue:
            raise OrchestratorError("SECRET_FILE_RESIDUE")
        if temporary_residue:
            raise OrchestratorError("TEMPORARY_FILE_RESIDUE")
        ledger = store.transition(
            LedgerState.CLEANUP_COMPLETED,
            timestamp=wall_clock(),
        )
        return _with_runtime_evidence(
            config.canary_output_root,
            artifacts,
            _result(
                terminal_state="SUCCEEDED",
                request_id=request_id,
                ledger=ledger,
                provider_http_status=provider_http_status,
                route="inbox",
                host_validation_status=host_validation_status,
                cleanup=cleanup,
                secret_file_residue_count=secret_residue,
                temporary_file_residue_count=temporary_residue,
                archive=archive,
            ),
            ledger,
            success_route_paths=route_paths,
        )
    except BackendError as exc:
        archive = archive_runtime_evidence()
        category = (
            "ATTEMPT_CONSUMED_UNKNOWN"
            if exc.unknown_dispatch_state
            else exc.category
        )
        unknown_dispatch_state = exc.unknown_dispatch_state
        if archive.child_terminal_reason is not None:
            category = archive.child_terminal_reason
            unknown_dispatch_state = category.endswith("_EXIT_UNKNOWN")
        if (
            ledger is not None
            and ledger.provider_attempt_count > 0
            and archive.status
            not in {
                "NOT_RUN",
                "ARCHIVED",
                "PRE_DISPATCH_ARCHIVED",
            }
        ):
            category = archive.status
            unknown_dispatch_state = False
        if store is not None and ledger is not None:
            target = (
                LedgerState.ATTEMPT_CONSUMED_UNKNOWN
                if unknown_dispatch_state
                else (
                    LedgerState.FAILED_AFTER_DISPATCH
                    if ledger.provider_attempt_count
                    else LedgerState.FAILED_BEFORE_DISPATCH
                )
            )
            if ledger.state not in _TERMINAL_STATES:
                ledger = store.transition(target, timestamp=wall_clock())
        _remove_route(route_paths)
        cleanup, secret_residue, temporary_residue = finalize_runtime()
        if not unknown_dispatch_state:
            if cleanup.timed_out or not cleanup.completed:
                category = "CLEANUP_TIMEOUT"
            elif cleanup.container_residue_count or cleanup.network_residue_count:
                category = "CLEANUP_RESIDUE"
            elif secret_residue:
                category = "SECRET_FILE_RESIDUE"
            elif temporary_residue:
                category = "TEMPORARY_FILE_RESIDUE"
        if request_id is not None:
            _publish_rejection(config.canary_output_root, request_id, category)
        return _with_runtime_evidence(
            config.canary_output_root,
            artifacts,
            _result(
                terminal_state=category,
                request_id=request_id,
                ledger=ledger,
                provider_http_status=provider_http_status,
                route="rejected" if request_id else "none",
                host_validation_status=host_validation_status,
                cleanup=cleanup,
                secret_file_residue_count=secret_residue,
                temporary_file_residue_count=temporary_residue,
                archive=archive,
                error_category=category,
            ),
            ledger,
        )
    except Exception as exc:
        archive = archive_runtime_evidence()
        category = _terminal_category(exc)
        if archive.child_terminal_reason is not None:
            category = archive.child_terminal_reason
        if (
            ledger is not None
            and ledger.provider_attempt_count > 0
            and archive.status
            not in {
                "NOT_RUN",
                "ARCHIVED",
                "PRE_DISPATCH_ARCHIVED",
            }
        ):
            category = archive.status
        if store is not None and ledger is not None and ledger.state not in _TERMINAL_STATES:
            target = (
                LedgerState.FAILED_AFTER_DISPATCH
                if ledger.provider_attempt_count
                else LedgerState.FAILED_BEFORE_DISPATCH
            )
            ledger = store.transition(target, timestamp=wall_clock())
        _remove_route(route_paths)
        cleanup, secret_residue, temporary_residue = finalize_runtime()
        if cleanup.timed_out or not cleanup.completed:
            category = "CLEANUP_TIMEOUT"
        elif cleanup.container_residue_count or cleanup.network_residue_count:
            category = "CLEANUP_RESIDUE"
        elif secret_residue:
            category = "SECRET_FILE_RESIDUE"
        elif temporary_residue:
            category = "TEMPORARY_FILE_RESIDUE"
        if request_id is not None:
            with contextlib.suppress(OrchestratorError):
                _publish_rejection(config.canary_output_root, request_id, category)
        return _with_runtime_evidence(
            config.canary_output_root,
            artifacts,
            _result(
                terminal_state=category,
                request_id=request_id,
                ledger=ledger,
                provider_http_status=provider_http_status,
                route="rejected" if request_id else "none",
                host_validation_status=host_validation_status,
                cleanup=cleanup,
                secret_file_residue_count=secret_residue,
                temporary_file_residue_count=temporary_residue,
                archive=archive,
                error_category=category,
            ),
            ledger,
        )
    finally:
        if prepared or workdir is not None:
            finalize_runtime()
        lock.release()


def _mock_stage_event(
    name: str,
    *,
    occurred: bool,
    index: int,
    http_status: int | None = None,
    byte_count: int | None = None,
) -> dict[str, Any]:
    return {
        "event": name,
        "occurred": occurred,
        "wall_time": (
            f"2026-08-08T08:23:{index:02d}.000000Z" if occurred else None
        ),
        "monotonic_ns": index * 1_000_000_000 if occurred else None,
        "http_status": http_status if occurred else None,
        "byte_count": byte_count if occurred else None,
    }


class MockCanaryBackend:
    """Deterministic local fault injector; it never opens a socket."""

    FAULT_CATALOG = (
        "provider_1s",
        "provider_59s",
        "provider_60s",
        "provider_over_60s",
        "relay_exit_before_candidate",
        "proxy_exit_after_request",
        "host_crash_before_dispatch",
        "host_crash_after_dispatch",
        "host_crash_after_response",
        "candidate_partial_write",
        "candidate_rename_failure",
        "candidate_missing",
        "candidate_multiple",
        "nonzero_container_exit",
        "cleanup_timeout",
        "duplicate_request_id",
        "concurrent_orchestrator",
        "stale_ledger",
        "stale_lock",
        "runtime_residue",
    )

    def __init__(self, *, scenario: str, relay_response: Mapping[str, Any]) -> None:
        if scenario not in self.FAULT_CATALOG:
            raise OrchestratorError("mock_scenario_invalid")
        self.scenario = scenario
        self.relay_response = dict(relay_response)
        self.prepare_count = 0
        self.dispatch_count = 0
        self.archive_count = 0
        self.cleanup_count = 0
        self.lifecycle: list[str] = []
        self._child_evidence: dict[str, ChildProcessEvidence] = {}
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    def inspect_global_residue(self, *, deadline: float) -> CleanupResult:
        del deadline
        return CleanupResult(completed=True)

    def prepare(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> None:
        del deadline
        self.lifecycle.append("prepare")
        self.prepare_count += 1
        if self.scenario == "host_crash_before_dispatch":
            proxy_events = {
                name: _mock_stage_event(
                    name,
                    occurred=name in {"process_started", "proxy_ready"},
                    index=index,
                )
                for index, name in enumerate(_PROXY_EVENTS, start=1)
            }
            _write_private_file(
                context.proxy_output_dir / "proxy-receipt.json",
                canonical_json_bytes(
                    {
                        "receipt_schema_version": 2,
                        "request_id": context.request_id,
                        "component": "proxy",
                        "method_allowed": False,
                        "path_allowed": False,
                        "auth_present": False,
                        "tls_verification": True,
                        "redirect_followed": False,
                        "provider_attempt_count": 0,
                        "retry_count": 0,
                        "provider_http_status": None,
                        "response_category": "PROXY_READY",
                        "response_size": None,
                        "response_bytes": None,
                        "terminal_status": "PROXY_READY",
                        **proxy_events,
                    }
                ),
            )
            timing = ChildExecutionTiming(
                started_at="2026-08-08T08:23:20.000000Z",
                exited_at="2026-08-08T08:23:20.000000Z",
                started_monotonic_ns=20_000_000_000,
                exited_monotonic_ns=20_000_000_000,
            )
            self._child_evidence["proxy"] = classify_running_child(
                "proxy",
                timing,
            )
            raise BackendError("HOST_CRASH_BEFORE_DISPATCH")

    def _publish(self, context: CanaryRuntimeContext, value: Mapping[str, Any]) -> None:
        raw = canonical_json_bytes(value)
        candidate = context.output_dir / "candidate.json"
        marker = context.output_dir / "candidate.ready.json"
        _publish_private_file(candidate, raw)
        _publish_private_file(
            marker,
            canonical_json_bytes(
                {
                    "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                    "projection_sha256": value["projection_sha256"],
                    "request_id": value["request_id"],
                }
            ),
        )

    def _write_receipts(self, context: CanaryRuntimeContext) -> None:
        relay_failed = self.scenario in {
            "provider_over_60s",
            "relay_exit_before_candidate",
            "nonzero_container_exit",
            "proxy_exit_after_request",
            "host_crash_after_dispatch",
            "host_crash_after_response",
        }
        candidate_published = self.scenario not in {
            "provider_over_60s",
            "relay_exit_before_candidate",
            "nonzero_container_exit",
            "proxy_exit_after_request",
            "host_crash_after_dispatch",
            "host_crash_after_response",
            "candidate_partial_write",
            "candidate_rename_failure",
            "candidate_missing",
        }
        proxy_events: dict[str, dict[str, Any]] = {}
        for index, name in enumerate(_PROXY_EVENTS, start=1):
            occurred = not (
                self.scenario == "proxy_exit_after_request"
                and name
                in {
                    "request_write_completed",
                    "response_headers_received",
                    "response_body_completed",
                }
            )
            proxy_events[name] = _mock_stage_event(
                name,
                occurred=occurred,
                index=index,
                http_status=(
                    200
                    if name
                    in {"response_headers_received", "response_body_completed"}
                    and occurred
                    else None
                ),
                byte_count=(
                    1
                    if name in {"request_write_completed", "response_body_completed"}
                    and occurred
                    else None
                ),
            )
        proxy_category = (
            "REQUEST_WRITE_FAILED"
            if self.scenario == "proxy_exit_after_request"
            else "FORWARDED"
        )
        proxy_receipt = {
            "receipt_schema_version": 2,
            "request_id": context.request_id,
            "component": "proxy",
            "method_allowed": True,
            "path_allowed": True,
            "auth_present": True,
            "tls_verification": True,
            "redirect_followed": False,
            "provider_attempt_count": 1,
            "retry_count": 0,
            "provider_http_status": (
                None if self.scenario == "proxy_exit_after_request" else 200
            ),
            "response_category": proxy_category,
            "response_size": (
                None if self.scenario == "proxy_exit_after_request" else 1
            ),
            "response_bytes": (
                None if self.scenario == "proxy_exit_after_request" else 1
            ),
            "terminal_status": proxy_category,
            **proxy_events,
        }
        relay_events: dict[str, dict[str, Any]] = {}
        for index, name in enumerate(_RELAY_EVENTS, start=20):
            occurred = name not in {
                "candidate_write_started",
                "candidate_write_completed",
                "candidate_ready_published",
            } or candidate_published
            relay_events[name] = _mock_stage_event(
                name,
                occurred=occurred,
                index=index,
                http_status=(200 if name == "response_received" and occurred else None),
                byte_count=(1 if name in {"request_submitted", "response_received"} and occurred else None),
            )
        relay_terminal = (
            "RELAY_NONZERO_EXIT"
            if self.scenario in {"relay_exit_before_candidate", "nonzero_container_exit"}
            else "RELAY_TIMEOUT"
            if self.scenario == "provider_over_60s"
            else "RELAY_PROCESS_ERROR"
            if relay_failed
            else "RELAY_COMPLETED"
        )
        relay_terminal_source = (
            "relay_self"
            if self.scenario in {"provider_over_60s"} or not relay_failed
            else "host_child_process"
        )
        relay_receipt = {
            "receipt_schema_version": 2,
            "request_id": context.request_id,
            "component": "relay",
            "status": "REJECTED" if relay_failed else "SUCCEEDED",
            "exit_code": 2 if relay_failed else 0,
            "terminal_status": relay_terminal,
            "terminal_reason_source": relay_terminal_source,
            "error_category": relay_terminal if relay_failed else None,
            "proxy_http_status": 200,
            "proxy_request_count": 1,
            "retry_count": 0,
            "response_size": 1,
            "response_sha256": "0" * 64,
            "started_at": "2026-08-08T08:23:20.000000Z",
            "completed_at": "2026-08-08T08:23:40.000000Z",
            **relay_events,
        }
        _write_private_file(
            context.proxy_output_dir / "proxy-receipt.json",
            canonical_json_bytes(proxy_receipt),
        )
        _write_private_file(
            context.output_dir / "relay-receipt.json",
            canonical_json_bytes(relay_receipt),
        )

    def _record_child_evidence(self) -> None:
        timing = ChildExecutionTiming(
            started_at="2026-08-08T08:23:20.000000Z",
            exited_at="2026-08-08T08:23:40.000000Z",
            started_monotonic_ns=20_000_000_000,
            exited_monotonic_ns=40_000_000_000,
        )
        relay_code = (
            1
            if self.scenario in {"relay_exit_before_candidate", "nonzero_container_exit"}
            else 0
        )
        self._child_evidence["relay"] = classify_completed_child(
            "relay",
            subprocess.CompletedProcess(
                ["mock-relay"],
                relay_code,
                stdout="",
                stderr="mock relay failure" if relay_code else "",
            ),
            timing,
        )
        if self.scenario == "proxy_exit_after_request":
            self._child_evidence["proxy"] = classify_completed_child(
                "proxy",
                subprocess.CompletedProcess(
                    ["mock-proxy"],
                    1,
                    stdout="",
                    stderr="mock proxy failure",
                ),
                timing,
            )
        else:
            self._child_evidence["proxy"] = classify_running_child(
                "proxy",
                timing,
            )

    def dispatch(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> BackendDispatchResult:
        del deadline
        self.lifecycle.append("dispatch")
        self.dispatch_count += 1
        if self.dispatch_count > 1:
            raise AssertionError("mock provider dispatched more than once")
        self._write_receipts(context)
        self._record_child_evidence()
        delays = {
            "provider_1s": 1.0,
            "provider_59s": 59.0,
            "provider_60s": 60.0,
            "provider_over_60s": 60.000_001,
        }
        self._now += delays.get(self.scenario, 1.0)
        if self.scenario == "provider_over_60s":
            raise BackendError("PROVIDER_TIMEOUT")
        if self.scenario in {"proxy_exit_after_request", "host_crash_after_dispatch"}:
            raise BackendError(
                "PROXY_EXITED",
                unknown_dispatch_state=True,
            )
        if self.scenario == "host_crash_after_response":
            raise BackendError(
                "HOST_CRASH_AFTER_RESPONSE",
                unknown_dispatch_state=True,
            )
        if self.scenario == "relay_exit_before_candidate":
            raise BackendError("CANDIDATE_NOT_PRODUCED")
        if self.scenario == "nonzero_container_exit":
            raise BackendError("CONTAINER_EXIT_NONZERO")
        if self.scenario == "candidate_partial_write":
            (context.output_dir / ".candidate.json.synthetic.partial").write_bytes(
                b"{\n"
            )
            return BackendDispatchResult(200, True)
        if self.scenario in {"candidate_rename_failure", "candidate_missing"}:
            return BackendDispatchResult(200, True)
        value = dict(self.relay_response)
        if self.scenario == "duplicate_request_id":
            value["request_id"] = "b" * 32
        self._publish(context, value)
        if self.scenario == "candidate_multiple":
            _publish_private_file(
                context.output_dir / "candidate.extra.json",
                canonical_json_bytes(value),
            )
        return BackendDispatchResult(200, True)

    def archive_evidence(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> ArchiveResult:
        del deadline
        self.lifecycle.append("archive")
        self.archive_count += 1
        return _archive_stage_evidence(
            context,
            self._child_evidence,
            expected_proxy_identity=_OPENAI_PROXY_RECEIPT_IDENTITY,
        )

    def cleanup(
        self,
        _context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> CleanupResult:
        del deadline
        self.lifecycle.append("cleanup")
        self.cleanup_count += 1
        if self.scenario == "cleanup_timeout":
            return CleanupResult(completed=False, timed_out=True)
        if self.scenario == "runtime_residue":
            return CleanupResult(
                completed=True,
                container_residue_count=1,
                network_residue_count=1,
            )
        return CleanupResult(completed=True)


CommandExecutor = Callable[
    [list[str], float],
    subprocess.CompletedProcess[str],
]


def _default_command_executor(
    command: list[str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=timeout,
        env={"PATH": os.environ.get("PATH", "")},
    )


def _mount(source: Path, destination: str, *, readonly: bool) -> str:
    suffix = ",readonly" if readonly else ""
    return f"type=bind,src={source},dst={destination}{suffix}"


def _hardened_create_prefix(name: str, network: str) -> list[str]:
    return [
        "docker",
        "create",
        "--name",
        name,
        "--network",
        network,
        "--read-only",
        "--user",
        "65532:65532",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "16",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--ipc",
        "none",
        "--restart",
        "no",
    ]


class DockerCanaryBackend:
    proxy_receipt_identity: Mapping[str, str] = _OPENAI_PROXY_RECEIPT_IDENTITY
    """Production one-shot Docker lifecycle; construction performs no I/O."""

    def __init__(
        self,
        *,
        executor: CommandExecutor = _default_command_executor,
        monotonic: Callable[[], float] = time.monotonic,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_clock: Callable[[], str] = _wall_timestamp,
        waiter: Callable[[float], None] = time.sleep,
    ) -> None:
        self.executor = executor
        self.monotonic = monotonic
        self.monotonic_ns = monotonic_ns
        self.wall_clock = wall_clock
        self.waiter = waiter
        self._names: dict[str, str] = {}
        self._child_evidence: dict[str, ChildProcessEvidence] = {}
        self._validated_receipts: dict[str, bytes] = {}
        self._proxy_started_at: tuple[str, int] | None = None

    def _remaining(self, deadline: float, category: str) -> float:
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise BackendError(category)
        return remaining

    def _run(
        self,
        command: list[str],
        *,
        deadline: float,
        category: str,
        maximum_timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        remaining = self._remaining(deadline, category)
        timeout = min(remaining, maximum_timeout or remaining)
        try:
            result = self.executor(command, timeout)
        except subprocess.TimeoutExpired as exc:
            raise BackendError(category) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackendError(category) from exc
        if result.returncode != 0:
            raise BackendError(category)
        return result

    def _run_child(
        self,
        command: list[str],
        *,
        component: Literal["relay", "proxy"],
        deadline: float,
        maximum_timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        started_at = self.wall_clock()
        started_monotonic_ns = self.monotonic_ns()
        remaining = self._remaining(deadline, f"{component.upper()}_TIMEOUT")
        timeout = min(remaining, maximum_timeout or remaining)
        host_timeout_is_binding = (
            maximum_timeout is not None and remaining < maximum_timeout
        )
        try:
            result = self.executor(command, timeout)
        except Exception as exc:
            timing = ChildExecutionTiming(
                started_at=started_at,
                exited_at=self.wall_clock(),
                started_monotonic_ns=started_monotonic_ns,
                exited_monotonic_ns=max(
                    started_monotonic_ns,
                    self.monotonic_ns(),
                ),
            )
            evidence = classify_child_exception(component, exc, timing)
            if isinstance(exc, subprocess.TimeoutExpired) and host_timeout_is_binding:
                evidence = replace(
                    evidence,
                    terminal_reason="ORCHESTRATOR_TIMEOUT",
                )
            self._child_evidence[component] = evidence
            raise BackendError(
                evidence.terminal_reason,
                unknown_dispatch_state=(
                    evidence.child_state is ChildState.EXIT_UNKNOWN
                ),
            ) from exc
        timing = ChildExecutionTiming(
            started_at=started_at,
            exited_at=self.wall_clock(),
            started_monotonic_ns=started_monotonic_ns,
            exited_monotonic_ns=max(started_monotonic_ns, self.monotonic_ns()),
        )
        evidence = classify_completed_child(
            component,
            result,
            timing,
            interpret_positive_signal=True,
        )
        self._child_evidence[component] = evidence
        if evidence.child_state is not ChildState.EXITED_ZERO:
            raise BackendError(
                evidence.terminal_reason,
                unknown_dispatch_state=(
                    evidence.child_state is ChildState.EXIT_UNKNOWN
                ),
            )
        return result

    def _capture_proxy_evidence(self, *, deadline: float) -> None:
        if "proxy" in self._child_evidence:
            return
        if self._proxy_started_at is None or not self._names:
            return
        started_at, started_monotonic_ns = self._proxy_started_at
        try:
            remaining = self._remaining(deadline, "PROXY_TIMEOUT")
            result = self.executor(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .State}}",
                    self._names["proxy"],
                ],
                remaining,
            )
            if result.returncode != 0:
                raise subprocess.SubprocessError("proxy inspect failed")
            state = json.loads(result.stdout)
            if not isinstance(state, dict) or not isinstance(
                state.get("Running"),
                bool,
            ):
                raise RuntimeError("proxy state unknown")
            timing = ChildExecutionTiming(
                started_at=started_at,
                exited_at=self.wall_clock(),
                started_monotonic_ns=started_monotonic_ns,
                exited_monotonic_ns=max(
                    started_monotonic_ns,
                    self.monotonic_ns(),
                ),
            )
            if state["Running"]:
                evidence = classify_running_child("proxy", timing)
            else:
                exit_code = state.get("ExitCode")
                evidence = classify_completed_child(
                    "proxy",
                    subprocess.CompletedProcess(
                        ["docker", "inspect", self._names["proxy"]],
                        exit_code,
                        stdout="",
                        stderr=(
                            state.get("Error")
                            if isinstance(state.get("Error"), str)
                            else ""
                        ),
                    ),
                    timing,
                    interpret_positive_signal=True,
                )
        except Exception as exc:
            timing = ChildExecutionTiming(
                started_at=started_at,
                exited_at=self.wall_clock(),
                started_monotonic_ns=started_monotonic_ns,
                exited_monotonic_ns=max(
                    started_monotonic_ns,
                    self.monotonic_ns(),
                ),
            )
            evidence = classify_child_exception("proxy", exc, timing)
        self._child_evidence["proxy"] = evidence

    def _runtime_names(self, request_id: str) -> dict[str, str]:
        suffix = request_id[:12]
        return {
            "relay_network": f"phase2-canary-relay-{suffix}",
            "egress_network": f"phase2-canary-egress-{suffix}",
            "proxy": f"phase2-openai-proxy-{suffix}",
            "relay": f"phase2-canary-relay-worker-{suffix}",
        }

    def inspect_global_residue(self, *, deadline: float) -> CleanupResult:
        containers = self._run(
            ["docker", "container", "ls", "--all", "--format", "{{.Names}}"],
            deadline=deadline,
            category="GLOBAL_RUNTIME_SCAN_FAILED",
            maximum_timeout=15.0,
        )
        networks = self._run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            deadline=deadline,
            category="GLOBAL_RUNTIME_SCAN_FAILED",
            maximum_timeout=15.0,
        )
        container_prefixes = (
            "phase2-openai-proxy-",
            "phase2-canary-relay-worker-",
        )
        network_prefixes = (
            "phase2-canary-relay-",
            "phase2-canary-egress-",
        )
        container_count = sum(
            line.startswith(container_prefixes)
            for line in containers.stdout.splitlines()
            if line
        )
        network_count = sum(
            line.startswith(network_prefixes)
            for line in networks.stdout.splitlines()
            if line
        )
        return CleanupResult(
            completed=not container_count and not network_count,
            container_residue_count=container_count,
            network_residue_count=network_count,
        )

    def _wait_for_proxy_ready(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> None:
        ready_path = context.proxy_output_dir / "proxy-ready.json"
        readiness_deadline = min(
            deadline,
            self.monotonic()
            + float(context.runtime_contract["provider_connect_timeout_seconds"]),
        )
        while self.monotonic() <= readiness_deadline:
            if ready_path.exists() or ready_path.is_symlink():
                try:
                    raw = _read_regular_unrestricted(
                        ready_path,
                        "PROXY_READINESS_INVALID",
                    )
                    value = _strict_json_bytes(raw, "PROXY_READINESS_INVALID")
                except OrchestratorError as exc:
                    raise BackendError("PROXY_READINESS_INVALID") from exc
                if (
                    set(value) != _PROXY_READY_FIELDS
                    or value.get("request_id") != context.request_id
                    or value.get("proxy_ready") is not True
                    or value.get("listener_ready") is not True
                    or not isinstance(value.get("wall_time"), str)
                    or not value["wall_time"]
                    or isinstance(value.get("monotonic_ns"), bool)
                    or not isinstance(value.get("monotonic_ns"), int)
                    or value["monotonic_ns"] < 0
                    or raw != canonical_json_bytes(value)
                    or _SENSITIVE_EVIDENCE.search(raw.decode("utf-8"))
                ):
                    raise BackendError("PROXY_READINESS_INVALID")
                return
            remaining = readiness_deadline - self.monotonic()
            if remaining <= 0:
                break
            self.waiter(min(0.05, remaining))
        raise BackendError("PROXY_READINESS_TIMEOUT")

    def prepare(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> None:
        self._names = self._runtime_names(context.request_id)
        self._child_evidence = {}
        self._validated_receipts = {}
        self._proxy_started_at = None
        names = self._names
        commands = [
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--internal",
                names["relay_network"],
            ],
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                names["egress_network"],
            ],
            [
                *_hardened_create_prefix(
                    names["proxy"],
                    names["relay_network"],
                ),
                "--network-alias",
                "phase2-egress-proxy",
                "--mount",
                _mount(
                    context.secret_path,
                    "/run/phase2/provider-auth",
                    readonly=True,
                ),
                "--mount",
                _mount(
                    context.proxy_output_dir,
                    "/output",
                    readonly=False,
                ),
                "--mount",
                _mount(
                    context.request_path,
                    "/input/request.json",
                    readonly=True,
                ),
                context.proxy_image_id,
            ],
            [
                "docker",
                "network",
                "connect",
                names["egress_network"],
                names["proxy"],
            ],
            [
                *_hardened_create_prefix(
                    names["relay"],
                    names["relay_network"],
                ),
                "--mount",
                _mount(
                    context.request_path,
                    "/input/request.json",
                    readonly=True,
                ),
                "--mount",
                _mount(
                    context.projection_path,
                    "/input/projection.json",
                    readonly=True,
                ),
                "--mount",
                _mount(context.output_dir, "/output", readonly=False),
                context.relay_image_id,
            ],
            ["docker", "start", names["proxy"]],
        ]
        for command in commands:
            if command == ["docker", "start", names["proxy"]]:
                self._proxy_started_at = (
                    self.wall_clock(),
                    self.monotonic_ns(),
                )
            self._run(
                command,
                deadline=deadline,
                category="RUNTIME_PREPARE_FAILED",
            )
        self._wait_for_proxy_ready(context, deadline=deadline)

    def dispatch(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> BackendDispatchResult:
        if not self._names:
            raise BackendError("RUNTIME_NOT_PREPARED")
        self._run_child(
            ["docker", "start", "--attach", self._names["relay"]],
            component="relay",
            deadline=deadline,
            maximum_timeout=float(
                context.runtime_contract["relay_candidate_wait_timeout_seconds"]
            ),
        )
        self._capture_proxy_evidence(deadline=deadline)
        proxy_evidence = self._child_evidence.get("proxy")
        if proxy_evidence is not None and proxy_evidence.child_state not in {
            ChildState.RUNNING,
            ChildState.EXITED_ZERO,
        }:
            raise BackendError(
                proxy_evidence.terminal_reason,
                unknown_dispatch_state=(
                    proxy_evidence.child_state is ChildState.EXIT_UNKNOWN
                ),
            )
        raw, receipt, receipt_status, _events = _load_stage_receipt(
            context.proxy_output_dir / "proxy-receipt.json",
            component="proxy",
            request_id=context.request_id,
            expected_proxy_identity=self.proxy_receipt_identity,
        )
        if receipt_status != "VALID" or raw is None or receipt is None:
            raise BackendError("PROXY_RECEIPT_INVALID")
        attempts = receipt.get("provider_attempt_count")
        retry = receipt.get("retry_count")
        status = receipt.get("provider_http_status")
        category = receipt.get("response_category")
        if (
            attempts != 1
            or retry != 0
            or isinstance(status, bool)
            or not isinstance(status, int)
            or category != "FORWARDED"
        ):
            raise BackendError("PROXY_RECEIPT_INVALID")
        self._validated_receipts["proxy"] = raw
        return BackendDispatchResult(
            provider_http_status=status,
            response_received=True,
        )

    def archive_evidence(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> ArchiveResult:
        self._capture_proxy_evidence(deadline=deadline)
        return _archive_stage_evidence(
            context,
            self._child_evidence,
            self._validated_receipts,
            expected_proxy_identity=self.proxy_receipt_identity,
        )

    def _cleanup_command(
        self,
        command: list[str],
        *,
        resource_kind: Literal["container", "network"],
        resource_name: str,
        deadline: float,
    ) -> bool:
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            return False
        try:
            result = self.executor(command, remaining)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 or self._docker_absence_proven(
            result,
            resource_kind=resource_kind,
            resource_name=resource_name,
        )

    @staticmethod
    def _docker_absence_proven(
        result: subprocess.CompletedProcess[str],
        *,
        resource_kind: Literal["container", "network"],
        resource_name: str,
    ) -> bool:
        if (
            result.returncode != 1
            or not isinstance(result.stdout, str)
            or result.stdout.strip() not in {"", "[]"}
            or not isinstance(result.stderr, str)
        ):
            return False
        expected = {
            "container": {
                f"Error response from daemon: No such container: {resource_name}",
                f"Error: No such container: {resource_name}",
                f"Error: No such object: {resource_name}",
            },
            "network": {
                f"Error response from daemon: network {resource_name} not found",
                f"Error: No such network: {resource_name}",
                f"Error: No such object: {resource_name}",
            },
        }[resource_kind]
        return result.stderr.strip() in expected

    def cleanup(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> CleanupResult:
        names = self._names or self._runtime_names(context.request_id)
        completed = True
        for role in ("relay", "proxy"):
            completed = self._cleanup_command(
                ["docker", "rm", "--force", names[role]],
                resource_kind="container",
                resource_name=names[role],
                deadline=deadline,
            ) and completed
        for role in ("relay_network", "egress_network"):
            completed = self._cleanup_command(
                ["docker", "network", "rm", names[role]],
                resource_kind="network",
                resource_name=names[role],
                deadline=deadline,
            ) and completed
        container_residue = 0
        network_residue = 0
        for role in ("relay", "proxy"):
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return CleanupResult(
                    completed=False,
                    timed_out=True,
                    container_residue_count=container_residue,
                    network_residue_count=network_residue,
                )
            try:
                result = self.executor(
                    ["docker", "container", "inspect", names[role]],
                    remaining,
                )
            except (OSError, subprocess.SubprocessError):
                result = None
            if (
                result is None
                or result.returncode == 0
                or not self._docker_absence_proven(
                    result,
                    resource_kind="container",
                    resource_name=names[role],
                )
            ):
                container_residue += 1
        for role in ("relay_network", "egress_network"):
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return CleanupResult(
                    completed=False,
                    timed_out=True,
                    container_residue_count=container_residue,
                    network_residue_count=network_residue,
                )
            try:
                result = self.executor(
                    ["docker", "network", "inspect", names[role]],
                    remaining,
                )
            except (OSError, subprocess.SubprocessError):
                result = None
            if (
                result is None
                or result.returncode == 0
                or not self._docker_absence_proven(
                    result,
                    resource_kind="network",
                    resource_name=names[role],
                )
            ):
                network_residue += 1
        return CleanupResult(
            completed=completed and not container_residue and not network_residue,
            container_residue_count=container_residue,
            network_residue_count=network_residue,
        )


class ArkDockerCanaryBackend(DockerCanaryBackend):
    """Ark runtime with provider-bound Receipt validation."""

    proxy_receipt_identity: Mapping[str, str] = _ARK_PROXY_RECEIPT_IDENTITY

    def _runtime_names(self, request_id: str) -> dict[str, str]:
        names = super()._runtime_names(request_id)
        names["proxy"] = f"phase2-ark-proxy-{request_id[:12]}"
        return names

    def dispatch(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> BackendDispatchResult:
        result = super().dispatch(context, deadline=deadline)
        raw = self._validated_receipts.get("proxy")
        if raw is None:
            raise BackendError("PROXY_RECEIPT_INVALID")
        try:
            _parse_stage_receipt_bytes(
                raw,
                component="proxy",
                request_id=context.request_id,
                expected_proxy_identity=_ARK_PROXY_RECEIPT_IDENTITY,
            )
        except OrchestratorError as exc:
            self._validated_receipts.pop("proxy", None)
            raise BackendError("PROXY_RECEIPT_INVALID") from exc
        return result

    def archive_evidence(
        self,
        context: CanaryRuntimeContext,
        *,
        deadline: float,
    ) -> ArchiveResult:
        self._capture_proxy_evidence(deadline=deadline)
        return _archive_stage_evidence(
            context,
            self._child_evidence,
            self._validated_receipts,
            expected_proxy_identity=_ARK_PROXY_RECEIPT_IDENTITY,
        )

    def inspect_global_residue(self, *, deadline: float) -> CleanupResult:
        result = super().inspect_global_residue(deadline=deadline)
        containers = self._run(
            ["docker", "container", "ls", "--all", "--format", "{{.Names}}"],
            deadline=deadline,
            category="GLOBAL_RUNTIME_SCAN_FAILED",
            maximum_timeout=15.0,
        )
        ark_count = sum(
            line.startswith("phase2-ark-proxy-")
            for line in containers.stdout.splitlines()
            if line
        )
        return CleanupResult(
            completed=result.completed and ark_count == 0,
            container_residue_count=result.container_residue_count + ark_count,
            network_residue_count=result.network_residue_count,
            timed_out=result.timed_out,
        )


def read_keychain_secret_once(
    *,
    executor: CommandExecutor = _default_command_executor,
) -> str:
    """Read the fixed Canary credential once without exposing metadata."""
    command = [
        "security",
        "find-generic-password",
        "-w",
        "-s",
        "tickflow-phase2-canary-openai",
    ]
    try:
        result = executor(command, 10.0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OrchestratorError("keychain_secret_unavailable") from exc
    if result.returncode != 0:
        raise OrchestratorError("keychain_secret_unavailable")
    value = result.stdout.removesuffix("\n")
    if (
        not value
        or value != value.strip()
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise OrchestratorError("keychain_secret_invalid")
    return value


def read_ark_keychain_secret_once(
    *,
    executor: CommandExecutor = _default_command_executor,
) -> str:
    """Read the fixed Ark credential once after approval gates pass."""
    command = [
        "security",
        "find-generic-password",
        "-w",
        "-s",
        "tickflow-phase2-canary-volcengine-ark",
    ]
    try:
        result = executor(command, 10.0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OrchestratorError("keychain_secret_unavailable") from exc
    if result.returncode != 0:
        raise OrchestratorError("keychain_secret_unavailable")
    value = result.stdout.removesuffix("\n")
    if (
        not value
        or value != value.strip()
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise OrchestratorError("keychain_secret_invalid")
    return value


def load_installed_runtime_approval(
    *,
    candidate_path: Path,
    approval_path: Path,
) -> ApprovedCanaryArtifacts:
    """Bind one repository Candidate to a separate local approval record."""
    try:
        candidate_raw = _read_regular_unrestricted(
            candidate_path,
            "runtime_candidate_invalid",
        )
        approval_raw = _read_trusted_runtime_approval(approval_path)
        _strict_json_bytes(
            candidate_raw,
            "runtime_candidate_invalid",
        )
        approval = _strict_json_bytes(
            approval_raw,
            "runtime_approval_invalid",
        )
    except OrchestratorError as exc:
        if str(exc) == "runtime_candidate_invalid":
            raise
        raise OrchestratorError("runtime_approval_invalid") from exc
    if approval_raw != canonical_json_bytes(approval):
        raise OrchestratorError("runtime_approval_invalid")
    try:
        candidate_model = RuntimeArtifactCandidate.model_validate_json(
            candidate_raw
        )
    except ValidationError as exc:
        raise OrchestratorError("runtime_candidate_invalid") from exc
    if candidate_raw != canonical_json_bytes(
        candidate_model.model_dump(mode="json")
    ):
        raise OrchestratorError("runtime_candidate_invalid")
    expected_approval_fields = {
        "runtime_approval_schema_version",
        "approved_candidate_sha256",
        "approval_scope",
        "symbol",
        "provider",
        "maximum_provider_attempts",
        "retry_count",
    }
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    if (
        set(approval) != expected_approval_fields
        or approval.get("runtime_approval_schema_version") != 2
        or approval.get("approval_scope")
        != "single_symbol_openai_canary_runtime_v2"
        or approval.get("symbol") != "000403.SZ"
        or approval.get("provider") != "openai"
        or approval.get("maximum_provider_attempts") != 1
        or approval.get("retry_count") != 0
        or not isinstance(approval.get("approved_candidate_sha256"), str)
        or not hmac.compare_digest(
            approval["approved_candidate_sha256"],
            candidate_sha256,
        )
    ):
        raise OrchestratorError("runtime_approval_invalid")
    identity = candidate_model.artifact_identity.model_dump(mode="json")
    try:
        return ApprovedCanaryArtifacts.model_validate(
            {
                **identity,
                "approval_candidate_sha256": candidate_sha256,
            }
        )
    except ValidationError as exc:
        raise OrchestratorError("runtime_candidate_invalid") from exc
