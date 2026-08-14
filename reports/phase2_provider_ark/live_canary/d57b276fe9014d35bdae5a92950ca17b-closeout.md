# TickFlow Phase 2B Ark 单票真实 Canary 关闭报告

## 最终状态

`PHASE2B_ARK_CANARY_PROVIDER_BLOCKED`

唯一一次已授权的 Ark Provider attempt 已执行。Scope 在 durable ledger 写入 `NETWORK_DISPATCH_STARTED` 后永久消费，不得再次请求。

## 批准身份

- Authorized execution Head：`0ff1cf806b1ade56baf6f288413dc330886060f2`
- Approval Candidate：`26c09cc183adcd7562e53f72368a27eceff8b56603cb0ce250fe1244466b41ac`
- Approval Scope：`6f696a3213c32f962c2b7f8571509b499d9d7e978e773edd09aac1543f7f265a`
- Provider：`volcengine_ark`
- Exact model：`doubao-seed-2-1-turbo-260628`
- Symbol：`000403.SZ` 派林生物
- Request ID：`d57b276fe9014d35bdae5a92950ca17b`

## 执行结果

- Scope attempt：`FAILED_AFTER_DISPATCH / CONSUMED`
- This-run Provider attempts：`1`
- AI calls：`0`
- Retry：`0`
- Provider HTTP：`NOT_PROVEN`
- Route：`REJECTED`
- Machine validation：`FAILED`
- Manual review：`PENDING`
- can_publish：`false`

## Provider 阶段证据

| 事件 | 结果 |
|---|---|
| provider_connect_started | YES |
| provider_connect_completed | NO |
| tls_completed | NO |
| request_write_started | NO |
| request_write_completed | NO |
| response_headers_received | NO |
| response_body_completed | NO |

Ark Proxy receipt 的终态为 `TLS_FAILED`，最后可证明阶段为 `provider_connect_started`。Relay 已向 Proxy 提交一次请求，但未收到有效 Provider 响应；Proxy 返回 502 后，Relay 以 `RELAY_PROTOCOL_REJECTED` 退出。

由于请求正文没有开始写入 Provider，本轮 AI call 计为 `0`。但 Provider attempt 已在调度后消费，不能重试。

## 下游校验

- Candidate：`NOT_PRODUCED`
- Claims：`NOT_RUN`
- Claim count：`0`
- Facts Pointer bindings：`0`
- Unsupported Claims：`0`
- Free-text fields：`0`
- Trading Claims：`0`
- Raw/QFQ：`NOT_RUN`
- Renderer：`NOT_RUN`

失败发生在 Provider TLS/连接阶段，未进入 Ark Responses Envelope、Structured Output、Candidate、Claims 或 Renderer。

## 证据与清理

- Attempt ledger：`FAILED_AFTER_DISPATCH`
- Receipt archive：`ARCHIVED`
- Proxy receipt：`VALID`
- Relay receipt：`VALID`
- Child metadata：`VALID`
- Container residue：`0`
- Network residue：`0`
- Secret temporary residue：`0`
- Request/response temporary residue：`0`
- Global lock：`RELEASED`
- Secret hits：`0`
- Historical evidence：`UNCHANGED`

已通过的 TLS Probe `d73e91f766854ff7a64c0e725939eb64` 继续保持 `PASSED / PRESERVED / NON_REUSABLE`。本轮没有再次执行 TLS Probe。

## 冻结边界

- 当前 Approval Scope：`NON_REUSABLE`
- 第二次 Ark 请求：`FORBIDDEN`
- OpenAI 请求：`NOT_RUN`
- 中金黄金 / 东方财富：`NOT_RUN`
- Three-symbol batch：`NOT_APPROVED`
- Paper Trading：`NOT_STARTED`
- Obsidian real Vault：`NO`
- Cloud deploy：`NO`
- Integrated Gold：`DISABLED`

## 下一动作

`DIAGNOSE_BLOCKER`

后续只能针对本次已归档证据做只读诊断。若要再次调用 Ark，必须设计全新的 Candidate 和 Approval Scope，并重新获得明确授权；不得复用本 Scope 或 Request ID。
