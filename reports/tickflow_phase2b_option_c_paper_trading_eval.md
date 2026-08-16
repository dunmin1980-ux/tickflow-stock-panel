# TickFlow Phase 2B Option C Paper Trading 评测报告

## 1. 最终状态

```text
PHASE2B_OPTION_C_PAPER_TRADING_READY
```

| 项目 | 状态 |
|---|---|
| Option A | EXHAUSTED |
| Option C | ACTIVE |
| Real Provider integration | DEFERRED / FROZEN |
| Paper Trading Engine | READY |
| Reference Fixture | VALID |
| Claims integration | PASSED |
| Research Decision Layer | PASSED |
| PnL accounting | PASSED |
| Look-ahead guard | PASSED |
| Replay x3 | PASSED |
| ChenQuant Daily | PASSED |
| Real trading | DISABLED |
| Independent review | NO ACTIONABLE P0/P1 FINDINGS |

实现提交：`f87257c697a6f49864c4458d2f14461530aeb34d`

开工基线：`8f4b0f8f885a625a51e3213414b074807affdd98`

## 2. 本轮交付范围

本轮建立了独立旁路的单标的确定性研究与模拟账本，不接主 API/UI，
不复用批量回测引擎，不连接 Provider、TickFlow、券商或任何实盘路径。

固定业务标的：`000403.SZ` 派林生物。

闭环为：

```text
冻结 Facts / Projection
→ 严格 Typed Claims
→ INTERPRETATION
→ SIGNAL
→ 确定性规则 ACTION
→ A 股 T+1 Paper Trading
→ Decimal PnL
→ ChenQuant Daily
→ Replay x3
```

冻结业务样本的结果为：

```text
MIXED_TECHNICAL_STRUCTURE
→ MIXED_OBSERVATION
→ HOLD
```

没有为演示 BUY/SELL 伪造业务行情。BUY/SELL、部分卖出和全量退出仅在
`DETERMINISTIC_TEST_FIXTURE` 测试路径中验证，未进入发布目录。

## 3. 核心合同

### 3.1 研究与行动分层

- Facts、Projection、Claims、Interpretation、Signal、Action 使用不同严格对象；
- Claims 不能直接执行 Action；
- 执行入口必须同时收到对应 Signal，并用当前账户快照重新计算 Action；
- 手工构造或来源不匹配的 Action 以 `action_provenance_mismatch` 阻断；
- Claims 对象必须与冻结 canonical bytes 一致；
- Projection 内容必须重新计算 SHA-256，不能只信任对象内自报哈希。

### 3.2 A 股执行规则

- 所有金额使用 `Decimal`，JSON 金额使用字符串，不使用浮点金额；
- 买入数量必须为 100 股整数手，数量字段为整数；
- 当日买入的持仓当日不可卖出；
- 信号形成后，只能使用下一交易日 `open` 成交；
- 决策日期、前一交易日和执行日期必须形成连续合同；
- 成交不读取下一交易日 `close`，改变未来收盘价不影响成交结果；
- 账户 mark、持仓 lot 或成交记录晚于信号日期时拒绝生成 Action；
- 重复 Action ID 在账本变更前阻断。

### 3.3 费用与盈亏

固定配置：

- 初始现金：`100000.00 CNY`；
- 佣金率：`0.0003`；
- 最低佣金：`5.00 CNY`；
- 卖出印花税率：`0.0005`；
- 滑点：`0`；
- 成本分配：FIFO；
- 支持部分退出、全部退出、已实现/未实现盈亏、总权益和最大回撤。

## 4. 确定性证据

输出目录：`reports/phase2_option_c/`

共 10 个文件：

1. `account_config.json`
2. `chenquant_daily.json`
3. `chenquant_daily.md`
4. `decision.json`
5. `evidence_index.json`
6. `paper_account.json`
7. `reference_fixture_manifest.json`
8. `reference_typed_claims.json`
9. `replay_validation.json`
10. `trade_ledger.json`

重复离线构建后，10 个文件的 SHA-256 全部不变。

Replay 结果：

```text
replay_count=3
status=DETERMINISTIC_REPLAY_PASSED
trade_ledger_identical=true
position_identical=true
pnl_identical=true
json_identical=true
markdown_identical=true
```

Replay 校验器要求六个运行工件完整存在；三次运行同时缺失同一文件时不会再
误判为通过。

