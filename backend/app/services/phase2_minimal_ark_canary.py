"""Minimal one-shot Host path for the approved Ark typed-Claims Canary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.providers.ark_provider import (
    ARK_ENDPOINT_ALIAS,
    ARK_EXACT_MODEL_ID,
    ARK_PROVIDER_ID,
    ArkProviderContractError,
    adapt_ark_responses_envelope,
    build_ark_responses_request,
)
from app.providers.base import ProviderRequest
from app.schemas.phase2_claims import WorkerClaimsCandidate
from app.services.phase2_ai_worker_protocol import build_worker_projection
from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_isolation_runtime import validate_isolated_candidate

EXPECTED_FACTS_SHA256 = (
    "adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956"
)
EXPECTED_PROJECTION_SHA256 = (
    "0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f"
)
EXPECTED_SYMBOL = "000403.SZ"
EXPECTED_TRADE_DATE = "2026-07-31"
REQUEST_CONTRACT_PATH = Path(__file__).with_name(
    "phase2_minimal_ark_request_contract.json"
)
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_TERMINAL_STATES = {
    "SUCCEEDED",
    "HTTP_ERROR",
    "TIMEOUT",
    "TRANSPORT_ERROR",
    "RESPONSE_REJECTED",
    "CANDIDATE_REJECTED",
    "CLAIMS_REJECTED",
    "RENDERER_REJECTED",
    "INTERNAL_ERROR",
}
_VALIDATION_STATES = {
    "NOT_RUN",
    "VALID",
    "REJECTED",
    "BLOCKED",
}


class MinimalArkCanaryError(RuntimeError):
    """Fail-closed error for the minimal Ark Canary path."""


class MinimalArkRequestContract(BaseModel):
    """Closed runtime contract for the direct Host HTTPS request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract_version: Literal[1]
    provider_id: Literal["volcengine_ark"]
    exact_model_id: Literal["doubao-seed-2-1-turbo-260628"]
    scheme: Literal["https"]
    host: Literal["ark.cn-beijing.volces.com"]
    port: Literal[443]
    path: Literal["/api/v3/responses"]
    method: Literal["POST"]
    tls_verification: Literal[True]
    hostname_verification: Literal[True]
    tls_minimum: Literal["TLSv1_2"]
    follow_redirects: Literal[False]
    trust_env: Literal[False]
    connect_timeout_seconds: Literal[10]
    read_timeout_seconds: Literal[180]
    write_timeout_seconds: Literal[180]
    maximum_response_bytes: Literal[1_048_576]
    stream: Literal[False]
    store: Literal[False]
    tools: tuple[()]
    strict_json_schema: Literal[True]
    retry_count: Literal[0]
    maximum_attempts: Literal[1]
    can_publish: Literal[False]
    trade_date: Literal["2026-07-31"]

    @field_validator("tools", mode="before")
    @classmethod
    def require_empty_tools(cls, value: Any) -> tuple[()]:
        if value not in ([], ()):
            raise ValueError("tools_forbidden")
        return ()

    @property
    def endpoint(self) -> str:
        return f"{self.scheme}://{self.host}{self.path}"


@dataclass(frozen=True)
class MinimalExecutionInputs:
    provider_id: str
    exact_model_id: str
    endpoint_alias: str
    endpoint: str
    request_id: str
    symbol: str
    trade_date: str
    facts_sha256: str
    projection_sha256: str
    prompt_sha256: str
    projection: dict[str, Any]
    claims_schema: dict[str, Any]
    request_body: dict[str, Any]
    contract: MinimalArkRequestContract


@dataclass(frozen=True)
class MinimalHTTPResponse:
    status_code: int
    content_type: str
    body: bytes


@dataclass(frozen=True)
class MinimalCanaryResult:
    status: str
    attempt_count: int
    provider_http_status: int | None
    candidate_validation_state: str
    claims_validation_state: str
    renderer_validation_state: str
    claim_count: int
    claims_candidate_sha256: str
    rendered_sha256: str
    can_publish: Literal[False]
    ledger_path: Path


CommandExecutor = Callable[
    [list[str], float],
    subprocess.CompletedProcess[str],
]


def _read_regular_bytes(path: Path, error_code: str) -> bytes:
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise MinimalArkCanaryError(error_code)
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
        try:
            before = os.fstat(descriptor)
            raw = b""
            while len(raw) <= 8 * 1024 * 1024:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                raw += chunk
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise MinimalArkCanaryError(error_code) from exc
    if (
        len(raw) > 8 * 1024 * 1024
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(raw) != after.st_size
    ):
        raise MinimalArkCanaryError(error_code)
    return raw


