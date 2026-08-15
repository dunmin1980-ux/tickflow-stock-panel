#!/usr/bin/env python3
"""One-shot host launcher for the separately approved Proxy-path TLS Probe."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "docker/phase2-ark-proxy-tls-probe/probe.py"
OUTPUT_ROOT = REPO_ROOT / "reports/phase2_provider_ark/proxy_tls_probe"
CANDIDATE_PATH = OUTPUT_ROOT / "approval_candidate.json"
SCOPE_PATH = OUTPUT_ROOT / "approval_scope.json"
ATTEMPTS_ROOT = OUTPUT_ROOT / "attempts"
LOCAL_ROOT = (
    Path.home() / "Library/Application Support/TickFlowPhase2ArkProxyTlsProbe"
)
LOCAL_APPROVAL_PATH = LOCAL_ROOT / "runtime-approval.json"
LOCK_PATH = LOCAL_ROOT / "runtime.lock"
SCOPE_TYPE = "production_proxy_path_tls_only_probe"
TARGET_HOST = "ark.cn-beijing.volces.com"
TARGET_PORT = 443


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def scope_id_for_candidate(candidate_sha256: str) -> str:
    if len(candidate_sha256) != 64:
        raise ValueError("approval_candidate_sha256_invalid")
    return sha256_bytes(
        canonical_json_bytes(
            {
                "approval_candidate_sha256": candidate_sha256,
                "scope_type": SCOPE_TYPE,
                "target_host": TARGET_HOST,
                "target_port": TARGET_PORT,
            }
        )
    )


def expected_scope(candidate_sha256: str) -> dict[str, Any]:
    return {
        "ai_canary_scope_shared": False,
        "approval_candidate_sha256": candidate_sha256,
        "approval_scope_id": scope_id_for_candidate(candidate_sha256),
        "approval_scope_schema_version": 1,
        "attempt_availability": "AVAILABLE",
        "generic_tls_probe_scope_shared": False,
        "historical_attempts": 0,
        "maximum_probe_attempts": 1,
        "openai_scope_shared": False,
        "provider_scope_shared": False,
        "retry_count": 0,
        "scope_type": SCOPE_TYPE,
    }


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".staging", dir=path.parent
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        raw = canonical_json_bytes(value)
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
    finally:
        temporary.unlink(missing_ok=True)


def publish_dispatch_gate(path: Path, *, probe_id: str) -> None:
    if len(probe_id) != 32 or any(
        character not in "0123456789abcdef" for character in probe_id
    ):
        raise ValueError("probe_id_invalid")
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".staging", dir=path.parent
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((probe_id + "\n").encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _read_regular_bytes(path: Path, *, mode: int | None = None) -> bytes:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("runtime_approval_invalid")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise ValueError("runtime_approval_invalid")
    return path.read_bytes()


def read_canonical_json(path: Path, *, mode: int | None = None) -> dict[str, Any]:
    try:
        raw = _read_regular_bytes(path, mode=mode)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("runtime_approval_invalid") from error
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise ValueError("runtime_approval_invalid")
    return value


def _git_head(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
        timeout=15.0,
    )
    head = result.stdout.strip()
    if result.returncode != 0 or len(head) != 40 or any(
        character not in "0123456789abcdef" for character in head
    ):
        raise ValueError("runtime_source_binding_invalid")
    return head


def _bound_source_path(repo_root: Path, relative_value: Any) -> Path:
    if not isinstance(relative_value, str):
        raise ValueError("runtime_source_binding_invalid")
    relative = Path(relative_value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("runtime_source_binding_invalid")
    current = repo_root
    try:
        for part in relative.parts:
            current = current / part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("runtime_source_binding_invalid")
    except OSError as error:
        raise ValueError("runtime_source_binding_invalid") from error
    if not stat.S_ISREG(current.lstat().st_mode):
        raise ValueError("runtime_source_binding_invalid")
    try:
        current.resolve(strict=True).relative_to(repo_root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise ValueError("runtime_source_binding_invalid") from error
    return current


def validate_runtime_source_bindings(
    candidate: Mapping[str, Any],
    *,
    repo_root: Path,
    runner_path: Path,
    launcher_path: Path,
    git_head_loader: Callable[[Path], str] = _git_head,
) -> None:
    bindings = candidate.get("source_bindings")
    if (
        not isinstance(bindings, dict)
        or set(("host_launcher_source", "container_runner_source"))
        - set(bindings)
        or git_head_loader(repo_root) != candidate.get("current_git_head")
    ):
        raise ValueError("runtime_source_binding_invalid")
    resolved: dict[str, Path] = {}
    for name, binding in bindings.items():
        if not isinstance(name, str) or not isinstance(binding, dict):
            raise ValueError("runtime_source_binding_invalid")
        path = _bound_source_path(repo_root, binding.get("path"))
        expected_sha = binding.get("sha256")
        if (
            not isinstance(expected_sha, str)
            or len(expected_sha) != 64
            or sha256_bytes(path.read_bytes()) != expected_sha
        ):
            raise ValueError("runtime_source_binding_invalid")
        resolved[name] = path.resolve(strict=True)
    if (
        resolved["container_runner_source"] != runner_path.resolve(strict=True)
        or resolved["host_launcher_source"] != launcher_path.resolve(strict=True)
    ):
        raise ValueError("runtime_source_binding_invalid")


def load_execution_identity(
    *,
    candidate_path: Path,
    scope_path: Path,
    approval_path: Path,
    repo_root: Path = REPO_ROOT,
    runner_path: Path = RUNNER_PATH,
    launcher_path: Path = Path(__file__),
    verify_runtime_bindings: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    candidate_raw = _read_regular_bytes(candidate_path)
    try:
        candidate = json.loads(candidate_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("runtime_approval_invalid") from error
    if not isinstance(candidate, dict) or candidate_raw != canonical_json_bytes(candidate):
        raise ValueError("runtime_approval_invalid")
    if verify_runtime_bindings is None:
        validate_runtime_source_bindings(
            candidate,
            repo_root=repo_root,
            runner_path=runner_path,
            launcher_path=launcher_path,
        )
    else:
        verify_runtime_bindings(candidate)
    candidate_sha = sha256_bytes(candidate_raw)
    scope = read_canonical_json(scope_path)
    approval = read_canonical_json(approval_path, mode=0o600)
    scope_id = scope_id_for_candidate(candidate_sha)
    if (
        candidate.get("candidate_status")
        != "PROXY_TLS_PROBE_CANDIDATE_READY_FOR_REVIEW"
        or candidate.get("scope_type") != SCOPE_TYPE
        or candidate.get("target_host") != TARGET_HOST
        or candidate.get("target_port") != TARGET_PORT
        or candidate.get("retry_count") != 0
        or candidate.get("maximum_probe_attempts") != 1
        or scope != expected_scope(candidate_sha)
        or approval
        != {
            "approval_candidate_sha256": candidate_sha,
            "approval_scope_id": scope_id,
            "approval_status": "APPROVED",
            "runtime_approval_schema_version": 1,
        }
    ):
        raise ValueError("runtime_approval_invalid")
    return {
        "approval_candidate_sha256": candidate_sha,
        "approval_scope_id": scope_id,
        "historical_attempts": 0,
        "attempt_availability": "AVAILABLE",
        "proxy_image_id": candidate.get("proxy_image_id"),
    }


def reserve_scope(
    *,
    attempts_root: Path,
    scope_id: str,
    probe_id: str,
) -> dict[str, Path]:
    scope_directory = attempts_root / scope_id
    attempt_directory = scope_directory / probe_id
    try:
        os.mkdir(scope_directory, 0o700)
        os.mkdir(attempt_directory, 0o700)
    except FileExistsError as error:
        raise ValueError("probe_scope_attempt_unavailable") from error
    ledger_path = attempt_directory / "ledger.json"
    atomic_write_json(
        ledger_path,
        {
            "ai_call_count": 0,
            "probe_attempt_count": 0,
            "probe_id": probe_id,
            "provider_attempt_count": 0,
            "retry_count": 0,
            "scope_id": scope_id,
            "started_at": None,
            "state": "RESERVED_BEFORE_NETWORK",
        },
    )
    return {"attempt_directory": attempt_directory, "ledger_path": ledger_path}


def rollback_reservation_before_dispatch(
    *,
    attempts_root: Path,
    scope_id: str,
    reservation: Mapping[str, Path],
) -> None:
    scope_directory = attempts_root / scope_id
    attempt_directory = reservation.get("attempt_directory")
    ledger_path = reservation.get("ledger_path")
    if (
        not isinstance(attempt_directory, Path)
        or not isinstance(ledger_path, Path)
        or attempt_directory.parent != scope_directory
        or ledger_path != attempt_directory / "ledger.json"
    ):
        raise ValueError("probe_reservation_rollback_invalid")
    ledger = read_canonical_json(ledger_path, mode=0o600)
    if (
        ledger.get("state") != "RESERVED_BEFORE_NETWORK"
        or ledger.get("probe_attempt_count") != 0
        or ledger.get("provider_attempt_count") != 0
        or ledger.get("ai_call_count") != 0
    ):
        raise ValueError("probe_reservation_rollback_invalid")
    output_directory = attempt_directory / "runtime-output"
    if os.path.lexists(output_directory):
        metadata = output_directory.lstat()
        if (
            output_directory.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or any(output_directory.iterdir())
        ):
            raise ValueError("probe_reservation_rollback_invalid")
        output_directory.rmdir()
    ledger_path.unlink()
    attempt_directory.rmdir()
    scope_directory.rmdir()


def mark_dispatch_started(path: Path, *, started_at: str) -> dict[str, Any]:
    ledger = read_canonical_json(path, mode=0o600)
    if (
        ledger.get("state") != "RESERVED_BEFORE_NETWORK"
        or ledger.get("probe_attempt_count") != 0
        or ledger.get("provider_attempt_count") != 0
        or ledger.get("ai_call_count") != 0
        or ledger.get("retry_count") != 0
    ):
        raise ValueError("probe_ledger_state_invalid")
    ledger.update(
        probe_attempt_count=1,
        started_at=started_at,
        state="NETWORK_DISPATCH_STARTED",
    )
    atomic_write_json(path, ledger)
    return ledger


@contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise ValueError("probe_runtime_locked") from error
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def runtime_names(probe_id: str) -> dict[str, str]:
    if len(probe_id) != 32:
        raise ValueError("probe_id_invalid")
    suffix = probe_id[:12]
    return {
        "relay_network": f"phase2-proxy-tls-probe-relay-{suffix}",
        "egress_network": f"phase2-proxy-tls-probe-egress-{suffix}",
        "proxy": f"phase2-proxy-tls-probe-{suffix}",
    }


def network_create_commands(
    names: Mapping[str, str],
    *,
    mock_mode: bool,
) -> list[list[str]]:
    relay = [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        names["relay_network"],
    ]
    egress = [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
    ]
    if mock_mode:
        egress.append("--internal")
    egress.append(names["egress_network"])
    return [relay, egress]


def _mount(source: Path, destination: str, *, readonly: bool) -> str:
    options = ["type=bind", f"src={source}", f"dst={destination}"]
    if readonly:
        options.append("readonly")
    return ",".join(options)


def proxy_create_command(
    *,
    names: Mapping[str, str],
    image_id: str,
    output_dir: Path,
    probe_id: str,
) -> list[str]:
    return [
        "docker",
        "container",
        "create",
        "--name",
        names["proxy"],
        "--network",
        names["relay_network"],
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65532:65532",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint",
        "/usr/bin/python3",
        "--mount",
        _mount(output_dir, "/output", readonly=False),
        image_id,
        "/probe/dispatch_gate.py",
        "--gate",
        "/output/dispatch.ready",
        "--probe-id",
        probe_id,
    ]


def network_connect_command(names: Mapping[str, str]) -> list[str]:
    return [
        "docker",
        "network",
        "connect",
        names["egress_network"],
        names["proxy"],
    ]


def _proxy_networks(
    inspect_payload: Any,
) -> dict[str, Any]:
    try:
        networks = inspect_payload[0]["NetworkSettings"]["Networks"]
    except (IndexError, KeyError, TypeError) as error:
        raise ValueError("proxy_network_attachment_invalid") from error
    if not isinstance(networks, dict):
        raise ValueError("proxy_network_attachment_invalid")
    return networks


def validate_pre_start_proxy_attachments(
    inspect_payload: Any,
    names: Mapping[str, str],
) -> None:
    try:
        networks = _proxy_networks(inspect_payload)
    except ValueError as error:
        raise ValueError("proxy_pre_start_attachment_invalid") from error
    expected = {names["relay_network"], names["egress_network"]}
    if set(networks) != expected or any(
        not isinstance(networks[name], dict) for name in expected
    ):
        raise ValueError("proxy_pre_start_attachment_invalid")


def validate_post_start_proxy_attachments(
    inspect_payload: Any,
    names: Mapping[str, str],
    network_inspect: Mapping[str, Mapping[str, Any]],
) -> None:
    try:
        networks = _proxy_networks(inspect_payload)
        container_id = inspect_payload[0]["Id"]
        container_name = inspect_payload[0]["Name"].removeprefix("/")
    except ValueError as error:
        raise ValueError("proxy_post_start_attachment_invalid") from error
    except (IndexError, KeyError, TypeError, AttributeError) as error:
        raise ValueError("proxy_post_start_attachment_invalid") from error
    expected = {names["relay_network"], names["egress_network"]}
    if (
        set(networks) != expected
        or set(network_inspect) != expected
        or not isinstance(container_id, str)
        or not container_id
        or container_name != names["proxy"]
    ):
        raise ValueError("proxy_post_start_attachment_invalid")
    for name in expected:
        attachment = networks[name]
        metadata = network_inspect[name]
        if not isinstance(attachment, dict) or not isinstance(metadata, dict):
            raise ValueError("proxy_post_start_attachment_invalid")
        network_id = attachment.get("NetworkID")
        endpoint_id = attachment.get("EndpointID")
        containers = metadata.get("Containers")
        membership = containers.get(container_id) if isinstance(containers, dict) else None
        if (
            not isinstance(network_id, str)
            or not network_id
            or network_id != metadata.get("Id")
            or not isinstance(endpoint_id, str)
            or not endpoint_id
            or not isinstance(membership, dict)
            or membership.get("EndpointID") != endpoint_id
            or membership.get("Name") != names["proxy"]
        ):
            raise ValueError("proxy_post_start_attachment_invalid")


def validate_production_networks(
    network_inspect: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, str],
) -> None:
    expected = {names["relay_network"], names["egress_network"]}
    if set(network_inspect) != expected:
        raise ValueError("production_network_contract_invalid")
    relay = network_inspect[names["relay_network"]]
    egress = network_inspect[names["egress_network"]]
    if (
        relay.get("Driver") != "bridge"
        or relay.get("Internal") is not True
        or egress.get("Driver") != "bridge"
        or egress.get("Internal") is not False
    ):
        raise ValueError("production_network_contract_invalid")


_RECEIPT_FIELDS = {
    "ai_call_count",
    "authorization_constructed",
    "cipher_name",
    "completed_at",
    "errno",
    "exception_class",
    "failure_phase",
    "hostname_verification_target",
    "http_request_sent",
    "probe_id",
    "provider_attempt_count",
    "real_public_network_success_count",
    "receipt_schema_version",
    "secret_content_read",
    "sni_hostname",
    "stages",
    "started_at",
    "target_host",
    "target_port",
    "terminal_status",
    "tls_version",
    "verify_code",
    "verify_message",
}
_TERMINAL_STATUSES = {
    "PROBE_PASSED",
    "DNS_RESOLUTION_FAILED",
    "TLS_CERT_VERIFY_FAILED",
    "TLS_HOSTNAME_VERIFY_FAILED",
    "TLS_PROTOCOL_FAILED",
    "TLS_HANDSHAKE_TIMEOUT",
    "TLS_CONNECTION_RESET",
    "TLS_EOF",
    "TLS_OTHER_SSL_ERROR",
    "TLS_CLOSE_FAILED",
    "PROVIDER_CONNECT_FAILED",
}
_TLS_HANDSHAKE_FAILURES = _TERMINAL_STATUSES - {
    "PROBE_PASSED",
    "DNS_RESOLUTION_FAILED",
    "PROVIDER_CONNECT_FAILED",
    "TLS_CLOSE_FAILED",
}
_TLS_EXCEPTION_CLASSES = {
    "TLS_CERT_VERIFY_FAILED": {"SSLCertVerificationError"},
    "TLS_HOSTNAME_VERIFY_FAILED": {"SSLCertVerificationError"},
    "TLS_PROTOCOL_FAILED": {"SSLError"},
    "TLS_HANDSHAKE_TIMEOUT": {"TimeoutError"},
    "TLS_CONNECTION_RESET": {
        "BrokenPipeError",
        "ConnectionAbortedError",
        "ConnectionResetError",
    },
    "TLS_EOF": {"SSLEOFError", "SSLZeroReturnError"},
    "TLS_OTHER_SSL_ERROR": {"SSLError"},
}
# Receipts originate in the Linux probe image, so validate its EAI/POSIX codes
# instead of the macOS host's platform constants.
_DNS_ERROR_NUMBERS = frozenset(range(-12, 0))
_DNS_DIAGNOSTICS: dict[str, frozenset[int | None]] = {
    "gaierror": _DNS_ERROR_NUMBERS,
    "OSError": frozenset({None}),
}
_PROVIDER_CONNECT_DIAGNOSTICS: dict[str, frozenset[int | None]] = {
    "ConnectionRefusedError": frozenset({111}),
    "ConnectionResetError": frozenset({104}),
    "ConnectionAbortedError": frozenset({103}),
    "BrokenPipeError": frozenset({32}),
    "TimeoutError": frozenset({None, 110}),
    "PermissionError": frozenset({1, 13}),
    "InterruptedError": frozenset({4}),
    "gaierror": _DNS_ERROR_NUMBERS,
    "OSError": frozenset(
        {
            12,
            22,
            23,
            24,
            93,
            97,
            98,
            99,
            100,
            101,
            102,
            105,
            106,
            107,
            112,
            113,
            114,
            115,
        }
    ),
    "MissingConnectedSocket": frozenset({None}),
    "HTTPException": frozenset({None}),
    "NotConnected": frozenset({None}),
    "InvalidURL": frozenset({None}),
    "UnknownProtocol": frozenset({None}),
    "UnknownTransferEncoding": frozenset({None}),
    "UnimplementedFileMode": frozenset({None}),
    "IncompleteRead": frozenset({None}),
    "ImproperConnectionState": frozenset({None}),
    "CannotSendRequest": frozenset({None}),
    "CannotSendHeader": frozenset({None}),
    "ResponseNotReady": frozenset({None}),
    "BadStatusLine": frozenset({None}),
    "LineTooLong": frozenset({None}),
    "RemoteDisconnected": frozenset({None}),
}
_STAGE_FIELDS = {
    "certificate_verified",
    "clean_tls_close",
    "dns_completed",
    "hostname_verified",
    "tcp_connected",
    "tls_completed",
}


def _utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _optional_string(value: Any, *, maximum: int) -> bool:
    return value is None or (
        isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value
    )


def _optional_integer(value: Any) -> bool:
    return value is None or type(value) is int


def validate_probe_receipt(
    receipt: Mapping[str, Any],
    *,
    probe_id: str,
    container_exit_code: int,
) -> None:
    stages = receipt.get("stages")
    started_at = _utc_timestamp(receipt.get("started_at"))
    completed_at = _utc_timestamp(receipt.get("completed_at"))
    terminal_status = receipt.get("terminal_status")
    failure_phase = receipt.get("failure_phase")
    valid = (
        set(receipt) == _RECEIPT_FIELDS
        and receipt.get("receipt_schema_version") == 2
        and receipt.get("probe_id") == probe_id
        and receipt.get("target_host") == TARGET_HOST
        and receipt.get("target_port") == TARGET_PORT
        and receipt.get("sni_hostname") == TARGET_HOST
        and receipt.get("hostname_verification_target") == TARGET_HOST
        and receipt.get("terminal_status") in _TERMINAL_STATUSES
        and receipt.get("provider_attempt_count") == 0
        and receipt.get("ai_call_count") == 0
        and receipt.get("secret_content_read") is False
        and receipt.get("authorization_constructed") is False
        and receipt.get("http_request_sent") is False
        and receipt.get("real_public_network_success_count") == 0
        and isinstance(stages, dict)
        and set(stages) == _STAGE_FIELDS
        and all(isinstance(value, bool) for value in stages.values())
        and started_at is not None
        and completed_at is not None
        and completed_at >= started_at
        and _optional_string(receipt.get("exception_class"), maximum=128)
        and _optional_string(receipt.get("verify_message"), maximum=512)
        and _optional_integer(receipt.get("verify_code"))
        and _optional_integer(receipt.get("errno"))
        and _optional_string(receipt.get("tls_version"), maximum=64)
        and _optional_string(receipt.get("cipher_name"), maximum=128)
    )
    if terminal_status == "PROBE_PASSED":
        valid = (
            valid
            and container_exit_code == 0
            and failure_phase is None
            and all(stages.values())
            and isinstance(receipt.get("tls_version"), str)
            and isinstance(receipt.get("cipher_name"), str)
            and receipt.get("exception_class") is None
            and receipt.get("verify_code") is None
            and receipt.get("verify_message") is None
            and receipt.get("errno") is None
        )
    else:
        expected_stages: dict[str, bool] | None = None
        if terminal_status == "DNS_RESOLUTION_FAILED" and failure_phase == "dns":
            expected_stages = dict.fromkeys(_STAGE_FIELDS, False)
        elif terminal_status == "PROVIDER_CONNECT_FAILED" and failure_phase == "tcp":
            expected_stages = dict.fromkeys(_STAGE_FIELDS, False)
            expected_stages["dns_completed"] = True
        elif terminal_status == "TLS_CLOSE_FAILED" and failure_phase == "tls_close":
            expected_stages = dict.fromkeys(_STAGE_FIELDS, False)
            expected_stages["dns_completed"] = True
            expected_stages["tcp_connected"] = True
            expected_stages.update(
                tls_completed=True,
                certificate_verified=True,
                hostname_verified=True,
            )
            valid = (
                valid
                and isinstance(receipt.get("tls_version"), str)
                and isinstance(receipt.get("cipher_name"), str)
            )
        elif terminal_status in _TLS_HANDSHAKE_FAILURES and failure_phase == "tls_handshake":
            expected_stages = dict.fromkeys(_STAGE_FIELDS, False)
            expected_stages["dns_completed"] = True
            expected_stages["tcp_connected"] = True
        if failure_phase in {"dns", "tcp", "tls_handshake"}:
            valid = (
                valid
                and receipt.get("tls_version") is None
                and receipt.get("cipher_name") is None
            )
        if terminal_status in {
            "TLS_CERT_VERIFY_FAILED",
            "TLS_HOSTNAME_VERIFY_FAILED",
        }:
            valid = (
                valid
                and receipt.get("exception_class")
                in _TLS_EXCEPTION_CLASSES[terminal_status]
                and type(receipt.get("verify_code")) is int
                and isinstance(receipt.get("verify_message"), str)
            )
        elif terminal_status in _TLS_HANDSHAKE_FAILURES:
            valid = (
                valid
                and receipt.get("exception_class")
                in _TLS_EXCEPTION_CLASSES[terminal_status]
                and receipt.get("verify_code") is None
                and receipt.get("verify_message") is None
            )
        elif terminal_status == "TLS_CLOSE_FAILED":
            valid = (
                valid
                and receipt.get("verify_code") is None
                and receipt.get("verify_message") is None
            )
        elif terminal_status in {"DNS_RESOLUTION_FAILED", "PROVIDER_CONNECT_FAILED"}:
            diagnostics = (
                _DNS_DIAGNOSTICS
                if terminal_status == "DNS_RESOLUTION_FAILED"
                else _PROVIDER_CONNECT_DIAGNOSTICS
            )
            allowed_error_numbers = diagnostics.get(receipt.get("exception_class"))
            valid = (
                valid
                and receipt.get("verify_code") is None
                and receipt.get("verify_message") is None
                and allowed_error_numbers is not None
                and receipt.get("errno") in allowed_error_numbers
            )
        valid = (
            valid
            and container_exit_code == 2
            and expected_stages is not None
            and stages == expected_stages
            and isinstance(receipt.get("exception_class"), str)
        )
    if not valid:
        raise ValueError("probe_receipt_invalid")


def run_once(
    command: Sequence[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    result = run(
        list(command),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError("docker_command_failed")
    return result


def inspect_proxy_attachments(names: Mapping[str, str]) -> None:
    result = run_once(
        ["docker", "container", "inspect", names["proxy"]],
        timeout=15.0,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("proxy_network_attachment_invalid") from error
    validate_post_start_proxy_attachments(payload, names)


def wait_for_container_exit(
    names: Mapping[str, str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> int:
    result = run_once(
        ["docker", "container", "wait", names["proxy"]],
        run=run,
        timeout=210.0,
    )
    value = result.stdout.strip()
    if not value.isdigit():
        raise ValueError("proxy_container_exit_invalid")
    exit_code = int(value)
    if not 0 <= exit_code <= 255:
        raise ValueError("proxy_container_exit_invalid")
    return exit_code


def _network_metadata(
    names: Mapping[str, str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for role in ("relay_network", "egress_network"):
        result = run_once(
            ["docker", "network", "inspect", names[role]],
            run=run,
            timeout=15.0,
        )
        try:
            values[names[role]] = json.loads(result.stdout)[0]
        except (IndexError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("production_network_contract_invalid") from error
    return values


def _cleanup_runtime(
    names: Mapping[str, str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    def snapshot() -> tuple[dict[str, list[str]], list[str]]:
        commands = {
            "container": [
                "docker",
                "container",
                "ls",
                "--all",
                "--filter",
                f"name=^/{names['proxy']}$",
                "--format",
                "{{.Names}}",
            ],
            "network": [
                "docker",
                "network",
                "ls",
                "--filter",
                f"name=^{names['relay_network']}$",
                "--filter",
                f"name=^{names['egress_network']}$",
                "--format",
                "{{.Name}}",
            ],
        }
        values = {"container": [], "network": []}
        errors: list[str] = []
        for role, command in commands.items():
            try:
                result = run(
                    command,
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=15.0,
                )
                if result.returncode != 0:
                    errors.append(f"{role}_verification_nonzero")
                else:
                    values[role] = [
                        line for line in result.stdout.splitlines() if line.strip()
                    ]
            except (OSError, subprocess.TimeoutExpired):
                errors.append(f"{role}_verification_exception")
        return values, errors

    before, cleanup_errors = snapshot()
    removal_commands: list[tuple[str, list[str]]] = []
    if names["proxy"] in before["container"] or any(
        error.startswith("container_verification") for error in cleanup_errors
    ):
        removal_commands.append(
            (
                "container",
                ["docker", "container", "rm", "--force", names["proxy"]],
            )
        )
    for role in ("egress_network", "relay_network"):
        if names[role] in before["network"] or any(
            error.startswith("network_verification") for error in cleanup_errors
        ):
            removal_commands.append(
                (role, ["docker", "network", "rm", names[role]])
            )
    for role, command in removal_commands:
        try:
            result = run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=15.0,
            )
            if result.returncode != 0:
                cleanup_errors.append(f"{role}_remove_nonzero")
        except (OSError, subprocess.TimeoutExpired):
            cleanup_errors.append(f"{role}_remove_exception")

    residues, verification_errors = snapshot()
    cleanup_errors.extend(verification_errors)
    status_value = (
        "PASSED"
        if not residues["container"]
        and not residues["network"]
        and not cleanup_errors
        else "FAILED"
    )
    return {
        "cleanup_errors": cleanup_errors,
        "container_residue": residues["container"],
        "network_residue": residues["network"],
        "status": status_value,
    }


def _mark_terminal(
    ledger_path: Path,
    *,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    ledger = read_canonical_json(ledger_path, mode=0o600)
    if (
        ledger.get("state") != "NETWORK_DISPATCH_STARTED"
        or ledger.get("probe_attempt_count") != 1
    ):
        raise ValueError("probe_ledger_state_invalid")
    ledger.update(
        completed_at=receipt["completed_at"],
        state="TERMINAL_AWAITING_CLEANUP",
        terminal_status=receipt["terminal_status"],
    )
    atomic_write_json(ledger_path, ledger)
    return ledger


def _mark_cleanup(
    ledger_path: Path,
    *,
    cleanup: Mapping[str, Any],
) -> dict[str, Any]:
    ledger = read_canonical_json(ledger_path, mode=0o600)
    state = ledger.get("state")
    cleanup_status = cleanup.get("status")
    if (
        state
        not in {
            "RESERVED_BEFORE_NETWORK",
            "NETWORK_DISPATCH_STARTED",
            "TERMINAL_AWAITING_CLEANUP",
        }
        or cleanup_status not in {"PASSED", "FAILED"}
        or not isinstance(cleanup.get("container_residue"), list)
        or not isinstance(cleanup.get("network_residue"), list)
        or not isinstance(cleanup.get("cleanup_errors", []), list)
    ):
        raise ValueError("probe_cleanup_state_invalid")
    pre_dispatch = state == "RESERVED_BEFORE_NETWORK"
    terminal = state == "TERMINAL_AWAITING_CLEANUP"
    if pre_dispatch and cleanup_status != "FAILED":
        raise ValueError("probe_cleanup_state_invalid")
    if pre_dispatch:
        next_state = "PRE_DISPATCH_CLEANUP_FAILED"
    elif cleanup_status == "FAILED":
        next_state = "TERMINAL_CLEANUP_FAILED" if terminal else "POST_DISPATCH_CLEANUP_FAILED"
    else:
        next_state = "TERMINAL" if terminal else "POST_DISPATCH_ERROR_CLEANUP_PASSED"
    ledger.update(
        cleanup_status=cleanup_status,
        cleanup_errors=cleanup.get("cleanup_errors", []),
        container_residue=cleanup["container_residue"],
        network_residue=cleanup["network_residue"],
        state=next_state,
    )
    atomic_write_json(ledger_path, ledger)
    return ledger


def run_live_probe(
    *,
    candidate_path: Path = CANDIDATE_PATH,
    scope_path: Path = SCOPE_PATH,
    approval_path: Path = LOCAL_APPROVAL_PATH,
    attempts_root: Path = ATTEMPTS_ROOT,
    runner_path: Path = RUNNER_PATH,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    probe_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    now: Callable[[], str] = _utc_now,
) -> dict[str, Any]:
    identity = load_execution_identity(
        candidate_path=candidate_path,
        scope_path=scope_path,
        approval_path=approval_path,
        runner_path=runner_path,
    )
    image_id = identity.get("proxy_image_id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise ValueError("runtime_approval_invalid")
    attempts_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    probe_id = probe_id_factory()
    names = runtime_names(probe_id)
    reservation = reserve_scope(
        attempts_root=attempts_root,
        scope_id=identity["approval_scope_id"],
        probe_id=probe_id,
    )
    output_dir = reservation["attempt_directory"] / "runtime-output"
    dispatch_started = False
    result_payload: dict[str, Any] | None = None
    pending_error: BaseException | None = None
    try:
        output_dir.mkdir(mode=0o733)
        for command in network_create_commands(names, mock_mode=False):
            run_once(command, run=run, timeout=30.0)
        run_once(
            proxy_create_command(
                names=names,
                image_id=image_id,
                output_dir=output_dir,
                probe_id=probe_id,
            ),
            run=run,
            timeout=30.0,
        )
        run_once(network_connect_command(names), run=run, timeout=30.0)
        inspected = run_once(
            ["docker", "container", "inspect", names["proxy"]],
            run=run,
            timeout=15.0,
        )
        validate_pre_start_proxy_attachments(json.loads(inspected.stdout), names)
        validate_production_networks(
            _network_metadata(names, run=run),
            names,
        )
        run_once(
            ["docker", "container", "start", names["proxy"]],
            run=run,
            timeout=30.0,
        )
        inspected = run_once(
            ["docker", "container", "inspect", names["proxy"]],
            run=run,
            timeout=15.0,
        )
        post_start_networks = _network_metadata(names, run=run)
        validate_production_networks(post_start_networks, names)
        validate_post_start_proxy_attachments(
            json.loads(inspected.stdout),
            names,
            post_start_networks,
        )
        mark_dispatch_started(reservation["ledger_path"], started_at=now())
        dispatch_started = True
        publish_dispatch_gate(
            output_dir / "dispatch.ready",
            probe_id=probe_id,
        )
        container_exit_code = wait_for_container_exit(names, run=run)
        receipt = read_canonical_json(output_dir / "receipt.json", mode=0o600)
        validate_probe_receipt(
            receipt,
            probe_id=probe_id,
            container_exit_code=container_exit_code,
        )
        _mark_terminal(reservation["ledger_path"], receipt=receipt)
        result_payload = {
            "ai_call_count": 0,
            "approval_scope_id": identity["approval_scope_id"],
            "probe_attempt_count": 1,
            "probe_id": probe_id,
            "provider_attempt_count": 0,
            "retry_count": 0,
            "terminal_status": receipt["terminal_status"],
        }
    except BaseException as error:
        pending_error = error
    finally:
        cleanup = _cleanup_runtime(names, run=run)
        if dispatch_started:
            try:
                _mark_cleanup(reservation["ledger_path"], cleanup=cleanup)
            except BaseException as error:
                pending_error = pending_error or error
        elif cleanup["status"] == "PASSED":
            try:
                rollback_reservation_before_dispatch(
                    attempts_root=attempts_root,
                    scope_id=identity["approval_scope_id"],
                    reservation=reservation,
                )
            except BaseException as error:
                pending_error = pending_error or error
        else:
            try:
                _mark_cleanup(reservation["ledger_path"], cleanup=cleanup)
            except BaseException as error:
                pending_error = pending_error or error
        if cleanup["status"] != "PASSED":
            raise RuntimeError("probe_cleanup_failed") from pending_error
    if pending_error is not None:
        raise pending_error
    if result_payload is None:
        raise RuntimeError("probe_result_missing")
    return result_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("explicit --live is required")
    with exclusive_lock(LOCK_PATH):
        result = run_live_probe()
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("terminal_status") == "PROBE_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
