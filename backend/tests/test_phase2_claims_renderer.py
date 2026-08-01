from __future__ import annotations

import ast
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import app.services.phase2_claims_delivery as claims_delivery
from app.services.phase2_claims_delivery import (
    Phase2ClaimsDeliveryError,
    publish_claims_bundle,
    publish_invalid_claims_receipt,
    route_claims_preview,
    validate_claims_bundle,
)
from app.services.phase2_claims_renderer import (
    FRONTMATTER_LINES,
    SECTION_HEADINGS,
    Phase2ClaimsRenderError,
    render_claims_document,
    validate_rendered_document,
)
from app.services.phase2_claims_service import (
    CLAIMS_VALID,
    build_claims_document,
    validate_claims_document,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_FRONTMATTER = (
    "---\n"
    "type: stock-typed-claims-review\n"
    "source_system: tickflow-stock-panel\n"
    "facts_schema_version: 1\n"
    "claims_schema_version: 1\n"
    "data_scope: single-symbol\n"
    "market_scope: incomplete\n"
    "financial_scope: unavailable\n"
    "news_scope: unavailable\n"
    "verification_status: validated\n"
    "can_publish: false\n"
    "trading_advice: false\n"
    "rendering_mode: deterministic\n"
    "---\n"
)


def _validated_fixture(symbol: str = "000403.SZ"):
    document = build_claims_document(REPO_ROOT, symbol)
    validation = validate_claims_document(REPO_ROOT, document)
    assert validation.status == CLAIMS_VALID
    return document, validation


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_renderer_has_exact_safe_frontmatter_and_seven_sections() -> None:
    document, validation = _validated_fixture()

    rendered = render_claims_document(document, validation)

    assert rendered.startswith(EXPECTED_FRONTMATTER)
    assert tuple(EXPECTED_FRONTMATTER.strip().splitlines()) == FRONTMATTER_LINES
    assert tuple(
        line.removeprefix("## ")
        for line in rendered.splitlines()
        if line.startswith("## ")
    ) == SECTION_HEADINGS
    assert SECTION_HEADINGS == (
        "数据范围",
        "日线事实",
        "技术指标事实",
        "分钟数据质量",
        "数据限制",
        "供应商待确认",
        "免责声明",
    )
    assert rendered.count("\n## ") == 7
    assert "不构成投资建议" in rendered
    assert "原始价格口径" in rendered
    assert "前复权价格口径" in rendered


def test_renderer_is_byte_idempotent() -> None:
    document, validation = _validated_fixture("600489.SH")

    first = render_claims_document(document, validation)
    second = render_claims_document(document, validation)

    assert first == second
    assert hashlib.sha256(first.encode()).hexdigest() == hashlib.sha256(
        second.encode()
    ).hexdigest()


def test_renderer_refuses_invalid_validation() -> None:
    document, validation = _validated_fixture()
    invalid = replace(validation, status="CLAIMS_INVALID", errors=["test_invalid"])

    with pytest.raises(Phase2ClaimsRenderError, match="claims_not_valid"):
        render_claims_document(document, invalid)


def test_renderer_refuses_validation_from_another_claims_document() -> None:
    _, validation = _validated_fixture("000403.SZ")
    other_document, _ = _validated_fixture("600489.SH")

    with pytest.raises(
        Phase2ClaimsRenderError,
        match="claims_validation_binding_mismatch",
    ):
        render_claims_document(other_document, validation)


def test_rendered_validator_rejects_markdown_html_links_and_zero_width() -> None:
    document, validation = _validated_fixture()
    rendered = render_claims_document(document, validation)

    assert validate_rendered_document(document, validation, rendered) == []
    assert "rendered_document_not_deterministic" in validate_rendered_document(
        document,
        validation,
        rendered + "\n## 额外\n",
    )
    assert "rendered_document_contains_link" in validate_rendered_document(
        document,
        validation,
        rendered + "\n[x](https://example.com)\n",
    )
    assert "rendered_document_contains_html" in validate_rendered_document(
        document,
        validation,
        rendered + "\n<!-- hidden -->\n",
    )
    assert "rendered_document_contains_zero_width" in validate_rendered_document(
        document,
        validation,
        rendered + "\u200b",
    )


def test_renderer_module_has_no_filesystem_network_or_subprocess_imports() -> None:
    source_path = REPO_ROOT / "backend/app/services/phase2_claims_renderer.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert not ({"pathlib", "requests", "httpx", "socket", "subprocess"} & imported_roots)
    assert "open(" not in source_path.read_text(encoding="utf-8")


def test_preview_route_is_closed_and_never_routes_to_reviewed() -> None:
    assert route_claims_preview("CLAIMS_VALID") == "inbox"
    assert route_claims_preview("CLAIMS_INVALID") == "rejected"
    with pytest.raises(Phase2ClaimsDeliveryError, match="claims_status_unknown"):
        route_claims_preview("REVIEWED")


def test_bundle_publication_is_idempotent_and_preserves_historical_rejected(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    shutil.copytree(
        REPO_ROOT / "reports/phase2_obsidian_preview",
        reports_root / "phase2_obsidian_preview",
    )
    rejected_root = reports_root / "phase2_obsidian_preview/rejected"
    rejected_before = _tree_hashes(rejected_root)

    first = publish_claims_bundle(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )
    first_bundle_hashes = _tree_hashes(reports_root / "phase2_claims")
    first_typed_hashes = {
        key: value
        for key, value in _tree_hashes(
            reports_root / "phase2_obsidian_preview/inbox"
        ).items()
        if key.startswith("typed_")
    }
    second = publish_claims_bundle(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )

    assert first == second
    assert first["status"] == "CLAIMS_VALID"
    assert first["rendering_status"] == "RENDERED_VALID"
    assert len(first["entries"]) == 3
    assert all(item["preview_route"] == "inbox" for item in first["entries"])
    assert _tree_hashes(reports_root / "phase2_claims") == first_bundle_hashes
    assert {
        key: value
        for key, value in _tree_hashes(
            reports_root / "phase2_obsidian_preview/inbox"
        ).items()
        if key.startswith("typed_")
    } == first_typed_hashes
    assert sorted(first_typed_hashes) == [
        "typed_000403SZ_派林生物.md",
        "typed_300059SZ_东方财富.md",
        "typed_600489SH_中金黄金.md",
    ]
    assert _tree_hashes(rejected_root) == rejected_before
    assert list((reports_root / "phase2_obsidian_preview/reviewed").iterdir()) == []
    assert validate_claims_bundle(REPO_ROOT, reports_root)["errors"] == []


def test_bundle_index_matches_rendered_and_preview_hashes(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()

    publish_claims_bundle(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )
    index = json.loads(
        (reports_root / "phase2_claims/claims_index.json").read_text(
            encoding="utf-8"
        )
    )

    for entry in index["entries"]:
        rendered = reports_root / "phase2_claims" / entry["rendered_file"]
        preview = reports_root / "phase2_obsidian_preview" / entry["preview_file"]
        assert hashlib.sha256(rendered.read_bytes()).hexdigest() == entry[
            "rendered_sha256"
        ]
        assert rendered.read_bytes() == preview.read_bytes()
    assert index["can_publish"] is False
    assert index["trading_advice"] is False
    assert index["reviewed_auto_write"] is False
    assert index["new_tickflow_api_request_count"] == 0
    assert index["new_ai_call_count"] == 0
    assert index["new_provider_attempt_count"] == 0


def test_bundle_validator_rejects_symlinked_rendered_artifact_before_read(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    publish_claims_bundle(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )
    rendered = reports_root / "phase2_claims/rendered/000403SZ_派林生物.md"
    outside = tmp_path / "outside.md"
    outside.write_text("outside content must not be consumed", encoding="utf-8")
    rendered.unlink()
    rendered.symlink_to(outside)

    result = validate_claims_bundle(REPO_ROOT, reports_root)

    assert result["status"] == "CLAIMS_INVALID"
    assert result["errors"] == ["claims_tree_symlink_forbidden"]


@pytest.mark.parametrize("directory_name", ["schema", "fixtures", "rendered"])
def test_bundle_validator_rejects_symlinked_claims_subtree_before_read(
    tmp_path: Path,
    directory_name: str,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    publish_claims_bundle(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )
    target = reports_root / "phase2_claims" / directory_name
    outside = tmp_path / f"outside_{directory_name}"
    shutil.copytree(target, outside)
    shutil.rmtree(target)
    target.symlink_to(outside, target_is_directory=True)

    result = validate_claims_bundle(REPO_ROOT, reports_root)

    assert result["status"] == "CLAIMS_INVALID"
    assert result["errors"] == ["claims_tree_symlink_forbidden"]


def test_invalid_candidate_routes_only_a_sanitized_typed_rejection_receipt(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    canary = b"Bearer SECRET-CANARY-SHOULD-NOT-BE-WRITTEN"

    receipt = publish_invalid_claims_receipt(
        REPO_ROOT,
        reports_root,
        symbol="000403.SZ",
        candidate_bytes=canary,
        validation_errors=["worker_candidate_schema_invalid:SECRET-CANARY"],
        _allow_test_output_root=True,
    )

    assert receipt.parent.name == "rejected"
    assert receipt.name == "typed_000403SZ_派林生物_rejected.md"
    contents = receipt.read_text(encoding="utf-8")
    assert "SECRET-CANARY" not in contents
    assert "Bearer " not in contents
    assert hashlib.sha256(canary).hexdigest() in contents
    assert "can_publish: false" in contents
    assert "trading_advice: false" in contents


def test_bundle_publication_replaces_exact_typed_inbox_set(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    inbox = reports_root / "phase2_obsidian_preview/inbox"
    inbox.mkdir(parents=True)
    (inbox / "typed_stale.md").write_text("stale", encoding="utf-8")
    (inbox / "manual_note.md").write_text("preserve", encoding="utf-8")

    publish_claims_bundle(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )

    assert not (inbox / "typed_stale.md").exists()
    assert (inbox / "manual_note.md").read_text(encoding="utf-8") == "preserve"
    assert sorted(path.name for path in inbox.glob("typed_*.md")) == [
        "typed_000403SZ_派林生物.md",
        "typed_300059SZ_东方财富.md",
        "typed_600489SH_中金黄金.md",
    ]


def test_bundle_and_inbox_publication_roll_back_as_one_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    publish_claims_bundle(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )
    index = reports_root / "phase2_claims/claims_index.json"
    first_preview = (
        reports_root
        / "phase2_obsidian_preview/inbox/typed_000403SZ_派林生物.md"
    )
    index.write_text('{"old":"bundle"}\n', encoding="utf-8")
    first_preview.write_text("old preview\n", encoding="utf-8")
    bundle_before = _tree_hashes(reports_root / "phase2_claims")
    inbox_before = _tree_hashes(reports_root / "phase2_obsidian_preview/inbox")
    original_publish = claims_delivery.atomic_publish_directory

    def fail_second_destination(staging: Path, destination: Path) -> None:
        if destination.name == "inbox":
            raise OSError("injected inbox publication failure")
        original_publish(staging, destination)

    monkeypatch.setattr(
        claims_delivery,
        "atomic_publish_directory",
        fail_second_destination,
    )

    with pytest.raises(Phase2ClaimsDeliveryError, match="publication_failed"):
        publish_claims_bundle(
            REPO_ROOT,
            reports_root,
            _allow_test_output_root=True,
        )

    assert _tree_hashes(reports_root / "phase2_claims") == bundle_before
    assert (
        _tree_hashes(reports_root / "phase2_obsidian_preview/inbox")
        == inbox_before
    )
    assert not (reports_root / ".phase2_claims_delivery_transaction.json").exists()


def test_bundle_validator_fails_closed_while_transaction_marker_exists(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    publish_claims_bundle(
        REPO_ROOT,
        reports_root,
        _allow_test_output_root=True,
    )
    (reports_root / ".phase2_claims_delivery_transaction.json").write_text(
        '{"version":1}\n',
        encoding="utf-8",
    )

    result = validate_claims_bundle(REPO_ROOT, reports_root)

    assert result["status"] == "CLAIMS_INVALID"
    assert result["errors"] == ["publication_transaction_incomplete"]