def load_minimal_request_contract(
    path: Path = REQUEST_CONTRACT_PATH,
) -> MinimalArkRequestContract:
    try:
        value = json.loads(_read_regular_bytes(path, "request_contract_invalid"))
        contract = MinimalArkRequestContract.model_validate(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        MinimalArkCanaryError,
    ) as exc:
        raise MinimalArkCanaryError("request_contract_invalid") from exc
    if list(contract.tools):
        raise MinimalArkCanaryError("request_contract_invalid")
    return contract


def _provider_request(
    *,
    request_id: str,
    projection: dict[str, Any],
    claims_schema: dict[str, Any],
) -> ProviderRequest:
    return ProviderRequest(
        provider_id=ARK_PROVIDER_ID,
        exact_model_id=ARK_EXACT_MODEL_ID,
        endpoint_alias=ARK_ENDPOINT_ALIAS,
        request_id=request_id,
        symbol=EXPECTED_SYMBOL,
        projection_sha256=projection["projection_sha256"],
        facts_sha256=projection["facts_sha256"],
        claims_schema=claims_schema,
        minimal_projection=projection,
    )


def build_minimal_execution_inputs(
    repo_root: Path,
    request_id: str,
) -> MinimalExecutionInputs:
    if _REQUEST_ID.fullmatch(request_id) is None:
        raise MinimalArkCanaryError("request_id_invalid")
    contract = load_minimal_request_contract()
    facts_path = repo_root / "reports/phase2_facts/000403SZ_facts.json"
    facts_bytes = _read_regular_bytes(facts_path, "facts_file_invalid")
    facts_sha256 = hashlib.sha256(facts_bytes).hexdigest()
    if facts_sha256 != EXPECTED_FACTS_SHA256:
        raise MinimalArkCanaryError("facts_identity_mismatch")
    try:
        facts = json.loads(facts_bytes)
        projection = build_worker_projection(
            facts,
            facts_sha256,
            facts_bytes=facts_bytes,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise MinimalArkCanaryError("projection_build_failed") from exc
    if (
        projection.get("projection_sha256") != EXPECTED_PROJECTION_SHA256
        or projection.get("facts_sha256") != EXPECTED_FACTS_SHA256
        or projection.get("symbol") != EXPECTED_SYMBOL
        or projection.get("trade_date") != EXPECTED_TRADE_DATE
    ):
        raise MinimalArkCanaryError("projection_identity_mismatch")
    claims_schema = WorkerClaimsCandidate.model_json_schema(mode="validation")
    request = _provider_request(
        request_id=request_id,
        projection=projection,
        claims_schema=claims_schema,
    )
    request_body = build_ark_responses_request(request)
    placeholder = _provider_request(
        request_id="0" * 32,
        projection=projection,
        claims_schema=claims_schema,
    )
    prompt_sha256 = hashlib.sha256(
        canonical_json_bytes(build_ark_responses_request(placeholder)["input"])
    ).hexdigest()
    inputs = MinimalExecutionInputs(
        provider_id=ARK_PROVIDER_ID,
        exact_model_id=ARK_EXACT_MODEL_ID,
        endpoint_alias=ARK_ENDPOINT_ALIAS,
        endpoint=contract.endpoint,
        request_id=request_id,
        symbol=EXPECTED_SYMBOL,
        trade_date=EXPECTED_TRADE_DATE,
        facts_sha256=facts_sha256,
        projection_sha256=projection["projection_sha256"],
        prompt_sha256=prompt_sha256,
        projection=projection,
        claims_schema=claims_schema,
        request_body=request_body,
        contract=contract,
    )
    validate_minimal_execution_inputs(inputs)
    return inputs


def validate_minimal_execution_inputs(inputs: MinimalExecutionInputs) -> None:
    fmt = inputs.request_body.get("text", {}).get("format", {})
    expected_request_body = build_ark_responses_request(
        _provider_request(
            request_id=inputs.request_id,
            projection=inputs.projection,
            claims_schema=inputs.claims_schema,
        )
    )
    if (
        inputs.provider_id != ARK_PROVIDER_ID
        or inputs.exact_model_id != ARK_EXACT_MODEL_ID
        or inputs.endpoint_alias != ARK_ENDPOINT_ALIAS
        or inputs.endpoint != inputs.contract.endpoint
        or inputs.symbol != EXPECTED_SYMBOL
        or inputs.trade_date != EXPECTED_TRADE_DATE
        or inputs.facts_sha256 != EXPECTED_FACTS_SHA256
    ):
        raise MinimalArkCanaryError("execution_identity_mismatch")
    if inputs.projection_sha256 != EXPECTED_PROJECTION_SHA256:
        raise MinimalArkCanaryError("projection_identity_mismatch")
    if (
        inputs.projection.get("projection_sha256") != inputs.projection_sha256
        or inputs.projection.get("facts_sha256") != inputs.facts_sha256
        or inputs.request_body != expected_request_body
        or inputs.request_body.get("model") != ARK_EXACT_MODEL_ID
        or inputs.request_body.get("stream") is not False
        or inputs.request_body.get("store") is not False
        or inputs.request_body.get("tools") != []
        or fmt.get("type") != "json_schema"
        or fmt.get("strict") is not True
        or inputs.contract.retry_count != 0
        or inputs.contract.maximum_attempts != 1
        or inputs.contract.can_publish is not False
    ):
        raise MinimalArkCanaryError("request_contract_invalid")


def _private_directory(path: Path) -> Path:
    try:
        if path.is_symlink():
            raise MinimalArkCanaryError("runtime_directory_invalid")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = os.lstat(path)
        if not stat.S_ISDIR(metadata.st_mode):
            raise MinimalArkCanaryError("runtime_directory_invalid")
        os.chmod(path, 0o700, follow_symlinks=False)
    except OSError as exc:
        raise MinimalArkCanaryError("runtime_directory_invalid") from exc
    return path


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary: Path | None = None
    try:
        raw = canonical_json_bytes(dict(value))
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".staging",
            dir=path.parent,
        )
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | _DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError) as exc:
        raise MinimalArkCanaryError("ledger_write_failed") from exc


