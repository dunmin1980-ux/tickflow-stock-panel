"""Immutable Ark-only timeout contract for the single-symbol Canary."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.services.phase2_canary_runtime_contract import (
    CanaryRuntimeContract,
    RuntimeContractError,
    load_runtime_contract,
)

_CONTRACT_FILENAME = "phase2_ark_timeout_contract.json"
_COPY_PATHS = (
    Path("docker/phase2-ark-egress-proxy/runtime-contract.json"),
    Path("docker/phase2-ark-canary-relay/runtime-contract.json"),
)


def _canonical_bytes(value: object) -> bytes:
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


def load_ark_runtime_contract(path: Path | None = None) -> CanaryRuntimeContract:
    """Load the committed Ark contract without environment overrides."""
    candidate = path or Path(__file__).with_name(_CONTRACT_FILENAME)
    return load_runtime_contract(candidate)


def canonical_ark_runtime_contract_bytes() -> bytes:
    contract = load_ark_runtime_contract()
    return _canonical_bytes(contract.model_dump(mode="json"))


def ark_runtime_contract_sha256() -> str:
    return hashlib.sha256(canonical_ark_runtime_contract_bytes()).hexdigest()


def _write_atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RuntimeContractError("ark_runtime_contract_parent_invalid")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise RuntimeContractError("ark_runtime_contract_publish_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def write_ark_runtime_contract_copies(repo_root: Path) -> tuple[Path, Path]:
    root = repo_root.resolve(strict=True)
    raw = canonical_ark_runtime_contract_bytes()
    paths = tuple(root / relative for relative in _COPY_PATHS)
    for path in paths:
        _write_atomic(path, raw)
    return paths  # type: ignore[return-value]


def check_ark_runtime_contract_copies(repo_root: Path) -> bool:
    root = repo_root.resolve(strict=True)
    expected = canonical_ark_runtime_contract_bytes()
    for relative in _COPY_PATHS:
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
            raise RuntimeContractError("ark_runtime_contract_copy_mismatch")
    return True
