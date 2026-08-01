# TickFlow Phase 2 AI 个股复盘与 Obsidian 旁路评估

## 1. 最终状态

```text
PHASE2_AI_OUTPUT_BLOCKED
```

Phase 2A Facts 均已通过，三票也均完成了唯一一次 Codex CLI 生成。派林生物和
中金黄金通过 AI 输出合同并保持人工待核验；东方财富因一处 raw 收盘
与 qfq 均线的跨口径比较被拒绝。按“每票仅一次、不重复生成到满意”的规则，
本批次不再调用 AI，也不得进入短周期 AI 观察。

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
- model：`codex_cli_default`。
- model source：`isolated_codex_cli_default_configuration`。
- 应用 AI provider：`openai_compat`，`has_ai_key=false`，本轮未修改。
- provider 调用：`3`，每票 `1`次。
- retry：`0`。
- 离线重物化：`2`次，新增 AI 调用 `0`，新增 provider 尝试 `0`。
- TickFlow API 新请求：`0`。
- 云端修改：`0`。

离线重物化仅修正两个校验器分词问题：中文紧邻日期，以及“1分钟bar / 30分钟
bucket”的中英紧邻写法。三份 AI 正文 SHA-256 在重物化前均已核对，正文没有改写。

## 5. 三票 AI 输出

| 标的 | 状态 | 无来源数字 | 交易建议 | 财务编造 | 新闻编造 | raw/qfq 混用 | 路由 |
|---|---|---:|---:|---:|---:|---:|---|
| 派林生物 | REVIEW_NEEDS_VERIFICATION | 0 | 0 | 0 | 0 | 0 | inbox |
| 中金黄金 | REVIEW_NEEDS_VERIFICATION | 0 | 0 | 0 | 0 | 0 | inbox |
| 东方财富 | REVIEW_REJECTED | 0 | 0 | 0 | 0 | 1 | rejected |

东方财富拒绝原因是正文把“日线收盘”与“前复权均线簇”做了相对比较。Facts
明确日线收盘为 raw，指标价格为 qfq；两者不得直接比较。这一拒绝不会被
自动改写或手工改 frontmatter 规避。

## 6. 数字与范围校验

模型只获得 Facts 投影和确定性数字目录。数字目录对每个可引用数字记录：

- Facts SHA-256；
- Facts JSON Pointer；
- raw / qfq / none 口径；
- 单位和展示变换。

单位未确认的 volume / amount 绝对值没有放入 AI 可引用目录。文档校验同时
覆盖章节顺序、禁止词、数字、日期、股票代码、财务/新闻断言、范围声明、
vendor pending、敏感形态和 raw/qfq 跨口径关系。

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

预览路由已正常工作：派林生物和中金黄金在 `inbox`，东方财富在 `rejected`，
`reviewed` 为空。真实 Obsidian Vault 写入为 `NO`，金融日报自动发布为 `NO`。

## 8. 完整目标回答

1. Phase 1 PR 是否已合并：`YES`。
2. Phase 2 Base：`ce1f0b221cfd390cafc92a26ef8e16cf7dd3a753`。
3. 三票 Facts 是否通过：`YES`。
4. 每个指标是否可溯源：`YES`。
5. AI provider：`codex_cli`，使用隔离的 CLI 默认模型配置。
6. 三票 AI 输出是否生成：`YES`；两份待人工核验，一份被拒绝。
7. 是否存在无来源数字：`NO`，最终校验为 0。
8. 是否存在交易建议：`NO`，命中为 0。
9. 财务/新闻是否被编造：`NO`，两类命中均为 0。
10. Obsidian 旁路是否可用：路由和安全 frontmatter `READY`，批次整体因一票拒绝为 `BLOCKED`。
11. 是否建议进入短周期 AI 观察：`NO`，先修正 raw/qfq 口径提示与语义校验。
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

- Phase 2 Facts / AI Review / AI provider 专项：`96 tests collected`，全部通过。
- AI Review / AI provider 最终复验：`59 passed`。
- 后端全量：`1458 passed, 13 warnings`。
- `compileall app scripts`：`PASSED`。
- Ruff F821：`PASSED`。
- Facts 离线校验：`FACTS_VALID`。
- AI 交付离线校验：`PHASE2_AI_OUTPUT_BLOCKED`，`errors=[]`，唯一拒绝指标为
  `basis_mixing_claim_count=1`。
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
DIAGNOSE_PHASE2_AI_BASIS_GUARD_BEFORE_NEW_OBSERVATION
```

下一步只应增强 Prompt 中的 raw/qfq 隔离示例和语义校验，然后由用户决定是否
批准新的三票一次性 AI 观察批次。当前批次不重跑。
