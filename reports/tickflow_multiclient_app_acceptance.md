# TickFlow 多端 App 最终验收报告

## 1. 结论

自动化交付范围已完成，结论为 `CONDITIONAL_PASS`：

- 手机 PWA 已通过 Tailscale 私网部署，可访问且具备可安装 manifest。
- Intel Mac `x86_64` App 与 DMG 已构建、校验并安装。
- 云端主库工作区同步已开启，版本化写入、冲突处理、SSE 和离线只读边界通过。
- Mac 本地计算输入经过路径、大小、checksum、日期和参数 digest 校验；允许的故障可受控降级。
- Gold 工作区仍关闭，Telegram、券商、OpenClaw 主链路和自动交易均未接入。

未完成项均为人工交互：真机添加 PWA、设置真实云端密码并验证 GUI 会话、目视检查红色桌面图标。当前云端位于 Tailscale 私网，但应用认证尚未初始化，正式日常使用前应先设置密码。

## 2. 交付入口

| 入口 | 位置 |
|---|---|
| 手机 PWA | `https://vm-0-9-ubuntu.tail21c236.ts.net:8443/` |
| Intel DMG | `dist/TickFlowStockPanel-intel-x86_64.dmg` |
| 构建 App | `backend/dist/TickFlowStockPanel.app` |
| 已安装 App | `/Applications/TickFlowStockPanel.app` |
| 总运行手册 | `docs/runbook.md` |
| 工作区详细手册 | `docs/workspace-sync-runbook.md` |

PWA 仅对同一 tailnet 可见。3019 未开放公网，现有 443 主入口保持不变。

## 3. 产物完整性

| 项目 | 值 |
|---|---|
| 目标平台 | macOS Intel `x86_64` |
| DMG SHA-256 | `66b9dfae51d1d757b0c302d5e2fc1407d2499b8cf4507fb7fe95a3dfda52aa15` |
| DMG 大小 | `187042992` bytes |
| App tree SHA-256 | `750f9f30de3b9663f24115dc8d4ef480b810bd2fd3b7519535fccd6c3c7c5c30` |
| PWA tree SHA-256 | `a8cd03fd592cfdbfa23213731f64ddad66a4d972380f25bf0a20f12cd4f75a8d` |
| 签名 | ad-hoc，深度严格校验通过 |
| 公证 / App Store / 自动更新 | 本轮不包含 |

构建 App 和 `/Applications` 已安装 App 的 tree SHA-256 一致。冻结 App 已从 `/tmp` 且清空环境运行 smoke，未修改启动目录 `.env`，用户数据写入 `~/Library/Application Support/TickFlowStockPanel/`。

## 4. 云端运行态

| 项目 | 值 |
|---|---|
| 云端目录 | `/home/ubuntu/tickflow-stock-panel-stage-a` |
| 运行源码 | `7c4666003892c12bb284cda4f3dacab3a81de441` |
| 镜像 | `sha256:02d556047ec97314eeb10f8ae8f7e902b59efc16d1c93387d4b5d7933f5f56a5` |
| 容器 | `TickFlow_Stock_Panel` |
| 端口 | `127.0.0.1:3019 -> 3018/tcp` |
| Tailscale | `8443 -> 127.0.0.1:3019`，tailnet only |
| 现有主入口 | `443 -> 127.0.0.1:8787`，未改动 |
| 工作区同步 | `WORKSPACE_SYNC_ENABLED=true` |
| Gold | `GOLD_WORKSPACE_ENABLED=false` |
| Cookie | `AUTH_COOKIE_SECURE=true` |
| 认证 | `configured=false`，需人工初始化 |

切换只重建应用容器，并使用 `--no-deps`。n8n、Gold Shadow 与 Postgres 的容器 ID、镜像和启动时间未变化。镜像先对正式数据卷执行网络隔离、只读 schema/revision 契约预检，再切换运行态。

## 5. 功能与安全验收

| 验收 | 结果 |
|---|---|
| 后端全量 | 1282 passed，11 warnings |
| 工作区 / Desktop 定向 | 313 passed |
| 持久化 schema | 17 passed |
| 前端单元测试 | 97 passed |
| TypeScript | 通过 |
| Playwright | 25 passed，2 skipped |
| PWA precache | 69 entries |
| Intel App | `MACOS_INTEL_APP_OK` |
| Gold 回归 | 34 passed，`STAGE_A_READY` |
| 隔离 sidecar | `WORKSPACE_SIDECAR_CONTRACT_OK` |
| 总安全门禁 | `MULTICLIENT_SECURITY_OK` |

隔离 sidecar 覆盖认证、SSE、200、412、428、旧写接口 409、服务重启恢复和 Gold 零外发。生产卷严格校验偏好、密钥结构与研究报告 schema；损坏或未知字段 fail closed。一次性 canary 已从 shell 清除，验收容器已自动删除。

## 6. 数据与同步边界

- 云端主库同步自选股、允许共享的偏好、个股复盘元数据、市场复盘元数据和回测摘要。
- 报告正文不进入 PWA 持久离线缓存；认证失败会撤销离线授权状态。
- 离线时客户端只读，不排队写入；恢复网络后重新读取 revision。
- 412 必须人工刷新确认，不自动重放；回测冲突不得自动重跑。
- 本地计算降级只允许既定故障类型；完整性、路径、日期或覆盖范围失败直接停止。
- 当前 `/health` 返回的数据模式为 `none`，本轮没有写入真实数据源 Key。

## 7. 回滚

- 云端备份：`/home/ubuntu/tickflow-backups/20260722_130555-multiclient`
- 云端回滚镜像：`tickflow-stock-panel:rollback-20260722-130555`
- Mac 最近备份：`/Applications/TickFlowStockPanel.before-storage-schema-20260722_143703.app`

云端异常时先将 `WORKSPACE_SYNC_ENABLED=false` 并只重建应用容器，再按 `docs/workspace-sync-runbook.md` 的原子恢复流程处理数据。不得停止 Gold Shadow 或修改 443 主入口。

## 8. 人工门槛

1. 用户在 Tailscale 私网内设置应用访问密码，不把密码写入聊天、Git 或命令行历史。
2. 在真实 iPhone/Android 安装 PWA，验证启动、断网只读、恢复网络和安全退出。
3. 在 Mac App 中完成真实登录、重启会话恢复、退出登录删除 Keychain 会话。
4. Finder 首次右键打开未公证 App，并目视确认红色 Finder/Dock 图标。

上述门槛不阻断自动化交付结论，但完成前不标记为公开发布就绪。

## 9. 最终边界声明

本产物是私有 A 股研究工作台客户端，不是交易系统。它不接券商、不做实盘、不自动下单、不开发 Telegram、不接 OpenClaw 主链路，也不启用 Gold 工作区。AI 或策略输出仍需人工核验，不构成投资建议。
