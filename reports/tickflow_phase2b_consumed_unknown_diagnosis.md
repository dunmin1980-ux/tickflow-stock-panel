# TickFlow Phase 2B-3B3 ATTEMPT_CONSUMED_UNKNOWN 只读取证诊断

## 1. 最终状态

```text
PHASE2B_CANARY_OBSERVABILITY_GAP_IDENTIFIED
```

| 项目 | 结果 |
|---|---|
| Request ID | `d59766101b63450e8148541d589a90bf` |
| Attempt | `CONSUMED` |
| 新增 Provider attempts | `0` |
| 新增 AI calls | `0` |
| 分类 | `DISPATCH_LEDGER_ONLY` |
| 最后确定的向前阶段 | `RELAY_RUNNING` |
| Provider 根因 | `ROOT_CAUSE_NOT_PROVABLE_WITH_CURRENT_EVIDENCE` |
| OpenAI Project Usage | `PENDING` |

本轮只读取 ledger、已落盘 Canary evidence、Docker Desktop 本地日志和
Git 工件。没有再次运行 launcher，没有 DNS/TLS/HTTP 测试，也没有调用
OpenAI 或 TickFlow。

## 2. 结论

最后一个有运行证据证明完成的阶段是 `RELAY_RUNNING`。Proxy 的 Docker
start 已成功返回，`NETWORK_DISPATCH_STARTED` 已原子持久化，Relay 的
Docker start 也成功返回。Relay 随后在 75 秒合同上限前快速退出；从
dispatch ledger 到终态只经过 `4.556997` 秒。

没有任何持久证据能证明 Proxy 收到了 Relay 请求，也不能证明 DNS、TCP、
TLS、HTTP request、HTTP response 或 response body 发生。因此不能把本次
失败归因于 OpenAI、网络、TLS、模型、Prompt 或 HTTP 状态。

## 3. 严格时间线

下表均为 UTC wall clock。本次运行没有持久化 monotonic 时间；报告不补造
monotonic 值。

| 时间 | 组件 | 事件 | 证据 |
|---|---|---|---|
| `<= 08:23:29.987635` | Host | `PRECHECK_PASSED`、`LOCK_ACQUIRED` | ledger 已进入 PREPARED，且源码中这些门禁位于 prepare 之前；无独立精确时间戳 |
| `08:23:29.987635` | Ledger | `LEDGER_PREPARED` | durable ledger |
| `08:23:31.554655` | Proxy | Docker start 请求开始 | Docker Desktop host log |
| `08:23:32.186070` | Proxy | Docker start 成功返回，Proxy process 存活 | Docker Desktop host log |
| `08:23:32.214310` | Ledger | `NETWORK_DISPATCH_STARTED` | durable ledger |
| `08:23:32.344128` | Relay | attach + Docker start 请求开始 | Docker Desktop host log |
| `08:23:32.911644` | Relay | Docker start 成功返回，Relay process 存活 | Docker Desktop host log |
| `08:23:36.620803` | Relay | task-delete；Relay 先退出 | Docker Desktop VM log |
| `08:23:36.771307` | Ledger | `ATTEMPT_CONSUMED_UNKNOWN` | durable ledger |
| `08:23:36.841918` | Host | Relay cleanup 开始 | Docker Desktop host log |
| `08:23:36.964298` | Host | Proxy cleanup 开始 | Docker Desktop host log |

关键顺序没有冲突：Proxy 先启动，dispatch ledger 后启动 Relay，Relay 先退出，
Proxy 随后由 Host cleanup 强制删除。

## 4. 阶段矩阵

| 阶段 | 状态 | 说明 |
|---|---|---|
| PRECHECK | `PROVEN` | 后续 durable state 只能在门禁通过后产生；无独立时间戳 |
| LOCK_ACQUIRED | `PROVEN` | ledger 写入位于独占锁内；无独立时间戳 |
| LEDGER_PREPARED | `PROVEN` | `prepared_at` 已持久化 |
| NETWORK_DISPATCH_STARTED | `PROVEN` | `dispatch_started_at` 已持久化 |
| PROXY_RUNNING | `PROVEN` | Docker start 成功，直到 cleanup 才删除 task |
| RELAY_RUNNING | `PROVEN` | Docker start 成功，随后存在 task-delete |
| DNS | `UNKNOWN` | 无阶段 receipt |
| TCP_CONNECT | `UNKNOWN` | 无阶段 receipt |
| TLS_HANDSHAKE | `UNKNOWN` | 无阶段 receipt |
| HTTP_REQUEST_SENT | `UNKNOWN` | 无阶段 receipt |
| HTTP_RESPONSE_HEADERS | `UNKNOWN` | `provider_http_status=null` |
| HTTP_RESPONSE_BODY | `UNKNOWN` | `response_received_at=null` |
| CANDIDATE_CREATED | `UNKNOWN` | Candidate 未回收；临时目录已按合同清理 |
| HOST_VALIDATION | `NOT_COMPLETED` | `host_validation_completed_at=null` |

## 5. 三组件诊断

### Host Orchestrator

- `NETWORK_DISPATCH_STARTED` 有 durable ledger 证据。
- Proxy start 成功完成后才持久化 dispatch；之后启动 Relay，顺序正确。
- Host 没有命中 90 秒 timeout。dispatch 到 terminal ledger 仅 `4.556997`
  秒。
- Host 没有持久化 child 的精确 exit code、stdout 或 stderr。
- error path 在归档 Relay receipt、Proxy receipt 和 bounded child metadata
  之前执行 cleanup 并删除 workdir。

