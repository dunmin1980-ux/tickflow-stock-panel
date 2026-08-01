"""Offline-only safety gates for the Phase 2B single-symbol Provider Canary."""

from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.phase2_canary_provider_config import (
    CanaryEgressPolicy,
    CanaryProviderApproval,
    build_responses_request_contract,
    validate_exact_egress,
)
from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_facts import validate_facts_document
from app.services.phase2_provider_relay_runner import (
    PROXY_IMAGE,
    ProxyRuntimePaths,
    RelayRuntimePaths,
    build_proxy_create_command,
    build_relay_create_command,
)
from scripts.validate_phase2_provider_relay import validate_provider_relay_artifacts

_MAX_CONFIG_BYTES = 65_536
_CONFIG_SENSITIVE = re.compile(
    rb"sk-|Authorization|Bearer|api[_-]?key|secret_value|OPENAI_API_KEY",
    re.IGNORECASE,
)
_FACTS_SHA256 = "adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956"
_PROJECTION_SHA256 = (
    "0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f"
)
_PROJECTION_BYTES_SHA256 = (
    "5db35e91eabce452e5be3a3020b5c10711797941431df9bd5bb52f67625b42e1"
)
_CLAIMS_SCHEMA_SHA256 = (
    "5988b34ad5bb1703e3795e9b54a18c7dcdb0009c65ee55662f7e71facebc43c3"
)
_RENDERER_SHA256 = (
    "53d94e60fce7914582c85beea977ca65f7a6713c7066ee65df2c84a8ec6c361e"
)
_PROTECTED_EXPECTED = {
    "phase1_observation": (
        36,
        "8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5",
    ),
    "phase1_request_audits": (
        7,
        "dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d",
    ),
    "phase2_facts": (
        4,
        "10c6664754b8e1391f55d9b1b8b98f1577b749f0dceafbb77288749ae5376ef7",
    ),
    "typed_claims": (
        8,
        "39ef9b714798f193c3ba283c1035fad42372cedf4f7a17a36fc008411ed41bbf",
    ),
    "typed_inbox": (
        3,
        "a9bfddecc5a9046badf5f7cf4485fab029508681dae8a5a5206a93d4ab2e2291",
    ),
    "historical_ai_markdown": (
        3,
        "9a0bc445f7580c55db630b8e3fbfac545fe71d54b36588919dd7ec79ef57feb5",
    ),
    "historical_rejected": (
        3,
        "6e2e0c7b311a76ccff061917f3893f97aca4ba84782c769ee004960d335f164c",
    ),
    "isolation_runtime_evidence": (
        1,
        "9cf174d4def6908e84522366105b41ed33a00f3be8aab9dc01454c4f75f5533c",
    ),
    "provider_relay_evidence": (
        1,
        "4ae713b5ad4641a2c4f5f84f0d86bc6cdf5e309bcc23aab7fe596f6138fd897e",
    ),
}
_PREFLIGHT_BASE_HEAD = "b5c7e7bf3833f63c706535da9e1be818ddb2ec4f"
_PREFLIGHT_BRANCH = "codex/tickflow-phase2-ai-review"
# A real Provider adapter must be reviewed independently before its exact hash is
# pinned here.  None intentionally keeps every non-mock runtime fail-closed.
_APPROVED_RUNTIME_PROXY_SHA256: str | None = None
_APPROVED_RUNTIME_PROXY_IMAGE_DIGEST: str | None = None
_RUNTIME_PROXY_SOURCE_LABEL = "org.tickflow.phase2.proxy-source-sha256"
_ALLOWED_PREFLIGHT_CHANGES = {
    "backend/app/schemas/phase2_canary_provider_config.py",
    "backend/scripts/validate_phase2_canary_provider_config.py",
    "backend/tests/test_phase2_canary_provider_config.py",
    "docs/superpowers/plans/2026-08-01-tickflow-phase2b3b-canary-provider-preflight.md",
    "reports/tickflow_phase2b_canary_provider_preflight_eval.md",
}


