# TickFlow Phase 2B Option C 恢复审计

## 状态

```text
RESUME_STATE_AUDIT=PASSED
```

审计日期：`2026-08-20`（Asia/Shanghai）

## 仓库身份

- Worktree：`/Users/macbookpro/Documents/TradingView 量化/tickflow-phase2-ai-review`
- Branch：`codex/tickflow-phase2-ai-review`
- Current Head：`3d2fbac2c723dcb24664af20398537c44eb1dce1`
- Fork remote Head：`3d2fbac2c723dcb24664af20398537c44eb1dce1`
- Local / fork：`MATCHED`
- Worktree：`CLEAN`
- 未提交文件：`0`
- 未推送 commit：`0`

`git fetch --all --prune` 中 `origin` HTTPS 和 fork SSH 发生网络超时，
`upstream` 更新成功。随后使用已登录 GitHub API 只读核验 fork 分支 SHA，
确认与本地完全一致。

## 已有 Option C 工作

设计与计划：

- `docs/superpowers/specs/2026-08-16-tickflow-phase2b-option-c-paper-trading-design.md`
- `docs/superpowers/plans/2026-08-16-tickflow-phase2b-option-c-paper-trading.md`

已有领域模块：

- `backend/app/schemas/phase2_option_c.py`
- `backend/app/services/phase2_option_c_fixture.py`
- `backend/app/services/phase2_research_decision.py`
- `backend/app/services/phase2_paper_trading.py`
- `backend/app/services/phase2_chenquant_daily.py`
- `backend/app/services/phase2_option_c_delivery.py`
- `backend/scripts/build_phase2_option_c_offline.py`

已有专项测试：

- `backend/tests/test_phase2_option_c_fixture.py`
- `backend/tests/test_phase2_research_decision.py`
- `backend/tests/test_phase2_paper_trading.py`
- `backend/tests/test_phase2_chenquant_daily.py`
- `backend/tests/test_phase2_option_c_replay.py`

已有交付目录：`reports/phase2_option_c/`，共 10 个正式工件。

## 已完成模块

- 冻结 Facts、Projection、Typed Claims 与 SHA-256 绑定；
- Deterministic Reference Fixture；
- Typed Claims Validator 和 Renderer 兼容；
- `FACT → INTERPRETATION → SIGNAL → ACTION` 基础分层；
- 独立 Decimal Paper Trading ledger；
- A 股 T+1 基础约束；
- 下一交易日开盘价基础校验；
- 佣金、最低佣金、卖出税费、FIFO 成本和 PnL；
- ChenQuant Daily JSON / Markdown；
- 原子发布和 Replay x3；
- 冻结业务样本重新计算结果：`MIXED_OBSERVATION → HOLD`。

## 恢复基线验证

```text
Option C + Claims / Renderer: 132 passed
compileall: PASSED
Ruff F821: PASSED
git diff --check: PASSED
```

未发现暂停前中止的 pytest、Option C builder、Ark/Provider/Canary 进程。
Docker Desktop 当前未运行，Docker socket 不存在；未发现 Option C staging、
lock 或临时目录残留。

## 本次简报新增缺口

当前实现尚未对以下恢复指令要求建立显式机器合同：

1. `PaperTradingConfig` 名称及 `t_plus_one=true`、
   `execution_policy=NEXT_TRADING_DAY_OPEN` 字段；
2. `order_id` 与独立 `idempotency_key`；
3. idempotency key 对 symbol、decision date、signal、action、fixture、config 的
   明确绑定；
4. Position lot 的 `sellable_from_trade_date`；
5. Trade record 的 `sell_cost_cny`、`cost_basis_cny` 和 `fixture_identity`；
6. 确定性 Market Fixture 的 next-available-day 查找；
7. 缺失下一交易日行情时的 `NO_EXECUTION_PRICE_AVAILABLE`；
8. 独立的 insufficient holdings、duplicate decision/order、projection date
   mismatch 和 deterministic BUY/SELL/HOLD fixture 测试证据。

因此不能仅复用五天前的 READY 结论。

## 下一精确步骤

在现有 Option C 模块上执行 TDD：

```text
先新增上述失败测试
→ 补齐严格 schema 与确定性 Market Fixture
→ 更新 ledger / PnL / Daily / Replay
→ 聚焦回归与独立复审
→ 重新生成 10 个正式工件
```

不创建平行第二套架构，不触碰 Ark/OpenAI/TLS/Proxy/Relay，不接主 API/UI、
券商、实盘、三票批次、云端或正式 Obsidian。
