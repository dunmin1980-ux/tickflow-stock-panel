# TickFlow Phase 2B-3A Provider Relay 验收报告

## 1. 最终状态

```text
PHASE2B_PROVIDER_RELAY_READY
```

验收日期：2026-08-01（Asia/Shanghai）

本阶段只验证 `000403.SZ` 派林生物的本地 Mock Provider 链路。真实 AI、
真实 Provider、TickFlow、正式 Obsidian、Paper Trading、云端、Telegram、
OpenClaw 和 Integrated Gold 均未启用。

机器可读证据：

```text
reports/phase2_provider_relay/runtime_evidence.json
```

## 2. 已验证架构

```text
Host Orchestrator
  -> 最小 Facts Projection
  -> Relay（relay_proxy_net）
  -> Egress Proxy（双 internal 网络）
  -> Mock Provider（proxy_provider_net）
  -> Relay 原始 Candidate
  -> Host Claims Validator
  -> Deterministic Renderer
  -> reports/phase2_provider_relay/mock_preview/
```

Host 保留 Projection/Facts 绑定、Claims 校验、确定性渲染、路由和审计职责。
Relay 不读取完整 Facts，不持有 Secret，不渲染 Markdown，也不修改或修复
Candidate。Proxy 只接受固定方法和路径，并只转发至固定 Mock 目标。

## 3. Docker 与镜像证据

Docker Server 为 `29.3.1`，运行平台为 `linux/amd64`。三个镜像均基于：

```text
sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5
```

| 组件 | Image ID | Dockerfile SHA-256 | Runtime user |
|---|---|---|---|
| Relay | `sha256:efd1c21cdb3502cf3240ab389470556946450f1a7572f66a194ed1134d50dc0d` | `fad706cbe077f22d1476b8299012b09a3ee278c9c4572ec7fb57944398c72d68` | `65532:65532` |
| Proxy | `sha256:d431ec75c7609a6add036f7f7c4a42ba3f351aebab5bfcb6442ee087b2059f6a` | `f40209a8bff8136a441415b100ae4b805013851536bb4b2c29d7cbf312b7eef4` | `65532:65532` |
| Mock | `sha256:8d843277fad0b1207a83ec0f5dd7fabc7507c31cdb09e1bd6a5aee26f26507fb` | `23c7a0ca7324210333bc0a8c8d687e545a4c58d07599f2d29d18b7eacf1a105d` | `65532:65532` |

每次运行均验证：只读 rootfs、`cap-drop=ALL`、
`no-new-privileges`、PID/CPU/内存限制、`ipc=none`、restart disabled、
无发布端口、无设备和无 Docker control mount。

Relay 恰有四个临时单文件挂载：request/projection 只读，response/receipt
可写。Proxy 恰有两个挂载，Mock 恰有三个挂载。没有 Home、仓库、Vault、
SSH、用户配置或数据库目录挂载。

## 4. 网络与 Secret 边界

每次场景创建两个 Docker `internal` 网络。Relay 只加入 Relay/Proxy 网络，
Mock 只加入 Proxy/Provider 网络，Proxy 是唯一同时加入两个网络的组件；没有
使用普通 bridge、host network 或宿主端口。

真实网络探针结果：

| 探针 | 结果 |
|---|---|
| 固定 Proxy 方法与路径 | `ALLOWED` |
| Relay 直连 Mock | `NOT_REACHABLE` |
| 任意主机名 | `NOT_REACHABLE` |
| 公网测试 IP | `NOT_REACHABLE` |
| 宿主入口 | `NOT_REACHABLE` |
| Docker control file | `BLOCKED` |
| Docker control TCP | `NOT_REACHABLE` |
| Proxy 非允许路径 | `BLOCKED` |
| Proxy 非允许方法 | `BLOCKED` |

重定向响应和返回外部 URL 的响应均单独进入无效场景并失败关闭；未跟随
外部 URL。真实公网成功连接数为 `0`。

测试 Secret 仅以临时只读单文件提供给 Proxy 与 Mock。Relay 无 Secret
挂载；Secret 不通过参数、环境变量或 label 注入，不记录摘要。容器 inspect
可见值、容器日志和发布产物中的测试 Secret 命中数为 `0`，运行后 Secret
文件残留为 `0`。

## 5. Mock E2E 与失败关闭

总场景运行数为 `24`：两次合法 Candidate、21 个无效模式和一次网络探针。
本地 Mock Provider 尝试数为 `24`，每次均为一次尝试，`retry_count=0`。

| 项目 | 结果 |
|---|---:|
| 合法 Candidate 通过 | 2/2 |
| 无效模式拒绝 | 21/21 |
| 无效模式误接收 | 0 |
| inbox 文件 | 1 |
| rejected 文件 | 21 |
| 审计 receipt | 24 |

失败关闭覆盖 Markdown、自由文本字段、未知 Claim/Predicate、symbol/request
ID/Projection SHA/Facts SHA 错误、raw/qfq 混用、非法 Facts Pointer、交易
Claim、超大/空/非 JSON/多文档响应、timeout、429、500、敏感形态、重定向
和外部 URL。系统没有补字段、删 Claim、改口径、重试或把失败结果转成正文。

Host Claims Validator 对合法 Candidate 返回 `CLAIMS_VALID`。两次合法运行的
normalized Candidate SHA-256 均为：

