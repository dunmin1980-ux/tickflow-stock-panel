# TickFlow Phase 2B-2 AI Worker 运行时隔离设计

## 1. 目标与结论门禁

本阶段只用 Fake Provider 验证 OS 级 Worker 隔离，不执行真实
AI 调用、外部 Provider 请求或三票 AI 批次。唯一成功状态为：

```text
PHASE2B_ISOLATION_RUNTIME_VERIFIED
```

任一必需的 Docker 运行时证据缺失、容器约束不符合或负向探测
未受 OS 拒绝，结果必须是：

```text
PHASE2B_ISOLATION_RUNTIME_BLOCKED
```

不得用 Python 路径守卫、mock 或 Fake Provider 功能测试代替容器
运行时证据。

## 2. 方案选择

采用专用 distroless Python Worker 镜像，不复用 TickFlow 主应用镜像。
这个选择的目的是缩小根文件系统和可执行程序范围，并通过无
Shell 镜像直接证明 Shell 探测失败。

镜像要求：

- 基础镜像必须使用不可变 digest，不接受 tag-only 构建；
- 最终镜像不包含 `/bin/sh`、`/bin/bash`、BusyBox 或包管理器；
- 只包含 Python 标准库、Fake Provider Worker 和两个挂载占位文件；
- 镜像配置用户和运行时用户均固定为 `65532:65532`；
- 镜像不包含仓库、Home、Vault、Secret 或原始 Facts 文件。

## 3. 容器安全合同

每次 Worker 容器必须同时使用：

```text
--network none
--read-only
--user 65532:65532
--cap-drop ALL
--security-opt no-new-privileges
--pids-limit 16
--memory 128m
--cpus 0.5
--ipc none
```

容器不挂载 Home、仓库、Obsidian、`.ssh`、`.config`、Docker socket、
设备或云端目录。宿主端必须解析 `docker inspect`，不能只信任
拼接的命令行。

## 4. 最小挂载合同

单次只验证 `000403.SZ` 派林生物，不执行三票批次。容器只有
两个 bind mount：

| 容器路径 | 类型 | 权限 | 用途 |
|---|---|---|---|
| `/input/projection.json` | 单文件 bind | read-only | Host 生成的单票最小 Projection |
| `/output/candidate.json` | 单文件 bind | read-write | Worker 唯一允许的输出 |

不挂载整个输出目录。这比“独立可写目录”更严格：容器内
`/output` 仍属于只读根文件系统，只有 `candidate.json` 这一个挂载点
可写。宿主临时目录不进入 Git，验证完成后立即删除。

Projection 必须由当前 Host 协议代码生成，并且：

- 原始 Facts 字节 SHA-256 与 `facts_sha256` 相符；
- Projection 只包含已批准的结构化字段；
- 不包含宿主路径、Secret、Cookie、Session 或 Authorization；
- 挂载前和容器退出后均复算 Projection SHA-256。

## 5. Fake Provider 和 Host 端数据流

```text
committed Facts bytes
-> Host build_worker_projection
-> read-only projection bind
-> isolated Fake Provider Worker
-> one candidate JSON file
-> Host validate_worker_candidate
-> Host ClaimsDocument adapter
-> Host validate_claims_document
-> Host deterministic renderer
-> hash-only runtime evidence
```

Fake Provider 只使用 Python 标准库，从 Projection 生成完整类型化
Claims Candidate JSON。它不包含 HTTP 客户端、SDK、密钥参数、自由文本
或 Markdown Renderer。

Worker 输出不直接进入 Obsidian inbox。Host 必须独立验证：

1. Projection SHA、symbol、trade date 和 Facts SHA 绑定；
2. 完整 predicate 集合和确定性 claim ID；
3. 每个 Facts Pointer、operand label、unit 和 value 回放；
4. Calculation Registry 和 raw/qfq 隔离；
5. Claims Schema、自由文本、交易字段和敏感形态；
6. Renderer 纯函数的确定性输出。

候选 JSON 和渲染 Markdown 只存在于临时目录和内存中。正式报告
只保存 SHA-256、计数和状态，不写入真实 Obsidian Vault。

## 6. OS 级负向探测

探测必须在与 Candidate Worker 相同的镜像和容器约束下实际执行。
下列项必须全部由 OS 拒绝：

- 连接外部 IP 的 TCP socket；
- 执行 `/bin/sh`、`/bin/bash` 或 BusyBox；
- 读取宿主 Home、仓库、`.ssh`、`.config`、Vault 或 Docker socket 候选路径；
- 覆写 `/input/projection.json`；
- 在 `/tmp`、`/worker`、`/etc` 和 `/output` 中创建额外文件；
- 修改容器内已有 Worker 源码。

探测只将脱敏的布尔值、errno 类别和计数写入唯一输出文件，
不保存宿主绝对路径、命令行或完整 `docker inspect`。

## 7. Host 端运行时证据

Host 编排器必须从 Docker API/CLI 的实际结果验证：

- `Config.User == 65532:65532`；
- `HostConfig.NetworkMode == none`；
- `HostConfig.ReadonlyRootfs == true`；
- `CapDrop` 包含 `ALL`；
- `SecurityOpt` 包含 `no-new-privileges`；
- PIDs、内存、CPU 和 IPC 约束与设计一致；
- mount 数量精确为 2，目标和 RW 属性精确匹配；
- 无 Docker socket、Home、仓库或 Vault mount；
- 容器正常退出，没有 restart policy；
- 镜像 digest 与报告中的固定 digest 一致。

证据文件不得保存 mount source、容器完整环境、容器 ID、
Docker socket 路径或本机用户名。

## 8. 错误处理和清理

- Docker 不可用、镜像 digest 不匹配或 inspect 缺失时失败关闭；
- 任一负向探测意外成功时失败关闭；
- Candidate 超过大小限制、包含额外文件或 Host 验证失败时失败关闭；
- 出现错误也必须删除容器和临时输入/输出；
- 容器删除失败时报告 `cleanup_failed` 并不得标记验收通过；
- 不自动重试 Worker，不切换镜像或放宽安全参数。

## 9. 测试与交付物

单元测试覆盖命令合同、inspect 解析、mount 白名单、Candidate 适配、
脱敏证据、失败清理和禁止参数。真实 Docker 集成验收不由 mock
测试代替，必须另行执行实际 Worker 和 probe 容器。

交付物：

```text
docker/phase2-ai-worker/
backend/app/services/phase2_isolation_runtime.py
backend/scripts/validate_phase2_isolation_runtime.py
backend/tests/test_phase2_isolation_runtime.py
reports/phase2_isolation_runtime/runtime_evidence.json
reports/tickflow_phase2b_isolation_runtime_eval.md
```

验收后还必须重跑 Phase 2 Claims 专项、后端全量、compileall、Ruff F821、
敏感形态扫描和 Phase 1/Phase 2 不变性哈希。只推送当前
`codex/tickflow-phase2-ai-review` 分支，不建 PR、不合并、不部署。

## 10. 冻结边界

```text
TickFlow API requests: 0
AI calls: 0
Provider attempts: 0
AI key: NOT_CONFIGURED
Paper Trading: NOT_STARTED
Obsidian real Vault write: NO
Cloud redeploy: NO
Telegram / OpenClaw: NOT_CONNECTED
Integrated Gold: DISABLED
external_send_count: 0
```

