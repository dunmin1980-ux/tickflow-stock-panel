# TickFlow Phase 2B-2 AI Worker 运行时隔离验收报告

## 1. 结论

最终状态：

```text
PHASE2B_ISOLATION_RUNTIME_VERIFIED
```

本轮使用真实 Docker Desktop Linux VM 运行了一次正式隔离验证：
一个 Fake Provider Candidate 容器和一个越权 Probe 容器。两者的
Docker inspect 合同、OS 负向探针和宿主 Claims 验证均通过，容器已全部清理。

该结论仅证明当前 Fake Provider Worker 的本地容器隔离边界，
不代表真实模型、外部 Provider、云端部署或三票真实 AI 批次已获批。

## 2. 镜像与运行时

| 项目 | 验收值 |
|---|---|
| Docker Server | `29.3.1` |
| 运行平台 | Linux `amd64` (Docker Desktop VM) |
| Worker 镜像 | `tickflow-phase2-fake-worker:runtime-v1` |
| Worker 镜像 ID | `sha256:eff194d53d8958b3b1007a61efe7938b06642a5aa253583c11c764f759efd936` |
| 基础镜像 digest | `sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5` |
| 运行 UID/GID | `65532:65532` |
| Entry point | `/usr/bin/python3 /worker/worker.py` |
| 标的 | `000403.SZ` 派林生物 |
| Provider | 标准库 Fake Provider |

Dockerfile 的 `FROM` 使用完整 digest；不包含 `RUN`、`ADD`、
Shell、包管理器或凭据。基础镜像拉取是本轮唯一的基础设施下载，
不是 TickFlow、AI Provider 或业务数据请求。镜像从本地缓存以
`--network none --pull=false` 构建。

## 3. 容器强制边界

Candidate 与 Probe 的实际 inspect 均确认：

| 边界 | 结果 |
|---|---|
| 非 root `65532:65532` | PASSED |
| `network=none` | PASSED |
| 根文件系统只读 | PASSED |
| `cap-drop=ALL` | PASSED |
| `no-new-privileges` | PASSED |
| PID 上限 `16` | PASSED |
| 内存上限 `128 MiB` | PASSED |
| CPU 上限 `0.5` | PASSED |
| IPC 禁用 | PASSED |
| 自动重启禁用 | PASSED |
| 挂载数 | `2` |
| Projection 单文件只读挂载 | PASSED |
| Candidate 单文件可写挂载 | PASSED |
| Home/仓库/Vault/SSH/配置/容器控制端挂载 | `0` |

投影与输出是受限临时目录中的单文件。Projection 在两个容器
运行前后的 SHA-256 一致，完整投影摘要为
`5db35e91eabce452e5be3a3020b5c10711797941431df9bd5bb52f67625b42e1`。

## 4. OS 负向探针

| 探针 | 结果 |
|---|---|
| 外部 TCP 连接 | BLOCKED |
| Shell 可执行路径 | `3/3` BLOCKED |
| 禁止文件读取 | `6/6` BLOCKED |
| Projection 覆写 | BLOCKED |
| 根文件系统与 Worker 源码写入 | BLOCKED |
| 临时目录与系统配置目录写入 | BLOCKED |
| 额外输出文件写入 | BLOCKED |
| 禁止写入合计 | `6/6` BLOCKED |

探针只落盘布尔值和计数，没有保存容器 ID、命令、挂载源路径、
原始 inspect、stdout 或 stderr。

## 5. 宿主验证链

Fake Worker 候选仅在宿主上通过以下链路后被记为有效：

```text
Worker Candidate Protocol
-> Facts SHA / Projection SHA
-> Typed Claims schema
-> Facts Pointer binding
-> raw/qfq basis checks
-> deterministic calculations
-> deterministic Renderer
```