def _read_ledger(path: Path) -> dict[str, Any]:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise MinimalArkCanaryError("ledger_invalid") from exc
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise MinimalArkCanaryError("ledger_invalid")
    raw = _read_regular_bytes(path, "ledger_invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinimalArkCanaryError("ledger_invalid") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise MinimalArkCanaryError("ledger_invalid")
    return value


def reserve_minimal_attempt(
    attempts_root: Path,
    *,
    scope_id: str,
    candidate_sha256: str,
    inputs: MinimalExecutionInputs,
    now: str,
) -> Path:
    validate_minimal_execution_inputs(inputs)
    if (
        _SHA256.fullmatch(scope_id) is None
        or _SHA256.fullmatch(candidate_sha256) is None
        or _REQUEST_ID.fullmatch(inputs.request_id) is None
    ):
        raise MinimalArkCanaryError("attempt_identity_invalid")
    root = _private_directory(attempts_root)
    scope = root / scope_id
    try:
        os.mkdir(scope, 0o700)
    except FileExistsError as exc:
        raise MinimalArkCanaryError("scope_already_consumed") from exc
    except OSError as exc:
        raise MinimalArkCanaryError("attempt_reservation_failed") from exc
    request = scope / inputs.request_id
    try:
        os.mkdir(request, 0o700)
    except OSError as exc:
        raise MinimalArkCanaryError("attempt_reservation_failed") from exc
    ledger = {
        "attempt_count": 0,
        "candidate_sha256": candidate_sha256,
        "candidate_validation_state": "NOT_RUN",
        "claims_validation_state": "NOT_RUN",
        "completed_at": None,
        "created_at": now,
        "dispatch_started": False,
        "dispatch_started_at": None,
        "exact_model_id": inputs.exact_model_id,
        "ledger_version": 1,
        "provider_http_status": None,
        "provider_id": inputs.provider_id,
        "request_id": inputs.request_id,
        "scope_id": scope_id,
        "state": "PREPARED",
        "symbol": inputs.symbol,
        "terminal_state": None,
        "trade_date": inputs.trade_date,
    }
    ledger_path = request / "ledger.json"
    _atomic_write_json(ledger_path, ledger)
    return ledger_path


def mark_minimal_dispatch(path: Path, *, now: str) -> dict[str, Any]:
    ledger = _read_ledger(path)
    if (
        ledger.get("state") != "PREPARED"
        or ledger.get("attempt_count") != 0
        or ledger.get("dispatch_started") is not False
    ):
        raise MinimalArkCanaryError("attempt_already_consumed")
    ledger.update(
        {
            "attempt_count": 1,
            "dispatch_started": True,
            "dispatch_started_at": now,
            "state": "DISPATCH_STARTED",
        }
    )
    _atomic_write_json(path, ledger)
    return ledger


def release_prepared_attempt(path: Path) -> None:
    ledger = _read_ledger(path)
    request_directory = path.parent
    scope_directory = request_directory.parent
    attempts_root = scope_directory.parent
    if (
        ledger.get("state") != "PREPARED"
        or ledger.get("attempt_count") != 0
        or ledger.get("dispatch_started") is not False
        or ledger.get("terminal_state") is not None
        or request_directory.name != ledger.get("request_id")
        or scope_directory.name != ledger.get("scope_id")
    ):
        raise MinimalArkCanaryError("prepared_attempt_release_forbidden")
    try:
        if set(request_directory.iterdir()) != {path}:
            raise MinimalArkCanaryError("prepared_attempt_release_forbidden")
        path.unlink()
        request_handle = os.open(request_directory, os.O_RDONLY | _DIRECTORY)
        try:
            os.fsync(request_handle)
        finally:
            os.close(request_handle)
        request_directory.rmdir()
        if any(scope_directory.iterdir()):
            raise MinimalArkCanaryError("prepared_attempt_release_forbidden")
        scope_directory.rmdir()
        root_handle = os.open(attempts_root, os.O_RDONLY | _DIRECTORY)
        try:
            os.fsync(root_handle)
        finally:
            os.close(root_handle)
    except MinimalArkCanaryError:
        raise
    except OSError as exc:
        raise MinimalArkCanaryError("prepared_attempt_release_failed") from exc


def finalize_minimal_attempt(
    path: Path,
    *,
    terminal_state: str,
    provider_http_status: int | None,
    candidate_validation_state: str,
    claims_validation_state: str,
    now: str,
) -> dict[str, Any]:
    ledger = _read_ledger(path)
    if (
        ledger.get("state") != "DISPATCH_STARTED"
        or ledger.get("attempt_count") != 1
        or ledger.get("dispatch_started") is not True
        or terminal_state not in _TERMINAL_STATES
        or candidate_validation_state not in _VALIDATION_STATES
        or claims_validation_state not in _VALIDATION_STATES
        or (
            provider_http_status is not None
            and (
                isinstance(provider_http_status, bool)
                or not isinstance(provider_http_status, int)
                or not 100 <= provider_http_status <= 599
            )
        )
    ):
        raise MinimalArkCanaryError("ledger_terminal_transition_invalid")
    ledger.update(
        {
            "candidate_validation_state": candidate_validation_state,
            "claims_validation_state": claims_validation_state,
            "completed_at": now,
            "provider_http_status": provider_http_status,
            "state": "TERMINAL",
            "terminal_state": terminal_state,
        }
    )
    _atomic_write_json(path, ledger)
    return ledger


def _default_command_executor(
    command: list[str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def read_minimal_ark_keychain_secret_once(
    *,
    executor: CommandExecutor = _default_command_executor,
) -> str:
    command = [
        "security",
        "find-generic-password",
        "-w",
        "-s",
        "tickflow-phase2-canary-volcengine-ark",
    ]
    try:
        result = executor(command, 10.0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MinimalArkCanaryError("keychain_secret_unavailable") from exc
    if result.returncode != 0:
        raise MinimalArkCanaryError("keychain_secret_unavailable")
    value = result.stdout.removesuffix("\n")
    if (
        not value
        or value != value.strip()
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise MinimalArkCanaryError("keychain_secret_invalid")
    return value


class ArkHostTransport:
    """One-call verified HTTPS transport with an injectable offline boundary."""

    def __init__(self, *, mock_transport: httpx.BaseTransport | None = None) -> None:
        self._mock_transport = mock_transport

    @staticmethod
    def _tls_context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def send(
        self,
        inputs: MinimalExecutionInputs,
        secret: str,
    ) -> MinimalHTTPResponse:
        validate_minimal_execution_inputs(inputs)
        if (
            not secret
            or secret != secret.strip()
            or any(character in secret for character in ("\x00", "\r", "\n"))
        ):
            raise MinimalArkCanaryError("keychain_secret_invalid")
        timeout = httpx.Timeout(
            connect=inputs.contract.connect_timeout_seconds,
            read=inputs.contract.read_timeout_seconds,
            write=inputs.contract.write_timeout_seconds,
            pool=inputs.contract.connect_timeout_seconds,
        )
        client_options: dict[str, Any] = {
            "verify": self._tls_context(),
            "follow_redirects": False,
            "trust_env": False,
            "timeout": timeout,
        }
        if self._mock_transport is not None:
            client_options["transport"] = self._mock_transport
        try:
            with httpx.Client(**client_options) as client, client.stream(
                "POST",
                inputs.endpoint,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
                content=canonical_json_bytes(inputs.request_body),
            ) as response:
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > inputs.contract.maximum_response_bytes:
                        raise MinimalArkCanaryError("provider_response_too_large")
                return MinimalHTTPResponse(
                    status_code=response.status_code,
                    content_type=response.headers.get("Content-Type", ""),
                    body=body,
                )
        except MinimalArkCanaryError:
            raise
        except httpx.TimeoutException as exc:
            raise MinimalArkCanaryError("provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise MinimalArkCanaryError("provider_transport_error") from exc


def _strict_envelope(raw: bytes) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_key")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MinimalArkCanaryError("provider_envelope_invalid") from exc
    if not isinstance(value, dict):
        raise MinimalArkCanaryError("provider_envelope_invalid")
    return value


def _result(
    *,
    status: str,
    provider_http_status: int | None,
    candidate_validation_state: str,
    claims_validation_state: str,
    renderer_validation_state: str,
    claim_count: int,
    claims_candidate_sha256: str,
    rendered_sha256: str,
    ledger_path: Path,
) -> MinimalCanaryResult:
    return MinimalCanaryResult(
        status=status,
        attempt_count=1,
        provider_http_status=provider_http_status,
        candidate_validation_state=candidate_validation_state,
        claims_validation_state=claims_validation_state,
        renderer_validation_state=renderer_validation_state,
        claim_count=claim_count,
        claims_candidate_sha256=claims_candidate_sha256,
        rendered_sha256=rendered_sha256,
        can_publish=False,
        ledger_path=ledger_path,
    )


def _finalize_result(
    ledger_path: Path,
    *,
    terminal_state: str,
    provider_http_status: int | None,
    candidate_validation_state: str,
    claims_validation_state: str,
    renderer_validation_state: str,
    clock: Callable[[], str],
    claim_count: int = 0,
    claims_candidate_sha256: str = "",
    rendered_sha256: str = "",
) -> MinimalCanaryResult:
    finalize_minimal_attempt(
        ledger_path,
        terminal_state=terminal_state,
        provider_http_status=provider_http_status,
        candidate_validation_state=candidate_validation_state,
        claims_validation_state=claims_validation_state,
        now=clock(),
    )
    return _result(
        status=terminal_state,
        provider_http_status=provider_http_status,
        candidate_validation_state=candidate_validation_state,
        claims_validation_state=claims_validation_state,
        renderer_validation_state=renderer_validation_state,
        claim_count=claim_count,
        claims_candidate_sha256=claims_candidate_sha256,
        rendered_sha256=rendered_sha256,
        ledger_path=ledger_path,
    )


def execute_minimal_ark_canary(
    *,
    repo_root: Path,
    attempts_root: Path,
    scope_id: str,
    candidate_sha256: str,
    request_id: str,
    transport: ArkHostTransport,
    secret_reader: Callable[[], str],
    request_id_guard: Callable[[str], None],
    clock: Callable[[], str],
) -> MinimalCanaryResult:
    """Execute one already approved attempt without fallback or retry."""
    try:
        request_id_guard(request_id)
    except MinimalArkCanaryError:
        raise
    except Exception as exc:
        raise MinimalArkCanaryError("request_id_guard_failed") from exc
    inputs = build_minimal_execution_inputs(repo_root, request_id)
    ledger_path = reserve_minimal_attempt(
        attempts_root,
        scope_id=scope_id,
        candidate_sha256=candidate_sha256,
        inputs=inputs,
        now=clock(),
    )
    try:
        secret = secret_reader()
        if not isinstance(secret, str):
            raise MinimalArkCanaryError("keychain_secret_invalid")
    except Exception as exc:
        try:
            release_prepared_attempt(ledger_path)
        except MinimalArkCanaryError as cleanup_exc:
            raise MinimalArkCanaryError(
                "prepared_attempt_release_failed"
            ) from cleanup_exc
        if isinstance(exc, MinimalArkCanaryError):
            raise
        raise MinimalArkCanaryError("keychain_secret_unavailable") from exc
    mark_minimal_dispatch(ledger_path, now=clock())
    try:
        response = transport.send(inputs, secret)
    except MinimalArkCanaryError as exc:
        error_code = str(exc)
        terminal_state = {
            "provider_timeout": "TIMEOUT",
            "provider_transport_error": "TRANSPORT_ERROR",
            "provider_response_too_large": "RESPONSE_REJECTED",
        }.get(error_code, "INTERNAL_ERROR")
        return _finalize_result(
            ledger_path,
            terminal_state=terminal_state,
            provider_http_status=None,
            candidate_validation_state="NOT_RUN",
            claims_validation_state="NOT_RUN",
            renderer_validation_state="NOT_RUN",
            clock=clock,
        )
    finally:
        secret = ""

    if response.status_code != 200:
        return _finalize_result(
            ledger_path,
            terminal_state="HTTP_ERROR",
            provider_http_status=response.status_code,
            candidate_validation_state="NOT_RUN",
            claims_validation_state="NOT_RUN",
            renderer_validation_state="NOT_RUN",
            clock=clock,
        )
    if not response.content_type.lower().startswith("application/json"):
        return _finalize_result(
            ledger_path,
            terminal_state="RESPONSE_REJECTED",
            provider_http_status=response.status_code,
            candidate_validation_state="NOT_RUN",
            claims_validation_state="NOT_RUN",
            renderer_validation_state="NOT_RUN",
            clock=clock,
        )
    try:
        envelope = _strict_envelope(response.body)
    except MinimalArkCanaryError:
        return _finalize_result(
            ledger_path,
            terminal_state="RESPONSE_REJECTED",
            provider_http_status=response.status_code,
            candidate_validation_state="NOT_RUN",
            claims_validation_state="NOT_RUN",
            renderer_validation_state="NOT_RUN",
            clock=clock,
        )
    request = _provider_request(
        request_id=inputs.request_id,
        projection=inputs.projection,
        claims_schema=inputs.claims_schema,
    )
    try:
        provider_result = adapt_ark_responses_envelope(
            request,
            provider_http_status=response.status_code,
            content_type=response.content_type,
            envelope=envelope,
            stage_metadata={
                "execution_path": "minimal_ark_host",
                "retry_count": 0,
            },
        )
    except ArkProviderContractError as exc:
        error_code = str(exc)
        terminal_state = (
            "CANDIDATE_REJECTED"
            if error_code.startswith("ark_candidate_")
            or error_code.startswith("ark_structured_output_")
            else "RESPONSE_REJECTED"
        )
        return _finalize_result(
            ledger_path,
            terminal_state=terminal_state,
            provider_http_status=response.status_code,
            candidate_validation_state=(
                "REJECTED" if terminal_state == "CANDIDATE_REJECTED" else "NOT_RUN"
            ),
            claims_validation_state="NOT_RUN",
            renderer_validation_state="NOT_RUN",
            clock=clock,
        )

    candidate = provider_result.claims_candidate.model_dump(mode="json")
    try:
        evidence = validate_isolated_candidate(repo_root, candidate, inputs.projection)
    except Exception:
        return _finalize_result(
            ledger_path,
            terminal_state="INTERNAL_ERROR",
            provider_http_status=response.status_code,
            candidate_validation_state="VALID",
            claims_validation_state="BLOCKED",
            renderer_validation_state="BLOCKED",
            clock=clock,
        )
    if evidence.claims_status != "CLAIMS_VALID":
        return _finalize_result(
            ledger_path,
            terminal_state="CLAIMS_REJECTED",
            provider_http_status=response.status_code,
            candidate_validation_state="VALID",
            claims_validation_state="REJECTED",
            renderer_validation_state="NOT_RUN",
            clock=clock,
            claims_candidate_sha256=evidence.candidate_sha256,
        )
    if evidence.renderer_status != "RENDERED_VALID" or evidence.errors:
        return _finalize_result(
            ledger_path,
            terminal_state="RENDERER_REJECTED",
            provider_http_status=response.status_code,
            candidate_validation_state="VALID",
            claims_validation_state="VALID",
            renderer_validation_state="REJECTED",
            clock=clock,
            claim_count=evidence.claim_count,
            claims_candidate_sha256=evidence.candidate_sha256,
        )
    return _finalize_result(
        ledger_path,
        terminal_state="SUCCEEDED",
        provider_http_status=response.status_code,
        candidate_validation_state="VALID",
        claims_validation_state="VALID",
        renderer_validation_state="VALID",
        clock=clock,
        claim_count=evidence.claim_count,
        claims_candidate_sha256=evidence.candidate_sha256,
        rendered_sha256=evidence.rendered_sha256,
    )
