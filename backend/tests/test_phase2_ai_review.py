# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

import app.services.phase2_ai_review as phase2_ai_review
from app.services.phase2_ai_review import (
    REVIEW_NEEDS_VERIFICATION,
    REVIEW_REJECTED,
    Phase2ReviewError,
    ProviderResult,
    build_numeric_claim_catalog,
    build_review_prompt,
    rematerialize_review_delivery,
    render_review_document,
    route_review_preview,
    run_review_batch,
    validate_review_body,
    validate_review_delivery,
    validate_review_document,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FACTS_DIR = REPO_ROOT / "reports/phase2_facts"
SYMBOLS = {
    "000403.SZ": "派林生物",
    "600489.SH": "中金黄金",
    "300059.SZ": "东方财富",
}
HEADINGS = [
    "一句话观察结论",
    "日线技术结构",
    "分钟结构",
    "量价变化",
    "关键观察区间",
    "风险边界",
    "下一交易日观察清单",
    "需要人工确认的数据",
    "结论失效条件",
    "免责声明",
]
VENDOR_PENDING = [
    "intraday_batch_entitlement",
    "first_30m_bucket_includes_09_30",
    "volume_unit",
    "amount_unit",
]


def _load_facts(symbol: str = "000403.SZ") -> tuple[dict, str]:
    compact = symbol.replace(".", "")
    path = FACTS_DIR / f"{compact}_facts.json"
    return json.loads(path.read_text(encoding="utf-8")), hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_body(facts: dict) -> str:
    close = f'{facts["daily"]["close"]:.2f}'
    ma5 = f'{facts["indicators"]["ma5"]["value"]:.4f}'
    return f"""### 1. 一句话观察结论
截至 {facts["trade_date"]}，{facts["name"]}（{facts["symbol"]}）的确定性指标呈现分化，需继续观察。

### 2. 日线技术结构
原始口径收盘价为 {close}；前复权 MA5 为 {ma5}。两种价格口径仅分别陈述，没有直接比较。

### 3. 分钟结构
分钟数据合同已通过，供应商首桶口径仍待确认。

### 4. 量价变化
可以观察相对量能指标，但成交量和成交额的权威单位尚未确认。

### 5. 关键观察区间
确定性关键价位算法尚未实现，本节不生成具体区间。

### 6. 风险边界
若后续确定性数据与当前结构不一致，本结论需要重新评估。

### 7. 下一交易日观察清单
继续记录日线结构、分钟合同和相对量能的变化，并保持价格口径分离。

### 8. 需要人工确认的数据
市场范围不完整，仅覆盖单只股票。
财务数据未接入，需人工确认。
新闻与公告数据未接入，需人工确认。
供应商待确认项：`intraday_batch_entitlement`、`first_30m_bucket_includes_09_30`、`volume_unit`、`amount_unit`。

### 9. 结论失效条件
数据日期、复权口径或分钟合同一旦变化，当前观察结论即需重做。

### 10. 免责声明
本材料仅用于研究记录，不构成投资建议。
"""


def _parse_frontmatter(document: str) -> tuple[dict, str]:
    _, yaml_text, body = document.split("---", 2)
    return yaml.safe_load(yaml_text), body.lstrip("\n")


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_numeric_catalog_is_traceable_and_withholds_unknown_units() -> None:
    facts, facts_hash = _load_facts()

    catalog = build_numeric_claim_catalog(facts, facts_hash)

    assert catalog["10.43"][0].facts_pointer == "/daily/close"
    assert catalog["10.1360"][0].facts_pointer == "/indicators/ma5/value"
    assert all(item.facts_sha256 == facts_hash for items in catalog.values() for item in items)
    assert "107884" not in catalog
    assert "111975400" not in catalog
    assert all("volume_ma" not in item.facts_pointer for items in catalog.values() for item in items)


def test_prompt_is_facts_only_and_requires_exact_structure() -> None:
    facts, facts_hash = _load_facts()

    prompt = build_review_prompt(facts, facts_hash)

    for index, heading in enumerate(HEADINGS, 1):
        assert f"### {index}. {heading}" in prompt.system
    assert facts_hash in prompt.user
    assert "不得计算、插值、四舍五入或创造新数字" in prompt.system
    assert "即使是否定句也不得输出" in prompt.system
    assert "不要输出 YAML frontmatter" in prompt.system
    assert "VENDOR_CONFIRMATION_PENDING" in prompt.user
    assert "No approved deterministic key-level algorithm" in prompt.user
    assert str(facts["daily"]["volume"]) not in prompt.user
    assert str(facts["daily"]["amount"]) not in prompt.user


def test_valid_body_matches_every_numeric_claim_to_facts() -> None:
    facts, facts_hash = _load_facts()

    result = validate_review_body(_valid_body(facts), facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.errors == ["freeform_semantic_validation_incomplete"]
    assert result.unsupported_numeric_claims == []
    pointers = {item["facts_pointer"] for item in result.matched_numeric_claims}
    assert "/daily/close" in pointers
    assert "/indicators/ma5/value" in pointers
    assert result.can_publish is False


def test_chinese_adjacent_date_and_minute_periods_map_to_facts() -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("截至 2026-07-31", "截至2026-07-31")
    body = body.replace(
        "分钟数据合同已通过",
        "1分钟bar合同已通过，30分钟bucket合同已通过",
    )

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.errors == ["freeform_semantic_validation_incomplete"]
    assert result.unsupported_numeric_claims == []
    pointers = {item["facts_pointer"] for item in result.matched_numeric_claims}
    assert "/trade_date" in pointers
    assert "/minute_1m/period" in pointers
    assert "/minute_30m/period" in pointers


def test_raw_close_compared_with_qfq_indicator_is_rejected() -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace(
        "继续记录日线结构、分钟合同和相对量能的变化，并保持价格口径分离。",
        "关注日线收盘相对前复权均线簇的结构变化。",
    )

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.basis_mixing_claim_count == 1
    assert "raw_qfq_comparison_detected" in result.errors


@pytest.mark.parametrize("claim", ["99.99", "2026-08-01", "15:01", "000001.SZ"])
def test_unsupported_numeric_claim_is_rejected(claim: str) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("继续观察。", f"继续观察 {claim}。", 1)

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.unsupported_numeric_claims


@pytest.mark.parametrize(
    "claim",
    [
        "RMB999.99",
        "比例为8 %",
        "员工人数为241",
        "SH600000",
        "数值为1e3",
        "数值为10,000.00",
    ],
)
def test_numeric_claim_bypass_shapes_are_rejected(claim: str) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("继续观察。", f"继续观察。{claim}。", 1)

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.unsupported_numeric_claims


@pytest.mark.parametrize(
    "claim",
    [
        "数值为九十九点九",
        "比例为百分之八",
        "30分钟bar数量为8 **%**",
        f"facts_sha256 为 {'0' * 64}",
        "员工人数为10.43，收盘价来自Facts",
    ],
)
def test_extended_numeric_obfuscation_is_rejected(claim: str) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("继续观察。", f"继续观察。{claim}。", 1)

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.unsupported_numeric_claims


@pytest.mark.parametrize(
    "phrase",
    ["买入", "卖出", "加仓", "减仓", "仓位", "止损", "止盈", "目标价", "操作建议", "建议介入"],
)
def test_transaction_language_is_rejected_even_when_negated(phrase: str) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("不构成投资建议", f"不提供{phrase}")

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert phrase in result.forbidden_terms


def test_zero_width_character_cannot_split_forbidden_term() -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("不构成投资建议", "不提供买\u200b入指令")

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert "买入" in result.forbidden_terms


def test_variation_selector_cannot_split_forbidden_term() -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("不构成投资建议", "不提供买\ufe0f入指令")

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert "买入" in result.forbidden_terms


@pytest.mark.parametrize("obfuscated", ["买 入", "买**入", "买`入"])
def test_formatting_cannot_split_forbidden_term(obfuscated: str) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("不构成投资建议", f"不提供{obfuscated}指令")

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert "买入" in result.forbidden_terms


def test_financial_fabrication_is_rejected_without_a_number() -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("财务数据未接入，需人工确认。", "公司 ROE 表现稳健。")

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.financial_fabrication_count == 1


def test_news_fabrication_is_rejected_without_a_number() -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("新闻与公告数据未接入，需人工确认。", "公告显示公司正在回购。")

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.news_fabrication_count == 1


@pytest.mark.parametrize(
    "replacement,expected_field",
    [
        ("财务数据未接入，但净利润增长。", "financial_fabrication_count"),
        ("新闻数据未接入，但公告显示正在回购。", "news_fabrication_count"),
    ],
)
def test_unavailable_marker_cannot_hide_fabrication(
    replacement: str, expected_field: str
) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts)
    if expected_field == "financial_fabrication_count":
        body = body.replace("财务数据未接入，需人工确认。", replacement)
    else:
        body = body.replace("新闻与公告数据未接入，需人工确认。", replacement)

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert getattr(result, expected_field) == 1


@pytest.mark.parametrize(
    "replacement,expected_field",
    [
        ("财务数据未接入，但公司盈利能力明显改善。", "financial_fabrication_count"),
        ("新闻数据未接入，但公司公告披露新签重大合同。", "news_fabrication_count"),
    ],
)
def test_broader_fabrication_claims_are_rejected(
    replacement: str, expected_field: str
) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts)
    if expected_field == "financial_fabrication_count":
        body = body.replace("财务数据未接入，需人工确认。", replacement)
    else:
        body = body.replace("新闻与公告数据未接入，需人工确认。", replacement)

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert getattr(result, expected_field) == 1


