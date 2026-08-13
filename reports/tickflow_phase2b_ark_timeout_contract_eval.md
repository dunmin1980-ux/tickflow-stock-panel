# TickFlow Phase 2B Ark Provider Timeout Contract 评估报告

## 1. 最终状态

```text
PHASE2B_ARK_TIMEOUT_CONTRACT_READY_FOR_REAPPROVAL
```

本轮只修改和验证 Ark Provider 的超时合同。没有安装新 Approval，没有执行真实 Ark Provider 请求，没有读取 Ark Secret 内容，也没有产生真实 AI 调用。

## 2. Timeout Contract

| 项目 | 值 |
|---|---:|
| Provider connect | 10s |
| Provider read | 180s |
| Provider total | 180s |
| Relay wait | 195s |
| Host orchestrator | 210s |
| Cleanup | 15s |
| Retry | 0 |
| Maximum Provider attempts | 1 |

顺序门禁：`180 < 195 < 210`。

Ark 专用 Runtime Contract SHA-256：

```text
3cb3064e4e68bb2e9a07a9153f9c2bcef8de127ac0fd779b2c41e1ab9e0ac681
```

共享 OpenAI Runtime Contract 保持冻结，SHA-256 仍为：

```text
34aa9235d25ecc3f2b658551382f2a8ade031eb84f46cc2a1d79bcbc86bdcf68
```

## 3. 历史请求保全

历史 Ark Request：

```text
2f17745f58534063bdd7eda1eb0d16f1
FAILED_AFTER_DISPATCH / CONSUMED / PRESERVED
WAITING_FOR_PROVIDER_RESPONSE_HEADERS
```

固定事实：连接、TLS 和请求写入均已完成；未收到响应头，未完成响应体。该 request ID 不可复用。

历史 evidence、四份 receipt、rejected 和 ledger 均通过固定 SHA-256 校验。独立历史基线：

```text
reports/phase2_provider_ark/timeout_contract_history_baseline.json
SHA-256: 704a4e4a3341e0c04943468ed02b6ea7c4757bec0794b5946eb5097405698853
```

校验器拒绝历史文件缺失、字节变化、额外文件、叶子符号链接和祖先目录符号链接。旧 Approval Candidate `0fee5a1c509c77ae9b03107dfddf8caefa453673106cced13db4a4d6ac523ffc` 已原字节保存在 `superseded/`。

## 4. 边界与 Mock 验证

虚拟时钟场景：

| 场景 | 虚拟耗时 | 结果 |
|---|---:|---|
| fast response | 1s | SUCCEEDED |
| delayed response | 60s | SUCCEEDED |
| before deadline | 179s | SUCCEEDED |
| exact deadline | 180s | PROVIDER_TIMEOUT |

Relay 在精确 `195s` fail-closed；Ark Host 在精确 `210s` fail-closed。Host 每个门禁只读取一次 monotonic clock；OpenAI 保留原有 `>` 边界语义。

Timeout Mock evidence SHA-256：

```text
047fb70c7dd5e46c4bcb622b24df313b100c626ef935c9255badb7c81911977a
```

三次本地隔离 Docker Mock E2E 全部通过。Timeout evidence 的 Ark receipt identity 从实际归档 receipt 读取，Candidate 生成前会校验完整场景、terminal state、scope ID、attempt、route、validation、residue、类型和零真实调用语义。

## 5. 不可变工件

```text
Proxy image:
sha256:7c74df9f92df2209abaf0f58b8d00075382025a89b00f2061ed9e961da698ae8

Relay image:
sha256:5447846b77d5918d5280ecbcbcfde7fa06ae427af3c82b7cab52edc747ea259c

New Ark Approval Candidate:
c9a305de7f0064a47ff6d3549ee00190120c67428cde1fd9b7f31aace682fbad

New Ark Approval Scope:
008f6b0d0e821d198d9d49f628b13c1b677c852654a21c92101ad77947032bec
```

新 Scope：`historical_attempts=0`、`attempt_availability=AVAILABLE`、对应 attempt 目录不存在。Candidate 和 Docker Mock 绑定同一 orchestrator SHA：

```text
e4eff708671ecde8f9dc8473769dd3ee86a08b8570637ae219d513fade68a964
```

## 6. 验证结果

```text
Ark/Runtime 聚焦回归：255 passed
全新 git archive 专项：49 passed
Backend full：2501 passed
compileall：PASSED
Ruff F821：PASSED
Changed-file Ruff：PASSED
git diff --check：PASSED
敏感形态扫描：CLEAN
独立复审：NO ACTIONABLE FINDINGS
```

专项测试的历史 preflight fixture 只在 `tmp_path` 副本中恢复 Git 无法保存的
`0700/0600` 权限；它不再修改工作区证据，也不依赖当前用户目录状态。

Docker 收尾：

```text
Container residue: 0
Network residue: 0
Temporary/Secret residue: 0
```

## 7. 严格边界确认

```text
new_real_ark_attempts=0
new_real_ai_calls=0
provider_http=NOT_RUN
approval_installed=false
secret_content_read=NO
reasoning_parameters=UNCHANGED
three_symbol_batch=NOT_STARTED
Paper Trading=NOT_STARTED
Obsidian real Vault write=NO
Cloud redeploy=NO
Integrated Gold=DISABLED
```

## 8. 下一动作

```text
REQUEST_ARK_TIMEOUT_HASH_REAPPROVAL
```

只有新的 Candidate、Runtime Contract、Proxy image、Relay image 和完整绑定哈希集合获得重新审批后，才可另行申请一次新的单票真实 Ark Canary。当前报告不构成真实请求授权。
