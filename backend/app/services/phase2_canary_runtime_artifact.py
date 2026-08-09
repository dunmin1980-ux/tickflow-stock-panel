"""Immutable offline artifacts for the one-shot Phase 2 Provider Canary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_canary_runtime_contract import (
    load_runtime_contract,
    runtime_contract_sha256,
)
from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_openai_proxy_contract import (
    canonical_proxy_contract_bytes,
    proxy_policy_sha256,
)

BASE_IMAGE_DIGEST = (
    "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)
BASE_IMAGE_REFERENCE = "gcr.io/distroless/python3-debian12@" + BASE_IMAGE_DIGEST
PROXY_IMAGE_NAME = "tickflow-phase2-openai-egress-proxy:runtime-v1"
RELAY_IMAGE_NAME = "tickflow-phase2-canary-relay:runtime-v1"
RUNTIME_USER = "65532:65532"
READY_STATUS = "PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL"
OLD_CANDIDATE_SHA256 = (
    "45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127"
)

_PROXY_CONTEXT = Path("docker/phase2-openai-egress-proxy")
_RELAY_CONTEXT = Path("docker/phase2-canary-relay")
_ORCHESTRATOR = Path("backend/app/services/phase2_canary_orchestrator.py")
_OBSERVABILITY = Path("backend/app/services/phase2_canary_observability.py")
_LAUNCHER = Path("backend/scripts/run_phase2_single_symbol_canary.py")
_ARTIFACT_VERIFIER = Path(
    "backend/app/services/phase2_canary_runtime_artifact.py"
)
_ARTIFACT_BUILDER = Path("backend/scripts/build_phase2_canary_runtime_offline.py")
_RUNBOOK = Path("docs/phase2-single-call-orchestrator-runbook.md")
_FACTS = Path("reports/phase2_facts/000403SZ_facts.json")
_OLD_CANDIDATE = Path(
    "reports/phase2_provider_canary/superseded/"
    "45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127.json"
)
_PROXY_FILES = {
    "Dockerfile",
    "proxy.py",
    "responses-contract.json",
    "runtime-contract.json",
    "readiness-contract.json",
    "secret.placeholder",
    "receipt.placeholder.json",
}
_RELAY_FILES = {
    "Dockerfile",
    "relay.py",
    "runtime-contract.json",
    "request.placeholder.json",
    "projection.placeholder.json",
    "output.placeholder",
}
_PROXY_ENTRYPOINT = ("/usr/bin/python3", "/proxy/proxy.py")
_RELAY_ENTRYPOINT = ("/usr/bin/python3", "/relay/relay.py")
_PROXY_ENV = {
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
    "LANG=C.UTF-8",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
}
_RELAY_ENV = {
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
    "LANG=C.UTF-8",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
}
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SENSITIVE_HISTORY = re.compile(
    r"Authorization:|Bearer |api[_-]?key\s*[:=]|"
    r"(?:password|token|credential)\s*[:=]|"
    r"BEGIN [A-Z ]*PRIVATE KEY|--mount=type=(?:secret|ssh)",
    re.IGNORECASE,
)


class RuntimeArtifactError(ValueError):
    """Stable fail-closed runtime artifact validation error."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RuntimeArtifactInputHashes(_StrictFrozenModel):
    proxy_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proxy_dockerfile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    responses_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proxy_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relay_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relay_dockerfile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readiness_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    orchestrator_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observability_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    launcher_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_verifier_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_builder_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runbook_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocalBaseImageEvidence(_StrictFrozenModel):
    base_available: Literal[True]
    base_reference: Literal[
        "gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
    ]
    image_id: Literal[
        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
    ]
    rootfs_layers: tuple[str, ...]


