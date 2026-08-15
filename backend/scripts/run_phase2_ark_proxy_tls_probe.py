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


def load_execution_identity(
    *,
    candidate_path: Path,
    scope_path: Path,
    approval_path: Path,
) -> dict[str, Any]:
    candidate_raw = _read_regular_bytes(candidate_path)
    try:
        candidate = json.loads(candidate_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("runtime_approval_invalid") from error
    if not isinstance(candidate, dict) or candidate_raw != canonical_json_bytes(candidate):
        raise ValueError("runtime_approval_invalid")
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
        or scope.get("scope_type") != SCOPE_TYPE
        or scope.get("approval_candidate_sha256") != candidate_sha
        or scope.get("approval_scope_id") != scope_id
        or scope.get("historical_attempts") != 0
        or scope.get("attempt_availability") != "AVAILABLE"
        or scope.get("retry_count") != 0
        or scope.get("maximum_probe_attempts") != 1
        or scope.get("provider_scope_shared") is not False
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
    runner_path: Path,
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
        _mount(runner_path, "/probe/probe.py", readonly=True),
        "--mount",
        _mount(output_dir, "/output", readonly=False),
        image_id,
        "/probe/probe.py",
        "--probe-id",
        probe_id,
        "--output",
        "/output/receipt.json",
    ]


def network_connect_command(names: Mapping[str, str]) -> list[str]:
    return [
        "docker",
        "network",
        "connect",
        names["egress_network"],
        names["proxy"],
    ]


def validate_proxy_attachments(
    inspect_payload: Any,
    names: Mapping[str, str],
) -> None:
    try:
        networks = inspect_payload[0]["NetworkSettings"]["Networks"]
    except (IndexError, KeyError, TypeError) as error:
        raise ValueError("proxy_network_attachment_invalid") from error
    expected = {names["relay_network"], names["egress_network"]}
    if not isinstance(networks, dict) or set(networks) != expected:
        raise ValueError("proxy_network_attachment_invalid")
    if any(
        not isinstance(networks[name], dict)
        or not isinstance(networks[name].get("NetworkID"), str)
        or not networks[name]["NetworkID"]
        for name in expected
    ):
        raise ValueError("proxy_network_attachment_invalid")


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
    "PROVIDER_CONNECT_FAILED",
}
_STAGE_FIELDS = {
    "certificate_verified",
    "clean_tls_close",
    "dns_completed",
    "hostname_verified",
    "tcp_connected",
    "tls_completed",
}


def validate_probe_receipt(receipt: Mapping[str, Any], *, probe_id: str) -> None:
    stages = receipt.get("stages")
    valid = (
        set(receipt) == _RECEIPT_FIELDS
        and receipt.get("receipt_schema_version") == 1
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
        and isinstance(receipt.get("started_at"), str)
        and isinstance(receipt.get("completed_at"), str)
    )
    if receipt.get("terminal_status") == "PROBE_PASSED":
        valid = valid and all(stages.values())
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
    validate_proxy_attachments(payload, names)


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
) -> None:
    for command in (
        ["docker", "container", "rm", "--force", names["proxy"]],
        ["docker", "network", "rm", names["egress_network"]],
        ["docker", "network", "rm", names["relay_network"]],
    ):
        run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=15.0,
        )


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
        state="TERMINAL",
        terminal_status=receipt["terminal_status"],
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
    )
    image_id = identity.get("proxy_image_id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise ValueError("runtime_approval_invalid")
    attempts_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    probe_id = probe_id_factory()
    reservation = reserve_scope(
        attempts_root=attempts_root,
        scope_id=identity["approval_scope_id"],
        probe_id=probe_id,
    )
    output_dir = reservation["attempt_directory"] / "runtime-output"
    output_dir.mkdir(mode=0o733)
    names = runtime_names(probe_id)
    dispatch_started = False
    try:
        for command in network_create_commands(names, mock_mode=False):
            run_once(command, run=run, timeout=30.0)
        run_once(
            proxy_create_command(
                names=names,
                image_id=image_id,
                runner_path=runner_path,
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
        validate_proxy_attachments(json.loads(inspected.stdout), names)
        validate_production_networks(
            _network_metadata(names, run=run),
            names,
        )
        mark_dispatch_started(reservation["ledger_path"], started_at=now())
        dispatch_started = True
        run(
            ["docker", "start", "--attach", names["proxy"]],
            capture_output=True,
            check=False,
            text=True,
            timeout=210.0,
        )
        receipt = read_canonical_json(output_dir / "receipt.json", mode=0o600)
        validate_probe_receipt(receipt, probe_id=probe_id)
        _mark_terminal(reservation["ledger_path"], receipt=receipt)
        return {
            "ai_call_count": 0,
            "approval_scope_id": identity["approval_scope_id"],
            "probe_attempt_count": 1,
            "probe_id": probe_id,
            "provider_attempt_count": 0,
            "retry_count": 0,
            "terminal_status": receipt["terminal_status"],
        }
    except BaseException:
        if not dispatch_started:
            rollback_reservation_before_dispatch(
                attempts_root=attempts_root,
                scope_id=identity["approval_scope_id"],
                reservation=reservation,
            )
        raise
    finally:
        _cleanup_runtime(names, run=run)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("explicit --live is required")
    with exclusive_lock(LOCK_PATH):
        result = run_live_probe()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
