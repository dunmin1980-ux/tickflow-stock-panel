from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.providers.ark_contract import (
    build_ark_approval_candidate,
    build_ark_build_provenance,
    build_ark_responses_contract,
    validate_ark_mock_e2e_evidence,
    validate_ark_build_provenance,
    validate_ark_approval_candidate,
    validate_ark_image_set,
)
from app.services.phase2_canary_runtime_artifact import BASE_IMAGE_REFERENCE
from app.services.phase2_claims_service import canonical_json_bytes
from scripts import build_phase2_ark_contract as ark_contract_builder
from scripts import build_phase2_ark_runtime_images as ark_image_builder
from scripts import run_phase2_ark_mock_e2e as ark_mock_runner

REPO_ROOT = Path(__file__).resolve().parents[2]

PROXY_IMAGE_ID = "sha256:" + "1" * 64
RELAY_IMAGE_ID = "sha256:" + "2" * 64


def _expected_labels(role: str) -> dict[str, str]:
    if role == "proxy":
        return {
            "org.tickflow.phase2.provider-id": "volcengine_ark",
            "org.tickflow.phase2.base-image-digest": (
                "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
            ),
            "org.tickflow.phase2.proxy-dockerfile-sha256": hashlib.sha256(
                (REPO_ROOT / "docker/phase2-ark-egress-proxy/Dockerfile").read_bytes()
            ).hexdigest(),
            "org.tickflow.phase2.proxy-source-sha256": hashlib.sha256(
                (REPO_ROOT / "docker/phase2-ark-egress-proxy/proxy.py").read_bytes()
            ).hexdigest(),
            "org.tickflow.phase2.responses-contract-sha256": hashlib.sha256(
                (
                    REPO_ROOT
                    / "docker/phase2-ark-egress-proxy/responses-contract.json"
                ).read_bytes()
            ).hexdigest(),
            "org.tickflow.phase2.proxy-policy-sha256": (
                "406f837966c47ee842c8b7e338a021dbf9007872135a0349d4326cd0abda061c"
            ),
            "org.tickflow.phase2.runtime-contract-sha256": (
                "3cb3064e4e68bb2e9a07a9153f9c2bcef8de127ac0fd779b2c41e1ab9e0ac681"
            ),
            "org.tickflow.phase2.readiness-contract-sha256": (
                "c667b982d81f14d485e43c40deba637141675582cea044e2090e44ec465b7a20"
            ),
        }
    return {
        "org.tickflow.phase2.provider-id": "volcengine_ark",
        "org.tickflow.phase2.base-image-digest": (
            "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
        ),
        "org.tickflow.phase2.relay-dockerfile-sha256": hashlib.sha256(
            (REPO_ROOT / "docker/phase2-ark-canary-relay/Dockerfile").read_bytes()
        ).hexdigest(),
        "org.tickflow.phase2.relay-source-sha256": hashlib.sha256(
            (REPO_ROOT / "docker/phase2-ark-canary-relay/relay.py").read_bytes()
        ).hexdigest(),
        "org.tickflow.phase2.runtime-contract-sha256": (
            "3cb3064e4e68bb2e9a07a9153f9c2bcef8de127ac0fd779b2c41e1ab9e0ac681"
        ),
    }


def _inspect_value(
    image_id: str,
    labels: dict[str, str],
    *,
    layers: list[str] | None = None,
) -> dict[str, object]:
    return {
        "Id": image_id,
        "Os": "linux",
        "Architecture": "amd64",
        "Created": "2026-08-13T08:00:00Z",
        "RootFS": {"Layers": layers or ["sha256:" + "3" * 64]},
        "Config": {"Labels": labels},
    }


def test_committed_ark_contract_is_canonical_and_current() -> None:
    path = REPO_ROOT / "docker/phase2-ark-egress-proxy/responses-contract.json"
    observed = json.loads(path.read_text(encoding="utf-8"))
    assert observed == build_ark_responses_contract(REPO_ROOT)
    assert observed["policy"]["provider_id"] == "volcengine_ark"
    assert observed["response_format"]["type"] == "json_schema"
    assert observed["response_format"]["strict"] is True


def test_ark_image_inspect_allows_slow_docker_desktop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["timeout"] = kwargs["timeout"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"Id": PROXY_IMAGE_ID}]),
        )

    monkeypatch.setattr(ark_contract_builder.subprocess, "run", fake_run)

    assert ark_contract_builder._inspect_image("synthetic") == {"Id": PROXY_IMAGE_ID}
    assert observed["timeout"] == 60


