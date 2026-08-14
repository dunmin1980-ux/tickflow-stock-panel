#!/usr/bin/env python3
"""Prepare a new Ark Canary approval scope without any public network activity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.providers.ark_contract import (
    exclusive_ark_artifact_lock,
    load_ark_artifact_generation,
    load_ark_tls_probe_evidence_binding,
    validate_ark_approval_candidate,
)
from app.services.phase2_claims_service import canonical_json_bytes
from scripts import build_phase2_ark_contract as ark_contract_builder

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / "reports/phase2_provider_ark/new_canary_scope"
SUPERSEDED_ROOT = REPO_ROOT / "reports/phase2_provider_ark/superseded"
TLS_SCOPE_ID = "abd999450d007f23cadccd86d22136608c3fbb04b1d92c0078153088781f919c"
TLS_PROBE_ID = "d73e91f766854ff7a64c0e725939eb64"
TLS_ATTEMPT_ROOT = (
    REPO_ROOT
    / "reports/phase2_provider_ark/tls_probe_v2/attempts"
    / TLS_SCOPE_ID
    / TLS_PROBE_ID
)
TLS_BINDING_PATH = (
    REPO_ROOT
    / "reports/phase2_provider_ark/tls_probe_v2/success_evidence_binding.json"
)
HISTORY_BINDING_PATH = (
    REPO_ROOT / "reports/phase2_provider_ark/new_canary_history_binding.json"
)
KEYCHAIN_SERVICE = "tickflow-phase2-canary-volcengine-ark"
FORK_REMOTE_REF = "fork/codex/tickflow-phase2-ai-review"
REQUIRED_TLS_EVENTS = (
    "probe_started",
    "dns_started",
    "dns_completed",
    "tcp_connect_started",
    "tcp_connect_completed",
    "tls_handshake_started",
    "tls_handshake_completed",
    "certificate_verified",
    "hostname_verified",
    "probe_closed",
    "cleanup_completed",
)
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")

_REQUEST_FILES = {
    "2f17745f58534063bdd7eda1eb0d16f1": (
        "reports/phase2_provider_canary/attempts/"
        "898079e765f188ba48b2de143ff5fce81e1191ec7bfc6cb773352b2bed90982d/"
        "2f17745f58534063bdd7eda1eb0d16f1/ledger.json",
        "reports/phase2_provider_ark/live_canary/evidence/"
        "2f17745f58534063bdd7eda1eb0d16f1.json",
        "reports/phase2_provider_ark/live_canary/rejected/"
        "2f17745f58534063bdd7eda1eb0d16f1.json",
        "reports/phase2_provider_ark/live_canary/receipts/"
        "2f17745f58534063bdd7eda1eb0d16f1/archive-status.json",
        "reports/phase2_provider_ark/live_canary/receipts/"
        "2f17745f58534063bdd7eda1eb0d16f1/child-metadata.json",
        "reports/phase2_provider_ark/live_canary/receipts/"
        "2f17745f58534063bdd7eda1eb0d16f1/proxy-receipt.json",
        "reports/phase2_provider_ark/live_canary/receipts/"
        "2f17745f58534063bdd7eda1eb0d16f1/relay-receipt.json",
    ),
    "93cf0ea84804444ebc9745fa34c4bb82": (
        "reports/phase2_provider_canary/attempts/"
        "8e42a251cfa4e5b4cbdc95b3aceca9d59152ce574e65f5ba2300a0009df8a019/"
        "93cf0ea84804444ebc9745fa34c4bb82/ledger.json",
        "reports/phase2_provider_ark/live_canary/evidence/"
        "93cf0ea84804444ebc9745fa34c4bb82.json",
        "reports/phase2_provider_ark/live_canary/rejected/"
        "93cf0ea84804444ebc9745fa34c4bb82.json",
        "reports/phase2_provider_ark/live_canary/receipts/"
        "93cf0ea84804444ebc9745fa34c4bb82/archive-status.json",
        "reports/phase2_provider_ark/live_canary/receipts/"
        "93cf0ea84804444ebc9745fa34c4bb82/child-metadata.json",
        "reports/phase2_provider_ark/live_canary/receipts/"
        "93cf0ea84804444ebc9745fa34c4bb82/proxy-receipt.json",
        "reports/phase2_provider_ark/live_canary/receipts/"
        "93cf0ea84804444ebc9745fa34c4bb82/relay-receipt.json",
    ),
}

_SUCCESSFUL_TLS_FILES = (
    "ledger.json",
    "evidence/child-receipt.json",
    "evidence/transport-evidence.json",
    "evidence/transport-evidence-precleanup.json",
    "evidence/receipt.json",
    "evidence/receipt.ready",
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_regular(path: Path, *, maximum_bytes: int = 2 * 1024 * 1024) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError("offline_evidence_missing") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("offline_evidence_invalid")
    if metadata.st_size > maximum_bytes:
        raise ValueError("offline_evidence_invalid")
    return path.read_bytes()


def _atomic_write(path: Path, raw: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def validate_tls_probe_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("tls_probe_receipt_invalid") from exc
    events = value.get("events") if isinstance(value, dict) else None
    observed_monotonic: list[int] = []
    if isinstance(events, dict):
        for event_name in REQUIRED_TLS_EVENTS:
            event = events.get(event_name)
            if (
                not isinstance(event, dict)
                or event.get("occurred") is not True
                or event.get("category") != "PASSED"
                or type(event.get("monotonic_ns")) is not int
            ):
                raise ValueError("tls_probe_receipt_invalid")
            observed_monotonic.append(event["monotonic_ns"])
    if (
        not isinstance(value, dict)
        or value.get("probe_id") != TLS_PROBE_ID
        or value.get("terminal_status") != "PROBE_PASSED"
        or value.get("tls_error_category") != "NONE"
        or value.get("tls_version") != "TLSv1.3"
        or value.get("cipher_name") != "TLS_AES_256_GCM_SHA384"
        or value.get("san_contains_hostname") is not True
        or value.get("probe_attempt_count") != 1
        or value.get("retry_count") != 0
        or value.get("provider_attempt_count") != 0
        or value.get("ai_call_count") != 0
        or value.get("secret_content_read") is not False
        or value.get("authorization_constructed") is not False
        or value.get("http_request_sent") is not False
        or value.get("business_body_sent") is not False
        or not isinstance(events, dict)
        or set(events) != set(REQUIRED_TLS_EVENTS)
        or observed_monotonic != sorted(observed_monotonic)
        or len(set(observed_monotonic)) != len(observed_monotonic)
        or value.get("target")
        != {
            "host": "ark.cn-beijing.volces.com",
            "port": 443,
            "sni": "ark.cn-beijing.volces.com",
            "hostname_verification_target": "ark.cn-beijing.volces.com",
        }
    ):
        raise ValueError("tls_probe_receipt_invalid")
    return value


def preserve_content_addressed(
    source: Path,
    destination_root: Path,
    *,
    suffix: str | None = None,
) -> Path:
    raw = _read_regular(source)
    digest = _sha256(raw)
    name = f"{digest}{'-' + suffix if suffix else ''}{source.suffix}"
    destination = destination_root / name
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or _read_regular(destination) != raw:
            raise ValueError("superseded_artifact_collision")
        return destination
    _atomic_write(destination, raw)
    return destination


def keychain_secret_present(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    command = ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE]
    result = run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0


def _hash_paths(relative_paths: tuple[str, ...]) -> dict[str, str]:
    return {
        relative: _sha256(_read_regular(REPO_ROOT / relative))
        for relative in relative_paths
    }


def _historical_tls_probe_files() -> tuple[str, ...]:
    baseline_path = (
        REPO_ROOT
        / "reports/phase2_provider_ark/tls_receipt_transport/"
        "historical_probe_baseline.json"
    )
    value = json.loads(_read_regular(baseline_path))
    files = value.get("files") if isinstance(value, dict) else None
    if not isinstance(files, list) or len(files) != 7:
        raise ValueError("historical_tls_probe_baseline_invalid")
    paths: list[str] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("historical_tls_probe_baseline_invalid")
        relative = item["path"]
        if _sha256(_read_regular(REPO_ROOT / relative)) != item["sha256"]:
            raise ValueError("historical_evidence_mutated")
        paths.append(relative)
    return tuple(paths)


def build_tls_probe_evidence_binding() -> dict[str, Any]:
    receipt_path = TLS_ATTEMPT_ROOT / "evidence/receipt.json"
    receipt = validate_tls_probe_receipt(receipt_path)
    ledger = json.loads(_read_regular(TLS_ATTEMPT_ROOT / "ledger.json"))
    receipt_sha256 = _sha256(_read_regular(receipt_path))
    if (
        ledger.get("probe_id") != TLS_PROBE_ID
        or ledger.get("approval_scope_id") != TLS_SCOPE_ID
        or ledger.get("state") != "COMPLETED"
        or ledger.get("final_status") != "PHASE2B_ARK_TLS_CONNECTIVITY_PROBE_PASSED"
        or ledger.get("receipt_sha256") != receipt_sha256
        or ledger.get("probe_attempt_count") != 1
        or ledger.get("retry_count") != 0
        or ledger.get("provider_attempt_count") != 0
        or ledger.get("ai_call_count") != 0
    ):
        raise ValueError("tls_probe_ledger_invalid")
    evidence_files = {
        relative: _sha256(_read_regular(TLS_ATTEMPT_ROOT / relative))
        for relative in _SUCCESSFUL_TLS_FILES
    }
    return {
        "tls_probe_evidence_binding_schema_version": 1,
        "probe_id": TLS_PROBE_ID,
        "approval_scope_id": TLS_SCOPE_ID,
        "terminal_status": receipt["terminal_status"],
        "reuse_status": "CONSUMED_PRESERVED_NON_REUSABLE",
        "receipt_sha256": receipt_sha256,
        "tls_version": receipt["tls_version"],
        "cipher_name": receipt["cipher_name"],
        "probe_attempt_count": 1,
        "retry_count": 0,
        "provider_attempt_count": 0,
        "ai_call_count": 0,
        "secret_content_read": False,
        "authorization_constructed": False,
        "http_request_sent": False,
        "historical_evidence": "UNCHANGED",
        "evidence_files": evidence_files,
    }


def build_history_evidence_binding() -> dict[str, Any]:
    successful_tls_paths = tuple(
        (TLS_ATTEMPT_ROOT / relative).relative_to(REPO_ROOT).as_posix()
        for relative in _SUCCESSFUL_TLS_FILES
    )
    objects = {
        request_id: {
            "terminal_status": (
                "FAILED_AFTER_DISPATCH"
                if request_id == "2f17745f58534063bdd7eda1eb0d16f1"
                else "RELAY_PROTOCOL_REJECTED_AFTER_DISPATCH"
            ),
            "preservation_status": "PRESERVED",
            "reuse_status": "NON_REUSABLE",
            "files": _hash_paths(paths),
        }
        for request_id, paths in _REQUEST_FILES.items()
    }
    objects["5dd641ca9e4b4ccba1035fff4d695409"] = {
        "terminal_status": "PROCESS_ERROR_CONSUMED",
        "preservation_status": "PRESERVED",
        "reuse_status": "NON_REUSABLE",
        "files": _hash_paths(_historical_tls_probe_files()),
    }
    objects[TLS_PROBE_ID] = {
        "terminal_status": "PROBE_PASSED_CONSUMED",
        "preservation_status": "PRESERVED",
        "reuse_status": "NON_REUSABLE",
        "files": _hash_paths(successful_tls_paths),
    }
    return {
        "historical_evidence_binding_schema_version": 1,
        "historical_evidence": "UNCHANGED",
        "historical_objects": objects,
    }


def prepare_source_bindings() -> dict[str, str]:
    preserved = {
        "candidate": preserve_content_addressed(
            REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json",
            SUPERSEDED_ROOT,
        ),
        "scope": preserve_content_addressed(
            REPO_ROOT / "reports/phase2_provider_ark/approval_scope_preflight.json",
            SUPERSEDED_ROOT,
            suffix="scope",
        ),
        "artifact_generation": preserve_content_addressed(
            REPO_ROOT / "reports/phase2_provider_ark/artifact_generation.json",
            SUPERSEDED_ROOT,
            suffix="artifact-generation",
        ),
    }
    _atomic_write(
        TLS_BINDING_PATH,
        canonical_json_bytes(build_tls_probe_evidence_binding()),
    )
    _atomic_write(
        HISTORY_BINDING_PATH,
        canonical_json_bytes(build_history_evidence_binding()),
    )
    load_ark_tls_probe_evidence_binding(REPO_ROOT)
    return {name: path.relative_to(REPO_ROOT).as_posix() for name, path in preserved.items()}


def _git_value(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    value = result.stdout.strip()
    if result.returncode != 0:
        raise ValueError("git_identity_invalid")
    return value


def _residue_counts() -> dict[str, int]:
    containers = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    networks = subprocess.run(
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if containers.returncode != 0 or networks.returncode != 0:
        raise ValueError("runtime_residue_check_failed")
    prefixes = ("tickflow-phase2-ark-", "phase2-ark-")
    container_count = sum(
        name.startswith(prefixes) for name in containers.stdout.splitlines()
    )
    network_count = sum(name.startswith(prefixes) for name in networks.stdout.splitlines())
    work_root = Path.home() / "Library/Application Support/TickFlowPhase2CanaryArk/work-v1"
    temporary_count = (
        sum(1 for _ in work_root.iterdir())
        if work_root.exists() and work_root.is_dir() and not work_root.is_symlink()
        else 0
    )
    return {
        "container_count": container_count,
        "network_count": network_count,
        "temporary_count": temporary_count,
    }


def generate_scope(source_git_head: str) -> dict[str, Any]:
    if _HEX_40.fullmatch(source_git_head) is None:
        raise ValueError("source_git_head_invalid")
    if _git_value("rev-parse", "HEAD") != source_git_head:
        raise ValueError("source_git_head_mismatch")
    if _git_value("rev-parse", FORK_REMOTE_REF) != source_git_head:
        raise ValueError("fork_remote_head_mismatch")
    if _git_value("status", "--short"):
        raise ValueError("worktree_not_clean")
    if canonical_json_bytes(build_history_evidence_binding()) != _read_regular(
        HISTORY_BINDING_PATH
    ):
        raise ValueError("historical_evidence_mutated")
    if canonical_json_bytes(build_tls_probe_evidence_binding()) != _read_regular(
        TLS_BINDING_PATH
    ):
        raise ValueError("historical_evidence_mutated")

    args = argparse.Namespace(
        write=True,
        capture_build_provenance=False,
        build_path="READ_ONLY_EXISTING_IMAGE_INSPECT",
        source_git_head=source_git_head,
    )
    with exclusive_ark_artifact_lock(REPO_ROOT):
        if ark_contract_builder._main_locked(args) != 0:
            raise ValueError("ark_candidate_publication_failed")

    candidate_path = REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
    scope_path = REPO_ROOT / "reports/phase2_provider_ark/approval_scope_preflight.json"
    candidate = json.loads(_read_regular(candidate_path))
    scope = json.loads(_read_regular(scope_path))
    validate_ark_approval_candidate(REPO_ROOT, candidate)
    load_ark_artifact_generation(REPO_ROOT)
    residue = _residue_counts()
    keychain_present = keychain_secret_present()
    valid = (
        candidate.get("current_git_head") == source_git_head
        and scope.get("current_git_head") == source_git_head
        and scope.get("historical_attempts") == 0
        and scope.get("attempt_availability") == "AVAILABLE"
        and scope.get("attempt_directory") == "ABSENT"
        and scope.get("tls_probe_result") == "PASSED"
        and keychain_present
        and all(value == 0 for value in residue.values())
    )
    if not valid:
        raise ValueError("offline_preflight_blocked")
    preflight = {
        "offline_preflight_schema_version": 1,
        "status": "CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL",
        "source_git_head": source_git_head,
        "approval_candidate_sha256": _sha256(_read_regular(candidate_path)),
        "approval_scope_id": scope["approval_scope_id"],
        "tls_connectivity": "VERIFIED",
        "tls_probe_id": TLS_PROBE_ID,
        "tls_probe_scope_id": TLS_SCOPE_ID,
        "tls_probe_evidence": "PASSED",
        "scope_historical_attempts": 0,
        "scope_availability": "AVAILABLE",
        "keychain_secret": "PRESENT",
        "secret_content_read": False,
        "real_ark_provider_attempts": 0,
        "real_ai_calls": 0,
        "provider_http": "NOT_RUN",
        "historical_evidence": "UNCHANGED",
        "residue": residue,
        "independent_review": "PENDING",
    }
    _atomic_write(OUTPUT_ROOT / "offline_preflight.json", canonical_json_bytes(preflight))
    return preflight


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-source-bindings", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--source-git-head")
    args = parser.parse_args()
    if args.prepare_source_bindings:
        print(json.dumps(prepare_source_bindings(), sort_keys=True))
        return 0
    if args.write and args.source_git_head:
        print(json.dumps(generate_scope(args.source_git_head), sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
