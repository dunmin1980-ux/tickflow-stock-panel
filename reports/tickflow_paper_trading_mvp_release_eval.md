# TickFlow Paper Trading MVP 交付评测报告

## 1. 最终状态

```text
TICKFLOW_PAPER_TRADING_MVP_RELEASE_READY
```

Option C 已从单日确定性 Paper Trading 扩展为可恢复、幂等、连续多日的单标的离线 MVP。实现仍为旁路模块，未接入主 API/UI、真实 Provider、券商或云端部署。

## 2. 业务范围

| 项目 | 结果 |
|---|---|
| 标的 | `000403.SZ` 派林生物 |
| 模式 | `SIMULATION ONLY` |
| 可发布 | `can_publish=false` |
| 交易建议 | `trading_advice=false` |
| A 股 T+1 | PASSED |
| 执行价 | `NEXT_TRADING_DAY_OPEN` |
| 真实 Provider | `NOT_REQUIRED / DEFERRED` |
| 真实交易 | `DISABLED` |

## 3. 单票正式验证

冻结 Facts、Projection 和 Claims 每次重新校验后，确定性研究结果仍为：

```text
MIXED_TECHNICAL_STRUCTURE
-> MIXED_OBSERVATION
-> HOLD
```

HOLD 语义符合合同：

- `order_id=null`；
- 订单数 `0`；
- 成交数 `0`；
- 无虚假 fill 或 execution price；
- 保留 decision、signal、Facts、Claims、fixture 和 account state 身份；
- 现金与总权益均为 `100000.00 CNY`。

## 4. 连续多日生命周期

BUY / HOLD / SELL 只使用独立 `DETERMINISTIC_TEST_FIXTURE`，没有修改正式派林生物 reference。

| 日期 | 决策 | 当日成交 | Pending | 持仓 | 现金 | 总权益 |
|---|---|---|---|---:|---:|---:|
| 2026-08-03 | BUY | 0 | BUY | 0 | 100000.00 | 100000.00 |
| 2026-08-04 | HOLD | BUY 100 @ 10.50 | NONE | 100 | 98945.00 | 100025.00 |
| 2026-08-05 | SELL | 0 | SELL | 100 | 98945.00 | 100065.00 |
| 2026-08-06 | HOLD | SELL 100 @ 11.00 | NONE | 0 | 100039.45 | 100039.45 |

最终结果：

- 已实现 PnL：`39.45 CNY`；
- 未实现 PnL：`0.00 CNY`；
- 最大回撤：`25.55 CNY`；
- 最终持仓：`0`；
- 完整买入、持有、T+1 可卖、卖出和清仓闭环：PASSED。

## 5. 状态和恢复

每个交易日生成不可变完整快照，包含：

- account state；
- position lots；
- trade ledger；
- decision ledger；
- equity history；
- input identities；
- ChenQuant Daily JSON / Markdown；
- run receipt 与完整 artifact hashes。

写入流程为 staging -> fsync -> atomic rename -> directory fsync。如果进程在日目录发布后、`current_state.json` 更新前中断，下一次会从最后完整日快照恢复。持锁运行会清理符合固定命名的 stale staging，未知隐藏残留继续失败关闭。

## 6. Daily Runner

离线命令：

```bash
cd backend

PYTHONPATH=. .venv/bin/python scripts/run_phase2_option_c_daily.py \
  --repo-root .. \
  --state-dir data/user_data/phase2_option_c_paper/reference_account \
  --reference
```

已验证：

- 首次运行 `DAY_PUBLISHED`；
- 同日相同输入 `DAY_ALREADY_PUBLISHED`；
- 同日不同身份失败关闭；
- 跨进程锁冲突在状态转换前拒绝；
- 不调用 Provider、TickFlow API 或 Broker。

完整用法见根目录 `TICKFLOW_PAPER_TRADING_MVP_RUNBOOK.md`。

## 7. ChenQuant Daily

每日 JSON 和 Markdown 均包含：

- Date / Symbol；
- Facts / Claims / Signal / Input / Fixture identity；
- Research Signal / Paper Action / Pending Action；
- Position / Sellable Quantity / Cash / Cost Basis / Market Value；
- Realized / Unrealized PnL / Total Equity / Cumulative Return / Max Drawdown；
- Trades Today；
- Risk Notes / Next Observation Condition；
- `SIMULATION ONLY`、`can_publish=false`、`real_trading=DISABLED`。

## 8. Release Replay x3

从相同空账户状态完整运行四日生命周期，重置后重复三次。下列证据逐字节一致：

- decision ledger；
- trade ledger；
- positions；
- cash；
- realized / unrealized PnL；
- equity history；
- 所有 Daily JSON；
- 所有 Daily Markdown。

```text
MVP_RELEASE_REPLAY_X3=PASSED
Release SHA=eeb0ed2718198bdf2f78cc969eae81a6f48006c2fe5de3ec496d3bc7c6becae2
```

## 9. Release Gate

| 检查 | 结果 |
|---|---|
| Option C + Claims focused tests | `189 passed` |
| Single-symbol validation | PASSED |
| Continuous lifecycle | PASSED |
| T+1 / next-day-open | PASSED |
| Look-ahead / future leakage guards | PASSED |
| Decimal / fees / PnL / drawdown | PASSED |
| Idempotency / duplicate execution | PASSED |
| Crash recovery / append-only state | PASSED |
| Daily Runner / ChenQuant Daily | PASSED |
| Replay x3 | PASSED |
| compileall | PASSED |
| Ruff F821 | PASSED |
| focused Ruff | PASSED |
| git diff --check | PASSED |
| 敏感形态扫描 | CLEAN |
| 独立 P0/P1 focused review | `NO_P0_P1_FINDINGS` |

Backend full 未运行：本轮未修改通用后端核心路径，变更限于 Option C schema/service/script/test/report。

## 10. 证据安全

- 新发布树：`63` 个文件；
- 文件 mode：全部 `0600`；
- symlink：`0`；
- staging residue：`0`；
- 敏感信息命中：`0`；
- 历史保护证据：`273` 个文件；
- 历史证据聚合 SHA：`caa81d6f500ed69be79bd168244fb9eadad1c465299c717d95c7a3712a212234`；
- 开工前/发布后哈希：UNCHANGED。

## 11. P2 Technical Debt

1. 运行时业务合同仅支持 `000403.SZ`。
2. 新鲜每日 Facts / Claims 输入包仍由上游离线流程人工准备。
3. 定时调度和 UI 集成按边界延后。

以上三项不影响当前确定性单标的 Paper Trading MVP 发布门禁，但在扩展到新鲜每日自动输入或多标的前必须另行设计。

## 12. 最终结论

TickFlow Paper Trading MVP 已满足当前单标的、离线、确定性模拟研究闭环。真实派林生物 reference 继续产生合法 HOLD，测试 Fixture 完成 BUY -> HOLD -> SELL 状态、T+1、PnL 和恢复验证。

可进入人工发布/日常离线使用准备，但不得自动进入三票、真实 Provider、主系统、云端部署或实盘交易。
