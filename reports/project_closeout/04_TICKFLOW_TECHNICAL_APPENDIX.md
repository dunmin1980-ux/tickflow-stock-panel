# TickFlow 技术附录

## 1. 当前运行架构

当前 Visual Workbench 是现有 FastAPI/React 应用上的薄层，核心账本仍由 Option C 单一实现维护。

| Layer | Main artifact | Responsibility |
|---|---|---|
| Startup | `scripts/start_tickflow_visual.sh` | 启动本地后端/前端并打开浏览器 |
| Route | `frontend/src/router.tsx` | 默认进入 `/paper-trading`，提供 `/paper-account` |
| Today UI | `frontend/src/pages/PaperTrading.tsx` | 今日状态、Signal、Action、待办、Daily 与唯一 Run 入口 |
| Account UI | `frontend/src/pages/PaperAccount.tsx` | 账户、持仓、决策、交易、PnL 与权益只读视图 |
| Frontend API | `frontend/src/lib/api.ts` | `paperTradingDashboard` 与 `paperTradingRun` 合同 |
| FastAPI | `backend/app/api/paper_trading.py` | `/api/paper-trading/dashboard`、`/run` 与友好错误映射 |
| Facade | `backend/app/services/phase2_visual_workbench.py` | 聚合状态、输入、Claims、Daily；不拥有交易规则 |
| Input | `backend/app/services/phase2_visual_daily_input.py` | 交易日、收盘、行情日期与企业行动门禁 |
| Research | `backend/app/services/phase2_research_decision.py` | Interpretation、Signal、Action 分层 |
| Ledger | `backend/app/services/phase2_paper_trading.py` | T+1、订单、持仓 lot、费用、PnL、回撤、幂等 |
| Daily | `backend/app/services/phase2_chenquant_daily.py` | 确定性 JSON/Markdown 日报 |
| State | `backend/app/services/phase2_option_c_state.py` | 原子持久化、日快照和恢复 |

### API 合同

```text
GET  /api/paper-trading/dashboard?target_date=YYYY-MM-DD
POST /api/paper-trading/run
body: { "target_date": "YYYY-MM-DD" | null }
```

薄 API 将内部异常映射为稳定错误码，包括：

- `PAPER_INPUT_NOT_READY`
- `PAPER_MARKET_NOT_CLOSED`
- `PAPER_MARKET_SOURCE_UNAVAILABLE`
- `PAPER_CORPORATE_ACTION_REVIEW_REQUIRED`
- `PAPER_RUN_ALREADY_LOCKED`
- `PAPER_RUN_REJECTED`
- `PAPER_STATE_UNAVAILABLE`

这些错误不会触发真实交易，也不会让前端修改账本。

## 2. 目录图

```text
tickflow-phase2-ai-review/
├── backend/
│   ├── app/
│   │   ├── api/paper_trading.py
│   │   ├── schemas/phase2_option_c.py
│   │   └── services/
│   │       ├── phase2_facts.py
│   │       ├── phase2_claims_*.py
│   │       ├── phase2_research_decision.py
│   │       ├── phase2_paper_trading.py
│   │       ├── phase2_option_c_*.py
│   │       ├── phase2_chenquant_daily.py
│   │       ├── phase2_visual_daily_input.py
│   │       └── phase2_visual_workbench.py
│   ├── data/user_data/phase2_option_c_paper/
│   │   ├── inputs/
│   │   └── reference_account/
│   └── tests/test_phase2_*.py
├── frontend/src/
│   ├── pages/PaperTrading.tsx
│   ├── pages/PaperAccount.tsx
│   ├── components/paper-trading/
│   ├── lib/api.ts
│   └── router.tsx
├── reports/
│   ├── phase2_facts/
│   ├── phase2_claims/
│   ├── phase2_provider_canary/
│   ├── phase2_provider_ark/
│   ├── phase2_option_c/
│   ├── phase2_option_c_mvp/
│   ├── visual_workbench_v1/
│   └── project_closeout/
├── scripts/start_tickflow_visual.sh
└── TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md
```

## 3. 状态文件

Runbook 定义的正式本地状态根：

```text
backend/data/user_data/phase2_option_c_paper/
├── inputs/
│   └── YYYY-MM-DD/
│       ├── raw_daily.json
│       ├── qfq_daily.json
│       ├── facts.json
│       ├── projection.json
│       ├── claims.json
│       ├── claims_validation.json
│       ├── input.json
│       └── manifest.json
└── reference_account/
    ├── current_state.json
    └── days/YYYY-MM-DD/
        ├── account_state.json
        ├── continuous_state.json
        ├── decision_ledger.json
        ├── trade_ledger.json
        ├── position_lots.json
        ├── equity_history.json
        ├── chenquant_daily.json
        ├── chenquant_daily.md
        └── run_receipt.json
```

状态由后端原子发布。前端只读取 API 返回值，不应直接读写这些文件。不能通过编辑 `current_state.json` 或日目录来改变结果。

## 4. Paper Trading Schema 与合同

### PaperTradingConfig

正式合同包含：

