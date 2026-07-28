# TickFlow Phase 1.2 Day 2 阻断诊断

## 1. 最终状态

```text
PHASE1_OBSERVATION_BLOCKED
```

- 观察日期：`2026-07-28`
- live 执行次数：`1`
- 新增高层 API 请求：`14`
- HTTP 429：`0`
- 自动重试：`0`
- `intraday_batch`：未调用
- 第二次 live：未执行

Day 2 已按 fail-closed 规则记录为 `DAY_BLOCKED`。有效交易日仍为
`1/5`，剩余 `4` 日。

## 2. 阻断原因

阻断不是行情数据失败，而是 live 结果生产者与观察 materializer 的边界
schema 不一致。

本地 consumer 在
`scripts/validate_phase1_observation.py:1247` 校验顶层 `boundaries`，要求：

```text
cloud_redeployed=false
real_key_exposed=false
raw_market_values_modified=false
timestamps_shifted=false
missing_minutes_filled=false
ai_configured=false
paper_trading_started=false
integrated_gold_enabled=false
integrated_gold_external_send_count=0
```

远端 producer 在
`backend/scripts/validate_phase1_tickflow_contracts.py:2237` 输出：

```text
full_market_sync=false
twelve_month_minute_backfill=false
ai_configured_by_phase=false
paper_trading_started=false
cloud_redeployed=false
integrated_gold_enabled=false
integrated_gold_external_send_count=0
independent_gold_shadow_modified=false
real_key_exposed=false
credential_persisted_by_probe=false
```

因此 consumer 在首个缺失字段处停止：

```text
BOUNDARY_EVIDENCE_FAILED: raw_market_values_modified
```

`raw_market_values_modified`、`timestamps_shifted` 和
`missing_minutes_filled` 的 `false` 证据存在于逐股
`local_1m_to_30m.dual_first_bucket` 内，但未提升到 producer 顶层
`boundaries`。`ai_configured` 则使用了不同键名
`ai_configured_by_phase`。

现有测试分别验证了 producer 和 consumer 自身，但 consumer fixture
直接构造了理想边界字段，没有用 producer 的真实输出做端到端兼容测试。

## 3. 行情合同结果

远端脱敏结果自身状态：

```text
PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING
```

- 最近交易日：`2026-07-28`
- 三票原始/前复权日线：`3/3 PASSED`
- 三票除权因子：`3/3 PASSED`
- 每票 1m：`241` 根，最后时间 `15:00`
- 每票直接 30m：`8` 根，最后时间 `15:00`
- 1m 本地聚合 30m：逐股 `passed=true`
- 30m OHLC 合同：逐股 `PASSED`
- material OHLC 异常：`0`
- material negative amount：`0`
- 重复时间戳：`0`
- 午休伪 K 线：`0`
- 五档一次性合同：`PASSED`

首桶 volume/amount 口径仍是 `VENDOR_CONVENTION_PENDING`，与既有
vendor pending 口径一致。本次阻断发生在边界 schema 校验阶段，本地
materializer 没有继续把上述行情合同计入 Day 2 通过指标。

## 4. 安全与证据

- 远端认证闸门：`PASSED`
- TickFlow Key：已配置但未输出
- AI Key：未配置
- `auth.json` / `secrets.json`：普通文件，`0600`
- Integrated Gold：`DISABLED`
- `external_send_count=0`
- 云端重新部署：`NO`
- 最近日志敏感形态命中：`0`
- 新生成观察证据敏感形态命中：`0`
- live 结束后远端验证进程：`0`
- runtime lock：已释放

原 Phase 1.1 请求审计保持不变：

```text
4b804ef218d5b7ac936e12814c300230f5cbcf1c691b62800f6d00a9edf3cf0e
```

保留的脱敏 live 结果：

```text
/var/folders/ln/hd9n0j9910s4htp7296nwqbw0000gn/T/tickflow-phase1-observation.3FfsSk
SHA-256: 443f56d446f6e09849c0a47eba76041643851b5a2a394e2c2a8f22be84d42719
mode: 0600
```

Day 2 紧凑证据已保存到：

```text
reports/phase1_observation/2026-07-28/
```

## 5. 建议的后续处理

本轮不修复、不删除阻断记录、不重跑 API。

下一独立任务建议：

1. 给 producer 的 `boundaries` 增加 consumer 需要的四个规范字段；
2. 增加“真实 producer 输出直接进入 consumer”的端到端离线测试；
3. 设计保留原 `DAY_BLOCKED` 审计轨迹的受控修复流程；
4. 修复验证后优先复用本次脱敏 live 结果离线重物化，避免再次消耗 API；
5. 未完成上述治理前，不继续 Day 3，也不把 Day 2 计入有效观察日。

继续保持：

```text
AI=NOT_CONFIGURED
Paper Trading=NOT_STARTED
Integrated Gold=DISABLED
cloud_redeploy=NO
```
