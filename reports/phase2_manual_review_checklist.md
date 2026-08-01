# TickFlow Phase 2 人工复核清单

## 当前自动校验摘要

| 标的 | Facts | AI 正文 | 无来源数字 | 交易建议词 | 财务编造 | 新闻编造 | raw/qfq 混用 | 预览路由 |
|---|---|---|---:|---:|---:|---:|---:|---|
| 派林生物 `000403.SZ` | VALID | REVIEW_REJECTED | 0 | 0 | 0 | 0 | 0 | rejected |
| 中金黄金 `600489.SH` | VALID | REVIEW_REJECTED | 0 | 0 | 0 | 0 | 0 | rejected |
| 东方财富 `300059.SZ` | VALID | REVIEW_REJECTED | 0 | 0 | 0 | 0 | 1 | rejected |

所有样本均保持 `verification_status=pending`、`can_publish=false` 和
`trading_advice=false`。表中数值是确定性规则的检测命中数，不是语义完整性
证明。因此自由文本统一以 `freeform_semantic_validation_incomplete`
失败关闭，下列复核项未由程序自动勾选。

## 派林生物

- [ ] 股票代码与名称为 `000403.SZ` / 派林生物。
- [ ] `trade_date` 和 `latest_trade_date` 为 `2026-07-31`。
- [ ] raw 收盘价与 Facts 一致。
- [ ] MA、MACD、RSI、BOLL、ATR 数值与 qfq Facts 一致。
- [ ] 分钟结构仅陈述已通过合同，没有扩展为供应商已确认口径。
- [ ] 成交量和成交额绝对值没有被赋予未确认单位。
- [ ] 财务、新闻、公告和关键价位均正确标记为未接入或未实现。
- [ ] 没有跨 raw/qfq 口径比较或交易指令。
- [ ] 确认文档仅存在于 `rejected`，不在 `inbox` 或 `reviewed`。

## 中金黄金

- [ ] 股票代码与名称为 `600489.SH` / 中金黄金。
- [ ] `trade_date` 和 `latest_trade_date` 为 `2026-07-31`。
- [ ] raw 收盘价与 Facts 一致。
- [ ] MA、MACD、RSI、BOLL、ATR 数值与 qfq Facts 一致。
- [ ] raw 日内区间和 qfq 指标区间始终分开陈述。
- [ ] Facts SHA-256 只用于失效条件，没有被解读为市场数据。
- [ ] 财务、新闻、公告和关键价位均正确标记为未接入或未实现。
- [ ] 没有跨 raw/qfq 口径比较或交易指令。
- [ ] 确认文档仅存在于 `rejected`，不在 `inbox` 或 `reviewed`。

## 东方财富

- [ ] 股票代码与名称为 `300059.SZ` / 东方财富。
- [ ] `trade_date` 和 `latest_trade_date` 为 `2026-07-31`。
- [ ] raw 收盘价和 qfq 指标数值分别与 Facts 一致。
- [ ] 确认拒绝原因：正文出现“日线收盘相对前复权均线簇”的跨口径表述。
- [ ] 确认该文档仅存在于 `rejected`，不在 `inbox` 或 `reviewed`。
- [ ] 不手工改动 frontmatter 规避拒绝。
- [ ] 财务、新闻、公告和关键价位均正确标记为未接入或未实现。

## 批次级别界

- [ ] 供应商待确认四项完整保留。
- [ ] `reviewed/` 为空。
- [ ] `inbox/` 为空。
- [ ] 真实 Obsidian Vault 未写入。
- [ ] 金融日报未自动发布。
- [ ] TickFlow API 新请求为 0，云端修改为 0。
- [ ] Paper Trading、Integrated Gold、Telegram 和 OpenClaw 均未启动。
