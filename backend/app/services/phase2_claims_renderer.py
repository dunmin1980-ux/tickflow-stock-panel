# ruff: noqa: RUF001
"""Pure deterministic Markdown rendering for validated Phase 2 Claims."""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Callable
from typing import Any

from app.schemas.phase2_claims import ClaimsDocument
from app.services.phase2_claims_service import (
    CLAIMS_VALID,
    ClaimsValidationResult,
    canonical_json_bytes,
)

SECTION_HEADINGS = (
    "数据范围",
    "日线事实",
    "技术指标事实",
    "分钟数据质量",
    "数据限制",
    "供应商待确认",
    "免责声明",
)
FRONTMATTER_LINES = (
    "---",
    "type: stock-typed-claims-review",
    "source_system: tickflow-stock-panel",
    "facts_schema_version: 1",
    "claims_schema_version: 1",
    "data_scope: single-symbol",
    "market_scope: incomplete",
    "financial_scope: unavailable",
    "news_scope: unavailable",
    "verification_status: validated",
    "can_publish: false",
    "trading_advice: false",
    "rendering_mode: deterministic",
    "---",
)

_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff")
_HTML_PATTERN = re.compile(r"<!--|-->|</?[A-Za-z][^>]*>")
_LINK_PATTERN = re.compile(r"\[[^\]\n]*\]\([^\n)]*\)|https?://", re.IGNORECASE)
_MARKDOWN_META = re.compile(r"([\\`*_{}\[\]()#+!|])")

_LABELS = {
    "market_scope": "市场范围",
    "financial_scope": "财务数据范围",
    "news_scope": "消息数据范围",
    "industry_scope": "行业数据范围",
    "daily_close": "日线收盘价",
    "daily_open_to_close_percent": "开盘至收盘变化率",
    "ma5": "MA5",
    "ma10": "MA10",
    "ma20": "MA20",
    "ma60": "MA60",
    "macd_dif": "MACD DIF",
    "macd_dea": "MACD DEA",
    "macd_hist": "MACD 柱值",
    "rsi6": "RSI6",
    "rsi14": "RSI14",
    "boll_upper": "BOLL 上轨",
    "boll_middle": "BOLL 中轨",
    "boll_lower": "BOLL 下轨",
    "atr14": "ATR14",
    "minute_1m_bar_count": "1 分钟 K 线数量",
    "minute_30m_bar_count": "30 分钟 K 线数量",
    "daily_contract_status": "日线合同状态",
    "minute_1m_contract_status": "1 分钟合同状态",
    "minute_30m_contract_status": "30 分钟合同状态",
    "adjustment_factor_status": "复权因子合同状态",
    "data_freshness": "数据新鲜度",
    "adjustment_repeat_stable": "复权因子重复计算稳定",
    "adjustment_second_adjustment_detected": "检出二次复权",
    "minute_30m_first_bucket_mechanically_explained": "30 分钟首桶差异可机械解释",
    "minute_1m_duplicate_timestamp_count": "1 分钟重复时间戳数",
    "minute_1m_lunch_break_bar_count": "1 分钟午休伪 K 线数",
    "minute_1m_material_ohlc_anomaly_count": "1 分钟实质 OHLC 异常数",
    "minute_1m_material_negative_amount_count": "1 分钟实质负成交额数",
    "minute_30m_duplicate_timestamp_count": "30 分钟重复时间戳数",
    "minute_30m_lunch_break_bar_count": "30 分钟午休伪 K 线数",
    "minute_30m_ohlc_mismatch_count": "30 分钟 OHLC 不一致数",
    "minute_30m_non_first_bucket_volume_amount_mismatch_count": (
        "30 分钟非首桶量额不一致数"
    ),
    "minute_30m_material_ohlc_anomaly_count": "30 分钟实质 OHLC 异常数",
    "minute_30m_material_negative_amount_count": "30 分钟实质负成交额数",
    "macd_dif_vs_dea": "MACD DIF 与 DEA 关系",
    "ma_value_order": "均线数值顺序",
    "vendor_pending": "供应商待确认项",
}
_OPERAND_LABELS = {
    "daily_open": "日线开盘价",
    "daily_close": "日线收盘价",
    "ma5": "MA5",
    "ma10": "MA10",
    "ma20": "MA20",
    "ma60": "MA60",
    "macd_dif": "MACD DIF",
    "macd_dea": "MACD DEA",
    "rsi6": "RSI6",
    "rsi14": "RSI14",
}
_UNIT_LABELS = {
    "raw_price": "原始价格",
    "qfq_price": "前复权价格",
    "percent": "%",
    "dimensionless": "无量纲",
    "count": "条",
    "none": "无",
}
_BASIS_LABELS = {
    "raw": "原始价格口径",
    "qfq": "前复权价格口径",
    "none": "非价格口径",
}
_STATE_LABELS = {
    "PASSED": "通过",
    "FAILED": "失败",
    "AVAILABLE": "可用",
    "UNAVAILABLE": "不可用",
    "COMPLETE": "完整",
    "INCOMPLETE": "不完整",
    "fresh": "数据新鲜",
}
_SCOPE_LABELS = {
    "incomplete": "不完整",
    "unavailable": "不可用",
}
_RELATION_LABELS = {
    "ABOVE": "高于",
    "BELOW": "低于",
    "EQUAL_WITHIN_EPSILON": "在容差内相等",
    "DESCENDING": "严格降序",
    "ASCENDING": "严格升序",
}
_VENDOR_LABELS = {
    "intraday_batch_entitlement": "intraday_batch Pro 权限",
    "first_30m_bucket_includes_09_30": "30 分钟首桶是否正式包含 09:30",
    "volume_unit": "volume 权威单位",
    "amount_unit": "amount 权威单位",
}

