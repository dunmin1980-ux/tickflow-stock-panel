# TickFlow P0 云端补丁升级验收报告

> 执行时间：2026-07-23（Asia/Shanghai）  
> 本地分支：`codex/tickflow-p0-validation`  
> 部署 commit：`aa9fc0a84b8e72091f8e26c86983c66819befd30`  
> P0 运行时代码最终提交：`0c2b73d521f0f8b54e8ec8ccf4de301f02963e10`  
> 运行入口：Tailscale HTTPS `:8443 -> 127.0.0.1:3019`  
> 总判定：`CLOUD_PWA_PATCH_DEPLOYED / WORKSPACE_CONTRACT_OK / ROLLBACK_READY / PR_CI_PENDING`

本报告只记录 A 股日线研究工作台云端补丁升级。没有接入券商、实盘、自动下单、Telegram、OpenClaw 主链路或正式金融日报，也没有启用集成 Gold 工作区。

## 1. 升级目标

- 把已通过本地验收的增量指标 P0 修复部署到云端 3019。
- 保持现有 258 MB 数据目录、workspace 合同和 Tailscale 私网入口不变。
- 只替换 `TickFlow_Stock_Panel`，不操作独立 Gold Shadow、n8n 或 PostgreSQL。
- 建立可验证的旧镜像和数据双回滚点。

## 2. 部署产物

| 项目 | 结果 |
|---|---|
| 候选源码 | fork 分支 `codex/tickflow-p0-validation` |
| 源码 HEAD | `aa9fc0a84b8e72091f8e26c86983c66819befd30` |
| 候选镜像 tag | `tickflow-stock-panel-stage-a-app:candidate-aa9fc0a` |
| 运行镜像 ID | `sha256:4f3f240cec7a911364ce2c0a84e89a42a1206e93bd74496792045775bfdd1003` |
| OCI revision | `aa9fc0a84b8e72091f8e26c86983c66819befd30` |
| 运行容器 | `TickFlow_Stock_Panel` |
| 绑定 | `127.0.0.1:3019 -> container:3018` |
| 数据挂载 | `/home/ubuntu/tickflow-stock-panel-stage-a/data -> /app/data` |

健康接口仍返回 `version=0.1.86`，因为本补丁线没有伪造上游新版本号。当前是否为新镜像必须以 OCI revision 和镜像 digest 判断，不能只看产品版本字符串。

## 3. 构建过程

1. 在 `/home/ubuntu/tickflow-candidates/aa9fc0a84b8e72091f8e26c86983c66819befd30` 从 fork 精确克隆分支。
2. 首次命令因云机使用 legacy Docker builder、不支持 `--progress=plain` 而在构建开始前退出；没有生成镜像，也没有影响运行容器。
3. 移除展示参数后按 frozen lock 全量构建成功。
4. 前端生产构建完成 2697 个 modules；PWA precache 为 69 entries、2794.03 KiB。
5. 镜像内 Codex CLI 固定为 `0.144.3`。

构建仍有既有前端体积告警：主入口约 566.69 kB，ECharts chunk 约 1,034.94 kB。该告警不阻断本次正确性补丁，但仍属于移动端首次加载 P1。

## 4. 候选旁路验收

候选镜像先以无密钥、隔离数据目录运行在 `127.0.0.1:3020`，没有加入 Tailscale Serve。

| 验证 | 结果 |
|---|---|
| 健康检查 | `status=ok, version=0.1.86, mode=none` |
| 认证状态 | `configured=false, authenticated=false` |
| 网络绑定 | 仅 `127.0.0.1:3020` |
| P0 镜像内合同 | `CANDIDATE_P0_CONTRACT_OK` |
| Workspace sidecar | `WORKSPACE_SIDECAR_CONTRACT_OK` |
| Gold | `enabled=false`、`external_send_count=0` |

P0 镜像内合同验证了：

- 非空复权因子下，历史收盘价保持 `[50.0, 60.0]`，不再二次复权为 `[25.0, 60.0]`。
- 原始收盘价保持 `[100.0, 60.0]`。
- 损坏 Parquet 明确降级为空历史。
- `ColumnNotFoundError` 等查询编程错误继续向上抛出。

Workspace sidecar 使用一次性随机 canary 和临时数据，验证认证、ETag、SSE、428/412 并发控制、旧写接口 409、Gold 零外发以及 canary 不进入 API 响应或容器日志。临时 sidecar 已自动销毁。

## 5. 测试证据边界

