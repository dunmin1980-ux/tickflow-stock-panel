"""Versioned immutable timeout contract for the single-symbol AI Canary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_CONTRACT_FILENAME = "phase2_canary_runtime_contract.json"
_COPY_PATHS = (
    Path("docker/phase2-openai-egress-proxy/runtime-contract.json"),
    Path("docker/phase2-canary-relay/runtime-contract.json"),
)
_MAXIMUM_CONTRACT_BYTES = 16_384
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class RuntimeContractError(ValueError):
    """Raised with a stable category when the runtime contract fails closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CanaryRuntimeContract(BaseModel):
    """Exact non-configurable timeout and single-attempt policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    canary_runtime_contract_version: Literal[1]
    provider_connect_timeout_seconds: int = Field(gt=0)
    provider_read_timeout_seconds: int = Field(gt=0)
    provider_total_timeout_seconds: int = Field(gt=0)
    relay_candidate_wait_timeout_seconds: int = Field(gt=0)
    host_orchestrator_timeout_seconds: int = Field(gt=0)
    cleanup_timeout_seconds: int = Field(gt=0)
    retry_count: Literal[0]
    maximum_provider_attempts: Literal[1]

    @model_validator(mode="after")
    def require_safe_ordering(self) -> CanaryRuntimeContract:
        if self.provider_connect_timeout_seconds > self.provider_total_timeout_seconds:
            raise ValueError("provider_connect_timeout_exceeds_total")
        if self.provider_read_timeout_seconds > self.provider_total_timeout_seconds:
            raise ValueError("provider_read_timeout_exceeds_total")
        if not (
            self.provider_total_timeout_seconds
            < self.relay_candidate_wait_timeout_seconds
            < self.host_orchestrator_timeout_seconds
        ):
            raise ValueError("runtime_timeout_order_invalid")
        return self


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


def _read_regular(path: Path) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RuntimeContractError("runtime_contract_missing") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeContractError("runtime_contract_not_regular")
    if not 0 < before.st_size <= _MAXIMUM_CONTRACT_BYTES:
        raise RuntimeContractError("runtime_contract_size_invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise RuntimeContractError("runtime_contract_open_failed") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise RuntimeContractError("runtime_contract_replaced")
        raw = os.read(descriptor, _MAXIMUM_CONTRACT_BYTES + 1)
        after = os.lstat(path)
        if (
            len(raw) != opened.st_size
            or len(raw) > _MAXIMUM_CONTRACT_BYTES
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise RuntimeContractError("runtime_contract_replaced")
        return raw
    except OSError as exc:
        raise RuntimeContractError("runtime_contract_read_failed") from exc
    finally:
        os.close(descriptor)


def _decode_contract(raw: bytes) -> CanaryRuntimeContract:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite")

    try:
        value = json.loads(raw, parse_constant=reject_constant)
        contract = CanaryRuntimeContract.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        raise RuntimeContractError("runtime_contract_invalid") from exc
    if raw != _canonical_bytes(contract.model_dump(mode="json")):
        raise RuntimeContractError("runtime_contract_not_canonical")
    return contract


def load_runtime_contract(path: Path | None = None) -> CanaryRuntimeContract:
    """Load the committed contract without consulting environment variables."""
    candidate = path or Path(__file__).with_name(_CONTRACT_FILENAME)
    return _decode_contract(_read_regular(candidate))


def canonical_runtime_contract_bytes() -> bytes:
    """Return the exact canonical bytes committed for every component."""
    contract = load_runtime_contract()
    return _canonical_bytes(contract.model_dump(mode="json"))


def runtime_contract_sha256() -> str:
    """Return the stable SHA-256 of the canonical runtime contract."""
    return hashlib.sha256(canonical_runtime_contract_bytes()).hexdigest()


def _write_atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RuntimeContractError("runtime_contract_parent_invalid")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise RuntimeContractError("runtime_contract_write_failed")
            offset += written
        os.fsync(descriptor)
    except OSError as exc:
        raise RuntimeContractError("runtime_contract_write_failed") from exc
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise RuntimeContractError("runtime_contract_publish_failed") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _repo_contract_path(repo_root: Path) -> Path:
    return repo_root / "backend/app/services" / _CONTRACT_FILENAME


def write_runtime_contract_copies(repo_root: Path) -> tuple[Path, Path]:
    """Publish byte-identical generated copies into both image contexts."""
    root = repo_root.resolve(strict=True)
    canonical = _read_regular(_repo_contract_path(root))
    _decode_contract(canonical)
    paths = tuple(root / relative for relative in _COPY_PATHS)
    for path in paths:
        _write_atomic(path, canonical)
    return paths  # type: ignore[return-value]


def check_runtime_contract_copies(repo_root: Path) -> bool:
    """Fail closed unless Host, Relay, and Proxy bytes match exactly."""
    root = repo_root.resolve(strict=True)
    expected = _read_regular(_repo_contract_path(root))
    _decode_contract(expected)
    for relative in _COPY_PATHS:
        if _read_regular(root / relative) != expected:
            raise RuntimeContractError("runtime_contract_copy_mismatch")
    return True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.write:
            write_runtime_contract_copies(_repo_root())
        else:
            check_runtime_contract_copies(_repo_root())
    except RuntimeContractError:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
