# TickFlow Phase 2B-3B7 Approval-scoped Attempt Ledger Namespace 评估

## 1. 最终状态

```text
PHASE2B_CANARY_LEDGER_NAMESPACE_READY_FOR_REAPPROVAL
```

本轮仅完成 ledger namespace、离线 Candidate、本地 Mock 和审计证据。未安装新
Approval，未读取 Keychain Secret 内容，未执行真实 OpenAI Provider 请求。

## 2. 核心合同

- `ledger_namespace_version=1`
- Scope 身份：Candidate SHA-256 + symbol + provider + exact model + endpoint alias
- Scope material 分隔符：`\n|\n`，无尾随换行
- 新 ledger：`reports/phase2_provider_canary/attempts/<scope>/<request>/ledger.json`
- 全局锁仍是：`~/Library/Application Support/TickFlowPhase2Canary/runtime-v1/.runtime.lock`
- 同一 scope 发生 `NETWORK_DISPATCH_STARTED` 后永久消耗，换 request ID 不能绕过
- 旧 scope 的 terminal consumed ledger 只读保留，对新 scope 非阻断
- 任何无法解析、非 terminal、孤立 evidence、未知锁或运行残留全局 fail closed
- Request ID 在 legacy/scoped ledger、evidence、rejected、inbox 和 receipts 中全局唯一
- 账本更新使用无覆盖创建与 inode/内容绑定的 fail-closed CAS；竞态写入不会被覆盖
- 历史 runtime evidence 使用严格字段、严格整数类型、canonical JSON 和 route/artifact 交叉校验
- 已完成 inbox 要求 Claims JSON/Markdown 成对，Markdown 必须与 Claims 校验后的确定性重渲染结果逐字节一致
- `FAILED_BEFORE_DISPATCH` 必须有严格的 rejected artifact；缺失时不得恢复 stale lock
- receipt 事件必须为单调时钟上的连续前缀，HTTP status 在 receipt/runtime evidence 间必须一致
- receipt 目录使用无覆盖目标占位和 commit-marker-last 发布，竞态目录不会被替换
- legacy state root 只允许已知 ledger/history/lock 入口，备份别名和未知状态全局阻断

## 3. 历史证据

| Request | 历史状态 | 处理 | Ledger SHA-256 |
|---|---|---|---|
| `d59766101b63450e8148541d589a90bf` | `ATTEMPT_CONSUMED_UNKNOWN` | `CONSUMED / PRESERVED` | `ded0f39813dd70657cedd6a8c92669d3508480e97efc6ed44b1a45f9470b5772` |
| `3d3e6adbe5b74c98ad18a2efe0fd1d0c` | `FAILED_AFTER_DISPATCH` | `CONSUMED / PRESERVED` | `4adcce4ffff6364c0a2552b59be17f6cdd646cb2232d5c02eb16f19716ebf64b` |

结果：两份历史 ledger 和冻结 evidence 的前后哈希均未变。没有删除、移动、
reset、回填字段或复用 request ID。

Request 2 使用观测字段引入前的 receipt 格式，其 Candidate 也是带
`observability_source_sha256` 的 schema-v1 过渡形态。兼容层只对 Candidate
`b30c156b...` 和 Request 2 三份 receipt 的已冻结精确 SHA-256 生效，仅在
内存中补齐后来增加的非敏感语义字段。任一文件变化都会退回严格解码
并 fail closed，不会改写历史字节。

## 4. 当前 Scope 离线门禁

- New Approval Candidate SHA-256：`039551809431a0af3044613295f1df860c3c329001510b793c5017740b1b3e4f`
- Current approval scope ID：`b9fef659680b81c7ed9a0a1e00e31970bc575cb1b2db21b4ae974c59357f7444`
- Current scope historical attempts：`0`
- Current scope availability：`AVAILABLE`
- Global active lock：`NONE`
- Global runtime residue：`container=0 / network=0`
- 769f Approval：`SUPERSEDED_AND_PRESERVED`
- New Approval installed：`NO`

机器证据：
`reports/phase2_provider_canary/ledger_namespace_preflight.json`

