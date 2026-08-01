from __future__ import annotations

import ast
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from app.services.phase2_claims_delivery import (
    Phase2ClaimsDeliveryError,
    publish_claims_bundle,
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