class RuntimeImageEvidence(_StrictFrozenModel):
    role: Literal["proxy", "relay"]
    image_valid: Literal[True]
    history_clean: Literal[True]
    image_name: str
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_image_digest: Literal[
        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
    ]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dockerfile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readiness_contract_sha256: str | None = None
    responses_contract_sha256: str | None = None
    proxy_policy_sha256: str | None = None
    runtime_user: Literal["65532:65532"]
    entrypoint: tuple[str, ...]
    base_rootfs_prefix_verified: Literal[True]
    rootfs_layer_count: int = Field(ge=1)

    @model_validator(mode="after")
    def require_role_identity(self) -> RuntimeImageEvidence:
        expected = {
            "proxy": (PROXY_IMAGE_NAME, _PROXY_ENTRYPOINT),
            "relay": (RELAY_IMAGE_NAME, _RELAY_ENTRYPOINT),
        }[self.role]
        if (self.image_name, self.entrypoint) != expected:
            raise ValueError("artifact_image_role_identity_invalid")
        if self.role == "proxy" and (
            self.responses_contract_sha256 is None
            or self.proxy_policy_sha256 is None
            or self.readiness_contract_sha256 is None
        ):
            raise ValueError("artifact_proxy_identity_incomplete")
        if self.role == "relay" and (
            self.responses_contract_sha256 is not None
            or self.proxy_policy_sha256 is not None
            or self.readiness_contract_sha256 is not None
        ):
            raise ValueError("artifact_relay_identity_invalid")
        return self


class RuntimeImageContentEvidence(_StrictFrozenModel):
    role: Literal["proxy", "relay"]
    content_valid: Literal[True]
    cleanup_complete: Literal[True]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readiness_contract_sha256: str | None = None
    responses_contract_sha256: str | None = None

    @model_validator(mode="after")
    def require_role_content(self) -> RuntimeImageContentEvidence:
        if (
            (self.role == "proxy")
            != (self.responses_contract_sha256 is not None)
            or (self.role == "proxy")
            != (self.readiness_contract_sha256 is not None)
        ):
            raise ValueError("artifact_image_content_role_invalid")
        return self


class RuntimeArtifactIdentity(_StrictFrozenModel):
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proxy_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    relay_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    timeout_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readiness_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    orchestrator_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RuntimeArtifactCandidate(_StrictFrozenModel):
    runtime_candidate_schema_version: Literal[2]
    status: Literal["PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL"]
    symbol: Literal["000403.SZ"]
    trade_date: Literal["2026-07-31"]
    provider: Literal["openai"]
    exact_model: Literal["gpt-5.6-terra"]
    endpoint_alias: Literal["openai_responses_v1"]
    artifact_identity: RuntimeArtifactIdentity
    inputs: RuntimeArtifactInputHashes
    proxy_image: RuntimeImageEvidence
    relay_image: RuntimeImageEvidence
    proxy_contents: RuntimeImageContentEvidence
    relay_contents: RuntimeImageContentEvidence
    runtime_contract: dict[str, int]
    base_image_reference: Literal[
        "gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
    ]
    build_network: Literal["none"]
    pull_allowed: Literal[False]
    no_cache: Literal[True]
    base_digest_pinned: Literal[True]
    old_approval_candidate_sha256: Literal[
        "45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127"
    ]
    new_approval_installed: Literal[False]
    provider_attempt_count: Literal[0]
    ai_call_count: Literal[0]
    provider_http: Literal["NOT_RUN"]
    retry_count: Literal[0]
    maximum_provider_attempts: Literal[1]
    can_publish: Literal[False]
    manual_review: Literal["NOT_STARTED"]

    @model_validator(mode="after")
    def require_cross_bindings(self) -> RuntimeArtifactCandidate:
        if (
            self.proxy_image.role != "proxy"
            or self.relay_image.role != "relay"
            or self.proxy_contents.role != "proxy"
            or self.relay_contents.role != "relay"
            or self.artifact_identity.proxy_image_id != self.proxy_image.image_id
            or self.artifact_identity.relay_image_id != self.relay_image.image_id
            or self.artifact_identity.timeout_contract_sha256
            != self.inputs.runtime_contract_sha256
            or self.artifact_identity.readiness_contract_sha256
            != self.inputs.readiness_contract_sha256
            or self.artifact_identity.orchestrator_source_sha256
            != self.inputs.orchestrator_source_sha256
            or self.proxy_image.runtime_contract_sha256
            != self.inputs.runtime_contract_sha256
            or self.proxy_image.readiness_contract_sha256
            != self.inputs.readiness_contract_sha256
            or self.relay_image.runtime_contract_sha256
            != self.inputs.runtime_contract_sha256
            or self.proxy_contents.source_sha256
            != self.inputs.proxy_source_sha256
            or self.relay_contents.source_sha256
            != self.inputs.relay_source_sha256
            or self.proxy_contents.runtime_contract_sha256
            != self.inputs.runtime_contract_sha256
            or self.proxy_contents.readiness_contract_sha256
            != self.inputs.readiness_contract_sha256
            or self.relay_contents.runtime_contract_sha256
            != self.inputs.runtime_contract_sha256
            or self.proxy_contents.responses_contract_sha256
            != self.inputs.responses_contract_sha256
        ):
            raise ValueError("artifact_candidate_binding_invalid")
        return self


