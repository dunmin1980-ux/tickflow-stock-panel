"""Host-side contracts for the Phase 2 isolated Fake Provider runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.phase2_claims import ClaimsDocument, WorkerClaimsCandidate
from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_ai_worker_protocol import (
    build_worker_projection,
    validate_worker_candidate,
)
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
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_JSON_BYTES = 1_048_576
_WORKER_IMAGE = "tickflow-phase2-fake-worker:runtime-v1"
_RUNTIME_VERIFIED = "PHASE2B_ISOLATION_RUNTIME_VERIFIED"
_RUNTIME_BLOCKED = "PHASE2B_ISOLATION_RUNTIME_BLOCKED"
_EXPECTED_ENTRYPOINT = ["/usr/bin/python3", "/worker/worker.py"]
_BASE_DIGEST_LABEL = "org.tickflow.phase2.base-image-digest"
_SENSITIVE_ENV_NAME = re.compile(
    r"(?:^|_)(?:api_?key|authorization|cookie|password|secret|session|token)(?:$|_)",
    re.IGNORECASE,
)
_PROBE_GROUPS = {
    "shells": {"sh", "bash", "busybox"},
    "forbidden_reads": {
        "host_home",
        "repository",
        "ssh",
        "config",
        "vault",
        "docker_socket",
    },
    "forbidden_writes": {
        "projection",
        "tmp",
        "worker",
        "worker_source",
        "etc",
        "extra_output",
    },
}


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


@dataclass(frozen=True)
class RuntimeValidationResult:
    status: str
    errors: list[str]
    cleanup_complete: bool
    evidence: dict[str, Any]


@dataclass(frozen=True)
class _ContainerRunResult:
    contract: RuntimeContractEvidence
    output: dict[str, Any]
    cleanup_complete: bool


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


def _default_executor(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_command(
    executor: Callable[[list[str]], subprocess.CompletedProcess[str]],
    command: list[str],
    error_code: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = executor(command)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase2IsolationRuntimeError(error_code) from exc
    if result.returncode != 0:
        raise Phase2IsolationRuntimeError(error_code)
    return result


def _json_object(raw: bytes | str, error_code: str) -> dict[str, Any]:
    raw_bytes = raw.encode() if isinstance(raw, str) else raw
    if not raw_bytes or len(raw_bytes) > _MAX_JSON_BYTES:
        raise Phase2IsolationRuntimeError(error_code)

    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite")

    try:
        value = json.loads(raw_bytes, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Phase2IsolationRuntimeError(error_code) from exc
    if not isinstance(value, dict):
        raise Phase2IsolationRuntimeError(error_code)
    return value


def _json_list(raw: str, error_code: str) -> list[Any]:
    if not raw or len(raw.encode()) > _MAX_JSON_BYTES:
        raise Phase2IsolationRuntimeError(error_code)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Phase2IsolationRuntimeError(error_code) from exc
    if not isinstance(value, list):
        raise Phase2IsolationRuntimeError(error_code)
    return value


def _read_regular_file(path: Path, error_code: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise Phase2IsolationRuntimeError(error_code)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            size = os.fstat(descriptor).st_size
            if size <= 0 or size > _MAX_JSON_BYTES:
                raise Phase2IsolationRuntimeError(error_code)
            value = os.read(descriptor, _MAX_JSON_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise Phase2IsolationRuntimeError(error_code) from exc
    if len(value) != size:
        raise Phase2IsolationRuntimeError(error_code)
    return value


def _write_durable(path: Path, value: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        os.write(descriptor, value)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, mode, follow_symlinks=False)


def _reset_output(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise Phase2IsolationRuntimeError("worker_output_path_invalid")
    flags = os.O_WRONLY | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            os.write(descriptor, b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(path, 0o666, follow_symlinks=False)
    except OSError as exc:
        raise Phase2IsolationRuntimeError("worker_output_reset_failed") from exc


def _resolve_reports_root(
    repo_root: Path,
    reports_root: Path,
    *,
    allow_test_output_root: bool,
) -> Path:
    repo_root = repo_root.resolve(strict=True)
    if reports_root.is_symlink():
        raise Phase2IsolationRuntimeError("reports_root_must_not_be_symlink")
    resolved = reports_root.resolve(strict=True)
    if not resolved.is_dir():
        raise Phase2IsolationRuntimeError("reports_root_must_be_directory")
    if not allow_test_output_root and resolved != repo_root / "reports":
        raise Phase2IsolationRuntimeError("reports_root_must_be_repository_reports")
    return resolved


def _runtime_server_evidence(raw: str) -> dict[str, str]:
    value = _json_object(raw, "docker_server_info_invalid")
    required = {"Version", "Os", "Arch"}
    if not required.issubset(value) or not all(
        isinstance(value[field], str) and value[field]
        for field in required
    ):
        raise Phase2IsolationRuntimeError("docker_server_info_invalid")
    return {
        "server_version": value["Version"],
        "os": value["Os"],
        "arch": value["Arch"],
    }


def _image_evidence(raw: str) -> dict[str, Any]:
    payload = _json_list(raw, "worker_image_inspect_invalid")
    item = _inspect_item(payload)
    if item is None:
        raise Phase2IsolationRuntimeError("worker_image_inspect_invalid")
    config = item.get("Config")
    if not isinstance(config, Mapping):
        raise Phase2IsolationRuntimeError("worker_image_config_invalid")
    image_id = item.get("Id")
    labels = config.get("Labels")
    base_digest = labels.get(_BASE_DIGEST_LABEL) if isinstance(labels, Mapping) else None
    env = config.get("Env")
    if not isinstance(env, list) or any(not isinstance(value, str) for value in env):
        raise Phase2IsolationRuntimeError("worker_image_environment_invalid")
    env_names = [value.partition("=")[0] for value in env]
    checks = {
        "worker_image_id_invalid": isinstance(image_id, str)
        and bool(_SHA256.fullmatch(image_id)),
        "worker_image_user_invalid": config.get("User") == RUNTIME_USER,
        "worker_image_entrypoint_invalid": config.get("Entrypoint")
        == _EXPECTED_ENTRYPOINT,
        "worker_base_digest_invalid": isinstance(base_digest, str)
        and bool(_SHA256.fullmatch(base_digest)),
        "worker_image_sensitive_environment": not any(
            _SENSITIVE_ENV_NAME.search(name) for name in env_names
        ),
    }
    failed = [code for code, passed in checks.items() if not passed]
    if failed:
        raise Phase2IsolationRuntimeError(failed[0])
    return {
        "image_id": image_id,
        "base_image_digest": base_digest,
        "user": RUNTIME_USER,
        "entrypoint": list(_EXPECTED_ENTRYPOINT),
    }


def _combine_runtime_contract(
    created: RuntimeContractEvidence,
    exited: RuntimeContractEvidence,
) -> RuntimeContractEvidence:
    errors = sorted(set(created.errors + exited.errors))
    return RuntimeContractEvidence(
        contract_valid=not errors,
        errors=errors,
        mount_count=exited.mount_count,
        network_none=created.network_none and exited.network_none,
        rootfs_readonly=created.rootfs_readonly and exited.rootfs_readonly,
        non_root=created.non_root and exited.non_root,
        cap_drop_all=created.cap_drop_all and exited.cap_drop_all,
        no_new_privileges=(
            created.no_new_privileges and exited.no_new_privileges
        ),
        pids_limited=created.pids_limited and exited.pids_limited,
        memory_limited=created.memory_limited and exited.memory_limited,
        cpu_limited=created.cpu_limited and exited.cpu_limited,
        ipc_none=created.ipc_none and exited.ipc_none,
        restart_disabled=created.restart_disabled and exited.restart_disabled,
        projection_readonly=(
            created.projection_readonly and exited.projection_readonly
        ),
        output_writable=created.output_writable and exited.output_writable,
        state_valid=created.state_valid and exited.state_valid,
    )


def _run_container_once(
    executor: Callable[[list[str]], subprocess.CompletedProcess[str]],
    paths: IsolationPaths,
    mode: str,
) -> _ContainerRunResult:
    name = f"phase2-{mode}-{uuid.uuid4().hex[:12]}"
    created = False
    cleanup_complete = True
    contract: RuntimeContractEvidence | None = None
    output: dict[str, Any] = {}
    caught: Exception | None = None
    try:
        _reset_output(paths.output)
        create = build_container_create_command(_WORKER_IMAGE, name, mode, paths)
        _run_command(executor, create, f"docker_{mode}_create_failed")
        created = True
        created_payload = _json_list(
            _run_command(
                executor,
                ["docker", "inspect", name],
                f"docker_{mode}_created_inspect_failed",
            ).stdout,
            f"docker_{mode}_created_inspect_invalid",
        )
        created_contract = validate_container_inspect(
            created_payload,
            paths,
            expected_state="created",
        )
        if not created_contract.contract_valid:
            raise Phase2IsolationRuntimeError(f"docker_{mode}_created_contract_blocked")
        _run_command(
            executor,
            ["docker", "start", "--attach", name],
            f"docker_{mode}_start_failed",
        )
        exited_payload = _json_list(
            _run_command(
                executor,
                ["docker", "inspect", name],
                f"docker_{mode}_exited_inspect_failed",
            ).stdout,
            f"docker_{mode}_exited_inspect_invalid",
        )
        exited_contract = validate_container_inspect(
            exited_payload,
            paths,
            expected_state="exited",
        )
        contract = _combine_runtime_contract(created_contract, exited_contract)
        if not contract.contract_valid:
            raise Phase2IsolationRuntimeError(f"docker_{mode}_exited_contract_blocked")
        output = _json_object(
            _read_regular_file(paths.output, f"worker_{mode}_output_invalid"),
            f"worker_{mode}_output_invalid",
        )
    except Exception as exc:  # cleanup must run for every created container
        caught = exc
    finally:
        if created:
            try:
                removed = executor(["docker", "rm", "-f", name])
                cleanup_complete = removed.returncode == 0
            except (OSError, subprocess.SubprocessError):
                cleanup_complete = False
    if not cleanup_complete:
        raise Phase2IsolationRuntimeError(f"docker_{mode}_cleanup_failed")
    if caught is not None:
        if isinstance(caught, Phase2IsolationRuntimeError):
            raise caught
        raise Phase2IsolationRuntimeError(f"docker_{mode}_runtime_failed") from caught
    if contract is None:
        raise Phase2IsolationRuntimeError(f"docker_{mode}_contract_missing")
    return _ContainerRunResult(
        contract=contract,
        output=output,
        cleanup_complete=cleanup_complete,
    )


def _validate_probe(value: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if set(value) != {
        "probe_schema_version",
        "network",
        "shells",
        "forbidden_reads",
        "forbidden_writes",
    } or value.get("probe_schema_version") != 1:
        errors.append("probe_shape_invalid")

    def blocked(item: Any) -> bool:
        return (
            isinstance(item, Mapping)
            and set(item) == {"blocked", "errno"}
            and item.get("blocked") is True
            and isinstance(item.get("errno"), str)
            and bool(item.get("errno"))
        )

    network_blocked = blocked(value.get("network"))
    if not network_blocked:
        errors.append("probe_network_not_blocked")
    for group, expected_names in _PROBE_GROUPS.items():
        items = value.get(group)
        if not isinstance(items, Mapping) or set(items) != expected_names:
            errors.append(f"probe_{group}_shape_invalid")
            continue
        if not all(blocked(item) for item in items.values()):
            errors.append(f"probe_{group}_not_blocked")
    if errors:
        raise Phase2IsolationRuntimeError(sorted(errors)[0])
    return {
        "all_blocked": True,
        "network_blocked": True,
        "shell_blocked_count": len(_PROBE_GROUPS["shells"]),
        "forbidden_read_blocked_count": len(_PROBE_GROUPS["forbidden_reads"]),
        "forbidden_write_blocked_count": len(_PROBE_GROUPS["forbidden_writes"]),
    }


def _blocked_contract() -> RuntimeContractEvidence:
    return RuntimeContractEvidence(
        contract_valid=False,
        errors=["runtime_not_completed"],
        mount_count=0,
        network_none=False,
        rootfs_readonly=False,
        non_root=False,
        cap_drop_all=False,
        no_new_privileges=False,
        pids_limited=False,
        memory_limited=False,
        cpu_limited=False,
        ipc_none=False,
        restart_disabled=False,
        projection_readonly=False,
        output_writable=False,
        state_valid=False,
    )


def _blocked_host_evidence() -> CandidateHostEvidence:
    return _blocked_candidate_evidence(
        "WORKER_CANDIDATE_BLOCKED",
        ["candidate_not_completed"],
        "",
    )


def _host_evidence_for_report(value: CandidateHostEvidence) -> dict[str, Any]:
    evidence = value.to_dict()
    evidence["claim_without_provenance_count"] = evidence.pop(
        "unsourced_claim_count"
    )
    return evidence


def _publish_runtime_evidence(reports_root: Path, evidence: Mapping[str, Any]) -> None:
    staging = Path(
        tempfile.mkdtemp(prefix=".phase2_isolation_runtime.staging-", dir=reports_root)
    )
    try:
        _write_durable(
            staging / "runtime_evidence.json",
            canonical_json_bytes(evidence),
            0o644,
        )
        atomic_publish_directory(
            staging,
            reports_root / "phase2_isolation_runtime",
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def run_runtime_validation(
    repo_root: Path,
    reports_root: Path,
    *,
    _executor: Callable[[list[str]], subprocess.CompletedProcess[str]] = (
        _default_executor
    ),
    _allow_test_output_root: bool = False,
) -> RuntimeValidationResult:
    """Run each isolated worker mode once and publish sanitized OS evidence."""
    repo_root = repo_root.resolve(strict=True)
    reports_root = _resolve_reports_root(
        repo_root,
        reports_root,
        allow_test_output_root=_allow_test_output_root,
    )
    errors: list[str] = []
    cleanup_complete = True
    docker_evidence: dict[str, Any] = {}
    image_evidence: dict[str, Any] = {}
    candidate_contract = _blocked_contract()
    probe_contract = _blocked_contract()
    host_evidence = _blocked_host_evidence()
    probe_evidence = {
        "all_blocked": False,
        "network_blocked": False,
        "shell_blocked_count": 0,
        "forbidden_read_blocked_count": 0,
        "forbidden_write_blocked_count": 0,
    }
    projection_sha256 = ""

    try:
        docker_evidence = _runtime_server_evidence(
            _run_command(
                _executor,
                ["docker", "version", "--format", "{{json .Server}}"],
                "docker_runtime_unavailable",
            ).stdout
        )
        image_evidence = _image_evidence(
            _run_command(
                _executor,
                ["docker", "image", "inspect", _WORKER_IMAGE],
                "worker_image_unavailable",
            ).stdout
        )
        facts_path = repo_root / "reports/phase2_facts/000403SZ_facts.json"
        facts_bytes = _read_regular_file(facts_path, "facts_source_invalid")
        facts = _json_object(facts_bytes, "facts_source_invalid")
        facts_sha256 = hashlib.sha256(facts_bytes).hexdigest()
        projection = build_worker_projection(
            facts,
            facts_sha256,
            facts_bytes=facts_bytes,
        )
        projection_bytes = canonical_json_bytes(projection)
        projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()

        with tempfile.TemporaryDirectory(prefix="tickflow-phase2-isolation-") as raw:
            temporary = Path(raw)
            projection_path = temporary / "projection.json"
            output_path = temporary / "candidate.json"
            _write_durable(projection_path, projection_bytes, 0o444)
            _write_durable(output_path, b"\n", 0o666)
            paths = IsolationPaths(projection=projection_path, output=output_path)

            candidate_run = _run_container_once(_executor, paths, "candidate")
            candidate_contract = candidate_run.contract
            cleanup_complete = cleanup_complete and candidate_run.cleanup_complete
            if hashlib.sha256(projection_path.read_bytes()).hexdigest() != projection_sha256:
                raise Phase2IsolationRuntimeError("projection_mutated_by_candidate")
            host_evidence = validate_isolated_candidate(
                repo_root,
                candidate_run.output,
                projection,
            )
            if host_evidence.errors:
                raise Phase2IsolationRuntimeError("candidate_host_validation_blocked")

            probe_run = _run_container_once(_executor, paths, "probe")
            probe_contract = probe_run.contract
            cleanup_complete = cleanup_complete and probe_run.cleanup_complete
            if hashlib.sha256(projection_path.read_bytes()).hexdigest() != projection_sha256:
                raise Phase2IsolationRuntimeError("projection_mutated_by_probe")
            probe_evidence = _validate_probe(probe_run.output)
    except Phase2IsolationRuntimeError as exc:
        error_code = str(exc)
        errors.append(error_code)
        if error_code.endswith("_cleanup_failed"):
            cleanup_complete = False

    status = _RUNTIME_VERIFIED if not errors and cleanup_complete else _RUNTIME_BLOCKED
    evidence = {
        "status": status,
        "errors": sorted(set(errors)),
        "docker": docker_evidence,
        "image": image_evidence,
        "runtime_contract": {
            "candidate": candidate_contract.to_dict(),
            "probe": probe_contract.to_dict(),
        },
        "host_validation": _host_evidence_for_report(host_evidence),
        "probe": probe_evidence,
        "projection_sha256": projection_sha256,
        "cleanup_complete": cleanup_complete,
        "external_actions": {
            "ai_call_count": 0,
            "cloud_mutation_count": 0,
            "external_send_count": 0,
            "integrated_gold_enabled": False,
            "obsidian_real_vault_write": False,
            "paper_trading_started": False,
            "provider_attempt_count": 0,
            "tickflow_api_request_count": 0,
        },
    }
    _publish_runtime_evidence(reports_root, evidence)
    return RuntimeValidationResult(
        status=status,
        errors=sorted(set(errors)),
        cleanup_complete=cleanup_complete,
        evidence=evidence,
    )