- 本地 macOS 后端：`1287 passed, 13 warnings`。
- 本地前端：`97 passed`；TypeScript、Vite build 通过。
- 本地 Playwright：`25 passed, 2 skipped`。
- 候选镜像生产构建、P0 合同和 workspace sidecar 均通过。
- 额外 Linux 全量 pytest 容器已尝试，但大型 Python wheel 从外部源持续低速重试，未进入测试阶段；该一次性容器已终止并清理。
- GitHub PR 尚未创建，因此远端 Actions 尚未触发。不能把本地和候选验证写成 GitHub CI 绿灯。

## 6. 生产替换与回滚

### 6.1 回滚点

| 项目 | 值 |
|---|---|
| 数据与配置备份 | `/home/ubuntu/tickflow-backups/20260723_232548-p0-aa9fc0a` |
| 备份大小 | 368 MB |
| 回滚镜像 tag | `tickflow-stock-panel-stage-a-app:rollback-20260723_232548-pre-aa9fc0a` |
| 回滚镜像 ID | `sha256:02d556047ec97314eeb10f8ae8f7e902b59efc16d1c93387d4b5d7933f5f56a5` |
| 回滚状态文件 | `~/.local/state/tickflow-stock-panel-stage-a/last-rollback-image` |

第一次替换脚本在停机前因 root-owned `preferences.json` 无法由普通用户计算校验和而退出。生产容器和镜像没有改变。修订脚本只对 checksum 和备份使用只读/复制所需的 `sudo`，随后重新执行。

### 6.2 替换方式

- 停止 `TickFlow_Stock_Panel`。
- 在停机窗口复制正式数据和配置。
- 把候选镜像标记为 Compose `latest`。
- 使用 `docker compose up -d --no-build --force-recreate --no-deps app`。
- 健康或镜像 ID 不匹配时自动恢复回滚 tag。

替换后 `user_data` 前后 SHA-256 清单完全一致，证明镜像替换没有改写现有用户数据。

## 7. 替换后验证

| 验证项 | 结果 |
|---|---|
| 生产镜像 digest | 与候选 `4f3f240c...1003` 一致 |
| OCI revision | `aa9fc0a84b8e72091f8e26c86983c66819befd30` |
| PWA 私网门禁 | `PWA_PRIVATE_ACCESS_OK` |
| 健康检查 | `status=ok, version=0.1.86, mode=none` |
| Workspace schema | version 1，5 类资源完整 |
| 工作区开关 | `WORKSPACE_SYNC_ENABLED=true` |
| Gold 开关 | `GOLD_WORKSPACE_ENABLED=false` |
| Gold 外发计数 | `0` |
| 认证 | `configured=false`，仍待用户私下初始化 |
| 端口 | 3018、3019 均仅监听 loopback |
| Tailscale 443 | 仍指向 `127.0.0.1:8787` |
| Tailscale 8443 | 仍指向 `127.0.0.1:3019` |
| 应用错误日志 | 最近部署窗口无 Traceback、ERROR、CRITICAL |
| 用户数据 | 校验和未改变 |

旁路服务未受影响：

- `TickFlow_Gold_Shadow` 容器 ID 仍为 `59dcb35fa8db`，3018，healthy。
- `ai-diary-n8n` 容器 ID 仍为 `cf45d07e6f4e`。
- `ai-diary-postgres` 容器 ID 仍为 `0abaa309a13a`，healthy。

## 8. 清理结果

- `127.0.0.1:3020` 临时候选容器已删除。
- 候选隔离数据目录已删除。
- 第一次失败留下的空预检目录和重复回滚 tag 已删除。
- 候选镜像 tag、正式运行镜像、最终数据备份和正式回滚 tag 均保留。
- 独立 Gold Shadow、n8n、PostgreSQL 和 Tailscale 443 未修改。

## 9. 剩余门槛

### P0/P1

1. 从 `codex/tickflow-p0-validation` 向 fork 的 `codex/tickflow-multiclient-app` 创建 PR，触发并确认 GitHub Actions。
2. 保留当前回滚镜像和数据备份至少 3–7 天，观察期间不删除旧镜像。
3. Intel Mac App/DMG 尚未重建，因此桌面安装包仍不包含本次 P0 修复。

### 产品初始化

1. 用户在 Tailscale 私网内自行初始化应用密码。
2. 私下配置获准的数据源和 AI Key，不通过聊天传递。
3. 导入五只自选股并完成三股日线事实校验和去交易化复盘。

在 AI Markdown 和确定性校验稳定前，继续暂停 Telegram、OpenClaw 和正式金融日报自动发布。

## 10. 最终结论

本次已经把增量指标历史预热与重复复权修复部署到云端 3019，并保留原数据、私网路由和独立旁路系统。当前手机 PWA 访问的是带 `aa9fc0a` revision 的补丁镜像，而不是原 `02d556...` 镜像。云端基础设施升级完成且可回滚，但应用仍处于未初始化状态；PR/远端 CI、桌面 App 重建和真实数据/AI 闭环仍需后续完成。
