# TickFlow Phase 2B 单票 Provider Canary 执行报告

## 1. 最终状态

```text
PHASE2B_CANARY_RUNTIME_CONTRACT_BLOCKED
```

用户已明确授权唯一一次真实 Provider Canary，但执行前最后门禁发现已批准运行合同无法完成一次可追溯的端到端请求：OpenAI Proxy 上游超时为 `60s`，Provider Relay 同步等待超时仍固定为 `2s`，且仓库内只有 future Canary 的纯命令合同，没有已验收的真实一次性编排器。

根据“任一门禁不通过即失败关闭”的授权边界，本轮在读取 Secret、启动容器和公网连接之前停止。唯一真实请求未执行，授权未被消耗。

## 2. 执行摘要

| 项目 | 结果 |
|---|---|
| 开工前 Head | `9bba2fd98750350fa00b9b019f0d669445d4a98f` |
| 执行门禁 Head | `32818aa17b5d53c849f67b05da83073dd7b463e4` |
| 标的 | `000403.SZ 派林生物` |
| Trade date | `2026-07-31` |
| Provider | `openai` |
| Exact Model | `gpt-5.6-terra` |
| Endpoint Alias | `openai_responses_v1` |
| Provider attempts | `0` |
| AI calls | `0` |
| Retry | `0` |
| Provider HTTP | `NOT_RUN` |
| Provider store | `false`，已绑定在批准工件 |
| TLS | `NOT_RUN` |
| Egress | `NOT_RUN` |
| Candidate | `NOT_CREATED` |
| Claims | `NOT_RUN` |
| Claim count | `0` |
| Facts Pointer bindings | `0` |
| Unsupported Claims | `0` |
| Free-text fields | `0` |
| Trading Claims | `0` |
| Raw/QFQ | `NOT_RUN` |
| Renderer | `NOT_RUN` |
| Machine validation | `NOT_RUN` |
| Manual review | `NOT_STARTED` |
| Canary route | `NOT_CREATED` |
| Secret hits | `0` |
| Container residue | `0` |
| Network residue | `0` |
| Temporary file residue | `0` |
| TickFlow requests | `0` |
| Obsidian real Vault write | `NO` |
| Paper Trading | `NOT_STARTED` |
| Cloud redeploy | `NO` |
| Integrated Gold | `DISABLED / external_send_count=0` |
| Three-symbol real batch | `NOT_APPROVED` |

## 3. 失败关闭证据

批准前置项仍通过：Git Head 为 `32818aa17b5d53c849f67b05da83073dd7b463e4`，Approval Candidate SHA-256 为 `c7614947da89cae8727ed3c59d1e965dd73bc95450953d3e59c1a89dd0b9f96d`，工作区原始状态为 clean，Provider config、Keychain 存在性、strict JSON Schema、`store=false`、镜像内容、出站策略和九组历史证据均通过离线校验。

阻断条件为：Relay 的 `HTTPConnection(..., timeout=2)` 会在 Proxy 最长 `60s` 的同步 Provider 请求完成前先失败。此时 Provider 尝试可能已发生但响应无法回收给 Host Validator，与 `provider_attempt_count=1`、不重试和必须生成 Candidate 证据的合同冲突。

脱敏运行证据保存在 `reports/phase2_provider_canary/runtime_evidence.json`。本报告不保存完整请求、响应、Authorization header、Secret 或本地绝对路径。

## 4. 下一动作

```text
REMEDIATE_AND_REAPPROVE_END_TO_END_RUNTIME_CONTRACT
```

先将 Relay 等待合同与 Provider `60s` 超时一致，建立可测试、可清理、仅允许一次 Provider attempt 的 Host 编排器，然后重建并重新审批受影响的 Relay / Proxy / Launcher 哈希。新批准完成前不得读取 Secret 或发起 Provider 请求。
