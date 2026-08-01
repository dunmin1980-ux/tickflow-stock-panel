"""Offline build and identity evidence for the dedicated OpenAI Canary proxy."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.phase2_openai_proxy_contract import (
    canonical_proxy_contract_bytes,
    proxy_policy_sha256,
)

BASE_IMAGE_DIGEST = (
    "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
)
BASE_IMAGE_REFERENCE = (
    "gcr.io/distroless/python3-debian12@" + BASE_IMAGE_DIGEST
)
IMAGE_NAME = "tickflow-phase2-openai-egress-proxy:canary-v1"
RUNTIME_USER = "65532:65532"
ENTRYPOINT = ("/usr/bin/python3", "/proxy/proxy.py")
PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW = (
    "PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW"
)
PHASE2B_CANARY_BUILD_INPUT_BLOCKED = "PHASE2B_CANARY_BUILD_INPUT_BLOCKED"

_CONTEXT_RELATIVE = Path("docker/phase2-openai-egress-proxy")
_PROXY_RELATIVE = _CONTEXT_RELATIVE / "proxy.py"
_DOCKERFILE_RELATIVE = _CONTEXT_RELATIVE / "Dockerfile"
_CONTRACT_RELATIVE = _CONTEXT_RELATIVE / "responses-contract.json"
_LAUNCHER_RELATIVE = Path(
    "backend/app/services/phase2_openai_canary_runner.py"
)
_CONTEXT_FILES = {
    "Dockerfile",
    "proxy.py",
    "responses-contract.json",
    "secret.placeholder",
    "receipt.placeholder.json",
}
_EXPECTED_ENVIRONMENT = {
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
    "LANG=C.UTF-8",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
}
_LABELS = {
    "org.tickflow.phase2.base-image-digest",
    "org.tickflow.phase2.proxy-source-sha256",
    "org.tickflow.phase2.responses-contract-sha256",
    "org.tickflow.phase2.proxy-policy-sha256",
}
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_HISTORY = re.compile(
    r"Authorization:|Bearer |api[_-]?key\s*[:=]|"
    r"(?:password|token|credential)\s*[:=]|"
    r"BEGIN [A-Z ]*PRIVATE KEY|--mount=type=(?:secret|ssh)",
    re.IGNORECASE,
)


class OpenAIProxyArtifactError(ValueError):
    """Raised with a stable code when offline artifact evidence fails closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactInputHashes(_StrictFrozenModel):
    proxy_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dockerfile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    responses_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proxy_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    launcher_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocalBaseImageEvidence(_StrictFrozenModel):
    base_available: Literal[True]
    base_reference: Literal[
        "gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
    ]
    local_config_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    rootfs_layers: tuple[str, ...]


class ImageInspectionEvidence(_StrictFrozenModel):
    image_valid: Literal[True]
    history_clean: Literal[True]
    image_name: Literal["tickflow-phase2-openai-egress-proxy:canary-v1"]
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_image_digest: Literal[
        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
    ]
    proxy_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    responses_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proxy_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_user: Literal["65532:65532"]
    entrypoint: tuple[str, ...]
    base_rootfs_prefix_verified: Literal[True]
    rootfs_layer_count: int = Field(ge=1)

    @model_validator(mode="after")
    def require_exact_entrypoint(self) -> ImageInspectionEvidence:
        if self.entrypoint != ENTRYPOINT:
            raise ValueError("artifact_entrypoint_invalid")
        return self


class FreshBuildProvenance(_StrictFrozenModel):
    fresh_offline_build: Literal[True]
    pull_allowed: Literal[False]
    build_network: Literal["none"]
    no_cache: Literal[True]
    iidfile_verified: Literal[True]
    input_hashes_stable: Literal[True]
    base_rootfs_prefix_verified: Literal[True]
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_image_id: Literal[
        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
    ]


class ImageContentEvidence(_StrictFrozenModel):
    content_valid: Literal[True]
    cleanup_complete: Literal[True]
    proxy_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    responses_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OpenAIProxyArtifactCandidate(_StrictFrozenModel):
    approval_schema_version: Literal[1]
    image_name: Literal["tickflow-phase2-openai-egress-proxy:canary-v1"]
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_image_digest: Literal[
        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
    ]
    proxy_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dockerfile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    responses_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proxy_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    launcher_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entrypoint: tuple[str, ...]
    runtime_user: Literal["65532:65532"]

    @model_validator(mode="after")
    def require_exact_entrypoint(self) -> OpenAIProxyArtifactCandidate:
        if self.entrypoint != ENTRYPOINT:
            raise ValueError("artifact_entrypoint_invalid")
        return self