## 5. Exact Local Mock E2E

本地 Mock 连续执行 3 次，每次使用从真实 Candidate SHA 和 run index 确定性派生的
Mock-only approval identity。三个 approval scope ID 互不相同，Mock 身份不能被生产
approval loader 接受。

- Completed runs：`3/3`
- Distinct Mock approval scopes：`3`
- Candidate：`VALID` x3
- Claims：`VALID` x3
- Renderer：`DETERMINISTIC`
- Retry：`0` x3
- Mock dispatch：`1` x3
- Real Provider attempts：`0`
- Real AI calls：`0`
- Public network successes：`0`
- Container/network/temporary residue：`0/0/0`

机器证据：
`reports/phase2_provider_canary/local_path_mock_e2e.json`

## 6. 验证结果

- Ledger namespace 专项：`91 passed`
- Ledger namespace + Orchestrator：`168 passed`
- Phase 2 专项：`1047 passed`
- Provider config 治理专项：`89 passed`
- Backend 全量：`2396 passed, 13 warnings`
- Candidate offline build：`PASSED`
- Candidate immutable verify：`PASSED`
- `compileall`：`PASSED`
- Ruff F821：`PASSED`
- `git diff --check`：`PASSED`
- Historical ledger/evidence hashes：`UNCHANGED`
- Independent review round 1：`4 actionable findings / ALL FIXED`
- Independent review round 2：`5 actionable findings / ALL FIXED`
- Independent review round 3：`5 findings / 4 FIXED + 1 EXISTING BINDING PROVEN`
- Independent review round 4：`3 actionable findings / ALL FIXED`
- Independent review final recheck：`NO_ACTIONABLE_FINDINGS`

首轮独立复审发现并已通过回归测试关闭：正常 inbox 缺少内嵌 request ID 时的误阻断、
布尔值冒充整数导致 evidence false READY、账本最终 replace 的竞态窗口，以及报告哈希
过期。修复后重新生成 Candidate、重新执行 Mock x3、namespace preflight、Phase 2 和
后端全量测试。

后续复审累计关闭了布尔值冒充整数、Candidate 重复 key/嵌套字段绑定不足、
receipt/child/evidence 语义不一致、缺失 receipt 目录、inbox Markdown 未确定性重渲染、
`FAILED_BEFORE_DISPATCH` 缺少 rejection、receipt 事件链空洞、HTTP status 不一致、
receipt 目录覆盖竞态与 legacy state 别名绕过。current-scope identity 问题经新增回归测试
证明已由 scoped ledger 模型级 hash 重算绑定覆盖，无需再添加重复实现。

最终独立复审又发现完成态可将 receipt 降级为 `NOT_RUN`、旧 receipt SHA 校验与
解析二次读取的 TOCTOU，以及 receipt 目录创建后未绑定 inode。修复后，
`CLEANUP_COMPLETED` 必须有完整 receipt archive，旧 bundle 使用同一稳定字节快照
完成哈希与解析，目录发布则绑定创建、打开和最终名称的 inode。三项
RED-GREEN 回归通过后，同一复审者的最终结论为 `NO_ACTIONABLE_FINDINGS`。

13 条 warning 均为现有 Polars、websockets、`datetime.utcnow()` 弃用提示或 sortedness
warning，本轮无新测试失败。

## 7. 安全边界

- Provider HTTP：`NOT_RUN`
- Real Provider attempts：`0`
- AI calls：`0`
- Secret content read：`NO`
- DNS/TLS/curl：`NOT_RUN`
- New approval install：`NO`
- Third real Canary：`NOT_RUN`
- Three-symbol batch：`NOT_APPROVED`
- Paper Trading：`NOT_STARTED`
- Obsidian real Vault write：`NO`
- Cloud redeploy：`NO`
- Integrated Gold：`DISABLED / external_send_count=0`

## 8. 下一动作

```text
REQUEST_LEDGER_NAMESPACE_HASH_REAPPROVAL
```

新 Candidate 未安装。在完整哈希集获得新一轮人工重新审批前，不得运行真实
Canary launcher。
