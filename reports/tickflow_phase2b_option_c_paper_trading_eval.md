# TickFlow Phase 2B Option C Paper Trading 评测报告

## 1. 状态

```text
PHASE2B_OPTION_C_PAPER_TRADING_READY
```

开工 Head：`3d2fbac2c723dcb24664af20398537c44eb1dce1`

实现提交：`05ae11a44083402c386d0c6732c1701d59a85e9f`

该状态仅表示单票离线模拟工程门禁通过，不得解释为实盘、荐股、自动发布或
生产就绪批准。

| 门禁 | 当前结果 |
|---|---|
| Resume state audit | PASSED |
| Option A | EXHAUSTED |
| Option C | ACTIVE |
| Legacy Provider path | FROZEN |
| Reference Fixture | VALID |
| Claims Validator | PASSED |
| Research Decision Layer | PASSED |
| Paper Trading Engine | PASSED |
| A-share T+1 | PASSED |
| Next-day-open policy | PASSED |
| Look-ahead guard | PASSED |
| Decimal / PnL / drawdown | PASSED |
| Idempotency | PASSED |
| ChenQuant Daily | PASSED |
| Replay x3 | PASSED |
| Independent review | NO P0/P1 ACTIONABLE FINDINGS |
| Real trading | DISABLED |

## 2. 范围与架构

本轮继续现有 Option C 旁路模块，没有创建第二套引擎，也没有接入主 API/UI、
批量回测引擎、Provider、TickFlow、券商、云端或正式 Obsidian Vault。

固定业务标的：`000403.SZ` 派林生物。固定业务链：

```text
Frozen Facts / Projection
-> strict Typed Claims
-> INTERPRETATION
-> SIGNAL
-> deterministic ACTION
-> A-share T+1 Paper Trading
-> Decimal PnL
-> ChenQuant Daily
-> Replay x3
```

冻结业务证据重新计算为：

```text
MIXED_TECHNICAL_STRUCTURE -> MIXED_OBSERVATION -> HOLD
```

没有为展示 BUY/SELL 改写业务行情。BUY、SELL、部分退出和全部退出只使用明确标记
`DETERMINISTIC_TEST_FIXTURE` 的测试数据，不进入业务发布目录。

## 3. 本轮补强

### 3.1 显式配置与身份

`PaperTradingConfig` 固定并哈希绑定：

```text
initial_cash=100000.00
commission_rate=0.0003
minimum_commission=5.00
sell_fee_rate=0.0005
lot_size=100
t_plus_one=true
execution_policy=NEXT_TRADING_DAY_OPEN
slippage_rate=0
```

费率仅为测试专用显式配置，不宣称等同真实券商费率。每个 Action 记录
`decision_id`、`order_id`、`action_id`、`idempotency_key` 和
`fixture_identity`。幂等键绑定 symbol、decision date、signal identity、
side/quantity、Fixture identity 和 Paper config identity。

账户分别保存已处理 decision、order、action 和 idempotency key。任何重复身份都在
成交和账本变更前阻断。

### 3.2 下一交易日开盘与 T+1

可执行 Action 不再接收调用方直接指定的单根 Bar。引擎只接受封闭的
`DeterministicMarketFixture`：

- 交易日历必须有序且唯一；
- Bar 日期必须唯一，并与前一交易日和 Fixture 身份一致；
- 只查找 decision date 的精确下一交易日；
- 精确下一日 Bar 缺失时返回 `NO_EXECUTION_PRICE_AVAILABLE`；
- 不跳到更远日期，不使用决策日 close，不使用下一日 close 作为成交价；
- 成交价固定为精确下一交易日 `open`。

买入 lot 明确记录 `acquired_trade_date` 和 `sellable_from_trade_date`。卖出资格
只由后者判断，不用自然日推算；当日不可卖，下一 Fixture 交易日才可卖。

### 3.3 账本与 PnL

全部核心金额使用 `Decimal` 并按 CNY `0.01` 确定性舍入。Trade 明确记录：

```text
execution_price
gross_amount_cny
commission_cny
stamp_tax_cny
fees_cny
sell_cost_cny
cost_basis_cny
net_cash_flow_cny
realized_pnl_cny
```

严格 schema 会交叉验证费用分量、BUY 成本基础/净现金流、SELL 净收入/成本基础/
已实现盈亏和时间顺序。账户继续计算现金、FIFO 持仓成本、市场价值、未实现盈亏、
总权益、权益峰值和最大回撤。

### 3.4 Fixture provenance 与时间门禁

Reference manifest SHA-256 贯穿 Interpretation、Signal 和 Action 的
`fixture_identity`。Projection 日期即使具有重新计算后的有效自哈希，只要不等于
manifest 交易日也会以 `projection_date_mismatch` 阻断。执行 Fixture 的 source、
symbol 或 scenario identity 不一致同样阻断。市场 Fixture 另有基于完整日历和 Bar
内容计算的 identity；执行入口会重新计算，Trade 与 execution ID 同时绑定该身份。

`reference_typed_claims.json` 使用显式
`source=DETERMINISTIC_REFERENCE_FIXTURE` 的非发布 envelope；内嵌
`claims_document` 仍与冻结 canonical ClaimsDocument 字节一致并继续经过现有
Typed Claims Validator，未向冻结 Claims Schema 注入字段。

