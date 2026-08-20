# TickFlow Paper Trading MVP Runbook

## 1. MVP 是什么

TickFlow Paper Trading MVP 是一个独立、离线、单标的 A 股模拟账户旁路模块。它把已验证的 Facts / Claims 研究状态转换为确定性 Signal 和 Paper Action，按 A 股 T+1 与下一交易日开盘价记录模拟成交、持仓、现金和 PnL。

## 2. 当前支持

- 单标的 `000403.SZ` 派林生物。
- `FACT -> INTERPRETATION -> SIGNAL -> ACTION` 分层。
- `HOLD` 当日记录，不产生订单或成交。
- `BUY` / `SELL` 决策排队后，仅在下一可用交易日开盘执行。
- A 股 T+1、100 股整手买入、`Decimal` 金额、手续费、卖出费、已实现/未实现 PnL 和最大回撤。
- 跨日状态延续、同日幂等、进程锁、中断恢复和原子发布。
- 每日 ChenQuant JSON 与 Markdown。

## 3. 当前不支持

- 不连接券商，不实盘下单。
- 不调用 Ark、OpenAI 或其他真实 Provider。
- 不自动拉取新行情，不自动产生新 Facts / Claims。
- 不支持三票批量、主 API/UI、自动发布、Telegram 或 OpenClaw。
- 不把 `DETERMINISTIC_TEST_FIXTURE` 解释为真实市场证据。

## 4. 启动前检查

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-phase2-ai-review/backend"

test -x .venv/bin/python
PYTHONPATH=. .venv/bin/python -m compileall -q app
```

## 5. 每日运行命令

首次验证冻结的真实 reference：

```bash
mkdir -p data/user_data/phase2_option_c_paper/reference_account

PYTHONPATH=. .venv/bin/python scripts/run_phase2_option_c_daily.py \
  --repo-root .. \
  --state-dir data/user_data/phase2_option_c_paper/reference_account \
  --reference
```

对新的已准备离线输入包：

```bash
PYTHONPATH=. .venv/bin/python scripts/run_phase2_option_c_daily.py \
  --repo-root .. \
  --state-dir data/user_data/phase2_option_c_paper/daily_account \
  --input "/absolute/path/to/YYYY-MM-DD_input.json"
