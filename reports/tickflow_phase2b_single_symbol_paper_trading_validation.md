# TickFlow Phase 2B 单票 Paper Trading 验证报告

## 1. 最终状态

```text
PHASE2B_SINGLE_SYMBOL_PAPER_TRADING_VALIDATED
```

本报告只覆盖 `000403.SZ` 派林生物的离线确定性模拟闭环。它不构成投资建议，
不连接 Provider、券商或真实交易，也不批准三票扩展。

## 2. 固定输入与研究结论

```text
source=DETERMINISTIC_REFERENCE_FIXTURE
trade_date=2026-07-31
facts_sha256=adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956
projection_sha256=0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f
claims_identity=66f7ef471b293a4d5453ca53fd91668f217176915e6e88f2ce88f4f037559183
fixture_identity=9d4b6852f5a7a4ad7a0ed933e7039e7628e3c06b7fc452db0565843860ec0a80
paper_config_identity=70d2f4de250df5665f013d48462ac8d905b5811a51d9430f2d10d4ade65fe9bf
```

冻结输入经 Claims Validator 和 Research Decision Layer 重新计算为：

```text
MIXED_TECHNICAL_STRUCTURE -> MIXED_OBSERVATION -> HOLD
```

未修改 Fixture、Claims、Projection 或研究规则，也未使用测试 Fixture 制造交易。

## 3. HOLD 业务结果

| 字段 | 结果 |
|---|---:|
| Formal order count | 0 |
| Formal trade count | 0 |
| Position | FLAT / 0 shares |
| Cash | 100000.00 CNY |
| Realized PnL | 0.00 CNY |
| Unrealized PnL | 0.00 CNY |
| Market value | 0.00 CNY |
| Total equity | 100000.00 CNY |
| Max drawdown | 0.00 CNY |

HOLD 保留 decision、action、signal、Facts、Claims、Fixture 和 idempotency 身份，
但固定 `order_id=null`，不产生订单、成交或执行价格。

## 4. 时间与交易合同

- `PaperTradingConfig` 固定 `lot_size=100`、`t_plus_one=true`、
  `execution_policy=NEXT_TRADING_DAY_OPEN`；
- BUY/SELL 只接受 `DETERMINISTIC_TEST_FIXTURE`；
- 执行 Bar 只包含 09:30 可得 open，schema 禁止 close；
- 日终估值使用独立 `ValuationBar`，要求 `available_at <= valuation_at`；
- 下一交易日 open 缺失时返回 `NO_EXECUTION_PRICE_AVAILABLE`，不回退当日 close、
  不跳日、不猜价；
- BUY/SELL、部分卖出、全部退出、手续费、T+1 和同日卖出阻断均只在测试路径验证，
  未写入正式账本。

## 5. Replay 与日报

相同冻结输入连续运行三次，Research Signal、Action、trade ledger、position、cash、
PnL、equity、drawdown、JSON 和 Markdown 字节全部一致。

```text
SINGLE_SYMBOL_REPLAY_X3=PASSED
replay_sha256=30565345a6a35115bf3602fe047fbeb1162eaa85b22c1dcea351d6716a30c6d5
```

正式 `chenquant_daily.json` 与 `chenquant_daily.md` 均固定：

```text
SIMULATION ONLY
can_publish=false
trading_advice=false
```

## 6. 验证证据

| 门禁 | 结果 |
|---|---:|
| Claims Validator | PASSED |
| Research Decision | PASSED |
| HOLD semantics | PASSED |
| A-share T+1 | PASSED |
| Next-day-open | PASSED |
| Look-ahead / future leakage | PASSED |
| Decimal / fees / PnL / drawdown | PASSED |
| Idempotency / duplicate prevention | PASSED |
| ChenQuant Daily JSON / Markdown | PASSED |
| Replay x3 | PASSED |
| Focused tests | 167 passed |
| compileall | PASSED |
| Ruff F821 | PASSED |
| Independent review | NO P0/P1 ACTIONABLE FINDINGS |

273 个受保护历史文件在验证前后聚合 SHA-256 均为：

```text
caa81d6f500ed69be79bd168244fb9eadad1c465299c717d95c7a3712a212234
```

## 7. 外部行为

```text
TickFlow API requests=0
Real Provider attempts=0
Real AI calls=0
Broker calls=0
Cloud deployments=0
Real trading=DISABLED
Formal ledger contamination=0
```

## 8. 下一动作

```text
DESIGN_CONTINUOUS_SINGLE_SYMBOL_PAPER_RUN
```

本轮到此停止，不自动进入三票、主 API/UI、云部署或真实交易阶段。