```text
da07152ea51247251df6cca7b67d3f1fe1e58f62b4a486b646244732f9a4b045
```

两次确定性渲染 SHA-256 均为：

```text
63482087ca33cb7a0ea4767c9b4f41e9176b7abd0f5ac5516bb14b11fc9514e8
```

## 6. 审计与清理

24 份 receipt 均包含要求的 request/symbol/Projection/Facts 绑定、三个镜像
digest、Relay 合同 hash、Proxy 策略 hash、时间、HTTP 状态、尝试/重试、
响应、Candidate、Claims/Renderer、路由和清理字段。报告不包含完整请求体、
Authorization、Secret、完整环境、宿主绝对路径、raw inspect、raw logs、
Docker command 或容器 ID。

| 清理项 | 残留 |
|---|---:|
| Relay/Proxy/Mock 容器 | 0 |
| 测试网络 | 0 |
| Secret 文件 | 0 |
| 临时 request/response/receipt | 0 |
| staging/tmp/partial/bak | 0 |

发布采用 staging、fsync 和原子 rename。清理失败或发布失败不能生成成功状态。

## 7. 不变性

| 受保护集合 | 文件数 | SHA-256 | 结果 |
|---|---:|---|---|
| Phase 1 观察证据 | 36 | `8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5` | UNCHANGED |
| Phase 1 请求审计 | 7 | `dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d` | UNCHANGED |
| Phase 2 Facts | 4 | `10c6664754b8e1391f55d9b1b8b98f1577b749f0dceafbb77288749ae5376ef7` | UNCHANGED |
| Typed Claims | 8 | `39ef9b714798f193c3ba283c1035fad42372cedf4f7a17a36fc008411ed41bbf` | UNCHANGED |
| Typed inbox | 3 | `a9bfddecc5a9046badf5f7cf4485fab029508681dae8a5a5206a93d4ab2e2291` | UNCHANGED |
| 历史 AI Markdown | 3 | `9a0bc445f7580c55db630b8e3fbfac545fe71d54b36588919dd7ec79ef57feb5` | UNCHANGED |
| 历史 rejected | 3 | `6e2e0c7b311a76ccff061917f3893f97aca4ba84782c769ee004960d335f164c` | UNCHANGED |
| Phase 2B-2 runtime evidence | 1 | `9cf174d4def6908e84522366105b41ed33a00f3be8aab9dc01454c4f75f5533c` | UNCHANGED |

## 8. 测试与离线验证

| 验证 | 结果 |
|---|---|
| Provider Relay focused suite | `198 passed` |
| 后端全量 | `1817 passed, 13 warnings` |
| `compileall app scripts` | PASSED |
| Ruff F821 | PASSED |
| Phase 2B-3A 文件 Ruff | PASSED |
| Typed Claims | `CLAIMS_VALID` |
| 历史 freeform | `PHASE2_AI_OUTPUT_BLOCKED`（预期退出码 2） |
| Provider Relay 离线校验 | `PHASE2B_PROVIDER_RELAY_READY` |
| 敏感形态扫描 | 0 hits |

13 条 warning 为现有 Polars、websockets、`datetime.utcnow()` 弃用提示和
Polars sortedness 提示，本阶段没有扩大范围处理。

## 9. 十四项结论

1. Relay 在真实 Docker 隔离容器中运行：是。
2. Relay 为非 root：是，固定 `65532:65532`。
3. Relay 只有四个批准的临时单文件挂载：是，无广域或敏感宿主挂载。
4. Secret 未进入 inspect 可见值、日志或产物：是，命中数为 0。
5. Relay 只能访问 Proxy：在本次双 internal 网络和探针范围内，是。
6. Proxy 只允许固定 Mock 目标：是，目标、方法和路径均固定并实测拒绝旁路。
7. Relay 直接出站被阻断：是，直连 Mock、任意域名、公网 IP 和宿主均失败。
8. 重定向被阻断：是，未跟随重定向或外部 URL。
9. 合法 Mock Candidate 通过：是，两次均通过。
10. 所有无效模式失败关闭：是，21/21 拒绝，0 误接收。
11. Host Claims Validator 通过：是，合法 Candidate 为 `CLAIMS_VALID`。
12. Candidate 与 Renderer 确定性：是，两次 hash 分别一致。
13. 容器、网络、Secret 或临时文件残留：无，全部为 0。
14. 是否足以申请真实单票 Provider Canary：可以申请单独审批，但本报告不批准、
    不配置也不执行该 Canary。

## 10. 外部行为与限制

```text
TickFlow API 请求：0
真实 AI 调用：0
真实 Provider 尝试：0
真实公网成功连接：0
云端修改：0
正式 Obsidian 写入：0
外部发送：0
AI Key：NOT_CONFIGURED
Paper Trading：NOT_STARTED
Integrated Gold：DISABLED / external_send_count=0
Telegram / OpenClaw：NOT_CONNECTED
真实 Key：NOT_EXPOSED
```

当前证明仅覆盖单票、本地 Mock、固定协议和 Docker Desktop 边界。它不证明
真实 Provider 的 TLS、鉴权、限频、响应稳定性或供应商 SLA。任何真实 Canary
必须按独立 runbook 再审批，并保持单票、一次尝试、人工复核和失败关闭。
