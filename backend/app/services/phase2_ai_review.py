# ruff: noqa: RUF001
"""Facts-only Phase 2 AI review generation, validation and preview routing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_facts import (
    FACTS_SCHEMA_VERSION,
    FIXED_SYMBOLS,
    VENDOR_PENDING,
    canonical_json_bytes,
    validate_facts_document,
)

REVIEW_VALID = "REVIEW_VALID"
REVIEW_REJECTED = "REVIEW_REJECTED"
REVIEW_NEEDS_VERIFICATION = "REVIEW_NEEDS_VERIFICATION"
REVIEW_STATUSES = {
    REVIEW_VALID,
    REVIEW_REJECTED,
    REVIEW_NEEDS_VERIFICATION,
}

PHASE2_READY = "PHASE2_AI_REVIEW_READY_FOR_OBSERVATION"
PHASE2_OUTPUT_BLOCKED = "PHASE2_AI_OUTPUT_BLOCKED"

REVIEW_HEADINGS = [
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

REQUIRED_SCOPE_LINES = [
    "市场范围不完整，仅覆盖单只股票。",
    "财务数据未接入，需人工确认。",
    "新闻与公告数据未接入，需人工确认。",
]
DISCLAIMER = "本材料仅用于研究记录，不构成投资建议。"

FORBIDDEN_TERMS = (
    "买入",
    "卖出",
    "加仓",
    "减仓",
    "仓位",
    "止损",
    "止盈",
    "目标价",
    "操作建议",
    "进攻仓位",
    "防守仓位",
    "建议介入",
)

_FINANCIAL_ASSERTION_RE = re.compile(
    r"\bROE\b|净利润|营业收入|营收|毛利率|净利率|资产负债率|"
    r"市盈率|市净率|每股收益|业绩增长|利润增长|财务增速",
    re.IGNORECASE,
)
_NEWS_ASSERTION_RE = re.compile(
    r"公告显示|新闻显示|消息称|利好|利空|催化|回购|增持|"
    r"减持|解禁|分红|并购|重组",
    re.IGNORECASE,
)
_UNAVAILABLE_MARKERS = ("未接入", "不可用", "无法确认", "需人工确认")
_SECRET_PATTERNS = {
    "authorization": re.compile(r"Authorization\s*:\s*\S+", re.IGNORECASE),
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    "cookie": re.compile(r"Cookie\s*:\s*\S+", re.IGNORECASE),
    "session": re.compile(r"tf_session\s*=\s*\S+", re.IGNORECASE),
    "api_key": re.compile(r"api[_-]?key\s*[:=]\s*\S+", re.IGNORECASE),
    "private_key": re.compile(r"BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY", re.IGNORECASE),
}
_SYMBOL_RE = re.compile(r"\b\d{6}\.(?:SZ|SH|BJ)\b")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?(?:\d+\.\d+|\d+)(?![A-Za-z0-9_])")
_HEADING_RE = re.compile(r"^###\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)


class Phase2ReviewError(RuntimeError):
    """Raised when the review pipeline cannot publish a complete batch."""


@dataclass(frozen=True)
class NumericClaimSource:
    literal: str
    label: str
    facts_pointer: str
    facts_sha256: str
    source_value: int | float
    price_basis: str
    unit: str
    display_transform: str


@dataclass(frozen=True)
class ReviewPrompt:
    system: str
    user: str
    facts_sha256: str
    prompt_sha256: str

    @property
    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


@dataclass(frozen=True)
class ProviderResult:
    text: str
    provider: str
    model: str
    model_source: str
    duration_ms: int


@dataclass
class ReviewValidation:
    status: str
    errors: list[str]
    unsupported_numeric_claims: list[dict[str, Any]]
    matched_numeric_claims: list[dict[str, Any]]
    forbidden_terms: list[str]
    secret_hits: list[str]
    financial_fabrication_count: int
    news_fabrication_count: int
    needs_verification: list[str]
    can_publish: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ReviewProvider = Callable[[ReviewPrompt, str], Awaitable[ProviderResult]]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _require_facts_shape(facts: Mapping[str, Any], facts_sha256: str) -> None:
    if facts.get("facts_schema_version") != FACTS_SCHEMA_VERSION:
        raise Phase2ReviewError("unsupported Facts schema")
    symbol = facts.get("symbol")
    if symbol not in FIXED_SYMBOLS or facts.get("name") != FIXED_SYMBOLS[symbol]:
        raise Phase2ReviewError("Facts symbol is outside the fixed Phase 2 scope")
    if not re.fullmatch(r"[0-9a-f]{64}", facts_sha256):
        raise Phase2ReviewError("Facts SHA-256 is invalid")
    if facts.get("trading") is not False:
        raise Phase2ReviewError("Facts trading flag must be false")
    if facts.get("vendor_pending") != VENDOR_PENDING:
        raise Phase2ReviewError("Facts vendor pending contract changed")
    if facts.get("scope") != {
        "market_scope": "incomplete",
        "financial_scope": "unavailable",
        "news_scope": "unavailable",
        "industry_scope": "manual_verification_required",
    }:
        raise Phase2ReviewError("Facts scope contract changed")


def build_numeric_claim_catalog(
    facts: Mapping[str, Any], facts_sha256: str
) -> dict[str, list[NumericClaimSource]]:
    """Return exact, display-safe literals the model may copy into its body."""
    _require_facts_shape(facts, facts_sha256)
    catalog: dict[str, list[NumericClaimSource]] = {}

    def add(
        literal: str,
        *,
        label: str,
        pointer: str,
        value: int | float,
        price_basis: str,
        unit: str,
        transform: str,
    ) -> None:
        source = NumericClaimSource(
            literal=literal,
            label=label,
            facts_pointer=pointer,
            facts_sha256=facts_sha256,
            source_value=value,
            price_basis=price_basis,
            unit=unit,
            display_transform=transform,
        )
        catalog.setdefault(literal, []).append(source)

    daily = facts["daily"]
    for field in ("open", "high", "low", "close"):
        value = float(daily[field])
        add(
            f"{value:.2f}",
            label=f"daily_{field}",
            pointer=f"/daily/{field}",
            value=value,
            price_basis="raw",
            unit="raw_price",
            transform="fixed_2_decimal",
        )

    indicators = facts["indicators"]
    for name, item in indicators.items():
        if name in {"volume_ma5", "volume_ma10"}:
            continue
        value = float(item["value"])
        add(
            f"{value:.4f}",
            label=name,
            pointer=f"/indicators/{name}/value",
            value=value,
            price_basis=str(item["price_basis"]),
            unit=str(item["unit"]),
            transform="fixed_4_decimal",
        )

    for section in ("minute_1m", "minute_30m"):
        value = int(facts[section]["bar_count"])
        add(
            str(value),
            label=f"{section}_bar_count",
            pointer=f"/{section}/bar_count",
            value=value,
            price_basis="none",
            unit="bars",
            transform="integer",
        )

    factor_count = int(facts["adjustment_factors"]["factor_count"])
    add(
        str(factor_count),
        label="adjustment_factor_count",
        pointer="/adjustment_factors/factor_count",
        value=factor_count,
        price_basis="qfq",
        unit="factors",
        transform="integer",
    )
    return dict(sorted(catalog.items()))


def _system_prompt() -> str:
    headings = "\n".join(
        f"### {index}. {heading}" for index, heading in enumerate(REVIEW_HEADINGS, 1)
    )
    forbidden = "、".join(FORBIDDEN_TERMS)
    return f"""你是 TickFlow 的个股日线研究素材编辑，不是交易顾问。