```

`--input` 不会拉取或修正行情。输入包必须由上游离线 Facts / Claims 校验流程准备，且通过 `ContinuousDayInput` 严格身份和时间边界校验。

## 6. 输入位置

- 冻结 reference Facts：`reports/phase2_facts/000403SZ_facts.json`
- 冻结 reference Claims：`reports/phase2_claims/fixtures/000403SZ_claims.json`
- 正式冻结入口：`--reference`
- 新的人工准备离线入口：`--input <absolute-path>`
- 发布测试输入：`reports/phase2_option_c_mvp/lifecycle_state/days/*/input.json`

## 7. 输出位置

运行时输出在 `--state-dir` 内：

```text
<state-dir>/
├── current_state.json
├── .runtime.lock
└── days/YYYY-MM-DD/
    ├── chenquant_daily.json
    ├── chenquant_daily.md
    ├── account_state.json
    ├── position_lots.json
    ├── trade_ledger.json
    ├── decision_ledger.json
    ├── equity_history.json
    ├── input.json
    ├── input_identities.json
    ├── continuous_state.json
    └── run_receipt.json
```

## 8. 状态文件位置

默认建议：

```text
backend/data/user_data/phase2_option_c_paper/<account-name>/
```

`backend/data/**` 已被 Git 忽略，不应将运行账户提交到仓库。

## 9. ChenQuant Daily 在哪里

```text
<state-dir>/days/YYYY-MM-DD/chenquant_daily.json
<state-dir>/days/YYYY-MM-DD/chenquant_daily.md
```

## 10. 如何判断今天运行成功

命令输出是一行 JSON，关键字段必须为：

```json
{
  "status": "DAY_PUBLISHED",
  "simulation_only": "SIMULATION ONLY",
  "can_publish": false,
  "real_provider_attempts": 0,
  "real_ai_calls": 0,
  "real_trading": "DISABLED"
}
```

同日使用完全相同输入重复执行会返回 `DAY_ALREADY_PUBLISHED`，不追加决策或成交。

## 11. HOLD 怎么理解

`HOLD` 是有效研究结果，表示当日没有确定性可执行模拟动作。它会写入 decision ledger，但 `order_id=null`，不产生订单、成交、填单或执行价。

## 12. BUY / SELL 如何模拟执行

- BUY / SELL 当日只写入 `pending_action`。
- 下一交易日必须提供精确的 `execution_fixture`。
- 成交价只能是下一可用交易日 `09:30` 开盘价。
- 发布证据中的 BUY / SELL 仅来自 `DETERMINISTIC_TEST_FIXTURE`。

## 13. T+1 规则

买入成交的 lot 记录 `acquired_trade_date` 和 `sellable_from_trade_date`。当日买入的股份不可当日卖出；卖出量超过 T+1 可卖量时整个转换失败，不会部分写入。

## 14. 如何避免重复运行

- 始终复用同一 `--state-dir`。
- 同日相同 `input_identity` 会幂等返回。
- 同日不同 `input_identity` 会失败关闭，不覆盖旧日录。
- `.runtime.lock` 保证同一账户只有一个进程进入转换。

## 15. 如何恢复中断

直接用原命令重新执行。Runner 会扫描最后一个完整 `days/YYYY-MM-DD/` 快照；即使 `current_state.json` 在中断时未更新，也会从最后完整日恢复并重建指针。

## 16. 如何查看持仓

```bash
python3 -m json.tool \
  data/user_data/phase2_option_c_paper/daily_account/current_state.json
```

重点查看 `account.lots`、`remaining_quantity` 和 `sellable_from_trade_date`。

## 17. 如何查看 PnL

查看当日 `chenquant_daily.json` 的：

```text
position.realized_pnl_cny
position.unrealized_pnl_cny
position.total_equity_cny
position.max_drawdown_cny
```

## 18. 如何查看历史交易

```bash
python3 -m json.tool \
  data/user_data/phase2_option_c_paper/daily_account/days/YYYY-MM-DD/trade_ledger.json
```

每个日目录保存截至该日的完整追加账本。

## 19. 常见错误

- `daily_runner_already_locked`：另一进程正在使用同一账户。
- `published_day_identity_conflict`：已有同日不同输入，不可覆盖。
- `pending_execution_fixture_required`：前一日存在 BUY/SELL pending，但本日缺少开盘执行证据。
- `NO_EXECUTION_PRICE_AVAILABLE`：下一交易日开盘价缺失，不回退到当日收盘价。
- `future_evidence_not_allowed`：Facts / Projection / Signal / 估值数据晚于决策时间。
- `paper_config_mismatch`：现有账户和当前配置身份不一致。

## 20. 如何停止或重置测试账户

停止：不再执行 Daily Runner 即可，没有后台定时任务。

重置测试账户时不要直接删除，先归档：

```bash
account="data/user_data/phase2_option_c_paper/test_account"
archive="${account}.archived-$(date +%Y%m%d_%H%M%S)"
mv "$account" "$archive"
mkdir -m 700 "$account"
```

不得用重置测试账户的方式改写正式日录。

## 21. SIMULATION ONLY

所有账户、日报和发布证据固定：

```text
simulation_only=SIMULATION ONLY
can_publish=false
trading_advice=false
```

任何文件都不构成投资建议。

## 22. Real Provider

```text
Real Provider dependency=NOT_REQUIRED
Real Provider=DEFERRED
Real Provider attempts=0
Real AI calls=0
```

## 23. Real Trading

```text
Real Trading=DISABLED
Broker API=NOT_CONNECTED
Broker calls=0
```

该 MVP 不包含能够将 Paper Action 路由到真实交易的接口。
