# TickFlow Phase 2 AI 个股复盘与 Obsidian 旁路评估

## 1. 最终状态

```text
PHASE2_AI_OUTPUT_BLOCKED
```

Phase 2A Facts 均已通过，三票均完成了历史单次 Codex CLI 生成。
自由文本检测器可以拦截已知数字、范围和措辞问题，但无法证明所有自然语言
断言均受 Facts 支持。因此本轮改为失败关闭：所有自由文本样本均标记
`REVIEW_REJECTED` 并仅进入 `rejected`；东方财富另有一处 raw 与 qfq 跨口径
比较命中。本批次不再调用 AI。未来必须先实现类型化 Claims 合同，并
建立能限制宿主读取的外部隔离。

## 2. Phase 1 治理与 Phase 2 基线

- Phase 1 PR：[#2](https://github.com/dunmin1980-ux/tickflow-stock-panel/pull/2)，`MERGED`。
- Phase 1 验证 Head：`3af4185b0342c9f274c2ba7b8cd18e6c2c8745dd`。
- Phase 2 Base / Phase 1 merge commit：`ce1f0b221cfd390cafc92a26ef8e16cf7dd3a753`。
- Phase 1 Head 已进入 Base：`YES`。
- 治理标签：`phase1-observation-passed-20260731`。
- Phase 2 基线后端：`1349 passed`；前端：`97 passed`。

## 3. Facts 结果

| 标的 | Facts | 指标来源 | raw/qfq | 无来源数字 |
|---|---|---|---|---:|
| 派林生物 `000403.SZ` | VALID | TRACEABLE | SEPARATED | 0 |
| 中金黄金 `600489.SH` | VALID | TRACEABLE | SEPARATED | 0 |
| 东方财富 `300059.SZ` | VALID | TRACEABLE | SEPARATED | 0 |

MA、MACD、RSI、BOLL、ATR 和相对量能指标均由
`app.indicators.pipeline.compute_indicators` 确定性计算。每个数字有来源文件
SHA-256、JSON Pointer、计算函数、参数和价格口径。成交量与成交额单位仍为
`VENDOR_CONFIRMATION_PENDING`，关键价位仍为 `NOT_IMPLEMENTED`。

## 4. AI 调用证据

- provider：`codex_cli`。
- model audit alias：`codex_cli_default`；历史运行未记录真实模型名。
- model identity：`NOT_VERIFIED`，证据标记为 `not_recorded_cli_default_alias`。
- 历史 model source 字段：`isolated_codex_cli_default_configuration`，仅作当时进程记录，不代表宿主读取隔离证明。
- 应用 AI provider：`openai_compat`，`has_ai_key=false`，本轮未修改。
- provider 调用：进程内记录 `3`，每票 `1`次；非外部计费或系统级证明。
- retry：`0`。
- 离线重物化：`5`次，新增 AI 调用 `0`，新增 provider 尝试 `0`。
- TickFlow API 新请求：`0`。
- 云端修改：`0`。

第三至五次离线重物化用于应用安全加固后的校验规则、frontmatter、路由、
审计链和自由文本失败关闭策略同步。三份 AI 正文 SHA-256 在每次重物化前
均已核对，正文没有改写。
本次开始把立即前驱审计原文按 SHA-256 保存到 `audit_history/`；更早两次
重物化的前驱文件当时未保留，因此 `audit_history_complete=false`。

## 5. 三票 AI 输出

| 标的 | 状态 | 无来源数字检测命中 | 交易词检测命中 | 财务断言检测命中 | 新闻断言检测命中 | raw/qfq 混用 | 路由 |
|---|---|---:|---:|---:|---:|---:|---|
| 派林生物 | REVIEW_REJECTED | 0 | 0 | 0 | 0 | 0 | rejected |
| 中金黄金 | REVIEW_REJECTED | 0 | 0 | 0 | 0 | 0 | rejected |
| 东方财富 | REVIEW_REJECTED | 0 | 0 | 0 | 0 | 1 | rejected |

派林生物和中金黄金的已知检测器命中数为 0，但仍因
`freeform_semantic_validation_incomplete` 被拒绝。东方财富拒绝原因是正文把
“日线收盘”与“前复权均线簇”做了相对比较。Facts 明确日线收盘为 raw，
指标价格为 qfq；两者不得直接比较。拒绝不会被自动改写或手工改
frontmatter 规避。

## 6. 数字与范围校验

模型只获得 Facts 投影和确定性数字目录。数字目录对每个可引用数字记录：

- Facts SHA-256；
- Facts JSON Pointer；
- raw / qfq / none 口径；
- 单位和展示变换。

单位未确认的 volume / amount 绝对值没有放入 AI 可引用目录。文档校验同时
覆盖章节顺序、禁止词、数字、日期、股票代码、财务/新闻断言、范围声明、
vendor pending、敏感形态和 raw/qfq 跨口径关系。加固后还覆盖
Unicode 零宽字符、嵌入数字、空格百分号、科学计数、千分位、数值与
字段语义错配、HTML 注释和额外 Markdown 标题。

这些结果是确定性规则的“检测命中数”，不是对自然语言语义的完整证明。
因此即使命中为 0，自由文本文档仍必须失败关闭。当前没有自由文本路径
可产生 `REVIEW_VALID`；需要类型化 Claims 合同才能建立可验证的语义所有权。

## 7. Markdown 与 Obsidian 旁路

三份 Markdown 均包含固定 frontmatter：

```yaml
type: stock-daily-review
source_system: tickflow-stock-panel
facts_schema_version: 1
data_scope: single-symbol
market_scope: incomplete
financial_scope: unavailable
news_scope: unavailable
verification_status: pending
can_publish: false
trading_advice: false
```

预览路由已失败关闭：三份文档均在 `rejected`，`inbox` 和 `reviewed` 为空。
真实 Obsidian Vault 写入为 `NO`，金融日报自动发布为 `NO`。

## 8. 完整目标回答

1. Phase 1 PR 是否已合并：`YES`。
2. Phase 2 Base：`ce1f0b221cfd390cafc92a26ef8e16cf7dd3a753`。
3. 三票 Facts 是否通过：`YES`。
4. 每个指标是否可溯源：`YES`。
5. AI provider：历史记录为 `codex_cli`；真实模型名和宿主读取隔离未被证明。
6. 三票 AI 输出是否生成：`YES`；三份均按自由文本失败关闭策略被拒绝。
7. 无来源数字规则命中：`0`；不替代人工语义复核。
8. 交易建议词规则命中：`0`；文档仍不可发布。
9. 财务/新闻伪造断言规则命中：`0`；两项数据仍标记未接入并需人工复核。
10. Obsidian 旁路是否可用：安全 frontmatter 和拒绝路由 `READY`，自动入库 `BLOCKED`。
11. 是否建议进入短周期 AI 观察：`NO`，先实现类型化 Claims 输出合同。
12. 是否具备 Paper Trading 账本 POC 资格：`NO`，本阶段不得启动。

## 9. 供应商待确认

- `intraday_batch_entitlement`
- `first_30m_bucket_includes_09_30`
- `volume_unit`
- `amount_unit`

## 10. 冻结边界

- Paper Trading：`NOT_STARTED`。
- 云端重新部署：`NO`。
- Integrated Gold：`DISABLED / external_send_count=0`。
- Telegram / OpenClaw：`NOT_CONNECTED`。
- Obsidian 正式写入：`NO`。
- 真实 Key：`NOT_EXPOSED`。
- 当前不得使用 `PRODUCTION_READY`、`TRADING_READY` 或 `PAPER_TRADING_READY`。

## 11. 验证与不变性

- Phase 2 快照 / Facts / AI Review / AI provider 专项：`174 passed`。
- AI Review 加固专项：`90 passed`。
- 后端全量：`1506 passed, 13 warnings`。
- `compileall app scripts`：`PASSED`。
- Ruff F821：`PASSED`。
- Facts 离线校验：`FACTS_VALID`。
- AI 交付离线校验：`PHASE2_AI_OUTPUT_BLOCKED`，`errors=[]`；两票因
  `freeform_semantic_validation_incomplete` 拒绝，一票因
  `raw_qfq_comparison_detected` 拒绝。
- 写入路径：仅仓库固定 `reports/`；真实 Vault 路径无法作为生产输出根。
- 发布一致性：逐目录原子替换，正常失败回滚两目录，异常中断由事务标记阻断验证。
- 并发发布：事务标记使用独占创建，同时只允许一个发布者进入。
- 审计链：立即前驱哈希、重物化序号和不可变 provider/正文字段均由离线验证器复核；早期缺失链仍诚实标记为不完整。
- Phase 1 观察证据：36 个文件，聚合 SHA-256
  `8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5`，`UNCHANGED`。
- Phase 1 请求审计：7 个文件，聚合 SHA-256
  `dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d`，`UNCHANGED`。
- Phase 2 交付目录敏感形态扫描：`CLEAN`。
- 三份 AI 样本禁止交易词扫描：`0 hits`。

13 个 warning 均为已有 Polars、websockets、`datetime.utcnow()` 弃用提示和
Polars sortedness 警告，本轮没有扩大范围处理。

## 12. 下一动作

```text
DESIGN_TYPED_CLAIMS_OUTPUT_CONTRACT
```

下一步应先设计类型化 Claims 输出合同，使每个可发布断言都能绑定 Facts
指针、口径和校验规则。新 AI 批次启用前仍需同时验收外部容器或 OS 级的
宿主文件读取隔离。当前批次不重跑。
