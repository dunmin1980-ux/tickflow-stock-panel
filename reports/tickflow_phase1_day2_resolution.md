# TickFlow Phase 1.2 Day 2 边界 Schema 解决报告

## 1. 最终状态

```text
PHASE1_OBSERVATION_IN_PROGRESS
```

- Day 1：`DAY_PASSED`
- Day 2 原状态：`DAY_BLOCKED`
- Day 2 最终状态：`DAY_PASSED`
- 解决方式：`OFFLINE_SCHEMA_REMATERIALIZATION`
- 有效交易日：`2/5`
- 剩余交易日：`3`
- 下一动作：`WAIT_FOR_NEXT_REAL_TRADING_DAY_CLOSE`

## 2. Producer / Consumer 缺失字段

旧 Producer 顶层 `boundaries` 使用
`ai_configured_by_phase`，但没有输出：

- `boundary_schema_version`
- `raw_market_values_modified`
- `timestamps_shifted`
- `missing_minutes_filled`
- `ai_configured`

旧 Consumer 已直接要求后三项数据变更字段和 `ai_configured`，因此在行情合同
实际通过后仍以
`BOUNDARY_EVIDENCE_FAILED: raw_market_values_modified` 阻断 Day 2。

Schema 实现提交：
`ff8f66be656d4f87d8b81a6d28d80c83f5fbfe50`。

## 3. 规范 Schema

Producer 现在直接输出唯一的 version 1 顶层合同：

```json
{
  "boundary_schema_version": 1,
  "cloud_redeployed": false,
  "real_key_exposed": false,
  "raw_market_values_modified": false,
  "timestamps_shifted": false,
  "missing_minutes_filled": false,
  "ai_configured": false,
  "paper_trading_started": false,
  "integrated_gold_enabled": false,
  "integrated_gold_external_send_count": 0
}
```

Consumer 对字段存在性和类型做严格检查；布尔字段必须是显式布尔值，Gold
外发计数必须是整数零。

## 4. 旧证据转换方式

`normalize_boundary_evidence_v1()` 对 2026-07-28 的旧证据执行显式转换：

- `ai_configured_by_phase` 映射为 `ai_configured`；
- 固定三票逐一读取
  `contracts.minute_to_30m.symbols.<symbol>.local_1m_to_30m.dual_first_bucket`；
- `raw_values_modified` 映射为 `raw_market_values_modified`；
- `timestamps_shifted` 和 `missing_minutes_filled` 保持同名；
- 三票的三项数据变更字段必须全部存在、类型为布尔且显式为 `false`；
- 任一标的缺字段、值为 `true` 或类型错误均阻断。

转换审计记录：

```text
normalization_applied=true
normalization_version=boundary-v1
source_schema=legacy-phase1.1
source_evidence_hash=443f56d446f6e09849c0a47eba76041643851b5a2a394e2c2a8f22be84d42719
```

## 5. 是否存在默认推断

没有。缺字段不会默认填为 `false`，也不会从文本报告或字段未出现推断安全。
三个固定标的必须逐一提供完整证据。

## 6. 原 DAY_BLOCKED 审计是否保留

已保留，未删除或覆盖：

- `original_blocked_daily_summary.json`
  - SHA-256：
    `62bfba01c2be93b581d0a5bddb7bdd56c017f1e42873f5aedb8108777bed62b3`
- `original_blocked_request_audit.json`
  - SHA-256：
    `72014770169d1ddad00d55326b5c37a0c1c5a8ddc93b296f93ed8e6c28d287da`
- `original_blocked_observation_index.json`
  - SHA-256：
    `d2c23ce6ba90979c52a746b97849aaa459e0ab056af06b6355793ecd3d051c4b`
- `reports/tickflow_phase1_day2_blocker_diagnosis.md`
  - SHA-256：
    `ec43fdd005447f2332c65c7c31ad2156b035a5a3beebd983c288aa4fa863c428`

当前 Day 2 摘要同时记录：

```text
original_status=DAY_BLOCKED
original_blocker=BOUNDARY_SCHEMA_MISMATCH
resolution=OFFLINE_SCHEMA_REMATERIALIZATION
```

## 7. 是否发生第二次 live

没有。

```text
evidence_origin=phase1_2_live_reuse
live_request_reexecuted=false
source_live_request_count=14
new_api_request_count=0
```

重物化进程显式移除了 TickFlow、Tushare 和 DeepSeek API 环境变量，只读取本地
冻结的脱敏结果。相同命令重复执行后所有重物化文件哈希保持不变。

## 8. 请求审计是否变化

累计请求审计 SHA-256 修复前后均为：

```text
4b804ef218d5b7ac936e12814c300230f5cbcf1c691b62800f6d00a9edf3cf0e
```

新增 API 请求为 `0`，没有第二次 live，没有 429 重试，也没有调用
`intraday_batch`。

## 9. Day 2 最终合同

三票均为 `PASSED`：

- 派林生物 `000403.SZ`
- 中金黄金 `600489.SH`
- 东方财富 `300059.SZ`

聚合结果：

- 数据过期：`0`
- material OHLC 异常：`0`
- material negative amount：`0`
- 重复时间戳：`0`
- 午休伪 K：`0`
- 30m OHLC 不一致：`0`
- 非首桶量额不一致：`0`
- 09:30 首桶稳定率：`100%`
- 除权因子通过率：`100%`
- HTTP 429：`0`
- 敏感形态命中：`0`

## 10. 测试与静态验证

- Producer / Consumer 专项：`75 passed`
- 后端全量：`1371 passed`
- `compileall`：`PASSED`
- Ruff F821：`PASSED`
- Bash 语法：`PASSED`
- 离线观察索引验证：`PASSED`
- 本轮变更和证据敏感形态扫描：`PASSED`

## 11. 是否允许进入 Day 3

允许，但只能等待下一个实际 A 股交易日收盘后的安全窗口再执行一次标准
pipeline。不得提前生成 Day 3，不得重跑 Day 2。

继续保持：

```text
AI=NOT_CONFIGURED
Paper Trading=NOT_STARTED
cloud_redeploy=NO
Integrated Gold=DISABLED
external_send_count=0
real_key=NOT_EXPOSED
```