## 4. 33 项需求覆盖

| # | 合同 | 证据状态 |
|---:|---|---|
| 1 | initial cash | PASSED |
| 2 | BUY | PASSED / TEST FIXTURE ONLY |
| 3 | HOLD | PASSED / REFERENCE FIXTURE |
| 4 | SELL | PASSED / TEST FIXTURE ONLY |
| 5 | partial sell | PASSED |
| 6 | full exit | PASSED |
| 7 | insufficient cash | PASSED |
| 8 | insufficient holdings | PASSED |
| 9 | invalid quantity | PASSED |
| 10 | 100-share buy lot | PASSED |
| 11 | commission | PASSED |
| 12 | minimum commission | PASSED |
| 13 | sell fee | PASSED |
| 14 | realized PnL | PASSED |
| 15 | unrealized PnL | PASSED |
| 16 | total equity | PASSED |
| 17 | max drawdown | PASSED |
| 18 | duplicate decision | PASSED |
| 19 | duplicate order | PASSED |
| 20 | T+1 same-day sell blocked | PASSED |
| 21 | T+1 next-day sell allowed | PASSED |
| 22 | missing next-day price | PASSED |
| 23 | Facts timestamp after decision blocked | PASSED |
| 24 | future market data blocked | PASSED |
| 25 | Projection date mismatch blocked | PASSED |
| 26 | invalid Claims produces no action | PASSED |
| 27 | Fixture identity mismatch blocked | PASSED |
| 28 | deterministic BUY fixture | PASSED |
| 29 | deterministic SELL fixture | PASSED |
| 30 | deterministic HOLD fixture | PASSED |
| 31 | replay x3 | PASSED |
| 32 | Daily JSON deterministic | PASSED |
| 33 | Daily Markdown deterministic | PASSED |

## 5. 构建与重放证据

输出目录：`reports/phase2_option_c/`，共 10 个普通文件。连续两次离线构建的
全部文件 SHA-256 完全一致。

```text
replay_count=3
status=DETERMINISTIC_REPLAY_PASSED
trade_ledger_identical=true
position_identical=true
pnl_identical=true
json_identical=true
markdown_identical=true
```

Replay evidence SHA-256：

```text
d906f9bb5eae728039cd6fb6a35b128c218ef3cd45c6bd6fd9441fc485cd713e
```

生成的业务日报继续固定 `SIMULATION ONLY`、`can_publish=false`、
`trading_advice=false`，结果为 HOLD 且模拟成交数为 0。

## 6. 冻结证据与外部行为

273 个受保护历史文件在构建前后聚合 SHA-256 均为：

```text
caa81d6f500ed69be79bd168244fb9eadad1c465299c717d95c7a3712a212234
```

因此 Phase 1 行情与请求审计、Phase 2 Facts/Claims、历史 Provider/Ark、Relay、
Isolation 和 Obsidian Preview 证据未改变。

```text
TickFlow API requests=0
Real Provider attempts=0
Real AI calls=0
Broker calls=0
Cloud deployments=0
Real trading=DISABLED
Integrated Gold=DISABLED
```

未读取或输出真实 Key，未发起 DNS/TLS/HTTP 探测，未连接券商，未写正式 Vault，
未接 Telegram/OpenClaw，也未重新部署云端。

## 7. 验证状态

| 验证 | 结果 |
|---|---:|
| Paper Trading 专项 | `32 passed` |
| Option C 专项 | `64 passed` |
| Option C + Claims/Renderer | `141 passed` |
| compileall | PASSED |
| Ruff F821 | PASSED |
| focused Ruff | PASSED |
| git diff --check | PASSED |
| 两次离线构建 | PASSED / SHA IDENTICAL |
| 敏感信息扫描 | PASSED |
| 禁止 backtest/provider/network 依赖扫描 | PASSED |
| 独立复审 | NO P0/P1 ACTIONABLE FINDINGS |

独立复审首次发现两项 P1：

1. 市场 Fixture identity 未绑定日历和 Bar 内容；
2. `reference_typed_claims.json` 未在文件自身明确 reference source。

两项均按 TDD 修复。复审回归确认市场身份会在执行入口重新计算，Trade 保留研究
Fixture 和市场 Fixture 两类身份；Claims envelope 显式标记来源，内嵌文档仍与
冻结 canonical ClaimsDocument 字节一致。最终结论：

```text
NO P0/P1 ACTIONABLE FINDINGS
```

本轮未修改共享核心模块，因此未重跑历史全量后端。此前全量基线为
`2835 passed / 58 frozen Ark/Gold failures`；按批准边界不重开这些冻结工程债务。

## 8. 结论

当前代码已证明单票确定性研究信号可以进入独立的 T+1 模拟账本，并在精确下一
交易日开盘政策下稳定计算现金、持仓和 PnL。它仍只是工程侧的离线模拟闭环，
不代表真实交易可用，也不构成投资建议。

全部门禁通过，下一动作固定为：

```text
RUN_SINGLE_SYMBOL_PAPER_TRADING_VALIDATION
```

不会自动启动三票、Provider、主系统集成、Paper Trading 实时任务或下一阶段。
