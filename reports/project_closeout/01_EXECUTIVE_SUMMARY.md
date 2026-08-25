# TickFlow 项目执行摘要

> 结项状态：`TICKFLOW_PROJECT_CLOSEOUT_READY_FOR_REVIEW`
> 当前产品：`Paper Trading=ACTIVE`、`Real Provider=DEFERRED`、`Real Trading=DISABLED`、`SIMULATION ONLY`
> 当前范围：单票 `000403.SZ` 派林生物，本地浏览器日用，不含券商、实盘、三票批次、云端自动发布。

## TickFlow 是什么

TickFlow 当前是一套面向 A 股单票日线研究的确定性工作流：把已验证行情冻结为 Facts，经 Projection、Typed Claims、Research Decision 和 Paper Trading 规则层，形成可追溯的模拟账户、PnL、ChenQuant Daily 与浏览器工作台。它不是 AI 荐股器，也不是自动交易系统。

**FACT**：当前首页为 `/paper-trading`“今日工作台”，账户页为 `/paper-account`“模拟盘”；二者读取同一份 Option C 持久状态，只有今日工作台具有受控的 Daily Run 入口。页面固定显示 `SIMULATION ONLY / REAL TRADING DISABLED`。

Evidence: `TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md`、`TICKFLOW_WORKBENCH_ACCOUNT_UX_SPLIT_RELEASED.md`、`frontend/src/router.tsx`。
Commit: `f1587ab62950583205d28652fe7e952f7dd28434`

## 为什么立项

原始问题不是“让大模型替人选股”，而是把零散的 A 股研究步骤变成一个每天可重复、可验证、可回放的闭环：数据必须有来源，结论必须经过结构化校验，模拟动作必须满足 A 股规则，结果必须能持久保存并形成盘后素材。

**DECISION**：先用三票、五个实际交易日验证数据合同，再建立 Facts 与 Claims，避免把不稳定输入直接交给 AI 或模拟账本。

**RETROSPECTIVE**：这一步是项目最重要的正确顺序。它让后续 Provider 失败时，数据、规则和账本仍可独立交付。

Evidence: `reports/tickflow_phase1_observation_final.md`、`reports/tickflow_phase2a_facts_eval.md`。

## 架构如何变化

1. **Data contract**：三票五日观察，验证日线、分钟线、复权、30m 聚合、429 与敏感信息边界。
2. **Deterministic Facts**：把行情、指标、来源、计算函数和口径固化为可校验 JSON。
3. **Typed Claims**：用 Strict Schema、Claims Validator 和 Deterministic Renderer 限制自由文本。
4. **Provider exploration**：为 OpenAI/Ark 建立隔离 Relay、不可变 Candidate、Approval Scope、one-shot、ledger、receipt 和 TLS 诊断。
5. **Engineering stop**：真实 Provider 路径没有形成稳定业务输出，工程成本持续上升，Provider 被移出 Critical Path。
6. **Option C**：使用冻结 Facts/Claims 和确定性研究规则，直接进入 T+1 Paper Trading、PnL 和 ChenQuant Daily。
7. **Product delivery**：从 CLI Daily Runner 进入 Visual Workbench，再完成导航清理和“今日工作台/模拟盘”职责拆分。

## 为什么真实 Provider 被 Deferred

真实 Provider 并非简单“调用失败”。工程先后证明了：Strict JSON Schema、精确模型/端点、只读 Secret 注入、单次尝试、不可变审批、出站白名单、失败关闭、持久 ledger 和阶段 receipt 都是可实现的安全控制；同时也留下了 `ATTEMPT_CONSUMED_UNKNOWN`、OpenAI HTTP 401、Ark 连接/响应头等待、TLS/Proxy 差分无法完全归因等负面证据。

**FACT**：历史真实请求没有产出可进入正式 Claims 路由的结果；所有发生网络 dispatch 的 request 对应 Scope 都按一次性合同消费并保留，未自动重试，`can_publish=false`。只完成离线审批但未执行的 Scope/Candidate 则保持未消费或 superseded 状态。

**DECISION**：停止继续扩大 Provider/TLS/Proxy 工程，将真实 Provider 标记为 `DEFERRED`。

**RETROSPECTIVE**：这是 `BUSINESS_VALUE_REPRIORITIZATION`，不是技术投降。Provider 的边界设计仍是资产，但它不应阻塞最小业务闭环。

Evidence: `reports/tickflow_phase2b_single_symbol_canary_eval.md`、`reports/tickflow_phase2b_http401_diagnosis.md`、`reports/tickflow_phase2b_ark_proxy_tls_differential_eval.md`。

## Option C 为什么成为 Critical Path