@pytest.mark.parametrize(
    "required",
    [
        "市场范围不完整，仅覆盖单只股票。",
        "财务数据未接入，需人工确认。",
        "新闻与公告数据未接入，需人工确认。",
        "本材料仅用于研究记录，不构成投资建议。",
    ],
)
def test_required_scope_or_disclaimer_cannot_be_omitted(required: str) -> None:
    facts, facts_hash = _load_facts()

    result = validate_review_body(_valid_body(facts).replace(required, ""), facts, facts_hash)

    assert result.status == REVIEW_REJECTED


def test_all_vendor_pending_items_are_required() -> None:
    facts, facts_hash = _load_facts()

    for item in VENDOR_PENDING:
        result = validate_review_body(_valid_body(facts).replace(item, "removed"), facts, facts_hash)
        assert result.status == REVIEW_REJECTED
        assert f"vendor_pending_missing:{item}" in result.errors


@pytest.mark.parametrize(
    "secret_text",
    [
        "Authorization: Bearer abcdefghijklmnop",
        "Cookie: tf_session=abcdefghijklmnop",
        "api_key=abcdefghijklmnop",
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_secret_shapes_are_rejected(secret_text: str) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("继续观察。", f"继续观察。{secret_text}", 1)

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.secret_hits


def test_heading_order_and_model_frontmatter_are_rejected() -> None:
    facts, facts_hash = _load_facts()
    swapped = _valid_body(facts).replace("### 2. 日线技术结构", "### 2. 分钟结构")
    with_frontmatter = f"---\ncan_publish: true\n---\n{_valid_body(facts)}"

    assert validate_review_body(swapped, facts, facts_hash).status == REVIEW_REJECTED
    assert validate_review_body(with_frontmatter, facts, facts_hash).status == REVIEW_REJECTED


def test_extra_heading_and_html_comment_are_rejected() -> None:
    facts, facts_hash = _load_facts()
    extra_heading = _valid_body(facts) + "\n## 额外结论\n隐藏扩展。\n"
    hidden_scope = _valid_body(facts).replace(
        "市场范围不完整，仅覆盖单只股票。",
        "<!-- 市场范围不完整，仅覆盖单只股票。 -->",
    )

    assert validate_review_body(extra_heading, facts, facts_hash).status == REVIEW_REJECTED
    assert validate_review_body(hidden_scope, facts, facts_hash).status == REVIEW_REJECTED


@pytest.mark.parametrize(
    "suffix,expected_error",
    [
        ("\n额外结论\n====\n", "setext_heading_forbidden"),
        ("\n<b>额外结论</b>\n", "html_forbidden"),
    ],
)
def test_setext_heading_and_generic_html_are_rejected(
    suffix: str,
    expected_error: str,
) -> None:
    facts, facts_hash = _load_facts()

    result = validate_review_body(_valid_body(facts) + suffix, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert expected_error in result.errors


@pytest.mark.parametrize(
    "claim",
    [
        "30分钟bar数量为八根",
        "30分钟bar数量为8万根",
        r"30分钟bar数量为8\%",
        "1分钟bar数量为241和员工人数为241",
        "原始收盘比前复权MA5更高",
        "原始收盘高**于**前复权MA5",
        "不能直接比较，但原始收盘仍高于前复权MA5",
        "公司盈利水平明显改善",
        "公司签署战略合作协议",
        "全市场资金流入",
    ],
)
def test_freeform_bypass_examples_are_fail_closed(claim: str) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace("继续观察。", f"继续观察。{claim}。", 1)

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert route_review_preview(result.status) == "rejected"


def test_raw_qfq_comparison_synonym_is_rejected() -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace(
        "继续记录日线结构、分钟合同和相对量能的变化，并保持价格口径分离。",
        "原始收盘大于前复权MA5。",
    )

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.basis_mixing_claim_count == 1


@pytest.mark.parametrize(
    "replacement",
    [
        "口径不同，但原始收盘仍高于前复权MA5。",
        "原始收盘已记录。前复权MA5已记录。前者高于后者。",
    ],
)
def test_raw_qfq_comparison_cannot_hide_behind_scope_or_pronouns(
    replacement: str,
) -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts).replace(
        "继续记录日线结构、分钟合同和相对量能的变化，并保持价格口径分离。",
        replacement,
    )

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert result.basis_mixing_claim_count >= 1


def test_scope_lines_must_belong_to_section_eight() -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts)
    scope_block = """market placeholder"""
    for required in [
        "市场范围不完整，仅覆盖单只股票。",
        "财务数据未接入，需人工确认。",
        "新闻与公告数据未接入，需人工确认。",
    ]:
        body = body.replace(required, "")
        scope_block += f"\n{required}"
    body = body.replace("需继续观察。", f"需继续观察。\n{scope_block}", 1)

    result = validate_review_body(body, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert "section_8_scope_contract_invalid" in result.errors


def test_rendered_document_has_fixed_safe_frontmatter() -> None:
    facts, facts_hash = _load_facts()
    body = _valid_body(facts)
    generated_at = "2026-08-01T16:30:00+08:00"

    document = render_review_document(body, facts, facts_hash, generated_at)
    frontmatter, parsed_body = _parse_frontmatter(document)
    validation = validate_review_document(document, facts, facts_hash)

    assert parsed_body == body
    assert frontmatter == {
        "type": "stock-daily-review",
        "source_system": "tickflow-stock-panel",
        "facts_schema_version": 1,
        "symbol": "000403.SZ",
        "name": "派林生物",
        "timeframe": ["1d", "30m"],
        "as_of": "2026-07-31",
        "latest_trade_date": "2026-07-31",
        "generated_at": generated_at,
        "data_scope": "single-symbol",
        "market_scope": "incomplete",
        "financial_scope": "unavailable",
        "news_scope": "unavailable",
        "data_freshness": "fresh",
        "verification_status": "pending",
        "review_status": REVIEW_REJECTED,
        "can_publish": False,
        "trading_advice": False,
        "vendor_pending": VENDOR_PENDING,
    }
    assert validation.status == REVIEW_REJECTED
    assert validation.errors == ["freeform_semantic_validation_incomplete"]


def test_frontmatter_tampering_is_rejected() -> None:
    facts, facts_hash = _load_facts()
    document = render_review_document(
        _valid_body(facts), facts, facts_hash, "2026-08-01T16:30:00+08:00"
    )

    tampered = document.replace("can_publish: false", "can_publish: true")
    result = validate_review_document(tampered, facts, facts_hash)

    assert result.status == REVIEW_REJECTED
    assert "frontmatter_can_publish_invalid" in result.errors


def test_preview_router_never_routes_to_reviewed() -> None:
    assert route_review_preview("REVIEW_VALID") == "inbox"
    assert route_review_preview(REVIEW_NEEDS_VERIFICATION) == "rejected"
    assert route_review_preview(REVIEW_REJECTED) == "rejected"
    assert "reviewed" not in {
        route_review_preview("REVIEW_VALID"),
        route_review_preview(REVIEW_NEEDS_VERIFICATION),
        route_review_preview(REVIEW_REJECTED),
    }


def test_batch_status_is_ready_only_when_every_review_is_valid() -> None:
    assert phase2_ai_review._batch_status(["REVIEW_VALID"] * 3) == (
        "PHASE2_AI_REVIEW_READY_FOR_OBSERVATION"
    )
    assert phase2_ai_review._batch_status(
        ["REVIEW_VALID", REVIEW_NEEDS_VERIFICATION, "REVIEW_VALID"]
    ) == "PHASE2_AI_OUTPUT_BLOCKED"
    assert phase2_ai_review._batch_status(
        ["REVIEW_VALID", REVIEW_REJECTED, "REVIEW_VALID"]
    ) == "PHASE2_AI_OUTPUT_BLOCKED"


def test_batch_rejects_output_outside_repository_reports(tmp_path: Path) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        raise AssertionError("provider must not run before output-root validation")

    with pytest.raises(Phase2ReviewError, match="repository reports directory"):
        asyncio.run(
            run_review_batch(
                REPO_ROOT,
                tmp_path,
                provider,
                generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            )
        )


def test_batch_rejects_symlinked_repository_reports(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    external_reports = tmp_path / "external-reports"
    repo_root.mkdir()
    external_reports.mkdir()
    (repo_root / "reports").symlink_to(external_reports, target_is_directory=True)

    async def provider(prompt, symbol: str) -> ProviderResult:
        raise AssertionError("provider must not run before output-root validation")

    with pytest.raises(
        Phase2ReviewError, match="repository reports directory must not be a symlink"
    ):
        asyncio.run(
            run_review_batch(
                repo_root,
                external_reports,
                provider,
                generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            )
        )


def test_transaction_marker_creation_is_exclusive(tmp_path: Path) -> None:
    marker = tmp_path / ".phase2_delivery_transaction.json"

    phase2_ai_review._create_exclusive_transaction_marker(marker, b"first\n")

    with pytest.raises(Phase2ReviewError, match="transaction already exists"):
        phase2_ai_review._create_exclusive_transaction_marker(marker, b"second\n")
    assert marker.read_bytes() == b"first\n"


def test_batch_calls_provider_once_per_symbol_and_builds_isolated_preview(tmp_path: Path) -> None:
    calls: list[str] = []

    async def provider(prompt, symbol: str) -> ProviderResult:
        calls.append(symbol)
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    result = asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )

    assert calls == list(SYMBOLS)
    assert result["status"] == "PHASE2_AI_OUTPUT_BLOCKED"
    assert result["provider_attempt_count"] == 3
    assert result["tickflow_api_request_count"] == 0
    assert result["cloud_mutation_count"] == 0
    for symbol, name in SYMBOLS.items():
        filename = f'{symbol.replace(".", "")}_{name}.md'
        assert (tmp_path / "phase2_ai_samples" / filename).is_file()
        assert (tmp_path / "phase2_obsidian_preview" / "rejected" / filename).is_file()
        assert not (tmp_path / "phase2_obsidian_preview" / "inbox" / filename).exists()
    assert list((tmp_path / "phase2_obsidian_preview" / "reviewed").iterdir()) == []

    audit = json.loads(
        (tmp_path / "phase2_ai_samples/generation_audit.json").read_text(encoding="utf-8")
    )
    assert [item["provider_attempt_count"] for item in audit["symbols"]] == [1, 1, 1]
    assert all(item["review_status"] == REVIEW_REJECTED for item in audit["symbols"])
    assert audit["provider_attempt_policy"] == (
        "one_process_recorded_attempt_per_symbol_no_retry"
    )
    assert audit["attempt_evidence_strength"] == (
        "process_recorded_not_external_attestation"
    )
    assert all(item["model_identity_verified"] is False for item in audit["symbols"])


def test_provider_failure_does_not_publish_partial_batch(tmp_path: Path) -> None:
    calls: list[str] = []

    async def provider(prompt, symbol: str) -> ProviderResult:
        calls.append(symbol)
        if len(calls) == 2:
            raise RuntimeError("provider failed")
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    with pytest.raises(Phase2ReviewError, match="provider failed"):
        asyncio.run(
            run_review_batch(
                REPO_ROOT,
                tmp_path,
                provider,
                generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
                _allow_test_output_root=True,
            )
        )

    assert calls == ["000403.SZ", "600489.SH"]
    assert not (tmp_path / "phase2_ai_samples").exists()
    assert not (tmp_path / "phase2_obsidian_preview").exists()


def test_rejected_body_is_preserved_only_in_rejected_preview(tmp_path: Path) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        body = _valid_body(facts)
        if symbol == "600489.SH":
            body = body.replace("继续观察。", "继续观察 99.99。", 1)
        return ProviderResult(
            text=body,
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    result = asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )

    assert result["status"] == "PHASE2_AI_OUTPUT_BLOCKED"
    rejected = tmp_path / "phase2_obsidian_preview/rejected/600489SH_中金黄金.md"
    assert rejected.is_file()
    assert not (tmp_path / "phase2_obsidian_preview/inbox/600489SH_中金黄金.md").exists()


def test_offline_delivery_validator_accepts_complete_batch(tmp_path: Path) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )

    result = validate_review_delivery(REPO_ROOT, tmp_path)

    assert result["status"] == "PHASE2_AI_OUTPUT_BLOCKED"
    assert result["errors"] == []
    assert result["unsupported_numeric_claim_count"] == 0
    assert result["forbidden_term_count"] == 0
    assert result["financial_fabrication_count"] == 0
    assert result["news_fabrication_count"] == 0
    assert result["secret_hit_count"] == 0
    assert result["reviewed_file_count"] == 0
    assert result["tickflow_api_request_count"] == 0


def test_publication_failure_rolls_back_both_delivery_directories(
    tmp_path: Path, monkeypatch
) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )
    before = _tree_hashes(tmp_path)
    real_publish = phase2_ai_review.atomic_publish_directory
    call_count = 0

    def fail_second_publish(staging: Path, destination: Path) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("simulated second-directory publication failure")
        real_publish(staging, destination)

    monkeypatch.setattr(
        phase2_ai_review,
        "atomic_publish_directory",
        fail_second_publish,
    )

    with pytest.raises(Phase2ReviewError, match="delivery publication failed"):
        asyncio.run(
            run_review_batch(
                REPO_ROOT,
                tmp_path,
                provider,
                generated_at=datetime.fromisoformat("2026-08-01T17:30:00+08:00"),
                _allow_test_output_root=True,
            )
        )

    assert _tree_hashes(tmp_path) == before
    assert not (tmp_path / ".phase2_delivery_transaction.json").exists()


