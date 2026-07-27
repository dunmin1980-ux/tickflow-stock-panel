# TickFlow Phase 1.2 三票分钟数据连续观察设计

日期：2026-07-27

## 1. 目标

对以下三只股票连续观察五个实际交易日，验证 TickFlow Pro 的日线、
除权因子、当日 1 分钟、直接 30 分钟及本地 1 分钟聚合 30 分钟合同是否稳定：

- `000403.SZ` 派林生物
- `600489.SH` 中金黄金
- `300059.SZ` 东方财富

2026-07-27 的 Phase 1.1 正式 live 证据计为 Day 1。该日只复用既有证据，
不得重新调用 TickFlow API，也不得把复用记为新的请求。

## 2. 边界

- 不配置 AI，不开发 Paper Trading，不启用 Telegram，不接 OpenClaw。
- 不开启 Integrated Gold，不修改独立 Gold Shadow。
- 不做全市场同步，不做 12 个月分钟回填，不调用 `intraday_batch`。
- 不修改云端镜像或部署。
- 不修改、填补、平移或归一化原始行情数据。
- 不输出 Key、Cookie、Session、Authorization 或完整请求信息。
- 出现首次 HTTP 429 后立即停止当日 pipeline，不自动重试。
- 本阶段只生成脚本、Day 1 证据和观察计划，不声称未来四日已完成。

## 3. 方案

采用独立离线观察器和薄运行脚本：

1. `scripts/validate_phase1_observation.py`
   - 负责结构化证据校验、Day 1 复用、每日文件生成、索引汇总和最终报告。
   - Day 1 模式完全离线，只读取 Phase 1.1 报告和 JSON 证据。
   - 未来日期模式接收 Phase 1.1 live 校验器生成的本地临时结果，不直接访问网络。
2. `scripts/run_phase1_daily_validation.sh`
   - 负责未来交易日的一次性人工运行。
   - 检查 Asia/Shanghai 时间窗、互斥锁和必要参数。
   - 以单进程、单 pipeline、三票串行、SDK `retry_count=0` 调用既有 live 校验器。
   - 不安装 cron、launchd 或云端定时任务。

不把五日状态管理继续加入
`backend/scripts/validate_phase1_tickflow_contracts.py`，避免一次性数据采集器与长期
观察索引耦合。

## 4. Day 1 复用合同

### 4.1 权威源

- `reports/tickflow_phase1_numeric_contract_closeout.md`
- `reports/phase1_tickflow_request_audit.json`
- `reports/phase1_numeric_contracts/`
- `reports/phase1_data_contracts/`

观察器计算每个源文件的 SHA-256，并保存相对路径、文件大小和哈希。原始文件
不复制、不改写。目录哈希由目录内普通文件的相对路径、大小和 SHA-256
按路径排序后确定。

### 4.2 必须重新判定的条件

Day 1 只有同时满足以下条件才写为 `DAY_PASSED`：

- Phase 1.1 最终状态为
  `PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING`。
- 三票日线的原始与 qfq 最新交易日均为 `2026-07-27`，且日线合同通过。
- 三票 1m 和直接 30m 的最新时间均为 `2026-07-27T15:00:00+08:00`
  或更晚，但不得超过当日。
- 三票 1m 和 30m 时间戳唯一、严格递增，无未来时间戳、无午休伪 K 线。
- material OHLC 异常数量为 0，绝对容差固定为 `1e-10`。
- material negative amount 数量为 0，负值失败阈值固定为 `< -1e-6`。
- 三票直接 30m 均有 8 个完整桶。
- 1m 聚合 30m 的 OHLC 不一致数量为 0。
- 第二桶至尾桶的 volume 和 amount 不一致数量为 0。
- 三票首桶差异均由 `09:30` 行机械精确解释。
- 三票除权因子合同通过，重复调用稳定，qfq 关系稳定且无二次复权。
- Phase 1.1 正式请求数为 14，`retry_count=0`，HTTP 429 数量为 0。
- 正式 Phase 1.1 请求记录不包含 `intraday_batch`。
- 敏感形态扫描通过，真实 Key 未暴露。
- 云端未重新部署，Integrated Gold 关闭且 `external_send_count=0`。

任何字段缺失、类型错误或证据冲突都视为合同失败，不从 Markdown 文本推断缺失
数据。

### 4.3 Day 1 固定元数据

```yaml
observation_date: 2026-07-27
observation_day: 1
status: DAY_PASSED
evidence_origin: phase1_1_reuse
live_request_reexecuted: false
source_status: PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING
source_request_count: 14
new_request_count: 0
source_data_hashes_verified: true
vendor_pending:
  - intraday_batch_entitlement
  - first_30m_bucket_includes_09_30
  - volume_unit
  - amount_unit
```