Option C 把模型从执行关键路径移除：

`Frozen Facts → Projection → Deterministic Reference Fixture → Typed Claims Validator → Research Decision → Paper Trading → PnL → ChenQuant Daily`

它保留了最有价值的工程约束：事实可追溯、Claims 结构化、决策分层、无未来数据、A 股 T+1、下一交易日开盘成交、Decimal 账本、幂等与可回放。它也接受真实冻结样本计算出的 `MIXED_OBSERVATION → HOLD`，没有为了演示交易而改 Fixture 或规则；BUY/SELL 只用明确的 deterministic test fixture 验证。

**FACT**：单票验证中 HOLD 不产生订单或交易，现金和权益保持一致；完整测试生命周期另行验证 BUY、T+1、SELL、费用和 PnL。

Evidence: `reports/tickflow_phase2b_option_c_paper_trading_eval.md`、`reports/tickflow_phase2b_single_symbol_paper_trading_validation.md`、`reports/phase2_option_c_mvp/release_verification.json`。

## 最终产品能做什么

- 一键启动本地服务并打开浏览器。
- 在“今日工作台”查看交易日状态、Research Signal、Paper Action、Claims 状态、今日持仓、PnL、待办与 ChenQuant Daily。
- 在安全门禁通过时只运行一次今日模拟盘。
- 在“模拟盘”查看总资产、现金、持仓、可卖数量、成本、已实现/未实现 PnL、最大回撤、决策、交易和权益曲线。
- 刷新或重启后读取原子持久化状态，不由前端重新计算账本。
- 固定执行 `A 股 T+1`、`NEXT_TRADING_DAY_OPEN`、100 股整数手、Decimal 费用和幂等控制。

## 当前不能做什么

- 不调用真实 OpenAI/Ark；AI 输出不是 MVP 必需条件。
- 不连接券商，不发送真实订单，不提供真实交易路径。
- 不支持三票正式批次、全市场扫描、云端部署或自动发布。
- 单票持仓跨除权口径变化时 fail closed，要求人工核验。
- volume/amount 单位及 30m 首桶等供应商口径仍保留待确认状态。

## 三个最重要成果

1. **确定性研究资产**：Facts、Projection、Typed Claims、Validator、Research Decision 的可追溯链。
2. **可信模拟账本**：T+1、下一交易日开盘、Decimal、费用、PnL、回撤、幂等、恢复和 Replay。
3. **可日用产品**：CLI 引擎被薄 API 和浏览器工作台包装，研究页与账户页职责清晰，状态持久化。

## 三个最重要教训

1. **Provider 不是产品价值本身**：真实模型接通若不能稳定形成可验证业务输出，就不应占据 Critical Path。
2. **AI Agent 会同时放大速度与复杂度**：它能快速建立审批、ledger、receipt、TLS 隔离，也能让局部工程问题不断产生新合同和新 Scope。
3. **HOLD 是有效结果**：研究系统的价值在于忠实执行数据与规则，不在于每天制造动作。

## 项目成本与可信口径

可由 Git 和工件证明的证据跨度为 2026-07-27 至 2026-08-25，含首尾 30 个日历日期、历时 29 天；Phase 2 主链从 2026-07-31 至 2026-08-25，含首尾 26 个日期、历时 25 天。该跨度内有 18 个发生相关 Git 提交的活跃日期。日历跨度和活跃提交日都不等于实际人时。

测试数字来自各阶段正式报告，测试集合存在重叠，不能相加为项目总测试量。仓库没有可信的项目级 Codex token 总账，也没有完整 Agent wall-clock 汇总，因此本报告不伪造总 token 或总工时；若未来补充单任务统计，必须标记 `PARTIAL OBSERVED AGENT USAGE`。

## 下一阶段最值得投入什么

先进入真实日用，积累单票研究决策、HOLD/交易、PnL 和人工复核记录；再根据真实使用中的高频摩擦决定是否扩展第二只股票、企业行动处理和输入自动化。Provider 只在明确的、可量化的增益假设下重新进入，例如“在不改变动作规则的前提下提高解释质量”，而不是再次承担关键路径。

推荐路径：

`Data → Deterministic Research → Paper Trading → Visual Workbench → Real Usage → Optional Provider`

## 当前日用入口

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-phase2-ai-review"
./scripts/start_tickflow_visual.sh
```

浏览器：`http://127.0.0.1:3018/paper-trading`

日用纪律：收盘后检查日期与安全标识，只点击一次“运行今日模拟盘”；先看今日工作台，再到模拟盘检查账户与历史。任何输入、企业行动或状态错误都应保持 fail closed，不手工改写账本文件。