仅能使用用户消息中的确定性 Facts 投影。不得计算、插值、四舍五入或创造新数字。如需引用数字，只能逐字复制 `allowed_numeric_claims` 中的 `literal`。不得把 raw 价格与 qfq 指标直接比较，不得给出未实现的关键价位。

正文中绝对不得出现以下字样，即使是否定句也不得输出：{forbidden}。不得给出任何交易指令、价格预测或个人化倾向。

财务、新闻和公告未接入，只能按指定句子声明不可用，不得推断。市场范围只有单只股票，不得扩展为全市场或行业结论。

仅输出 Markdown 正文，不要输出 YAML frontmatter、文档总标题、JSON、代码块或额外章节。必须严格按下列标题顺序各输出一次：

{headings}

第八节必须逐字包含三条范围声明和全部 `vendor_pending`。第十节只输出指定免责句。语言保持客观、简洁、可人工复核。""".strip()


def build_review_prompt(facts: Mapping[str, Any], facts_sha256: str) -> ReviewPrompt:
    """Build the model prompt without exposing raw unit-pending quantities."""
    catalog = build_numeric_claim_catalog(facts, facts_sha256)
    allowed = []
    for literal, sources in catalog.items():
        allowed.append(
            {
                "literal": literal,
                "claims": [
                    {
                        "label": source.label,
                        "facts_pointer": source.facts_pointer,
                        "price_basis": source.price_basis,
                        "unit": source.unit,
                    }
                    for source in sources
                ],
            }
        )
    projection = {
        "facts_sha256": facts_sha256,
        "symbol": facts["symbol"],
        "name": facts["name"],
        "trade_date": facts["trade_date"],
        "timezone": facts["timezone"],
        "price_basis": facts["price_basis"],
        "allowed_numeric_claims": allowed,
        "minute_contract": {
            "one_minute_status": facts["minute_1m"]["status"],
            "thirty_minute_status": facts["minute_30m"]["status"],
            "ohlc_contract_passed": facts["minute_30m"]["ohlc_mismatch_count"] == 0,
            "first_bucket_mechanically_explained": facts["minute_30m"][
                "first_bucket_mechanically_explained"
            ],
            "vendor_contract_confirmed": facts["minute_30m"][
                "vendor_contract_confirmed"
            ],
        },
        "key_levels": facts["key_levels"],
        "scope": facts["scope"],
        "units": facts["units"],
        "vendor_pending": facts["vendor_pending"],
        "required_scope_lines": REQUIRED_SCOPE_LINES,
        "required_disclaimer": DISCLAIMER,
    }
    user = (
        "请为下列单标的 Facts 投影生成研究素材。"
        "只能使用此 JSON 中的内容，并严格复制允许的数字字面量。\n\n"
        + json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True)
    )
    system = _system_prompt()
    prompt_hash = _sha256_bytes(
        canonical_json_bytes({"system": system, "user": user})
    )
    return ReviewPrompt(
        system=system,
        user=user,
        facts_sha256=facts_sha256,
        prompt_sha256=prompt_hash,
    )


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in occupied)


def _numeric_claims(
    body: str,
    facts: Mapping[str, Any],
    facts_sha256: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    catalog = build_numeric_claim_catalog(facts, facts_sha256)
    matched: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []

    for match in _HEADING_RE.finditer(body):
        number_start = match.start(1)
        occupied.append((number_start, match.end(1)))

    for match in _DATE_RE.finditer(body):
        occupied.append(match.span())
        literal = match.group(0)
        if literal == facts["trade_date"]:
            matched.append(
                {
                    "literal": literal,
                    "facts_pointer": "/trade_date",
                    "facts_sha256": facts_sha256,
                    "display_transform": "identity",
                }
            )
        else:
            unsupported.append(
                {"literal": literal, "offset": match.start(), "reason": "date_not_in_facts"}
            )

    for match in _SYMBOL_RE.finditer(body):
        occupied.append(match.span())
        literal = match.group(0)
        if literal == facts["symbol"]:
            matched.append(
                {
                    "literal": literal,
                    "facts_pointer": "/symbol",
                    "facts_sha256": facts_sha256,
                    "display_transform": "identity",
                }
            )
        else:
            unsupported.append(
                {"literal": literal, "offset": match.start(), "reason": "symbol_out_of_scope"}
            )

    for match in _TIME_RE.finditer(body):
        if _overlaps(match.span(), occupied):
            continue
        occupied.append(match.span())
        unsupported.append(
            {"literal": match.group(0), "offset": match.start(), "reason": "time_not_allowed"}
        )

    for match in _NUMBER_RE.finditer(body):
        if _overlaps(match.span(), occupied):
            continue
        literal = match.group(0)
        if match.end() < len(body) and body[match.end()] in {"%", "％"}:
            unsupported.append(
                {
                    "literal": literal + body[match.end()],
                    "offset": match.start(),
                    "reason": "percentage_not_in_catalog",
                }
            )
            continue
        sources = catalog.get(literal)
        if not sources:
            unsupported.append(
                {"literal": literal, "offset": match.start(), "reason": "literal_not_in_catalog"}
            )
            continue
        for source in sources:
            matched.append(asdict(source))
    return matched, unsupported


def _fabricated_sentence_count(body: str, pattern: re.Pattern[str]) -> int:
    count = 0
    for sentence in re.split(r"[\n。！？]+", body):
        if pattern.search(sentence) and not any(marker in sentence for marker in _UNAVAILABLE_MARKERS):
            count += 1
    return count


def _rejected_validation(
    errors: list[str],
    *,
    unsupported: list[dict[str, Any]] | None = None,
    matched: list[dict[str, Any]] | None = None,
    forbidden: list[str] | None = None,
    secrets: list[str] | None = None,
    financial_count: int = 0,
    news_count: int = 0,
    needs_verification: list[str] | None = None,
) -> ReviewValidation:
    return ReviewValidation(
        status=REVIEW_REJECTED,
        errors=sorted(set(errors)),
        unsupported_numeric_claims=unsupported or [],
        matched_numeric_claims=matched or [],
        forbidden_terms=forbidden or [],
        secret_hits=secrets or [],
        financial_fabrication_count=financial_count,
        news_fabrication_count=news_count,
        needs_verification=needs_verification or [],
        can_publish=False,
    )


def validate_review_body(
    body: str,
    facts: Mapping[str, Any],
    facts_sha256: str,
) -> ReviewValidation:
    """Validate model-authored Markdown before frontmatter or preview routing."""
    _require_facts_shape(facts, facts_sha256)
    errors: list[str] = []
    if not isinstance(body, str) or not body.strip():
        return _rejected_validation(["body_empty"])
    if body.lstrip().startswith("---"):
        errors.append("model_frontmatter_forbidden")
    if "```" in body:
        errors.append("code_fence_forbidden")
    if re.search(r"<\s*(?:script|iframe|object)\b", body, re.IGNORECASE):
        errors.append("active_html_forbidden")

    headings = [(int(number), title) for number, title in _HEADING_RE.findall(body)]
    expected_headings = list(enumerate(REVIEW_HEADINGS, 1))
    if headings != expected_headings:
        errors.append("heading_contract_invalid")

    for required in REQUIRED_SCOPE_LINES:
        if required not in body:
            errors.append(f"required_scope_missing:{required}")
    if DISCLAIMER not in body:
        errors.append("disclaimer_missing")
    for item in VENDOR_PENDING:
        if item not in body:
            errors.append(f"vendor_pending_missing:{item}")

    forbidden = [term for term in FORBIDDEN_TERMS if term in body]
    if forbidden:
        errors.extend(f"forbidden_term:{term}" for term in forbidden)
    secret_hits = [name for name, pattern in _SECRET_PATTERNS.items() if pattern.search(body)]
    if secret_hits:
        errors.extend(f"secret_shape:{name}" for name in secret_hits)

    financial_count = _fabricated_sentence_count(body, _FINANCIAL_ASSERTION_RE)
    news_count = _fabricated_sentence_count(body, _NEWS_ASSERTION_RE)
    if financial_count:
        errors.append("financial_fabrication_detected")
    if news_count:
        errors.append("news_fabrication_detected")

    matched, unsupported = _numeric_claims(body, facts, facts_sha256)
    if unsupported:
        errors.append("unsupported_numeric_claim_detected")

    needs_verification = [
        "financial_data_unavailable",
        "news_data_unavailable",
        "key_levels_not_implemented",
        *VENDOR_PENDING,
    ]
    if errors:
        return _rejected_validation(
            errors,
            unsupported=unsupported,
            matched=matched,
            forbidden=forbidden,
            secrets=secret_hits,
            financial_count=financial_count,
            news_count=news_count,
            needs_verification=needs_verification,
        )
    return ReviewValidation(
        status=REVIEW_NEEDS_VERIFICATION,
        errors=[],
        unsupported_numeric_claims=[],
        matched_numeric_claims=matched,
        forbidden_terms=[],
        secret_hits=[],
        financial_fabrication_count=0,
        news_fabrication_count=0,
        needs_verification=needs_verification,
        can_publish=False,
    )


def _frontmatter(
    facts: Mapping[str, Any], generated_at: str, review_status: str
) -> dict[str, Any]:
    return {
        "type": "stock-daily-review",
        "source_system": "tickflow-stock-panel",
        "facts_schema_version": FACTS_SCHEMA_VERSION,
        "symbol": facts["symbol"],
        "name": facts["name"],
        "timeframe": ["1d", "30m"],
        "as_of": facts["trade_date"],
        "latest_trade_date": facts["trade_date"],
        "generated_at": generated_at,
        "data_scope": "single-symbol",
        "market_scope": "incomplete",
        "financial_scope": "unavailable",
        "news_scope": "unavailable",
        "data_freshness": "fresh",
        "verification_status": "pending",
        "review_status": review_status,
        "can_publish": False,
        "trading_advice": False,
        "vendor_pending": list(VENDOR_PENDING),
    }


def _validate_generated_at(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise Phase2ReviewError("generated_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Phase2ReviewError("generated_at must include a timezone offset")


def render_review_document(
    body: str,
    facts: Mapping[str, Any],
    facts_sha256: str,
    generated_at: str,
) -> str:
    """Attach deterministic safety frontmatter to one model-authored body."""
    _validate_generated_at(generated_at)
    validation = validate_review_body(body, facts, facts_sha256)
    frontmatter = _frontmatter(facts, generated_at, validation.status)
    yaml_text = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    ).strip()
    return f"---\n{yaml_text}\n---\n{body.strip()}\n"


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing frontmatter", node.start_mark, "duplicate key", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _split_document(document: str) -> tuple[dict[str, Any], str]:
    if not document.startswith("---\n"):
        raise Phase2ReviewError("frontmatter opening delimiter is missing")
    try:
        yaml_text, body = document[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise Phase2ReviewError("frontmatter closing delimiter is missing") from exc
    try:
        value = yaml.load(yaml_text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise Phase2ReviewError("frontmatter YAML is invalid") from exc
    if not isinstance(value, dict):
        raise Phase2ReviewError("frontmatter must be a mapping")
    return value, body


def validate_review_document(
    document: str,
    facts: Mapping[str, Any],
    facts_sha256: str,
) -> ReviewValidation:
    """Validate deterministic frontmatter and the contained model body."""
    try:
        frontmatter, body = _split_document(document)
    except Phase2ReviewError as exc:
        return _rejected_validation([f"frontmatter_invalid:{exc}"])
    body_validation = validate_review_body(body, facts, facts_sha256)
    errors = list(body_validation.errors)

    expected_keys = set(_frontmatter(facts, "placeholder", body_validation.status))
    if set(frontmatter) != expected_keys:
        errors.append("frontmatter_keys_invalid")
    for key, expected in _frontmatter(
        facts,
        str(frontmatter.get("generated_at", "")),
        body_validation.status,
    ).items():
        if frontmatter.get(key) != expected:
            errors.append(f"frontmatter_{key}_invalid")
    try:
        _validate_generated_at(str(frontmatter.get("generated_at", "")))
    except Phase2ReviewError:
        errors.append("frontmatter_generated_at_invalid")

    if errors:
        return _rejected_validation(
            errors,
            unsupported=body_validation.unsupported_numeric_claims,
            matched=body_validation.matched_numeric_claims,
            forbidden=body_validation.forbidden_terms,
            secrets=body_validation.secret_hits,
            financial_count=body_validation.financial_fabrication_count,
            news_count=body_validation.news_fabrication_count,
            needs_verification=body_validation.needs_verification,
        )
    return body_validation


def route_review_preview(status: str) -> Literal["inbox", "rejected"]:
    if status in {REVIEW_VALID, REVIEW_NEEDS_VERIFICATION}:
        return "inbox"
    if status == REVIEW_REJECTED:
        return "rejected"
    raise Phase2ReviewError(f"unknown review status: {status}")


def _safe_facts_path(repo_root: Path, symbol: str) -> Path:
    root = repo_root.resolve(strict=True)
    path = root / "reports/phase2_facts" / f'{symbol.replace(".", "")}_facts.json'
    if path.is_symlink():
        raise Phase2ReviewError(f"Facts path must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise Phase2ReviewError(f"Facts path is invalid: {path}") from exc
    if not resolved.is_file():
        raise Phase2ReviewError(f"Facts path is not a regular file: {path}")
    return resolved


def _write_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


async def run_review_batch(
    repo_root: Path,
    reports_root: Path,
    provider: ReviewProvider,
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    """Invoke one provider attempt per symbol and publish a complete batch."""
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise Phase2ReviewError("generated_at must be timezone-aware")
    generated_at_text = generated_at.isoformat(timespec="seconds")
    repo_root = repo_root.resolve(strict=True)
    if reports_root.is_symlink():
        raise Phase2ReviewError("reports root must not be a symlink")
    reports_root.mkdir(parents=True, exist_ok=True)
    reports_root = reports_root.resolve(strict=True)

    entries: list[dict[str, Any]] = []
    documents: dict[str, bytes] = {}
    for symbol, name in FIXED_SYMBOLS.items():
        facts_path = _safe_facts_path(repo_root, symbol)
        facts_bytes = facts_path.read_bytes()
        facts_sha256 = _sha256_bytes(facts_bytes)
        try:
            facts = json.loads(facts_bytes)
        except json.JSONDecodeError as exc:
            raise Phase2ReviewError(f"Facts JSON is invalid for {symbol}") from exc
        facts_errors = validate_facts_document(repo_root, facts)
        if facts_errors:
            raise Phase2ReviewError(f"Facts validation failed for {symbol}: {facts_errors}")
        prompt = build_review_prompt(facts, facts_sha256)
        try:
            provider_result = await provider(prompt, symbol)
        except Exception as exc:
            raise Phase2ReviewError(f"{symbol} provider failed: {exc}") from exc
        if not isinstance(provider_result, ProviderResult):
            raise Phase2ReviewError(f"{symbol} provider returned an invalid result")
        if not provider_result.text.strip():
            raise Phase2ReviewError(f"{symbol} provider returned empty text")
        if provider_result.duration_ms < 0:
            raise Phase2ReviewError(f"{symbol} provider duration is invalid")

        document = render_review_document(
            provider_result.text,
            facts,
            facts_sha256,
            generated_at_text,
        )
        validation = validate_review_document(document, facts, facts_sha256)
        document_bytes = document.encode("utf-8")
        filename = f'{symbol.replace(".", "")}_{name}.md'
        documents[filename] = document_bytes
        entries.append(
            {
                "symbol": symbol,
                "name": name,
                "provider": provider_result.provider,
                "model": provider_result.model,
                "model_source": provider_result.model_source,
                "provider_attempt_count": 1,
                "retry_count": 0,
                "duration_ms": provider_result.duration_ms,
                "input_facts_path": facts_path.relative_to(repo_root).as_posix(),
                "input_facts_sha256": facts_sha256,
                "prompt_sha256": prompt.prompt_sha256,
                "ai_body_sha256": _sha256_bytes(provider_result.text.strip().encode("utf-8")),
                "output_markdown_sha256": _sha256_bytes(document_bytes),
                "review_status": validation.status,
                "can_publish": False,
                "unsupported_numeric_claim_count": len(
                    validation.unsupported_numeric_claims
                ),
                "forbidden_term_count": len(validation.forbidden_terms),
                "financial_fabrication_count": validation.financial_fabrication_count,
                "news_fabrication_count": validation.news_fabrication_count,
                "secret_hit_count": len(validation.secret_hits),
                "validation": validation.to_dict(),
                "preview_route": route_review_preview(validation.status),
            }
        )

    blocked = any(entry["review_status"] == REVIEW_REJECTED for entry in entries)
    status = PHASE2_OUTPUT_BLOCKED if blocked else PHASE2_READY
    audit = {
        "schema_version": 1,
        "status": status,
        "generated_at": generated_at_text,
        "symbol_scope": list(FIXED_SYMBOLS),
        "provider_attempt_policy": "exactly_once_no_retry",
        "provider_attempt_count": len(entries),
        "retry_count": 0,
        "tickflow_api_request_count": 0,
        "cloud_mutation_count": 0,
        "cloud_redeploy": False,
        "application_ai_config_changed": False,
        "paper_trading_started": False,
        "obsidian_real_vault_write": False,
        "integrated_gold_enabled": False,
        "external_send_count": 0,
        "symbols": entries,
    }
    validation_manifest = {
        "schema_version": 1,
        "status": status,
        "can_publish": False,
        "reviewed_auto_write": False,
        "inbox_count": sum(entry["preview_route"] == "inbox" for entry in entries),
        "rejected_count": sum(entry["preview_route"] == "rejected" for entry in entries),
        "entries": [
            {
                "symbol": entry["symbol"],
                "review_status": entry["review_status"],
                "route": entry["preview_route"],
                "output_markdown_sha256": entry["output_markdown_sha256"],
                "unsupported_numeric_claim_count": entry[
                    "unsupported_numeric_claim_count"
                ],
                "forbidden_term_count": entry["forbidden_term_count"],
                "financial_fabrication_count": entry[
                    "financial_fabrication_count"
                ],
                "news_fabrication_count": entry["news_fabrication_count"],
                "secret_hit_count": entry["secret_hit_count"],
            }
            for entry in entries
        ],
    }

    samples_stage = Path(
        tempfile.mkdtemp(prefix=".phase2_ai_samples.staging-", dir=reports_root)
    )
    preview_stage = Path(
        tempfile.mkdtemp(prefix=".phase2_obsidian_preview.staging-", dir=reports_root)
    )
    try:
        for filename, document_bytes in documents.items():
            _write_durable(samples_stage / filename, document_bytes)
        _write_durable(
            samples_stage / "generation_audit.json", canonical_json_bytes(audit)
        )

        for route in ("inbox", "reviewed", "rejected"):
            (preview_stage / route).mkdir(parents=True, exist_ok=True)
        for entry in entries:
            filename = f'{entry["symbol"].replace(".", "")}_{entry["name"]}.md'
            _write_durable(
                preview_stage / entry["preview_route"] / filename,
                documents[filename],
            )
        _write_durable(
            preview_stage / "validation_manifest.json",
            canonical_json_bytes(validation_manifest),
        )

        atomic_publish_directory(samples_stage, reports_root / "phase2_ai_samples")
        atomic_publish_directory(
            preview_stage, reports_root / "phase2_obsidian_preview"
        )
    finally:
        shutil.rmtree(samples_stage, ignore_errors=True)
        shutil.rmtree(preview_stage, ignore_errors=True)
    return audit


def _load_regular_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase2ReviewError(f"{label} must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase2ReviewError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise Phase2ReviewError(f"{label} must contain a JSON object")
    return value


def validate_review_delivery(
    repo_root: Path,
    reports_root: Path,
) -> dict[str, Any]:
    """Offline validation for the complete samples and preview delivery."""
    repo_root = repo_root.resolve(strict=True)
    reports_root = reports_root.resolve(strict=True)
    samples_root = reports_root / "phase2_ai_samples"
    preview_root = reports_root / "phase2_obsidian_preview"
    errors: list[str] = []
    if samples_root.is_symlink() or not samples_root.is_dir():
        return {
            "status": PHASE2_OUTPUT_BLOCKED,
            "errors": ["samples_directory_invalid"],
        }
    if preview_root.is_symlink() or not preview_root.is_dir():
        return {
            "status": PHASE2_OUTPUT_BLOCKED,
            "errors": ["preview_directory_invalid"],
        }

    try:
        audit = _load_regular_json(
            samples_root / "generation_audit.json", label="generation audit"
        )
        preview_manifest = _load_regular_json(
            preview_root / "validation_manifest.json",
            label="preview validation manifest",
        )
    except Phase2ReviewError as exc:
        return {"status": PHASE2_OUTPUT_BLOCKED, "errors": [str(exc)]}

    audit_entries = audit.get("symbols")
    if not isinstance(audit_entries, list):
        audit_entries = []
        errors.append("audit_symbols_invalid")
    audit_by_symbol = {
        item.get("symbol"): item
        for item in audit_entries
        if isinstance(item, dict) and isinstance(item.get("symbol"), str)
    }
    expected_filenames: set[str] = set()
    validations: list[ReviewValidation] = []
    for symbol, name in FIXED_SYMBOLS.items():
        filename = f'{symbol.replace(".", "")}_{name}.md'
        expected_filenames.add(filename)
        facts_path = _safe_facts_path(repo_root, symbol)
        facts_bytes = facts_path.read_bytes()
        facts_sha256 = _sha256_bytes(facts_bytes)
        try:
            facts = json.loads(facts_bytes)
        except json.JSONDecodeError:
            errors.append(f"facts_json_invalid:{symbol}")
            continue
        facts_errors = validate_facts_document(repo_root, facts)
        if facts_errors:
            errors.append(f"facts_invalid:{symbol}")
            continue

        sample_path = samples_root / filename
        if sample_path.is_symlink() or not sample_path.is_file():
            errors.append(f"sample_missing:{symbol}")
            continue
        document = sample_path.read_text(encoding="utf-8")
        validation = validate_review_document(document, facts, facts_sha256)
        validations.append(validation)
        expected_route = route_review_preview(validation.status)
        preview_path = preview_root / expected_route / filename
        if preview_path.is_symlink() or not preview_path.is_file():
            errors.append(f"preview_route_missing:{symbol}:{expected_route}")
        elif preview_path.read_bytes() != sample_path.read_bytes():
            errors.append(f"preview_sample_hash_mismatch:{symbol}")

        opposite_route = "rejected" if expected_route == "inbox" else "inbox"
        if (preview_root / opposite_route / filename).exists():
            errors.append(f"preview_duplicate_route:{symbol}")

        audit_entry = audit_by_symbol.get(symbol)
        if not isinstance(audit_entry, dict):
            errors.append(f"audit_entry_missing:{symbol}")
            continue
        actual_output_hash = _sha256_file(sample_path)
        expected_audit = {
            "name": name,
            "provider_attempt_count": 1,
            "retry_count": 0,
            "input_facts_sha256": facts_sha256,
            "output_markdown_sha256": actual_output_hash,
            "review_status": validation.status,
            "can_publish": False,
            "unsupported_numeric_claim_count": len(
                validation.unsupported_numeric_claims
            ),
            "forbidden_term_count": len(validation.forbidden_terms),
            "financial_fabrication_count": validation.financial_fabrication_count,
            "news_fabrication_count": validation.news_fabrication_count,
            "secret_hit_count": len(validation.secret_hits),
            "preview_route": expected_route,
        }
        for key, expected in expected_audit.items():
            if audit_entry.get(key) != expected:
                errors.append(f"audit_mismatch:{symbol}:{key}")
        if audit_entry.get("validation") != validation.to_dict():
            errors.append(f"audit_validation_mismatch:{symbol}")
        if audit_entry.get("provider") != "codex_cli":
            errors.append(f"audit_provider_invalid:{symbol}")
        if not isinstance(audit_entry.get("model"), str) or not audit_entry.get(
            "model"
        ):
            errors.append(f"audit_model_invalid:{symbol}")
        if not isinstance(audit_entry.get("model_source"), str) or not audit_entry.get(
            "model_source"
        ):
            errors.append(f"audit_model_source_invalid:{symbol}")

    actual_sample_names = {
        path.name
        for path in samples_root.iterdir()
        if path.is_file() and path.suffix == ".md"
    }
    if actual_sample_names != expected_filenames:
        errors.append("sample_filename_set_invalid")
    reviewed_root = preview_root / "reviewed"
    reviewed_files = (
        [path for path in reviewed_root.rglob("*") if path.is_file()]
        if reviewed_root.is_dir() and not reviewed_root.is_symlink()
        else []
    )
    if reviewed_files:
        errors.append("reviewed_directory_must_be_empty")

    if audit.get("symbol_scope") != list(FIXED_SYMBOLS):
        errors.append("audit_symbol_scope_invalid")
    if audit.get("provider_attempt_policy") != "exactly_once_no_retry":
        errors.append("audit_attempt_policy_invalid")
    if audit.get("provider_attempt_count") != len(FIXED_SYMBOLS):
        errors.append("audit_attempt_count_invalid")
    if audit.get("retry_count") != 0:
        errors.append("audit_retry_count_nonzero")
    if audit.get("tickflow_api_request_count") != 0:
        errors.append("tickflow_api_request_count_nonzero")
    if audit.get("cloud_mutation_count") != 0:
        errors.append("cloud_mutation_count_nonzero")
    for key in (
        "cloud_redeploy",
        "application_ai_config_changed",
        "paper_trading_started",
        "obsidian_real_vault_write",
        "integrated_gold_enabled",
    ):
        if audit.get(key) is not False:
            errors.append(f"audit_boundary_invalid:{key}")
    if audit.get("external_send_count") != 0:
        errors.append("external_send_count_nonzero")

    expected_ready = bool(validations) and all(
        item.status in {REVIEW_VALID, REVIEW_NEEDS_VERIFICATION}
        for item in validations
    )
    computed_status = PHASE2_READY if expected_ready and not errors else PHASE2_OUTPUT_BLOCKED
    if audit.get("status") != computed_status:
        errors.append("audit_status_mismatch")
        computed_status = PHASE2_OUTPUT_BLOCKED
    if preview_manifest.get("status") != audit.get("status"):
        errors.append("preview_manifest_status_mismatch")
        computed_status = PHASE2_OUTPUT_BLOCKED
    if preview_manifest.get("reviewed_auto_write") is not False:
        errors.append("preview_reviewed_auto_write_invalid")
        computed_status = PHASE2_OUTPUT_BLOCKED
    if preview_manifest.get("can_publish") is not False:
        errors.append("preview_can_publish_invalid")
        computed_status = PHASE2_OUTPUT_BLOCKED

    return {
        "status": computed_status,
        "errors": sorted(set(errors)),
        "symbol_count": len(validations),
        "unsupported_numeric_claim_count": sum(
            len(item.unsupported_numeric_claims) for item in validations
        ),
        "forbidden_term_count": sum(
            len(item.forbidden_terms) for item in validations
        ),
        "financial_fabrication_count": sum(
            item.financial_fabrication_count for item in validations
        ),
        "news_fabrication_count": sum(
            item.news_fabrication_count for item in validations
        ),
        "secret_hit_count": sum(len(item.secret_hits) for item in validations),
        "reviewed_file_count": len(reviewed_files),
        "provider_attempt_count": audit.get("provider_attempt_count"),
        "retry_count": audit.get("retry_count"),
        "tickflow_api_request_count": audit.get("tickflow_api_request_count"),
        "cloud_mutation_count": audit.get("cloud_mutation_count"),
        "cloud_redeploy": audit.get("cloud_redeploy"),
        "application_ai_config_changed": audit.get(
            "application_ai_config_changed"
        ),
        "paper_trading_started": audit.get("paper_trading_started"),
        "obsidian_real_vault_write": audit.get("obsidian_real_vault_write"),
        "integrated_gold_enabled": audit.get("integrated_gold_enabled"),
        "external_send_count": audit.get("external_send_count"),
    }