def test_ark_runtime_builder_uses_read_only_staging_contexts() -> None:
    observed: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path) -> None:
        context = Path(command[-1])
        observed.append(command)
        assert cwd == REPO_ROOT
        assert context.is_dir()
        assert REPO_ROOT not in context.parents
        assert not any(path.is_symlink() for path in context.rglob("*"))
        assert all(
            path.stat().st_mode & 0o222 == 0
            for path in context.rglob("*")
            if path.is_file()
        )
        assert command[command.index("--network") + 1] == "none"
        assert "--pull=false" in command
        assert "--no-cache" in command

    ark_image_builder._build_runtime_images(
        REPO_ROOT,
        runtime_sha=(
            "3cb3064e4e68bb2e9a07a9153f9c2bcef8de127ac0fd779b2c41e1ab9e0ac681"
        ),
        runner=fake_run,
        image_inspector=lambda _reference: {"Id": BASE_IMAGE_REFERENCE.rsplit("@", 1)[1]},
    )

    assert len(observed) == 2
    assert "PROXY_DOCKERFILE_SHA256=" in " ".join(observed[0])
    assert "RELAY_DOCKERFILE_SHA256=" in " ".join(observed[1])


def test_ark_runtime_builder_requires_local_pinned_base_before_build() -> None:
    events: list[str] = []

    def missing_base(reference: str) -> dict[str, object]:
        events.append(f"inspect:{reference}")
        raise RuntimeError("ARK_OFFLINE_REBUILD_BLOCKED_BASE_IMAGE_MISSING")

    def unexpected_build(_command: list[str], *, cwd: Path) -> None:
        events.append(f"build:{cwd}")

    with pytest.raises(
        RuntimeError,
        match="ARK_OFFLINE_REBUILD_BLOCKED_BASE_IMAGE_MISSING",
    ):
        ark_image_builder._build_runtime_images(
            REPO_ROOT,
            runtime_sha=(
                "3cb3064e4e68bb2e9a07a9153f9c2bcef8de127ac0fd779b2c41e1ab9e0ac681"
            ),
            runner=unexpected_build,
            image_inspector=missing_base,
        )

    assert events == [f"inspect:{BASE_IMAGE_REFERENCE}"]


def test_ark_runtime_builder_rejects_symlink_in_source_context(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    (source / "linked").symlink_to(outside)

    with pytest.raises(RuntimeError, match="ark_build_context_invalid"):
        ark_image_builder._copy_readonly_context(source, tmp_path / "staging")


def test_ark_mock_runs_use_distinct_docker_name_prefixes() -> None:
    request_ids = [ark_mock_runner._mock_request_id(index) for index in range(1, 4)]

    assert all(len(request_id) == 32 for request_id in request_ids)
    assert all(set(request_id) <= set("0123456789abcdef") for request_id in request_ids)
    assert len({request_id[:12] for request_id in request_ids}) == 3


def test_ark_mock_global_lock_rejects_concurrent_invocation(tmp_path: Path) -> None:
    with ark_mock_runner._exclusive_mock_lock(tmp_path):
        with pytest.raises(ark_mock_runner.ArkMockE2EError, match="global_lock"):
            with ark_mock_runner._exclusive_mock_lock(tmp_path):
                pass


def test_ark_mock_global_lock_never_creates_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(ark_mock_runner.ArkMockE2EError, match="global_lock"):
        with ark_mock_runner._exclusive_mock_lock(missing):
            pass

    assert not missing.exists()


def test_committed_ark_mock_e2e_is_semantically_valid() -> None:
    observed = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_text()
    )

    validate_ark_mock_e2e_evidence(REPO_ROOT, observed)


def test_committed_ark_candidate_binds_all_required_hashes() -> None:
    path = REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
    observed = json.loads(path.read_text(encoding="utf-8"))
    assert observed == build_ark_approval_candidate(
        REPO_ROOT,
        proxy_image_id=observed["proxy_image_id"],
        relay_image_id=observed["relay_image_id"],
    )
    required = {
        "ark_responses_contract_sha256",
        "ark_proxy_policy_sha256",
        "ark_adapter_source_sha256",
        "ark_launcher_source_sha256",
        "orchestrator_source_sha256",
        "runtime_contract_sha256",
        "readiness_contract_sha256",
        "facts_sha256",
        "history_baseline_sha256",
        "projection_sha256",
        "typed_claims_schema_sha256",
        "timeout_mock_e2e_sha256",
        "mock_e2e_sha256",
        "build_provenance_sha256",
    }
    assert set(observed["artifact_hashes"]) == required
    assert observed["source_bindings"] == {
        "base_image_digest": (
            "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
        ),
        "proxy_dockerfile_sha256": hashlib.sha256(
            (REPO_ROOT / "docker/phase2-ark-egress-proxy/Dockerfile").read_bytes()
        ).hexdigest(),
        "proxy_source_sha256": hashlib.sha256(
            (REPO_ROOT / "docker/phase2-ark-egress-proxy/proxy.py").read_bytes()
        ).hexdigest(),
        "relay_dockerfile_sha256": hashlib.sha256(
            (REPO_ROOT / "docker/phase2-ark-canary-relay/Dockerfile").read_bytes()
        ).hexdigest(),
        "relay_source_sha256": hashlib.sha256(
            (REPO_ROOT / "docker/phase2-ark-canary-relay/relay.py").read_bytes()
        ).hexdigest(),
    }
    assert len(hashlib.sha256(path.read_bytes()).hexdigest()) == 64
    assert "approval_scope" not in observed


