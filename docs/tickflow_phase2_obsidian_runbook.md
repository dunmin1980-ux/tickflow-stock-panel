# TickFlow Phase 2 Obsidian 旁路预览手册

## 定位

Phase 2 只生成个股日线研究素材，不是交易建议，不会写入真实
Obsidian Vault、金融日报、Telegram 或 OpenClaw。所有文档固定：

```yaml
verification_status: pending
can_publish: false
trading_advice: false
```

## 目录

```text
reports/phase2_ai_samples/
├── 000403SZ_派林生物.md
├── 600489SH_中金黄金.md
├── 300059SZ_东方财富.md
└── generation_audit.json

reports/phase2_obsidian_preview/
├── inbox/
├── reviewed/
├── rejected/
└── validation_manifest.json
```

路由规则：

- `REVIEW_VALID` 和 `REVIEW_NEEDS_VERIFICATION` 进入 `inbox`。
- `REVIEW_REJECTED` 进入 `rejected`。
- 程序没有写入 `reviewed` 的路径。
- 本阶段没有真实 Vault 目录参数。

## 离线验证

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-phase2-ai-review/backend"

PYTHONPATH=. .venv/bin/python scripts/validate_phase2_ai_review.py \
  --repo-root .. \
  --reports-root ../reports
```

当前批次的预期状态是 `PHASE2_AI_OUTPUT_BLOCKED`：东方财富样本含一处
raw 收盘与 qfq 均线的跨口径比较，因此保留在 `rejected`。这是正常的
安全拒绝，不应手工忽略。

## 人工复核

1. 先打开 `reports/phase2_manual_review_checklist.md`。
2. 核对代码、名称、日期、raw 收盘、qfq 指标及分钟合同。
3. 确认财务、新闻、公告和关键价位没有被冒充为已知事实。
4. 确认没有 raw/qfq 跨口径比较和任何交易指令。
5. 仅人工审阅者可以手动复制合格材料；本工具不会自动移入
   `reviewed` 或真实 Vault。

## 未来新批次

当前三票已各调用 Codex CLI 一次，不得重跑刷新本批次。只有在用户
明确批准新的 AI 观察批次后，才可使用：

```bash
PYTHONPATH=. .venv/bin/python scripts/generate_phase2_ai_reviews.py \
  --repo-root .. \
  --reports-root ../reports \
  --provider codex_cli
```

生成器使用空工作目录、只读 sandbox、隔离的临时 `CODEX_HOME` 和环境变量
白名单。它不更改应用 AI provider，不配置 AI Key，不调用 TickFlow API，也
不会重试失败的调用。

## 供应商待确认

- `intraday_batch_entitlement`
- `first_30m_bucket_includes_09_30`
- `volume_unit`
- `amount_unit`

以上项目不影响当前三票的相对指标研究，但在扩展数据范围或进入
Paper Trading 前必须继续保留。
