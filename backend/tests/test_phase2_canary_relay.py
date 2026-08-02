from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.services.phase2_ai_worker_protocol import (
    FakeClaimsWorker,
    build_worker_projection,
)
from app.services.phase2_claims_service import canonical_json_bytes
from app.services.phase2_provider_relay_protocol import build_relay_request

REPO_ROOT = Path(__file__).resolve().parents[2]
RELAY_PATH = REPO_ROOT / "docker/phase2-canary-relay/relay.py"
RUNTIME_CONTRACT_PATH = (
    REPO_ROOT / "docker/phase2-canary-relay/runtime-contract.json"
)
FACTS_PATH = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"
REQUEST_ID = "b" * 32


@pytest.fixture(scope="module")
def relay_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase2_canary_relay_under_test",
        RELAY_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@pytest.fixture(scope="module")
def projection() -> dict[str, Any]:
    facts_bytes = FACTS_PATH.read_bytes()
    facts = json.loads(facts_bytes)
    return build_worker_projection(
        facts,
        hashlib.sha256(facts_bytes).hexdigest(),
        facts_bytes=facts_bytes,
    )


@pytest.fixture(scope="module")
def relay_request(projection: dict[str, Any]) -> dict[str, Any]:
    return build_relay_request(
        projection,
        request_id=REQUEST_ID,
    ).model_dump(mode="json")


@pytest.fixture(scope="module")
def relay_response(projection: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "request_id": REQUEST_ID,
        "symbol": "000403.SZ",
        "projection_sha256": projection["projection_sha256"],
        "claims_candidate": json.loads(FakeClaimsWorker().run(projection)),
    }


def _write_inputs(
    root: Path,
    request: dict[str, Any],
    projection: dict[str, Any],
) -> tuple[Path, Path, Path]:
    request_path = root / "request.json"
    projection_path = root / "projection.json"
    output = root / "output"
    request_path.write_bytes(canonical_json_bytes(request))
    projection_path.write_bytes(canonical_json_bytes(projection))
    request_path.chmod(0o444)
    projection_path.chmod(0o444)
    output.mkdir(mode=0o700)
    return request_path, projection_path, output


def test_relay_uses_the_shared_75_second_wait_contract(
    relay_module: ModuleType,
) -> None:
    contract = relay_module.load_runtime_contract(str(RUNTIME_CONTRACT_PATH))

    assert contract["provider_total_timeout_seconds"] == 60
    assert contract["relay_candidate_wait_timeout_seconds"] == 75
    assert relay_module.generation_controls(contract) == {
        "temperature": 0,
        "response_format": "typed_claims_json",
        "tool_use": False,
        "web_browsing": False,
        "file_tools": False,
        "function_calling": False,
        "streaming": False,
        "retry_count": 0,
        "maximum_output_bytes": 1_048_576,
        "timeout_seconds": 75,
    }


def test_relay_dispatches_once_and_atomically_publishes_candidate(
    relay_module: ModuleType,
    relay_request: dict[str, Any],
    projection: dict[str, Any],
    relay_response: dict[str, Any],
    tmp_path: Path,
) -> None:
    request_path, projection_path, output = _write_inputs(
        tmp_path,
        relay_request,
        projection,
    )
    calls: list[tuple[bytes, int]] = []

    def requester(body: bytes, timeout_seconds: int) -> tuple[int, bytes]:
        calls.append((body, timeout_seconds))
        return 200, canonical_json_bytes(relay_response)

    receipt = relay_module.run_relay(
        request_path=str(request_path),
        projection_path=str(projection_path),
        output_dir=str(output),
        runtime_contract_path=str(RUNTIME_CONTRACT_PATH),
        requester=requester,
    )

    assert len(calls) == 1
    assert calls[0][1] == 75
    assert json.loads(calls[0][0])["generation"]["timeout_seconds"] == 75
    candidate_bytes = (output / "candidate.json").read_bytes()
    marker = json.loads((output / "candidate.ready.json").read_bytes())
    assert json.loads(candidate_bytes) == relay_response
    assert marker == {
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "projection_sha256": projection["projection_sha256"],
        "request_id": REQUEST_ID,
    }
    assert not list(output.glob("*.partial"))
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["proxy_request_count"] == 1
    assert receipt["retry_count"] == 0


def test_relay_timeout_never_retries_or_publishes_candidate(
    relay_module: ModuleType,
    relay_request: dict[str, Any],
    projection: dict[str, Any],
    tmp_path: Path,
) -> None:
    request_path, projection_path, output = _write_inputs(
        tmp_path,
        relay_request,
        projection,
    )
    calls = 0

    def requester(_body: bytes, _timeout_seconds: int) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        raise relay_module.RelayError("RELAY_TIMEOUT", proxy_request_count=1)

    receipt = relay_module.run_relay(
        request_path=str(request_path),
        projection_path=str(projection_path),
        output_dir=str(output),
        runtime_contract_path=str(RUNTIME_CONTRACT_PATH),
        requester=requester,
    )

    assert calls == 1
    assert receipt["status"] == "REJECTED"
    assert receipt["error_category"] == "RELAY_TIMEOUT"
    assert receipt["proxy_request_count"] == 1
    assert receipt["retry_count"] == 0
    assert not (output / "candidate.json").exists()
    assert not (output / "candidate.ready.json").exists()


def test_existing_candidate_blocks_before_dispatch(
    relay_module: ModuleType,
    relay_request: dict[str, Any],
    projection: dict[str, Any],
    tmp_path: Path,
) -> None:
    request_path, projection_path, output = _write_inputs(
        tmp_path,
        relay_request,
        projection,
    )
    (output / "candidate.json").write_bytes(b"{}\n")
    calls = 0

    def requester(_body: bytes, _timeout_seconds: int) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        return 200, b"{}"

    receipt = relay_module.run_relay(
        request_path=str(request_path),
        projection_path=str(projection_path),
        output_dir=str(output),
        runtime_contract_path=str(RUNTIME_CONTRACT_PATH),
        requester=requester,
    )

    assert calls == 0
    assert receipt["error_category"] == "CANDIDATE_ALREADY_EXISTS"


def test_candidate_publish_failure_never_writes_ready_marker(
    relay_module: ModuleType,
    relay_request: dict[str, Any],
    projection: dict[str, Any],
    relay_response: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    real_replace = os.replace

    def fail_candidate_replace(source: str | Path, target: str | Path) -> None:
        if Path(target).name == "candidate.json":
            raise OSError("synthetic candidate rename failure")
        real_replace(source, target)

    monkeypatch.setattr(relay_module.os, "replace", fail_candidate_replace)

    with pytest.raises(relay_module.RelayError, match="CANDIDATE_PUBLISH_FAILED"):
        relay_module.publish_candidate(
            relay_response,
            relay_request,
            projection,
            str(output),
        )

    assert not (output / "candidate.json").exists()
    assert not (output / "candidate.ready.json").exists()
    assert not list(output.glob("*.partial"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "c" * 32),
        ("symbol", "600489.SH"),
        ("projection_sha256", "0" * 64),
    ],
)
def test_candidate_binding_mismatch_is_rejected_before_publication(
    field: str,
    value: str,
    relay_module: ModuleType,
    relay_request: dict[str, Any],
    projection: dict[str, Any],
    relay_response: dict[str, Any],
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    mutated = {**relay_response, field: value}

    with pytest.raises(relay_module.RelayError, match="CANDIDATE_BINDING_INVALID"):
        relay_module.publish_candidate(
            mutated,
            relay_request,
            projection,
            str(output),
        )

    assert not list(output.iterdir())