def test_offline_delivery_validator_blocks_incomplete_transaction(tmp_path: Path) -> None:
    (tmp_path / "phase2_ai_samples").mkdir()
    (tmp_path / "phase2_obsidian_preview").mkdir()
    (tmp_path / ".phase2_delivery_transaction.json").write_text(
        '{"state":"publishing"}\n', encoding="utf-8"
    )

    result = validate_review_delivery(REPO_ROOT, tmp_path)

    assert result["status"] == "PHASE2_AI_OUTPUT_BLOCKED"
    assert result["errors"] == ["delivery_transaction_incomplete"]


def test_offline_delivery_validator_detects_sample_tampering(tmp_path: Path) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )
    sample = tmp_path / "phase2_ai_samples/000403SZ_派林生物.md"
    sample.write_text(sample.read_text(encoding="utf-8") + "\n无来源数字 99.99\n", encoding="utf-8")

    result = validate_review_delivery(REPO_ROOT, tmp_path)

    assert result["status"] == "PHASE2_AI_OUTPUT_BLOCKED"
    assert result["errors"]


@pytest.mark.parametrize(
    "field,value,expected_error",
    [
        ("prompt_sha256", "0" * 64, "audit_mismatch:000403.SZ:prompt_sha256"),
        ("ai_body_sha256", "0" * 64, "audit_mismatch:000403.SZ:ai_body_sha256"),
        (
            "input_facts_path",
            "reports/phase2_facts/another.json",
            "audit_mismatch:000403.SZ:input_facts_path",
        ),
    ],
)
def test_offline_delivery_validator_recomputes_provenance_fields(
    tmp_path: Path, field: str, value: str, expected_error: str
) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )
    audit_path = tmp_path / "phase2_ai_samples/generation_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["symbols"][0][field] = value
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    result = validate_review_delivery(REPO_ROOT, tmp_path)

    assert result["status"] == "PHASE2_AI_OUTPUT_BLOCKED"
    assert expected_error in result["errors"]