class Executor(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        shell: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[Any]: ...


def _absolute_root(repo_root: Path) -> Path:
    root = Path(os.path.abspath(os.fspath(repo_root)))
    try:
        metadata = os.lstat(root)
    except OSError as exc:
        raise OpenAIProxyArtifactError("artifact_repo_root_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise OpenAIProxyArtifactError("artifact_repo_root_invalid")
    return root.resolve(strict=True)


def _read_regular(path: Path, category: str) -> bytes:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise OpenAIProxyArtifactError(category) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise OpenAIProxyArtifactError(category)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OpenAIProxyArtifactError(category) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > 4_194_304:
            raise OpenAIProxyArtifactError(category)
        chunks: list[bytes] = []
        remaining = 4_194_305
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != opened.st_size or len(raw) > 4_194_304:
            raise OpenAIProxyArtifactError(category)
        return raw
    except OSError as exc:
        raise OpenAIProxyArtifactError(category) from exc
    finally:
        os.close(descriptor)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _context_is_exact(root: Path) -> None:
    context = root / _CONTEXT_RELATIVE
    try:
        metadata = os.lstat(context)
        entries = {
            str(path.relative_to(context))
            for path in context.rglob("*")
        }
    except (OSError, ValueError) as exc:
        raise OpenAIProxyArtifactError("artifact_build_context_invalid") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or entries != _CONTEXT_FILES
    ):
        raise OpenAIProxyArtifactError("artifact_build_context_invalid")
    for name in _CONTEXT_FILES:
        _read_regular(context / name, "artifact_build_context_invalid")


def compute_artifact_input_hashes(repo_root: Path) -> ArtifactInputHashes:
    root = _absolute_root(repo_root)
    _context_is_exact(root)
    contract_bytes = _read_regular(
        root / _CONTRACT_RELATIVE,
        "artifact_contract_invalid",
    )
    if contract_bytes != canonical_proxy_contract_bytes():
        raise OpenAIProxyArtifactError("artifact_contract_invalid")
    return ArtifactInputHashes(
        proxy_source_sha256=_sha256(
            _read_regular(root / _PROXY_RELATIVE, "artifact_proxy_source_invalid")
        ),
        dockerfile_sha256=_sha256(
            _read_regular(root / _DOCKERFILE_RELATIVE, "artifact_dockerfile_invalid")
        ),
        responses_contract_sha256=_sha256(contract_bytes),
        proxy_policy_sha256=proxy_policy_sha256(),
        launcher_source_sha256=_sha256(
            _read_regular(root / _LAUNCHER_RELATIVE, "artifact_launcher_invalid")
        ),
    )


def build_offline_image_command(
    repo_root: Path,
    hashes: ArtifactInputHashes,
    *,
    iidfile: Path,
) -> list[str]:
    root = _absolute_root(repo_root)
    if not isinstance(hashes, ArtifactInputHashes):
        raise OpenAIProxyArtifactError("artifact_hashes_invalid")
    current = compute_artifact_input_hashes(root)
    if hashes != current:
        raise OpenAIProxyArtifactError("artifact_input_hash_mismatch")
    iidfile_path = Path(os.path.abspath(os.fspath(iidfile)))
    try:
        parent = os.lstat(iidfile_path.parent)
        exists = os.path.lexists(iidfile_path)
    except OSError as exc:
        raise OpenAIProxyArtifactError("artifact_iidfile_path_invalid") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or exists
    ):
        raise OpenAIProxyArtifactError("artifact_iidfile_path_invalid")
    return [
        "docker",
        "build",
        "--pull=false",
        "--network=none",
        "--no-cache",
        "--iidfile",
        str(iidfile_path),
        "--tag",
        IMAGE_NAME,
        "--build-arg",
        f"BASE_IMAGE_DIGEST={BASE_IMAGE_DIGEST}",
        "--build-arg",
        "SOURCE_DATE_EPOCH=0",
        "--build-arg",
        f"PROXY_SOURCE_SHA256={hashes.proxy_source_sha256}",
        "--build-arg",
        f"RESPONSES_CONTRACT_SHA256={hashes.responses_contract_sha256}",
        "--build-arg",
        f"PROXY_POLICY_SHA256={hashes.proxy_policy_sha256}",
        str(root / _CONTEXT_RELATIVE),
    ]


