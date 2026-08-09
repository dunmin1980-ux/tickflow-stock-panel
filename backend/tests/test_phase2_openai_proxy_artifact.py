from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import scripts.build_phase2_openai_proxy_offline as artifact_cli
from app.services.phase2_openai_proxy_artifact import (
    BASE_IMAGE_DIGEST,
    BASE_IMAGE_REFERENCE,
    IMAGE_NAME,
    ArtifactInputHashes,
    FreshBuildProvenance,
    OpenAIProxyArtifactError,
    build_artifact_candidate,
    build_offline_image_command,
    compute_artifact_input_hashes,
    inspect_openai_proxy_image,
    validate_fresh_build_provenance,
    validate_local_base_image,
    verify_image_contents,
    write_artifact_reports,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTEXT = REPO_ROOT / "docker/phase2-openai-egress-proxy"
DOCKERFILE = CONTEXT / "Dockerfile"
PROXY_SOURCE = CONTEXT / "proxy.py"
CONTRACT = CONTEXT / "responses-contract.json"
RUNTIME_CONTRACT = CONTEXT / "runtime-contract.json"
READINESS_CONTRACT = CONTEXT / "readiness-contract.json"
LAUNCHER = REPO_ROOT / "backend/app/services/phase2_openai_canary_runner.py"
EXPECTED_ENVIRONMENT = [
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
    "LANG=C.UTF-8",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
]
BASE_ROOTFS_LAYERS = tuple("sha256:" + str(index % 10) * 64 for index in range(43))
ADDED_ROOTFS_LAYERS = tuple(
    "sha256:" + character * 64
    for character in ("a", "b", "c", "d", "e", "f", "0")
)


@pytest.fixture
def hashes() -> ArtifactInputHashes:
    return compute_artifact_input_hashes(REPO_ROOT)


def test_dockerfile_is_digest_pinned_shellless_and_exact() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    significant = [
        line.strip()
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert significant[0] == f"FROM {BASE_IMAGE_REFERENCE}"
    assert "USER 65532:65532" in significant
    assert "WORKDIR /proxy" in significant
    assert 'ENTRYPOINT ["/usr/bin/python3", "/proxy/proxy.py"]' in significant
    assert len([line for line in significant if line.startswith("COPY ")]) == 6
    assert all(
        line.startswith("COPY --chown=65532:65532 ")
        for line in significant
        if line.startswith("COPY ")
    )
    assert "COPY ." not in source
    for name in (
        "proxy.py",
        "responses-contract.json",
        "runtime-contract.json",
        "readiness-contract.json",
        "secret.placeholder",
        "receipt.placeholder.json",
    ):
        assert any(name in line for line in significant if line.startswith("COPY "))
    for label in (
        "org.tickflow.phase2.base-image-digest",
        "org.tickflow.phase2.proxy-source-sha256",
        "org.tickflow.phase2.responses-contract-sha256",
        "org.tickflow.phase2.proxy-policy-sha256",
        "org.tickflow.phase2.runtime-contract-sha256",
        "org.tickflow.phase2.readiness-contract-sha256",
    ):
        assert label in source
    for environment in EXPECTED_ENVIRONMENT:
        assert environment in source
    for forbidden in (
        ":latest",
        "RUN ",
        "ADD ",
        "apt-get",
        "apk ",
        "pip ",
        "curl ",
        "wget ",
        "http://",
        "https://",
        "OPENAI_API_KEY",
        "Authorization",
        "TOKEN",
        "PASSWORD",
        "SECRET_VALUE",
        "/bin/sh",
    ):
        assert forbidden not in source


def test_dedicated_build_context_is_exactly_seven_regular_files() -> None:
    assert {path.name for path in CONTEXT.iterdir() if path.is_file()} == {
        "Dockerfile",
        "proxy.py",
        "responses-contract.json",
        "runtime-contract.json",
        "readiness-contract.json",
        "secret.placeholder",
        "receipt.placeholder.json",
    }
    assert all(not path.is_symlink() for path in CONTEXT.iterdir())


def test_build_context_rejects_even_an_extra_empty_directory(tmp_path: Path) -> None:
    context = tmp_path / "docker/phase2-openai-egress-proxy"
    context.mkdir(parents=True)
    for source in CONTEXT.iterdir():
        if source.is_file():
            shutil.copy2(source, context / source.name)
    launcher = tmp_path / "backend/app/services/phase2_openai_canary_runner.py"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(LAUNCHER, launcher)
    (context / "unexpected-empty-directory").mkdir()

    with pytest.raises(OpenAIProxyArtifactError, match="artifact_build_context_invalid"):
        compute_artifact_input_hashes(tmp_path)


def test_build_command_is_offline_narrow_and_contains_only_nonsensitive_hashes(
    hashes: ArtifactInputHashes,
    tmp_path: Path,
) -> None:
    iidfile = tmp_path / "image.iid"
    command = build_offline_image_command(REPO_ROOT, hashes, iidfile=iidfile)

    assert command[:3] == ["docker", "build", "--pull=false"]
    assert "--network=none" in command
    assert "--no-cache" in command
    assert command[command.index("--iidfile") + 1] == str(iidfile)
    assert "SOURCE_DATE_EPOCH=0" in command
    assert command[-1] == str(CONTEXT)
    assert "--secret" not in command
    assert "--ssh" not in command
    assert "--pull" not in command
    assert "--network=host" not in command
    assert command[command.index("--tag") + 1] == IMAGE_NAME
    joined = " ".join(command)
    assert str(REPO_ROOT) in joined
    for digest in (
        BASE_IMAGE_DIGEST,
        hashes.proxy_source_sha256,
        hashes.responses_contract_sha256,
        hashes.proxy_policy_sha256,
        hashes.runtime_contract_sha256,
        hashes.readiness_contract_sha256,
    ):
        assert digest in joined
    for forbidden in (
        "provider-auth",
        "api_key",
        "Authorization",
        "Bearer ",
        "Cookie",
    ):
        assert forbidden not in joined


def test_build_command_rejects_one_byte_hash_drift(
    hashes: ArtifactInputHashes,
    tmp_path: Path,
) -> None:
    for field in ArtifactInputHashes.model_fields:
        value = hashes.model_dump()
        value[field] = ("0" if value[field][0] != "0" else "1") + value[field][1:]
        drifted = ArtifactInputHashes.model_validate(value)
        with pytest.raises(OpenAIProxyArtifactError):
            build_offline_image_command(
                REPO_ROOT,
                drifted,
                iidfile=tmp_path / "image.iid",
            )


def _base_inspect() -> list[dict[str, Any]]:
    return [
        {
            "Id": BASE_IMAGE_DIGEST,
            "RepoDigests": [BASE_IMAGE_REFERENCE],
            "RootFS": {"Type": "layers", "Layers": list(BASE_ROOTFS_LAYERS)},
        }
    ]


def test_local_base_check_requires_exact_repo_digest() -> None:
    evidence = validate_local_base_image(_base_inspect())
    assert evidence.base_available is True
    assert evidence.base_reference == BASE_IMAGE_REFERENCE
    assert evidence.local_config_id == BASE_IMAGE_DIGEST
    assert evidence.rootfs_layers == BASE_ROOTFS_LAYERS

    for payload in (
        [],
        [{}],
        [{"Id": BASE_IMAGE_DIGEST, "RepoDigests": [], "RootFS": {"Layers": []}}],
        [
            {
                "Id": BASE_IMAGE_DIGEST,
                "RepoDigests": ["gcr.io/distroless/python3-debian12@sha256:" + "0" * 64],
                "RootFS": {"Layers": list(BASE_ROOTFS_LAYERS)},
            }
        ],
        [
            {
                "Id": "sha256:" + "b" * 64,
                "RepoDigests": [BASE_IMAGE_REFERENCE],
                "RootFS": {"Layers": list(BASE_ROOTFS_LAYERS)},
            }
        ],
    ):
        with pytest.raises(OpenAIProxyArtifactError):
            validate_local_base_image(payload)


def _image_inspect(hashes: ArtifactInputHashes) -> list[dict[str, Any]]:
    return [
        {
            "Id": "sha256:" + "a" * 64,
            "RepoTags": [IMAGE_NAME],
            "RootFS": {
                "Type": "layers",
                "Layers": [*BASE_ROOTFS_LAYERS, *ADDED_ROOTFS_LAYERS],
            },
            "Config": {
                "User": "65532:65532",
                "Entrypoint": ["/usr/bin/python3", "/proxy/proxy.py"],
                "Cmd": None,
                "Env": list(EXPECTED_ENVIRONMENT),
                "Labels": {
                    "org.tickflow.phase2.base-image-digest": BASE_IMAGE_DIGEST,
                    "org.tickflow.phase2.proxy-source-sha256": hashes.proxy_source_sha256,
                    "org.tickflow.phase2.responses-contract-sha256": (
                        hashes.responses_contract_sha256
                    ),
                    "org.tickflow.phase2.proxy-policy-sha256": (
                        hashes.proxy_policy_sha256
                    ),
                    "org.tickflow.phase2.runtime-contract-sha256": (
                        hashes.runtime_contract_sha256
                    ),
                    "org.tickflow.phase2.readiness-contract-sha256": (
                        hashes.readiness_contract_sha256
                    ),
                },
            },
        }
    ]


def _clean_history() -> list[dict[str, Any]]:
    return [
        {"CreatedBy": "ENTRYPOINT [\"/usr/bin/python3\" \"/proxy/proxy.py\"]"},
        {"CreatedBy": "COPY proxy.py /proxy/proxy.py # buildkit"},
        {"CreatedBy": "COPY responses-contract.json /proxy/responses-contract.json"},
    ]


def _base_evidence():
    return validate_local_base_image(_base_inspect())


def _provenance(
    hashes: ArtifactInputHashes,
    *,
    image_id: str = "sha256:" + "a" * 64,
) -> FreshBuildProvenance:
    image = inspect_openai_proxy_image(
        _image_inspect(hashes),
        _clean_history(),
        hashes,
        _base_evidence(),
        expected_image_id=image_id,
    )
    return validate_fresh_build_provenance(
        hashes_before=hashes,
        hashes_after=hashes,
        iidfile_image_id=image_id,
        image=image,
        base=_base_evidence(),
    )


def test_image_inspection_requires_exact_identity_labels_and_clean_history(
    hashes: ArtifactInputHashes,
) -> None:
    evidence = inspect_openai_proxy_image(
        _image_inspect(hashes),
        _clean_history(),
        hashes,
        _base_evidence(),
        expected_image_id="sha256:" + "a" * 64,
    )

    assert evidence.image_valid is True
    assert evidence.history_clean is True
    assert evidence.image_id == "sha256:" + "a" * 64
    assert evidence.image_name == IMAGE_NAME
    assert evidence.runtime_user == "65532:65532"
    assert evidence.entrypoint == ("/usr/bin/python3", "/proxy/proxy.py")
    assert evidence.base_rootfs_prefix_verified is True
    assert evidence.rootfs_layer_count == 50


@pytest.mark.parametrize(
    "case",
    [
        "image_id",
        "tag",
        "user",
        "entrypoint",
        "command",
        "environment",
        "base_label",
        "source_label",
        "contract_label",
        "policy_label",
        "runtime_contract_label",
        "readiness_contract_label",
        "extra_label",
        "base_layer_prefix",
        "extra_layer_count",
        "iidfile_mismatch",
        "sensitive_history",
    ],
)
def test_image_inspection_rejects_every_identity_or_layer_mutation(
    case: str,
    hashes: ArtifactInputHashes,
) -> None:
    payload = _image_inspect(hashes)
    history = _clean_history()
    config = payload[0]["Config"]
    labels = config["Labels"]
    if case == "image_id":
        payload[0]["Id"] = "not-a-digest"
    elif case == "tag":
        payload[0]["RepoTags"] = ["other:latest"]
    elif case == "user":
        config["User"] = "0:0"
    elif case == "entrypoint":
        config["Entrypoint"] = ["/bin/sh"]
    elif case == "command":
        config["Cmd"] = ["--override"]
    elif case == "environment":
        config["Env"].append("OPENAI_API_KEY=synthetic")
    elif case == "base_label":
        labels["org.tickflow.phase2.base-image-digest"] = "sha256:" + "0" * 64
    elif case == "source_label":
        labels["org.tickflow.phase2.proxy-source-sha256"] = "0" * 64
    elif case == "contract_label":
        labels["org.tickflow.phase2.responses-contract-sha256"] = "0" * 64
    elif case == "policy_label":
        labels["org.tickflow.phase2.proxy-policy-sha256"] = "0" * 64
    elif case == "runtime_contract_label":
        labels["org.tickflow.phase2.runtime-contract-sha256"] = "0" * 64
    elif case == "readiness_contract_label":
        labels["org.tickflow.phase2.readiness-contract-sha256"] = "0" * 64
    elif case == "extra_label":
        labels["unapproved"] = "value"
    elif case == "base_layer_prefix":
        payload[0]["RootFS"]["Layers"][0] = "sha256:" + "f" * 64
    elif case == "extra_layer_count":
        payload[0]["RootFS"]["Layers"].append("sha256:" + "f" * 64)
    elif case == "iidfile_mismatch":
        payload[0]["Id"] = "sha256:" + "f" * 64
    elif case == "sensitive_history":
        history.append({"CreatedBy": "OPENAI_API_KEY=synthetic"})
    else:  # pragma: no cover - parameter list is closed above
        raise AssertionError(case)

    with pytest.raises(OpenAIProxyArtifactError):
        inspect_openai_proxy_image(
            payload,
            history,
            hashes,
            _base_evidence(),
            expected_image_id="sha256:" + "a" * 64,
        )


def test_fresh_build_provenance_binds_iid_inputs_and_base_layers(
    hashes: ArtifactInputHashes,
) -> None:
    image = inspect_openai_proxy_image(
        _image_inspect(hashes),
        _clean_history(),
        hashes,
        _base_evidence(),
        expected_image_id="sha256:" + "a" * 64,
    )

    evidence = validate_fresh_build_provenance(
        hashes_before=hashes,
        hashes_after=hashes,
        iidfile_image_id=image.image_id,
        image=image,
        base=_base_evidence(),
    )

    assert evidence.fresh_offline_build is True
    assert evidence.no_cache is True
    assert evidence.iidfile_verified is True
    assert evidence.input_hashes_stable is True
    assert evidence.base_rootfs_prefix_verified is True
    assert evidence.image_id == image.image_id
    assert evidence.base_image_id == BASE_IMAGE_DIGEST

    with pytest.raises(OpenAIProxyArtifactError):
        validate_fresh_build_provenance(
            hashes_before=hashes,
            hashes_after=hashes.model_copy(
                update={"launcher_source_sha256": "0" * 64}
            ),
            iidfile_image_id=image.image_id,
            image=image,
            base=_base_evidence(),
        )
    with pytest.raises(OpenAIProxyArtifactError):
        validate_fresh_build_provenance(
            hashes_before=hashes,
            hashes_after=hashes,
            iidfile_image_id="sha256:" + "f" * 64,
            image=image,
            base=_base_evidence(),
        )


class _ExecutorDouble:
    def __init__(
        self,
        *,
        source_drift: bool = False,
        contract_drift: bool = False,
        runtime_contract_drift: bool = False,
        readiness_contract_drift: bool = False,
        fail_cp: bool = False,
        fail_cleanup: bool = False,
    ) -> None:
        self.source_drift = source_drift
        self.contract_drift = contract_drift
        self.runtime_contract_drift = runtime_contract_drift
        self.readiness_contract_drift = readiness_contract_drift
        self.fail_cp = fail_cp
        self.fail_cleanup = fail_cleanup
        self.calls: list[list[str]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        shell: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        values = list(command)
        self.calls.append(values)
        assert capture_output is True
        assert text is True
        assert shell is False
        assert 0 < timeout <= 60
        if values[:2] == ["docker", "create"]:
            assert "--network" in values
            assert values[values.index("--network") + 1] == "none"
            return subprocess.CompletedProcess(values, 0, "container-id\n", "")
        if values[:2] == ["docker", "cp"]:
            if self.fail_cp:
                raise subprocess.CalledProcessError(1, values, stderr="synthetic")
            destination = Path(values[-1])
            if values[-2].endswith(":/proxy/proxy.py"):
                raw = PROXY_SOURCE.read_bytes()
                if self.source_drift:
                    raw += b"\n"
            elif values[-2].endswith(":/proxy/responses-contract.json"):
                raw = CONTRACT.read_bytes()
                if self.contract_drift:
                    raw += b"\n"
            elif values[-2].endswith(":/proxy/readiness-contract.json"):
                raw = READINESS_CONTRACT.read_bytes()
                if self.readiness_contract_drift:
                    raw += b"\n"
            else:
                raw = RUNTIME_CONTRACT.read_bytes()
                if self.runtime_contract_drift:
                    raw += b"\n"
            destination.write_bytes(raw)
            return subprocess.CompletedProcess(values, 0, "", "")
        if values[:3] == ["docker", "rm", "-f"]:
            if self.fail_cleanup:
                return subprocess.CompletedProcess(values, 1, "", "synthetic")
            return subprocess.CompletedProcess(values, 0, "", "")
        raise AssertionError(values)


def test_image_contents_are_copied_from_never_started_container_and_cleaned(
    hashes: ArtifactInputHashes,
) -> None:
    executor = _ExecutorDouble()

    evidence = verify_image_contents(
        REPO_ROOT,
        "sha256:" + "a" * 64,
        executor=executor,
    )

    assert evidence.content_valid is True
    assert evidence.cleanup_complete is True
    assert evidence.proxy_source_sha256 == hashes.proxy_source_sha256
    assert evidence.responses_contract_sha256 == hashes.responses_contract_sha256
    assert evidence.runtime_contract_sha256 == hashes.runtime_contract_sha256
    assert evidence.readiness_contract_sha256 == hashes.readiness_contract_sha256
    assert len(executor.calls) == 6
    assert all("start" not in command for command in executor.calls)
    assert executor.calls[-1][:3] == ["docker", "rm", "-f"]


@pytest.mark.parametrize(
    "case",
    [
        "source_drift",
        "contract_drift",
        "runtime_contract_drift",
        "readiness_contract_drift",
        "cp_failure",
    ],
)
def test_image_content_failure_always_removes_container(
    case: str,
) -> None:
    executor = _ExecutorDouble(
        source_drift=case == "source_drift",
        contract_drift=case == "contract_drift",
        runtime_contract_drift=case == "runtime_contract_drift",
        readiness_contract_drift=case == "readiness_contract_drift",
        fail_cp=case == "cp_failure",
    )

    with pytest.raises(OpenAIProxyArtifactError):
        verify_image_contents(
            REPO_ROOT,
            "sha256:" + "a" * 64,
            executor=executor,
        )

    assert executor.calls[-1][:3] == ["docker", "rm", "-f"]


def test_image_content_cleanup_failure_blocks_success() -> None:
    executor = _ExecutorDouble(fail_cleanup=True)

    with pytest.raises(OpenAIProxyArtifactError, match="artifact_cleanup_failed"):
        verify_image_contents(
            REPO_ROOT,
            "sha256:" + "a" * 64,
            executor=executor,
        )


def test_artifact_candidate_is_exact_path_free_and_hash_bound(
    hashes: ArtifactInputHashes,
) -> None:
    image = inspect_openai_proxy_image(
        _image_inspect(hashes),
        _clean_history(),
        hashes,
        _base_evidence(),
        expected_image_id="sha256:" + "a" * 64,
    )
    contents = verify_image_contents(
        REPO_ROOT,
        image.image_id,
        executor=_ExecutorDouble(),
    )

    candidate = build_artifact_candidate(
        REPO_ROOT,
        image,
        contents,
        _provenance(hashes),
    )
    value = candidate.model_dump(mode="json")

    assert set(value) == {
        "approval_schema_version",
        "image_name",
        "image_id",
        "base_image_digest",
        "proxy_source_sha256",
        "dockerfile_sha256",
        "responses_contract_sha256",
        "proxy_policy_sha256",
        "launcher_source_sha256",
        "entrypoint",
        "runtime_user",
    }
    assert value["approval_schema_version"] == 1
    assert value["image_name"] == IMAGE_NAME
    assert value["image_id"] == image.image_id
    assert value["base_image_digest"] == BASE_IMAGE_DIGEST
    assert value["proxy_source_sha256"] == hashes.proxy_source_sha256
    assert value["dockerfile_sha256"] == hashes.dockerfile_sha256
    assert value["responses_contract_sha256"] == hashes.responses_contract_sha256
    assert value["proxy_policy_sha256"] == hashes.proxy_policy_sha256
    assert value["launcher_source_sha256"] == hashes.launcher_source_sha256
    assert value["entrypoint"] == ["/usr/bin/python3", "/proxy/proxy.py"]
    serialized = json.dumps(value, sort_keys=True)
    assert str(REPO_ROOT) not in serialized
    assert str(Path.home()) not in serialized


def test_artifact_candidate_rejects_current_source_or_content_drift(
    hashes: ArtifactInputHashes,
) -> None:
    image = inspect_openai_proxy_image(
        _image_inspect(hashes),
        _clean_history(),
        hashes,
        _base_evidence(),
        expected_image_id="sha256:" + "a" * 64,
    )
    contents = verify_image_contents(
        REPO_ROOT,
        image.image_id,
        executor=_ExecutorDouble(),
    )
    for field in (
        "proxy_source_sha256",
        "responses_contract_sha256",
        "runtime_contract_sha256",
        "readiness_contract_sha256",
    ):
        drifted = contents.model_copy(
            update={field: "0" * 64},
        )
        with pytest.raises(OpenAIProxyArtifactError):
            build_artifact_candidate(
                REPO_ROOT,
                image,
                drifted,
                _provenance(hashes),
            )

    drifted_provenance = _provenance(hashes).model_copy(
        update={"image_id": "sha256:" + "f" * 64}
    )
    with pytest.raises(OpenAIProxyArtifactError):
        build_artifact_candidate(
            REPO_ROOT,
            image,
            contents,
            drifted_provenance,
        )


def test_artifact_reports_are_canonical_atomic_and_path_free(
    hashes: ArtifactInputHashes,
    tmp_path: Path,
) -> None:
    image = inspect_openai_proxy_image(
        _image_inspect(hashes),
        _clean_history(),
        hashes,
        _base_evidence(),
        expected_image_id="sha256:" + "a" * 64,
    )
    contents = verify_image_contents(
        REPO_ROOT,
        image.image_id,
        executor=_ExecutorDouble(),
    )
    provenance = _provenance(hashes)
    candidate = build_artifact_candidate(
        REPO_ROOT,
        image,
        contents,
        provenance,
    )

    write_artifact_reports(
        tmp_path,
        candidate,
        image,
        contents,
        provenance,
    )

    report_dir = tmp_path / "reports/phase2_openai_proxy_artifact"
    candidate_path = report_dir / "approval_candidate.json"
    evidence_path = report_dir / "build_evidence.json"
    assert candidate_path.is_file() and not candidate_path.is_symlink()
    assert evidence_path.is_file() and not evidence_path.is_symlink()
    assert candidate_path.read_bytes().endswith(b"\n")
    evidence = json.loads(evidence_path.read_bytes())
    assert evidence["status"] == "PHASE2B_PROXY_ARTIFACT_READY_FOR_REVIEW"
    assert evidence["provider_attempt_count"] == 0
    assert evidence["ai_call_count"] == 0
    assert evidence["tickflow_request_count"] == 0
    assert evidence["public_network_request_count"] == 0
    assert evidence["fresh_offline_build"] is True
    assert evidence["no_cache"] is True
    assert evidence["iidfile_verified"] is True
    assert evidence["input_hashes_stable"] is True
    assert evidence["base_rootfs_prefix_verified"] is True
    serialized = candidate_path.read_text() + evidence_path.read_text()
    assert str(REPO_ROOT) not in serialized
    assert str(Path.home()) not in serialized
    assert not any(path.name.endswith((".tmp", ".partial")) for path in report_dir.iterdir())


def test_verify_mode_rechecks_immutable_attestation_without_rebuilding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(artifact_cli, "_base_evidence", lambda: object())
    monkeypatch.setattr(
        artifact_cli,
        "_verify_attested_artifact",
        lambda _root, *, base: observed.append("verified"),
    )
    monkeypatch.setattr(
        artifact_cli,
        "_fresh_offline_build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verify must not rebuild or rotate the approved IID")
        ),
    )

    assert artifact_cli.main(["--verify"]) == 0
    assert observed == ["verified"]


def test_attest_mode_is_the_only_report_writing_fresh_build_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = type("ImageEvidence", (), {"image_id": "sha256:" + "a" * 64})()
    provenance = object()
    contents = object()
    candidate = object()
    writes: list[tuple[object, ...]] = []
    monkeypatch.setattr(artifact_cli, "_base_evidence", lambda: object())
    monkeypatch.setattr(
        artifact_cli,
        "_fresh_offline_build",
        lambda _root, *, base: (image, provenance),
    )
    monkeypatch.setattr(
        artifact_cli,
        "verify_image_contents",
        lambda *_args, **_kwargs: contents,
    )
    monkeypatch.setattr(
        artifact_cli,
        "build_artifact_candidate",
        lambda *_args: candidate,
    )
    monkeypatch.setattr(
        artifact_cli,
        "write_artifact_reports",
        lambda *args: writes.append(args),
    )

    assert artifact_cli.main(["--attest"]) == 0
    assert len(writes) == 1
    assert writes[0][1:] == (candidate, image, contents, provenance)


def test_unattested_build_mode_is_not_exposed() -> None:
    with pytest.raises(SystemExit):
        artifact_cli.main(["--build"])