```text
initial_cash
commission_rate
minimum_commission
sell_fee_rate
lot_size=100
t_plus_one=true
execution_policy=NEXT_TRADING_DAY_OPEN
```

参考评测中的默认值：初始现金 100000 元、佣金率 0.0003、最低佣金 5 元、卖出费用率 0.0005、滑点 0。

### Idempotency

`order_id` 与 `idempotency_key` 独立。幂等身份显式绑定：

```text
symbol
decision_date
signal_identity
action
fixture_identity
paper_config_identity
```

同一决策或同一订单不能重复改变账户。

### Position Lot

每个 lot 至少记录：

```text
acquired_trade_date
quantity
sellable_from_trade_date
```

可卖数量由 lot 的交易日期计算，不由 UI 推测。

### Trade Record

交易记录包含成交方向、价格、数量、佣金、卖出费用、成本基础、已实现 PnL 和 fixture identity。金额使用 Decimal；BUY 只接受 100 股整数手。

### Execution and valuation

- 决策形成后，只使用下一可用交易日 09:30 open 成交。
- 缺少对应开盘时返回 `NO_EXECUTION_PRICE_AVAILABLE`。
- 估值使用在估值时点已经可用的独立 valuation bar。
- 禁止使用未来行情、未来收盘价或用 T 日 close 替代执行价。

## 5. 决策流水线

```text
Validated Source Evidence
  → Facts (traceable numbers)
  → Minimal Projection
  → Typed Claims
  → Claims Validator
  → INTERPRETATION
  → SIGNAL
  → Deterministic ACTION
  → Paper Trading
  → Ledger / PnL / Drawdown
  → ChenQuant Daily
  → Visual Workbench
```

边界规则：

- Facts 不包含无来源数字。
- Claims 不能包含未知 pointer、未知 predicate、交易建议或口径混用。
- Typed Claims 不能直接生成 ACTION。
- INVALID Claims 结果为 no action。
- 真实参考输入的 `MIXED_OBSERVATION → HOLD` 是正式有效结果。
- BUY/SELL 闭环使用明确标识的 deterministic test fixture，不伪造真实行情。

## 6. 重要服务模块

### Deterministic core

| Module | Notes |
|---|---|
| `phase2_facts.py` | 从冻结行情生成带 provenance 的 Facts |
| `phase2_claims_service.py` | Claims 读取与服务层 |
| `phase2_claims_renderer.py` | 确定性 Markdown 渲染 |
| `phase2_research_decision.py` | Claims 到 Interpretation/Signal/Action 的规则边界 |
| `phase2_paper_trading.py` | 账本、费用、持仓、T+1、PnL、幂等 |
| `phase2_option_c_fixture.py` | 冻结参考 Fixture 与身份校验 |
| `phase2_option_c_daily.py` | 单日 Runner 合同 |
| `phase2_option_c_continuous.py` | 跨日状态演进 |
| `phase2_option_c_state.py` | 持久化与恢复 |
| `phase2_chenquant_daily.py` | JSON/Markdown 输出 |

### Visual delivery

| Module | Notes |
|---|---|
| `phase2_visual_daily_input.py` | 日用输入准备、行情与企业行动门禁 |
| `phase2_visual_workbench.py` | 状态聚合与一次性运行 facade |
| `api/paper_trading.py` | 认证后的薄 HTTP API 与稳定错误码 |
| `PaperTrading.tsx` | 今日工作台，唯一运行入口 |
| `PaperAccount.tsx` | 模拟账户只读视图 |

### Historical Provider path

Provider 模块仍保存在仓库中作为历史资产，包括：

```text
phase2_ai_worker_protocol.py
phase2_provider_relay_protocol.py
phase2_provider_relay_runner.py
phase2_isolation_runtime.py
phase2_openai_canary_runner.py
phase2_canary_orchestrator.py
phase2_canary_runtime_contract.py
phase2_canary_observability.py
phase2_minimal_ark_canary.py
phase2_ark_timeout_contract.py
```

它们当前不是 Visual Workbench 或 Paper Trading 的运行依赖，不应因存在这些文件而判断 `Real Provider=ENABLED`。

## 7. Frontend 路由与产品语义

| Route | Product question | Mutation |
|---|---|---|
| `/paper-trading` | 今天我要干什么？ | 允许受控 `paperTradingRun` |
| `/paper-account` | 模拟账户现在怎么样？ | 无运行 mutation |

当前导航表达的是研究产品，而不是历史 SaaS/Provider 设置：今日工作台、模拟盘、个股研究、市场、监控、数据、设置。历史 API Key UI 被隐藏或标记为 Visual v1 不使用。

## 8. 测试与验证证据

下列数字属于不同阶段、存在重叠，不能相加为“项目测试总数”。

