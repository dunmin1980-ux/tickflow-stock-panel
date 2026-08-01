# TickFlow Phase 2A 确定性 Facts 层评估报告

## 1. 最终状态

```text
PHASE2A_FACTS_READY
```

Phase 2A 已完成独立日线快照固化、确定性指标计算、全数字溯源、幂等与回归验证。三票 Facts 均为 `VALID`，可作为下一阶段去交易化 AI Review 的确定性输入。

基线身份：

- Phase 2 Base：`ce1f0b221cfd390cafc92a26ef8e16cf7dd3a753`
- 本次恢复开工前 Head：`7ba58c325612d74556f5841c413451089616f688`
- 快照提交：`cd31cc8` (`feat: add existing cloud snapshot source for phase2 facts`)
- 分支：`codex/tickflow-phase2-ai-review`

## 2. 独立来源决策

指标日线来源固定为：

```text
source_type=existing_cloud_store_snapshot
source_store_alias=tickflow_cloud_primary
phase1_original_payload_recovered=false
phase1_byte_equivalent=false
```

**该云端快照不是已经遗失的 Phase 1 临时原始载荷副本。它是 Phase 2 使用的独立既有数据来源。**

Phase 1 五日观察仍只用于证明分钟数据、复权因子、1m 聚合 30m、限频和安全合同的稳定性；不将独立快照重标为 Phase 1 原始证据。

## 3. 云端只读访问证据

- 访问方式：通过 SSH 将无网络导出器流入现有容器 Python stdin，bundle 仅从 stdout 返回。
- 远端文件写入：`0`。
- 行情任务触发：`0`。
- TickFlow API 新请求：`0`。
- 云端部署、重启或任务状态修改：`0`。
- 导出前后对白名单源文件执行 SHA-256 一致性校验。
- 导出模块静态禁止 HTTP、TickFlow、AI 和数据库客户端依赖。

同日任务元数据：

| 字段 | 值 |
|---|---|
| task id | `e1c347277f` |
| started | `2026-07-31T07:30:00Z` |
| completed | `2026-07-31T07:32:38Z` |
| daily sync | `success` |
| enriched calculation | `success` |
| capability | `Pro` |
| daily provider | `tickflow` |
| adjustment source | `same_as_daily` |

`preferences.json` 未显式保存两个 provider 键，导出器按应用现有确定性默认语义解析，并记录 `application_default_absent`，未将默认值冒充为显式用户配置。

## 4. 快照合同

| 标的 | raw | qfq | 日期范围 | 结果 |
|---|---:|---:|---|---|
| 派林生物 `000403.SZ` | 251 | 251 | 2025-07-21 至 2026-07-31 | VALID |
| 中金黄金 `600489.SH` | 251 | 251 | 2025-07-21 至 2026-07-31 | VALID |
| 东方财富 `300059.SZ` | 251 | 251 | 2025-07-21 至 2026-07-31 | VALID |

快照校验覆盖：代码与名称、日期严格递增与唯一、raw/qfq 日期集一致、OHLC 边界、量额非负、无未来日期、无 NaN/Infinity、provider/任务元数据、源分区哈希、输出哈希、敏感字段和交易字段。

- 规范化业务 SHA-256：`b61bf121fcc998e2f0bfa49c8b4e8b9abfdb81e8e7ae081b625bf12bd2321095`
- raw 分区聚合 SHA-256：`4fd9f74299533d78a1be695319e827a68de6ffe491403fa8d52af2682555f5a4`
- qfq 分区聚合 SHA-256：`755862c8daba3bbc3d9d4cec4aa1a1e80d02db6a4984d85870c5c737b5eff8c8`
- 两次独立只读导出：`IDEMPOTENT`
- 六份输出哈希、两类源分区哈希和业务哈希：全部一致；仅 `captured_at` 不同。

## 5. 指标口径与溯源

全部指标复用 `app.indicators.pipeline.compute_indicators`，未复制第二套算法。

