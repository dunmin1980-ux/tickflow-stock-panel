from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.services.phase2_canary_runtime_artifact import (
    BASE_IMAGE_DIGEST,
    BASE_IMAGE_REFERENCE,
    PROXY_IMAGE_NAME,
    RELAY_IMAGE_NAME,
    RuntimeArtifactError,
    RuntimeImageContentEvidence,
    RuntimeImageEvidence,
    build_runtime_artifact_candidate,
    build_runtime_image_commands,
    compute_runtime_artifact_input_hashes,
    inspect_runtime_image,
    validate_local_base_image,
    verify_runtime_artifact_candidate,
    verify_runtime_image_contents,
)
from app.services.phase2_claims_service import canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
OLD_CANDIDATE = (
    REPO_ROOT
    / "reports/phase2_provider_canary/superseded"
    / "45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127.json"
)
OLD_CANDIDATE_SHA256 = (
    "45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127"
)
PREVIOUS_CANDIDATE = (
    REPO_ROOT
    / "reports/phase2_provider_canary/superseded"
    / "e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b.json"
)
LEGACY_CANDIDATE = (
    REPO_ROOT / "reports/phase2_openai_proxy_artifact/approval_candidate.json"
)
LEGACY_CANDIDATE_SHA256 = (
    "c7614947da89cae8727ed3c59d1e965dd73bc95450953d3e59c1a89dd0b9f96d"
)
BASE_LAYERS = tuple("sha256:" + str(index % 10) * 64 for index in range(43))
PROXY_ADDED_LAYERS = tuple("sha256:" + character * 64 for character in "abcdef0")
RELAY_ADDED_LAYERS = tuple("sha256:" + character * 64 for character in "abcdef")


def _base_inspect() -> list[dict[str, Any]]:
    return [
        {
            "Id": BASE_IMAGE_DIGEST,
            "RepoDigests": [BASE_IMAGE_REFERENCE],
            "RootFS": {"Type": "layers", "Layers": list(BASE_LAYERS)},
        }
    ]