_DAILY_PREDICATES = frozenset(
    {
        "daily_close",
        "daily_open_to_close_percent",
        "daily_contract_status",
        "adjustment_factor_status",
        "adjustment_repeat_stable",
        "adjustment_second_adjustment_detected",
    }
)
_TECHNICAL_PREDICATES = frozenset(
    {
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "macd_dif",
        "macd_dea",
        "macd_hist",
        "rsi6",
        "rsi14",
        "boll_upper",
        "boll_middle",
        "boll_lower",
        "atr14",
        "macd_dif_vs_dea",
        "ma_value_order",
    }
)
_LIMIT_PREDICATES = frozenset(
    {"financial_scope", "news_scope", "industry_scope"}
)


class Phase2ClaimsRenderError(ValueError):
    """Raised when validated Claims cannot be rendered deterministically."""


def _escape_inline(value: str) -> str:
    if any(character in value for character in _ZERO_WIDTH):
        raise Phase2ClaimsRenderError("dynamic_text_contains_zero_width")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return _MARKDOWN_META.sub(r"\\\1", html.escape(normalized, quote=True))


def _format_number(value: int | float, precision: int | None) -> str:
    if precision is None:
        raise Phase2ClaimsRenderError("numeric_precision_missing")
    return f"{value:.{precision}f}"


def _format_numeric(claim: Any) -> str:
    value = _format_number(claim.object.value, claim.rendering.display_precision)
    return (
        f"- {_LABELS[claim.predicate]}：{value}；单位："
        f"{_UNIT_LABELS[claim.object.unit]}；口径："
        f"{_BASIS_LABELS[claim.provenance.price_basis]}"
    )


def _format_boolean(claim: Any) -> str:
    value = "是" if claim.object.value else "否"
    return f"- {_LABELS[claim.predicate]}：{value}；口径：非价格口径"


def _format_enum(claim: Any) -> str:
    return (
        f"- {_LABELS[claim.predicate]}：{_STATE_LABELS[claim.object.value]}"
        "；口径：非价格口径"
    )


def _format_comparison(claim: Any) -> str:
    precision = claim.rendering.display_precision
    left = claim.object.left
    right = claim.object.right
    return (
        f"- {_LABELS[claim.predicate]}：{_OPERAND_LABELS[left.label_id]} "
        f"{_format_number(left.value, precision)} "
        f"{_RELATION_LABELS[claim.object.relation]} "
        f"{_OPERAND_LABELS[right.label_id]} {_format_number(right.value, precision)}"
        f"；口径：{_BASIS_LABELS[claim.provenance.price_basis]}"
    )


def _format_ordered(claim: Any) -> str:
    precision = claim.rendering.display_precision
    values = " > ".join(
        f"{_OPERAND_LABELS[item.label_id]} {_format_number(item.value, precision)}"
        for item in claim.object.operands
    )
    if claim.object.relation == "ASCENDING":
        values = " < ".join(
            f"{_OPERAND_LABELS[item.label_id]} {_format_number(item.value, precision)}"
            for item in claim.object.operands
        )
    return (
        f"- {_LABELS[claim.predicate]}：{values}；"
        f"关系：{_RELATION_LABELS[claim.object.relation]}；"
        f"口径：{_BASIS_LABELS[claim.provenance.price_basis]}"
    )


def _format_scope(claim: Any) -> str:
    return f"- {_LABELS[claim.predicate]}：{_SCOPE_LABELS[claim.object.value]}"


