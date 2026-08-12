# TickFlow Phase 2B OpenAI HTTP 401 诊断报告

## 1. 结论

```text
PHASE2B_CANARY_PROVIDER_BLOCKED
```

Current Approval Scope 的唯一一次真实 Provider attempt 已被消费，不得重试。
OpenAI 上游在 TLS 和请求写入完成后返回 `HTTP 401`，但当前安全
Proxy 按设计在非 2xx 响应头阶段终止，不读取或保存错误响应正文。
因此本地证据不足以在以下分支中做唯一归因：

1. Canary 专用 API Key 无效、已撤销或已被替换；
2. OpenAI 组织启用 IP allowlist，当前出口 IP 未授权；
3. legacy user key 需要明确的 organization/project 上下文，或 Key 属于不同项目。

`gpt-5.6-terra` 是官方有效模型，并支持 Responses API 和 Structured
Outputs，因此“模型 ID 不存在”不是本次 401 的合理主因。

## 2. 冻结运行身份

| 项目 | 值 |
|---|---|
| Current Head | `f55a0e6ca6fe666c0ccf8ec797f08aa8150c6dcb` |
| Approval Candidate | `039551809431a0af3044613295f1df860c3c329001510b793c5017740b1b3e4f` |
| Approval Scope | `b9fef659680b81c7ed9a0a1e00e31970bc575cb1b2db21b4ae974c59357f7444` |
| Request ID | `9728fc1f156c4568a98cea9c05ec9fa7` |
| Symbol | `000403.SZ` |
| Provider / Model | `openai / gpt-5.6-terra` |
| Ledger | `FAILED_AFTER_DISPATCH / CONSUMED` |
| Provider attempts / Retry | `1 / 0` |
| Route | `REJECTED` |
| `can_publish` | `false` |

## 3. 已证事实

- Proxy readiness 在 `NETWORK_DISPATCH_STARTED` 之前通过。
- Keychain 条目存在；本轮只读取了非敏感元数据，诊断未读取 Secret 内容。
- Proxy receipt 记录 `auth_present=true`，鉴权文件被按合同读取。
- `api.openai.com:443/v1/responses`、`POST`、TLS 校验、禁止 redirect 均通过。
- `provider_connect_started/completed`、`tls_completed`、
  `request_write_started/completed` 均已持久化。
- `response_headers_received=true`，上游 HTTP status 为 `401`。
- `response_body_completed=false`，不存在可用的 OpenAI error code/body 证据。
- Relay 仅请求 Proxy 一次，以 `RELAY_PROTOCOL_REJECTED / exit 2` 终止。
- Candidate 未生成，Claims/Renderer 未运行。
- receipt archive 完整，Secret hit 为 0，容器、网络、临时文件残留为 0。
- Current scope preflight 现为 `CURRENT_SCOPE_ATTEMPT_CONSUMED / BLOCKED`。

## 4. 冻结证据 SHA-256

| 证据 | SHA-256 |
|---|---|
| Scoped ledger | `bfe53986f409aed4bac368a370fc59b9141caa6eb06d28f141744dfb06559dae` |
| Runtime evidence | `46f36aad32f3c8f0ef708d6d395b166eeef3c60572ad9cac50deeed5e3fc759d` |
| Proxy receipt | `0d6bbe87812c1f4766dc25097228499e5a11525fd9db2b4ca9a576c11a031604` |
| Relay receipt | `15ec73d5b5c45c659790bc0942b0e5b11c5a6fc5dfe289b2d77d630ae6812294` |
| Archive status | `391b85501266b47ae2cb15ba3eeac174880b28b59cd7b80bf5d1e28251e7b598` |
| Child metadata | `489de0ad618c2912fc18d56ef57de4f44f3a0d0a5ff3272940cd76c9aeaaffbe` |
| Rejection | `fd01ac78185e01e6ec1e198683bf5525a7a83148363402b1e433a196e116656b` |

## 5. 官方语义与可判定范围

OpenAI API 使用 Bearer API Key 认证。官方错误指南对 incorrect key
建议核对 API Keys 页面中的 Key，并确认应用没有混用两个 Key。
官方 IP allowlisting 文档也明确说明：未授权出口 IP 会返回
`HTTP 401` 和 `ip_not_authorized`。

官方参考：

- [API authentication](https://platform.openai.com/docs/api-reference/authentication)
- [Incorrect API key troubleshooting](https://help.openai.com/en/articles/6882433-incorrect-api-key-provided)
- [IP allowlisting](https://help.openai.com/en/articles/20001201-ip-allowlisting-for-the-openai-api)
- [GPT-5.6 Terra model](https://developers.openai.com/api/docs/models/gpt-5.6-terra)

当前 Proxy 不读取非 2xx body，因此只能证明“OpenAI 认证/组织边界
拒绝了请求”，不能证明具体 error code。

## 6. 用户侧安全处置

1. 在 OpenAI Platform API Keys 页面确认 Canary Key 是否存在且未撤销。
2. 如果无法证明当前 Key 仍有效，创建新的专用项目 Key，不在聊天中发送。
3. 检查所属组织是否启用 IP allowlist；如已启用，确认当前 Mac
   公网出口 IP 在允许范围。
4. 确认新 Key 属于希望的 API 组织/项目，项目已启用 billing，且账户
   至少是 Tier 1；官方模型页显示 `gpt-5.6-terra` 不支持 Free tier。
5. 仅在本机用 `security add-generic-password ... -U -w` 更新
   `tickflow-phase2-canary-openai`；不要在 argv、聊天、日志或仓库中提供 Key。
6. 换钥不解除当前 scope 的 consumed 状态。后续必须修改可批准工件并
   生成新 Candidate 和新 `approval_scope_id`，再经 Mock、hash 重审和唯一调用授权。

## 7. 新 Scope 前的最小工程要求

当前安全设计可以防止错误正文泄漏，但也使 401 无法分类。下一个
Candidate 应在 Proxy 中增加严格、非敏感的错误分类：

- 对非 2xx JSON body 仅限定长读取，严格验证 content-type 和 schema；
- 仅提取白名单 error `type/code`，如 `invalid_api_key` 或
  `ip_not_authorized`；
- 不保存 `message`、Key 片段、响应正文或响应哈希；
- receipt 只记录枚举化、无敏感信息的 `provider_error_code`；
- 新增 TDD、Mock、敏感信息扫描和独立复审；
- 代码和工件哈希变更后必须重新生成 Candidate，不得复用当前 Approval。

## 8. 停止点

```text
current_scope=CONSUMED
provider_attempt_count=1
retry_count=0
second_request=FORBIDDEN
next_action=ROTATE_OR_VERIFY_KEY_AND_DESIGN_NEW_SCOPE
```

本报告不授权新请求，不授权读取 Key 内容，不授权修改或删除已消费
ledger。

## 9. 本轮离线复核限制

完整 namespace preflight 在重验不可变运行工件时返回
`artifact_base_image_unavailable`。本轮未拉取、重建或替换镜像，也未触发
任何网络请求。历史 Request 1、Request 2 的 ledger SHA-256 仍分别精确匹配
冻结值；当前 scope 的 ledger、evidence、rejection 和 receipt archive 均已
落盘。再次执行完整工件 preflight 需要先恢复获批的本地基础镜像，但这不
改变当前 scope 已消费且禁止重试的结论。
