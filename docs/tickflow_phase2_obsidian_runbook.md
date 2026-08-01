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
├── audit_history/
└── generation_audit.json

reports/phase2_obsidian_preview/
├── inbox/
├── reviewed/
├── rejected/
└── validation_manifest.json
```

路由规则：

- 只有未来结构化 Claims 合同可能产生的 `REVIEW_VALID` 才进入 `inbox`。
- `REVIEW_NEEDS_VERIFICATION` 和 `REVIEW_REJECTED` 均进入 `rejected`。
- 程序没有写入 `reviewed` 的路径。
- 本阶段没有真实 Vault 目录参数。

## 离线验证

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-phase2-ai-review/backend"

PYTHONPATH=. .venv/bin/python scripts/validate_phase2_ai_review.py \
  --repo-root .. \
  --reports-root ../reports
```

当前批次的预期状态是 `PHASE2_AI_OUTPUT_BLOCKED`。自由文本检测器
无法证明语义完整性，因此三份样本全部保留在 `rejected`；其中
东方财富还含一处 raw 收盘与 qfq 均线的跨口径比较。不应手工
改写 frontmatter 绕过拒绝。

## 人工复核

1. 先打开 `reports/phase2_manual_review_checklist.md`。
2. 核对代码、名称、日期、raw 收盘、qfq 指标及分钟合同。
3. 确认财务、新闻、公告和关键价位没有被冒充为已知事实。
4. 确认没有 raw/qfq 跨口径比较和任何交易指令。
5. 仅人工审阅者可以手动复制合格材料；本工具不会自动移入
   `reviewed` 或真实 Vault。

## 未来新批次

当前三票已完成历史一次性 Codex CLI 冒烟，不得重跑刷新本批次。
本分支不再提供自动 Codex 生成命令：CLI 的 `read-only` 模式只能限制
写入，不能证明无法读取宿主其他文件。

未来新批次必须先满足：

1. 定义类型化 Claims 输出合同，每个断言都有 Facts 指针和口径；
2. 只有结构化 Claims 校验通过才允许产生 `REVIEW_VALID`；
3. 用外部容器或 OS 级策略限制可读路径；
4. 只挂载三份 Facts 投影和临时输出目录；
5. 不挂载应用 Secret、用户主目录或真实 Obsidian Vault；
6. 继续执行每票单次、零重试和离线验证。

写入路径固定为仓库内 `reports/`；运行时参数不允许指向真实
Obsidian Vault。

## 供应商待确认

- `intraday_batch_entitlement`
- `first_30m_bucket_includes_09_30`
- `volume_unit`
- `amount_unit`

以上项目不影响当前三票的相对指标研究，但在扩展数据范围或进入
Paper Trading 前必须继续保留。