### Relay

- Relay 容器确实启动并运行。
- Relay 未等待到 75 秒；Docker task 在启动成功后约 `3.709159` 秒删除。
- Relay 是第一个退出的组件，结果为非成功；精确 exit code 未持久化。
- 是否连接 Proxy、是否写出请求、是否开始等待 response 均为 `UNKNOWN`。
- `relay-receipt.json`、stderr、Candidate temp 和 ready marker 均未归档，
  所以无法证明它们是否曾存在。

### Proxy

- Proxy 容器确实启动，且在 Relay 退出后仍存活，随后由 Host cleanup 删除。
- 没有独立 readiness receipt，因此监听状态不作强结论。
- Proxy 是否收到 Relay 请求为 `UNKNOWN`。
- Provider transaction、DNS、TCP、TLS、HTTP request、response headers、
  response body 均无证据。
- `proxy-receipt.json` 未在 cleanup 前归档。

## 6. 超时判定

| 项目 | 判定 | 依据 |
|---|---|---|
| Host timeout | `NO` | 未达到 90 秒 |
| Relay timeout | `NO` | 未达到 75 秒；Relay 提前退出 |
| Proxy timeout | `NO` | 整个 dispatch 未达到 60 秒 Provider total timeout |

源码存在一个明确的诊断缺陷：Docker command wrapper 会把 timeout、进程调用
异常和任何非零 Relay exit 都压成传入的 `RELAY_TIMEOUT`；dispatch 再把该
类别升级为 `ATTEMPT_CONSUMED_UNKNOWN`。因此当前 `RELAY_TIMEOUT` 标签不是
真实超时证据。

这解释了“为什么最终只剩 unknown”，但不能证明“Relay 为什么失败”或
“Provider 请求到了哪一步”。Provider 失败根因仍不可证明。

## 7. 17 项回答

1. 最后一个确定完成阶段：`RELAY_RUNNING`。
2. `NETWORK_DISPATCH_STARTED`：有持久证据，`YES`。
3. Proxy 是否收到请求：`UNKNOWN`。
4. Relay 是否正常等待：启动成功，但提前退出；未等待到 75 秒。
5. DNS 是否发生：`NOT_PROVEN`。
6. TCP 是否成功：`NOT_PROVEN`。
7. TLS 是否成功：`NOT_PROVEN`。
8. HTTP request 是否发送：`NOT_PROVEN`。
9. HTTP response：`NOT_PROVEN`。
10. Response body：`NOT_PROVEN`。
11. Candidate 是否曾产生：`UNKNOWN`；没有回收或持久证据。
12. 先退出组件：Relay；Proxy 后由 cleanup 删除。
13. Host timeout：`NO`。
14. Relay timeout：`NO`；现有标签是错误折叠，不是 75 秒超时。
15. Proxy timeout：`NO`；未达到 60 秒。
16. 当前根因：Provider/网络根因不可证明；本地诊断与证据保留缺陷已确定。
17. 缺失审计点：见下一节七个最小事件。

## 8. 最小可观测性缺口

后续候选只需增加以下七个脱敏事件：

```text
provider_connect_started
provider_connect_completed
tls_completed
request_write_started
request_write_completed
response_headers_received
response_body_completed
```

每个事件只允许记录 boolean、timestamp、HTTP status 和 byte counts；不得
记录 Authorization、Secret、完整 request body 或 response body。现有
Relay/Proxy receipt 和 bounded child exit metadata 还必须在 cleanup 前原子
归档，否则这些事件仍可能随 workdir 一起丢失。

## 9. 证据完整性

| 项目 | 结果 |
|---|---|
| Ledger SHA-256 | `ded0f39813dd70657cedd6a8c92669d3508480e97efc6ed44b1a45f9470b5772` |
| Runtime evidence SHA-256 | `0ef11071083053ac7db825b39492e7e5d24ce27d32cc40c6daa90f43138603e4` |
| Rejected receipt SHA-256 | `ad7bada723a39a4fd0b266a3740e59f3e040d4ce81e79e6e594b6a2b3785e2f4` |
| Orchestrator source SHA-256 | `4e7607b2c185faab69a2aa5c930e4a61c970fa271882ce4c8a1ce1d67f7cfa5a` |
| Relay source SHA-256 | `cafd46a67ba08da1d92c2158771af2d529373a87558bbb392d1b5d9e9f81ee2b` |
| Proxy source SHA-256 | `3e75b5c4cb97e45d613c1b9c5bfde5d146eb720c88bacb399e20a90d534e5036` |
| Protected historical files | `66` |
| HEAD manifest | `3af93021cd94d0ef771a35d8dcc7fd03dd1e150718891a81b124929494c0ce46` |
| Worktree manifest | `3af93021cd94d0ef771a35d8dcc7fd03dd1e150718891a81b124929494c0ce46` |
| Historical evidence | `UNCHANGED` |

## 10. 人工 Usage 字段

```text
openai_project_usage_check=PENDING
```

人工可在 OpenAI Project `TickFlow Phase 2 Canary` 中查看
`2026-08-08T08:23:32Z` 前后约 5 分钟。即便未来填写
`NO_USAGE_OBSERVED`，也不能据此断言请求未发送。

## 11. 下一动作

```text
ADD_MINIMAL_STAGE_RECEIPTS
```

当前 attempt 永久保持 `CONSUMED`。本报告不授权第二次 Provider 请求、
DNS/TLS 探测、其他标的、三票批次、Paper Trading、Obsidian 正式写入、
云端部署或 Integrated Gold。