def _inspect_item(payload: Any, category: str) -> Mapping[str, Any]:
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], Mapping)
    ):
        raise OpenAIProxyArtifactError(category)
    return payload[0]


def validate_local_base_image(payload: Any) -> LocalBaseImageEvidence:
    item = _inspect_item(payload, "artifact_base_image_unavailable")
    image_id = item.get("Id")
    repo_digests = item.get("RepoDigests")
    rootfs = item.get("RootFS")
    layers = rootfs.get("Layers") if isinstance(rootfs, Mapping) else None
    if (
        image_id != BASE_IMAGE_DIGEST
        or not isinstance(repo_digests, list)
        or BASE_IMAGE_REFERENCE not in repo_digests
        or not isinstance(layers, list)
        or not layers
        or any(
            not isinstance(layer, str) or _IMAGE_ID.fullmatch(layer) is None
            for layer in layers
        )
    ):
        raise OpenAIProxyArtifactError("artifact_base_image_unavailable")
    return LocalBaseImageEvidence(
        base_available=True,
        base_reference=BASE_IMAGE_REFERENCE,
        local_config_id=image_id,
        rootfs_layers=tuple(layers),
    )


def inspect_openai_proxy_image(
    payload: Any,
    history: Any,
    hashes: ArtifactInputHashes,
    base: LocalBaseImageEvidence,
    *,
    expected_image_id: str,
) -> ImageInspectionEvidence:
    if not isinstance(hashes, ArtifactInputHashes) or not isinstance(
        base,
        LocalBaseImageEvidence,
    ):
        raise OpenAIProxyArtifactError("artifact_hashes_invalid")
    item = _inspect_item(payload, "artifact_image_inspect_invalid")
    config = item.get("Config")
    if not isinstance(config, Mapping):
        raise OpenAIProxyArtifactError("artifact_image_inspect_invalid")
    image_id = item.get("Id")
    repo_tags = item.get("RepoTags")
    rootfs = item.get("RootFS")
    layers = rootfs.get("Layers") if isinstance(rootfs, Mapping) else None
    environment = config.get("Env")
    labels = config.get("Labels")
    expected_labels = {
        "org.tickflow.phase2.base-image-digest": BASE_IMAGE_DIGEST,
        "org.tickflow.phase2.proxy-source-sha256": hashes.proxy_source_sha256,
        "org.tickflow.phase2.responses-contract-sha256": (
            hashes.responses_contract_sha256
        ),
        "org.tickflow.phase2.proxy-policy-sha256": hashes.proxy_policy_sha256,
    }
    if (
        not isinstance(expected_image_id, str)
        or _IMAGE_ID.fullmatch(expected_image_id) is None
        or image_id != expected_image_id
        or repo_tags != [IMAGE_NAME]
        or config.get("User") != RUNTIME_USER
        or config.get("Entrypoint") != list(ENTRYPOINT)
        or config.get("Cmd") is not None
        or not isinstance(environment, list)
        or len(environment) != len(_EXPECTED_ENVIRONMENT)
        or set(environment) != _EXPECTED_ENVIRONMENT
        or not isinstance(labels, Mapping)
        or set(labels) != _LABELS
        or dict(labels) != expected_labels
        or not isinstance(layers, list)
        or len(layers) != len(base.rootfs_layers) + 5
        or tuple(layers[: len(base.rootfs_layers)]) != base.rootfs_layers
        or any(
            not isinstance(layer, str) or _IMAGE_ID.fullmatch(layer) is None
            for layer in layers
        )
    ):
        raise OpenAIProxyArtifactError("artifact_image_identity_invalid")
    if not isinstance(history, list) or not history:
        raise OpenAIProxyArtifactError("artifact_image_history_invalid")
    for entry in history:
        if not isinstance(entry, Mapping):
            raise OpenAIProxyArtifactError("artifact_image_history_invalid")
        serialized = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        if _SENSITIVE_HISTORY.search(serialized):
            raise OpenAIProxyArtifactError("artifact_image_history_sensitive")
    return ImageInspectionEvidence(
        image_valid=True,
        history_clean=True,
        image_name=IMAGE_NAME,
        image_id=image_id,
        base_image_digest=BASE_IMAGE_DIGEST,
        proxy_source_sha256=hashes.proxy_source_sha256,
        responses_contract_sha256=hashes.responses_contract_sha256,
        proxy_policy_sha256=hashes.proxy_policy_sha256,
        runtime_user=RUNTIME_USER,
        entrypoint=ENTRYPOINT,
        base_rootfs_prefix_verified=True,
        rootfs_layer_count=len(layers),
    )


