# TickFlow Phase 1.2 三票分钟数据连续观察

## 当前状态

```text
PHASE1_OBSERVATION_IN_PROGRESS
```

有效交易日：4/5，剩余 1 日
复用观察日：1；Phase 1.2 新增 live 观察日：3

当前只完成真实证据已经落盘的观察日；未预生成未来交易日结果。

## 观察日证据

| 日期 | Day | 状态 | 执行时段 | 证据来源 | 源 live | 原始 live | 观察 live | 重物化 live | 重复 live | 新增 API 请求 |
|---|---:|---|---|---|---|---|---|---|---|---:|
| 2026-07-27 | 1 | DAY_PASSED | - | phase1_1_reuse | YES | NO | NO | NO | NO | 0 |
| 2026-07-28 | 2 | DAY_PASSED | - | phase1_2_live_reuse | NO | YES | NO | NO | NO | 0 |
| 2026-07-29 | 3 | DAY_PASSED | LATE_SAME_DAY | phase1_2_daily_live | NO | NO | YES | NO | NO | 14 |
| 2026-07-30 | 4 | DAY_PASSED | LATE_SAME_DAY | phase1_2_daily_live | NO | NO | YES | NO | NO | 14 |

Day 1 新增 API 请求：0；该日复用 Phase 1.1 正式 live 证据。

Day 2 保留原 DAY_BLOCKED 审计，并通过 OFFLINE_SCHEMA_REMATERIALIZATION 复用唯一一次 live 证据；新增 API 请求为 0。

Day 3 于 2026-07-29T21:44:21+08:00 启动，2026-07-29T21:44:52+08:00 完成；执行时段为 LATE_SAME_DAY，行情与安全合同仍按原阈值验收。

Day 4 于 2026-07-30T20:58:32+08:00 启动，2026-07-30T20:58:41+08:00 完成；执行时段为 LATE_SAME_DAY，行情与安全合同仍按原阈值验收。

## 三票状态

| 标的 | 状态 |
|---|---|
| 派林生物 `000403.SZ` | PASSED |
| 中金黄金 `600489.SH` | PASSED |
| 东方财富 `300059.SZ` | PASSED |

## 聚合合同

- 数据过期：0
- material OHLC 异常：0
- material negative amount：0
- 重复时间戳：0
- 午休伪 K 线：0
- 30m OHLC 不一致：0
- 非首桶量额不一致：0
- 09:30 首桶稳定率：100.00%
- 除权因子通过率：100.00%
- HTTP 429：0
- 敏感形态命中：0

## 边界

AI：NOT_CONFIGURED

Paper Trading：NOT_STARTED

云端重新部署：NO

Integrated Gold：DISABLED / external_send_count=0

真实 Key：NOT_EXPOSED

供应商仍待确认：`intraday_batch` 权限、30m 首桶是否正式包含 09:30、volume 单位、amount 单位。这些待确认项不改变已验证的 有效观察日计数。