供应商待确认项保留，但不阻塞 Day 1 计数。

## 5. 每日文件

每个日期目录必须包含：

```text
reports/phase1_observation/YYYY-MM-DD/
├── daily_summary.json
├── 000403SZ_contract.json
├── 600489SH_contract.json
├── 300059SZ_contract.json
├── minute_30m_comparison.json
└── request_audit.json
```

`daily_summary.json` 保存日期、序号、状态、证据来源、判定汇总、失败原因、
供应商待确认项和源证据清单。

逐股合同保存日线、1m、30m、因子、新鲜度和数值阈值的紧凑判定，不复制原始
OHLCV 行。

`minute_30m_comparison.json` 保存每只股票的桶数量、OHLC 差异数量、非首桶
量额差异数量和 09:30 首桶解释结果。

`request_audit.json` 只保存当日 pipeline 的脱敏指标。Day 1 明确写
`new_request_count=0`，同时引用 `source_request_count=14`，不修改
`reports/phase1_tickflow_request_audit.json`。

## 6. 幂等与冲突

- 同一日期、相同源证据哈希重复执行时保持结果不变。
- 同一日期已存在且源证据哈希不同，不覆盖目录，返回
  `OBSERVATION_EVIDENCE_CONFLICT`。
- 先在同级临时目录完成全部文件，再原子重命名为日期目录。
- 只有日期目录完整落盘后才重建索引。
- 索引按观察日期排序，`observation_day` 只对 `DAY_PASSED` 的实际交易日连续
  编号。
- `NON_TRADING_DAY` 不计入有效交易日。

## 7. 状态机

每日状态仅允许：

- `DAY_PASSED`
- `DAY_BLOCKED`
- `NON_TRADING_DAY`

总状态：

- 任一天命中停止条件：`PHASE1_OBSERVATION_BLOCKED`
- 1 至 4 个有效日且无阻塞：`PHASE1_OBSERVATION_IN_PROGRESS`
- 至少 5 个有效日，且聚合阈值全部通过：
  `PHASE1_OBSERVATION_PASSED`

未来日期不能预生成 `DAY_PASSED`。观察器只接受已完成的真实 live 证据。

## 8. 未来交易日流程

1. 操作者在 16:10 至 18:00 Asia/Shanghai 手动运行薄脚本。
2. 脚本获取日期锁，拒绝同日期并发运行。
3. 既有 Phase 1.1 live 校验器执行一次，三票分钟接口逐股串行。
4. 首次 429 立即结束，不进行自动重试。
5. live 输出先写本地临时文件；观察器离线校验并生成日期目录。
6. 临时 live 文件在成功物化后删除；失败时只保留经过脱敏的失败证据。
7. 重建观察索引和最终报告。

运行脚本不持有、不读取或打印完整凭据，只依赖目标实例已经完成的私密配置。

## 9. 汇总输出

主索引：

```text
reports/phase1_observation/observation_index.json
```

最终报告：

```text
reports/tickflow_phase1_observation_final.md
```

索引和报告必须包含有效交易日数、三票状态、数据过期、material OHLC、
material negative amount、30m OHLC、非首桶量额、首桶稳定率、因子、429、
敏感扫描、云端部署、Gold、AI 和 Paper Trading 状态。

Day 1 完成后主报告状态必须为
`PHASE1_OBSERVATION_IN_PROGRESS`，有效交易日为 1，剩余观察日为 4。

## 10. 测试

测试先于实现，并覆盖：

- 完整 Phase 1.1 证据可离线生成 Day 1，且网络调用计数为 0。
- 缺少源文件、字段、三票之一或哈希不一致时拒绝通过。
- 日期过期、收盘时间不足、重复时间戳、午休伪 K 线或未来时间戳会阻塞。
- material OHLC、material negative amount、30m OHLC、非首桶量额异常会阻塞。
- 三票 09:30 首桶精确解释通过；任一票不能解释时阻塞。
- 因子冲突、qfq 关系失败、429、重试或敏感形态命中会阻塞。
- 相同证据重复执行幂等，不同证据拒绝覆盖。
- `NON_TRADING_DAY` 不计数。
- 1 至 4 日为进行中；5 个真实通过日才为最终通过。
- 运行脚本拒绝错误时间窗和并发锁，不使用 `intraday_batch`。

## 11. 验收口径

当前交付只允许声明：

```text
PHASE1_OBSERVATION_IN_PROGRESS
```

当且仅当 Day 1 全部离线合同通过。后续四日必须按实际交易日运行并累积证据，
不得提前声明 `PHASE1_OBSERVATION_PASSED`。