def _image_inspect(
    *,
    role: str,
    image_id: str,
    hashes: Any,
) -> list[dict[str, Any]]:
    if role == "proxy":
        image_name = PROXY_IMAGE_NAME
        entrypoint = ["/usr/bin/python3", "/proxy/proxy.py"]
        environment = [
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
            "LANG=C.UTF-8",
            "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONHASHSEED=0",
        ]
        labels = {
            "org.tickflow.phase2.base-image-digest": BASE_IMAGE_DIGEST,
            "org.tickflow.phase2.proxy-source-sha256": hashes.proxy_source_sha256,
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
    else:
        image_name = RELAY_IMAGE_NAME
        entrypoint = ["/usr/bin/python3", "/relay/relay.py"]
        environment = [
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
            "LANG=C.UTF-8",
            "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONHASHSEED=0",
        ]
        labels = {
            "org.tickflow.phase2.base-image-digest": BASE_IMAGE_DIGEST,
            "org.tickflow.phase2.relay-source-sha256": hashes.relay_source_sha256,
            "org.tickflow.phase2.runtime-contract-sha256": (
                hashes.runtime_contract_sha256
            ),
        }
    return [
        {
            "Id": image_id,
            "RepoTags": [image_name],
            "RootFS": {
                "Type": "layers",
                "Layers": [
                    *BASE_LAYERS,
                    *(PROXY_ADDED_LAYERS if role == "proxy" else RELAY_ADDED_LAYERS),
                ],
            },
            "Config": {
                "User": "65532:65532",
                "Entrypoint": entrypoint,
                "Cmd": None,
                "Env": environment,
                "Labels": labels,
            },
        }
    ]


def _history(role: str) -> list[dict[str, str]]:
    target = "/proxy/proxy.py" if role == "proxy" else "/relay/relay.py"
    return [{"CreatedBy": f"ENTRYPOINT [\"/usr/bin/python3\" \"{target}\"]"}]


def _image_evidence(role: str, hashes: Any) -> RuntimeImageEvidence:
    image_id = "sha256:" + ("a" if role == "proxy" else "b") * 64
    return inspect_runtime_image(
        _image_inspect(role=role, image_id=image_id, hashes=hashes),
        _history(role),
        role=role,
        hashes=hashes,
        base=validate_local_base_image(_base_inspect()),
        expected_image_id=image_id,
    )


def _content_evidence(role: str, hashes: Any) -> RuntimeImageContentEvidence:
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


def test_old_approval_candidate_is_preserved_exactly() -> None:
    assert hashlib.sha256(OLD_CANDIDATE.read_bytes()).hexdigest() == (
        OLD_CANDIDATE_SHA256
    )
    assert hashlib.sha256(PREVIOUS_CANDIDATE.read_bytes()).hexdigest() == (
        "e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b"
    )
    assert hashlib.sha256(LEGACY_CANDIDATE.read_bytes()).hexdigest() == (
        LEGACY_CANDIDATE_SHA256
    )


def test_runtime_artifact_input_hashes_bind_every_runtime_component() -> None:
    hashes = compute_runtime_artifact_input_hashes(REPO_ROOT)

    for field, value in hashes.model_dump().items():
        assert field.endswith("_sha256")
        assert len(value) == 64
        int(value, 16)
    assert hashes.runtime_contract_sha256 == (
        "34aa9235d25ecc3f2b658551382f2a8ade031eb84f46cc2a1d79bcbc86bdcf68"
    )
    assert hashes.readiness_contract_sha256 == hashlib.sha256(
        (
            REPO_ROOT
            / "docker/phase2-openai-egress-proxy/readiness-contract.json"
        ).read_bytes()
    ).hexdigest()
    assert hashes.orchestrator_source_sha256 == hashlib.sha256(
        (REPO_ROOT / "backend/app/services/phase2_canary_orchestrator.py").read_bytes()
    ).hexdigest()
    assert hashes.observability_source_sha256 == hashlib.sha256(
        (
            REPO_ROOT
            / "backend/app/services/phase2_canary_observability.py"
        ).read_bytes()
    ).hexdigest()
    assert hashes.launcher_source_sha256 == hashlib.sha256(
        (REPO_ROOT / "backend/scripts/run_phase2_single_symbol_canary.py").read_bytes()
    ).hexdigest()
    assert hashes.runbook_sha256 == hashlib.sha256(
        (REPO_ROOT / "docs/phase2-single-call-orchestrator-runbook.md").read_bytes()
    ).hexdigest()
    assert hashes.artifact_verifier_source_sha256 == hashlib.sha256(
        (
            REPO_ROOT
            / "backend/app/services/phase2_canary_runtime_artifact.py"
        ).read_bytes()
    ).hexdigest()
    assert hashes.artifact_builder_source_sha256 == hashlib.sha256(
        (
            REPO_ROOT / "backend/scripts/build_phase2_canary_runtime_offline.py"
        ).read_bytes()
    ).hexdigest()


def test_build_commands_are_fresh_offline_and_secret_free(tmp_path: Path) -> None:
    hashes = compute_runtime_artifact_input_hashes(REPO_ROOT)
    proxy_command, relay_command = build_runtime_image_commands(
        REPO_ROOT,
        hashes,
        proxy_iidfile=tmp_path / "proxy.iid",
        relay_iidfile=tmp_path / "relay.iid",
    )

    for command, image_name in (
        (proxy_command, PROXY_IMAGE_NAME),
        (relay_command, RELAY_IMAGE_NAME),
    ):
        assert command[:3] == ["docker", "build", "--pull=false"]
        assert "--network=none" in command
        assert "--no-cache" in command
        assert command[command.index("--tag") + 1] == image_name
        assert "--secret" not in command
        assert "--ssh" not in command
        serialized = " ".join(command)
        assert "Authorization" not in serialized
        assert "provider-auth" not in serialized
        assert "api_key" not in serialized.lower()
    assert any(
        item == f"READINESS_CONTRACT_SHA256={hashes.readiness_contract_sha256}"
        for item in proxy_command
    )


@pytest.mark.parametrize("role", ["proxy", "relay"])
def test_image_inspection_binds_identity_history_and_base_prefix(role: str) -> None:
    hashes = compute_runtime_artifact_input_hashes(REPO_ROOT)
    evidence = _image_evidence(role, hashes)

    assert evidence.role == role
    assert evidence.image_valid is True
    assert evidence.history_clean is True
    assert evidence.base_rootfs_prefix_verified is True
    assert evidence.runtime_user == "65532:65532"
    assert evidence.rootfs_layer_count == (50 if role == "proxy" else 49)


def test_image_inspection_rejects_sensitive_history() -> None:
    hashes = compute_runtime_artifact_input_hashes(REPO_ROOT)
    with pytest.raises(RuntimeArtifactError, match="artifact_image_history_sensitive"):
        inspect_runtime_image(
            _image_inspect(
                role="proxy",
                image_id="sha256:" + "a" * 64,
                hashes=hashes,
            ),
            [{"CreatedBy": "Authorization: Bearer forbidden"}],
            role="proxy",
            hashes=hashes,
            base=validate_local_base_image(_base_inspect()),
            expected_image_id="sha256:" + "a" * 64,
        )


@pytest.mark.parametrize("role", ["proxy", "relay"])
def test_image_content_verification_copies_without_starting_container(
    role: str,
    tmp_path: Path,
) -> None:
    del tmp_path
    commands: list[list[str]] = []
    source_by_container_path = {
        "/proxy/proxy.py": REPO_ROOT / "docker/phase2-openai-egress-proxy/proxy.py",
        "/proxy/responses-contract.json": (
            REPO_ROOT
            / "docker/phase2-openai-egress-proxy/responses-contract.json"
        ),
        "/proxy/runtime-contract.json": (
            REPO_ROOT / "docker/phase2-openai-egress-proxy/runtime-contract.json"
        ),
        "/proxy/readiness-contract.json": (
            REPO_ROOT
            / "docker/phase2-openai-egress-proxy/readiness-contract.json"
        ),
        "/relay/relay.py": REPO_ROOT / "docker/phase2-canary-relay/relay.py",
        "/relay/runtime-contract.json": (
            REPO_ROOT / "docker/phase2-canary-relay/runtime-contract.json"
        ),
    }

    def executor(command: Any) -> subprocess.CompletedProcess[str]:
        command = list(command)
        commands.append(command)
        if command[:2] == ["docker", "cp"]:
            container_path = command[2].split(":", 1)[1]
            shutil.copy2(source_by_container_path[container_path], command[3])
        return subprocess.CompletedProcess(command, 0, "", "")

    hashes = compute_runtime_artifact_input_hashes(REPO_ROOT)
    evidence = verify_runtime_image_contents(
        REPO_ROOT,
        role=role,
        image_id="sha256:" + ("a" if role == "proxy" else "b") * 64,
        executor=executor,
    )

    assert evidence.content_valid is True
    assert evidence.cleanup_complete is True
    assert [command[:2] for command in commands].count(["docker", "create"]) == 1
    assert [command[:2] for command in commands].count(["docker", "rm"]) == 1
    assert not any(command[:2] in (["docker", "run"], ["docker", "start"]) for command in commands)
    assert evidence.runtime_contract_sha256 == hashes.runtime_contract_sha256


def test_candidate_is_deterministic_and_has_exact_launcher_identity() -> None:
    hashes = compute_runtime_artifact_input_hashes(REPO_ROOT)
    candidate = build_runtime_artifact_candidate(
        REPO_ROOT,
        proxy=_image_evidence("proxy", hashes),
        relay=_image_evidence("relay", hashes),
        proxy_contents=_content_evidence("proxy", hashes),
        relay_contents=_content_evidence("relay", hashes),
    )

    assert candidate == build_runtime_artifact_candidate(
        REPO_ROOT,
        proxy=_image_evidence("proxy", hashes),
        relay=_image_evidence("relay", hashes),
        proxy_contents=_content_evidence("proxy", hashes),
        relay_contents=_content_evidence("relay", hashes),
    )
    assert candidate.status == (
        "PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL"
    )
    assert set(candidate.artifact_identity.model_dump()) == {
        "facts_sha256",
        "projection_sha256",
        "proxy_image_id",
        "relay_image_id",
        "timeout_contract_sha256",
        "readiness_contract_sha256",
        "orchestrator_source_sha256",
    }
    assert candidate.provider_attempt_count == 0
    assert candidate.ai_call_count == 0
    assert candidate.provider_http == "NOT_RUN"
    assert candidate.new_approval_installed is False
    assert candidate.old_approval_candidate_sha256 == OLD_CANDIDATE_SHA256


def test_candidate_verifier_rejects_hash_or_source_drift(tmp_path: Path) -> None:
    hashes = compute_runtime_artifact_input_hashes(REPO_ROOT)
    candidate = build_runtime_artifact_candidate(
        REPO_ROOT,
        proxy=_image_evidence("proxy", hashes),
        relay=_image_evidence("relay", hashes),
        proxy_contents=_content_evidence("proxy", hashes),
        relay_contents=_content_evidence("relay", hashes),
    )
    candidate_path = tmp_path / "candidate.json"
    raw = canonical_json_bytes(candidate.model_dump(mode="json"))
    candidate_path.write_bytes(raw)

    with pytest.raises(RuntimeArtifactError, match="artifact_candidate_hash_mismatch"):
        verify_runtime_artifact_candidate(
            REPO_ROOT,
            candidate_path,
            expected_candidate_sha256="0" * 64,
            inspect_images=False,
        )

    value = json.loads(raw)
    value["inputs"]["launcher_source_sha256"] = "0" * 64
    candidate_path.write_bytes(canonical_json_bytes(value))
    with pytest.raises(RuntimeArtifactError, match="artifact_candidate_input_mismatch"):
        verify_runtime_artifact_candidate(
            REPO_ROOT,
            candidate_path,
            expected_candidate_sha256=hashlib.sha256(
                candidate_path.read_bytes()
            ).hexdigest(),
            inspect_images=False,
        )