Executor = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _root(repo_root: Path) -> Path:
    root = Path(os.path.abspath(os.fspath(repo_root)))
    try:
        metadata = os.lstat(root)
    except OSError as exc:
        raise RuntimeArtifactError("artifact_repo_root_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeArtifactError("artifact_repo_root_invalid")
    return root.resolve(strict=True)


def _read_regular(path: Path, category: str, *, limit: int = 4_194_304) -> bytes:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RuntimeArtifactError(category) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > limit
    ):
        raise RuntimeArtifactError(category)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeArtifactError(category) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != metadata.st_size:
            raise RuntimeArtifactError(category)
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != opened.st_size or len(raw) > limit:
            raise RuntimeArtifactError(category)
        return raw
    except OSError as exc:
        raise RuntimeArtifactError(category) from exc
    finally:
        os.close(descriptor)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact_context(root: Path, relative: Path, expected: set[str]) -> None:
    context = root / relative
    try:
        metadata = os.lstat(context)
        entries = {str(path.relative_to(context)) for path in context.rglob("*")}
    except (OSError, ValueError) as exc:
        raise RuntimeArtifactError("artifact_build_context_invalid") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or entries != expected
    ):
        raise RuntimeArtifactError("artifact_build_context_invalid")
    for name in expected:
        _read_regular(context / name, "artifact_build_context_invalid")


