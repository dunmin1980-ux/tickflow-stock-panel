# TickFlow Phase 1.2 设计一致性审查

审查日期：2026-07-27

权威设计：
`docs/superpowers/specs/2026-07-27-tickflow-phase1-2-observation-design.md`

## 审查结论

收口前实现已满足 Day 1 离线复用、三票固定范围、供应商待确认项保留、
每日与阶段状态枚举、禁止 AI/Paper Trading/通知、证据目录原子发布等核心
约束，但存在四项需在本轮修正的实现偏差：

1. 观察仅完成 1/5 日时仍生成了
   `reports/tickflow_phase1_observation_final.md`，应改为进行中的
   `reports/tickflow_phase1_observation_status.md`。
2. Day 1 与索引使用 `new_request_count`，最新合同要求固定字段
   `new_api_request_count: 0`。
3. 日期锁只覆盖运行中的同日进程，尚未在 SSH/live 入口前拒绝已经存在
   `DAY_PASSED` 或成功请求审计的同日重跑。
4. 索引虽然通过临时文件和 `os.replace` 写入，但尚未完成目录 fsync、
   连续 Day 编号校验、Day 1 来源不可覆盖校验及更新前后哈希证据。

## 逐项检查

| 项目 | 收口前 | 本轮验收要求 |
|---|---|---|
| Day 1 明确复用 Phase 1.1 | 符合 | 保持 `evidence_origin=phase1_1_reuse` |
| Day 1 无新增 API 请求 | 符合 | 固化为 `new_api_request_count=0` |
| 每日状态仅三种 | 符合 | 保持严格枚举 |
| 阶段状态仅三种 | 符合 | 保持严格枚举 |
| 真实交易日和实际证据 | 符合 Day 1 | 后续增加前置日期门禁 |
| 禁止未来日期 | 部分符合 | 在 live 前显式拒绝 |
| 禁止同日重复 live | 部分符合 | 在 SSH 前检查目录、索引和成功审计 |
| 保留 vendor pending | 符合 | 四项原样保留 |
| 禁止自动进入 Paper Trading | 符合 | 保持 `NOT_STARTED` |
| 禁止自动配置 AI/通知 | 符合 | 保持 `NOT_CONFIGURED`，无通知入口 |
| 进行中报告命名 | 不符合 | 1-4 日只生成 `status` |
| 索引原子更新 | 部分符合 | 增加父目录 fsync、失败保留旧索引和哈希证据 |

## 不变边界

- 不调用 TickFlow API，不增加请求审计计数。
- 不修改 Phase 1.1 源证据或累计请求审计。
- 不生成 Day 2 至 Day 5。
- 不修改 `backend/app/**` 或 `frontend/src/**`。
- 不安装定时任务，不连接或重新部署云端。
- 不开启 Integrated Gold，不配置 AI，不开发 Paper Trading，不接通知链路。

最终一致性状态将在
`reports/phase1_observation_implementation_closeout.md` 中依据实际测试结果关闭。

## 收口复核

实现收口后的逐项状态：

| 项目 | 结果 | 证据 |
|---|---|---|
| Day 1 复用且无新增请求 | PASSED | `new_api_request_count=0`，源请求 14 |
| 每日/阶段状态枚举 | PASSED | 验证器严格拒绝枚举外状态 |
| 未来/历史/收盘前门禁 | PASSED | Python 合同测试与 Bash 边界测试 |
| 非交易日零请求 | PASSED | 2026 上交所官方休市表离线判定 |
| 同日重复与成功审计门禁 | PASSED | live 前拒绝 |
| 固定三票 | PASSED | 固定 symbol-set SHA-256 |
| 跨进程锁 | PASSED | 活锁拒绝、死锁恢复、并发最多一次 SSH |
| Day 目录原子发布 | PASSED | staging、文件 fsync、目录 fsync、rename |
| 索引原子更新 | PASSED | 临时文件、fsync、rename、失败保留旧索引 |
| Day 1 来源不可覆盖 | PASSED | 日期、来源、请求数、幂等键和源哈希强校验 |
| 进行中报告命名 | PASSED | 仅 `tickflow_phase1_observation_status.md` |
| vendor pending 保留 | PASSED | 四项完整保留且不阻塞 Day 1 |
| 禁止 AI/Paper/通知 | PASSED | 无对应执行入口，边界状态保持关闭 |

最终设计一致性：`PASSED`
