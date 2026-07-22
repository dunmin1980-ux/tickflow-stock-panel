# TickFlow 多端 App 验收结果

## 结论

状态：`CONDITIONAL_PASS`。自动化范围全部通过，云端 PWA 与 Intel Mac App 已部署；真实手机安装、真实云端密码登录和 Finder 图标目视确认仍需人工完成。

本轮只覆盖手机 PWA、Intel Mac 私有 App、云端工作区同步、本地计算与受控云端降级。Gold 工作区保持关闭，不接 Telegram、券商、OpenClaw 主链路或自动交易。

## 多端一致性

| 项目 | 结果 | 证据 |
|---|---|---|
| Desktop -> Mobile SSE | 通过 | Desktop 加入 `000403.SZ` 后，Mobile context 通过原生 `EventSource` 自动可见 |
| Mobile -> Desktop SSE | 通过 | Mobile 加入 `600489.SH` 后，Desktop context 自动可见 |
| 并发旧 revision | 通过 | 两端携带同一 ETag，严格一个 200、一个 412，不自动重放 |
| 缺少 `If-Match` | 通过 | 写请求返回 428 |
| 旧写接口 | 通过 | 工作区开启后返回 409，防止绕过 revision 合同 |
| 离线禁写 | 通过 | 写控件禁用，强制触发后 command request 为 0 |
| 登录页隔离 | 通过 | 未认证页面不启动 workspace SSE，401 不被误判为离线 |
| 敏感数据隔离 | 通过 | canary 未出现在 PWA、App、DMG、缓存、日志、sidecar 响应或日志中 |

Playwright 设备为 Desktop 1440x900、iPhone 14 和 Pixel 7。数据 fixture 只验证协议与 UI，不代表市场数据新鲜度。

## 自动化门禁

| 门禁 | 结果 |
|---|---|
| Backend 全量 pytest | 1282 passed，11 个已知弃用警告 |
| Workspace / desktop 定向回归 | 313 passed |
| 持久化 schema 定向回归 | 17 passed |
| Frontend Vitest | 97 passed |
| TypeScript | `tsc -b` 通过 |
| PWA production build | 通过，Service Worker precache 69 entries |
| Playwright 全量 | 25 passed，2 skipped（双客户端场景仅在 desktop project 内执行） |
| Intel App | `MACOS_INTEL_APP_OK` |
| Gold Stage A 回归 | 34 passed，`STAGE_A_READY` |
| Security 正向门禁 | `WORKSPACE_SIDECAR_CONTRACT_OK`、`MULTICLIENT_SECURITY_OK` |
| Security 负向预检 | 缺少 canary、非法 SSH host、缺少收据均 fail closed |

## 交付产物

| 产物 | 值 |
|---|---|
| PWA | `https://vm-0-9-ubuntu.tail21c236.ts.net:8443/`（Tailscale only） |
| Intel App | `backend/dist/TickFlowStockPanel.app` |
| 已安装 App | `/Applications/TickFlowStockPanel.app` |
| Intel DMG | `dist/TickFlowStockPanel-intel-x86_64.dmg` |
| DMG SHA-256 | `66b9dfae51d1d757b0c302d5e2fc1407d2499b8cf4507fb7fe95a3dfda52aa15` |
| DMG 字节数 | `187042992` |
| App tree SHA-256 | `750f9f30de3b9663f24115dc8d4ef480b810bd2fd3b7519535fccd6c3c7c5c30` |
| 总运行手册 | `docs/runbook.md` |
| 工作区手册 | `docs/workspace-sync-runbook.md` |
| Security gate | `scripts/verify_multiclient_security.sh` |

## 云端运行态

| 检查 | 结果 |
|---|---|
| 运行镜像代码基线 | `7c4666003892c12bb284cda4f3dacab3a81de441` |
| 云端源码目录 | 已快进到当前分支最终验收提交；较镜像基线只新增文档与验收证据 |
| 运行镜像 | `sha256:02d556047ec97314eeb10f8ae8f7e902b59efc16d1c93387d4b5d7933f5f56a5` |
| 3019 绑定 | 仅 `127.0.0.1:3019` |
| Tailscale 443 | 保持指向 `127.0.0.1:8787` |
| Tailscale 8443 | 指向 `127.0.0.1:3019`，tailnet only |
| `WORKSPACE_SYNC_ENABLED` | `true` |
| `GOLD_WORKSPACE_ENABLED` | `false` |
| `AUTH_COOKIE_SECURE` | `true` |
| 应用认证 | 尚未初始化，`configured=false`；需用户在私网内人工设置密码 |
| 云端回滚点 | `/home/ubuntu/tickflow-backups/20260722_130555-multiclient` |
| 回滚镜像 | `tickflow-stock-panel:rollback-20260722-130555` |

n8n、Gold Shadow 和 Postgres 的容器 ID 与启动时间在应用切换前后保持不变。构建使用官方 PyPI；镜像源超时的尝试在切换容器前已停止。

## 人工验收项

1. 在真实 iPhone/Android 上完成 PWA 添加到主屏幕、启动和断网只读检查。
2. 用户在私网内自行设置云端密码，完成真实 GUI 登录、重启会话恢复和退出登录。
3. Finder 中确认最终 App 红色图标和 DMG 拖放体验。

Apple 公证、App Store 和自动更新不在本轮范围。自动报告只记录状态、计数、哈希和脱敏路径，不记录 Key、Cookie、密码或 AI 复盘正文。
