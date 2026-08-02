# TickFlow Phase 2B-3B1 Canary 运行合同评估

## 1. 最终状态

```text
PHASE2B_CANARY_RUNTIME_CONTRACT_READY_FOR_REAPPROVAL
```

本阶段仅完成离线运行合同修复、Mock 故障注入、不可变镜像重建和新审批候选生成。未执行 OpenAI Provider 请求，未读取 Keychain Secret 内容，未安装新运行审批。

## 2. 运行合同

| 字段 | 固定值 |
|---|---:|
| `canary_runtime_contract_version` | 1 |
| Provider connect timeout | 10s |
| Provider read timeout | 60s |
| Provider total timeout | 60s |
| Relay Candidate wait | 75s |
| Host orchestrator timeout | 90s |
| Cleanup timeout | 15s |
| Retry | 0 |
| Maximum Provider attempts | 1 |

关系 `60 < 75 < 90` 已由严格 schema 和测试固定。Host、Relay 和 Proxy 使用字节一致的提交版本，不允许运行时环境变量覆盖。

Runtime Contract SHA-256：

```text
34aa9235d25ecc3f2b658551382f2a8ade031eb84f46cc2a1d79bcbc86bdcf68
```

## 3. 唯一请求语义

- One-shot Orchestrator：`READY`
- 全局独占锁：`PASSED`
- fsync + atomic rename 持久化账本：`PASSED`
- dispatch 前崩溃：`NO_ATTEMPT_CONSUMED`
- dispatch 后崩溃：`ATTEMPT_CONSUMED`
- 无法证明是否发送：`ATTEMPT_CONSUMED_UNKNOWN`
- 自动重试：`0`
- 同一时刻只允许一个 request ID 和一条 Provider 调度路径。

调度顺序已复核：Proxy 在 prepare 阶段只启动监听；Relay 只在账本原子进入 `NETWORK_DISPATCH_STARTED` 后启动。任何超时、Candidate 失效、Host 校验失败或清理失败都不允许继续或重试。

## 4. Candidate 与镜像

Fresh build 参数：

```text
build_network=none
pull=false
no_cache=true
base_digest_pinned=true
```

Pinned base：

```text
gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5
```

| 工件 | ID / SHA-256 |
|---|---|
| Proxy image | `sha256:8fef193a24525906bbd0caa5f15ba52009e0b3e76c66bea913875c6941f3eb6e` |
| Relay image | `sha256:a9aedf3ed19689ecd967e00ded38a9e41e644e78a3de256602fc47983588be24` |
| Orchestrator source | `4e7607b2c185faab69a2aa5c930e4a61c970fa271882ce4c8a1ce1d67f7cfa5a` |
| Facts | `adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956` |
| Projection | `0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f` |

新 Approval Candidate SHA-256：

```text
e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b
```

旧 Candidate 仍保留，SHA-256 为：

```text
c7614947da89cae8727ed3c59d1e965dd73bc95450953d3e59c1a89dd0b9f96d
```

旧本地审批已原子重命名为 superseded 普通文件，mode `0600`，内容未读取。新 `runtime-contract-approval.json` 不存在，因此 launcher 仍会失败关闭。

## 5. 故障注入与边界

20 类指定 Mock 故障均已有自动化覆盖，包括 1/59/60/>60 秒边界、Relay/Proxy/Host 崩溃、Candidate 部分写入、原子 rename 前失败、无/多 Candidate、Cleanup 超时、重复 request ID、并发 Orchestrator、遗留 Ledger/锁/网络/容器。

所有失败场景均保持：

```text
retry_count=0
second_provider_request=0
failed_route_to_inbox=0
```

Candidate 发布使用临时文件、fsync、atomic rename 和 ready marker；Host 不读取部分 Candidate。

## 6. Secret 生命周期

机器测试仅使用占位 Secret。真实 Keychain Secret 未读取。实现固定为：

```text
Keychain -> Host 读取一次 -> 0600 临时单文件
-> Proxy 只读挂载 -> 结束后删除 -> 残留扫描
```

Relay 不持有 Secret；Secret 不进入环境变量、CLI 参数、Docker label、receipt、日志、哈希或报告。清理失败时禁止生成成功状态。

## 7. 验证结果

| 验证 | 结果 |
|---|---|
| Orchestrator + Runtime Contract 专项 | `55 passed` |
| Relay + Artifact + 兼容性专项 | `141 passed` |
| 后端全量 | `2210 passed`, 13 warnings |
| `compileall` | `PASSED` |
| Ruff F821 | `PASSED` |
| Runtime Contract `--check` | `PASSED` |
| Immutable Artifact `--verify` | `PASSED` |
| 敏感形态扫描 | `PASSED` |
| `git diff --check` | `PASSED` |

全量测试首轮暴露了旧 Mock Provider 在客户端超时主动断开后可能输出 `BrokenPipeError` 堆栈的稳定性问题。发送边界增加对预期断连的最小处理后，目标测试连续通过 3 次，最终全量通过。该修复不在 OpenAI Proxy/Relay 真实路径上，不改变新 Candidate 哈希。

## 8. 不变性与残留

| 项目 | 结果 |
|---|---|
| Phase 1 观察证据与请求审计 | `UNCHANGED` |
| Phase 2 Facts | `UNCHANGED` |
| Typed Claims / inbox | `UNCHANGED` |
| 历史 AI Markdown / rejected | `UNCHANGED / PRESERVED` |
| Isolation Runtime / Provider Relay 证据 | `UNCHANGED` |
| 历史 Canary runtime evidence | `UNCHANGED` |
| 容器残留 | `0` |
| 网络残留 | `0` |
| Secret 文件残留 | `0` |
| 临时文件残留 | `0` |
| `live_canary` 路由 | `ABSENT` |

外部行为计数：

```text
provider_attempt_count=0
ai_call_count=0
provider_http=NOT_RUN
tickflow_requests=0
real_public_network_successes=0
obsidian_real_vault_writes=0
paper_trading=NOT_STARTED
cloud_redeploy=NO
integrated_gold=DISABLED
```

## 9. 结论与下一步

运行合同修复已达到重新审批条件，但它仍只是新的不可变审批候选。下一动作仅为：

```text
REQUEST_RUNTIME_CONTRACT_HASH_REAPPROVAL
```

在新 Candidate 完整哈希集合被另行明确批准、本地 `0600` 审批记录安装之前，不得执行 launcher 或任何真实 Provider 请求。