def _format_vendor(claim: Any) -> str:
    values = "；".join(_VENDOR_LABELS[item] for item in claim.object.items)
    return f"- {_LABELS[claim.predicate]}：{values}"


_TEMPLATE_FORMATTERS: dict[str, Callable[[Any], str]] = {
    "daily_close_v1": _format_numeric,
    "daily_open_to_close_percent_v1": _format_numeric,
    "indicator_numeric_v1": _format_numeric,
    "data_quality_count_v1": _format_numeric,
    "contract_state_v1": _format_enum,
    "boolean_state_v1": _format_boolean,
    "same_basis_comparison_v1": _format_comparison,
    "ordered_relation_v1": _format_ordered,
    "scope_notice_v1": _format_scope,
    "vendor_pending_v1": _format_vendor,
}


def _section_for(predicate: str) -> str:
    if predicate in {"market_scope", "data_freshness"}:
        return "数据范围"
    if predicate in _DAILY_PREDICATES:
        return "日线事实"
    if predicate in _TECHNICAL_PREDICATES:
        return "技术指标事实"
    if predicate.startswith("minute_"):
        return "分钟数据质量"
    if predicate in _LIMIT_PREDICATES:
        return "数据限制"
    if predicate == "vendor_pending":
        return "供应商待确认"
    raise Phase2ClaimsRenderError(f"predicate_section_unknown:{predicate}")


def render_claims_document(
    document: ClaimsDocument,
    validation: ClaimsValidationResult,
) -> str:
    """Render one already validated Claims document without external access."""
    if not isinstance(document, ClaimsDocument):
        raise Phase2ClaimsRenderError("claims_document_type_invalid")
    if validation.status != CLAIMS_VALID or validation.errors:
        raise Phase2ClaimsRenderError("claims_not_valid")
    document_sha256 = hashlib.sha256(
        canonical_json_bytes(document.model_dump(mode="json"))
    ).hexdigest()
    if (
        validation.normalized_sha256 != document_sha256
        or validation.claim_count != len(document.claims)
        or validation.free_text_field_count != 0
        or validation.unsourced_claim_count != 0
        or validation.trading_claim_count != 0
        or validation.raw_qfq_mismatch_count != 0
        or validation.sensitive_hit_count != 0
    ):
        raise Phase2ClaimsRenderError("claims_validation_binding_mismatch")
    sections: dict[str, list[str]] = {heading: [] for heading in SECTION_HEADINGS}
    sections["数据范围"].extend(
        (
            f"- 标的：{_escape_inline(document.name)}（{_escape_inline(document.symbol)}）",
            f"- 数据日期：{_escape_inline(document.trade_date)}",
            f"- 时区：{_escape_inline(document.timezone)}",
        )
    )
    for claim in document.claims:
        formatter = _TEMPLATE_FORMATTERS.get(claim.rendering.template_id)
        if formatter is None:
            raise Phase2ClaimsRenderError(
                f"render_template_unknown:{claim.rendering.template_id}"
            )
        sections[_section_for(claim.predicate)].append(formatter(claim))
    sections["免责声明"] = [
        "- 本文仅为基于已验证数据合同生成的研究素材，不构成投资建议。",
        "- 不可自动发布，必须经人工二次核验。",
    ]
    lines = [*FRONTMATTER_LINES, ""]
    for heading in SECTION_HEADINGS:
        lines.extend((f"## {heading}", "", *sections[heading], ""))
    return "\n".join(lines).rstrip() + "\n"


def validate_rendered_document(
    document: ClaimsDocument,
    validation: ClaimsValidationResult,
    rendered: str,
) -> list[str]:
    """Validate deterministic rendering and reject active Markdown shapes."""
    errors: list[str] = []
    try:
        expected = render_claims_document(document, validation)
    except Phase2ClaimsRenderError:
        return ["rendered_document_source_invalid"]
    if rendered != expected:
        errors.append("rendered_document_not_deterministic")
    headings = tuple(
        line.removeprefix("## ")
        for line in rendered.splitlines()
        if line.startswith("## ")
    )
    if headings != SECTION_HEADINGS:
        errors.append("rendered_document_headings_invalid")
    if _HTML_PATTERN.search(rendered):
        errors.append("rendered_document_contains_html")
    if _LINK_PATTERN.search(rendered):
        errors.append("rendered_document_contains_link")
    if any(character in rendered for character in _ZERO_WIDTH):
        errors.append("rendered_document_contains_zero_width")
    if not rendered.startswith("\n".join(FRONTMATTER_LINES) + "\n"):
        errors.append("rendered_document_frontmatter_invalid")
    return sorted(set(errors))
