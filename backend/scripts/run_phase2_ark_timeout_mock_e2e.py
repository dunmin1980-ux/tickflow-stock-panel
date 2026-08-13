#!/usr/bin/env python3
"""Generate deterministic Ark timeout evidence without sockets or wall-clock delay."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.services.phase2_ai_worker_protocol import FakeClaimsWorker, build_worker_projection
from app.services.phase2_ark_timeout_contract import ark_runtime_contract_sha256
from app.services.phase2_canary_orchestrator import (
    ApprovedCanaryArtifacts,
    ArkMockCanaryBackend,
    CanaryOrchestratorConfig,
    run_single_symbol_canary,
)
from app.services.phase2_claims_service import canonical_json_bytes

SCENARIOS = (
    ("fast_response", "provider_1s", 1.0, "SUCCEEDED"),
    ("about_60s_response", "provider_60s", 60.0, "SUCCEEDED"),
    ("before_180s_response", "provider_179s", 179.0, "SUCCEEDED"),
    ("at_180s_deadline", "provider_180s", 180.0, "PROVIDER_TIMEOUT"),
)


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o644)
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


def _run_scenario(
    *,
    repo_root: Path,
    root: Path,
    projection: dict[str, Any],
    label: str,
    scenario: str,
    elapsed_seconds: float,
    expected_terminal: str,
    index: int,
) -> dict[str, Any]:
    run_root = root / f"run-{index}"
    paths = {
        name: run_root / name
        for name in ("state", "attempts", "work", "output", "historical", "candidates")
    }
    for path in paths.values():
        path.mkdir(parents=True, mode=0o700)
    request_id = f"{index + 100:032x}"
    backend = ArkMockCanaryBackend(
        scenario=scenario,
        relay_response={
            "protocol_version": 1,
            "request_id": request_id,
            "symbol": "000403.SZ",
            "projection_sha256": projection["projection_sha256"],
            "claims_candidate": json.loads(FakeClaimsWorker().run(projection)),
        },
    )
    artifacts = ApprovedCanaryArtifacts(
        approval_candidate_sha256=hashlib.sha256(label.encode()).hexdigest(),
        facts_sha256=projection["facts_sha256"],
        projection_sha256=projection["projection_sha256"],
        proxy_image_id="sha256:" + "1" * 64,
        relay_image_id="sha256:" + "2" * 64,
        timeout_contract_sha256=ark_runtime_contract_sha256(),
        readiness_contract_sha256="3" * 64,
        orchestrator_source_sha256="4" * 64,
    )
    result = run_single_symbol_canary(
        config=CanaryOrchestratorConfig(
            repo_root=repo_root,
            state_root=paths["state"],
            attempts_root=paths["attempts"],
            work_root=paths["work"],
            canary_output_root=paths["output"],
            historical_evidence_root=paths["historical"],
            candidate_roots=(paths["candidates"],),
            facts_path=repo_root / "reports/phase2_facts/000403SZ_facts.json",
        ),
        artifacts=artifacts,
        projection=projection,
        backend=backend,
        artifact_verifier=lambda value: value == artifacts
        or (_ for _ in ()).throw(ValueError("artifact_mismatch")),
        secret_reader=lambda: "synthetic-offline-value",
        request_id_factory=lambda: request_id,
        wall_clock=lambda: "2026-08-13T00:00:00Z",
        monotonic=backend.monotonic,
        provider_id="volcengine_ark",
        exact_model_id="doubao-seed-2-1-turbo-260628",
        endpoint_alias="ark_responses_cn_beijing_v1",
    )
    proxy_receipt = json.loads(
        (
            paths["output"]
            / "receipts"
            / request_id
            / "proxy-receipt.json"
        ).read_bytes()
    )
    proxy_receipt_identity = {
        field: proxy_receipt.get(field)
        for field in ("endpoint_alias", "exact_model_id", "provider_id")
    }
    succeeded = expected_terminal == "SUCCEEDED"
    inbox = paths["output"] / "inbox"
    candidate_valid = succeeded and result.host_validation_status == "VALID"
    renderer_deterministic = succeeded and len(list(inbox.glob("*.md"))) == 1
    value = {
        "scenario": label,
        "virtual_elapsed_seconds": elapsed_seconds,
        "terminal_state": result.terminal_state,
        "provider_attempt_count": result.provider_attempt_count,
        "retry_count": result.retry_count,
        "candidate_valid": candidate_valid,
        "claims_valid": candidate_valid,
        "renderer_deterministic": renderer_deterministic,
        "provider_http": "MOCK_ONLY",
        "proxy_receipt_identity": proxy_receipt_identity,
        "real_provider_attempt_count": 0,
        "failure_stage": (
            None if succeeded else "WAITING_FOR_PROVIDER_RESPONSE_HEADERS"
        ),
        "passed": (
            result.terminal_state == expected_terminal
            and result.provider_attempt_count == 1
            and result.retry_count == 0
            and (not succeeded or (candidate_valid and renderer_deterministic))
            and (succeeded or result.route == "rejected")
        ),
        "result": {
            key: value
            for key, value in asdict(result).items()
            if key
            in {
                "approval_scope_id",
                "route",
                "host_validation_status",
                "container_residue_count",
                "network_residue_count",
                "secret_file_residue_count",
                "temporary_file_residue_count",
            }
        },
    }
    return value


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    facts_path = repo_root / "reports/phase2_facts/000403SZ_facts.json"
    facts_raw = facts_path.read_bytes()
    projection = build_worker_projection(
        json.loads(facts_raw),
        hashlib.sha256(facts_raw).hexdigest(),
        facts_bytes=facts_raw,
    )
    with tempfile.TemporaryDirectory(prefix="phase2-ark-timeout-mock-") as name:
        root = Path(name).resolve(strict=True)
        runs = [
            _run_scenario(
                repo_root=repo_root,
                root=root,
                projection=projection,
                label=label,
                scenario=scenario,
                elapsed_seconds=elapsed,
                expected_terminal=terminal,
                index=index,
            )
            for index, (label, scenario, elapsed, terminal) in enumerate(SCENARIOS, start=1)
        ]
    value = {
        "ark_timeout_mock_e2e_schema_version": 1,
        "status": "PASSED" if all(run["passed"] for run in runs) else "FAILED",
        "provider_id": "volcengine_ark",
        "exact_model_id": "doubao-seed-2-1-turbo-260628",
        "runtime_contract_sha256": ark_runtime_contract_sha256(),
        "provider_timeout_seconds": 180,
        "relay_timeout_seconds": 195,
        "host_timeout_seconds": 210,
        "retry_count": 0,
        "maximum_provider_attempts": 1,
        "real_provider_attempt_count": 0,
        "real_ai_call_count": 0,
        "public_network_used": False,
        "runs": runs,
    }
    output = repo_root / "reports/phase2_provider_ark/timeout_mock_e2e.json"
    _atomic_write(output, canonical_json_bytes(value))
    print(json.dumps({"status": value["status"], "output": str(output)}, sort_keys=True))
    return 0 if value["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
