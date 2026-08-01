"""Host-side contracts for the Phase 2 isolated Fake Provider runtime."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.phase2_claims import ClaimsDocument, WorkerClaimsCandidate
from app.services.phase2_ai_worker_protocol import validate_worker_candidate
from app.services.phase2_claims_renderer import (
    Phase2ClaimsRenderError,
    render_claims_document,
    validate_rendered_document,
)
from app.services.phase2_claims_service import (
    CLAIMS_VALID,
    Phase2ClaimsError,
    canonical_json_bytes,
    validate_claims_document,
)
from app.services.phase2_facts import FIXED_SYMBOLS

RUNTIME_USER = "65532:65532"
EXPECTED_MEMORY = 128 * 1024 * 1024
EXPECTED_NANO_CPUS = 500_000_000
EXPECTED_PIDS_LIMIT = 16
EXPECTED_MOUNTS = {
    "/input/projection.json": False,
    "/output/candidate.json": True,
}
_CONTAINER_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_IMAGE_REF = re.compile(r"^[a-z0-9][a-z0-9./:_-]{0,200}$")


class Phase2IsolationRuntimeError(ValueError):
    """Raised when a host runtime contract cannot be constructed safely."""


@dataclass(frozen=True)
class IsolationPaths:
    projection: Path
    output: Path

    def __post_init__(self) -> None:
        for field in ("projection", "output"):
            value = getattr(self, field)
            if value.is_symlink():
                raise Phase2IsolationRuntimeError(f"{field}_must_not_be_symlink")
            resolved = value.resolve(strict=True)
            if not resolved.is_file():
                raise Phase2IsolationRuntimeError(f"{field}_must_be_regular_file")
            object.__setattr__(self, field, resolved)


@dataclass(frozen=True)
class RuntimeContractEvidence:
    contract_valid: bool
    errors: list[str]
    mount_count: int
    network_none: bool
    rootfs_readonly: bool
    non_root: bool
    cap_drop_all: bool
    no_new_privileges: bool
    pids_limited: bool
    memory_limited: bool
    cpu_limited: bool
    ipc_none: bool
    restart_disabled: bool
    projection_readonly: bool
    output_writable: bool
    state_valid: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateHostEvidence:
    worker_status: str
    claims_status: str
    renderer_status: str
    errors: list[str]
    claim_count: int
    facts_pointer_binding_count: int
    candidate_sha256: str
    rendered_sha256: str
    free_text_field_count: int
    unsourced_claim_count: int
    trading_claim_count: int
    raw_qfq_mismatch_count: int
    sensitive_hit_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_container_create_command(
    image_ref: str,
    container_name: str,
    mode: str,
    paths: IsolationPaths,
) -> list[str]:
    """Build the only approved Docker create command for the worker."""
    if not _IMAGE_REF.fullmatch(image_ref):
        raise Phase2IsolationRuntimeError("image_ref_invalid")
    if not _CONTAINER_NAME.fullmatch(container_name):
        raise Phase2IsolationRuntimeError("container_name_invalid")
    if mode not in {"candidate", "probe"}:
        raise Phase2IsolationRuntimeError("worker_mode_invalid")
    projection_mount = (
        f"type=bind,src={paths.projection},"
        "dst=/input/projection.json,readonly"
    )
    output_mount = (
        f"type=bind,src={paths.output},dst=/output/candidate.json"
    )
    return [
        "docker",
        "create",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--user",
        RUNTIME_USER,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(EXPECTED_PIDS_LIMIT),
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--ipc",
        "none",
        "--restart",
        "no",
        "--mount",
        projection_mount,
        "--mount",
        output_mount,
        image_ref,
        mode,
    ]


def _inspect_item(payload: Any) -> Mapping[str, Any] | None:
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], Mapping)
    ):
        return None
    return payload[0]


def validate_container_inspect(
    payload: Any,
    paths: IsolationPaths,
    *,
    expected_state: str,
) -> RuntimeContractEvidence:
    """Validate actual Docker inspect state without persisting host sources."""
    errors: list[str] = []
    item = _inspect_item(payload)
    if item is None:
        errors.append("runtime_inspect_invalid")
        item = {}
    config = item.get("Config") if isinstance(item.get("Config"), Mapping) else {}
    host = (
        item.get("HostConfig")
        if isinstance(item.get("HostConfig"), Mapping)
        else {}
    )
    state = item.get("State") if isinstance(item.get("State"), Mapping) else {}
    mounts = item.get("Mounts") if isinstance(item.get("Mounts"), list) else []

    non_root = config.get("User") == RUNTIME_USER
    network_none = host.get("NetworkMode") == "none"
    rootfs_readonly = host.get("ReadonlyRootfs") is True
    cap_drop_all = {
        str(value).upper() for value in host.get("CapDrop", [])
    } == {"ALL"}
    security_options = {
        str(value).lower() for value in host.get("SecurityOpt", [])
    }
    no_new_privileges = bool(
        security_options
        & {"no-new-privileges", "no-new-privileges:true"}
    ) and "no-new-privileges:false" not in security_options
    pids_limited = host.get("PidsLimit") == EXPECTED_PIDS_LIMIT
    memory_limited = host.get("Memory") == EXPECTED_MEMORY
    cpu_limited = host.get("NanoCpus") == EXPECTED_NANO_CPUS
    ipc_none = host.get("IpcMode") == "none"
    restart = host.get("RestartPolicy")
    restart_disabled = (
        isinstance(restart, Mapping)
        and restart.get("Name") in {"", "no"}
        and restart.get("MaximumRetryCount", 0) == 0
    )
    state_valid = state.get("Status") == expected_state
    if expected_state == "exited":
        state_valid = state_valid and state.get("ExitCode") == 0

    checks = {
        "runtime_user_invalid": non_root,
        "runtime_network_not_none": network_none,
        "runtime_rootfs_not_readonly": rootfs_readonly,
        "runtime_cap_drop_invalid": cap_drop_all,
        "runtime_security_opt_invalid": no_new_privileges,
        "runtime_pids_limit_invalid": pids_limited,
        "runtime_memory_limit_invalid": memory_limited,
        "runtime_cpu_limit_invalid": cpu_limited,
        "runtime_ipc_mode_invalid": ipc_none,
        "runtime_restart_policy_invalid": restart_disabled,
        "runtime_state_invalid": state_valid,
    }
    errors.extend(code for code, passed in checks.items() if not passed)

    mount_by_destination = {
        mount.get("Destination"): mount
        for mount in mounts
        if isinstance(mount, Mapping) and isinstance(mount.get("Destination"), str)
    }
    mount_set_valid = len(mounts) == 2 and set(mount_by_destination) == set(
        EXPECTED_MOUNTS
    )
    projection_readonly = False
    output_writable = False
    if mount_set_valid:
        projection = mount_by_destination["/input/projection.json"]
        output = mount_by_destination["/output/candidate.json"]
        projection_readonly = (
            projection.get("Type") == "bind"
            and projection.get("RW") is False
            and Path(str(projection.get("Source"))).resolve(strict=False)
            == paths.projection
        )
        output_writable = (
            output.get("Type") == "bind"
            and output.get("RW") is True
            and Path(str(output.get("Source"))).resolve(strict=False) == paths.output
        )
    if not mount_set_valid:
        errors.append("runtime_mount_set_invalid")
    if not projection_readonly:
        errors.append("runtime_projection_mount_not_readonly")
    if not output_writable:
        errors.append("runtime_output_mount_not_writable")

    unique_errors = sorted(set(errors))
    return RuntimeContractEvidence(
        contract_valid=not unique_errors,
        errors=unique_errors,
        mount_count=len(mounts),
        network_none=network_none,
        rootfs_readonly=rootfs_readonly,
        non_root=non_root,
        cap_drop_all=cap_drop_all,
        no_new_privileges=no_new_privileges,
        pids_limited=pids_limited,
        memory_limited=memory_limited,
        cpu_limited=cpu_limited,
        ipc_none=ipc_none,
        restart_disabled=restart_disabled,
        projection_readonly=projection_readonly,
        output_writable=output_writable,
        state_valid=state_valid,
    )


def _blocked_candidate_evidence(
    worker_status: str,
    errors: Sequence[str],
    candidate_sha256: str,
) -> CandidateHostEvidence:
    return CandidateHostEvidence(
        worker_status=worker_status,
        claims_status="CLAIMS_BLOCKED",
        renderer_status="RENDERED_BLOCKED",
        errors=sorted(set(errors)),
        claim_count=0,
        facts_pointer_binding_count=0,
        candidate_sha256=candidate_sha256,
        rendered_sha256="",
        free_text_field_count=0,
        unsourced_claim_count=0,
        trading_claim_count=0,
        raw_qfq_mismatch_count=0,
        sensitive_hit_count=0,
    )


def validate_isolated_candidate(
    repo_root: Path,
    candidate: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> CandidateHostEvidence:
    """Revalidate a worker candidate through every host-owned contract."""
    try:
        candidate_bytes = canonical_json_bytes(candidate)
    except (TypeError, ValueError):
        return _blocked_candidate_evidence(
            "WORKER_CANDIDATE_INVALID",
            ["candidate_json_invalid"],
            "",
        )
    candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
    worker_validation = validate_worker_candidate(candidate, projection)
    if worker_validation.errors:
        return _blocked_candidate_evidence(
            worker_validation.status,
            worker_validation.errors,
            candidate_sha256,
        )
    try:
        parsed = WorkerClaimsCandidate.model_validate(candidate)
        symbol = parsed.symbol
        if symbol not in FIXED_SYMBOLS:
            raise Phase2IsolationRuntimeError("candidate_symbol_invalid")
        facts_file = (
            f'reports/phase2_facts/{symbol.replace(".", "")}_facts.json'
        )
        safe_facts = projection["safe_facts"]
        scope = safe_facts["scope"]
        document = ClaimsDocument.model_validate(
            {
                "claims_schema_version": 1,
                "source_system": "tickflow-stock-panel",
                "symbol": symbol,
                "name": parsed.name,
                "trade_date": parsed.trade_date,
                "timezone": parsed.timezone,
                "facts_binding": {
                    "facts_file": facts_file,
                    "facts_sha256": projection["facts_sha256"],
                    "facts_schema_version": 1,
                },
                "scope": {
                    "market_scope": scope["market_scope"],
                    "financial_scope": scope["financial_scope"],
                    "news_scope": scope["news_scope"],
                    "industry_scope": "unavailable",
                },
                "claims": [item.model_dump(mode="json") for item in parsed.claims],
                "vendor_pending": safe_facts["vendor_pending"],
                "trading_advice": False,
            }
        )
        claims_validation = validate_claims_document(repo_root, document)
    except (
        KeyError,
        TypeError,
        ValidationError,
        Phase2ClaimsError,
        Phase2IsolationRuntimeError,
    ) as exc:
        return _blocked_candidate_evidence(
            worker_validation.status,
            [f"claims_adapter_invalid:{type(exc).__name__}"],
            candidate_sha256,
        )
    if claims_validation.status != CLAIMS_VALID:
        return CandidateHostEvidence(
            worker_status=worker_validation.status,
            claims_status=claims_validation.status,
            renderer_status="RENDERED_BLOCKED",
            errors=claims_validation.errors,
            claim_count=claims_validation.claim_count,
            facts_pointer_binding_count=(
                claims_validation.facts_pointer_binding_count
            ),
            candidate_sha256=candidate_sha256,
            rendered_sha256="",
            free_text_field_count=claims_validation.free_text_field_count,
            unsourced_claim_count=claims_validation.unsourced_claim_count,
            trading_claim_count=claims_validation.trading_claim_count,
            raw_qfq_mismatch_count=claims_validation.raw_qfq_mismatch_count,
            sensitive_hit_count=claims_validation.sensitive_hit_count,
        )
    try:
        rendered = render_claims_document(document, claims_validation)
        render_errors = validate_rendered_document(
            document,
            claims_validation,
            rendered,
        )
    except Phase2ClaimsRenderError as exc:
        render_errors = [f"renderer_error:{type(exc).__name__}"]
        rendered = ""
    if render_errors:
        return CandidateHostEvidence(
            worker_status=worker_validation.status,
            claims_status=claims_validation.status,
            renderer_status="RENDERED_BLOCKED",
            errors=render_errors,
            claim_count=claims_validation.claim_count,
            facts_pointer_binding_count=(
                claims_validation.facts_pointer_binding_count
            ),
            candidate_sha256=candidate_sha256,
            rendered_sha256="",
            free_text_field_count=claims_validation.free_text_field_count,
            unsourced_claim_count=claims_validation.unsourced_claim_count,
            trading_claim_count=claims_validation.trading_claim_count,
            raw_qfq_mismatch_count=claims_validation.raw_qfq_mismatch_count,
            sensitive_hit_count=claims_validation.sensitive_hit_count,
        )
    return CandidateHostEvidence(
        worker_status=worker_validation.status,
        claims_status=claims_validation.status,
        renderer_status="RENDERED_VALID",
        errors=[],
        claim_count=claims_validation.claim_count,
        facts_pointer_binding_count=claims_validation.facts_pointer_binding_count,
        candidate_sha256=candidate_sha256,
        rendered_sha256=hashlib.sha256(rendered.encode()).hexdigest(),
        free_text_field_count=claims_validation.free_text_field_count,
        unsourced_claim_count=claims_validation.unsourced_claim_count,
        trading_claim_count=claims_validation.trading_claim_count,
        raw_qfq_mismatch_count=claims_validation.raw_qfq_mismatch_count,
        sensitive_hit_count=claims_validation.sensitive_hit_count,
    )