def compute_runtime_artifact_input_hashes(
    repo_root: Path,
) -> RuntimeArtifactInputHashes:
    root = _root(repo_root)
    _exact_context(root, _PROXY_CONTEXT, _PROXY_FILES)
    _exact_context(root, _RELAY_CONTEXT, _RELAY_FILES)
    runtime_raw = _read_regular(
        root / "backend/app/services/phase2_canary_runtime_contract.json",
        "artifact_runtime_contract_invalid",
    )
    runtime_hash = runtime_contract_sha256()
    if _sha256(runtime_raw) != runtime_hash:
        raise RuntimeArtifactError("artifact_runtime_contract_invalid")
    for relative in (
        _PROXY_CONTEXT / "runtime-contract.json",
        _RELAY_CONTEXT / "runtime-contract.json",
    ):
        if _read_regular(root / relative, "artifact_runtime_contract_invalid") != (
            runtime_raw
        ):
            raise RuntimeArtifactError("artifact_runtime_contract_invalid")
    responses_raw = _read_regular(
        root / _PROXY_CONTEXT / "responses-contract.json",
        "artifact_responses_contract_invalid",
    )
    if responses_raw != canonical_proxy_contract_bytes():
        raise RuntimeArtifactError("artifact_responses_contract_invalid")
    readiness_raw = _read_regular(
        root / _PROXY_CONTEXT / "readiness-contract.json",
        "artifact_readiness_contract_invalid",
    )
    readiness_value = _strict_json(readiness_raw, "artifact_readiness_contract_invalid")
    if (
        not isinstance(readiness_value, dict)
        or readiness_raw != canonical_json_bytes(readiness_value)
        or readiness_value.get("proxy_readiness_contract_version") != 1
        or readiness_value.get("marker_path") != "/output/proxy-ready.json"
        or readiness_value.get("request_path") != "/input/request.json"
        or readiness_value.get("provider_attempt_count_before_ready") != 0
        or readiness_value.get("readiness_timeout_source")
        != "provider_connect_timeout_seconds"
    ):
        raise RuntimeArtifactError("artifact_readiness_contract_invalid")
    old_candidate = _read_regular(
        root / _OLD_CANDIDATE,
        "artifact_old_candidate_invalid",
    )
    if _sha256(old_candidate) != OLD_CANDIDATE_SHA256:
        raise RuntimeArtifactError("artifact_old_candidate_invalid")
    return RuntimeArtifactInputHashes(
        proxy_source_sha256=_sha256(
            _read_regular(root / _PROXY_CONTEXT / "proxy.py", "artifact_proxy_invalid")
        ),
        proxy_dockerfile_sha256=_sha256(
            _read_regular(
                root / _PROXY_CONTEXT / "Dockerfile",
                "artifact_proxy_dockerfile_invalid",
            )
        ),
        responses_contract_sha256=_sha256(responses_raw),
        proxy_policy_sha256=proxy_policy_sha256(),
        relay_source_sha256=_sha256(
            _read_regular(root / _RELAY_CONTEXT / "relay.py", "artifact_relay_invalid")
        ),
        relay_dockerfile_sha256=_sha256(
            _read_regular(
                root / _RELAY_CONTEXT / "Dockerfile",
                "artifact_relay_dockerfile_invalid",
            )
        ),
        runtime_contract_sha256=runtime_hash,
        readiness_contract_sha256=_sha256(readiness_raw),
        orchestrator_source_sha256=_sha256(
            _read_regular(root / _ORCHESTRATOR, "artifact_orchestrator_invalid")
        ),
        observability_source_sha256=_sha256(
            _read_regular(
                root / _OBSERVABILITY,
                "artifact_observability_invalid",
            )
        ),
        launcher_source_sha256=_sha256(
            _read_regular(root / _LAUNCHER, "artifact_launcher_invalid")
        ),
        artifact_verifier_source_sha256=_sha256(
            _read_regular(
                root / _ARTIFACT_VERIFIER,
                "artifact_verifier_invalid",
            )
        ),
        artifact_builder_source_sha256=_sha256(
            _read_regular(root / _ARTIFACT_BUILDER, "artifact_builder_invalid")
        ),
        runbook_sha256=_sha256(
            _read_regular(root / _RUNBOOK, "artifact_runbook_invalid")
        ),
    )


def _validate_iidfile(path: Path) -> Path:
    target = Path(os.path.abspath(os.fspath(path)))
    try:
        parent = os.lstat(target.parent)
    except OSError as exc:
        raise RuntimeArtifactError("artifact_iidfile_invalid") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or os.path.lexists(target)
    ):
        raise RuntimeArtifactError("artifact_iidfile_invalid")
    return target


def build_runtime_image_commands(
    repo_root: Path,
    hashes: RuntimeArtifactInputHashes,
    *,
    proxy_iidfile: Path,
    relay_iidfile: Path,
) -> tuple[list[str], list[str]]:
    root = _root(repo_root)
    if hashes != compute_runtime_artifact_input_hashes(root):
        raise RuntimeArtifactError("artifact_input_hash_mismatch")
    proxy_iid = _validate_iidfile(proxy_iidfile)
    relay_iid = _validate_iidfile(relay_iidfile)
    common = ["docker", "build", "--pull=false", "--network=none", "--no-cache"]
    proxy = [
        *common,
        "--iidfile",
        str(proxy_iid),
        "--tag",
        PROXY_IMAGE_NAME,
        "--build-arg",
        f"BASE_IMAGE_DIGEST={BASE_IMAGE_DIGEST}",
        "--build-arg",
        f"PROXY_SOURCE_SHA256={hashes.proxy_source_sha256}",
        "--build-arg",
        f"RESPONSES_CONTRACT_SHA256={hashes.responses_contract_sha256}",
        "--build-arg",
        f"PROXY_POLICY_SHA256={hashes.proxy_policy_sha256}",
        "--build-arg",
        f"RUNTIME_CONTRACT_SHA256={hashes.runtime_contract_sha256}",
        "--build-arg",
        f"READINESS_CONTRACT_SHA256={hashes.readiness_contract_sha256}",
        str(root / _PROXY_CONTEXT),
    ]
    relay = [
        *common,
        "--iidfile",
        str(relay_iid),
        "--tag",
        RELAY_IMAGE_NAME,
        "--build-arg",
        f"BASE_IMAGE_DIGEST={BASE_IMAGE_DIGEST}",
        "--build-arg",
        f"RELAY_SOURCE_SHA256={hashes.relay_source_sha256}",
        "--build-arg",
        f"RUNTIME_CONTRACT_SHA256={hashes.runtime_contract_sha256}",
        str(root / _RELAY_CONTEXT),
    ]
    return proxy, relay