def test_committed_ark_build_provenance_is_canonical_and_current() -> None:
    path = REPO_ROOT / "reports/phase2_provider_ark/ark_build_provenance.json"
    observed = json.loads(path.read_text(encoding="utf-8"))

    validate_ark_build_provenance(REPO_ROOT, observed)

    candidate = json.loads(
        (
            REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
        ).read_text(encoding="utf-8")
    )
    assert candidate["proxy_image_id"] == observed["proxy_image_id"]
    assert candidate["relay_image_id"] == observed["relay_image_id"]
    assert candidate["artifact_hashes"]["build_provenance_sha256"] == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def test_ark_candidate_validator_rejects_any_field_tampering() -> None:
    observed = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json").read_text()
    )
    observed["provider_attempt_count"] = 1
    with pytest.raises(ValueError, match="ark_approval_candidate_invalid"):
        validate_ark_approval_candidate(REPO_ROOT, observed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ark_approval_candidate_schema_version", True),
        ("provider_attempt_count", False),
        ("ai_call_count", False),
        ("retry_count", False),
        ("maximum_provider_attempts", True),
    ],
)
def test_ark_candidate_validator_rejects_boolean_numeric_fields(
    field: str,
    value: bool,
) -> None:
    observed = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json").read_text()
    )
    observed[field] = value

    with pytest.raises(ValueError, match="ark_approval_candidate_invalid"):
        validate_ark_approval_candidate(REPO_ROOT, observed)


@pytest.mark.parametrize(
    "proxy_image_id,relay_image_id",
    [
        ("sha256:short", RELAY_IMAGE_ID),
        (PROXY_IMAGE_ID, "sha256:" + "g" * 64),
        ("not-sha256:" + "1" * 64, RELAY_IMAGE_ID),
    ],
)
def test_ark_image_set_rejects_malformed_image_ids(
    proxy_image_id: str,
    relay_image_id: str,
) -> None:
    with pytest.raises(ValueError, match="ark_image_set_invalid"):
        validate_ark_image_set(
            repo_root=REPO_ROOT,
            proxy_image_id=proxy_image_id,
            relay_image_id=relay_image_id,
            proxy_labels=_expected_labels("proxy"),
            relay_labels=_expected_labels("relay"),
        )


@pytest.mark.parametrize(
    "role,label",
    [
        ("proxy", "org.tickflow.phase2.proxy-dockerfile-sha256"),
        ("relay", "org.tickflow.phase2.relay-dockerfile-sha256"),
        ("proxy", "org.tickflow.phase2.base-image-digest"),
        ("relay", "org.tickflow.phase2.base-image-digest"),
    ],
)
def test_ark_image_set_requires_complete_source_labels(role: str, label: str) -> None:
    proxy_labels = _expected_labels("proxy")
    relay_labels = _expected_labels("relay")
    (proxy_labels if role == "proxy" else relay_labels).pop(label)
    with pytest.raises(ValueError, match="ark_image_set_invalid"):
        validate_ark_image_set(
            repo_root=REPO_ROOT,
            proxy_image_id=PROXY_IMAGE_ID,
            relay_image_id=RELAY_IMAGE_ID,
            proxy_labels=proxy_labels,
            relay_labels=relay_labels,
        )


def test_ark_image_set_rejects_unexpected_labels() -> None:
    proxy_labels = _expected_labels("proxy")
    proxy_labels["unexpected"] = "not-allowed"
    with pytest.raises(ValueError, match="ark_image_set_invalid"):
        validate_ark_image_set(
            repo_root=REPO_ROOT,
            proxy_image_id=PROXY_IMAGE_ID,
            relay_image_id=RELAY_IMAGE_ID,
            proxy_labels=proxy_labels,
            relay_labels=_expected_labels("relay"),
        )