## 5. Fixture 与安全标识

冻结身份：

| 对象 | SHA-256 |
|---|---|
| Facts | `adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956` |
| Projection | `0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f` |
| Typed Claims | `66f7ef471b293a4d5453ca53fd91668f217176915e6e88f2ce88f4f037559183` |

`reference_typed_claims.json` 保持冻结 Typed Claims Schema 的精确
`ClaimsDocument`，不向冻结 Schema 注入额外字段。它的 SHA-256 由
`reference_fixture_manifest.json` 绑定，manifest 同时为该 Claims 文件声明：

```text
claims_artifact_simulation_only=SIMULATION ONLY
claims_artifact_can_publish=false
claims_artifact_trading_advice=false
```

发布目录未发现 Secret、Authorization、Cookie、Session、测试夹具标识或
明确交易建议措辞。全部文件为普通文件，mode `0600`，无符号链接。

## 6. 历史证据保护

受保护历史证据文件数：`273`。

构建前后聚合 SHA-256 均为：

```text
caa81d6f500ed69be79bd168244fb9eadad1c465299c717d95c7a3712a212234
```

结论：Phase 1 行情证据、请求审计、Facts、Claims、Provider/Ark、Relay、
Isolation 和 Obsidian Preview 历史证据均未修改。

## 7. 测试与复审

### 7.1 通过项

| 验证 | 结果 |
|---|---:|
| Option C + Claims 集成 | `132 passed` |
| compileall | PASSED |
| Ruff | PASSED |
| git diff --check | PASSED |
| 离线构建 | PASSED |
| Replay x3 | PASSED |
| 敏感信息扫描 | PASSED |
| 禁止网络/Provider/backtest 依赖扫描 | PASSED |
| 独立复审 | NO ACTIONABLE P0/P1 FINDINGS |

独立复审先后发现 6 个 P1，均已增加失败用例并关闭：

1. Action 执行入口缺少 Signal 来源绑定；
2. 内存 Claims 可与冻结 canonical bytes 脱节；
3. Replay 在全部缺少必需文件时可能误判通过；
4. 严格 Claims 文件缺少明确的旁路安全绑定；
5. 未来账户快照可影响过去信号；
6. Projection 内容未重新计算自哈希。

最终复审结果：`NO ACTIONABLE P0/P1 FINDINGS`。

### 7.2 全量后端说明

全量后端结果：

```text
2835 passed
58 failed
13 warnings
```

58 个失败不在 Option C 新模块：

- 44 个 Ark/Provider 失败已在开工 Head `8f4b0f8` 复现；
- 13 个 Gold 失败已在开工 Head 以相同集合复现；
- 1 个冻结 Minimal Ark launcher 测试绑定旧 Git Head，任何 Option C 新提交都会
  触发 `runtime_git_head_mismatch`。

Option C 只新增或修改自身 schema、service、离线脚本、专项测试和文档，未修改
Ark/Provider、Gold 或主 API/UI。按本阶段边界，不重开冻结 Provider 和既有 Gold
工程债务，也不重生成 Candidate/Scope 来制造全量绿色结果。

## 8. 外部行为审计

```text
TickFlow API requests=0
Real Provider attempts=0
Real AI calls=0
Broker calls=0
Cloud deployments=0
Real trading=DISABLED
Integrated Gold=DISABLED
```

未读取或输出真实 Key，未配置 AI，未发起 DNS/TLS/HTTP Provider 探测，未连接
券商，未写入正式 Obsidian Vault，未接 Telegram/OpenClaw，未部署云端。

## 9. 结论与下一动作

Option C 已达到单标的确定性 Paper Trading 工程就绪状态。当前结论仅证明：

- 冻结研究证据可以稳定映射为确定性 HOLD；
- 测试夹具可以验证 T+1、下一交易日开盘成交、费用、持仓和 PnL；
- 同一输入重复执行三次结果完全一致；
- 业务链不再依赖真实 LLM Provider；
- 不存在真实交易路径。

它不代表实盘可用，不代表三票批次通过，也不构成投资建议。

下一动作：

```text
RUN_SINGLE_SYMBOL_PAPER_TRADING_VALIDATION
```

本阶段完成后停止，不自动启动三票、不接主系统、不运行真实 Provider。
