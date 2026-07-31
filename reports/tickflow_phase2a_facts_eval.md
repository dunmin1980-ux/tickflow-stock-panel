# TickFlow Phase 2A 确定性 Facts 层评估报告

## 1. 最终状态

```text
PHASE2A_FACTS_BLOCKED
```

Phase 2A 的 schema、离线构建器、独立校验器、三票 Facts JSON 和专项测试已建立。三份 Facts 文档的结构、来源哈希和数字溯源均通过校验，但内容就绪状态为：

```text
BLOCKED_SOURCE_EVIDENCE
```

基线身份：

- Phase 2 Base：`ce1f0b221cfd390cafc92a26ef8e16cf7dd3a753`
- 开工前 Head：`690f82870e42051e7f359641c88422cb54f56980`
- 分支：`codex/tickflow-phase2-ai-review`

阻塞原因是 Phase 1 正式落盘证据仅保留数据合同摘要，未保留 Day 5 的 raw 日线 OHLCV 数值和 qfq 日线序列。成功运行的原始载荷位于临时目录，物化合同后已删除。在“不新增 TickFlow API 请求”的硬边界下，无法从已落盘证据重建 MA、MACD、RSI、BOLL、ATR 和量能指标。

## 2. 实现范围

- 固定标的：`000403.SZ`、`600489.SH`、`300059.SZ`。
- 仅读取已提交的 Phase 1 观察索引、Day 5 合同、分钟对比、请求审计和最终报告。
- 每个源文件都记录 SHA-256；拒绝符号链接、仓库外路径、非固定标的和非 `2026-07-31` 证据。
- 每个实际输出数字必须记录源 JSON Pointer 或命名常量、`calculation_function` 和价格口径；直接映射为 `identity`，常量为 `schema_constant`。
- 输出使用规范 JSON、禁止 NaN/Infinity，并以 staging + atomic rename 发布完整目录。
- 未读取旧应用缓存或未跟踪证据，未调用网络、TickFlow 或 AI。

## 3. 交付物

- `backend/app/services/phase2_facts.py`
- `backend/scripts/build_phase2_facts.py`
- `backend/scripts/validate_phase2_facts.py`
- `backend/tests/test_phase2_facts.py`
- `reports/phase2_facts/000403SZ_facts.json`
- `reports/phase2_facts/600489SH_facts.json`
- `reports/phase2_facts/300059SZ_facts.json`
- `reports/phase2_facts/build_manifest.json`

## 4. 三票 Facts 校验

| 标的 | 结构校验 | 数字溯源 | 内容就绪 | 日线指标 |
|---|---|---|---|---|
| 派林生物 `000403.SZ` | VALID | TRACEABLE | BLOCKED_SOURCE_EVIDENCE | NOT_IMPLEMENTED |
| 中金黄金 `600489.SH` | VALID | TRACEABLE | BLOCKED_SOURCE_EVIDENCE | NOT_IMPLEMENTED |
| 东方财富 `300059.SZ` | VALID | TRACEABLE | BLOCKED_SOURCE_EVIDENCE | NOT_IMPLEMENTED |

已保留并可溯源的内容包括：Phase 1 状态、交易日、除权因子合同结果、1m/30m 根数、时间范围、异常计数、1m 聚合 30m 对比结果和 09:30 首桶机械解释。

以下数值必须保持 `null` / `NOT_IMPLEMENTED`：Day 5 OHLCV、MA5/10/20/60、MACD DIF/DEA/HIST、RSI6/14、BOLL 上中下轨、ATR14、量均线、量比和关键价位。

## 5. 来源、口径与安全校验

- Facts 源文件集 SHA-256：`302111ca829458dc0d90ad3223bad45bdd07761666f8461a6e641057dec05d1f`
- Phase 1 证据聚合 SHA-256：`8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5`
- Phase 1 请求审计聚合 SHA-256：`dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d`
- 价格口径：日线 OHLCV=`none`，指标价格=`qfq`，指标成交量=`none`，分钟线=`none`。
- 无来源数字：`0`。
- NaN / Infinity：`0`。
- 源哈希不匹配：`0`。
- 敏感信息命中：`0`。
- 交易建议字段或文本命中：`0`。

## 6. 幂等性与回归

- 相同源证据连续两次构建的目录聚合 SHA-256 均为：`2e3f516cc2b59e8d01a55741c9c18c14bbad1c114131351ee5d825669abe9854`。
- 专项测试：`24 passed`。
- 后端全量回归：`1373 passed, 13 warnings`。
- `compileall app scripts`：PASSED。
- Ruff F821：PASSED。
- 变更文件 Ruff：PASSED。

13 个 warning 为现有 Polars、websockets、datetime 弃用提示和一条 Polars sortedness 警告，本轮未扩大范围处理。

## 7. 不可变性和运行边界

- Phase 1 原始证据：UNCHANGED。
- Phase 1 请求审计：UNCHANGED。
- 新增 TickFlow API 请求：`0`。
- AI 配置：NOT_CONFIGURED；AI 调用：`0`。
- Paper Trading：NOT_STARTED。
- Obsidian 正式写入：NO。
- 云端重新部署：NO。
- Integrated Gold：DISABLED / `external_send_count=0`。
- 真实 Key：NOT_EXPOSED。

## 8. 供应商待确认

以下项继续保留，本轮不自行推断：

- `intraday_batch_entitlement`
- `first_30m_bucket_includes_09_30`
- `volume_unit`
- `amount_unit`

## 9. 结论与下一动作

Facts 容器、数字溯源和校验门禁可作为后续 AI Review 的确定性输入边界，但当前三票 Facts 缺少日线数值和技术指标，不得进入 Phase 2B AI Review。

下一动作为 `DIAGNOSE_BLOCKER`：另行审批一次最小证据保留改造，在不修改本轮 Phase 1 历史证据的前提下，使后续受控采集能落盘 raw/qfq 日线序列及其哈希。该动作不在本轮授权范围内。