def test_ark_build_provenance_is_self_verifying_and_source_bound() -> None:
    base_layers = ["sha256:" + "a" * 64, "sha256:" + "b" * 64]
    base_inspect = _inspect_value(
        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5",
        {},
        layers=base_layers,
    )
    value = build_ark_build_provenance(
        REPO_ROOT,
        base_inspect=base_inspect,
        proxy_inspect=_inspect_value(
            PROXY_IMAGE_ID,
            _expected_labels("proxy"),
            layers=[*base_layers, "sha256:" + "c" * 64],
        ),
        relay_inspect=_inspect_value(
            RELAY_IMAGE_ID,
            _expected_labels("relay"),
            layers=[*base_layers, "sha256:" + "d" * 64],
        ),
        captured_at="2026-08-13T08:00:00Z",
        build_path="FRESH_OFFLINE_REBUILD",
    )

    validate_ark_build_provenance(REPO_ROOT, value)

    assert value["proxy_image_id"] == PROXY_IMAGE_ID
    assert value["relay_image_id"] == RELAY_IMAGE_ID
    assert value["docker_access_mode"] == "read_only_inspect"
    assert value["provider_attempt_count"] == 0
    assert value["ai_call_count"] == 0
    assert value["provider_http"] == "NOT_RUN"
    assert value["proxy_labels"] == _expected_labels("proxy")
    assert value["relay_labels"] == _expected_labels("relay")
    assert value["base_image_id"] == (
        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
    )
    assert value["proxy_base_rootfs_prefix_verified"] is True
    assert value["relay_base_rootfs_prefix_verified"] is True
    assert len(value["evidence_sha256"]) == 64

    tampered = dict(value)
    tampered["proxy_source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="ark_build_provenance_invalid"):
        validate_ark_build_provenance(REPO_ROOT, tampered)


def test_ark_build_provenance_rejects_image_without_pinned_base_prefix() -> None:
    base_inspect = _inspect_value(
        "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5",
        {},
        layers=["sha256:" + "a" * 64],
    )
    with pytest.raises(ValueError, match="ark_build_provenance_invalid"):
        build_ark_build_provenance(
            REPO_ROOT,
            base_inspect=base_inspect,
            proxy_inspect=_inspect_value(
                PROXY_IMAGE_ID,
                _expected_labels("proxy"),
                layers=["sha256:" + "f" * 64],
            ),
            relay_inspect=_inspect_value(
                RELAY_IMAGE_ID,
                _expected_labels("relay"),
                layers=["sha256:" + "a" * 64, "sha256:" + "d" * 64],
            ),
            captured_at="2026-08-13T08:00:00Z",
            build_path="FRESH_OFFLINE_REBUILD",
        )


def test_ark_build_provenance_rejects_unexpected_image_label() -> None:
    base_layers = ["sha256:" + "a" * 64]
    proxy_labels = _expected_labels("proxy")
    proxy_labels["unexpected"] = "not-allowed"
    with pytest.raises(ValueError, match="ark_build_provenance_invalid"):
        build_ark_build_provenance(
            REPO_ROOT,
            base_inspect=_inspect_value(
                "sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5",
                {},
                layers=base_layers,
            ),
            proxy_inspect=_inspect_value(
                PROXY_IMAGE_ID,
                proxy_labels,
                layers=[*base_layers, "sha256:" + "c" * 64],
            ),
            relay_inspect=_inspect_value(
                RELAY_IMAGE_ID,
                _expected_labels("relay"),
                layers=[*base_layers, "sha256:" + "d" * 64],
            ),
            captured_at="2026-08-13T08:00:00Z",
            build_path="FRESH_OFFLINE_REBUILD",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("captured_at", None),
        ("captured_at", "2026-08-13T08:00:00+08:00"),
        ("proxy_created", None),
        ("proxy_created", "not-a-time"),
        ("relay_created", None),
        ("relay_created", "2026-08-13"),
    ],
)
def test_ark_build_provenance_rejects_invalid_utc_timestamps(
    field: str,
    value: object,
) -> None:
    observed = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/ark_build_provenance.json").read_text()
    )
    observed[field] = value
    payload = dict(observed)
    payload.pop("evidence_sha256")
    observed["evidence_sha256"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    with pytest.raises(ValueError, match="ark_build_provenance_invalid"):
        validate_ark_build_provenance(REPO_ROOT, observed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_schema_version", True),
        ("provider_attempt_count", False),
        ("ai_call_count", False),
    ],
)
def test_ark_build_provenance_rejects_boolean_numeric_fields(
    field: str,
    value: bool,
) -> None:
    observed = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/ark_build_provenance.json").read_text()
    )
    observed[field] = value
    payload = dict(observed)
    payload.pop("evidence_sha256")
    observed["evidence_sha256"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    with pytest.raises(ValueError, match="ark_build_provenance_invalid"):
        validate_ark_build_provenance(REPO_ROOT, observed)


def test_committed_ark_scope_binds_final_candidate_bytes() -> None:
    candidate_path = REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json"
    scope_path = REPO_ROOT / "reports/phase2_provider_ark/approval_scope_preflight.json"
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    assert (
        scope["approval_candidate_sha256"]
        == hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    )
    assert scope["provider_id"] == "volcengine_ark"
    assert scope["exact_model_id"] == "doubao-seed-2-1-turbo-260628"
    assert scope["endpoint_alias"] == "ark_responses_cn_beijing_v1"
    assert scope["historical_attempts"] == 0
    assert scope["attempt_availability"] == "AVAILABLE"
    assert scope["approval_installed"] is False


def test_ark_proxy_derivation_check_is_cwd_independent() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend/scripts/prepare_phase2_ark_proxy_source.py"),
        ],
        cwd=REPO_ROOT / "backend",
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_committed_candidate_uses_the_mock_validated_relay_image() -> None:
    candidate = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json").read_text()
    )
    mock = json.loads((REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_text())
    assert candidate["proxy_image_id"] == mock["proxy_image_id"]
    assert candidate["relay_image_id"] == mock["relay_image_id"]
    assert (
        mock["orchestrator_source_sha256"]
        == candidate["artifact_hashes"]["orchestrator_source_sha256"]
    )
    assert (
        mock["runtime_contract_sha256"] == candidate["artifact_hashes"]["runtime_contract_sha256"]
    )
    assert mock["facts_sha256"] == candidate["artifact_hashes"]["facts_sha256"]
    assert mock["projection_sha256"] == candidate["artifact_hashes"]["projection_sha256"]
    scope = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/approval_scope_preflight.json").read_text()
    )
    assert (
        scope["approval_candidate_sha256"]
        == hashlib.sha256(
            (REPO_ROOT / "reports/phase2_provider_ark/approval_candidate.json").read_bytes()
        ).hexdigest()
    )
    assert (
        scope["mock_e2e_sha256"]
        == hashlib.sha256(
            (REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_bytes()
        ).hexdigest()
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("status",), "FAILED"),
        (("completed_run_count",), 2),
        (("real_provider_attempt_count",), 1),
        (("real_ai_call_count",), 1),
        (("real_public_network_success_count",), 1),
        (("proxy_image_id",), "sha256:" + "f" * 64),
        (("runs", 0, "candidate_valid"), False),
        (("runs", 0, "claims_valid"), False),
        (("runs", 0, "renderer_deterministic"), False),
        (("runs", 0, "container_residue_count"), 1),
        (("runs", 0, "retry_count"), 1),
        (("runs", 0, "proxy_receipt_identity"), {}),
    ],
)
def test_ark_mock_e2e_validator_rejects_semantic_tampering(
    path: tuple[object, ...],
    value: object,
) -> None:
    observed = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_text()
    )
    target = observed
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError, match="ark_mock_e2e_invalid"):
        validate_ark_mock_e2e_evidence(REPO_ROOT, observed)


def test_ark_mock_e2e_validator_requires_identical_renderer_hashes() -> None:
    observed = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_text()
    )
    for index, run in enumerate(observed["runs"], start=1):
        run["renderer_sha256"] = f"{index:064x}"

    with pytest.raises(ValueError, match="ark_mock_e2e_invalid"):
        validate_ark_mock_e2e_evidence(REPO_ROOT, observed)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("ark_mock_e2e_schema_version",), True),
        (("requested_run_count",), True),
        (("retry_count",), False),
        (("real_ai_call_count",), False),
        (("runs", 0, "run"), True),
        (("runs", 0, "provider_attempt_count"), True),
        (("runs", 0, "retry_count"), False),
        (("runs", 0, "provider_http_status"), True),
        (("runs", 0, "temporary_residue_count"), False),
    ],
)
def test_ark_mock_e2e_validator_rejects_boolean_numeric_fields(
    path: tuple[object, ...],
    value: bool,
) -> None:
    observed = json.loads(
        (REPO_ROOT / "reports/phase2_provider_ark/mock_e2e.json").read_text()
    )
    target = observed
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError, match="ark_mock_e2e_invalid"):
        validate_ark_mock_e2e_evidence(REPO_ROOT, observed)