| Stage | Evidence-backed result | Interpretation |
|---|---|---|
| Phase 1.2 | 5/5 valid trading days | 三票数据观察通过 |
| Option C implementation | Paper 36、Option C 68、focused 167 | T+1、执行价、PnL、幂等、Replay |
| Paper Trading MVP | focused 189 passed | 生命周期、恢复、Release SHA |
| Visual Workbench backend focused | 161 passed | 薄 API、输入、状态和错误处理 |
| Visual frontend unit | 104 passed | 页面和展示合同 |
| Visual UI E2E | 28 passed / 2 skipped at Head `443858e` | desktop/mobile、run、persistence；早于当前 Cleanup/UX Split |
| Current UX Split | release marker + source present | 当前两页与 E2E contracts 已提交；未声称重跑旧 E2E 数字 |
| Visual full backend | 2907 passed / 58 failed / 13 warnings | 58 为冻结 Ark/Gold 历史债务，不是全绿 |

Independent Review 在 Option C、MVP 和 Visual 阶段均记录 `NO_P0_P1_FINDINGS`。本次 closeout 另有独立只读复审，见 `review_findings.md`。

## 9. Release Markers

| Marker | Date | Meaning |
|---|---|---|
| `TICKFLOW_PAPER_TRADING_MVP_RELEASED.md` | 2026-08-20 | 单票确定性模拟盘可日用 |
| `TICKFLOW_VISUAL_WORKBENCH_V1_RELEASED.md` | 2026-08-21 | 本地浏览器工作台发布 |
| `TICKFLOW_VISUAL_PRODUCT_CLEANUP_RELEASED.md` | 2026-08-25 | 导航与能力表达收敛 |
| `TICKFLOW_WORKBENCH_ACCOUNT_UX_SPLIT_RELEASED.md` | 2026-08-25 | 今日工作台与账户职责拆分 |

## 10. Replay 与 Release SHA

- Option C 单票 `replay_validation.json` 文件 SHA-256：`30565345a6a35115bf3602fe047fbeb1162eaa85b22c1dcea351d6716a30c6d5`
- Paper Trading MVP Release SHA：`eeb0ed2718198bdf2f78cc969eae81a6f48006c2fe5de3ec496d3bc7c6becae2`

两个 SHA 对应不同验证集合，不能互相替代。前者验证 Option C 单票语义，后者验证 MVP 生命周期和 release bundle。

## 11. 当前技术债

### Current P2

1. 正式运行仍为单票 `000403.SZ`。
2. 持仓跨企业行动/复权口径变化时需要人工核验，未实现自动账本调整。
3. 日用输入准备依赖行情源可用性，失败时只能 fail closed。
4. 供应商 `intraday_batch`、30m 首桶、volume/amount 单位仍待确认。
5. 自动 Obsidian/金融日报发布关闭。
6. 本地模式发布，云端、多用户和调度未发布。
7. 生产前端仍有已有 large-chunk warning。

### Historical frozen debt

Visual Workbench 全量后端报告保留 58 个 Ark/Gold 历史失败。它们不属于当前 Option C 运行关键路径，但不能从质量结论中删除。重新启用 Provider 或 Gold 前必须先重新建立对应基线。

## 12. 项目成本口径

### Calendar duration

- 可证明的证据跨度：2026-07-27 至 2026-08-25，含首尾 30 个日期，elapsed time 为 29 天。
- Phase 2 主链：2026-07-31 至 2026-08-25，含首尾 26 个日期，elapsed time 为 25 天。
- 上述证据跨度内有 18 个发生相关 Git 提交的活跃日期；它不等于连续工作日或实际人时。

### Major rounds

本次时间线列出 22 个里程碑，包含数据观察、Facts、Claims、Provider、Ark/TLS、Option C、MVP、Visual 和产品 UX。里程碑数不是开发迭代总数。

### Commits

当前分支 HEAD 之前包含上游项目历史，不能用仓库总 commit count 代表本项目投入。附录只引用代表性 milestone commits，不虚构“项目总提交数”。

### Agent time and tokens

仓库证据没有完整项目级 Agent 执行时长或 token 账本。因此：

```text
TOTAL_AGENT_DURATION=NOT_AVAILABLE
TOTAL_TOKEN_USAGE=NOT_AVAILABLE
PARTIAL_OBSERVED_AGENT_USAGE=NOT_REPORTED_WITHOUT_REPOSITORY_EVIDENCE
```

这比把若干对话或单任务数字相加更可靠。

## 13. 安全与发布边界

```text
Real Provider         = DEFERRED
AI calls in release   = 0
Real Trading          = DISABLED
Broker API            = NOT CONNECTED
Three-symbol batch    = NOT RELEASED
Cloud deployment      = NOT RELEASED
Automatic publishing  = DISABLED
can_publish           = false
trading_advice        = false
```

本结项任务没有调用 Provider、读取 Secret、执行 TLS Probe、运行真实交易或改写历史 evidence。

## 14. 证据导航

- 管理摘要：`reports/project_closeout/01_EXECUTIVE_SUMMARY.md`
- 主报告：`reports/project_closeout/02_TICKFLOW_PROJECT_CLOSEOUT_REPORT.md`
- 时间线：`reports/project_closeout/03_TICKFLOW_TIMELINE_AND_DECISIONS.md`
- 证据索引：`reports/project_closeout/evidence_index.json`
- 独立复审：`reports/project_closeout/review_findings.md`
- 当前日用手册：`TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md`