def _inspect_item(payload: Any, category: str) -> Mapping[str, Any]:
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], Mapping)
    ):
        raise RuntimeArtifactError(category)
    return payload[0]


def validate_local_base_image(payload: Any) -> LocalBaseImageEvidence:
    item = _inspect_item(payload, "artifact_base_image_unavailable")
    rootfs = item.get("RootFS")
    layers = rootfs.get("Layers") if isinstance(rootfs, Mapping) else None
    if (
        item.get("Id") != BASE_IMAGE_DIGEST
        or not isinstance(item.get("RepoDigests"), list)
        or BASE_IMAGE_REFERENCE not in item["RepoDigests"]
        or not isinstance(layers, list)
        or not layers
        or any(not isinstance(layer, str) or not _IMAGE_ID.fullmatch(layer) for layer in layers)
    ):
        raise RuntimeArtifactError("artifact_base_image_unavailable")
    return LocalBaseImageEvidence(
        base_available=True,
        base_reference=BASE_IMAGE_REFERENCE,
        image_id=BASE_IMAGE_DIGEST,
        rootfs_layers=tuple(layers),
    )


def inspect_runtime_image(
    payload: Any,
    history: Any,
    *,
    role: Literal["proxy", "relay"],
    hashes: RuntimeArtifactInputHashes,
    base: LocalBaseImageEvidence,
    expected_image_id: str,
) -> RuntimeImageEvidence:
    if not _IMAGE_ID.fullmatch(expected_image_id):
        raise RuntimeArtifactError("artifact_image_id_invalid")
    item = _inspect_item(payload, "artifact_image_inspect_invalid")
    config = item.get("Config")
    rootfs = item.get("RootFS")
    layers = rootfs.get("Layers") if isinstance(rootfs, Mapping) else None
    if not isinstance(config, Mapping) or not isinstance(layers, list):
        raise RuntimeArtifactError("artifact_image_inspect_invalid")
    if role == "proxy":
        image_name = PROXY_IMAGE_NAME
        entrypoint = _PROXY_ENTRYPOINT
        environment = _PROXY_ENV
        source_hash = hashes.proxy_source_sha256
        dockerfile_hash = hashes.proxy_dockerfile_sha256
        labels = {
            "org.tickflow.phase2.base-image-digest": BASE_IMAGE_DIGEST,
            "org.tickflow.phase2.proxy-source-sha256": source_hash,
            "org.tickflow.phase2.responses-contract-sha256": (
                hashes.responses_contract_sha256
            ),
            "org.tickflow.phase2.proxy-policy-sha256": hashes.proxy_policy_sha256,
            "org.tickflow.phase2.runtime-contract-sha256": (
                hashes.runtime_contract_sha256
            ),
            "org.tickflow.phase2.readiness-contract-sha256": (
                hashes.readiness_contract_sha256
            ),
        }
        responses_hash: str | None = hashes.responses_contract_sha256
        policy_hash: str | None = hashes.proxy_policy_sha256
        readiness_hash: str | None = hashes.readiness_contract_sha256
    else:
        image_name = RELAY_IMAGE_NAME
        entrypoint = _RELAY_ENTRYPOINT
        environment = _RELAY_ENV
        source_hash = hashes.relay_source_sha256
        dockerfile_hash = hashes.relay_dockerfile_sha256
        labels = {
            "org.tickflow.phase2.base-image-digest": BASE_IMAGE_DIGEST,
            "org.tickflow.phase2.relay-source-sha256": source_hash,
            "org.tickflow.phase2.runtime-contract-sha256": (
                hashes.runtime_contract_sha256
            ),
        }
        responses_hash = None
        policy_hash = None
        readiness_hash = None
    if (
        item.get("Id") != expected_image_id
        or item.get("RepoTags") != [image_name]
        or config.get("User") != RUNTIME_USER
        or config.get("Entrypoint") != list(entrypoint)
        or config.get("Cmd") is not None
        or set(config.get("Env") or []) != environment
        or len(config.get("Env") or []) != len(environment)
        or config.get("Labels") != labels
        or len(layers)
        != len(base.rootfs_layers) + (7 if role == "proxy" else 6)
        or tuple(layers[: len(base.rootfs_layers)]) != base.rootfs_layers
        or any(not isinstance(layer, str) or not _IMAGE_ID.fullmatch(layer) for layer in layers)
    ):
        raise RuntimeArtifactError("artifact_image_identity_invalid")
    if not isinstance(history, list) or not history:
        raise RuntimeArtifactError("artifact_image_history_invalid")
    for item_history in history:
        if not isinstance(item_history, Mapping):
            raise RuntimeArtifactError("artifact_image_history_invalid")
        serialized = json.dumps(item_history, ensure_ascii=False, sort_keys=True)
        if _SENSITIVE_HISTORY.search(serialized):
            raise RuntimeArtifactError("artifact_image_history_sensitive")
    return RuntimeImageEvidence(
        role=role,
        image_valid=True,
        history_clean=True,
        image_name=image_name,
        image_id=expected_image_id,
        base_image_digest=BASE_IMAGE_DIGEST,
        source_sha256=source_hash,
        dockerfile_sha256=dockerfile_hash,
        runtime_contract_sha256=hashes.runtime_contract_sha256,
        readiness_contract_sha256=readiness_hash,
        responses_contract_sha256=responses_hash,
        proxy_policy_sha256=policy_hash,
        runtime_user=RUNTIME_USER,
        entrypoint=entrypoint,
        base_rootfs_prefix_verified=True,
        rootfs_layer_count=len(layers),
    )


