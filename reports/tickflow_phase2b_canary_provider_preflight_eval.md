# TickFlow Phase 2B-3B Provider Canary 安全预门禁报告

## 1. 结论

```text
PHASE2B_CANARY_EGRESS_BLOCKED
```

本轮仅执行纯离线预门禁，没有读取 Keychain Secret 内容，没有发起 Provider、AI、TickFlow 或其他公网请求。

阻断原因不是用户审批配置或 Keychain 缺失，而是当前实际运行时 Proxy 仍是 Mock-only HTTP 实现，不是已审核、已锁定的 OpenAI HTTPS 适配器。因此不得标记 `CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL`，也不得发起单次真实请求。

## 2. 配置与 Secret 门禁

| 项目 | 结果 |
|---|---|
| Provider | `openai` |
| Exact Model | `gpt-5.6-terra` |
| Endpoint Alias | `openai_responses_v1` |
| 审批 Endpoint | `POST https://api.openai.com:443/v1/responses` |
| Provider 配置 | `VALID` |
| 目录 / 文件权限 | `0700 / 0600`，`PASSED` |
| 配置 Secret 形态扫描 | `CLEAN` |
| Keychain Secret | `PRESENT` |
| Secret 内容读取 | `NO` |
| Secret 哈希记录 | `NO` |
| 临时只读单文件注入预演 | `PASSED` |
| Secret 日志命中 | `0` |
| Secret 产物命中 | `0` |
| Secret 临时残留 | `0` |

存在性检查只使用 `security find-generic-password` 的非输出模式；未使用 `-w`，也未记录长度、前后缀或哈希。

## 3. Responses 合同

| 项目 | 结果 |
|---|---|
| 严格 JSON Schema | `READY` |
| Schema 来源 | `WorkerClaimsCandidate` |
| Streaming | `false` |
| Tools / Web / Files | `disabled` |
| Retry | `0` |
| Max Provider Attempts | `1` |
| Symbol | `000403.SZ` |
| Can Publish | `false` |

请求合同只在内存中构建并校验，未序列化为真实 Provider 请求，未打开 socket。

## 4. 实际 Proxy 阻断证据

| 项目 | 实际值 |
|---|---|
| Runtime status | `MOCK_ONLY` |
| Connection | `HTTPConnection` |
| Host | `phase2-mock-provider` |
| Port | `8081` |
| Path | `/v1/typed-claims` |
| TLS verification | `false` |
| Redirect | `disabled` |
| Retry | `0` |
| Proxy source SHA-256 | `abe567151bfe8321bd5a65e1d043ad5f180647c00b7e5b84b17e32214f6c4df6` |
| Source hash approved | `false` |
| Image ID approved | `false` |
| Image/source label binding | `false` |
| Egress allowlist | `FAILED` |

预门禁的 `READY` 路径必须同时满足：

1. Proxy 源码结构精确强制 `POST https://api.openai.com:443/v1/responses`；
2. Proxy 源码 SHA-256 与独立审核值一致；
3. 本地运行镜像 ID 与独立审核值一致；
4. 镜像中的源码哈希标签与实际 Proxy 源文件一致。

当前没有获批的真实 Proxy 源码哈希和镜像 ID，所以即使出现看似正确的 HTTPS 常量，也只能进入 `UNPINNED` 或 `IMAGE_UNVERIFIED`，不能自证通过。

## 5. Facts 与历史证据

| 项目 | 结果 / SHA-256 |
|---|---|
| Facts | `VALID` / `adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956` |
| Trade date | `2026-07-31` |
| Projection | `VALID`, repeated generation stable |
| Projection self SHA | `0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f` |
| Projection bytes SHA | `5db35e91eabce452e5be3a3020b5c10711797941431df9bd5bb52f67625b42e1` |
| Claims Schema SHA | `5988b34ad5bb1703e3795e9b54a18c7dcdb0009c65ee55662f7e71facebc43c3` |
| Renderer SHA | `53d94e60fce7914582c85beea977ca65f7a6713c7066ee65df2c84a8ec6c361e` |
| Provider Relay | `PHASE2B_PROVIDER_RELAY_READY` |
| Canary output | `EMPTY` |
| 九组受保护证据 | `UNCHANGED` |

受保护集合包括 Phase 1 观察、Phase 1 请求审计、Phase 2 Facts、Typed Claims、Typed Inbox、历史 AI Markdown、历史 rejected、Isolation Runtime Evidence 和 Provider Relay Evidence。

## 6. 外部行为与残留

| 项目 | 结果 |
|---|---:|
| Provider attempts | 0 |
| AI calls | 0 |
| Retry | 0 |
| Provider HTTP | `NOT_RUN` |
| TickFlow API requests | 0 |
| Real public network successes | 0 |
| Provider container residue | 0 |
| Provider network residue | 0 |
| Provider process residue | 0 |
| Obsidian real vault writes | 0 |
| Paper Trading | `NOT_STARTED` |
| Cloud redeploy | `NO` |
| Integrated Gold | `DISABLED / external_send_count=0` |
| Three-symbol real batch | `NOT_APPROVED` |

零计数证据来自未变的 Phase 1 请求审计、未变的 Provider Relay 运行证据、空 Canary 输出目录和本地残留检查。

## 7. 验证

| 校验 | 结果 |
|---|---|
| Provider 安全预门禁专项 | `61 passed` |
| 后端全量 | `1878 passed, 13 warnings` |
| `compileall app scripts` | `PASSED` |
| Ruff F821 | `PASSED` |
| 新增文件聚焦 Ruff | `PASSED` |
| `git diff --check` | `PASSED` |
| 新增产物敏感形态扫描 | `CLEAN` |
| 独立安全代码复审 | `NO ACTIONABLE FINDINGS` |

## 8. 下一动作

```text
IMPLEMENT_AND_AUDIT_REAL_OPENAI_HTTPS_PROXY_OFFLINE
```

下一阶段必须作为单独的纯离线开发与审核任务：实现真实 OpenAI Responses HTTPS 适配器，完成严格 Endpoint/TLS/重定向/超时/响应大小策略测试，构建带源码哈希标签的镜像，独立审核后锁定源码与镜像哈希。在该任务完成且用户再次明确批准前，不得请求最终单次真实 Canary 执行。