def test_offline_delivery_validator_rejects_duplicate_audit_symbol(tmp_path: Path) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )
    audit_path = tmp_path / "phase2_ai_samples/generation_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["symbols"].append(dict(audit["symbols"][0]))
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    result = validate_review_delivery(REPO_ROOT, tmp_path)

    assert result["status"] == "PHASE2_AI_OUTPUT_BLOCKED"
    assert "audit_symbol_entries_invalid" in result["errors"]


def test_offline_delivery_validator_rejects_manifest_tampering(tmp_path: Path) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )
    manifest_path = tmp_path / "phase2_obsidian_preview/validation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"][0]["route"] = "reviewed"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    result = validate_review_delivery(REPO_ROOT, tmp_path)

    assert result["status"] == "PHASE2_AI_OUTPUT_BLOCKED"
    assert "preview_manifest_entries_mismatch" in result["errors"]


def test_offline_rematerialization_reuses_bodies_without_provider_calls(
    tmp_path: Path,
) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )
    original_audit_path = tmp_path / "phase2_ai_samples/generation_audit.json"
    original_audit_hash = hashlib.sha256(original_audit_path.read_bytes()).hexdigest()
    sample = tmp_path / "phase2_ai_samples/000403SZ_派林生物.md"
    sample.write_text(
        sample.read_text(encoding="utf-8").replace(
            "can_publish: false",
            "can_publish: true",
        ),
        encoding="utf-8",
    )

    result = rematerialize_review_delivery(
        REPO_ROOT,
        tmp_path,
        reason="validator_contract_fix",
        _allow_test_output_root=True,
    )

    assert result["provider_attempt_count"] == 3
    assert result["additional_ai_call_count"] == 0
    assert result["additional_provider_attempt_count"] == 0
    assert result["offline_rematerialization"]["source_audit_sha256"] == original_audit_hash
    assert result["offline_rematerialization"]["body_hashes_verified"] is True
    assert result["audit_predecessor_sha256"] == original_audit_hash
    history_file = (
        tmp_path
        / "phase2_ai_samples/audit_history"
        / f"{original_audit_hash}.json"
    )
    assert hashlib.sha256(history_file.read_bytes()).hexdigest() == original_audit_hash
    assert validate_review_delivery(REPO_ROOT, tmp_path)["status"] == (
        "PHASE2_AI_OUTPUT_BLOCKED"
    )