def _strict_json(raw: bytes, category: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite")

    try:
        return json.loads(raw, parse_constant=reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeArtifactError(category) from exc


def _facts_and_projection(root: Path) -> tuple[str, str]:
    facts_raw = _read_regular(root / _FACTS, "artifact_facts_invalid")
    facts_hash = _sha256(facts_raw)
    facts = _strict_json(facts_raw, "artifact_facts_invalid")
    if not isinstance(facts, dict):
        raise RuntimeArtifactError("artifact_facts_invalid")
    projection = build_worker_projection(facts, facts_hash, facts_bytes=facts_raw)
    projection_hash = projection.get("projection_sha256")
    if not isinstance(projection_hash, str) or len(projection_hash) != 64:
        raise RuntimeArtifactError("artifact_projection_invalid")
    return facts_hash, projection_hash


def build_runtime_artifact_candidate(
    repo_root: Path,
    *,
    proxy: RuntimeImageEvidence,
    relay: RuntimeImageEvidence,
    proxy_contents: RuntimeImageContentEvidence,
    relay_contents: RuntimeImageContentEvidence,
) -> RuntimeArtifactCandidate:
    root = _root(repo_root)
    hashes = compute_runtime_artifact_input_hashes(root)
    facts_hash, projection_hash = _facts_and_projection(root)
    if (
        proxy.role != "proxy"
        or relay.role != "relay"
        or proxy.source_sha256 != hashes.proxy_source_sha256
        or proxy.dockerfile_sha256 != hashes.proxy_dockerfile_sha256
        or proxy.responses_contract_sha256 != hashes.responses_contract_sha256
        or proxy.proxy_policy_sha256 != hashes.proxy_policy_sha256
        or relay.source_sha256 != hashes.relay_source_sha256
        or relay.dockerfile_sha256 != hashes.relay_dockerfile_sha256
        or proxy.runtime_contract_sha256 != hashes.runtime_contract_sha256
        or proxy.readiness_contract_sha256 != hashes.readiness_contract_sha256
        or relay.runtime_contract_sha256 != hashes.runtime_contract_sha256
        or proxy_contents.role != "proxy"
        or relay_contents.role != "relay"
        or proxy_contents.source_sha256 != hashes.proxy_source_sha256
        or relay_contents.source_sha256 != hashes.relay_source_sha256
        or proxy_contents.runtime_contract_sha256
        != hashes.runtime_contract_sha256
        or proxy_contents.readiness_contract_sha256
        != hashes.readiness_contract_sha256
        or relay_contents.runtime_contract_sha256
        != hashes.runtime_contract_sha256
        or proxy_contents.responses_contract_sha256
        != hashes.responses_contract_sha256
    ):
        raise RuntimeArtifactError("artifact_image_input_mismatch")
    runtime = load_runtime_contract().model_dump(mode="json")
    return RuntimeArtifactCandidate(
        runtime_candidate_schema_version=2,
        status=READY_STATUS,
        symbol="000403.SZ",
        trade_date="2026-07-31",
        provider="openai",
        exact_model="gpt-5.6-terra",
        endpoint_alias="openai_responses_v1",
        artifact_identity=RuntimeArtifactIdentity(
            facts_sha256=facts_hash,
            projection_sha256=projection_hash,
            proxy_image_id=proxy.image_id,
            relay_image_id=relay.image_id,
            timeout_contract_sha256=hashes.runtime_contract_sha256,
            readiness_contract_sha256=hashes.readiness_contract_sha256,
            orchestrator_source_sha256=hashes.orchestrator_source_sha256,
        ),
        inputs=hashes,
        proxy_image=proxy,
        relay_image=relay,
        proxy_contents=proxy_contents,
        relay_contents=relay_contents,
        runtime_contract=runtime,
        base_image_reference=BASE_IMAGE_REFERENCE,
        build_network="none",
        pull_allowed=False,
        no_cache=True,
        base_digest_pinned=True,
        old_approval_candidate_sha256=OLD_CANDIDATE_SHA256,
        new_approval_installed=False,
        provider_attempt_count=0,
        ai_call_count=0,
        provider_http="NOT_RUN",
        retry_count=0,
        maximum_provider_attempts=1,
        can_publish=False,
        manual_review="NOT_STARTED",
    )


def _default_executor(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=60,
        env={"PATH": os.environ.get("PATH", "")},
    )


def verify_runtime_image_contents(
    repo_root: Path,
    *,
    role: Literal["proxy", "relay"],
    image_id: str,
    executor: Executor = _default_executor,
) -> RuntimeImageContentEvidence:
    """Copy fixed files from a stopped container and compare exact bytes."""
    root = _root(repo_root)
    if not _IMAGE_ID.fullmatch(image_id):
        raise RuntimeArtifactError("artifact_image_id_invalid")
    hashes = compute_runtime_artifact_input_hashes(root)
    expected = {
        "proxy": {
            "/proxy/proxy.py": root / _PROXY_CONTEXT / "proxy.py",
            "/proxy/responses-contract.json": (
                root / _PROXY_CONTEXT / "responses-contract.json"
            ),
            "/proxy/runtime-contract.json": (
                root / _PROXY_CONTEXT / "runtime-contract.json"
            ),
            "/proxy/readiness-contract.json": (
                root / _PROXY_CONTEXT / "readiness-contract.json"
            ),
        },
        "relay": {
            "/relay/relay.py": root / _RELAY_CONTEXT / "relay.py",
            "/relay/runtime-contract.json": (
                root / _RELAY_CONTEXT / "runtime-contract.json"
            ),
        },
    }[role]
    container_name = f"phase2-canary-{role}-verify-{uuid.uuid4().hex[:12]}"
    created = False
    failure: RuntimeArtifactError | None = None
    cleanup_complete = False
    try:
        create = executor(
            [
                "docker",
                "create",
                "--name",
                container_name,
                "--network",
                "none",
                image_id,
            ]
        )
        if create.returncode != 0:
            raise RuntimeArtifactError("artifact_image_content_invalid")
        created = True
        with tempfile.TemporaryDirectory(prefix="phase2-canary-content-") as temporary:
            staging = Path(temporary)
            staging.chmod(0o700)
            for index, (container_path, source) in enumerate(expected.items()):
                destination = staging / f"content-{index}"
                copied = executor(
                    [
                        "docker",
                        "cp",
                        f"{container_name}:{container_path}",
                        str(destination),
                    ]
                )
                if copied.returncode != 0 or _read_regular(
                    destination,
                    "artifact_image_content_invalid",
                ) != _read_regular(source, "artifact_image_content_invalid"):
                    raise RuntimeArtifactError("artifact_image_content_invalid")
    except (OSError, subprocess.SubprocessError, RuntimeArtifactError) as exc:
        failure = (
            exc
            if isinstance(exc, RuntimeArtifactError)
            else RuntimeArtifactError("artifact_image_content_invalid")
        )
    finally:
        if created:
            try:
                removed = executor(["docker", "rm", "-f", container_name])
                cleanup_complete = removed.returncode == 0
            except (OSError, subprocess.SubprocessError):
                cleanup_complete = False
    if not cleanup_complete:
        raise RuntimeArtifactError("artifact_content_cleanup_failed")
    if failure is not None:
        raise failure
    return RuntimeImageContentEvidence(
        role=role,
        content_valid=True,
        cleanup_complete=True,
        source_sha256=(
            hashes.proxy_source_sha256
            if role == "proxy"
            else hashes.relay_source_sha256
        ),
        runtime_contract_sha256=hashes.runtime_contract_sha256,
        readiness_contract_sha256=(
            hashes.readiness_contract_sha256 if role == "proxy" else None
        ),
        responses_contract_sha256=(
            hashes.responses_contract_sha256 if role == "proxy" else None
        ),
    )


def _run_json(executor: Executor, command: Sequence[str], category: str) -> Any:
    try:
        result = executor(command)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeArtifactError(category) from exc
    if result.returncode != 0:
        raise RuntimeArtifactError(category)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeArtifactError(category) from exc


def _run_history(executor: Executor, image_id: str) -> list[dict[str, Any]]:
    try:
        result = executor(
            [
                "docker",
                "history",
                "--no-trunc",
                "--format",
                "{{json .}}",
                image_id,
            ]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeArtifactError("artifact_image_history_invalid") from exc
    if result.returncode != 0:
        raise RuntimeArtifactError("artifact_image_history_invalid")
    try:
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise RuntimeArtifactError("artifact_image_history_invalid") from exc


def verify_runtime_artifact_candidate(
    repo_root: Path,
    candidate_path: Path,
    *,
    expected_candidate_sha256: str,
    inspect_images: bool = True,
    executor: Executor = _default_executor,
) -> RuntimeArtifactCandidate:
    root = _root(repo_root)
    raw = _read_regular(candidate_path, "artifact_candidate_invalid", limit=262_144)
    if _sha256(raw) != expected_candidate_sha256:
        raise RuntimeArtifactError("artifact_candidate_hash_mismatch")
    value = _strict_json(raw, "artifact_candidate_invalid")
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise RuntimeArtifactError("artifact_candidate_invalid")
    try:
        candidate = RuntimeArtifactCandidate.model_validate_json(raw)
    except ValidationError as exc:
        raise RuntimeArtifactError("artifact_candidate_invalid") from exc
    current = compute_runtime_artifact_input_hashes(root)
    if candidate.inputs != current:
        raise RuntimeArtifactError("artifact_candidate_input_mismatch")
    facts_hash, projection_hash = _facts_and_projection(root)
    if (
        candidate.artifact_identity.facts_sha256 != facts_hash
        or candidate.artifact_identity.projection_sha256 != projection_hash
    ):
        raise RuntimeArtifactError("artifact_candidate_input_mismatch")
    if inspect_images:
        base = validate_local_base_image(
            _run_json(
                executor,
                ["docker", "image", "inspect", BASE_IMAGE_REFERENCE],
                "artifact_base_image_unavailable",
            )
        )
        for role, image in (
            ("proxy", candidate.proxy_image),
            ("relay", candidate.relay_image),
        ):
            inspected = inspect_runtime_image(
                _run_json(
                    executor,
                    ["docker", "image", "inspect", image.image_id],
                    "artifact_image_inspect_invalid",
                ),
                _run_history(executor, image.image_id),
                role=role,
                hashes=current,
                base=base,
                expected_image_id=image.image_id,
            )
            if inspected != image:
                raise RuntimeArtifactError("artifact_candidate_image_mismatch")
    return candidate