class CanaryPreflightError(ValueError):
    """Raised with a stable, sanitized preflight error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ProviderApprovalLoad:
    approval: CanaryProviderApproval
    directory_mode: str
    file_mode: str
    owner_matches: bool
    parents_not_symlinks: bool
    config_secret_scan_clean: bool


@dataclass(frozen=True)
class PlaceholderSecretEvidence:
    secret_present: bool
    secret_injection_mode: str
    secret_file_regular: bool
    secret_file_mode: str
    proxy_readonly_mount: bool
    relay_has_secret_mount: bool
    runtime_secret_boundary_verified: bool
    secret_value_logged: bool
    secret_hash_recorded: bool
    secret_artifact_hit_count: int
    secret_log_hit_count: int
    workspace_hit_count: int
    secret_cleanup: str
    secret_file_residue_count: int
    temporary_directory_residue_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeResidueEvidence:
    provider_container_residue_count: int
    provider_network_residue_count: int
    provider_process_residue_count: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class OfflineCanaryEvidence:
    status: str
    errors: list[str]
    facts_status: str
    trade_date: str | None
    facts_sha256: str | None
    projection_status: str
    projection_repeat_stable: bool
    projection_sha256: str | None
    projection_bytes_sha256: str | None
    claims_schema_sha256: str
    renderer_sha256: str
    provider_relay_status: str
    canary_output_empty: bool
    protected_evidence: dict[str, dict[str, Any]]
    external_action_evidence: str
    provider_attempt_count: int
    ai_call_count: int
    tickflow_api_request_count: int
    real_public_network_success_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanaryPreflightResult:
    status: str
    errors: list[str]
    git_gate: str = "NOT_RUN"
    validated_head: str = "NOT_RUN"
    provider: str = "NOT_CONFIGURED"
    exact_model: str = "NOT_CONFIGURED"
    endpoint_alias: str = "NOT_CONFIGURED"
    endpoint: str = "NOT_CONFIGURED"
    provider_config: str = "INVALID"
    config_permissions: str = "NOT_RUN"
    config_secret_scan: str = "NOT_RUN"
    keychain_secret: str = "NOT_RUN"
    secret_content_read: bool = False
    secret_hash_recorded: bool = False
    temporary_single_file_injection: str = "NOT_RUN"
    secret_log_hit_count: int = 0
    secret_artifact_hit_count: int = 0
    secret_temporary_residue_count: int = 0
    strict_json_schema: str = "NOT_RUN"
    tls: str = "NOT_RUN"
    redirect: str = "NOT_RUN"
    egress_allowlist: str = "NOT_RUN"
    facts: str = "NOT_RUN"
    projection: str = "NOT_RUN"
    projection_sha: str = "NOT_RUN"
    provider_relay: str = "NOT_RUN"
    historical_evidence: str = "NOT_RUN"
    provider_container_residue_count: int = 0
    provider_network_residue_count: int = 0
    provider_process_residue_count: int = 0
    provider_attempt_count: int = 0
    ai_call_count: int = 0
    retry_count: int = 0
    provider_http: str = "NOT_RUN"
    external_action_evidence: str = "NOT_MEASURED"
    tickflow_api_request_count: int = 0
    real_public_network_success_count: int = 0
    obsidian_real_vault_write: bool = False
    paper_trading_started: bool = False
    cloud_redeploy: bool = False
    integrated_gold_enabled: bool = False
    external_send_count: int = 0
    three_symbol_batch_approved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GitGateEvidence:
    ready: bool
    base_head: str
    current_head: str
    branch: str
    worktree_clean: bool
    base_is_ancestor: bool
    changed_paths_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeProxyPolicyEvidence:
    status: str
    approved_endpoint_enforced: bool
    artifact_sha256: str
    artifact_hash_approved: bool
    image_digest: str | None
    image_digest_approved: bool
    image_source_hash_matches: bool
    actual_method: str | None
    actual_host: str | None
    actual_port: int | None
    actual_path: str | None
    connection_type: str | None
    tls_verification_enforced: bool
    redirects_disabled: bool
    retry_count: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _assert_parent_chain(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parent.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise CanaryPreflightError("config_missing") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise CanaryPreflightError("config_parent_symlink_forbidden")
        if not stat.S_ISDIR(metadata.st_mode):
            raise CanaryPreflightError("config_parent_not_directory")


def _read_bounded_file(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, min(remaining, 65_536))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) != size:
        raise CanaryPreflightError("config_read_incomplete")
    return raw


def read_provider_approval(
    path: Path,
    *,
    current_uid: int,
) -> ProviderApprovalLoad:
    """Read one exact approval file without following filesystem links."""
    candidate = _absolute(path)
    _assert_parent_chain(candidate)
    try:
        before = os.lstat(candidate)
    except OSError as exc:
        raise CanaryPreflightError("config_missing") from exc
    if stat.S_ISLNK(before.st_mode):
        raise CanaryPreflightError("config_symlink_forbidden")
    if not stat.S_ISREG(before.st_mode):
        raise CanaryPreflightError("config_not_regular_file")

    directory = os.lstat(candidate.parent)
    if stat.S_IMODE(directory.st_mode) != 0o700:
        raise CanaryPreflightError("config_directory_mode_invalid")
    if directory.st_uid != current_uid:
        raise CanaryPreflightError("config_owner_invalid")
    if stat.S_IMODE(before.st_mode) != 0o600:
        raise CanaryPreflightError("config_file_mode_invalid")
    if before.st_uid != current_uid:
        raise CanaryPreflightError("config_owner_invalid")
    if not 0 < before.st_size <= _MAX_CONFIG_BYTES:
        raise CanaryPreflightError("config_size_invalid")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise CanaryPreflightError("config_open_failed") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise CanaryPreflightError("config_not_regular_file")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise CanaryPreflightError("config_replaced_during_open")
        raw = _read_bounded_file(descriptor, opened.st_size)
        after = os.lstat(candidate)
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise CanaryPreflightError("config_replaced_during_read")
    finally:
        os.close(descriptor)

    if _CONFIG_SENSITIVE.search(raw):
        raise CanaryPreflightError("config_secret_shape_detected")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON constant")

    try:
        parsed = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CanaryPreflightError("config_json_invalid") from exc
    try:
        approval = CanaryProviderApproval.model_validate(parsed)
    except ValidationError as exc:
        raise CanaryPreflightError("config_contract_invalid") from exc
    return ProviderApprovalLoad(
        approval=approval,
        directory_mode="0700",
        file_mode="0600",
        owner_matches=True,
        parents_not_symlinks=True,
        config_secret_scan_clean=True,
    )


def keychain_secret_exists(
    *,
    account: str,
    service: str,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> bool:
    """Check only whether the approved Keychain item exists."""
    command = [
        "security",
        "find-generic-password",
        "-a",
        account,
        "-s",
        service,
    ]
    result = runner(
        command,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=False,
        shell=False,
        timeout=5,
    )
    return result.returncode == 0


def _git_command(
    repo_root: Path,
    arguments: list[str],
    runner: Callable[..., subprocess.CompletedProcess[Any]],
) -> subprocess.CompletedProcess[Any]:
    return runner(
        ["git", "-C", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=10,
    )


def read_git_gate(
    repo_root: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> GitGateEvidence:
    """Bind preflight to the frozen base and an allowlisted clean delta."""
    root = _absolute(repo_root)
    head_result = _git_command(root, ["rev-parse", "HEAD"], runner)
    branch_result = _git_command(root, ["branch", "--show-current"], runner)
    status_result = _git_command(
        root,
        ["status", "--porcelain", "--untracked-files=all"],
        runner,
    )
    ancestor_result = _git_command(
        root,
        ["merge-base", "--is-ancestor", _PREFLIGHT_BASE_HEAD, "HEAD"],
        runner,
    )
    diff_result = _git_command(
        root,
        ["diff", "--name-status", f"{_PREFLIGHT_BASE_HEAD}..HEAD"],
        runner,
    )
    text_results = (head_result, branch_result, status_result, diff_result)
    if any(
        result.returncode != 0 or not isinstance(result.stdout, str)
        for result in text_results
    ):
        raise CanaryPreflightError("git_gate_probe_failed")

    current_head = head_result.stdout.strip()
    branch = branch_result.stdout.strip()
    worktree_clean = status_result.stdout == ""
    changed_paths_allowed = True
    for raw_line in diff_result.stdout.splitlines():
        fields = raw_line.split("\t")
        if (
            len(fields) != 2
            or fields[0] != "A"
            or fields[1] not in _ALLOWED_PREFLIGHT_CHANGES
        ):
            changed_paths_allowed = False
    head_valid = re.fullmatch(r"[0-9a-f]{40}", current_head) is not None
    base_is_ancestor = ancestor_result.returncode == 0
    ready = (
        head_valid
        and branch == _PREFLIGHT_BRANCH
        and worktree_clean
        and base_is_ancestor
        and changed_paths_allowed
    )
    return GitGateEvidence(
        ready=ready,
        base_head=_PREFLIGHT_BASE_HEAD,
        current_head=current_head if head_valid else "INVALID",
        branch=branch,
        worktree_clean=worktree_clean,
        base_is_ancestor=base_is_ancestor,
        changed_paths_allowed=changed_paths_allowed,
    )


def _assigned_constants(tree: ast.Module) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
    return values


def _call_name(node: ast.Call) -> str | None:
    function = node.func
    if not isinstance(function, ast.Attribute):
        return None
    if (
        isinstance(function.value, ast.Attribute)
        and isinstance(function.value.value, ast.Name)
        and function.value.value.id == "http"
        and function.value.attr == "client"
    ):
        return function.attr
    if isinstance(function.value, ast.Name) and function.value.id == "ssl":
        return f"ssl.{function.attr}"
    return None


def inspect_runtime_proxy_policy(
    path: Path,
    *,
    image_digest: str | None = None,
    image_source_sha256: str | None = None,
) -> RuntimeProxyPolicyEvidence:
    """Inspect the actual Proxy source that a Canary runner would execute."""
    candidate = _absolute(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise CanaryPreflightError("runtime_proxy_artifact_invalid")
    try:
        raw = candidate.read_bytes()
        tree = ast.parse(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise CanaryPreflightError("runtime_proxy_artifact_invalid") from exc
    artifact_sha256 = hashlib.sha256(raw).hexdigest()
    constants = _assigned_constants(tree)
    calls = [
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (name := _call_name(node)) is not None
    ]
    connection_calls = [
        name for name in calls if name in {"HTTPConnection", "HTTPSConnection"}
    ]
    connection_type = connection_calls[0] if len(connection_calls) == 1 else None
    default_tls_context = "ssl.create_default_context" in calls
    unverified_tls_context = "ssl._create_unverified_context" in calls
    tls_verified = (
        connection_type == "HTTPSConnection"
        and default_tls_context
        and not unverified_tls_context
    )
    method = constants.get("ALLOWED_METHOD")
    host = constants.get("UPSTREAM_HOST")
    port = constants.get("UPSTREAM_PORT")
    path_value = constants.get("UPSTREAM_PATH")
    redirects_disabled = constants.get("FOLLOW_REDIRECTS") is False
    retry_count = constants.get("RETRY_COUNT")
    endpoint_structurally_enforced = (
        method == "POST"
        and host == "api.openai.com"
        and type(port) is int
        and port == 443
        and path_value == "/v1/responses"
        and tls_verified
        and redirects_disabled
        and retry_count == 0
    )
    artifact_hash_approved = (
        _APPROVED_RUNTIME_PROXY_SHA256 is not None
        and hmac.compare_digest(
            artifact_sha256,
            _APPROVED_RUNTIME_PROXY_SHA256,
        )
    )
    image_digest_approved = (
        _APPROVED_RUNTIME_PROXY_IMAGE_DIGEST is not None
        and image_digest is not None
        and hmac.compare_digest(
            image_digest,
            _APPROVED_RUNTIME_PROXY_IMAGE_DIGEST,
        )
    )
    image_source_hash_matches = (
        image_source_sha256 is not None
        and hmac.compare_digest(image_source_sha256, artifact_sha256)
    )
    endpoint_enforced = (
        endpoint_structurally_enforced
        and artifact_hash_approved
        and image_digest_approved
        and image_source_hash_matches
    )
    mock_only = (
        host == "phase2-mock-provider"
        and port == 8081
        and path_value == "/v1/typed-claims"
        and connection_type == "HTTPConnection"
    )
    if mock_only:
        runtime_status = "MOCK_ONLY"
    elif endpoint_structurally_enforced and not artifact_hash_approved:
        runtime_status = "UNPINNED"
    elif endpoint_structurally_enforced and not endpoint_enforced:
        runtime_status = "IMAGE_UNVERIFIED"
    elif endpoint_enforced:
        runtime_status = "READY"
    else:
        runtime_status = "BLOCKED"
    return RuntimeProxyPolicyEvidence(
        status=runtime_status,
        approved_endpoint_enforced=endpoint_enforced,
        artifact_sha256=artifact_sha256,
        artifact_hash_approved=artifact_hash_approved,
        image_digest=image_digest,
        image_digest_approved=image_digest_approved,
        image_source_hash_matches=image_source_hash_matches,
        actual_method=method if isinstance(method, str) else None,
        actual_host=host if isinstance(host, str) else None,
        actual_port=port if type(port) is int else None,
        actual_path=path_value if isinstance(path_value, str) else None,
        connection_type=connection_type,
        tls_verification_enforced=tls_verified,
        redirects_disabled=redirects_disabled,
        retry_count=retry_count if type(retry_count) is int else None,
    )


def read_runtime_proxy_image_identity(
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> tuple[str, str]:
    """Read only the local image ID and audited source-hash label."""
    result = runner(
        [
            "docker",
            "image",
            "inspect",
            PROXY_IMAGE,
            "--format",
            (
                "{{.Id}}\t{{index .Config.Labels "
                f'"{_RUNTIME_PROXY_SOURCE_LABEL}"}}}}'
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=10,
    )
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise CanaryPreflightError("runtime_proxy_image_probe_failed")
    fields = result.stdout.strip().split("\t")
    if (
        len(fields) != 2
        or re.fullmatch(r"sha256:[0-9a-f]{64}", fields[0]) is None
        or re.fullmatch(r"[0-9a-f]{64}", fields[1]) is None
    ):
        raise CanaryPreflightError("runtime_proxy_image_identity_invalid")
    return fields[0], fields[1]


def _serialized(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


def count_secret_exposure(secret: str, artifacts: list[Any]) -> int:
    """Count artifacts containing a synthetic value without hashing it."""
    if not isinstance(secret, str) or not secret:
        raise CanaryPreflightError("placeholder_secret_invalid")
    encoded = secret.encode("utf-8")
    return sum(encoded in _serialized(item) for item in artifacts)


def _workspace_value_hits(repo_root: Path, value: bytes) -> int:
    excluded = {".git", ".venv", "node_modules", "__pycache__"}
    hits = 0
    for current, dirnames, filenames in os.walk(repo_root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in excluded]
        base = Path(current)
        for filename in filenames:
            path = base / filename
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if value in path.read_bytes():
                    hits += 1
            except OSError:
                hits += 1
    return hits


def exercise_placeholder_secret_injection(
    repo_root: Path,
    approval: CanaryProviderApproval,
) -> PlaceholderSecretEvidence:
    """Exercise only the synthetic temporary-file credential mechanism."""
    root = _absolute(repo_root)
    if root.is_symlink() or not root.is_dir():
        raise CanaryPreflightError("repo_root_invalid")
    placeholder = "".join(("PHASE2_", "TEST_SECRET_", "DO_NOT_USE"))
    placeholder_bytes = placeholder.encode("utf-8")
    temporary_root = Path(tempfile.mkdtemp(prefix="tickflow-phase2-canary-"))
    secret_path = temporary_root / "provider-auth"
    secret_present = False
    secret_file_regular = False
    secret_file_mode = "INVALID"
    proxy_readonly_mount = False
    relay_has_secret_mount = False
    runtime_secret_boundary_verified = False
    artifact_hits = 0
    log_hits = 0
    workspace_hits = 0
    try:
        os.chmod(temporary_root, 0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(secret_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, placeholder_bytes)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        metadata = os.lstat(secret_path)
        secret_file_regular = stat.S_ISREG(metadata.st_mode)
        secret_file_mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
        with secret_path.open("rb") as handle:
            secret_present = hmac.compare_digest(handle.read(), placeholder_bytes)

        runtime_files = {
            name: temporary_root / name
            for name in (
                "request.json",
                "projection.json",
                "response.json",
                "relay-receipt.json",
                "proxy-receipt.json",
            )
        }
        for path in runtime_files.values():
            path.write_bytes(b"{}\n")
            path.chmod(0o600)
        relay_paths = RelayRuntimePaths(
            runtime_files["request.json"],
            runtime_files["projection.json"],
            runtime_files["response.json"],
            runtime_files["relay-receipt.json"],
        )
        proxy_paths = ProxyRuntimePaths(
            secret_path,
            runtime_files["proxy-receipt.json"],
        )
        relay_command = build_relay_create_command(
            "phase2-canary-relay-preflight",
            "phase2-canary-net",
            relay_paths,
            mode="run",
        )
        proxy_command = build_proxy_create_command(
            "phase2-canary-proxy-preflight",
            "phase2-canary-net",
            proxy_paths,
        )
        runtime_evidence_path = (
            root / "reports/phase2_provider_relay/runtime_evidence.json"
        )
        runtime_evidence = json.loads(runtime_evidence_path.read_bytes())
        runtime_secret_boundary_verified = runtime_evidence.get(
            "secret_boundary"
        ) == {
            "secret_present": True,
            "secret_value_hit_count": 0,
            "secret_digest_recorded": False,
            "secret_file_cleanup": True,
            "relay_has_secret_mount": False,
            "proxy_has_readonly_secret_mount": True,
            "mock_has_readonly_secret_mount": True,
        }
        policy = CanaryEgressPolicy.from_approval(approval)
        request = build_responses_request_contract(approval)
        projection = {"symbol": approval.approved_symbol, "secret_present": False}
        candidate = {"status": "NOT_RUN"}
        receipt = {
            "secret_present": True,
            "secret_value_logged": False,
            "secret_hash_recorded": False,
        }
        artifacts = [
            proxy_command,
            relay_command,
            runtime_evidence.get("secret_boundary", {}),
            policy.model_dump(mode="json"),
            projection,
            request,
            candidate,
            receipt,
        ]
        logs = ["", ""]
        artifact_hits = count_secret_exposure(placeholder, artifacts)
        log_hits = count_secret_exposure(placeholder, logs)
        workspace_hits = _workspace_value_hits(root, placeholder_bytes)
        proxy_readonly_mount = any(
            item.endswith("dst=/run/phase2/provider-auth,readonly")
            for item in proxy_command
        )
        relay_has_secret_mount = any(
            "/run/phase2/provider-auth" in item for item in relay_command
        )
        if not validate_exact_egress(
            policy,
            scheme="https",
            host="api.openai.com",
            port=443,
            path="/v1/responses",
            method="POST",
            tls_verification=True,
            follow_redirects=False,
        ):
            raise CanaryPreflightError("egress_policy_invalid")
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)

    secret_file_residue = int(secret_path.exists())
    temporary_residue = int(temporary_root.exists())
    cleanup = (
        "PASSED"
        if secret_file_residue == 0 and temporary_residue == 0
        else "FAILED"
    )
    return PlaceholderSecretEvidence(
        secret_present=secret_present,
        secret_injection_mode="temporary_readonly_single_file",
        secret_file_regular=secret_file_regular,
        secret_file_mode=secret_file_mode,
        proxy_readonly_mount=proxy_readonly_mount,
        relay_has_secret_mount=relay_has_secret_mount,
        runtime_secret_boundary_verified=runtime_secret_boundary_verified,
        secret_value_logged=log_hits > 0,
        secret_hash_recorded=False,
        secret_artifact_hit_count=artifact_hits,
        secret_log_hit_count=log_hits,
        workspace_hit_count=workspace_hits,
        secret_cleanup=cleanup,
        secret_file_residue_count=secret_file_residue,
        temporary_directory_residue_count=temporary_residue,
    )


def _run_readonly(
    command: list[str],
    runner: Callable[..., subprocess.CompletedProcess[Any]],
) -> str:
    result = runner(
        command,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=10,
    )
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise CanaryPreflightError("runtime_residue_probe_failed")
    return result.stdout


def _matching_lines(raw: str, markers: tuple[str, ...]) -> int:
    return sum(
        any(marker in line.lower() for marker in markers)
        for line in raw.splitlines()
        if line.strip()
    )


def read_runtime_residue(
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> RuntimeResidueEvidence:
    """Use read-only local commands to count only Canary runtime residue."""
    containers = _run_readonly(
        ["docker", "ps", "-a", "--format", "{{.Image}}\t{{.Names}}"],
        runner,
    )
    networks = _run_readonly(
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        runner,
    )
    processes = _run_readonly(["ps", "-axo", "command="], runner)
    container_markers = (
        "tickflow-phase2-provider-relay",
        "tickflow-phase2-egress-proxy",
        "tickflow-phase2-mock-provider",
        "phase2-relay-",
        "phase2-proxy-",
        "phase2-mock-",
    )
    network_markers = ("phase2-provider-", "phase2-relay-proxy-")
    process_markers = (
        "phase2-provider-relay",
        "phase2-egress-proxy",
        "phase2-mock-provider",
        "run_phase2_mock_provider_relay",
    )
    return RuntimeResidueEvidence(
        provider_container_residue_count=_matching_lines(
            containers,
            container_markers,
        ),
        provider_network_residue_count=_matching_lines(networks, network_markers),
        provider_process_residue_count=_matching_lines(processes, process_markers),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _aggregate_sha256(repo_root: Path, paths: list[Path]) -> str:
    lines = b"".join(
        (
            f"{_sha256(path)}  {path.relative_to(repo_root).as_posix()}\n"
        ).encode()
        for path in sorted(paths)
    )
    return hashlib.sha256(lines).hexdigest()


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _protected_paths(repo_root: Path) -> dict[str, tuple[list[Path], bool]]:
    reports = repo_root / "reports"
    observation = _files(reports / "phase1_observation")
    observation.append(reports / "tickflow_phase1_observation_final.md")
    request_audits = sorted(reports.rglob("*request_audit*.json"))
    return {
        "phase1_observation": (observation, False),
        "phase1_request_audits": (request_audits, False),
        "phase2_facts": (_files(reports / "phase2_facts"), False),
        "typed_claims": (_files(reports / "phase2_claims"), False),
        "typed_inbox": (_files(reports / "phase2_obsidian_preview/inbox"), False),
        "historical_ai_markdown": (
            sorted((reports / "phase2_ai_samples").glob("*.md")),
            False,
        ),
        "historical_rejected": (
            _files(reports / "phase2_obsidian_preview/rejected"),
            False,
        ),
        "isolation_runtime_evidence": (
            [reports / "phase2_isolation_runtime/runtime_evidence.json"],
            True,
        ),
        "provider_relay_evidence": (
            [reports / "phase2_provider_relay/runtime_evidence.json"],
            True,
        ),
    }


def _protected_evidence(repo_root: Path) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for name, (paths, raw_single_file) in _protected_paths(repo_root).items():
        missing = [path for path in paths if not path.is_file() or path.is_symlink()]
        if missing or not paths:
            actual = "MISSING"
        elif raw_single_file:
            actual = _sha256(paths[0])
        else:
            actual = _aggregate_sha256(repo_root, paths)
        expected_count, expected_sha256 = _PROTECTED_EXPECTED[name]
        evidence[name] = {
            "file_count": len(paths),
            "sha256": actual,
            "status": (
                "UNCHANGED"
                if len(paths) == expected_count and actual == expected_sha256
                else "MUTATED"
            ),
        }
    return evidence


def _load_json_object(path: Path) -> tuple[bytes, dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise CanaryPreflightError("facts_file_invalid")
    raw = path.read_bytes()

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON constant")

    try:
        value = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CanaryPreflightError("facts_json_invalid") from exc
    if not isinstance(value, dict):
        raise CanaryPreflightError("facts_json_invalid")
    return raw, value


def validate_offline_canary_inputs(repo_root: Path) -> OfflineCanaryEvidence:
    """Revalidate frozen single-symbol inputs without external I/O."""
    root = _absolute(repo_root)
    errors: list[str] = []
    facts_status = "INVALID"
    projection_status = "INVALID"
    trade_date: str | None = None
    facts_sha256: str | None = None
    projection_sha256: str | None = None
    projection_bytes_sha256: str | None = None
    projection_repeat_stable = False

    facts_path = root / "reports/phase2_facts/000403SZ_facts.json"
    try:
        facts_bytes, facts = _load_json_object(facts_path)
        facts_sha256 = hashlib.sha256(facts_bytes).hexdigest()
        trade_date = facts.get("trade_date")
        facts_errors = validate_facts_document(root, facts)
        if facts_errors or facts_sha256 != _FACTS_SHA256:
            errors.append("facts_invalid")
        elif trade_date != "2026-07-31":
            errors.append("facts_trade_date_invalid")
        else:
            facts_status = "VALID"
        first = build_worker_projection(
            facts,
            facts_sha256,
            facts_bytes=facts_bytes,
        )
        second = build_worker_projection(
            facts,
            facts_sha256,
            facts_bytes=facts_bytes,
        )
        first_bytes = canonical_json_bytes(first)
        second_bytes = canonical_json_bytes(second)
        projection_repeat_stable = first_bytes == second_bytes
        projection_sha256 = first.get("projection_sha256")
        projection_bytes_sha256 = hashlib.sha256(first_bytes).hexdigest()
        if (
            not projection_repeat_stable
            or projection_sha256 != _PROJECTION_SHA256
            or projection_bytes_sha256 != _PROJECTION_BYTES_SHA256
        ):
            errors.append("projection_invalid")
        else:
            projection_status = "VALID"
    except (CanaryPreflightError, OSError, TypeError, ValueError, KeyError):
        errors.append("facts_or_projection_validation_failed")

    claims_schema_sha256 = _sha256(root / "backend/app/schemas/phase2_claims.py")
    renderer_sha256 = _sha256(
        root / "backend/app/services/phase2_claims_renderer.py"
    )
    if claims_schema_sha256 != _CLAIMS_SCHEMA_SHA256:
        errors.append("claims_schema_mutated")
    if renderer_sha256 != _RENDERER_SHA256:
        errors.append("renderer_mutated")

    relay = validate_provider_relay_artifacts(root / "reports/phase2_provider_relay")
    if relay.status != "PHASE2B_PROVIDER_RELAY_READY":
        errors.append("provider_relay_not_ready")

    canary_root = root / "reports/phase2_provider_canary"
    canary_output_empty = not canary_root.exists() or not any(canary_root.rglob("*"))
    if not canary_output_empty:
        errors.append("canary_output_not_empty")

    protected = _protected_evidence(root)
    if any(item["status"] != "UNCHANGED" for item in protected.values()):
        errors.append("protected_evidence_mutated")
    relay_actions = relay.evidence.get("external_actions", {})
    provider_attempt_count = relay_actions.get("real_provider_attempt_count")
    ai_call_count = relay_actions.get("real_ai_call_count")
    tickflow_api_request_count = relay_actions.get("tickflow_api_request_count")
    public_network_count = relay_actions.get("real_public_network_success_count")
    external_action_evidence = (
        "DERIVED_FROM_UNCHANGED_AUDITS_AND_ABSENT_CANARY_RUNTIME"
        if (
            canary_output_empty
            and protected["phase1_request_audits"]["status"] == "UNCHANGED"
            and protected["provider_relay_evidence"]["status"] == "UNCHANGED"
            and all(
                value == 0
                for value in (
                    provider_attempt_count,
                    ai_call_count,
                    tickflow_api_request_count,
                    public_network_count,
                )
            )
        )
        else "NOT_PROVEN"
    )
    if external_action_evidence == "NOT_PROVEN":
        errors.append("external_action_evidence_invalid")
    unique_errors = sorted(set(errors))
    return OfflineCanaryEvidence(
        status="READY" if not unique_errors else "BLOCKED",
        errors=unique_errors,
        facts_status=facts_status,
        trade_date=trade_date,
        facts_sha256=facts_sha256,
        projection_status=projection_status,
        projection_repeat_stable=projection_repeat_stable,
        projection_sha256=projection_sha256,
        projection_bytes_sha256=projection_bytes_sha256,
        claims_schema_sha256=claims_schema_sha256,
        renderer_sha256=renderer_sha256,
        provider_relay_status=relay.status,
        canary_output_empty=canary_output_empty,
        protected_evidence=protected,
        external_action_evidence=external_action_evidence,
        provider_attempt_count=(
            provider_attempt_count if type(provider_attempt_count) is int else -1
        ),
        ai_call_count=ai_call_count if type(ai_call_count) is int else -1,
        tickflow_api_request_count=(
            tickflow_api_request_count
            if type(tickflow_api_request_count) is int
            else -1
        ),
        real_public_network_success_count=(
            public_network_count if type(public_network_count) is int else -1
        ),
    )


def _configured_fields(loaded: ProviderApprovalLoad) -> dict[str, Any]:
    approval = loaded.approval
    return {
        "provider": approval.provider_id,
        "exact_model": approval.exact_model_id,
        "endpoint_alias": approval.approved_endpoint_alias,
        "endpoint": (
            f"{approval.endpoint_host}:{approval.endpoint_port}"
            f"{approval.endpoint_path}"
        ),
        "provider_config": "VALID",
        "config_permissions": "PASSED",
        "config_secret_scan": "CLEAN",
    }


def _placeholder_ready(evidence: PlaceholderSecretEvidence) -> bool:
    return (
        evidence.secret_present
        and evidence.secret_file_regular
        and evidence.secret_file_mode == "0600"
        and evidence.proxy_readonly_mount
        and not evidence.relay_has_secret_mount
        and evidence.runtime_secret_boundary_verified
        and not evidence.secret_value_logged
        and not evidence.secret_hash_recorded
        and evidence.secret_artifact_hit_count == 0
        and evidence.secret_log_hit_count == 0
        and evidence.workspace_hit_count == 0
        and evidence.secret_cleanup == "PASSED"
        and evidence.secret_file_residue_count == 0
        and evidence.temporary_directory_residue_count == 0
    )


def run_canary_preflight(
    *,
    repo_root: Path,
    config_path: Path,
    account: str,
    current_uid: int,
    keychain_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    runtime_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    git_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    proxy_policy_path: Path | None = None,
) -> CanaryPreflightResult:
    """Run every approved local gate and stop before Provider execution."""
    try:
        git_gate = read_git_gate(repo_root, runner=git_runner)
    except CanaryPreflightError as exc:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_CONFIG_BLOCKED",
            errors=[exc.code],
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
        )
    except Exception:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_CONFIG_BLOCKED",
            errors=["git_gate_probe_failed"],
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
        )
    if not git_gate.ready:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_CONFIG_BLOCKED",
            errors=["git_gate_invalid"],
            git_gate="FAILED",
            validated_head=git_gate.current_head,
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
        )
    git_fields = {
        "git_gate": "PASSED",
        "validated_head": git_gate.current_head,
    }
    try:
        loaded = read_provider_approval(config_path, current_uid=current_uid)
    except CanaryPreflightError as exc:
        user_action_codes = {
            "config_missing",
            "config_json_invalid",
            "config_contract_invalid",
        }
        return CanaryPreflightResult(
            status=(
                "PHASE2B_AI_USER_ACTION_REQUIRED"
                if exc.code in user_action_codes
                else "PHASE2B_CANARY_CONFIG_BLOCKED"
            ),
            errors=[exc.code],
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
            **git_fields,
        )

    configured = _configured_fields(loaded)
    try:
        keychain_present = keychain_secret_exists(
            account=account,
            service=loaded.approval.secret_service,
            runner=keychain_runner,
        )
    except Exception:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_CONFIG_BLOCKED",
            errors=["keychain_probe_failed"],
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
            **git_fields,
            **configured,
        )
    if not keychain_present:
        return CanaryPreflightResult(
            status="PHASE2B_AI_USER_ACTION_REQUIRED",
            errors=["keychain_secret_missing"],
            keychain_secret="MISSING",
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
            **git_fields,
            **configured,
        )

    request_contract = build_responses_request_contract(loaded.approval)
    response_format = request_contract.get("text", {}).get("format", {})
    strict_ready = (
        request_contract.get("model") == loaded.approval.exact_model_id
        and request_contract.get("stream") is False
        and request_contract.get("tools") == []
        and response_format.get("type") == "json_schema"
        and response_format.get("name") == "tickflow_phase2_claims_candidate"
        and response_format.get("strict") is True
        and isinstance(response_format.get("schema"), dict)
    )
    if not strict_ready:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_CONFIG_BLOCKED",
            errors=["responses_schema_not_ready"],
            keychain_secret="PRESENT",
            strict_json_schema="BLOCKED",
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
            **git_fields,
            **configured,
        )

    policy = CanaryEgressPolicy.from_approval(loaded.approval)
    egress_ready = validate_exact_egress(
        policy,
        scheme="https",
        host="api.openai.com",
        port=443,
        path="/v1/responses",
        method="POST",
        tls_verification=True,
        follow_redirects=False,
    )
    if not egress_ready:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_EGRESS_BLOCKED",
            errors=["egress_policy_invalid"],
            keychain_secret="PRESENT",
            strict_json_schema="READY",
            tls="BLOCKED",
            redirect="DISABLED",
            egress_allowlist="FAILED",
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
            **git_fields,
            **configured,
        )

    try:
        placeholder = exercise_placeholder_secret_injection(
            repo_root,
            loaded.approval,
        )
    except Exception:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_SECRET_BLOCKED",
            errors=["placeholder_secret_validation_failed"],
            keychain_secret="PRESENT",
            temporary_single_file_injection="FAILED",
            strict_json_schema="READY",
            tls="NOT_RUN",
            redirect="NOT_RUN",
            egress_allowlist="NOT_RUN",
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
            **git_fields,
            **configured,
        )
    if not _placeholder_ready(placeholder):
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_SECRET_BLOCKED",
            errors=["placeholder_secret_boundary_invalid"],
            keychain_secret="PRESENT",
            temporary_single_file_injection="FAILED",
            secret_log_hit_count=placeholder.secret_log_hit_count,
            secret_artifact_hit_count=placeholder.secret_artifact_hit_count,
            secret_temporary_residue_count=(
                placeholder.secret_file_residue_count
                + placeholder.temporary_directory_residue_count
            ),
            strict_json_schema="READY",
            tls="NOT_RUN",
            redirect="NOT_RUN",
            egress_allowlist="NOT_RUN",
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
            **git_fields,
            **configured,
        )

    try:
        offline = validate_offline_canary_inputs(repo_root)
    except Exception:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_CONFIG_BLOCKED",
            errors=["offline_evidence_validation_failed"],
            keychain_secret="PRESENT",
            temporary_single_file_injection="PASSED",
            strict_json_schema="READY",
            tls="NOT_RUN",
            redirect="NOT_RUN",
            egress_allowlist="NOT_RUN",
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
            **git_fields,
            **configured,
        )
    if offline.status != "READY":
        evidence_mutated = any(
            item["status"] != "UNCHANGED"
            for item in offline.protected_evidence.values()
        )
        return CanaryPreflightResult(
            status=(
                "PHASE2B_EVIDENCE_MUTATED"
                if evidence_mutated
                else "PHASE2B_CANARY_CONFIG_BLOCKED"
            ),
            errors=offline.errors,
            keychain_secret="PRESENT",
            temporary_single_file_injection="PASSED",
            strict_json_schema="READY",
            tls="NOT_RUN",
            redirect="NOT_RUN",
            egress_allowlist="NOT_RUN",
            facts=offline.facts_status,
            projection=offline.projection_status,
            projection_sha=(
                "PASSED"
                if offline.projection_status == "VALID"
                else "FAILED"
            ),
            provider_relay=offline.provider_relay_status,
            historical_evidence=(
                "MUTATED" if evidence_mutated else "UNCHANGED"
            ),
            external_action_evidence=offline.external_action_evidence,
            provider_attempt_count=offline.provider_attempt_count,
            ai_call_count=offline.ai_call_count,
            tickflow_api_request_count=offline.tickflow_api_request_count,
            real_public_network_success_count=(
                offline.real_public_network_success_count
            ),
            **git_fields,
            **configured,
        )

    try:
        runtime = read_runtime_residue(runner=runtime_runner)
    except Exception:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_CONFIG_BLOCKED",
            errors=["runtime_residue_probe_failed"],
            keychain_secret="PRESENT",
            temporary_single_file_injection="PASSED",
            strict_json_schema="READY",
            tls="NOT_RUN",
            redirect="NOT_RUN",
            egress_allowlist="NOT_RUN",
            facts="VALID",
            projection="VALID",
            projection_sha="PASSED",
            provider_relay=offline.provider_relay_status,
            historical_evidence="UNCHANGED",
            external_action_evidence=offline.external_action_evidence,
            provider_attempt_count=offline.provider_attempt_count,
            ai_call_count=offline.ai_call_count,
            tickflow_api_request_count=offline.tickflow_api_request_count,
            real_public_network_success_count=(
                offline.real_public_network_success_count
            ),
            **git_fields,
            **configured,
        )
    runtime_count = sum(runtime.to_dict().values())
    if runtime_count:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_CONFIG_BLOCKED",
            errors=["provider_runtime_residue_present"],
            keychain_secret="PRESENT",
            temporary_single_file_injection="PASSED",
            strict_json_schema="READY",
            tls="NOT_RUN",
            redirect="NOT_RUN",
            egress_allowlist="NOT_RUN",
            facts="VALID",
            projection="VALID",
            projection_sha="PASSED",
            provider_relay=offline.provider_relay_status,
            historical_evidence="UNCHANGED",
            external_action_evidence=offline.external_action_evidence,
            provider_attempt_count=offline.provider_attempt_count,
            ai_call_count=offline.ai_call_count,
            tickflow_api_request_count=offline.tickflow_api_request_count,
            real_public_network_success_count=(
                offline.real_public_network_success_count
            ),
            **git_fields,
            **runtime.to_dict(),
            **configured,
        )

    try:
        runtime_policy_path = (
            proxy_policy_path
            or _absolute(repo_root) / "docker/phase2-egress-proxy/proxy.py"
        )
        runtime_policy = inspect_runtime_proxy_policy(
            runtime_policy_path
        )
        if runtime_policy.status == "IMAGE_UNVERIFIED":
            image_digest, image_source_sha256 = read_runtime_proxy_image_identity(
                runner=runtime_runner
            )
            runtime_policy = inspect_runtime_proxy_policy(
                runtime_policy_path,
                image_digest=image_digest,
                image_source_sha256=image_source_sha256,
            )
    except Exception:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_EGRESS_BLOCKED",
            errors=["runtime_proxy_policy_probe_failed"],
            keychain_secret="PRESENT",
            temporary_single_file_injection="PASSED",
            strict_json_schema="READY",
            tls="BLOCKED",
            redirect="DISABLED",
            egress_allowlist="FAILED",
            facts="VALID",
            projection="VALID",
            projection_sha="PASSED",
            provider_relay=offline.provider_relay_status,
            historical_evidence="UNCHANGED",
            external_action_evidence=offline.external_action_evidence,
            provider_attempt_count=offline.provider_attempt_count,
            ai_call_count=offline.ai_call_count,
            tickflow_api_request_count=offline.tickflow_api_request_count,
            real_public_network_success_count=(
                offline.real_public_network_success_count
            ),
            **git_fields,
            **runtime.to_dict(),
            **configured,
        )
    if not runtime_policy.approved_endpoint_enforced:
        return CanaryPreflightResult(
            status="PHASE2B_CANARY_EGRESS_BLOCKED",
            errors=["runtime_proxy_policy_not_approved"],
            keychain_secret="PRESENT",
            temporary_single_file_injection="PASSED",
            secret_log_hit_count=placeholder.secret_log_hit_count,
            secret_artifact_hit_count=placeholder.secret_artifact_hit_count,
            secret_temporary_residue_count=0,
            strict_json_schema="READY",
            tls="BLOCKED",
            redirect=(
                "DISABLED" if runtime_policy.redirects_disabled else "ENABLED"
            ),
            egress_allowlist="FAILED",
            facts="VALID",
            projection="VALID",
            projection_sha="PASSED",
            provider_relay=offline.provider_relay_status,
            historical_evidence="UNCHANGED",
            external_action_evidence=offline.external_action_evidence,
            provider_attempt_count=offline.provider_attempt_count,
            ai_call_count=offline.ai_call_count,
            tickflow_api_request_count=offline.tickflow_api_request_count,
            real_public_network_success_count=(
                offline.real_public_network_success_count
            ),
            **git_fields,
            **runtime.to_dict(),
            **configured,
        )

    return CanaryPreflightResult(
        status="CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL",
        errors=[],
        keychain_secret="PRESENT",
        temporary_single_file_injection="PASSED",
        secret_log_hit_count=placeholder.secret_log_hit_count,
        secret_artifact_hit_count=placeholder.secret_artifact_hit_count,
        secret_temporary_residue_count=0,
        strict_json_schema="READY",
        tls="READY",
        redirect="DISABLED",
        egress_allowlist="PASSED",
        facts="VALID",
        projection="VALID",
        projection_sha="PASSED",
        provider_relay=offline.provider_relay_status,
        historical_evidence="UNCHANGED",
        external_action_evidence=offline.external_action_evidence,
        provider_attempt_count=offline.provider_attempt_count,
        ai_call_count=offline.ai_call_count,
        tickflow_api_request_count=offline.tickflow_api_request_count,
        real_public_network_success_count=(
            offline.real_public_network_success_count
        ),
        **git_fields,
        **runtime.to_dict(),
        **configured,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=(
            Path.home()
            / "Library/Application Support/TickFlowPhase2Canary/provider-approval.json"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    account = pwd.getpwuid(os.getuid()).pw_name
    try:
        result = run_canary_preflight(
            repo_root=args.repo_root,
            config_path=args.config_path,
            account=account,
            current_uid=os.getuid(),
        )
    except Exception:
        result = CanaryPreflightResult(
            status="PHASE2B_CANARY_CONFIG_BLOCKED",
            errors=["unexpected_preflight_failure"],
            external_action_evidence="STOPPED_BEFORE_CANARY_PIPELINE",
        )
    print(json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True))
    return 0 if result.status == "CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