def validate_fresh_build_provenance(
    *,
    hashes_before: ArtifactInputHashes,
    hashes_after: ArtifactInputHashes,
    iidfile_image_id: str,
    image: ImageInspectionEvidence,
    base: LocalBaseImageEvidence,
) -> FreshBuildProvenance:
    """Bind a fresh no-cache offline build's IID to stable reviewed inputs."""
    if (
        not isinstance(hashes_before, ArtifactInputHashes)
        or not isinstance(hashes_after, ArtifactInputHashes)
        or not isinstance(image, ImageInspectionEvidence)
        or not isinstance(base, LocalBaseImageEvidence)
        or hashes_before != hashes_after
        or iidfile_image_id != image.image_id
        or iidfile_image_id == base.local_config_id
        or not image.base_rootfs_prefix_verified
    ):
        raise OpenAIProxyArtifactError("artifact_fresh_build_provenance_invalid")
    return FreshBuildProvenance(
        fresh_offline_build=True,
        pull_allowed=False,
        build_network="none",
        no_cache=True,
        iidfile_verified=True,
        input_hashes_stable=True,
        base_rootfs_prefix_verified=True,
        image_id=image.image_id,
        base_image_id=base.local_config_id,
    )


def _execute(
    executor: Executor,
    command: Sequence[str],
    *,
    check: bool,
) -> subprocess.CompletedProcess[Any]:
    try:
        result = executor(
            command,
            check=check,
            capture_output=True,
            text=True,
            shell=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpenAIProxyArtifactError("artifact_executor_failed") from exc
    if check and result.returncode != 0:
        raise OpenAIProxyArtifactError("artifact_executor_failed")
    return result


def verify_image_contents(
    repo_root: Path,
    image_id: str,
    *,
    executor: Executor,
) -> ImageContentEvidence:
    root = _absolute_root(repo_root)
    if not isinstance(image_id, str) or _IMAGE_ID.fullmatch(image_id) is None:
        raise OpenAIProxyArtifactError("artifact_image_id_invalid")
    current = compute_artifact_input_hashes(root)
    container_name = f"phase2-openai-proxy-verify-{uuid.uuid4().hex[:12]}"
    cleanup_complete = False
    failure: OpenAIProxyArtifactError | None = None
    source_hash = ""
    contract_hash = ""
    try:
        with tempfile.TemporaryDirectory(
            prefix="tickflow-phase2-openai-proxy-verify-"
        ) as temporary:
            staging = Path(temporary)
            staging.chmod(0o700)
            source_copy = staging / "proxy.py"
            contract_copy = staging / "responses-contract.json"
            _execute(
                executor,
                [
                    "docker",
                    "create",
                    "--name",
                    container_name,
                    "--network",
                    "none",
                    image_id,
                ],
                check=True,
            )
            _execute(
                executor,
                [
                    "docker",
                    "cp",
                    f"{container_name}:/proxy/proxy.py",
                    str(source_copy),
                ],
                check=True,
            )
            _execute(
                executor,
                [
                    "docker",
                    "cp",
                    f"{container_name}:/proxy/responses-contract.json",
                    str(contract_copy),
                ],
                check=True,
            )
            source_bytes = _read_regular(
                source_copy,
                "artifact_image_content_invalid",
            )
            contract_bytes = _read_regular(
                contract_copy,
                "artifact_image_content_invalid",
            )
            source_hash = _sha256(source_bytes)
            contract_hash = _sha256(contract_bytes)
            if (
                source_bytes
                != _read_regular(root / _PROXY_RELATIVE, "artifact_proxy_source_invalid")
                or contract_bytes
                != _read_regular(root / _CONTRACT_RELATIVE, "artifact_contract_invalid")
                or source_hash != current.proxy_source_sha256
                or contract_hash != current.responses_contract_sha256
            ):
                raise OpenAIProxyArtifactError("artifact_image_content_invalid")
    except OpenAIProxyArtifactError as exc:
        failure = exc
    finally:
        try:
            cleanup = _execute(
                executor,
                ["docker", "rm", "-f", container_name],
                check=False,
            )
            cleanup_complete = cleanup.returncode == 0
        except OpenAIProxyArtifactError:
            cleanup_complete = False
    if not cleanup_complete:
        raise OpenAIProxyArtifactError("artifact_cleanup_failed")
    if failure is not None:
        raise failure
    return ImageContentEvidence(
        content_valid=True,
        cleanup_complete=True,
        proxy_source_sha256=source_hash,
        responses_contract_sha256=contract_hash,
    )


def build_artifact_candidate(
    repo_root: Path,
    image: ImageInspectionEvidence,
    contents: ImageContentEvidence,
    provenance: FreshBuildProvenance,
) -> OpenAIProxyArtifactCandidate:
    if not isinstance(image, ImageInspectionEvidence) or not isinstance(
        contents,
        ImageContentEvidence,
    ) or not isinstance(provenance, FreshBuildProvenance):
        raise OpenAIProxyArtifactError("artifact_evidence_invalid")
    hashes = compute_artifact_input_hashes(repo_root)
    if (
        image.proxy_source_sha256 != hashes.proxy_source_sha256
        or image.responses_contract_sha256 != hashes.responses_contract_sha256
        or image.proxy_policy_sha256 != hashes.proxy_policy_sha256
        or contents.proxy_source_sha256 != hashes.proxy_source_sha256
        or contents.responses_contract_sha256 != hashes.responses_contract_sha256
        or not image.image_valid
        or not image.history_clean
        or not contents.content_valid
        or not contents.cleanup_complete
        or not image.base_rootfs_prefix_verified
        or not provenance.fresh_offline_build
        or not provenance.no_cache
        or not provenance.iidfile_verified
        or not provenance.input_hashes_stable
        or not provenance.base_rootfs_prefix_verified
        or provenance.image_id != image.image_id
        or provenance.base_image_id != BASE_IMAGE_DIGEST
    ):
        raise OpenAIProxyArtifactError("artifact_evidence_mismatch")
    return OpenAIProxyArtifactCandidate(
        approval_schema_version=1,
        image_name=IMAGE_NAME,
        image_id=image.image_id,
        base_image_digest=BASE_IMAGE_DIGEST,
        proxy_source_sha256=hashes.proxy_source_sha256,
        dockerfile_sha256=hashes.dockerfile_sha256,
        responses_contract_sha256=hashes.responses_contract_sha256,
        proxy_policy_sha256=hashes.proxy_policy_sha256,
        launcher_source_sha256=hashes.launcher_source_sha256,
        entrypoint=ENTRYPOINT,
        runtime_user=RUNTIME_USER,
    )


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise OpenAIProxyArtifactError("artifact_report_path_invalid")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OpenAIProxyArtifactError("artifact_report_write_failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise OpenAIProxyArtifactError("artifact_report_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def write_artifact_reports(
    repo_root: Path,
    candidate: OpenAIProxyArtifactCandidate,
    image: ImageInspectionEvidence,
    contents: ImageContentEvidence,
    provenance: FreshBuildProvenance,
) -> None:
    if (
        not isinstance(candidate, OpenAIProxyArtifactCandidate)
        or not isinstance(image, ImageInspectionEvidence)
        or not isinstance(contents, ImageContentEvidence)
        or not isinstance(provenance, FreshBuildProvenance)
    ):
        raise OpenAIProxyArtifactError("artifact_evidence_invalid")
    root = Path(os.path.abspath(os.fspath(repo_root)))
    report_directory = root / "reports/phase2_openai_proxy_artifact"
    evidence = {
        "evidence_schema_version": 1,
        "status": PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW,
        "image_name": candidate.image_name,
        "image_id": candidate.image_id,
        "offline_build": True,
        "pull_allowed": False,
        "build_network": "none",
        "image_identity_valid": image.image_valid,
        "image_history_clean": image.history_clean,
        "image_contents_valid": contents.content_valid,
        "cleanup_complete": contents.cleanup_complete,
        "fresh_offline_build": provenance.fresh_offline_build,
        "no_cache": provenance.no_cache,
        "iidfile_verified": provenance.iidfile_verified,
        "input_hashes_stable": provenance.input_hashes_stable,
        "base_rootfs_prefix_verified": (
            provenance.base_rootfs_prefix_verified
        ),
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "tickflow_request_count": 0,
        "public_network_request_count": 0,
    }
    _atomic_write(
        report_directory / "approval_candidate.json",
        _canonical_bytes(candidate.model_dump(mode="json")),
    )
    _atomic_write(
        report_directory / "build_evidence.json",
        _canonical_bytes(evidence),
    )
