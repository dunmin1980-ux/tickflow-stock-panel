# TickFlow 多端 App 验收结果

## 结论

状态：`IN_PROGRESS`，本地完整回归已通过；待 Mac 专用 canary 重建、云端两阶段部署和最终安全门禁后固化。

本轮范围仅包括手机 PWA、Intel Mac 私有 App、云端主库同步、本地计算优先和受控云端降级。Gold 保持关闭，不接 Telegram、券商、OpenClaw 主链路或自动交易。

## 多端一致性

| 项目 | 结果 | 证据 |
|---|---|---|
| Desktop -> Mobile SSE | 通过 | Desktop 加入 `000403.SZ`，Mobile 通过原生 `EventSource` 请求自动可见 |
| Mobile -> Desktop SSE | 通过 | Mobile 加入 `600489.SH`，Desktop 通过原生 `EventSource` 请求自动可见 |
| 并发旧 revision | 通过 | 两端 UI 在服务端栅栏后携带同一 ETag，严格一个 200、一个 412，无自动重放 |
| 离线禁写 | 通过 | 保持网络可用、仅切换应用离线状态；按钮 disabled，强制派发点击后 command request 为 0 |
| 登录页隔离 | 通过 | 未认证页面不启动 workspace SSE，不再把 401 误判为离线 |

Playwright 设备：Desktop 1440x900 与 iPhone 14 context。数据日期 fixture：`2026-07-21`。fixture 只验证协议与 UI，不代表市场数据新鲜度。

## 自动化门禁

| 门禁 | 结果 |
|---|---|
| Backend workspace / desktop / compute | 435 passed |
| Backend 全量 pytest | 1261 passed，11 个已知弃用警告 |
| Frontend Vitest | 97 passed |
| TypeScript | `tsc --noEmit` 通过 |
| PWA production build | 通过，Service Worker precache 69 entries |
| Playwright 全量 | 25 passed，2 skipped（multi-client 只在 desktop project 内自建双 context） |
| Playwright multi-client | 1 passed，Desktop 1440x900 + iPhone 14 context |
| Gold Stage A 回归 | 34 passed，`STAGE_A_READY` |
| Security script 负向门禁 | 5 类失败分支全部正确阻断 |
| Secret canary 正向门禁 | 待最终 canary 构建和云端部署 |

## 交付产物

| 产物 | 值 |
|---|---|
| PWA | `https://vm-0-9-ubuntu.tail21c236.ts.net:8443/`（Tailscale only） |
| Intel App | `backend/dist/TickFlowStockPanel.app` |
| Intel DMG | `dist/TickFlowStockPanel-intel-x86_64.dmg` |
| DMG SHA-256 | 待最终重建 |
| Runbook | `docs/workspace-sync-runbook.md` |
| Security gate | `scripts/verify_multiclient_security.sh` |

## 云端运行态

| 检查 | 结果 |
|---|---|
| 3019 仅 loopback | 待最终记录 |
| Tailscale 443 -> 8787 | 待最终记录 |
| Tailscale 8443 -> 3019 | 待最终记录 |
| `GOLD_WORKSPACE_ENABLED=false` | 待最终记录 |
| `WORKSPACE_SYNC_ENABLED=true` | 待两阶段部署 |
| 回滚点 | 待部署时记录 |

## 人工验收项

- 在真实 iPhone/Android 上完成 PWA 添加到主屏幕、启动和断网只读检查。
- 使用用户自行设置的云端密码完成真实 GUI 登录；密码不进入聊天、报告或脚本。
- Finder 中确认最终 App 红色图标和 DMG 拖放体验。
- Apple 公证、App Store、自动更新不在本轮范围。

所有自动生成的报告只记录状态、计数、哈希和脱敏路径，不记录 Key、Cookie、密码或 AI 复盘正文。