| 指标 | 结果 |
|---|---|
| Worker Candidate | `WORKER_CANDIDATE_VALID` |
| Claims | `CLAIMS_VALID` |
| Renderer | `RENDERED_VALID` |
| Claim 数 | `42` |
| Facts Pointer 绑定 | `47` |
| 无来源 Claim | `0` |
| 自由文本字段 | `0` |
| 交易 Claim | `0` |
| raw/qfq 混用 | `0` |
| 敏感形态命中 | `0` |
| Candidate SHA-256 | `da07152ea51247251df6cca7b67d3f1fe1e58f62b4a486b646244732f9a4b045` |
| Rendered SHA-256 | `63482087ca33cb7a0ea4767c9b4f41e9176b7abd0f5ac5516bb14b11fc9514e8` |

Typed 三票旧产物离线复核仍为 `CLAIMS_VALID`：126 Claims、
141 次 Facts Pointer 绑定、0 无来源、0 交易、0 口径混用。
历史 freeform 产物继续失败关闭为 `PHASE2_AI_OUTPUT_BLOCKED`，
未被新 Worker 链路绕过。

## 6. 请求、密钥与外部行为

```text
TickFlow API requests: 0
AI calls: 0
Provider attempts: 0
External sends: 0
Cloud mutations: 0
Obsidian real Vault writes: 0
Paper Trading: NOT_STARTED
Integrated Gold: DISABLED
AI configuration: NOT_CONFIGURED
Real key exposure: 0
```

网络探针的连接尝试在 `network=none` 边界内失败，没有成功对外连接。

## 7. 不变性

| 受保护集合 | 文件数 | SHA-256 | 结果 |
|---|---:|---|---|
| Phase 1 观察证据 | 36 | `8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5` | UNCHANGED |
| Phase 1 请求审计 | 7 | `dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d` | UNCHANGED |
| Phase 2 Facts | 4 | `10c6664754b8e1391f55d9b1b8b98f1577b749f0dceafbb77288749ae5376ef7` | UNCHANGED |
| Provider audit | 1 | `7996c13681e248b2a9932cdd7428949f1317719589234a7087b8ab019e4274f9` | UNCHANGED |

三份历史 AI 正文与 rejected 副本的文件摘要仍为：

```text
000403: b3307f02406c0314186e41da69ff16f68f86aba720207a4736d53e791f3ca2be
300059: 8211f480f1c20cd0201a23a4c1bb2e79461ba98f85708abb47ef611726c77717
600489: 8de882f2a32ba48b8c049759bf5e7e9836e44c8b05b62d697b6fe378b6a88baa
```

历史 `ai_body_sha256` 仍为：

```text
000403: 028d7ab8d409dc2c9b58d993d63b3e62c5a82c47a1a80941f10075abe22f4675
600489: 5f2901b36c721dc99db06da28d26c2efe3ff14e2b11068eab375860b3da97890
300059: acd1c28a0774cfadd42cfd3196eacc5385c9f19ee43618193c95d21050abd98b
```

## 8. 测试与静态检查

| 验证 | 结果 |
|---|---|
| 隔离/Worker/Claims/Renderer 专项 | `113 passed` |
| 后端全量 | `1619 passed, 13 warnings` |
| `compileall app scripts` | PASSED |
| Ruff F821 | PASSED |
| 改动文件 Ruff | PASSED |
| Typed Claims 离线验证 | `CLAIMS_VALID` |
| 历史 freeform 离线验证 | `PHASE2_AI_OUTPUT_BLOCKED` (预期退出码 2) |
| 证据敏感形态扫描 | PASSED |
| 容器与临时产物残留 | `0` |

13 条 warning 为已有 Polars、websockets、`datetime.utcnow()` 弃用提示
与 Polars sortedness 警告，本轮未扩大范围处理。

## 9. 交付边界与下一步

本轮只推送 `codex/tickflow-phase2-ai-review` 分支，不创建 PR、
不合并、不部署云端。完成后应立即停止，不执行真实三票
AI Claims 批次。

如后续申请真实 Provider，必须单独审批并重新验证：凭据注入边界、
出站网络白名单、Provider 请求脱敏、超时/限频和人工复核门禁。
本报告不授权这些能力。

机器可读证据：

```text
reports/phase2_isolation_runtime/runtime_evidence.json
SHA-256: 9cf174d4def6908e84522366105b41ed33a00f3be8aab9dc01454c4f75f5533c
```