| 指标 | 输入口径 | 主要参数 | 结果 |
|---|---|---|---|
| MA5/10/20/60 | qfq close | simple rolling 5/10/20/60 | VALID |
| MACD DIF/DEA/HIST | qfq close | 12/26/9, histogram x2 | VALID |
| RSI6/14 | qfq close | Wilder-style EWM alpha=1/n | VALID |
| BOLL upper/middle/lower | qfq close | 20, 2 sigma, ddof=1 | VALID |
| ATR14 | qfq high/low/previous close | Wilder EWM 14 | VALID |
| volume MA5/10 | raw volume | simple rolling 5/10 | VALID_WITH_UNIT_PENDING |
| volume ratio | raw volume | current / prior 5-day mean | VALID_WITH_UNIT_PENDING |

每个数字记录了来源文件与 SHA-256、JSON Pointer 或计算函数、输入字段、参数、价格/数据口径、历史窗口和 `float64_unrounded` 精度。无来源数字为 `0`。

成交量单位保持 `VENDOR_CONFIRMATION_PENDING`；量比标记 `dimensionless=true` 和 `source_unit_unconfirmed=true`。amount 未参与需单位换算的派生指标。

关键价位继续冻结：

```text
key_levels.status=NOT_IMPLEMENTED
observed_supports=[]
observed_resistances=[]
```

## 6. Facts 验证

| 标的 | Facts | 数字溯源 | 日线指标 |
|---|---|---|---|
| 派林生物 `000403.SZ` | VALID | TRACEABLE | VALID |
| 中金黄金 `600489.SH` | VALID | TRACEABLE | VALID |
| 东方财富 `300059.SZ` | VALID | TRACEABLE | VALID |

- Facts 源集 SHA-256：`eb9b721dff7348e164ceeb3f33267dc1b86fcff4e23212ebdc892639ac9b35df`
- 派林生物 Facts：`adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956`
- 中金黄金 Facts：`2372a4aef60c340465d5492385a52785e8f6c02407376cac0821eb5a6f6c9409`
- 东方财富 Facts：`696ba210937c6ca5432c39920eebf1ca09ce8308fea3a7a94e86ab698bcad5af`
- 重复生成：`IDEMPOTENT`
- 无来源数字：`0`
- NaN / Infinity：`0`
- 源哈希不匹配：`0`
- 敏感信息命中：`0`
- 交易字段/文本命中：`0`

## 7. 不可变性与回归

- Phase 1 观察证据聚合 SHA-256：`8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5` (`UNCHANGED`)
- Phase 1 请求审计聚合 SHA-256：`dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d` (`UNCHANGED`)
- Phase 2A 专项：`67 passed`
- Facts 专项：`37 passed`
- 快照/原子发布专项：`30 passed`
- 后端全量：`1416 passed, 13 warnings`
- `compileall app scripts`：PASSED
- Ruff F821：PASSED
- 变更文件 Ruff：PASSED

13 个 warning 为现有 Polars、websockets、`datetime.utcnow()` 弃用提示和一条 Polars sortedness 警告，本轮未扩大范围处理。

提交前独立代码审查发现的 4 个 Important 问题已全部关闭：既有目录更新改用 macOS `renamex_np(RENAME_SWAP)` / Linux `renameat2(RENAME_EXCHANGE)` 原子交换；Facts 聚合来源由三份文档重建并对照磁盘哈希；分区路径严格限定为 `date=YYYY-MM-DD/part.parquet`；Facts 目录在 `resolve()` 前拒绝 symlink。同时将固定标的的未来日期行由静默忽略改为硬失败。

## 8. 供应商待确认

以下项继续保留，不阻塞三票相对指标计算，但在全市场扩展或模拟撮合前仍须获得官方确认：

- `intraday_batch_entitlement`
- `first_30m_bucket_includes_09_30`
- `volume_unit`
- `amount_unit`

## 9. 冻结边界

- AI：NOT_CONFIGURED；AI 调用：`0`。
- Paper Trading：NOT_STARTED。
- Obsidian 正式写入：NO。
- 云端重新部署：NO。
- Integrated Gold：DISABLED / `external_send_count=0`。
- Telegram / OpenClaw：NOT_CONNECTED。
- 真实 Key：NOT_EXPOSED。

## 10. 结论

Phase 2A 已达到 `PHASE2A_FACTS_READY`。三票 raw/qfq 快照来源可追溯、只读且可重复；日线数值和指标由同一正式流水线确定性生成，Facts 与 Phase 1 合同证据分域明确。

下一动作为 `READY_FOR_PHASE2B_AI_REVIEW`，本轮在此停止，不开始 AI Review。