def test_rematerialization_rejects_symlinked_audit_before_following_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )
    audit_path = tmp_path / "phase2_ai_samples/generation_audit.json"
    outside = tmp_path / "outside-audit.json"
    outside.write_bytes(audit_path.read_bytes())
    audit_path.unlink()
    audit_path.symlink_to(outside)
    original_read_bytes = Path.read_bytes
    followed: list[Path] = []

    def track_read_bytes(path: Path) -> bytes:
        if path == audit_path:
            followed.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", track_read_bytes)

    with pytest.raises(Phase2ReviewError, match="regular file"):
        rematerialize_review_delivery(
            REPO_ROOT,
            tmp_path,
            reason="symlink_safety_check",
            _allow_test_output_root=True,
        )
    assert followed == []


def test_offline_delivery_validator_checks_audit_history_sequence(tmp_path: Path) -> None:
    async def provider(prompt, symbol: str) -> ProviderResult:
        facts, _ = _load_facts(symbol)
        return ProviderResult(
            text=_valid_body(facts),
            provider="codex_cli",
            model="codex_cli_default",
            model_source="active_codex_cli_configuration",
            duration_ms=25,
        )

    asyncio.run(
        run_review_batch(
            REPO_ROOT,
            tmp_path,
            provider,
            generated_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
            _allow_test_output_root=True,
        )
    )
    rematerialize_review_delivery(
        REPO_ROOT,
        tmp_path,
        reason="validator_contract_fix",
        _allow_test_output_root=True,
    )
    audit_path = tmp_path / "phase2_ai_samples/generation_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["offline_rematerialization_count"] = 99
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    result = validate_review_delivery(REPO_ROOT, tmp_path)

    assert result["status"] == "PHASE2_AI_OUTPUT_BLOCKED"
    assert "audit_history_sequence_mismatch" in result["errors"]
