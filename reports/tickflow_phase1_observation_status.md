# TickFlow Phase 1.2 三票分钟数据连续观察

## 当前状态

```text
PHASE1_OBSERVATION_IN_PROGRESS
```

有效交易日：1/5，剩余 4 日
复用观察日：1；Phase 1.2 新增 live 观察日：0

当前只完成真实证据已经落盘的观察日；未预生成未来交易日结果。

## 观察日证据

| 日期 | Day | 状态 | 证据来源 | live 重跑 | 源请求 | 新增 API 请求 | 哈希复核 |
|---|---:|---|---|---|---:|---:|---|
| 2026-07-27 | 1 | DAY_PASSED | phase1_1_reuse | NO | 14 | 0 | PASSED |

Day 1 新增 API 请求：0；该日复用 Phase 1.1 正式 live 证据。

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

供应商仍待确认：`intraday_batch` 权限、30m 首桶是否正式包含 09:30、volume 单位、amount 单位。这些待确认项不改变已验证的 Day 1 计数。
